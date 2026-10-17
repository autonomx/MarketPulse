"""First-class conversational objective continuity for Omnix routing.

ActiveObjective is reference state, not execution authority. It survives ordinary
chat turns so terse follow-ups can be interpreted semantically without replaying
an unlimited transcript or relying on regexes to choose an execution lane.
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception
from app.config.env import env_str as _env_str

import hashlib
import json
import re
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field


ObjectiveStatus = Literal[
    "active",
    "blocked",
    "awaiting_user",
    "completed",
    "abandoned",
    "cancelled",
]


class ObjectiveRevisionEntry(BaseModel):
    """One user-authored change to an active conversational objective."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_id: str | None = Field(default=None, max_length=240)
    relation: Literal["continue", "resume", "revise"] = "continue"
    disposition: Literal[
        "continue_objective",
        "revise_objective",
        "replay_objective",
        "response_only_continuation",
    ] = "continue_objective"
    request: str = Field(min_length=1)


class ActiveObjective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: str = Field(min_length=1, max_length=160)
    objective_type: str = Field(min_length=1, max_length=80)
    # Compatibility projection for older metadata consumers. New code should
    # use base_request + revisions and latest_user_request().
    canonical_request: str = Field(min_length=1)
    base_request: str | None = None
    revisions: list[ObjectiveRevisionEntry] = Field(default_factory=list, max_length=128)
    status: ObjectiveStatus = "active"
    blocking_reason: str | None = Field(default=None, max_length=1000)
    workspace_name: str | None = Field(default=None, max_length=240)
    originating_turn_id: str | None = Field(default=None, max_length=240)
    last_relevant_turn_id: str | None = Field(default=None, max_length=240)
    run_id: str | None = Field(default=None, max_length=240)
    profile: str | None = Field(default=None, max_length=80)

    def latest_user_request(self) -> str:
        """Return the latest user-authored request that can carry execution authority."""

        for revision in reversed(self.revisions):
            if revision.disposition not in {
                "response_only_continuation",
                "replay_objective",
            }:
                return revision.request
        return str(self.base_request or self.canonical_request).strip()

    def effective_objective_text(self) -> str:
        base = str(self.base_request or self.canonical_request).strip()
        if not self.revisions:
            return base
        parts = [base]
        for revision in self.revisions:
            if revision.disposition in {
                "replay_objective",
                "response_only_continuation",
            }:
                continue
            if revision.relation == "revise":
                parts = [revision.request]
            elif revision.relation == "continue":
                parts.append(f"Later steering: {revision.request}")
        return "\n".join(parts)

    def reference_text(self, *, max_request_chars: int = 8000) -> str:
        """Return a strictly bounded routing projection of exact durable history.

        Persisted objective text is never truncated. Only the reference projection
        supplied to semantic routing is clipped, with a single total text budget so
        long base requests plus revision history cannot multiply that budget.
        """

        request = self.effective_objective_text()
        text_budget = max(1024, int(max_request_chars))

        def _clip(value: str, limit: int) -> str:
            text = str(value or "")
            limit = max(1, int(limit))
            if len(text) <= limit:
                return text
            marker = "\n...[objective text omitted from routing projection]...\n"
            if limit <= len(marker) + 2:
                return text[:limit]
            available = limit - len(marker)
            head = max(1, available * 3 // 4)
            tail = max(1, available - head)
            return text[:head] + marker + text[-tail:]

        latest_budget = max(160, min(1800, text_budget // 4))
        base_budget = max(160, min(1600, text_budget // 5))
        revision_budget_total = max(256, min(2400, text_budget // 3))
        effective_budget = max(
            256,
            text_budget - latest_budget - base_budget - revision_budget_total,
        )

        payload = self.model_dump(
            mode="json",
            exclude_none=True,
            exclude={"canonical_request", "base_request", "revisions"},
        )
        payload["canonical_request"] = _clip(
            self.latest_user_request(),
            latest_budget,
        )
        payload["base_request"] = _clip(
            str(self.base_request or self.canonical_request),
            base_budget,
        )

        selected_revisions = self.revisions[-8:]
        per_revision_budget = (
            max(24, revision_budget_total // len(selected_revisions))
            if selected_revisions
            else revision_budget_total
        )
        projected_revisions = []
        for revision in selected_revisions:
            row = revision.model_dump(mode="json", exclude_none=True)
            row["request"] = _clip(revision.request, per_revision_budget)
            projected_revisions.append(row)
        payload["revisions"] = projected_revisions
        payload["revision_count"] = len(self.revisions)
        if len(self.revisions) > len(projected_revisions):
            payload["older_revisions_omitted"] = (
                len(self.revisions) - len(projected_revisions)
            )

        payload["effective_objective"] = _clip(request, effective_budget)
        payload["effective_objective_digest"] = hashlib.sha256(
            request.encode("utf-8")
        ).hexdigest()
        payload["effective_objective_truncated_for_routing"] = (
            len(request) > effective_budget
        )
        payload["routing_projection_text_budget"] = text_budget
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class RoutingEnvironment(BaseModel):
    """Current-turn environment facts supplied to semantic routing.

    These facts can resolve statements such as "I attached the folder now", but
    they never grant execution authority by themselves.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    active_workspace: str | None = Field(default=None, max_length=240)
    workspace_source: Literal["turn_attachment", "configured_default", "none"] = "none"
    workspace_attached_this_turn: bool = False
    attachment_kinds: list[str] = Field(default_factory=list, max_length=12)
    attachment_count: int = Field(default=0, ge=0, le=100)
    agent_mode_selected: bool = False


_WORKSPACE_UNAVAILABLE = re.compile(
    r"(?:don'?t have access to the project folder|coding workspace.*(?:not available|only the image)|"
    r"workspace editor.*(?:not available|unavailable)|no coding workspace is configured)",
    re.I,
)
_STRONG_CONTINUITY = re.compile(
    r"\b(?:again|retry|re-?try|continue|resume|also|additionally|previous|before|"
    r"while\s+you(?:'re|\s+are)\s+at\s+it|same\s+(?:thing|task|change)|"
    r"attached|include(?:d)?\s+(?:the\s+)?(?:folder|workspace|project))\b",
    re.I,
)
_TERSE_REFERENCE = re.compile(
    r"^(?:please\s+)?(?:do|fix|change|update|run|try)\s+(?:it|that|this)(?:\s+again)?[.!\s]*$",
    re.I,
)

_EXPLICIT_RETRY = re.compile(
    r"(?:\b(?:try|do|run|implement|apply|make)\b.{0,80}\bagain\b|"
    r"^(?:please\s+)?(?:retry|re-?try|repeat)(?:\b|$))",
    re.I,
)
_EXPLICIT_CORRECTION = re.compile(
    r"^(?:actually\b|instead\b|forget\s+that\b|correction\b|one\s+correction\b|"
    r"do\s+not\b|don'?t\b|never\s+mind\b)",
    re.I,
)
_EXPLICIT_ADDITION = re.compile(
    r"^(?:also\b|add(?:itionally)?\b|include\b|keep\b|and\b|"
    r"while\s+you(?:'re|\s+are)\s+at\s+it\b)",
    re.I,
)
_ENVIRONMENT_ONLY_RETRY = re.compile(
    r"\b(?:try|retry|re-?try|do\s+it)\s+again\b.{0,120}"
    r"\b(?:code|coding|repo(?:sitory)?|workspace|project|folder)\b",
    re.I,
)


def _workspace_name(value: str | None) -> str | None:
    text = str(value or "").strip().rstrip("\\/")
    if not text:
        return None
    return re.split(r"[\\/]", text)[-1] or None


def build_routing_environment(user_message: Any) -> RoutingEnvironment:
    metadata = getattr(user_message, "metadata", {}) or {}
    selected = str(metadata.get("workspace_root") or "").strip()
    configured = str(_env_str("OMNIX_AGENT_DEFAULT_REPOSITORY", "") or "").strip()
    if selected:
        workspace = _workspace_name(selected)
        source = "turn_attachment"
    elif configured:
        workspace = _workspace_name(configured)
        source = "configured_default"
    else:
        workspace = None
        source = "none"

    kinds: list[str] = []
    if selected:
        kinds.append("local_folder")
    raw_images = metadata.get("image_data_urls")
    image_values = (
        [value for value in raw_images if isinstance(value, str) and value]
        if isinstance(raw_images, list)
        else []
    )
    legacy_image = metadata.get("image_data_url")
    if isinstance(legacy_image, str) and legacy_image and legacy_image not in image_values:
        image_values.insert(0, legacy_image)
    if image_values:
        kinds.append("image")
    raw_attachments = metadata.get("attachments")
    attachment_count = (len(raw_attachments) if isinstance(raw_attachments, list) else 0) + len(image_values)
    if isinstance(raw_attachments, list) and raw_attachments:
        kinds.append("file")

    return RoutingEnvironment(
        active_workspace=workspace,
        workspace_source=cast(Any, source),
        workspace_attached_this_turn=bool(selected),
        attachment_kinds=list(dict.fromkeys(kinds)),
        attachment_count=attachment_count,
        agent_mode_selected=bool(metadata.get("agent_mode")),
    )


def make_active_objective(
    *,
    canonical_request: str,
    profile: str,
    status: ObjectiveStatus,
    blocking_reason: str | None = None,
    workspace_name: str | None = None,
    originating_turn_id: str | None = None,
    last_relevant_turn_id: str | None = None,
    run_id: str | None = None,
    base_request: str | None = None,
    revisions: list[ObjectiveRevisionEntry] | None = None,
) -> ActiveObjective:
    request = str(canonical_request or "").strip()
    profile_id = str(profile or "unknown").strip() or "unknown"
    origin = str(originating_turn_id or "").strip() or None
    run = str(run_id or "").strip() or None
    if run:
        objective_id = f"run:{run}"
    else:
        digest = hashlib.sha256(
            f"{origin or ''}\n{profile_id}\n{request}".encode("utf-8")
        ).hexdigest()[:24]
        objective_id = f"chat-objective:{digest}"
    return ActiveObjective(
        objective_id=objective_id,
        objective_type=profile_id,
        canonical_request=request,
        base_request=str(base_request or request).strip(),
        revisions=list(revisions or []),
        status=status,
        blocking_reason=(str(blocking_reason).strip()[:1000] if blocking_reason else None),
        workspace_name=workspace_name,
        originating_turn_id=origin,
        last_relevant_turn_id=(
            str(last_relevant_turn_id or "").strip() or origin
        ),
        run_id=run,
        profile=profile_id,
    )


def _objective_from_message(messages: list[Any], index: int) -> ActiveObjective | None:
    message = messages[index]
    metadata = getattr(message, "metadata", {}) or {}

    explicit = metadata.get("active_objective")
    raw_run = metadata.get("agent_run")
    explicit_objective: ActiveObjective | None = None
    if isinstance(explicit, dict):
        try:
            explicit_objective = ActiveObjective.model_validate(explicit)
        except Exception as exc:
            log_recovered_exception("active objective snapshot lookup", exc, level="DEBUG")
            explicit_objective = None

    # A terminal run snapshot is newer and more authoritative state than a
    # carried-forward objective reference on the same message. For nonterminal
    # runs, however, agent_run.task is the immutable RunSpec task and can be
    # stale after steering. Preserve the explicit canonical objective while
    # taking status/run identity from the runtime snapshot.
    if getattr(message, "role", None) == "assistant" and isinstance(raw_run, dict):
        task = str(raw_run.get("task") or "").strip()
        profile = str(raw_run.get("profile") or "").strip()
        raw_status = str(raw_run.get("status") or "").strip().casefold()
        if task and profile:
            if raw_status in {"completed"}:
                status: ObjectiveStatus = "completed"
            elif raw_status in {"cancelled", "canceled"}:
                status = "cancelled"
            elif raw_status in {"waiting_for_approval", "waiting_for_input", "paused"}:
                status = "awaiting_user"
            elif raw_status in {"failed", "rejected"}:
                status = "blocked"
            else:
                status = "active"
            start = metadata.get("agent_start")
            reason = None
            if isinstance(start, dict):
                reason = str(start.get("reason") or start.get("error") or "").strip() or None
            if reason is None:
                reason = str(raw_run.get("last_error") or "").strip() or None
            source = messages[index - 1] if index > 0 else None
            source_meta = getattr(source, "metadata", {}) or {}
            run_id = str(raw_run.get("run_id") or "").strip() or None

            if status not in {"completed", "cancelled"} and explicit_objective is not None:
                same_run = (
                    explicit_objective.run_id is None
                    or run_id is None
                    or explicit_objective.run_id == run_id
                )
                same_profile = explicit_objective.profile in {None, profile}
                if same_run and same_profile:
                    return make_active_objective(
                        canonical_request=explicit_objective.canonical_request,
                        base_request=explicit_objective.base_request,
                        revisions=list(explicit_objective.revisions),
                        profile=profile,
                        status=status,
                        blocking_reason=reason or explicit_objective.blocking_reason,
                        workspace_name=(
                            explicit_objective.workspace_name
                            or _workspace_name(source_meta.get("workspace_root"))
                        ),
                        originating_turn_id=explicit_objective.originating_turn_id,
                        last_relevant_turn_id=(
                            str(getattr(message, "id", "") or "").strip()
                            or explicit_objective.last_relevant_turn_id
                        ),
                        run_id=run_id or explicit_objective.run_id,
                    )

            return make_active_objective(
                canonical_request=task,
                profile=profile,
                status=status,
                blocking_reason=reason,
                workspace_name=_workspace_name(source_meta.get("workspace_root")),
                originating_turn_id=(
                    str(getattr(source, "id", "") or "").strip() or None
                    if getattr(source, "role", None) == "user"
                    else None
                ),
                last_relevant_turn_id=str(getattr(message, "id", "") or "").strip() or None,
                run_id=run_id,
            )

    if explicit_objective is not None:
        return explicit_objective

    if getattr(message, "role", None) != "assistant":
        return None

    if _WORKSPACE_UNAVAILABLE.search(str(getattr(message, "content", "") or "")):
        source = messages[index - 1] if index > 0 else None
        if getattr(source, "role", None) == "user":
            task = str(getattr(source, "content", "") or "").strip()
            if task:
                source_meta = getattr(source, "metadata", {}) or {}
                return make_active_objective(
                    canonical_request=task,
                    profile="coding",
                    status="blocked",
                    blocking_reason="workspace_required",
                    workspace_name=_workspace_name(source_meta.get("workspace_root")),
                    originating_turn_id=str(getattr(source, "id", "") or "").strip() or None,
                    last_relevant_turn_id=str(getattr(message, "id", "") or "").strip() or None,
                )
    return None


def advance_active_objective(
    objective: ActiveObjective | None,
    *,
    request: str,
    profile: str,
    relation: Literal["none", "continue", "resume", "revise"],
    disposition: Literal[
        "new_objective",
        "continue_objective",
        "revise_objective",
        "replay_objective",
        "response_only_continuation",
    ],
    turn_id: str | None = None,
    run_id: str | None = None,
    status: ObjectiveStatus = "active",
    workspace_name: str | None = None,
) -> ActiveObjective:
    """Advance objective history using only user-authored request text."""

    clean = str(request or "").strip()
    if not clean:
        raise ValueError("objective request is required")
    profile_id = str(profile or (objective.profile if objective else "") or "unknown").strip()
    if objective is None or relation == "none" or disposition == "new_objective":
        return make_active_objective(
            canonical_request=clean,
            base_request=clean,
            profile=profile_id,
            status=status,
            workspace_name=workspace_name,
            originating_turn_id=turn_id,
            last_relevant_turn_id=turn_id,
            run_id=run_id,
        )

    revisions = list(objective.revisions)
    prior_authoritative_request = objective.latest_user_request()
    entry_relation: Literal["continue", "resume", "revise"] = (
        relation if relation in {"continue", "resume", "revise"} else "continue"
    )
    revisions.append(
        ObjectiveRevisionEntry(
            turn_id=turn_id,
            relation=entry_relation,
            disposition=disposition,
            request=clean,
        )
    )
    canonical = (
        prior_authoritative_request
        if disposition in {"replay_objective", "response_only_continuation"}
        else clean
    )
    return make_active_objective(
        canonical_request=canonical,
        base_request=objective.base_request or objective.canonical_request,
        revisions=revisions,
        profile=profile_id,
        status=status,
        blocking_reason=objective.blocking_reason,
        workspace_name=workspace_name or objective.workspace_name,
        originating_turn_id=objective.originating_turn_id,
        last_relevant_turn_id=turn_id or objective.last_relevant_turn_id,
        run_id=run_id or objective.run_id,
    )


def resolve_active_objective(session: Any, user_message: Any) -> ActiveObjective | None:
    """Resolve the newest persisted objective without treating it as authority."""

    current_id = str(getattr(user_message, "id", "") or "").strip()
    messages = list(getattr(session, "messages", []) or [])
    for index in range(len(messages) - 1, -1, -1):
        candidate = messages[index]
        if current_id and str(getattr(candidate, "id", "") or "").strip() == current_id:
            continue
        objective = _objective_from_message(messages, index)
        if objective is None:
            continue
        if objective.status in {"completed", "abandoned", "cancelled"}:
            return None
        return objective
    return None


def normalize_objective_relation(
    content: str,
    relation: str | None,
) -> str:
    """Normalize only explicit continuity/correction language.

    SemanticTask remains responsible for understanding what the user means.
    This deterministic layer protects durable objective history from a small
    set of unambiguous discourse markers: explicit retries resume prior work,
    explicit corrections revise it, and explicit additions cannot accidentally
    discard the prior objective if the model labels them as a revision.
    """

    text = " ".join(str(content or "").strip().split())
    normalized = str(relation or "none").strip().casefold() or "none"
    if normalized not in {"none", "continue", "resume", "revise"}:
        normalized = "none"
    if not text:
        return normalized
    if _EXPLICIT_RETRY.search(text):
        return "resume"
    if _EXPLICIT_CORRECTION.search(text):
        return "revise"
    if _EXPLICIT_ADDITION.search(text) and normalized in {"none", "revise"}:
        # "Also/add/include/and ..." is an unambiguous continuation marker.
        # Do not let a semantic parser's relation=None detach the turn from an
        # active objective. compile_turn_plan still resets the relation to none
        # when no active objective exists.
        return "continue"
    return normalized


def objective_continuity_candidate(content: str) -> bool:
    """Detect only that semantic context is required; never choose a lane."""

    text = " ".join(str(content or "").strip().split())
    if not text:
        return False
    if _STRONG_CONTINUITY.search(text) or _EXPLICIT_ADDITION.search(text):
        return True
    if _EXPLICIT_RETRY.search(text):
        return True
    if _TERSE_REFERENCE.fullmatch(text):
        return True
    words = text.split()
    return len(words) <= 8 and bool(
        re.search(r"\b(?:it|that|this|same|too)\b", text, re.I)
    )


def objective_resume_replays_prior_request(content: str) -> bool:
    """Return true only when a resume message delegates its action text to prior context.

    SemanticTask may correctly label both "try that exact request again" and
    "run the focused test again" as resume-like discourse. Only the former is
    incomplete without the prior canonical request. A complete retry command
    must remain authoritative as written instead of replaying an older task.
    """

    text = " ".join(str(content or "").strip().split())
    if not text:
        return False
    if _TERSE_REFERENCE.fullmatch(text):
        return True
    if _ENVIRONMENT_ONLY_RETRY.search(text):
        return True
    if re.fullmatch(
        r"(?:please\s+)?(?:try|retry|re-?try|repeat)(?:\s+(?:it|that|this))?(?:\s+again)?[.!\s]*",
        text,
        re.I,
    ):
        return True
    if not _EXPLICIT_RETRY.search(text):
        return False
    if re.search(
        r"\b(?:it|that|this)\b.{0,60}\b(?:thing|task|request|implementation|change|fix|command)\b",
        text,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:same|exact|previous|prior)\s+(?:thing|task|request|implementation|change|fix|command)\b",
        text,
        re.I,
    ):
        return True
    return False


__all__ = [
    "ActiveObjective",
    "ObjectiveRevisionEntry",
    "advance_active_objective",
    "RoutingEnvironment",
    "build_routing_environment",
    "make_active_objective",
    "normalize_objective_relation",
    "objective_continuity_candidate",
    "objective_resume_replays_prior_request",
    "resolve_active_objective",
]
