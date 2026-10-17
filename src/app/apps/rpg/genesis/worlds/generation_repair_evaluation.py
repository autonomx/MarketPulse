"""Finding-aware evaluation and retry-budget guards for World Forge repairs."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.worlds.generation_contract_bundle import CONTRACT_VERSION

_MAX_CONSECUTIVE_NO_OPS = 2
_REVIEWABLE_PROGRESS_KEYS = (
    "flagged_topic_ids",
    "failed_topic_ids",
    "blocked_topic_ids",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def same_current_retry_strategy(
    current_result: Mapping[str, Any],
    previous_result: Mapping[str, Any],
) -> bool:
    """Return true only for two attempts made under the active known strategy."""

    current_provider = _mapping(current_result.get("provider"))
    previous_provider = _mapping(previous_result.get("provider"))
    current_strategy = str(current_provider.get("strategy_identity") or "")
    previous_strategy = str(previous_provider.get("strategy_identity") or "")
    current_contract = str(
        _mapping(current_provider.get("contract_descriptor")).get("contract_version")
        or ""
    )
    previous_contract = str(
        _mapping(previous_provider.get("contract_descriptor")).get("contract_version")
        or ""
    )
    return (
        bool(current_strategy)
        and bool(previous_strategy)
        and current_strategy == previous_strategy
        and current_contract == CONTRACT_VERSION
        and previous_contract == CONTRACT_VERSION
    )


def _findings(result: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    row = _mapping(result)
    validation = _mapping(row.get("validation"))
    previous = _mapping(validation.get("previous_validation"))
    evidence = validation
    findings = evidence.get("outstanding_findings")
    if not isinstance(findings, (list, tuple)):
        findings = evidence.get("issues")
    if not isinstance(findings, (list, tuple)) and previous:
        findings = previous.get("issues")
    return [dict(item) for item in findings or () if isinstance(item, Mapping)]


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def finding_fingerprint(issue: Mapping[str, Any]) -> dict[str, str]:
    """Return stable issue identity plus the exact failing value evidence."""

    return {
        "code": str(issue.get("code") or "unknown"),
        "topic_id": str(issue.get("topic_id") or ""),
        "entity_id": str(issue.get("entity_id") or ""),
        "field_id": str(issue.get("field_id") or ""),
        "bad_value": _canonical(issue.get("supplied_value")),
    }


def _location_key(fingerprint: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (
        str(fingerprint.get("code") or ""),
        str(fingerprint.get("topic_id") or ""),
        str(fingerprint.get("entity_id") or ""),
        str(fingerprint.get("field_id") or ""),
    )


def evaluate_retry_repair(
    previous_result: Mapping[str, Any] | None,
    current_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Classify whether a retry fixed the findings that caused it."""

    previous = _mapping(previous_result)
    current = _mapping(current_result)
    previous_fingerprints = [finding_fingerprint(item) for item in _findings(previous)]
    current_fingerprints = [finding_fingerprint(item) for item in _findings(current)]
    previous_by_location = {_location_key(item): item for item in previous_fingerprints}
    current_by_location = {_location_key(item): item for item in current_fingerprints}
    previous_locations = set(previous_by_location)
    current_locations = set(current_by_location)
    remaining = previous_locations & current_locations
    repaired = previous_locations - current_locations
    introduced = current_locations - previous_locations

    candidate_changed = str(previous.get("candidate_hash") or "") != str(
        current.get("candidate_hash") or ""
    )
    if not previous_locations:
        outcome = "not_applicable"
    elif not remaining and not current_locations:
        outcome = "repaired"
    elif not remaining and current_locations:
        outcome = "replaced_with_new_failure"
    elif repaired:
        outcome = "partially_repaired"
    elif introduced or len(current_locations) > len(previous_locations):
        outcome = "regressed"
    else:
        outcome = "no_op"

    changed_values = [
        {
            "location": list(location),
            "previous_bad_value": previous_by_location[location]["bad_value"],
            "current_bad_value": current_by_location[location]["bad_value"],
        }
        for location in sorted(remaining)
        if previous_by_location[location]["bad_value"]
        != current_by_location[location]["bad_value"]
    ]
    return {
        "schema_version": "rpg_world_generation_repair_evaluation_v1",
        "outcome": outcome,
        "candidate_changed": candidate_changed,
        "original_finding_count": len(previous_locations),
        "remaining_finding_count": len(remaining),
        "repaired_finding_count": len(repaired),
        "introduced_finding_count": len(introduced),
        "remaining_finding_fingerprints": [
            previous_by_location[location] for location in sorted(remaining)
        ],
        "repaired_finding_fingerprints": [
            previous_by_location[location] for location in sorted(repaired)
        ],
        "introduced_finding_fingerprints": [
            current_by_location[location] for location in sorted(introduced)
        ],
        "changed_bad_values": changed_values,
    }


