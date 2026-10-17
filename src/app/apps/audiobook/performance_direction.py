"""Direct performance: an LLM writes a short delivery direction for each dialogue line.

The directions become the spans' delivery notes (reviewable in Span review), and
Fish S2 Pro speaks each line with its note as an inline tag (``[angry] ...``).
A chapter is sent in windows of the story with every target dialogue span
marked; the model returns one direction per span, or "" for a plain line.

Only dialogue is directed. A delivery the user wrote is never replaced, and
spans with an open review issue wait until it is resolved.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from app.persistence.database import PostgresDatabase
from app.persistence.tenant import TenantContext
from app.persistence.unit_of_work import unit_of_work

from .leases import JOB_LEASE_SECONDS, lease_heartbeat

logger = logging.getLogger(__name__)

JOB_TYPE = "audiobook.direct-performance"
# When classification finishes: "always" directs, "fish" directs while Fish S2 Pro
# is Omnix's TTS engine (the default), "never" leaves it to the button.
AUTO_DIRECT_SETTING = "auto_direct_performance"
AUTO_DIRECT_POLICIES = ("always", "fish", "never")
DEFAULT_AUTO_DIRECT = "fish"
# The TTS engines that speak delivery directions.
DIRECTION_ENGINES = frozenset({"fish-s2-pro"})
_ACTIVE_STATUSES = ("queued", "waiting", "retrying", "leased", "running")
DIRECTOR_VERSION = "audiobook-performance-director-v1"
# One request covers at most this much story text and this many target lines.
_WINDOW_CHARACTERS = 12_000
_WINDOW_SPANS = 60
_CONTEXT_CHARACTERS = 1_500
_MAX_DIRECTION_CHARACTERS = 60
_MAX_DIRECTION_WORDS = 8
_REQUEST_TIMEOUT_SECONDS = 180.0
_PLAIN = {"", "none", "neutral", "normal", "plain", "default", "n/a", "na"}

SYSTEM_PROMPT = (
    "You are the performance director for an audiobook voiced by a text-to-speech model. "
    "Before each line the model can take one short bracketed direction that sets the "
    "emotion and delivery, for example [angry, shouting], [whispering, afraid], [warm], "
    "[sarcastic], [tired, flat], [laughing], [pleading], [cold, quiet]. "
    "Read the story_text. Each target line is marked ⟦LINE span_id | speaker⟧ immediately "
    "before its words. For every target span_id write the direction a skilled narrator "
    "would perform that line with, judged from the scene, the speaker and what the line "
    "means. Use 1 to 6 lowercase words, comma-separated: emotion first, then delivery "
    "(volume, pace or voice quality) when it matters. Return an empty string for a line "
    "spoken plainly; do not direct every line, and prefer subtle directions unless the "
    "scene is clearly intense. Never describe actions, never include names or quotes, "
    "never repeat the line. Return exactly one JSON object: "
    "{\"directions\": [{\"span_id\": \"...\", \"delivery\": \"...\"}]} with one entry per "
    "target span_id, copied verbatim. No markdown or explanation."
)


def clean_direction(value: Any) -> str:
    """A model's direction as a short, safe delivery note ("" when plain or unusable)."""
    text = re.sub(r"[\[\]\"“”‘’`*_<>{}()]", " ", str(value or ""))
    text = " ".join(text.replace(";", ",").split()).strip(" ,.:-").lower()
    if text in _PLAIN:
        return ""
    words = text.split()
    if len(words) > _MAX_DIRECTION_WORDS:
        text = " ".join(words[:_MAX_DIRECTION_WORDS]).rstrip(",")
    return text[:_MAX_DIRECTION_CHARACTERS].rstrip(" ,")


