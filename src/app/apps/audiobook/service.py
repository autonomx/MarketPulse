"""Application service for durable projects and asynchronous source ingestion."""
from __future__ import annotations

import hashlib
from uuid import uuid4
from pathlib import Path
from io import BytesIO
from typing import Any, BinaryIO

from PIL import Image, UnidentifiedImageError

from app.assets.content import asset_available, materialize_asset
from app.assets.canonical_voice_clones import discover_canonical_voice_clone_assets
from app.persistence.contracts import BlobStore
from app.assets.voice_clone_identity import voice_reference_revision


from app.persistence.database import PostgresDatabase
from app.persistence.tenant import TenantContext
from app.persistence.unit_of_work import unit_of_work
from app.runtime.paths import resources_data_root

from .extraction import (
    EXTRACTOR_VERSION,
    MAX_SOURCE_BYTES,
    SUPPORTED_SOURCE_FORMATS,
    UnsupportedSource,
    normalize_extraction_settings,
)
from .spans import DETECTOR_VERSION
from .analysis_repository import PostgresAudiobookAnalysisRepository
from .project_repository import PostgresAudiobookProjectRepository
from .repository import PostgresAudiobookRepository, cancel_render_run_jobs
from .review_repository import PostgresAudiobookReviewRepository
from .render_planner import load_chapter_units
from .render_readiness import check_book_render_readiness
from .speech_exclusions import load_removed_chapters
from .export_service import start_export as create_export_job
from .report import audit_export
from .hashing import canonical_json
from .model_identity import assert_model_revision, current_model_identity
from .performance import normalize_performance, render_generation_parameters
from .performance_direction import AUTO_DIRECT_POLICIES, AUTO_DIRECT_SETTING, auto_direct_policy
from .document_structure import (
    ANALYSIS_POLICY_VERSION, DOCUMENT_STRUCTURE_VERSION, RENDER_POLICY_VERSION,
    READ_ONCE, analysis_policy, effective_render_action, effective_role,
    mask_span_for_render,
)
from .models import SourceSpan
from .document_structure_repository import PostgresAudiobookDocumentStructureRepository


WORDS_PER_MINUTE = 150.0
# Chapter render order is job priority: the first chapter gets the base, each
# later one a step lower. A step above the claim's age bonus (at most 100) keeps
# a later-created retry from overtaking the chapters queued ahead of it.
RENDER_BASE_PRIORITY = -100
RENDER_PRIORITY_STEP = 101
WAITING_RENDER_STATUSES = ("queued", "waiting", "retrying", "paused")


def _render_priority(position: int) -> int:
    return RENDER_BASE_PRIORITY - RENDER_PRIORITY_STEP * position


