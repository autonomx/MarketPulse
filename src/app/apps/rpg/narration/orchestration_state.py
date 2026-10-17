"""Phase 10.6 — LLM orchestration state models and normalization."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple, cast
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str

_MAX_ACTIVE_REQUESTS = 4
_MAX_COMPLETED_REQUESTS = 20
_MAX_REQUEST_STREAM_EVENTS = 40

_VALID_PROVIDER_MODES = {
    "disabled",
    "capture",
    "replay",
    "live",
}

_VALID_REQUEST_STATUS = {
    "pending",
    "streaming",
    "complete",
    "failed",
    "replayed",
}

_VALID_REQUEST_MODES = {
    "dialogue",
    "scene",
    "fallback_assist",
}


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_bool(v: Any) -> bool:
    return bool(v)


def _normalize_provider_mode(v: Any) -> str:
    value = _safe_str(v).strip().lower()
    return value if value in _VALID_PROVIDER_MODES else "disabled"


def _normalize_request_status(v: Any) -> str:
    value = _safe_str(v).strip().lower()
    return value if value in _VALID_REQUEST_STATUS else "pending"


def _normalize_request_mode(v: Any) -> str:
    value = _safe_str(v).strip().lower()
    return value if value in _VALID_REQUEST_MODES else "dialogue"


def build_llm_request_id(tick: int, sequence_index: int, actor_id: str, request_index: int) -> str:
    """Build a deterministic orchestration request id."""
    return (
        f"llmreq:{_safe_int(tick)}:"
        f"{_safe_int(sequence_index)}:"
        f"{_safe_str(actor_id)}:"
        f"{_safe_int(request_index)}"
    )


def _sort_key_stream_event(event: Dict[str, Any]) -> Tuple[int, str]:
    return (
        _safe_int(event.get("event_index"), 0),
        _safe_str(event.get("event_type")),
    )


def _sort_key_request(request: Dict[str, Any]) -> Tuple[int, str, str]:
    return (
        _safe_int(request.get("tick"), 0),
        _safe_str(request.get("request_id")),
        _safe_str(request.get("turn_id")),
    )


def _normalize_stream_event(event: Dict[str, Any]) -> Dict[str, Any]:
    event = _safe_dict(event)
    return {
        "event_index": max(0, _safe_int(event.get("event_index"), 0)),
        "event_type": _safe_str(event.get("event_type") or "text_chunk"),
        "text": _safe_str(event.get("text")),
        "final": _safe_bool(event.get("final")),
        "raw": _safe_dict(event.get("raw")),
    }


def _normalize_request(request: Dict[str, Any]) -> Dict[str, Any]:
    request = _safe_dict(request)

    stream_events = [
        _normalize_stream_event(v)
        for v in _safe_list(request.get("stream_events"))
        if isinstance(v, dict)
    ]
    stream_events = sorted(stream_events, key=_sort_key_stream_event)[-_MAX_REQUEST_STREAM_EVENTS:]

    return {
        "request_id": _safe_str(request.get("request_id")),
        "tick": _safe_int(request.get("tick"), 0),
        "sequence_id": _safe_str(request.get("sequence_id")),
        "turn_id": _safe_str(request.get("turn_id")),
        "actor_id": _safe_str(request.get("actor_id")),
        "speaker_id": _safe_str(request.get("speaker_id")),
        "mode": _normalize_request_mode(request.get("mode")),
        "status": _normalize_request_status(request.get("status")),
        "provider": _safe_str(request.get("provider")),
        "model": _safe_str(request.get("model")),
        "input_payload": _safe_dict(request.get("input_payload")),
        "stream_events": stream_events,
        "output_text": _safe_str(request.get("output_text")),
        "error": _safe_str(request.get("error")),
    }


def _normalize_llm_state(llm_state: Dict[str, Any]) -> Dict[str, Any]:
    llm_state = _safe_dict(llm_state)

    active_requests = [
        _normalize_request(v)
        for v in _safe_list(llm_state.get("active_requests"))
        if isinstance(v, dict)
    ]
    active_requests = sorted(active_requests, key=_sort_key_request)[-_MAX_ACTIVE_REQUESTS:]

    completed_requests = [
        _normalize_request(v)
        for v in _safe_list(llm_state.get("completed_requests"))
        if isinstance(v, dict)
    ]
    completed_requests = sorted(completed_requests, key=_sort_key_request)[-_MAX_COMPLETED_REQUESTS:]

    return {
        "active_requests": active_requests,
        "completed_requests": completed_requests,
        "request_counter": max(0, _safe_int(llm_state.get("request_counter"), 0)),
        "provider_mode": _normalize_provider_mode(llm_state.get("provider_mode")),
        "last_error": _safe_dict(llm_state.get("last_error")),
    }


def ensure_llm_orchestration_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure simulation_state contains normalized LLM orchestration state."""
    if not isinstance(simulation_state, dict):
        simulation_state = {}

    orchestration_state = simulation_state.setdefault("orchestration_state", {})
    if not isinstance(orchestration_state, dict):
        orchestration_state = simulation_state["orchestration_state"] = {}

    llm_state = orchestration_state.get("llm")
    orchestration_state["llm"] = _normalize_llm_state(cast(Any, llm_state))
    simulation_state["orchestration_state"] = orchestration_state
    return simulation_state