def direction_windows(
    canonical_text: str, lines: list[dict[str, Any]],
) -> list[tuple[str, list[str]]]:
    """Story windows with the target lines marked: ``(story_text, span_ids)``.

    ``lines`` are the chapter's dialogue spans in order, each with ``id``,
    ``start_offset`` and ``speaker``. A window carries some preceding story for
    context, so a scene split across windows is still directed in context.
    """
    windows: list[tuple[str, list[str]]] = []
    group: list[dict[str, Any]] = []

    def flush() -> None:
        if not group:
            return
        start = max(0, int(group[0]["start_offset"]) - _CONTEXT_CHARACTERS)
        end = min(len(canonical_text), int(group[-1]["end_offset"]) + _CONTEXT_CHARACTERS // 3)
        parts: list[str] = []
        cursor = start
        for line in group:
            offset = int(line["start_offset"])
            parts.append(canonical_text[cursor:offset])
            parts.append(f"⟦LINE {line['id']} | {line['speaker'] or 'unknown'}⟧")
            cursor = offset
        parts.append(canonical_text[cursor:end])
        windows.append(("".join(parts), [str(line["id"]) for line in group]))
        group.clear()

    for line in lines:
        if group and (len(group) >= _WINDOW_SPANS
                      or int(line["end_offset"]) - int(group[0]["start_offset"]) > _WINDOW_CHARACTERS):
            flush()
        group.append(line)
    flush()
    return windows


def parse_directions(value: Any, span_ids: list[str]) -> dict[str, str]:
    """The cleaned direction for each requested span the model answered; others are dropped."""
    entries = value.get("directions") if isinstance(value, dict) else None
    if not isinstance(entries, list):
        raise ValueError("performance direction must contain a directions array")
    requested = set(span_ids)
    directions: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("span_id")) in requested:
            directions[str(entry["span_id"])] = clean_direction(entry.get("delivery"))
    return directions


def llm_director() -> tuple[Callable[[dict[str, Any]], dict[str, Any]], dict[str, Any]]:
    """Call the configured chat provider (the one the classifier uses) for directions."""
    from app.providers import ChatMessage
    from app.providers.service import get_provider

    from .structured_call import json_object_call

    provider = get_provider()
    if provider is None:
        raise ValueError("directing the performance needs a configured chat provider")
    details: dict[str, Any] = {
        "version": DIRECTOR_VERSION,
        "provider_id": str(getattr(provider, "provider_name", "") or "configured"),
        "model": str(getattr(getattr(provider, "config", None), "model", "") or "") or None,
    }

    def direct(request: dict[str, Any]) -> dict[str, Any]:
        messages = [ChatMessage(role="system", content=SYSTEM_PROMPT),
                    ChatMessage(role="user", content=json.dumps(request, ensure_ascii=False))]
        value, served_model = json_object_call(
            provider, messages, contract_id="audiobook.performance_direction",
            request_timeout_seconds=_REQUEST_TIMEOUT_SECONDS, options={},
        )
        if served_model:
            details["model"] = served_model
        return value

    return direct, details


