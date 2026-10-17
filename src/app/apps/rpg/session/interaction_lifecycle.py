"""Progressive interaction lifecycle for authoritative turns and deferred narration."""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

INTERACTION_LIFECYCLE_VERSION = "rpg_interaction_lifecycle_v1"
INTERACTION_LIFECYCLE_STATUSES = (
    "accepted",
    "runtime_resolved",
    "narration_pending",
    "narration_complete",
    "narration_failed",
)
_MAX_LIFECYCLES = 64


def initialize_interaction_lifecycle(
    session: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach one lifecycle record to an already committed interaction."""

    interaction_id = _text(result.get("interaction_id"))
    if not interaction_id:
        return {}
    runtime = _dict(session.get("runtime_state"))
    turn_id = _first_text(result, "turn_id")
    stateful = _first_bool(result, "stateful")
    narration_request = _narration_request(result)
    record = {
        "format_version": INTERACTION_LIFECYCLE_VERSION,
        "interaction_id": interaction_id,
        "turn_id": turn_id,
        "submission_id": _text(result.get("submission_id")),
        "stateful": stateful,
        "status": "runtime_resolved",
        "accepted_at": _utc_now(),
        "runtime_resolved_at": _utc_now(),
        "narration_status": "not_requested",
        "authoritative_response": _compact_authoritative_response(result),
        "updated_at": _utc_now(),
    }
    if narration_request:
        narration_request["interaction_id"] = interaction_id
        record["status"] = "narration_pending"
        record["narration_status"] = "queued"
        record["narration_pending_at"] = _utc_now()
        _replace_narration_request(result, narration_request)

    lifecycles = _dict(runtime.get("interaction_lifecycles"))
    lifecycles[interaction_id] = record
    lifecycles = _bounded_mapping(lifecycles)
    runtime["interaction_lifecycles"] = lifecycles
    if turn_id:
        by_turn = _dict(runtime.get("interaction_id_by_turn"))
        by_turn[turn_id] = interaction_id
        runtime["interaction_id_by_turn"] = _bounded_mapping(by_turn)
    session["runtime_state"] = runtime
    result["interaction_lifecycle"] = deepcopy(record)
    result["narration_status"] = record["narration_status"]
    _update_interaction_event(session, interaction_id, record)
    return record


def queue_deferred_narration_for_interaction(
    session_id: str,
    result: dict[str, Any],
) -> bool:
    """Queue deferred narration once and bind it to the authoritative interaction."""

    narration_request = _narration_request(result)
    interaction_id = _text(result.get("interaction_id"))
    turn_id = _text(narration_request.get("turn_id") or result.get("turn_id"))
    if not narration_request or not interaction_id or not turn_id:
        return False

    from app.apps.rpg.session.jobs.narration_job import enqueue_rpg_narration

    narration_request = deepcopy(narration_request)
    narration_request["session_id"] = session_id
    narration_request["interaction_id"] = interaction_id
    performance = _dict(narration_request.get("performance"))
    performance["enable_live_narration_llm"] = True
    narration_request["performance"] = performance
    # An rpg.narration job (WP-5.6): the interaction's lifecycle, committed with
    # the turn, already records it as pending, so the session is not saved again.
    narration_job = enqueue_rpg_narration(
        session_id,
        turn_id,
        narration_request,
        interaction_id=interaction_id,
        tick=int(narration_request.get("tick") or result.get("tick") or 0),
    )
    result["narration_job"] = narration_job
    result["narration_status"] = "queued"
    return True


def apply_narration_result_to_interaction(
    session_id: str,
    worker_result: dict[str, Any],
) -> dict[str, Any]:
    """Update only lifecycle/presentation metadata after narration worker completion."""

    from app.apps.rpg.session.session_runtime_store import (
        load_runtime_session as load_runtime_session,
        save_runtime_session as save_runtime_session,
    )

    session = load_runtime_session(session_id)
    if not isinstance(session, dict):
        return worker_result
    runtime = _dict(session.get("runtime_state"))
    turn_id = _text(worker_result.get("turn_id"))
    interaction_id = _text(worker_result.get("interaction_id")) or _text(
        _dict(runtime.get("interaction_id_by_turn")).get(turn_id)
    )
    if not interaction_id:
        return worker_result
    lifecycles = _dict(runtime.get("interaction_lifecycles"))
    current = _dict(lifecycles.get(interaction_id))
    if not current or (current.get("turn_id") and _text(current.get("turn_id")) != turn_id):
        return worker_result

    status = _text(worker_result.get("status")).lower()
    if status == "completed":
        lifecycle_status = "narration_complete"
        narration_status = "complete"
    elif status == "failed":
        lifecycle_status = "narration_failed"
        narration_status = "failed"
    elif status in {"queued", "processing"}:
        lifecycle_status = "narration_pending"
        narration_status = status
    else:
        return worker_result

    updated = {
        **current,
        "status": lifecycle_status,
        "narration_status": narration_status,
        "updated_at": _utc_now(),
    }
    if lifecycle_status == "narration_complete":
        artifact = _dict(worker_result.get("artifact"))
        updated["narration_complete_at"] = _utc_now()
        updated["narration_enrichment"] = _compact_narration_artifact(artifact)
    elif lifecycle_status == "narration_failed":
        updated["narration_failed_at"] = _utc_now()
        updated["narration_error"] = _text(worker_result.get("error")) or "narration_failed"
    else:
        updated["narration_pending_at"] = current.get("narration_pending_at") or _utc_now()

    lifecycles[interaction_id] = updated
    runtime["interaction_lifecycles"] = _bounded_mapping(lifecycles)
    session["runtime_state"] = runtime
    _update_interaction_event(session, interaction_id, updated)
    save_runtime_session(session)
    worker_result = dict(worker_result)
    worker_result["interaction_id"] = interaction_id
    worker_result["interaction_lifecycle"] = deepcopy(updated)
    return worker_result


def recover_pending_interaction_narration(session_id: str, session: dict[str, Any]) -> int:
    runtime = _dict(session.get("runtime_state"))
    lifecycles = _dict(runtime.get("interaction_lifecycles"))
    pending = [
        lifecycle
        for lifecycle in lifecycles.values()
        if isinstance(lifecycle, dict) and lifecycle.get("status") == "narration_pending"
    ]
    if not pending:
        return 0
    # Saves from before WP-5.6 kept the request in the session's narration
    # queue; give each pending one its rpg.narration job (idempotent). Newer
    # pending narration already has its job, and its session holds no queue.
    legacy_queue = _dict(runtime.get("narration_jobs_by_turn"))
    recovered = 0
    for lifecycle in pending:
        turn_id = _text(lifecycle.get("turn_id"))
        request = _dict(_dict(legacy_queue.get(turn_id)).get("narration_request"))
        if not turn_id or not request:
            continue
        from app.apps.rpg.session.jobs.narration_job import enqueue_rpg_narration

        enqueue_rpg_narration(
            session_id,
            turn_id,
            {**request, "session_id": session_id},
            interaction_id=_text(lifecycle.get("interaction_id")),
            tick=int(request.get("tick") or 0),
        )
        recovered += 1
    return recovered


def _update_interaction_event(
    session: dict[str, Any],
    interaction_id: str,
    lifecycle: dict[str, Any],
) -> None:
    runtime = _dict(session.get("runtime_state"))
    timeline = _dict(runtime.get("interaction_timeline"))
    events = []
    for value in timeline.get("events", []) if isinstance(timeline.get("events"), list) else []:
        event = dict(value) if isinstance(value, dict) else {}
        if _text(event.get("interaction_id")) == interaction_id:
            event["lifecycle"] = deepcopy(lifecycle)
            enrichment = _dict(lifecycle.get("narration_enrichment"))
            if enrichment:
                event["narration_enrichment"] = enrichment
        events.append(event)
    timeline["events"] = events
    runtime["interaction_timeline"] = timeline
    runtime["recent_interactions"] = [dict(item) for item in events[-12:]]
    if events:
        runtime["last_interaction"] = dict(events[-1])
    session["runtime_state"] = runtime


def _narration_request(result: dict[str, Any]) -> dict[str, Any]:
    return (
        _dict(result.get("narration_request"))
        or _dict(_dict(result.get("result")).get("narration_request"))
        or _dict(_dict(result.get("authoritative")).get("narration_request"))
    )


def _replace_narration_request(result: dict[str, Any], request: dict[str, Any]) -> None:
    if isinstance(result.get("narration_request"), dict):
        result["narration_request"] = request
    for key in ("result", "authoritative"):
        nested = result.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("narration_request"), dict):
            nested["narration_request"] = request


def _compact_authoritative_response(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "turn_id": _first_text(result, "turn_id"),
            "tick": _first_value(result, "tick"),
            "action_type": _first_text(result, "action_type", "semantic_action_type"),
            "outcome": _first_text(result, "outcome"),
            "narration": _first_text(result, "final_narration", "narration", "summary"),
        }.items()
        if value not in (None, "")
    }


def _compact_narration_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "artifact_type": _text(artifact.get("artifact_type")),
            "turn_id": _text(artifact.get("turn_id")),
            "tick": artifact.get("tick"),
            "narration": _text(artifact.get("narration"))[:4000],
            "narration_json": _dict(artifact.get("narration_json")),
            "used_llm": bool(artifact.get("used_llm")),
            "created_at": _text(artifact.get("created_at")),
        }.items()
        if value not in (None, "", {})
    }


def _bounded_mapping(value: dict[str, Any]) -> dict[str, Any]:
    items = list(value.items())[-_MAX_LIFECYCLES:]
    return {key: item for key, item in items}


def _first_text(result: dict[str, Any], *keys: str) -> str:
    value = _first_value(result, *keys)
    return _text(value)


def _first_value(result: dict[str, Any], *keys: str) -> Any:
    for source in (result, _dict(result.get("result")), _dict(result.get("authoritative"))):
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def _first_bool(result: dict[str, Any], key: str) -> bool | None:
    value = _first_value(result, key)
    return value if isinstance(value, bool) else None


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _utc_now() -> str:
    return utc_now().isoformat()