def get_llm_orchestration_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return normalized LLM orchestration state."""
    simulation_state = ensure_llm_orchestration_state(simulation_state)
    orchestration_state = _safe_dict(simulation_state.get("orchestration_state"))
    return _safe_dict(orchestration_state.get("llm"))


def _set_llm_orchestration_state(
    simulation_state: Dict[str, Any],
    llm_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist normalized LLM orchestration state back into simulation_state."""
    simulation_state = ensure_llm_orchestration_state(simulation_state)
    orchestration_state = _safe_dict(simulation_state.get("orchestration_state"))
    orchestration_state["llm"] = _normalize_llm_state(cast(Any, llm_state))
    simulation_state["orchestration_state"] = orchestration_state
    return simulation_state


def _find_request_index_by_id(requests: List[Dict[str, Any]], request_id: str) -> int:
    request_id = _safe_str(request_id)
    for idx, request in enumerate(requests):
        if _safe_str(_safe_dict(request).get("request_id")) == request_id:
            return idx
    return -1


def _dedupe_and_sort_stream_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for event in events:
        normalized = _normalize_stream_event(event)
        key = (
            _safe_int(normalized.get("event_index"), 0),
            _safe_str(normalized.get("event_type")),
        )
        deduped[key] = normalized
    out = sorted(deduped.values(), key=_sort_key_stream_event)
    return out[-_MAX_REQUEST_STREAM_EVENTS:]


def _move_request_to_completed(
    llm_state: Dict[str, Any],
    request: Dict[str, Any],
) -> Dict[str, Any]:
    llm_state = _safe_dict(llm_state)
    active_requests = list(_safe_list(llm_state.get("active_requests")))
    completed_requests = list(_safe_list(llm_state.get("completed_requests")))

    request_id = _safe_str(_safe_dict(request).get("request_id"))
    active_requests = [
        _normalize_request(v)
        for v in active_requests
        if _safe_str(_safe_dict(v).get("request_id")) != request_id
    ]

    completed_requests.append(_normalize_request(request))
    completed_requests = sorted(completed_requests, key=_sort_key_request)[-_MAX_COMPLETED_REQUESTS:]

    llm_state["active_requests"] = active_requests
    llm_state["completed_requests"] = completed_requests
    return llm_state


def trim_llm_orchestration_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize and trim orchestration state to bounded caps."""
    llm_state = get_llm_orchestration_state(simulation_state)
    return _set_llm_orchestration_state(simulation_state, llm_state)


def begin_llm_request(
    simulation_state: Dict[str, Any],
    *,
    tick: int,
    sequence_index: int,
    actor_id: str,
    turn_id: str,
    sequence_id: str = "",
    speaker_id: str = "",
    mode: str = "dialogue",
    provider: str = "",
    model: str = "",
    input_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create or replace an active orchestration request deterministically."""
    simulation_state = ensure_llm_orchestration_state(simulation_state)
    llm_state = get_llm_orchestration_state(simulation_state)
    active_requests = list(_safe_list(llm_state.get("active_requests")))

    tick = _safe_int(tick, 0)
    sequence_index = max(0, _safe_int(sequence_index, 0))
    actor_id = _safe_str(actor_id)
    turn_id = _safe_str(turn_id)
    speaker_id = _safe_str(speaker_id) or actor_id
    mode = _normalize_request_mode(mode)
    provider = _safe_str(provider)
    model = _safe_str(model)
    input_payload = _safe_dict(input_payload)

    if not actor_id or not turn_id:
        return simulation_state

    request_counter = max(0, _safe_int(llm_state.get("request_counter"), 0))
    request_id = build_llm_request_id(tick, sequence_index, actor_id, request_counter)
    sequence_id = _safe_str(sequence_id) or f"seq:{tick}:{sequence_index}"

    request = _normalize_request({
        "request_id": request_id,
        "tick": tick,
        "sequence_id": sequence_id,
        "turn_id": turn_id,
        "actor_id": actor_id,
        "speaker_id": speaker_id,
        "mode": mode,
        "status": "pending",
        "provider": provider,
        "model": model,
        "input_payload": input_payload,
        "stream_events": [],
        "output_text": "",
        "error": "",
    })

    existing_idx = _find_request_index_by_id(active_requests, request_id)
    if existing_idx >= 0:
        active_requests[existing_idx] = request
    else:
        active_requests.append(request)

    active_requests = sorted(
        [_normalize_request(v) for v in active_requests],
        key=_sort_key_request,
    )[-_MAX_ACTIVE_REQUESTS:]

    llm_state["active_requests"] = active_requests
    llm_state["request_counter"] = request_counter + 1
    llm_state["last_error"] = {}

    return _set_llm_orchestration_state(simulation_state, llm_state)


