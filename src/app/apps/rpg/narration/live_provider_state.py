"""Phase 10.7 — Live provider execution models and normalization.

This module owns bounded provider execution artifacts only.
It does not mutate simulation truth directly.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple, cast
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str

_MAX_PROVIDER_EXECUTIONS = 12
_MAX_PROVIDER_EVENTS = 80

_VALID_PROVIDER_EXECUTION_STATUS = {
    "pending",
    "streaming",
    "complete",
    "failed",
}

_VALID_PROVIDER_EVENT_TYPES = {
    "request_started",
    "text_chunk",
    "response_completed",
    "response_failed",
}


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_bool(v: Any) -> bool:
    return bool(v)


def _normalize_execution_status(v: Any) -> str:
    value = _safe_str(v).strip().lower()
    return value if value in _VALID_PROVIDER_EXECUTION_STATUS else "pending"


def _normalize_provider_event_type(v: Any) -> str:
    value = _safe_str(v).strip().lower()
    return value if value in _VALID_PROVIDER_EVENT_TYPES else "text_chunk"


def build_provider_execution_id(request_id: str) -> str:
    """Build a deterministic provider execution id from request id."""
    return f"provexec:{_safe_str(request_id)}"


def _sort_key_provider_event(event: Dict[str, Any]) -> Tuple[int, str]:
    return (
        _safe_int(event.get("event_index"), 0),
        _safe_str(event.get("event_type")),
    )


def _sort_key_provider_execution(item: Dict[str, Any]) -> Tuple[int, str]:
    return (
        _safe_int(item.get("tick"), 0),
        _safe_str(item.get("execution_id")),
    )


def _normalize_provider_event(event: Dict[str, Any]) -> Dict[str, Any]:
    event = _safe_dict(event)
    return {
        "event_index": max(0, _safe_int(event.get("event_index"), 0)),
        "event_type": _normalize_provider_event_type(event.get("event_type")),
        "text": _safe_str(event.get("text")),
        "final": _safe_bool(event.get("final")),
        "raw": _safe_dict(event.get("raw")),
    }


def _normalize_provider_execution(item: Dict[str, Any]) -> Dict[str, Any]:
    item = _safe_dict(item)
    events = [
        _normalize_provider_event(v)
        for v in _safe_list(item.get("events"))
        if isinstance(v, dict)
    ]
    events = sorted(events, key=_sort_key_provider_event)[-_MAX_PROVIDER_EVENTS:]
    return {
        "execution_id": _safe_str(item.get("execution_id")),
        "request_id": _safe_str(item.get("request_id")),
        "tick": _safe_int(item.get("tick"), 0),
        "provider": _safe_str(item.get("provider")),
        "model": _safe_str(item.get("model")),
        "status": _normalize_execution_status(item.get("status")),
        "events": events,
        "output_text": _safe_str(item.get("output_text")),
        "error": _safe_str(item.get("error")),
    }


def _normalize_live_provider_state(live_state: Dict[str, Any]) -> Dict[str, Any]:
    live_state = _safe_dict(live_state)
    executions = [
        _normalize_provider_execution(v)
        for v in _safe_list(live_state.get("executions"))
        if isinstance(v, dict)
    ]
    executions = sorted(executions, key=_sort_key_provider_execution)[-_MAX_PROVIDER_EXECUTIONS:]
    return {
        "executions": executions,
    }


def ensure_live_provider_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure simulation_state contains normalized live provider state."""
    if not isinstance(simulation_state, dict):
        simulation_state = {}

    orchestration_state = simulation_state.setdefault("orchestration_state", {})
    if not isinstance(orchestration_state, dict):
        orchestration_state = simulation_state["orchestration_state"] = {}

    live_state = orchestration_state.get("live_provider")
    orchestration_state["live_provider"] = _normalize_live_provider_state(cast(Any, live_state))
    simulation_state["orchestration_state"] = orchestration_state
    return simulation_state


def get_live_provider_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return normalized live provider state."""
    simulation_state = ensure_live_provider_state(simulation_state)
    orchestration_state = _safe_dict(simulation_state.get("orchestration_state"))
    return _safe_dict(orchestration_state.get("live_provider"))


def _set_live_provider_state(
    simulation_state: Dict[str, Any],
    live_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist normalized live provider state back into simulation_state."""
    simulation_state = ensure_live_provider_state(simulation_state)
    orchestration_state = _safe_dict(simulation_state.get("orchestration_state"))
    orchestration_state["live_provider"] = _normalize_live_provider_state(cast(Any, live_state))
    simulation_state["orchestration_state"] = orchestration_state
    return simulation_state


def _find_execution_index_by_id(executions: List[Dict[str, Any]], execution_id: str) -> int:
    execution_id = _safe_str(execution_id)
    for idx, item in enumerate(executions):
        if _safe_str(_safe_dict(item).get("execution_id")) == execution_id:
            return idx
    return -1