def _render_queue(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Waiting render jobs in the order the worker claims them."""
    waiting = [job for job in jobs if str(job["status"]) in WAITING_RENDER_STATUSES]
    return sorted(waiting, key=lambda job: (-int(job["priority"]), str(job["created_at"]), str(job["id"])))


def _completed_chapter_assemblies(work: Any, context: TenantContext, render_run_id: str) -> dict[str, str]:
    """``chapter_id -> assembly_id`` for each chapter the render run has assembled (latest first)."""
    rows = work.jobs.query_jobs(
        context, module="audiobook", job_type="audiobook.assemble-chapter",
        statuses=("completed",), input_fields=(("render_run_id", render_run_id),),
        order_by="completed_desc", limit=1000,
    )
    assemblies: dict[str, str] = {}
    for job in rows:
        chapter_id = str((job["input_payload"] or {}).get("chapter_id") or "")
        refs = [ref for ref in (job["output_refs"] or []) if isinstance(ref, dict) and ref.get("assembly_id")]
        if chapter_id and refs and chapter_id not in assemblies:
            assemblies[chapter_id] = str(refs[0]["assembly_id"])
    return assemblies

_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "epub": "application/epub+zip",
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
    "text": "text/plain; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}

if set(_MIME) != set(SUPPORTED_SOURCE_FORMATS):  # pragma: no cover - developer contract
    raise RuntimeError("audiobook source format MIME mappings are incomplete")


def _projects(work: Any) -> PostgresAudiobookProjectRepository:
    return PostgresAudiobookProjectRepository(work.connection)


def _measure_source(stream: BinaryIO) -> tuple[int, str]:
    """Size and SHA-256 of a seekable stream, which is left rewound.

    Reading stops just past ``MAX_SOURCE_BYTES``; the size then reports the
    source as too large without hashing the rest.
    """
    digest = hashlib.sha256()
    size = 0
    stream.seek(0)
    while size <= MAX_SOURCE_BYTES and (chunk := stream.read(1024 * 1024)):
        size += len(chunk)
        digest.update(chunk)
    stream.seek(0)
    return size, digest.hexdigest()


class AudiobookService:
    def __init__(self, database: PostgresDatabase, blobs: BlobStore) -> None:
        self.database = database
        self.blobs = blobs

    @staticmethod
    def _require_active_project(connection: Any, context: TenantContext,
                                project_id: str, *, lock: bool = False) -> None:
        PostgresAudiobookProjectRepository(connection).require_active(
            context, project_id, lock=lock,
        )

    def create_project(
        self, context: TenantContext, *, title: str, author: str = "", language: str = "en",
        custom_rules: str = "",
    ) -> dict[str, object]:
        custom_rules = self._normalize_classification_rules(custom_rules)
        with unit_of_work(self.database) as work:
            result = PostgresAudiobookRepository(work.connection).create_project(
                context, project_id=f"ab:pr:{uuid4().hex}", title=title,
                author=author, language=language,
            )
            self._save_classification_rules(work, context, str(result["id"]), custom_rules)
            result["classification_rules"] = custom_rules
            work.commit()
        return result

    @staticmethod
    def _normalize_classification_rules(rules: str) -> str:
        if not isinstance(rules, str) or len(rules) > 4000:
            raise ValueError("classification rules must be at most 4000 characters")
        return rules.strip()

    @staticmethod
    def _save_classification_rules(work: Any, context: TenantContext, project_id: str, rules: str) -> None:
        if not _projects(work).save_classification_rules(context, project_id, rules):
            raise KeyError(project_id)

    def save_classification_rules(
        self, context: TenantContext, *, project_id: str, custom_rules: str,
    ) -> dict[str, str]:
        rules = self._normalize_classification_rules(custom_rules)
        with unit_of_work(self.database) as work:
            self._save_classification_rules(work, context, project_id, rules)
            work.commit()
        return {"classification_rules": rules}

    def update_project(
        self, context: TenantContext, *, project_id: str,
        title: str, author: str = "",
    ) -> dict[str, object]:
        title = title.strip()
        author = author.strip()
        if not title:
            raise ValueError("title is required")
        with unit_of_work(self.database) as work:
            row = _projects(work).update_metadata(
                context, project_id, title=title, author=author,
            )
            if row is None:
                raise KeyError(project_id)
            work.commit()
        return {
            "id": str(row[0]), "title": str(row[1]), "author": str(row[2]),
            "language": str(row[3]), "state": str(row[4]),
            "current_source_revision_id": str(row[5]) if row[5] else None,
        }

    def delete_project(self, context: TenantContext, *, project_id: str) -> None:
        """Hide a project while retaining immutable source and production history."""
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            if projects.lock_active(context, project_id) is None:
                raise KeyError(project_id)
            jobs = work.jobs.query_jobs(
                context,
                module="audiobook",
                input_fields=(("project_id", project_id),),
                statuses=("queued", "waiting", "retrying", "leased", "running", "paused", "cancel_requested"),
                limit=500,
                for_update=True,
            )
            for job in jobs:
                work.jobs.request_cancel(context, str(job["id"]))
            projects.mark_deleted(context, project_id)
            work.commit()

    def list_projects(self, context: TenantContext, *, offset: int = 0) -> list[dict[str, object]]:
        with unit_of_work(self.database) as work:
            result = PostgresAudiobookRepository(work.connection).list_projects(context, offset=offset)
            work.rollback()
        return result

    @staticmethod
    def list_voices() -> list[dict[str, str]]:
        return [{"id": item.id,
                 "name": str(item.metadata.get("profile_name") or item.metadata.get("speaker") or item.id),
                 "language": str(item.metadata.get("language") or "")}
                for item in discover_canonical_voice_clone_assets() if asset_available(item)]

    def get_project(self, context: TenantContext, project_id: str, *,
                    include_text: bool = True) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            repository = PostgresAudiobookRepository(work.connection)
            projects = _projects(work)
            project = repository.get_project(context, project_id)
            if project is None:
                raise KeyError(project_id)
            cover_row = projects.cover_summary(context, project_id)
            project["cover_asset_id"] = str(cover_row[0]) if cover_row and cover_row[0] else None
            project["cover_created_at"] = cover_row[2].isoformat() if cover_row and cover_row[2] else None
            project_settings = dict(cover_row[1] or {}) if cover_row else {}
            project["classification_rules"] = str(project_settings.get("classification_rules") or "")
            project["quote_extraction_rules"] = project_settings.get("quote_extraction_rules")
            project["audiobook_mode"] = str(
                project_settings.get("audiobook_mode") or "standard"
            )
            project["performance"] = normalize_performance(project_settings.get("performance"))
            project["auto_direct_performance"] = auto_direct_policy(project_settings.get(AUTO_DIRECT_SETTING))
            project["document_structure_version"] = DOCUMENT_STRUCTURE_VERSION
            project["render_policy_version"] = RENDER_POLICY_VERSION
            project["analysis_policy_version"] = ANALYSIS_POLICY_VERSION
            source_row = projects.source_summary(context, project_id)
            project["source_format"] = str(source_row[0]) if source_row else None
            project["source_filename"] = str(source_row[1]) if source_row and source_row[1] else None
            project["source_size_bytes"] = int(source_row[2]) if source_row and source_row[2] is not None else 0
            project["source_created_at"] = source_row[3].isoformat() if source_row and source_row[3] else None
            if project["current_source_revision_id"] and include_text:
                chapters = repository.list_chapters(context, project["current_source_revision_id"])
                for chapter in chapters:
                    chapter["spans"] = repository.list_spans(context, chapter["id"])
                    chapter["span_count"] = len(chapter["spans"])
            elif project["current_source_revision_id"]:
                chapter_rows = projects.chapter_summaries(
                    context, project["current_source_revision_id"],
                )
                chapters = [{"id": str(row[0]), "ordinal": int(row[1]),
                             "title": str(row[2]), "canonical_hash": str(row[3]),
                             "character_count": int(row[4]), "span_count": int(row[5])}
                            for row in chapter_rows]
            else:
                chapters = []
            removed_chapters = load_removed_chapters(work.connection, context, project_id)
            for chapter in chapters:
                chapter["audio_removal_id"] = removed_chapters.get(chapter["id"])
            project["review_issues"] = (
                PostgresAudiobookAnalysisRepository(work.connection).list_review_issues(
                    context, project_id,
                ) if project["state"] not in {"extracted", "ingesting"} else []
            )
            project["speakers"] = PostgresAudiobookReviewRepository(work.connection).list_speakers(context, project_id)
            pronunciation_rows = projects.current_pronunciations(context, project_id)
            project["pronunciations"] = [
                {"source_term": row[0], "spoken_term": row[1], "revision": row[2]}
                for row in pronunciation_rows
            ]
            pipeline_rows = work.jobs.query_jobs(
                context,
                module="audiobook",
                job_types=("audiobook.ingest", "audiobook.analyze", "audiobook.assemble-chapter",
                           "audiobook.direct-performance"),
                input_fields=(("project_id", project_id),),
                order_by="created_desc",
                limit=30,
            )
            project["pipeline_jobs"] = [
                {"id": str(job["id"]), "type": str(job["job_type"]), "status": str(job["status"]),
                 "progress": dict(job["progress"] or {}),
                 "error": dict(job["error"]) if job["error"] else None,
                 "attempts": int(job["attempt_count"]), "max_attempts": int(job["max_attempts"]),
                 "chapter_id": str((job["input_payload"] or {}).get("chapter_id") or "") or None,
                 "can_retry": (str(job["status"]) in {"failed", "canceled", "stale"}
                               and not dict(job["metadata"] or {}).get("superseded_by")),
                 "superseded_by": dict(job["metadata"] or {}).get("superseded_by"),
                 "reason": dict(job["metadata"] or {}).get("reason"),
                 "migration": dict(job["metadata"] or {}).get("migration"),
                 "pause_requested": bool(dict(job["metadata"] or {}).get("pause_requested")),
                 "paused": bool(dict(job["metadata"] or {}).get("paused"))}
                for job in pipeline_rows
            ]
            render_run_id = str(project_settings.get("current_render_run_id") or "") or None
            if render_run_id:
                job_rows = work.jobs.query_jobs(
                    context,
                    module="audiobook",
                    job_type="audiobook.render-chapter",
                    input_fields=(("render_run_id", render_run_id),),
                    order_by="created_asc",
                    limit=500,
                )
                queue_positions = {str(job["id"]): index + 1 for index, job in enumerate(_render_queue(job_rows))}
                assembly_by_chapter = _completed_chapter_assemblies(work, context, render_run_id)
                assemblies = projects.chapter_assemblies(context, list(assembly_by_chapter.values()))
                project["render_jobs"] = []
                for job in job_rows:
                    chapter_id = str((job["input_payload"] or {}).get("chapter_id") or "")
                    assembly = assemblies.get(assembly_by_chapter.get(chapter_id, ""))
                    project["render_jobs"].append({
                        "id": str(job["id"]), "status": str(job["status"]),
                        "progress": dict(job["progress"] or {}),
                        "error": dict(job["error"]) if job["error"] else None,
                        "attempts": int(job["attempt_count"]), "max_attempts": int(job["max_attempts"]),
                        "chapter_id": chapter_id,
                        "can_retry": bool((dict(job["error"] or {}).get("retryable", True))
                                          and int(job["attempt_count"]) < int(job["max_attempts"])),
                        "queue_position": queue_positions.get(str(job["id"])),
                        "audio": {"duration_seconds": assembly[2]} if assembly else None,
                    })
                total, completed = projects.render_key_counts(
                    context, [str(job["id"]) for job in job_rows],
                )
                project["render_progress"] = {"total": total, "completed": completed}
            else:
                project["render_jobs"] = []
                project["render_progress"] = {"total": 0, "completed": 0}
            export_rows = projects.recent_export_manifests(context, project_id)
            project["export_jobs"] = [
                {"id": job["id"], "status": job["status"], "progress": job["progress"],
                 "error": job["error"], "format": str(row[1]), "manifest_id": str(row[2]),
                "manifest_hash": str(row[3])}
                for row in export_rows
                if (job := work.jobs.get_job(context, str(row[0]))) is not None
            ]
            preview_rows = work.jobs.query_jobs(
                context,
                module="audiobook",
                job_type="audiobook.preview-span",
                input_fields=(("project_id", project_id),),
                order_by="created_desc",
                limit=40,
            )
            project["preview_jobs"] = [
                {"id": job["id"], "status": job["status"], "progress": job["progress"],
                 "error": job["error"], "span_id": (job["input_payload"] or {}).get("span_id"),
                 "output_refs": job["output_refs"]}
                for job in preview_rows
            ]
            if project["current_source_revision_id"]:
                text_rows = projects.chapter_texts(
                    context, project["current_source_revision_id"],
                )
                for chapter, row in zip(chapters, text_rows):
                    chapter["word_count"] = len(str(row[0]).split())
                    chapter["estimated_runtime_seconds"] = (chapter["word_count"] / WORDS_PER_MINUTE) * 60.0
                word_count = sum(chapter["word_count"] for chapter in chapters)
                project["word_count"] = word_count
                project["estimated_runtime_seconds"] = (word_count / WORDS_PER_MINUTE) * 60.0
                project["actual_runtime_seconds"] = projects.assembled_runtime_seconds(
                    context, project["current_source_revision_id"],
                )
            else:
                project["word_count"] = 0
                project["estimated_runtime_seconds"] = 0.0
                project["actual_runtime_seconds"] = 0.0
            project["render_readiness"] = check_book_render_readiness(
                work.connection, context, project_id=project_id,
                source_revision_id=project["current_source_revision_id"],
            ).public()
            work.rollback()
        return {**project, "chapters": chapters}

    def get_chapter(self, context: TenantContext, *, project_id: str,
                    chapter_id: str) -> dict[str, object]:
        from dataclasses import asdict

        from .speech_plan import build_speech_plan

        with unit_of_work(self.database) as work:
            projects = _projects(work)
            row = projects.current_chapter(context, project_id, chapter_id)
            if row is None:
                raise KeyError(chapter_id)
            spans = PostgresAudiobookRepository(work.connection).list_spans(context, chapter_id)
            annotation_rows = projects.latest_annotations(
                context, chapter_id,
                include_unresolved=row[6] not in {"extracted", "ingesting"},
            )
            annotations = {
                str(item[0]): {"id": str(item[1]), "revision": int(item[2]),
                               "role": str(item[3]),
                               "speaker_id": str(item[4]) if item[4] else None,
                               "speaker_candidate": str(item[5]) if item[5] else None,
                               "delivery": str(item[6]), "evidence": dict(item[7]),
                               "review_status": str(item[8])}
                for item in annotation_rows if item[1] is not None
            }
            overrides = {
                str(term): str(spoken)
                for term, spoken, _revision in projects.current_pronunciations(context, project_id)
            }
            structure_repository = PostgresAudiobookDocumentStructureRepository(
                work.connection
            )
            all_blocks = structure_repository.list_blocks(
                context, source_revision_id=str(row[5]),
            )
            blocks = [
                block for block in all_blocks if block.chapter_id == chapter_id
            ]
            document_overrides = structure_repository.list_overrides(
                context, project_id=project_id, source_revision_id=str(row[5]),
            )
            audiobook_mode = str(dict(row[7] or {}).get("audiobook_mode") or "standard")
            read_once_block_ids: set[str] = set()
            seen_read_once: set[str] = set()
            for block in all_blocks:
                if effective_render_action(
                    block, mode=audiobook_mode, overrides=document_overrides,
                ) != READ_ONCE:
                    continue
                key = block.recurrence_group or (
                    f"role:{effective_role(block, document_overrides)}"
                )
                if key not in seen_read_once:
                    seen_read_once.add(key)
                    read_once_block_ids.add(block.id)
            block_payload = []
            for block in blocks:
                payload = asdict(block)
                role = effective_role(block, document_overrides)
                payload["effective_role"] = role
                payload["render_action"] = effective_render_action(
                    block, mode=audiobook_mode, overrides=document_overrides,
                )
                payload["speaker_analysis_visibility"] = analysis_policy(
                    role, "speaker_attribution"
                )
                block_payload.append(payload)
            from .speech_exclusions import load_speech_exclusions, exclusions_for_span
            speech_exclusions = load_speech_exclusions(work.connection, context, project_id, chapter_id)
            for span in spans:
                span["annotation"] = annotations.get(span["id"])
                span["speech_exclusions"] = exclusions_for_span(
                    speech_exclusions, int(span["start_offset"]), int(span["end_offset"]),
                )
                render_text = span["source_text"]
                if blocks:
                    source_span = SourceSpan(
                        id=span["id"], chapter_id=chapter_id,
                        ordinal=int(span["ordinal"]),
                        start_offset=int(span["start_offset"]),
                        end_offset=int(span["end_offset"]),
                        source_text=span["source_text"],
                        source_hash=span["source_hash"],
                        structural_kind=span["structural_kind"],
                        detector_version=span["detector_version"],
                    )
                    render_text = mask_span_for_render(
                        source_span, blocks, mode=audiobook_mode,
                        overrides=document_overrides,
                        read_once_block_ids=read_once_block_ids,
                    )
                plan = build_speech_plan(
                    render_text, overrides=overrides,
                    structural_kind=span["structural_kind"],
                    exclusions=tuple((item["start_offset"], item["end_offset"]) for item in span["speech_exclusions"]),
                )
                span["speech_plan"] = {"tts_input_text": plan.tts_input_text,
                                       "hash": plan.hash,
                                       "transformations": [asdict(item) for item in plan.transformations]}
            work.rollback()
        return {"id": str(row[0]), "ordinal": int(row[1]), "title": str(row[2]),
                "canonical_text": str(row[3]), "canonical_hash": str(row[4]),
                "spans": spans, "document_blocks": block_payload,
                "audiobook_mode": audiobook_mode,
                "render_policy_version": RENDER_POLICY_VERSION,
                "analysis_policy_version": ANALYSIS_POLICY_VERSION}

    def get_document_structure(
        self, context: TenantContext, *, project_id: str,
        chapter_id: str | None = None,
    ) -> dict[str, object]:
        from dataclasses import asdict

        with unit_of_work(self.database) as work:
            project = _projects(work).structure_settings(context, project_id)
            if project is None:
                raise KeyError(project_id)
            mode = str(dict(project[1] or {}).get("audiobook_mode") or "standard")
            if not project[0]:
                return {
                    "source_revision_id": None, "run_id": None, "blocks": [],
                    "regions": [], "overrides": [], "audiobook_mode": mode,
                    "document_structure_version": DOCUMENT_STRUCTURE_VERSION,
                    "render_policy_version": RENDER_POLICY_VERSION,
                    "analysis_policy_version": ANALYSIS_POLICY_VERSION,
                }
            source_revision_id = str(project[0])
            repository = PostgresAudiobookDocumentStructureRepository(work.connection)
            blocks = repository.list_blocks(
                context, source_revision_id=source_revision_id,
                chapter_id=chapter_id,
            )
            regions = repository.list_regions(
                context, source_revision_id=source_revision_id,
                chapter_id=chapter_id,
            )
            overrides = repository.list_overrides(
                context, project_id=project_id,
                source_revision_id=source_revision_id,
            )
            rendered = []
            for block in blocks:
                item = asdict(block)
                role = effective_role(block, overrides)
                item["effective_role"] = role
                item["render_action"] = effective_render_action(
                    block, mode=mode, overrides=overrides,
                )
                item["analysis_visibility"] = {
                    consumer: analysis_policy(role, consumer)
                    for consumer in (
                        "speaker_attribution", "chapter_summarizer",
                        "dialogue_pronunciation",
                    )
                }
                rendered.append(item)
            run_id = repository.latest_run_id(context, source_revision_id)
            work.rollback()
        return {
            "source_revision_id": source_revision_id,
            "run_id": run_id,
            "document_structure_version": DOCUMENT_STRUCTURE_VERSION,
            "render_policy_version": RENDER_POLICY_VERSION,
            "analysis_policy_version": ANALYSIS_POLICY_VERSION,
            "audiobook_mode": mode,
            "blocks": rendered,
            "regions": [asdict(region) for region in regions],
            "overrides": overrides,
        }

    def set_audiobook_mode(
        self, context: TenantContext, *, project_id: str, mode: str,
    ) -> dict[str, object]:
        mode = mode.strip().casefold()
        if mode not in {"standard", "story_only", "verbatim"}:
            raise ValueError("audiobook mode must be standard, story_only, or verbatim")
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_render_settings(context, project_id)
            if project is None:
                raise KeyError(project_id)
            if project[1]:
                cancel_render_run_jobs(work.jobs, context, str(project[1]))
            projects.set_audiobook_mode(context, project_id, mode)
            work.commit()
        return {
            "project_id": project_id, "audiobook_mode": mode,
            "render_policy_version": RENDER_POLICY_VERSION,
        }

    def set_performance(
        self, context: TenantContext, *, project_id: str, performance: dict[str, Any],
    ) -> dict[str, object]:
        """Store the book's Stability/Style and per-character overrides; the render run restarts."""
        normalized = normalize_performance(performance)
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_render_settings(context, project_id)
            if project is None:
                raise KeyError(project_id)
            unknown = set(normalized["speakers"]) - projects.project_speaker_ids(context, project_id)
            if unknown:
                raise ValueError(f"unknown speaker: {sorted(unknown)[0]}")
            if normalize_performance(projects.performance(context, project_id)) == normalized:
                work.rollback()
                return {"project_id": project_id, "performance": normalized, "changed": False}
            if project[1]:
                cancel_render_run_jobs(work.jobs, context, str(project[1]))
            projects.set_performance(context, project_id, canonical_json(normalized))
            work.commit()
        return {"project_id": project_id, "performance": normalized, "changed": True}

    def set_auto_direct(self, context: TenantContext, *, project_id: str, policy: str) -> dict[str, str]:
        """Whether classification is followed by directing the dialogue: always, with Fish, or never."""
        if policy not in AUTO_DIRECT_POLICIES:
            raise ValueError(f"policy must be one of {', '.join(AUTO_DIRECT_POLICIES)}")
        with unit_of_work(self.database) as work:
            if not _projects(work).set_setting(context, project_id, AUTO_DIRECT_SETTING, policy):
                raise KeyError(project_id)
            work.commit()
        return {"project_id": project_id, "auto_direct_performance": policy}

    def direct_performance(
        self, context: TenantContext, *, project_id: str, chapter_ids: list[str] | None = None,
    ) -> dict[str, str]:
        """Queue an LLM pass that writes a delivery direction for each dialogue line."""
        from .performance_direction import start_direction

        return start_direction(self.database, context, project_id=project_id, chapter_ids=chapter_ids)

    def set_document_override(
        self, context: TenantContext, *, project_id: str, scope: str,
        scope_key: str, action: str = "DEFAULT",
        role_override: str | None = None,
    ) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_for_override(context, project_id)
            if project is None:
                raise KeyError(project_id)
            if not project[0]:
                raise ValueError("project has no canonical source")
            source_revision_id = str(project[0])
            structure_repository = PostgresAudiobookDocumentStructureRepository(
                work.connection
            )
            blocks = structure_repository.list_blocks(
                context, source_revision_id=source_revision_id,
            )
            before_overrides = structure_repository.list_overrides(
                context, project_id=project_id,
                source_revision_id=source_revision_id,
            )
            before_visibility = {
                block.id: analysis_policy(
                    effective_role(block, before_overrides),
                    "speaker_attribution",
                )
                for block in blocks
            }
            result = structure_repository.append_override(
                context, project_id=project_id,
                source_revision_id=source_revision_id, scope=scope,
                scope_key=scope_key, action=action,
                role_override=role_override,
            )
            after_overrides = structure_repository.list_overrides(
                context, project_id=project_id,
                source_revision_id=source_revision_id,
            )
            reanalysis_required = any(
                before_visibility.get(block.id)
                != analysis_policy(
                    effective_role(block, after_overrides),
                    "speaker_attribution",
                )
                for block in blocks
            )
            analysis_job_id: str | None = None
            if reanalysis_required:
                active_analysis = work.jobs.query_jobs(
                    context,
                    module="audiobook",
                    job_type="audiobook.analyze",
                    input_fields=(("project_id", project_id), ("source_revision_id", source_revision_id)),
                    statuses=("queued", "waiting", "retrying", "leased", "running", "paused", "cancel_requested"),
                    limit=1,
                    for_update=True,
                )
                if active_analysis:
                    raise ValueError(
                        "document role cannot change while source analysis is active"
                    )
                analysis_job_id = f"ab:reclassify:{uuid4().hex}"
                work.jobs.create_job(context, {
                    "id": analysis_job_id,
                    "module": "audiobook",
                    "job_type": "audiobook.analyze",
                    "resource_class": "cpu",
                    "priority": 0,
                    "input_payload": {
                        "project_id": project_id,
                        "source_revision_id": source_revision_id,
                        "force_reclassify": True,
                    },
                    "metadata": {
                        "reason": "document_role_override",
                        "document_override_id": result["id"],
                    },
                    "max_attempts": 3,
                })
            if project[2]:
                cancel_render_run_jobs(work.jobs, context, str(project[2]))
            next_state = (
                "analyzing"
                if reanalysis_required
                else (
                    "ready_to_render"
                    if str(project[1]) in {
                        "rendering", "mastering", "rendered",
                        "ready_to_export", "exported",
                    }
                    else str(project[1])
                )
            )
            projects.set_state_clearing_render(context, project_id, next_state)
            work.commit()
        return {
            **result,
            "reanalysis_required": reanalysis_required,
            "analysis_job_id": analysis_job_id,
        }

    def add_speaker(self, context: TenantContext, *, project_id: str, canonical_name: str) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            result = PostgresAudiobookReviewRepository(work.connection).add_speaker(
                context, project_id=project_id, canonical_name=canonical_name,
            )
            work.commit()
        return result

    @staticmethod
    def _invalidate_speech_audio(work: Any, context: TenantContext, project_id: str, render_run_id: Any) -> None:
        if render_run_id:
            cancel_render_run_jobs(work.jobs, context, str(render_run_id))
        _projects(work).reset_render(context, project_id)

    @classmethod
    def _after_speech_removal(cls, work: Any, context: TenantContext, project_id: str, render_run_id: Any) -> None:
        """Removed or restored text invalidates audio, and a removed chapter's issues stop blocking."""
        cls._invalidate_speech_audio(work, context, project_id, render_run_id)
        _projects(work).sync_review_state(context, project_id)

    def exclude_span_text(
        self, context: TenantContext, *, project_id: str, span_id: str,
        start_offset: int, end_offset: int, source_text: str,
    ) -> dict[str, str]:
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            row = projects.lock_current_span(context, project_id, span_id)
            if row is None:
                raise KeyError(span_id)
            if not 0 <= start_offset < end_offset <= len(row[0]) or row[0][start_offset:end_offset] != source_text:
                raise ValueError("selected text no longer matches the current source span")
            exclusion_id = projects.upsert_speech_exclusion(
                context, project_id, exclusion_id=f"ab:exclude:{uuid4().hex}",
                chapter_hash=row[2], chapter_ordinal=row[3],
                start_offset=int(row[1]) + start_offset,
                end_offset=int(row[1]) + end_offset, source_text=source_text,
            )
            self._after_speech_removal(work, context, project_id, row[4])
            work.commit()
        return {"id": exclusion_id}

    def restore_span_text(self, context: TenantContext, *, project_id: str, exclusion_id: str) -> dict[str, bool]:
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_render_run(context, project_id)
            if project is None:
                raise KeyError(project_id)
            if not projects.deactivate_speech_exclusion(context, project_id, exclusion_id):
                raise KeyError(exclusion_id)
            self._after_speech_removal(work, context, project_id, project[0])
            work.commit()
        return {"restored": True}

    def remove_chapter_from_audio(self, context: TenantContext, *, project_id: str,
                                  chapter_id: str) -> dict[str, str]:
        """Leave a whole chapter out of the audiobook; restoring its exclusion brings it back."""
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            row = projects.lock_current_chapter(context, project_id, chapter_id)
            if row is None:
                raise KeyError(chapter_id)
            text = str(row[0])
            if not text:
                raise ValueError("this chapter has no text to remove")
            exclusion_id = projects.upsert_speech_exclusion(
                context, project_id, exclusion_id=f"ab:exclude:{uuid4().hex}",
                chapter_hash=row[1], chapter_ordinal=row[2],
                start_offset=0, end_offset=len(text), source_text=text,
            )
            self._after_speech_removal(work, context, project_id, row[3])
            work.commit()
        return {"id": exclusion_id}

    def set_pronunciation(
        self, context: TenantContext, *, project_id: str,
        source_term: str, spoken_term: str,
    ) -> dict[str, object]:
        term, spoken = source_term.strip(), spoken_term.strip()
        if not term or not spoken or len(term) > 128 or len(spoken) > 256:
            raise ValueError("pronunciation terms must be non-empty and within length limits")
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_render_settings(context, project_id)
            if project is None:
                raise KeyError(project_id)
            current = projects.latest_pronunciation(context, project_id, term)
            if current and current[1] == spoken:
                return {"source_term": term, "spoken_term": spoken, "revision": int(current[0])}
            revision = int(current[0]) + 1 if current else 1
            projects.append_pronunciation(
                context, project_id, pronunciation_id=f"ab:pron:{uuid4().hex}",
                revision=revision, source_term=term, spoken_term=spoken,
            )
            self._invalidate_speech_audio(work, context, project_id, project[1])
            work.commit()
        return {"source_term": term, "spoken_term": spoken, "revision": revision}

    def set_cover(self, context: TenantContext, *, project_id: str,
                  content: bytes, filename: str = "cover") -> dict[str, str]:
        if not content or len(content) > 10 * 1024 * 1024:
            raise ValueError("cover must be non-empty and at most 10 MB")
        if content.startswith(b"\xff\xd8\xff"):
            mime, suffix = "image/jpeg", "jpg"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime, suffix = "image/png", "png"
        else:
            raise ValueError("cover must be a JPEG or PNG image")
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format != ("JPEG" if suffix == "jpg" else "PNG"):
                    raise ValueError("cover format does not match its content")
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("cover image is corrupt") from exc
        asset_id = f"ab:cover:{uuid4().hex}"
        storage_key = f"audiobook/cover/{uuid4().hex}.{suffix}"
        blob = self.blobs.put_bytes(storage_key, content)
        try:
            with unit_of_work(self.database) as work:
                projects = _projects(work)
                if projects.lock_active(context, project_id) is None:
                    raise KeyError(project_id)
                work.assets.create(context, {
                    "id": asset_id, "module": "audiobook", "asset_type": "cover",
                    "mime_type": mime, "byte_size": blob["byte_size"],
                    "checksum_sha256": blob["checksum_sha256"],
                    "storage_provider": blob["storage_provider"], "storage_key": storage_key,
                    "metadata": {"filename": filename},
                })
                projects.set_cover(context, project_id, asset_id)
                work.commit()
        except Exception:
            if blob["created"]:
                self.blobs.delete(storage_key)
            raise
        return {"cover_asset_id": asset_id, "mime_type": mime}

    def read_cover(self, context: TenantContext, *, project_id: str) -> tuple[bytes, str]:
        with unit_of_work(self.database) as work:
            row = _projects(work).cover_blob(context, project_id)
            work.rollback()
        if row is None:
            raise KeyError(project_id)
        return self.blobs.read_bytes(str(row[0]), expected_checksum=str(row[1])), str(row[2])

    def delete_asset(self, context: TenantContext, *, project_id: str,
                     asset_id: str) -> dict[str, object]:
        """Soft-delete a project asset and remove its local blob.

        Generated exports and the current cover are user-managed assets. Source
        assets remain immutable because they back the project's source revisions.
        """
        storage_key: str
        relationship: str
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_cover(context, project_id)
            if project is None:
                raise KeyError(project_id)

            asset = projects.lock_asset(context, asset_id)
            if asset is None or str(asset[3]) == "deleted":
                raise KeyError(asset_id)
            storage_key = str(asset[1])

            if project[0] is not None and str(project[0]) == asset_id:
                relationship = "cover"
            elif projects.is_export_output(context, project_id, asset_id):
                relationship = "export"
            elif projects.is_source_original(context, project_id, asset_id):
                raise ValueError("the manuscript source cannot be deleted")
            else:
                raise KeyError(asset_id)

            deleted = work.assets.mark_deleted(
                context, asset_id=asset_id, expected_revision=int(asset[2]),
            )
            if relationship == "cover":
                projects.set_cover(context, project_id, None)
            work.audit.append(
                context,
                aggregate_type="asset",
                aggregate_id=asset_id,
                action="asset.deleted",
                payload={"project_id": project_id, "relationship": relationship,
                         "revision": deleted["revision"], "delete_blob": True},
            )
            work.commit()

        return {"asset_id": asset_id, "deleted": True,
                "file_deleted": self.blobs.delete(storage_key)}

    def open_source(self, context: TenantContext, *, project_id: str) -> tuple[BinaryIO, str, str]:
        """Open the current immutable source revision for browser download."""
        with unit_of_work(self.database) as work:
            row = _projects(work).current_source_blob(context, project_id)
            work.rollback()
        if row is None:
            raise KeyError(project_id)
        source_format = str(row[3])
        filename = Path(str(row[4] or f"ebook.{source_format}")).name
        return (
            self.blobs.open_verified(str(row[0]), expected_checksum=str(row[1])),
            str(row[2]),
            filename or f"ebook.{source_format}",
        )

    def confirm_alias(self, context: TenantContext, *, project_id: str,
                      speaker_id: str, alias: str) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            result = PostgresAudiobookReviewRepository(work.connection).confirm_alias(
                context, project_id=project_id, speaker_id=speaker_id, alias=alias,
            )
            work.commit()
        return result

    def unconfirm_alias(self, context: TenantContext, *, project_id: str,
                        speaker_id: str, alias: str) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            result = PostgresAudiobookReviewRepository(work.connection).unconfirm_alias(
                context, project_id=project_id, speaker_id=speaker_id, alias=alias,
            )
            work.commit()
        return result

    def confirm_suggested_aliases(self, context: TenantContext, *, project_id: str) -> dict[str, int]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            result = PostgresAudiobookReviewRepository(work.connection).confirm_suggested_aliases(
                context, project_id=project_id,
            )
            work.commit()
        return result

    def reject_speaker(
        self, context: TenantContext, *, project_id: str, speaker_id: str,
    ) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(
                work.connection, context, project_id, lock=True
            )
            result = PostgresAudiobookReviewRepository(
                work.connection
            ).reject_proposed_speaker(
                context, project_id=project_id, speaker_id=speaker_id,
            )
            work.commit()
        return result

    def assign_voice(
        self, context: TenantContext, *, project_id: str,
        speaker_id: str, voice_profile_id: str,
    ) -> dict[str, object]:
        profile = next((item for item in discover_canonical_voice_clone_assets()
                        if item.id == voice_profile_id), None)
        if profile is None or not asset_available(profile):
            raise ValueError("voice profile is unavailable")
        voice_hash = voice_reference_revision(materialize_asset(profile))
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            result = PostgresAudiobookReviewRepository(work.connection).assign_voice(
                context, project_id=project_id, speaker_id=speaker_id,
                voice_profile_id=voice_profile_id, voice_revision_hash=voice_hash,
            )
            work.commit()
        return result

    def resolve_issue(
        self, context: TenantContext, *, project_id: str,
        issue_id: str, speaker_id: str, role: str, delivery: str = "",
    ) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            result = PostgresAudiobookReviewRepository(work.connection).resolve_issue(
                context, project_id=project_id, issue_id=issue_id,
                speaker_id=speaker_id, role=role, delivery=delivery,
            )
            work.commit()
        return result

    def revise_span(
        self, context: TenantContext, *, project_id: str, span_id: str,
        speaker_id: str, role: str, delivery: str = "",
    ) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            result = PostgresAudiobookReviewRepository(work.connection).revise_span(
                context, project_id=project_id, span_id=span_id,
                speaker_id=speaker_id, role=role, delivery=delivery,
            )
            work.commit()
        return result

    def cancel_job(self, context: TenantContext, *, project_id: str,
                   job_id: str) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            rows = work.jobs.query_jobs(
                context,
                job_id=job_id,
                module="audiobook",
                input_fields=(("project_id", project_id),),
                limit=1,
                for_update=True,
            )
            if not rows:
                raise KeyError(job_id)
            job = rows[0]
            # Analysis cancellation is safe to finalize immediately: the
            # worker's chapter writes and lease renewal share one transaction,
            # so clearing ownership prevents any partial commit while a
            # provider call is still unwinding. Render jobs retain their
            # cooperative cancel path because providers may have external
            # encoder side effects.
            if job["job_type"] == "audiobook.analyze" and job["status"] in {
                "leased", "running", "cancel_requested",
            }:
                work.jobs.cancel_active_job(context, job_id=job_id)
            else:
                work.jobs.request_cancel(context, job_id)
            work.commit()
        return {"job_id": job_id, "cancellation_requested": True}

    def pause_job(self, context: TenantContext, *, project_id: str,
                  job_id: str) -> dict[str, object]:
        """Pause one audiobook job, releasing queued work or requesting a safe stop."""
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            rows = work.jobs.query_jobs(
                context,
                job_id=job_id,
                module="audiobook",
                input_fields=(("project_id", project_id),),
                limit=1,
                for_update=True,
            )
            if not rows or rows[0]["job_type"] not in {"audiobook.render-chapter", "audiobook.analyze"}:
                raise KeyError(job_id)
            status = str(rows[0]["status"])
            if status in {"completed", "failed", "canceled", "stale", "cancel_requested"}:
                raise ValueError("only active audiobook jobs can be paused")
            if status == "paused":
                work.rollback()
                return {"job_id": job_id, "status": "paused", "paused": True}
            if status in {"queued", "waiting", "retrying"}:
                work.jobs.patch_job(
                    context,
                    job_id=job_id,
                    expected_statuses=(status,),
                    status="paused",
                    metadata_set={"paused": True},
                    metadata_remove=("pause_requested",),
                )
                result_status = "paused"
            else:
                work.jobs.patch_job(
                    context,
                    job_id=job_id,
                    expected_statuses=(status,),
                    metadata_set={"pause_requested": True},
                )
                result_status = "pause_requested"
            work.commit()
        return {"job_id": job_id, "status": result_status, "paused": True}

    def resume_job(self, context: TenantContext, *, project_id: str,
                   job_id: str) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            rows = work.jobs.query_jobs(
                context,
                job_id=job_id,
                module="audiobook",
                input_fields=(("project_id", project_id),),
                limit=1,
                for_update=True,
            )
            if not rows or rows[0]["job_type"] not in {"audiobook.render-chapter", "audiobook.analyze"}:
                raise KeyError(job_id)
            status = str(rows[0]["status"])
            if status == "paused":
                work.jobs.patch_job(
                    context,
                    job_id=job_id,
                    expected_statuses=(status,),
                    status="queued",
                    available_at_now=True,
                    metadata_set={},
                    metadata_remove=("paused", "pause_requested"),
                )
                result_status = "queued"
            elif status in {"leased", "running"}:
                work.jobs.patch_job(
                    context,
                    job_id=job_id,
                    expected_statuses=(status,),
                    metadata_set={},
                    metadata_remove=("pause_requested",),
                )
                result_status = status
            elif status == "cancel_requested":
                raise ValueError("a cancellation is already in progress")
            else:
                raise ValueError("only paused or active audiobook jobs can be resumed")
            work.commit()
        return {"job_id": job_id, "status": result_status, "resumed": True}

    def pause_render_queue(self, context: TenantContext, *, project_id: str,
                           resume: bool = False) -> dict[str, object]:
        """Pause or resume every render chapter in the current project queue."""
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            rows = work.jobs.query_jobs(
                context,
                module="audiobook",
                job_type="audiobook.render-chapter",
                input_fields=(("project_id", project_id),),
                statuses=("queued", "waiting", "retrying", "leased", "running", "paused"),
                limit=500,
                for_update=True,
            )
            changed = 0
            for job in rows:
                job_id = str(job["id"])
                status = str(job["status"])
                if resume:
                    if status == "paused":
                        work.jobs.patch_job(
                            context,
                            job_id=job_id,
                            expected_statuses=(status,),
                            status="queued",
                            available_at_now=True,
                            metadata_set={},
                            metadata_remove=("paused", "pause_requested"),
                        )
                        changed += 1
                    elif status in {"leased", "running"}:
                        work.jobs.patch_job(
                            context,
                            job_id=job_id,
                            expected_statuses=(status,),
                            metadata_set={},
                            metadata_remove=("pause_requested",),
                        )
                        changed += 1
                elif status in {"queued", "waiting", "retrying"}:
                    work.jobs.patch_job(
                        context,
                        job_id=job_id,
                        expected_statuses=(status,),
                        status="paused",
                        metadata_set={"paused": True},
                        metadata_remove=("pause_requested",),
                    )
                    changed += 1
                elif status in {"leased", "running"}:
                    work.jobs.patch_job(
                        context,
                        job_id=job_id,
                        expected_statuses=(status,),
                        metadata_set={"pause_requested": True},
                    )
                    changed += 1
            work.commit()
        return {"project_id": project_id, "resumed" if resume else "paused": changed}

    def stop_render_queue(self, context: TenantContext, *, project_id: str) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            rows = work.jobs.query_jobs(
                context,
                module="audiobook",
                job_types=("audiobook.render-chapter", "audiobook.assemble-chapter"),
                input_fields=(("project_id", project_id),),
                statuses=("queued", "waiting", "retrying", "leased", "running", "paused", "cancel_requested"),
                limit=500,
                for_update=True,
            )
            for job in rows:
                work.jobs.request_cancel(context, str(job["id"]))
            work.commit()
        return {"project_id": project_id, "stopped": len(rows)}

    def retry_pipeline_job(
        self, context: TenantContext, *, project_id: str, job_id: str,
    ) -> dict[str, object]:
        allowed = {
            "audiobook.ingest": "cpu",
            "audiobook.analyze": "cpu",
            "audiobook.assemble-chapter": "cpu",
        }
        terminal = {"failed", "canceled", "stale"}
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            rows = work.jobs.query_jobs(
                context,
                job_id=job_id,
                module="audiobook",
                input_fields=(("project_id", project_id),),
                limit=1,
                for_update=True,
            )
            if not rows:
                raise KeyError(job_id)
            job = rows[0]
            job_type, status = str(job["job_type"]), str(job["status"])
            if job_type not in allowed:
                raise ValueError("this audiobook job type has its own retry flow")
            if status not in terminal:
                raise ValueError("only terminal audiobook jobs can be retried")
            payload = dict(job["input_payload"] or {})
            projects = _projects(work)
            project = projects.lock_revision_and_run(context, project_id)
            if project is None:
                raise KeyError(project_id)
            if job_type == "audiobook.ingest" and project[0] is not None:
                raise ValueError(
                    "ingest retry is stale because the project already has a canonical source; "
                    "upload the intended source again to create or recover a deliberate revision"
                )
            if job_type == "audiobook.analyze":
                if not project[0] or str(project[0]) != str(payload.get("source_revision_id")):
                    raise ValueError("analysis retry is stale for the current source revision")
            if job_type == "audiobook.assemble-chapter":
                if not project[1] or str(project[1]) != str(payload.get("render_run_id")):
                    raise ValueError("assembly retry is stale for the current render run")
            retry_id = f"ab:retry:{uuid4().hex}"
            work.jobs.create_job(context, {
                "id": retry_id,
                "module": "audiobook",
                "job_type": job_type,
                "resource_class": str(job["resource_class"]) or allowed[job_type],
                "priority": int(job["priority"]),
                "input_payload": payload,
                "max_attempts": max(3, int(job["max_attempts"])),
                "metadata": {"retry_of": job_id},
            })
            work.jobs.patch_job(
                context,
                job_id=job_id,
                expected_statuses=(status,),
                metadata_set={"superseded_by": retry_id},
            )
            if job_type == "audiobook.ingest":
                next_state = "imported" if project[0] is None else None
                PostgresAudiobookRepository(work.connection).register_ingest_request(
                    context, project_id=project_id, job_id=retry_id,
                )
            else:
                next_state = {
                    "audiobook.analyze": "analyzing",
                    "audiobook.assemble-chapter": "mastering",
                }[job_type]
            if next_state is not None:
                projects.set_state(context, project_id, next_state)
            work.commit()
        return {"job_id": retry_id, "retry_of": job_id, "type": job_type}

    def reclassify_source(
        self, context: TenantContext, *, project_id: str,
        custom_rules: str | None = None,
        extraction_only: bool = False,
    ) -> dict[str, str]:
        """Queue either quote extraction for review or explicit speaker classification."""
        if custom_rules is not None:
            custom_rules = self._normalize_classification_rules(custom_rules)
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_with_current_revision(context, project_id)
            if project is None:
                raise KeyError(project_id)
            requested_rules = custom_rules
            custom_rules = custom_rules if custom_rules is not None else str(project[6] or "")
            source_revision_id = project[0]
            if not source_revision_id:
                raise ValueError("project has no canonical source")

            active_statuses = ("queued", "waiting", "retrying", "leased", "running", "paused", "cancel_requested")
            active_analysis = work.jobs.query_jobs(
                context,
                module="audiobook",
                job_type="audiobook.analyze",
                input_fields=(("project_id", project_id), ("source_revision_id", str(source_revision_id))),
                statuses=active_statuses,
                limit=1,
                for_update=True,
            )
            active_ingest = work.jobs.query_jobs(
                context,
                module="audiobook",
                job_type="audiobook.ingest",
                input_fields=(("project_id", project_id),),
                statuses=active_statuses,
                limit=1,
                for_update=True,
            )
            if active_analysis or active_ingest:
                raise ValueError("classification is already running")

            stale_detector = projects.has_stale_spans(
                context, str(source_revision_id), DETECTOR_VERSION,
            )
            needs_style_rediscovery = projects.has_open_missed_dialogue(
                context, str(source_revision_id),
            )
            if not extraction_only and (
                str(project[4]) != EXTRACTOR_VERSION or stale_detector
                or custom_rules != str(project[7] or "")
            ):
                raise ValueError("Extract quotes using the current rules, review the quote spans, then classify text.")
            needs_reextract = extraction_only

            if requested_rules is not None:
                self._save_classification_rules(work, context, project_id, custom_rules)
            render_run_id = project[1]
            if render_run_id:
                cancel_render_run_jobs(work.jobs, context, str(render_run_id))

            if needs_reextract:
                job_id = f"ab:reextract:{uuid4().hex}"
                extraction_settings = dict(project[5] or {})
                work.jobs.create_job(context, {
                    "id": job_id,
                    "module": "audiobook",
                    "job_type": "audiobook.ingest",
                    "resource_class": "cpu",
                    "priority": 0,
                    "input_payload": {
                        "project_id": project_id,
                        "source_asset_id": str(project[2]),
                        "source_format": str(project[3]),
                        "extraction_settings": extraction_settings,
                        "force_reclassify": True,
                        "custom_rules": custom_rules,
                    },
                    "metadata": {
                        "reason": "user_requested_quote_extraction",
                        "migration": {
                            "from_source_revision_id": str(source_revision_id),
                            "from_extractor_version": str(project[4]),
                            "to_extractor_version": EXTRACTOR_VERSION,
                            "to_span_detector_version": DETECTOR_VERSION,
                            "dialogue_style_rediscovery": needs_style_rediscovery,
                        },
                    },
                    "max_attempts": 3,
                })
                next_state = "ingesting"
                PostgresAudiobookRepository(work.connection).register_ingest_request(
                    context, project_id=project_id, job_id=job_id,
                )
            else:
                job_id = f"ab:reclassify:{uuid4().hex}"
                work.jobs.create_job(context, {
                    "id": job_id,
                    "module": "audiobook",
                    "job_type": "audiobook.analyze",
                    "resource_class": "cpu",
                    "priority": 0,
                    "input_payload": {
                        "project_id": project_id,
                        "source_revision_id": str(source_revision_id),
                        "force_reclassify": True,
                        "custom_rules": custom_rules,
                    },
                    "metadata": {"reason": "user_requested_reclassification"},
                    "max_attempts": 3,
                })
                next_state = "analyzing"

            projects.set_state_clearing_render(context, project_id, next_state)
            work.commit()

        return {
            "job_id": job_id,
            "source_revision_id": str(source_revision_id),
        }

    @staticmethod
    def _render_engine(provider_id: str | None, model_id: str | None) -> tuple[str, str]:
        """The engine a render names; by default Omnix's selected TTS engine."""
        if provider_id and model_id:
            return provider_id, model_id
        identity = current_model_identity(provider_id or None)
        return str(identity["provider_id"]), str(model_id or identity["model_id"])

    def start_render(
        self, context: TenantContext, *, project_id: str,
        provider_id: str | None = None, model_id: str | None = None,
        model_revision: str, generation_parameters: dict[str, object] | None = None,
        seed: int | None = None,
    ) -> dict[str, object]:
        if not model_revision.strip():
            raise ValueError("a pinned model revision is required for reproducible rendering")
        if seed is not None or "seed" in (generation_parameters or {}):
            raise ValueError("this TTS provider does not apply generation seeds")
        provider_id, model_id = self._render_engine(provider_id, model_id)
        assert_model_revision(provider_id, model_id, model_revision)
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_render_source(context, project_id)
            if project is None:
                raise KeyError(project_id)
            book_parameters, speaker_parameters = render_generation_parameters(
                provider_id, projects.performance(context, project_id), generation_parameters,
            )
            readiness = check_book_render_readiness(
                work.connection, context, project_id=project_id,
                source_revision_id=project[0],
            )
            if readiness.blockers:
                raise ValueError(" ".join(readiness.blockers))
            renderable_chapters = readiness.chapters
            skipped_chapter_ids = readiness.skipped_chapter_ids
            jobs = []
            render_run_id = f"ab:run:{uuid4().hex}"
            for position, chapter in enumerate(renderable_chapters):
                job_id = f"ab:job:{uuid4().hex}"
                work.jobs.create_job(context, {
                    "id": job_id, "module": "audiobook", "job_type": "audiobook.render-chapter",
                    "resource_class": "gpu:tts:offline", "priority": _render_priority(position),
                    "input_payload": {"project_id": project_id,
                                      "render_run_id": render_run_id,
                                      "source_revision_id": str(project[0]),
                                      "chapter_id": chapter["id"],
                                      "provider_id": provider_id, "model_id": model_id,
                                      "model_revision": model_revision,
                                      "generation_parameters": book_parameters,
                                      "speaker_generation_parameters": speaker_parameters,
                                      "seed": seed},
                    "max_attempts": 5,
                })
                jobs.append(job_id)
            projects.start_render_run(context, project_id, render_run_id)
            work.commit()
        return {
            "project_id": project_id,
            "render_run_id": render_run_id,
            "job_ids": jobs,
            "chapter_count": len(renderable_chapters),
            "source_chapter_count": len(renderable_chapters) + len(skipped_chapter_ids),
            "skipped_chapter_count": len(skipped_chapter_ids),
            "skipped_chapter_ids": skipped_chapter_ids,
        }

    def reorder_render_queue(
        self, context: TenantContext, *, project_id: str, chapter_ids: list[str],
    ) -> dict[str, object]:
        """Render the given waiting chapters first, in this order; the rest keep their order after them."""
        if len(set(chapter_ids)) != len(chapter_ids):
            raise ValueError("each chapter can appear only once in the render order")
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id, lock=True)
            row = _projects(work).lock_revision_and_run(context, project_id)
            render_run_id = str(row[1] or "") if row else ""
            if not render_run_id:
                raise ValueError("no render run is active")
            waiting = _render_queue(work.jobs.query_jobs(
                context, module="audiobook", job_type="audiobook.render-chapter",
                statuses=WAITING_RENDER_STATUSES, input_fields=(("render_run_id", render_run_id),),
                order_by="created_asc", limit=1000, for_update=True,
            ))
            by_chapter = {str((job["input_payload"] or {}).get("chapter_id") or ""): job for job in waiting}
            if any(chapter_id not in by_chapter for chapter_id in chapter_ids):
                raise ValueError("only chapters still waiting to render can be reordered")
            order = [*chapter_ids, *(chapter_id for chapter_id in by_chapter if chapter_id not in chapter_ids)]
            for position, chapter_id in enumerate(order):
                job = by_chapter[chapter_id]
                if int(job["priority"]) != _render_priority(position):
                    work.jobs.patch_job(
                        context, job_id=str(job["id"]), expected_statuses=WAITING_RENDER_STATUSES,
                        priority=_render_priority(position),
                    )
            work.commit()
        return {"render_run_id": render_run_id, "chapter_ids": order}

    def open_chapter_audio(
        self, context: TenantContext, *, project_id: str, chapter_id: str,
    ) -> Path:
        """The current render run's assembled audio for one chapter, as a local file."""
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id)
            projects = _projects(work)
            row = projects.revision_and_run(context, project_id)
            render_run_id = str(row[1] or "") if row else ""
            assembly_id = (_completed_chapter_assemblies(work, context, render_run_id).get(chapter_id)
                           if render_run_id else None)
            assembly = projects.chapter_assemblies(context, [assembly_id]).get(assembly_id) if assembly_id else None
            asset = work.assets.asset_fields(context, assembly[0], active_only=True) if assembly else None
            work.rollback()
        if asset is None:
            raise KeyError(chapter_id)
        storage_key, checksum = str(asset["storage_key"]), str(asset["checksum_sha256"])
        local_path = getattr(self.blobs, "local_path", None)
        if callable(local_path):
            path = Path(local_path(storage_key))
            if not path.is_file():
                raise KeyError(chapter_id)
            return path
        # A remote blob store: stage one checksum-verified copy, shared by every request.
        target = resources_data_root() / "cache" / "blobs" / checksum[:2] / f"{checksum}.wav"
        if not target.is_file():
            self.blobs.copy_verified_to(storage_key, target, expected_checksum=checksum)
        return target

    def start_preview(
        self, context: TenantContext, *, project_id: str,
        chapter_id: str, span_id: str, model_revision: str,
        provider_id: str | None = None, model_id: str | None = None,
        generation_parameters: dict[str, object] | None = None,
        seed: int | None = None,
        voice_profile_id: str | None = None,
    ) -> dict[str, str]:
        if not model_revision.strip():
            raise ValueError("a pinned model revision is required")
        if seed is not None or "seed" in (generation_parameters or {}):
            raise ValueError("this TTS provider does not apply generation seeds")
        provider_id, model_id = self._render_engine(provider_id, model_id)
        assert_model_revision(provider_id, model_id, model_revision)
        voice_revision_hash = None
        if voice_profile_id is not None:
            profile = next((item for item in discover_canonical_voice_clone_assets()
                            if item.id == voice_profile_id), None)
            if profile is None or not asset_available(profile):
                raise ValueError("preview voice profile is unavailable")
            voice_revision_hash = voice_reference_revision(materialize_asset(profile))
        with unit_of_work(self.database) as work:
            projects = _projects(work)
            project = projects.lock_current_revision(context, project_id)
            if project is None:
                raise KeyError(project_id)
            if not projects.chapter_in_revision(context, chapter_id, project[0]):
                raise ValueError("chapter is not in the current source")
            units = load_chapter_units(work.connection, context, project_id=project_id,
                                       chapter_id=chapter_id, span_id=span_id,
                                       voice_profile_id=voice_profile_id,
                                       voice_revision_hash=voice_revision_hash)
            if not any(unit.span_id == span_id for unit in units):
                raise ValueError("span is not renderable in this chapter")
            book_parameters, speaker_parameters = render_generation_parameters(
                provider_id, projects.performance(context, project_id), generation_parameters,
            )
            job_id = f"ab:preview:{uuid4().hex}"
            work.jobs.create_job(context, {
                "id": job_id, "module": "audiobook", "job_type": "audiobook.preview-span",
                "resource_class": "gpu:tts:preview", "priority": 50,
                "input_payload": {"project_id": project_id, "chapter_id": chapter_id,
                                  "source_revision_id": str(project[0]), "span_id": span_id,
                                  "provider_id": provider_id, "model_id": model_id,
                                  "model_revision": model_revision,
                                  "generation_parameters": book_parameters,
                                  "speaker_generation_parameters": speaker_parameters,
                                  "voice_profile_id": voice_profile_id,
                                  "voice_revision_hash": voice_revision_hash,
                                  "seed": seed},
                "max_attempts": 3,
            })
            work.commit()
        return {"job_id": job_id, "span_id": span_id}

    def read_preview(self, context: TenantContext, *, project_id: str,
                     job_id: str) -> bytes:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id)
            jobs = work.jobs.query_jobs(
                context,
                job_id=job_id,
                module="audiobook",
                job_type="audiobook.preview-span",
                statuses=("completed",),
                input_fields=(("project_id", project_id),),
                limit=1,
            )
            job = jobs[0] if jobs else None
            refs = job["output_refs"][0] if job and job["output_refs"] else {}
            asset_id = refs.get("audio_asset_id")
            asset = work.assets.get_asset(context, str(asset_id)) if asset_id else None
            valid_render = False
            render_id = refs.get("render_id")
            if render_id and asset is not None:
                render = _projects(work).render_audio(context, str(render_id))
                valid_render = bool(
                    render
                    and str(render[0]) == asset["id"]
                    and str(render[1]) == asset["checksum_sha256"]
                )
            valid_generation = bool(
                job
                and asset is not None
                and "render_ids" in refs
                and asset["generation_job_id"] == job["id"]
            )
            if not (
                job
                and asset
                and asset["module"] == "audiobook"
                and asset["lifecycle_status"] == "active"
                and (valid_render or valid_generation)
            ):
                asset = None
            work.rollback()
        if asset is None:
            raise KeyError(job_id)
        return self.blobs.read_bytes(
            str(asset["storage_key"]),
            expected_checksum=str(asset["checksum_sha256"]),
        )

    def start_export(self, context: TenantContext, *, project_id: str,
                     format: str = "m4b") -> dict[str, str]:
        return create_export_job(self.database, self.blobs, context,
                                 project_id=project_id, format=format)

    def list_exports(self, context: TenantContext, project_id: str) -> list[dict[str, object]]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id)
            rows = _projects(work).list_exports(context, project_id)
            work.rollback()
        return [{"id": row[0], "format": row[1], "manifest_hash": row[2],
                 "asset_id": row[3], "created_at": row[4].isoformat(),
                 "byte_size": row[5], "asset_created_at": row[6].isoformat()} for row in rows]

    def open_export(self, context: TenantContext, *, project_id: str,
                    export_id: str) -> tuple[BinaryIO, str, str]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id)
            row = _projects(work).export_blob(context, project_id, export_id)
            work.rollback()
        if row is None:
            raise KeyError(export_id)
        from .export import FORMAT_MIME

        return (self.blobs.open_verified(str(row[1]), expected_checksum=str(row[2])),
                FORMAT_MIME[str(row[0])], str(row[0]))

    def read_export(self, context: TenantContext, *, project_id: str,
                    export_id: str) -> tuple[bytes, str, str]:
        """Bounded test/helper API; browser delivery uses open_export instead."""
        handle, mime, format = self.open_export(context, project_id=project_id,
                                                export_id=export_id)
        with handle:
            return handle.read(), mime, format

    def export_report(self, context: TenantContext, *, project_id: str,
                      export_id: str) -> dict[str, object]:
        with unit_of_work(self.database) as work:
            self._require_active_project(work.connection, context, project_id)
            work.rollback()
        return audit_export(self.database, self.blobs, context,
                            project_id=project_id, export_id=export_id)

    def submit_source(
        self, context: TenantContext, *, project_id: str,
        source_format: str, content: bytes | BinaryIO, filename: str,
        extraction_settings: dict[str, object] | None = None,
    ) -> dict[str, str]:
        """Store a source and queue its ingestion.

        ``content`` is bytes or a seekable binary stream; a stream is hashed
        and stored in bounded chunks, never read into memory whole.
        """
        if source_format not in _MIME:
            raise UnsupportedSource(f"unsupported source format: {source_format}")
        stream = BytesIO(content) if isinstance(content, (bytes, bytearray)) else content
        size, source_hash = _measure_source(stream)
        if not size or size > MAX_SOURCE_BYTES:
            raise UnsupportedSource("source is empty or exceeds the supported size limit")
        extraction_settings = normalize_extraction_settings(source_format, extraction_settings)
        asset_id = f"ab:source:{uuid4().hex}"
        storage_key = f"audiobook/source/{asset_id.split(':')[-1]}-{source_hash}"
        job_id = f"ab:job:{uuid4().hex}"
        blob = self.blobs.put_stream(storage_key, stream, max_bytes=MAX_SOURCE_BYTES)
        if blob["checksum_sha256"] != source_hash:
            self.blobs.delete(storage_key)
            raise UnsupportedSource("the source changed while it was being stored")
        try:
            with unit_of_work(self.database) as work:
                repository = PostgresAudiobookRepository(work.connection)
                if repository.get_project(context, project_id) is None:
                    raise KeyError(project_id)
                rules_row = _projects(work).lock_classification_rules(context, project_id)
                if rules_row is None:
                    raise KeyError(project_id)
                custom_rules = str(rules_row[0] or "")
                work.assets.create(context, {
                    "id": asset_id, "module": "audiobook", "asset_type": "source",
                    "mime_type": _MIME[source_format], "byte_size": blob["byte_size"],
                    "checksum_sha256": blob["checksum_sha256"],
                    "storage_provider": blob["storage_provider"], "storage_key": storage_key,
                    "metadata": {"filename": filename, "source_format": source_format,
                                 "extraction_settings": extraction_settings},
                })
                work.jobs.create_job(context, {
                    "id": job_id, "module": "audiobook", "job_type": "audiobook.ingest",
                    "resource_class": "cpu", "priority": 0,
                    "input_payload": {"project_id": project_id, "source_asset_id": asset_id,
                                      "source_format": source_format,
                                      "extraction_settings": extraction_settings,
                                      "custom_rules": custom_rules},
                    "max_attempts": 3,
                })
                repository.register_ingest_request(
                    context, project_id=project_id, job_id=job_id,
                )
                work.commit()
        except Exception:
            if blob["created"]:
                self.blobs.delete(storage_key)
            raise
        return {"project_id": project_id, "source_asset_id": asset_id, "job_id": job_id}