def append_llm_stream_event(
    simulation_state: Dict[str, Any],
    *,
    request_id: str,
    event_index: int,
    event_type: str = "text_chunk",
    text: str = "",
    final: bool = False,
    raw: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Append a structured stream event to an active request deterministically."""
    simulation_state = ensure_llm_orchestration_state(simulation_state)
    llm_state = get_llm_orchestration_state(simulation_state)
    active_requests = list(_safe_list(llm_state.get("active_requests")))

    request_id = _safe_str(request_id)
    if not request_id:
        return simulation_state

    request_idx = _find_request_index_by_id(active_requests, request_id)
    if request_idx < 0:
        return simulation_state

    request = _normalize_request(active_requests[request_idx])
    stream_events = list(_safe_list(request.get("stream_events")))
    stream_events.append({
        "event_index": max(0, _safe_int(event_index, 0)),
        "event_type": _safe_str(event_type) or "text_chunk",
        "text": _safe_str(text),
        "final": _safe_bool(final),
        "raw": _safe_dict(raw),
    })
    request["stream_events"] = _dedupe_and_sort_stream_events(stream_events)

    if request["status"] not in {"complete", "failed", "replayed"}:
        request["status"] = "streaming"

    active_requests[request_idx] = _normalize_request(request)
    llm_state["active_requests"] = active_requests

    return _set_llm_orchestration_state(simulation_state, llm_state)


def finalize_llm_request(
    simulation_state: Dict[str, Any],
    *,
    request_id: str,
    output_text: str = "",
    replayed: bool = False,
) -> Dict[str, Any]:
    """Finalize an active request and move it to completed history."""
    simulation_state = ensure_llm_orchestration_state(simulation_state)
    llm_state = get_llm_orchestration_state(simulation_state)
    active_requests = list(_safe_list(llm_state.get("active_requests")))

    request_id = _safe_str(request_id)
    if not request_id:
        return simulation_state

    request_idx = _find_request_index_by_id(active_requests, request_id)
    if request_idx < 0:
        return simulation_state

    request = _normalize_request(active_requests[request_idx])
    if output_text:
        request["output_text"] = _safe_str(output_text)
    request["status"] = "replayed" if _safe_bool(replayed) else "complete"
    request["error"] = ""

    llm_state = _move_request_to_completed(llm_state, request)
    llm_state["last_error"] = {}

    return _set_llm_orchestration_state(simulation_state, llm_state)


def fail_llm_request(
    simulation_state: Dict[str, Any],
    *,
    request_id: str,
    error: str,
) -> Dict[str, Any]:
    """Fail an active request and move it to completed history."""
    simulation_state = ensure_llm_orchestration_state(simulation_state)
    llm_state = get_llm_orchestration_state(simulation_state)
    active_requests = list(_safe_list(llm_state.get("active_requests")))

    request_id = _safe_str(request_id)
    error = _safe_str(error)
    if not request_id:
        return simulation_state

    request_idx = _find_request_index_by_id(active_requests, request_id)
    if request_idx < 0:
        return simulation_state

    request = _normalize_request(active_requests[request_idx])
    request["status"] = "failed"
    request["error"] = error

    llm_state = _move_request_to_completed(llm_state, request)
    llm_state["last_error"] = {
        "request_id": request_id,
        "error": error,
    }

    return _set_llm_orchestration_state(simulation_state, llm_state)