def _dedupe_and_sort_provider_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for item in events:
        normalized = _normalize_provider_event(item)
        key = (
            _safe_int(normalized.get("event_index"), 0),
            _safe_str(normalized.get("event_type")),
        )
        deduped[key] = normalized
    out = sorted(deduped.values(), key=_sort_key_provider_event)
    return out[-_MAX_PROVIDER_EVENTS:]


def trim_live_provider_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize and trim live provider state to bounded caps."""
    live_state = get_live_provider_state(simulation_state)
    return _set_live_provider_state(simulation_state, live_state)


def begin_provider_execution(
    simulation_state: Dict[str, Any],
    *,
    request_id: str,
    tick: int,
    provider: str,
    model: str,
) -> Dict[str, Any]:
    """Create or replace a provider execution record deterministically."""
    simulation_state = ensure_live_provider_state(simulation_state)
    live_state = get_live_provider_state(simulation_state)
    executions = list(_safe_list(live_state.get("executions")))

    request_id = _safe_str(request_id)
    tick = _safe_int(tick, 0)
    provider = _safe_str(provider)
    model = _safe_str(model)

    if not request_id:
        return simulation_state

    execution_id = build_provider_execution_id(request_id)
    execution = _normalize_provider_execution({
        "execution_id": execution_id,
        "request_id": request_id,
        "tick": tick,
        "provider": provider,
        "model": model,
        "status": "pending",
        "events": [],
        "output_text": "",
        "error": "",
    })

    existing_idx = _find_execution_index_by_id(executions, execution_id)
    if existing_idx >= 0:
        executions[existing_idx] = execution
    else:
        executions.append(execution)

    executions = sorted(
        [_normalize_provider_execution(v) for v in executions],
        key=_sort_key_provider_execution,
    )[-_MAX_PROVIDER_EXECUTIONS:]

    live_state["executions"] = executions
    return _set_live_provider_state(simulation_state, live_state)


def append_provider_execution_event(
    simulation_state: Dict[str, Any],
    *,
    execution_id: str,
    event_index: int,
    event_type: str,
    text: str = "",
    final: bool = False,
    raw: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Append a structured provider event deterministically."""
    simulation_state = ensure_live_provider_state(simulation_state)
    live_state = get_live_provider_state(simulation_state)
    executions = list(_safe_list(live_state.get("executions")))

    execution_id = _safe_str(execution_id)
    if not execution_id:
        return simulation_state

    execution_idx = _find_execution_index_by_id(executions, execution_id)
    if execution_idx < 0:
        return simulation_state

    execution = _normalize_provider_execution(executions[execution_idx])
    events = list(_safe_list(execution.get("events")))
    events.append({
        "event_index": max(0, _safe_int(event_index, 0)),
        "event_type": _safe_str(event_type) or "text_chunk",
        "text": _safe_str(text),
        "final": _safe_bool(final),
        "raw": _safe_dict(raw),
    })
    execution["events"] = _dedupe_and_sort_provider_events(events)

    if execution["status"] not in {"complete", "failed"}:
        execution["status"] = "streaming"

    executions[execution_idx] = _normalize_provider_execution(execution)
    live_state["executions"] = executions
    return _set_live_provider_state(simulation_state, live_state)


def finalize_provider_execution(
    simulation_state: Dict[str, Any],
    *,
    execution_id: str,
    output_text: str = "",
) -> Dict[str, Any]:
    """Finalize a provider execution deterministically."""
    simulation_state = ensure_live_provider_state(simulation_state)
    live_state = get_live_provider_state(simulation_state)
    executions = list(_safe_list(live_state.get("executions")))

    execution_id = _safe_str(execution_id)
    if not execution_id:
        return simulation_state

    execution_idx = _find_execution_index_by_id(executions, execution_id)
    if execution_idx < 0:
        return simulation_state

    execution = _normalize_provider_execution(executions[execution_idx])
    if output_text:
        execution["output_text"] = _safe_str(output_text)
    execution["status"] = "complete"
    execution["error"] = ""

    executions[execution_idx] = _normalize_provider_execution(execution)
    live_state["executions"] = executions
    return _set_live_provider_state(simulation_state, live_state)


def fail_provider_execution(
    simulation_state: Dict[str, Any],
    *,
    execution_id: str,
    error: str,
) -> Dict[str, Any]:
    """Fail a provider execution deterministically."""
    simulation_state = ensure_live_provider_state(simulation_state)
    live_state = get_live_provider_state(simulation_state)
    executions = list(_safe_list(live_state.get("executions")))

    execution_id = _safe_str(execution_id)
    error = _safe_str(error)
    if not execution_id:
        return simulation_state

    execution_idx = _find_execution_index_by_id(executions, execution_id)
    if execution_idx < 0:
        return simulation_state

    execution = _normalize_provider_execution(executions[execution_idx])
    execution["status"] = "failed"
    execution["error"] = error

    executions[execution_idx] = _normalize_provider_execution(execution)
    live_state["executions"] = executions
    return _set_live_provider_state(simulation_state, live_state)