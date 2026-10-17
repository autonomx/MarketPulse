"""Append-only validation-attempt evidence for World Forge topic results."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, cast

_ATTEMPT_HISTORY_SCHEMA = "rpg_world_generation_attempt_history_v1"
_ATTEMPT_SCHEMA = "rpg_world_generation_validation_attempt_v1"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _history(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _attempt_number(provider: Mapping[str, Any], history: list[dict[str, Any]]) -> int:
    raw = provider.get("attempt_count")
    if not isinstance(raw, bool):
        try:
            return max(1, int(cast(Any, raw)))
        except (TypeError, ValueError):
            pass
    return len(history) + 1


def validation_snapshot(validation: Mapping[str, Any]) -> dict[str, Any]:
    """Return validation evidence without recursively embedding its ledger."""

    return {
        str(key): value
        for key, value in dict(validation).items()
        if str(key) not in {"attempt_history", "attempt_history_schema"}
    }


def with_validation_attempt(
    validation: Mapping[str, Any],
    *,
    run_id: str,
    topic_id: str,
    result_status: str,
    candidate_hash: str,
    provider: Mapping[str, Any] | None = None,
    job_id: str = "",
    trigger: str = "generation",
) -> dict[str, Any]:
    """Append one deterministic attempt unless the same evidence is already present."""

    report = dict(validation)
    history = _history(report.get("attempt_history"))
    provider_data = _mapping(provider)
    snapshot = validation_snapshot(report)
    identity = {
        "run_id": str(run_id),
        "topic_id": str(topic_id),
        "result_status": str(result_status),
        "candidate_hash": str(candidate_hash),
        "job_id": str(job_id),
        "attempt_number": _attempt_number(provider_data, history),
        "validation_hash": _digest(snapshot),
    }
    attempt_id = "attempt:" + _digest(identity).removeprefix("sha256:")
    if not any(str(item.get("attempt_id") or "") == attempt_id for item in history):
        history.append(
            {
                "schema_version": _ATTEMPT_SCHEMA,
                "attempt_id": attempt_id,
                "attempt_number": identity["attempt_number"],
                "trigger": str(trigger or "generation"),
                "run_id": str(run_id),
                "topic_id": str(topic_id),
                "job_id": str(job_id),
                "candidate_hash": str(candidate_hash),
                "result_status": str(result_status),
                "validation_status": str(
                    snapshot.get("validation_status")
                    or snapshot.get("status")
                    or "not_run"
                ),
                "reason_codes": list(snapshot.get("reason_codes") or ()),
                "issues": [
                    dict(item)
                    for item in snapshot.get("issues") or ()
                    if isinstance(item, Mapping)
                ],
                "validation_hash": identity["validation_hash"],
                "provider": provider_data,
            }
        )
    report["attempt_history_schema"] = _ATTEMPT_HISTORY_SCHEMA
    report["attempt_history"] = history
    return report


def preserve_attempt_history(
    target: Mapping[str, Any],
    source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Copy an existing immutable ledger into a derived review report."""

    payload = dict(target)
    previous = _mapping(source)
    history = _history(previous.get("attempt_history"))
    if history:
        payload["attempt_history_schema"] = str(
            previous.get("attempt_history_schema") or _ATTEMPT_HISTORY_SCHEMA
        )
        payload["attempt_history"] = history
    return payload


__all__ = [
    "preserve_attempt_history",
    "validation_snapshot",
    "with_validation_attempt",
]