def _result_chain(
    run_id: str,
    topic_id: str,
    *,
    database: Any | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    context = current_tenant()
    chain: list[dict[str, Any]] = []
    current_run_id = str(run_id)
    with unit_of_work(database) as work:
        while current_run_id and len(chain) < max(2, int(limit)):
            run = work.world_generation.get(context, current_run_id)
            if run is None:
                break
            result = work.world_generation.get_topic_result(
                context,
                run_id=current_run_id,
                topic_id=topic_id,
            )
            if result is not None:
                chain.append(dict(result))
            current_run_id = str(run.get("parent_run_id") or "")
        work.rollback()
    return chain


def consecutive_no_op_count(
    run_id: str,
    topic_id: str,
    *,
    database: Any | None = None,
) -> int:
    """Count consecutive no-op repair outcomes from the current run backwards."""

    chain = _result_chain(run_id, topic_id, database=database)
    count = 0
    for current, previous in zip(chain, chain[1:]):
        # Unknown or pre-current contract identities are incomparable. This
        # grants a clean budget after a model, prompt, or contract deployment.
        if not same_current_retry_strategy(current, previous):
            break
        if evaluate_retry_repair(previous, current)["outcome"] != "no_op":
            break
        count += 1
    return count


def _implicit_reviewable_topic_ids(
    run_id: str,
    *,
    database: Any | None = None,
) -> tuple[str, ...]:
    context = current_tenant()
    with unit_of_work(database) as work:
        run = work.world_generation.get(context, run_id)
        if run is None:
            work.rollback()
            raise KeyError(f"world_generation_run_not_found:{run_id}")
        progress = _mapping(run.get("progress"))
        work.rollback()
    return tuple(
        dict.fromkeys(
            str(topic_id)
            for key in _REVIEWABLE_PROGRESS_KEYS
            for topic_id in progress.get(key) or ()
            if str(topic_id)
        )
    )


def require_retry_budget(
    run_id: str,
    topic_ids: Sequence[str] = (),
    *,
    database: Any | None = None,
) -> dict[str, int]:
    """Reject a third consecutive no-op repair for explicit or bulk retries."""

    selected = tuple(str(topic_id) for topic_id in topic_ids if str(topic_id))
    if not selected:
        selected = _implicit_reviewable_topic_ids(run_id, database=database)
    counts = {
        topic_id: consecutive_no_op_count(
            run_id,
            topic_id,
            database=database,
        )
        for topic_id in selected
    }
    exhausted = sorted(
        topic_id
        for topic_id, count in counts.items()
        if count >= _MAX_CONSECUTIVE_NO_OPS
    )
    if exhausted:
        raise ValueError(
            "world_generation_retry_no_op_limit:"
            + ",".join(exhausted)
            + ":use_field_edit_deterministic_repair_or_waiver"
        )
    return counts


__all__ = [
    "consecutive_no_op_count",
    "evaluate_retry_repair",
    "finding_fingerprint",
    "require_retry_budget",
    "same_current_retry_strategy",
]