def direct_chapter(
    director: Callable[[dict[str, Any]], dict[str, Any]], chapter: dict[str, Any],
    speaker_names: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Directions for a chapter's dialogue lines, and any windows that failed."""
    lines = []
    for span in chapter.get("spans") or []:
        annotation = span.get("annotation") or {}
        if annotation.get("role") != "dialogue":
            continue
        lines.append({"id": span["id"], "start_offset": span["start_offset"], "end_offset": span["end_offset"],
                      "speaker": speaker_names.get(str(annotation.get("speaker_id") or ""), "")})
    directions: dict[str, str] = {}
    failures: list[str] = []
    for story_text, span_ids in direction_windows(str(chapter.get("canonical_text") or ""), lines):
        request = {"task": "direct_dialogue_performance", "chapter_title": chapter.get("title") or "",
                   "target_span_ids": span_ids, "story_text": story_text}
        for attempt in range(2):
            try:
                directions.update(parse_directions(director(request), span_ids))
                break
            except Exception as exc:
                if attempt:
                    logger.warning("performance direction failed for %d lines: %s", len(span_ids), exc)
                    failures.append(str(exc))
    return directions, failures


def _direction_active(work: Any, context: TenantContext, project_id: str) -> bool:
    return bool(work.jobs.query_jobs(
        context, module="audiobook", job_type=JOB_TYPE, statuses=_ACTIVE_STATUSES,
        input_fields=(("project_id", project_id),), limit=1,
    ))


def _create_direction_job(work: Any, context: TenantContext, *, project_id: str, source_revision_id: str,
                          chapter_ids: list[str], trigger: str) -> str:
    from uuid import uuid4

    job_id = f"ab:direct:{uuid4().hex}"
    work.jobs.create_job(context, {
        "id": job_id, "module": "audiobook", "job_type": JOB_TYPE, "resource_class": "cpu",
        "priority": 40,
        "input_payload": {"project_id": project_id, "source_revision_id": source_revision_id,
                          "chapter_ids": chapter_ids, "trigger": trigger},
        "max_attempts": 2,
    })
    return job_id


def start_direction(
    database: PostgresDatabase, context: TenantContext, *, project_id: str,
    chapter_ids: list[str] | None = None,
) -> dict[str, str]:
    """Queue directing the book's (or the given chapters') dialogue."""
    with unit_of_work(database) as work:
        row = work.connection.execute(
            """SELECT current_source_revision_id FROM omnix_audiobook_projects
                WHERE workspace_id = %s AND id = %s AND deleted_at IS NULL FOR UPDATE""",
            (context.workspace_id, project_id),
        ).fetchone()
        if row is None:
            raise KeyError(project_id)
        if not row[0]:
            raise ValueError("the project has no source to direct yet")
        if _direction_active(work, context, project_id):
            raise ValueError("the performance is already being directed")
        job_id = _create_direction_job(work, context, project_id=project_id, source_revision_id=str(row[0]),
                                       chapter_ids=list(chapter_ids or []), trigger="user")
        work.commit()
    return {"job_id": job_id}


def auto_direct_policy(value: Any) -> str:
    policy = str(value or DEFAULT_AUTO_DIRECT)
    return policy if policy in AUTO_DIRECT_POLICIES else DEFAULT_AUTO_DIRECT


def queue_after_classification(
    work: Any, context: TenantContext, *, project_id: str, source_revision_id: str,
    selected_engine: Callable[[], str] | None = None,
) -> str | None:
    """Queue directing in the classification's own transaction when the book's policy asks for it."""
    row = work.connection.execute(
        """SELECT settings->>%s FROM omnix_audiobook_projects
            WHERE workspace_id = %s AND id = %s AND deleted_at IS NULL""",
        (AUTO_DIRECT_SETTING, context.workspace_id, project_id),
    ).fetchone()
    policy = auto_direct_policy(row[0] if row else None)
    if row is None or policy == "never":
        return None
    if policy == "fish":
        if selected_engine is None:
            from app.providers.service import selected_tts_provider_id as selected_engine
        try:
            engine = selected_engine()
        except Exception:
            logger.debug("could not read the selected TTS engine", exc_info=True)
            return None
        if engine not in DIRECTION_ENGINES:
            return None
    if _direction_active(work, context, project_id):
        return None
    return _create_direction_job(work, context, project_id=project_id, source_revision_id=source_revision_id,
                                 chapter_ids=[], trigger="classification")


def run_direction_once(
    database: PostgresDatabase, blobs: Any, context: TenantContext, *, worker_id: str,
    director_factory: Callable[[], tuple[Callable[[dict[str, Any]], dict[str, Any]], dict[str, Any]]] = llm_director,
) -> bool:
    with unit_of_work(database) as work:
        job = work.jobs.claim_next(
            context, worker_id=worker_id, resource_classes=["cpu"],
            job_types=[JOB_TYPE], lease_seconds=JOB_LEASE_SECONDS,
        )
        if job is None:
            work.rollback()
            return False
        job = work.jobs.mark_running(
            context, job_id=job["id"], worker_id=worker_id, lease_token=job["lease_token"],
        )
        work.commit()
    with lease_heartbeat(database, context, job_id=job["id"], worker_id=worker_id,
                         lease_token=job["lease_token"]):
        _direct_claimed(database, blobs, context, job, worker_id=worker_id, director_factory=director_factory)
    return True


def _direct_claimed(
    database: PostgresDatabase, blobs: Any, context: TenantContext, job: dict[str, Any], *,
    worker_id: str, director_factory: Callable[[], Any],
) -> None:
    from .review_repository import PostgresAudiobookReviewRepository
    from .service import AudiobookService

    job_id, token = job["id"], job["lease_token"]
    payload = job["input_payload"]
    project_id = payload["project_id"]
    try:
        director, details = director_factory()
        service = AudiobookService(database, blobs)
        project = service.get_project(context, project_id, include_text=False)
        if str(project.get("current_source_revision_id") or "") != str(payload["source_revision_id"]):
            raise ValueError("the source changed after directing was requested")
        speaker_names = {str(item["id"]): str(item.get("display_name") or item.get("canonical_name") or "")
                         for item in project.get("speakers") or [] if isinstance(item, dict)}
        wanted = set(payload.get("chapter_ids") or [])
        chapters = [item for item in project.get("chapters") or []
                    if isinstance(item, dict) and (not wanted or str(item["id"]) in wanted)]
        totals = {"directed": 0, "plain": 0, "changed": 0, "kept_user_delivery": 0, "failed_windows": 0}
        for index, chapter_summary in enumerate(chapters):
            with unit_of_work(database) as work:
                current = work.jobs.get_job(context, job_id)
                if current["status"] == "cancel_requested":
                    work.jobs.acknowledge_cancel(context, job_id=job_id, worker_id=worker_id, lease_token=token)
                    work.commit()
                    return
                work.jobs.update_progress(
                    context, job_id=job_id, worker_id=worker_id, lease_token=token,
                    progress={"current": index, "total": len(chapters),
                              "message": f"directing {chapter_summary.get('title') or 'chapter'}"},
                )
                work.commit()
            chapter = service.get_chapter(context, project_id=project_id, chapter_id=str(chapter_summary["id"]))
            directions, failures = direct_chapter(director, chapter, speaker_names)
            totals["failed_windows"] += len(failures)
            totals["directed"] += sum(1 for value in directions.values() if value)
            totals["plain"] += sum(1 for value in directions.values() if not value)
            if directions:
                with unit_of_work(database) as work:
                    result = PostgresAudiobookReviewRepository(work.connection).apply_performance_directions(
                        context, project_id=project_id, directions=directions, director=dict(details),
                    )
                    work.commit()
                totals["changed"] += len(result["changed_span_ids"])
                totals["kept_user_delivery"] += int(result["kept_user_delivery"])
        if totals["failed_windows"] and not (totals["directed"] or totals["plain"]):
            raise ValueError("the chat provider returned no usable directions")
        with unit_of_work(database) as work:
            work.jobs.complete(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                output_refs=[{"project_id": project_id, **totals}],
                progress={"current": len(chapters), "total": len(chapters), **totals,
                          "message": "performance directed"},
            )
            work.commit()
    except Exception as exc:
        logger.exception("Directing the performance of %s failed", project_id)
        with unit_of_work(database) as work:
            work.jobs.fail(
                context, job_id=job_id, worker_id=worker_id, lease_token=token,
                error={"code": "performance_direction_failed", "message": str(exc),
                       "retryable": not isinstance(exc, (ValueError, KeyError))},
            )
            work.commit()


__all__ = [
    "AUTO_DIRECT_POLICIES",
    "AUTO_DIRECT_SETTING",
    "DIRECTOR_VERSION",
    "auto_direct_policy",
    "queue_after_classification",
    "JOB_TYPE",
    "SYSTEM_PROMPT",
    "clean_direction",
    "direct_chapter",
    "direction_windows",
    "llm_director",
    "parse_directions",
    "run_direction_once",
    "start_direction",
]
