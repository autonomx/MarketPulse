"""Independent-review execution identity, retry, and protocol semantics.

A reviewer process completing or failing is not itself code-quality evidence.
This module keeps execution/protocol attempts separate from substantive
``ReviewResult`` objects so infrastructure failures cannot consume implementation
repair attempts or masquerade as review findings.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
from collections.abc import Iterable

from .coding_quality import review_payload_from_text
from .contracts import (
    AgentRunSnapshot,
    ModelRef,
    ReviewAttempt,
    ReviewResult,
    ReviewSnapshot,
    TaskRevision,
    utc_now,
)

REVIEW_PROTOCOL_VERSION = "review-v3-subject-attribution"
_TERMINAL = {"completed", "failed", "cancelled"}


def review_runtime_retry_limit() -> int:
    """Number of retry generations after the initial reviewer execution."""

    raw = str(_env_str("OMNIX_AGENT_REVIEW_RUNTIME_RETRIES", "2") or "2").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 2
    return max(0, min(value, 5))


def review_total_attempt_limit() -> int:
    return 1 + review_runtime_retry_limit()


def reviewer_child_run_id(
    *,
    parent_run_id: str,
    snapshot: ReviewSnapshot,
    reviewer_slot: int,
    runtime_attempt: int,
    model: ModelRef,
    protocol_version: str = REVIEW_PROTOCOL_VERSION,
) -> str:
    """Return a retry-safe durable reviewer identity.

    Identity is bound to every authority-bearing input that may change across a
    steering event or review protocol/model upgrade. Retrying the same immutable
    snapshot therefore creates a fresh child without making stale attempts
    equivalent to the current reviewer execution.
    """

    material = "|".join(
        [
            "review",
            parent_run_id,
            str(snapshot.task_revision_id or ""),
            snapshot.snapshot_id,
            snapshot.workspace_state_id,
            str(max(0, reviewer_slot)),
            str(max(1, runtime_attempt)),
            protocol_version,
            model.provider_id,
            model.model_id,
            str(model.reasoning_effort or ""),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def review_attempt_id(
    *,
    parent_run_id: str,
    reviewer_run_id: str,
    snapshot_id: str,
) -> str:
    return hashlib.sha256(
        f"review-attempt:{parent_run_id}:{snapshot_id}:{reviewer_run_id}".encode("utf-8")
    ).hexdigest()


def new_review_attempt(
    *,
    parent_run_id: str,
    reviewer_run_id: str,
    snapshot: ReviewSnapshot,
    reviewer_slot: int,
    runtime_attempt: int,
    model: ModelRef,
    protocol_version: str = REVIEW_PROTOCOL_VERSION,
) -> ReviewAttempt:
    now = utc_now()
    return ReviewAttempt(
        review_attempt_id=review_attempt_id(
            parent_run_id=parent_run_id,
            reviewer_run_id=reviewer_run_id,
            snapshot_id=snapshot.snapshot_id,
        ),
        run_id=parent_run_id,
        reviewer_run_id=reviewer_run_id,
        review_snapshot_id=snapshot.snapshot_id,
        task_revision_id=snapshot.task_revision_id,
        workspace_state_id=snapshot.workspace_state_id,
        reviewer_slot=reviewer_slot,
        runtime_attempt=runtime_attempt,
        protocol_version=protocol_version,
        model_provider_id=model.provider_id,
        model_id=model.model_id,
        reasoning_effort=model.reasoning_effort,
        status="running",
        retryable=False,
        started_at=now,
        created_at=now,
    )


def review_payload_is_protocol_valid(
    text: str,
    revision: TaskRevision,
    *,
    require_path_claims: bool = True,
) -> bool:
    """Validate independent-review transport/schema before creating ReviewResult."""

    payload = review_payload_from_text(text)
    if not payload:
        return False
    for field in ("requirements", "findings", "missing_tests", "residual_risks"):
        if not isinstance(payload.get(field), list):
            return False

    required_ids = {item.id for item in revision.requirements if item.required}
    observed_ids: set[str] = set()
    for item in payload["requirements"]:
        if not isinstance(item, dict):
            return False
        requirement_id = str(item.get("requirement_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if (
            not requirement_id
            or requirement_id in observed_ids
            or status not in {"satisfied", "partial", "missing", "not_applicable"}
        ):
            return False
        observed_ids.add(requirement_id)

    for finding in payload["findings"]:
        if not isinstance(finding, dict) or not str(finding.get("problem") or "").strip():
            return False
        if require_path_claims:
            if not isinstance(finding.get("subject_paths"), list) or not isinstance(finding.get("context_paths"), list):
                return False
            if any(not isinstance(path, str) for path in finding.get("subject_paths", [])):
                return False
            if any(not isinstance(path, str) for path in finding.get("context_paths", [])):
                return False
        if str(finding.get("severity") or "medium").strip() not in {
            "blocker",
            "high",
            "medium",
            "low",
        }:
            return False

    if any(not isinstance(item, str) for item in payload["missing_tests"]):
        return False
    if any(not isinstance(item, str) for item in payload["residual_risks"]):
        return False
    return required_ids.issubset(observed_ids)


def latest_reviewer_text(events: Iterable[object]) -> str:
    for event in reversed(list(events)):
        if getattr(event, "event_type", None) != "model.message":
            continue
        payload = getattr(event, "payload", {})
        candidate = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        if candidate:
            return candidate
    return ""


def classify_runtime_failure(child: AgentRunSnapshot) -> tuple[str, str, bool]:
    """Classify a terminal reviewer that did not produce a usable verdict.

    The returned tuple is ``(failure_class, failure_reason, retryable)``.
    Local reviewer circuit-breaker exhaustion is retryable against the same
    immutable snapshot; parent/global authority exhaustion and cancellation are
    not. Provider throttling remains distinct from either budget scope.
    """

    reason = str(child.last_error or child.status or "reviewer_runtime_failed").strip()
    folded = reason.casefold()
    if child.status == "cancelled":
        return "cancelled", reason or "reviewer_cancelled", False
    # Check the parent/global authority class before the broad local budget
    # signature so a structured global-budget error can never be mislabeled as a
    # retryable reviewer-local circuit breaker.
    if "parent_global_budget" in folded or "aggregate child" in folded:
        return "parent_global_budget_exhausted", reason, False
    if (
        "agent_run_budget_exhausted" in folded
        or "agent_budget_error" in folded
        or folded.startswith("budget_")
        or "budget_max_" in folded
        or "budget_output_tokens_" in folded
        or "budget_cost_unmeterable_provider" in folded
    ):
        return "reviewer_local_budget_exhausted", reason, True
    if "rate_limit" in folded or "too_many_requests" in folded or " 429" in folded or folded.startswith("429"):
        return "provider_rate_limited", reason, True
    if "provider_unavailable" in folded:
        return "provider_unavailable", reason, True
    if any(
        token in folded
        for token in (
            "model_provider_error",
            "transport",
            "connection",
            "timeout",
            "temporarily unavailable",
        )
    ):
        return "provider_transport_failure", reason, True
    return "runtime_failure", reason, True


def finish_runtime_failed_attempt(
    attempt: ReviewAttempt,
    child: AgentRunSnapshot,
) -> ReviewAttempt:
    failure_class, reason, retryable = classify_runtime_failure(child)
    status = "cancelled" if failure_class == "cancelled" else "runtime_failed"
    return attempt.model_copy(
        update={
            "status": status,
            "failure_class": failure_class,
            "failure_reason": reason[:2000],
            "retryable": retryable,
            "finished_at": utc_now(),
        }
    )


def finish_protocol_failed_attempt(
    attempt: ReviewAttempt,
    *,
    reason: str = "reviewer did not return the required structured verdict",
) -> ReviewAttempt:
    return attempt.model_copy(
        update={
            "status": "protocol_failed",
            "failure_class": "review_protocol_invalid",
            "failure_reason": reason[:2000],
            "retryable": True,
            "finished_at": utc_now(),
        }
    )


def finish_completed_attempt(attempt: ReviewAttempt) -> ReviewAttempt:
    return attempt.model_copy(
        update={
            "status": "completed",
            "failure_class": None,
            "failure_reason": None,
            "retryable": False,
            "finished_at": utc_now(),
        }
    )


def latest_attempt_by_slot(attempts: Iterable[ReviewAttempt]) -> dict[int, ReviewAttempt]:
    latest: dict[int, ReviewAttempt] = {}
    for attempt in attempts:
        current = latest.get(attempt.reviewer_slot)
        if current is None or (attempt.runtime_attempt, attempt.created_at) > (
            current.runtime_attempt,
            current.created_at,
        ):
            latest[attempt.reviewer_slot] = attempt
    return latest


def results_by_slot(
    attempts: Iterable[ReviewAttempt],
    results: Iterable[ReviewResult],
) -> dict[int, ReviewResult]:
    slot_by_reviewer = {item.reviewer_run_id: item.reviewer_slot for item in attempts}
    resolved: dict[int, ReviewResult] = {}
    for result in results:
        slot = slot_by_reviewer.get(result.reviewer_run_id)
        if slot is not None:
            resolved[slot] = result
    return resolved


def retry_slots(
    *,
    required_slots: int,
    attempts: Iterable[ReviewAttempt],
    results: Iterable[ReviewResult],
) -> tuple[list[int], list[int], list[int]]:
    """Return ``(launch_or_retry, pending, exhausted)`` reviewer slots."""

    attempts_list = list(attempts)
    result_slots = results_by_slot(attempts_list, results)
    latest = latest_attempt_by_slot(attempts_list)
    launch: list[int] = []
    pending: list[int] = []
    exhausted: list[int] = []
    limit = review_total_attempt_limit()
    for slot in range(max(0, required_slots)):
        if slot in result_slots:
            continue
        attempt = latest.get(slot)
        if attempt is None:
            launch.append(slot)
            continue
        if attempt.status == "running":
            pending.append(slot)
            continue
        if attempt.retryable and attempt.runtime_attempt < limit:
            launch.append(slot)
            continue
        exhausted.append(slot)
    return launch, pending, exhausted


def review_complexity_score(
    snapshot: ReviewSnapshot,
    revision: TaskRevision,
    *,
    validation_count: int = 0,
) -> int:
    """Small deterministic signal used only to size reviewer circuit breakers."""

    path_points = min(8, len(snapshot.relevant_files) // 3)
    requirement_points = min(6, len(revision.requirements) // 2)
    validation_points = min(4, max(0, validation_count) // 3)
    return min(16, path_points + requirement_points + validation_points)
