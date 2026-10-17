"""Truthful, orthogonal review and validation state for World Forge candidates."""
from __future__ import annotations

from typing import Any, Mapping

from .generation_attempt_history import preserve_attempt_history, with_validation_attempt
from .generation_repair_evaluation import (
    evaluate_retry_repair,
    same_current_retry_strategy,
)

_FAILED_VALIDATION_STATUSES = {"blocked", "failed", "needs_review", "rejected"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _issues(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(str(item) for item in value if str(item)))


def validation_evidence(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return stable validation evidence without conflating it with review decisions."""

    current = _mapping(report)
    previous = _mapping(current.get("previous_validation"))
    evidence = previous or current
    issues = _issues(current.get("outstanding_findings"))
    if not issues:
        issues = _issues(evidence.get("issues"))
    reason_codes = _strings(current.get("outstanding_reason_codes"))
    if not reason_codes:
        reason_codes = _strings(evidence.get("reason_codes"))

    explicit_status = str(current.get("validation_status") or "").strip()
    source_status = str(evidence.get("status") or "").strip()
    failed = bool(
        issues
        or reason_codes
        or evidence.get("blocking")
        or source_status in _FAILED_VALIDATION_STATUSES
    )
    validation_status = explicit_status or ("failed" if failed else "passed")
    if validation_status not in {"passed", "failed", "not_run"}:
        validation_status = "failed" if failed else "passed"

    return {
        "validation_status": validation_status,
        "outstanding_findings": issues,
        "outstanding_reason_codes": reason_codes,
        "validation_blocking": bool(evidence.get("blocking") or failed),
        "source_validation_status": source_status,
    }


def accepted_review_report(
    previous_validation: Mapping[str, Any] | None,
    *,
    accepted_at: str,
    accepted_by: str = "local-game-master",
    waiver_reason: str = "",
) -> dict[str, Any]:
    """Record acceptance while preserving failed validation as an active waiver."""

    previous = _mapping(previous_validation)
    evidence = validation_evidence(previous)
    findings = list(evidence["outstanding_findings"])
    reason_codes = list(evidence["outstanding_reason_codes"])
    failed = evidence["validation_status"] == "failed"
    reason = str(waiver_reason or "").strip()
    if failed and not reason:
        reason = "Accepted by the Game Master after review with unresolved validation findings."

    report = {
        "schema_version": "rpg_world_generation_review_v2",
        "status": "accepted",
        "review_decision": "accepted",
        "validation_status": evidence["validation_status"],
        "waiver_status": "active" if failed else "none",
        "blocking": False,
        "validation_blocking": bool(evidence["validation_blocking"]),
        "error_type": str(previous.get("error_type") or ""),
        "reason_codes": reason_codes,
        "issues": findings,
        "outstanding_reason_codes": reason_codes,
        "outstanding_findings": findings,
        "summary": (
            "Candidate accepted by the Game Master with unresolved findings retained."
            if failed
            else "Candidate accepted by the Game Master after validation passed."
        ),
        "accepted_at": accepted_at,
        "accepted_by": accepted_by,
        "waiver": {
            "status": "active" if failed else "none",
            "reason": reason,
            "accepted_by": accepted_by,
            "accepted_at": accepted_at,
        },
        "previous_validation": previous,
    }
    return preserve_attempt_history(report, previous)


def _validation_with_projected_attempt(row: Mapping[str, Any]) -> dict[str, Any]:
    validation = _mapping(row.get("validation"))
    if validation.get("attempt_history"):
        return validation
    source_validation = _mapping(validation.get("previous_validation")) or validation
    source_status = str(source_validation.get("status") or row.get("status") or "not_run")
    return with_validation_attempt(
        source_validation,
        run_id=str(row.get("run_id") or ""),
        topic_id=str(row.get("topic_id") or ""),
        result_status=source_status,
        candidate_hash=str(row.get("candidate_hash") or ""),
        provider=_mapping(row.get("provider")),
        job_id=str(row.get("job_id") or ""),
        trigger="manual_retry" if row.get("previous_result") else "generation",
    )


def review_state(result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project independent review, validation, waiver, attempt and repair state."""

    row = _mapping(result)
    validation = _mapping(row.get("validation"))
    evidence = validation_evidence(validation)
    review_decision = str(validation.get("review_decision") or "").strip()
    if not review_decision and str(row.get("status") or "") == "accepted":
        review_decision = "accepted"
    waiver_status = str(validation.get("waiver_status") or "").strip()
    if not waiver_status:
        waiver_status = (
            "active"
            if review_decision == "accepted" and evidence["validation_status"] == "failed"
            else "none"
        )

    attempts = _validation_with_projected_attempt(row).get("attempt_history") or ()
    current_history = [dict(item) for item in attempts if isinstance(item, Mapping)]
    previous_result = row.get("previous_result")
    previous_history: list[dict[str, Any]] = []
    repair_evaluation: dict[str, Any] | None = None
    consecutive_no_ops = 0
    if isinstance(previous_result, Mapping):
        previous_state = review_state(previous_result)
        previous_history = list(previous_state["attempt_history"])
        repair_evaluation = evaluate_retry_repair(previous_result, row)
        consecutive_no_ops = (
            int(previous_state.get("consecutive_no_op_count") or 0) + 1
            if repair_evaluation["outcome"] == "no_op"
            and same_current_retry_strategy(row, previous_result)
            else 0
        )
    by_id: dict[str, dict[str, Any]] = {}
    for attempt in (*previous_history, *current_history):
        attempt_id = str(attempt.get("attempt_id") or "")
        if attempt_id:
            by_id.setdefault(attempt_id, attempt)
    attempt_history = sorted(
        by_id.values(),
        key=lambda item: (
            str(item.get("run_id") or ""),
            int(item.get("attempt_number") or 0),
            str(item.get("attempt_id") or ""),
        ),
    )
    return {
        "review_decision": review_decision or "pending",
        "validation_status": evidence["validation_status"],
        "waiver_status": waiver_status,
        "outstanding_finding_count": len(evidence["outstanding_findings"]),
        "outstanding_findings": evidence["outstanding_findings"],
        "outstanding_reason_codes": evidence["outstanding_reason_codes"],
        "attempt_count": len(attempt_history),
        "attempt_history": attempt_history,
        "repair_evaluation": repair_evaluation,
        "consecutive_no_op_count": consecutive_no_ops,
        "retry_budget_exhausted": consecutive_no_ops >= 2,
    }


__all__ = ["accepted_review_report", "review_state", "validation_evidence"]
