"""Phase 10.7 — Orchestration-to-presentation bridge.

Pure read-only bridge from normalized orchestration state to presentation
payload shape.
"""
from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.narration.live_provider_state import get_live_provider_state
from app.apps.rpg.narration.orchestration_state import get_llm_orchestration_state
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _build_request_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    item = _safe_dict(item)
    return {
        "request_id": _safe_str(item.get("request_id")),
        "tick": _safe_int(item.get("tick"), 0),
        "sequence_id": _safe_str(item.get("sequence_id")),
        "turn_id": _safe_str(item.get("turn_id")),
        "actor_id": _safe_str(item.get("actor_id")),
        "speaker_id": _safe_str(item.get("speaker_id")),
        "mode": _safe_str(item.get("mode")),
        "status": _safe_str(item.get("status")),
        "provider": _safe_str(item.get("provider")),
        "model": _safe_str(item.get("model")),
        "output_text": _safe_str(item.get("output_text")),
        "error": _safe_str(item.get("error")),
        "stream_event_count": len(_safe_list(item.get("stream_events"))),
        "has_input_payload": bool(_safe_dict(item.get("input_payload"))),
        "is_replayed": _safe_str(item.get("status")) == "replayed",
        "is_failed": _safe_str(item.get("status")) == "failed",
        "has_captured_stream": len(_safe_list(item.get("stream_events"))) > 0,
    }


def build_orchestration_presentation_payload(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a pure presentation payload from orchestration state."""
    llm_state = get_llm_orchestration_state(simulation_state)
    live_state = get_live_provider_state(simulation_state)

    active_requests = [
        _build_request_payload(v)
        for v in _safe_list(llm_state.get("active_requests"))
        if isinstance(v, dict)
    ]
    active_requests = sorted(
        active_requests,
        key=lambda item: (
            _safe_int(item.get("tick"), 0),
            _safe_str(item.get("request_id")),
            _safe_str(item.get("turn_id")),
        ),
    )
    completed_requests = [
        _build_request_payload(v)
        for v in _safe_list(llm_state.get("completed_requests"))
        if isinstance(v, dict)
    ]
    completed_requests = sorted(
        completed_requests,
        key=lambda item: (
            _safe_int(item.get("tick"), 0),
            _safe_str(item.get("request_id")),
            _safe_str(item.get("turn_id")),
        ),
    )
    last_error = _safe_dict(llm_state.get("last_error"))

    live_execution_count = len(_safe_list(live_state.get("executions")))

    return {
        "llm_orchestration": {
            "provider_mode": _safe_str(llm_state.get("provider_mode")),
            "request_counter": _safe_int(llm_state.get("request_counter"), 0),
            "live_execution_supported": False,
            "has_live_provider_diagnostics": live_execution_count > 0,
            "capture_mode_active": _safe_str(llm_state.get("provider_mode")) == "capture",
            "live_mode_active": _safe_str(llm_state.get("provider_mode")) == "live",
            "live_execution_count": live_execution_count,
            "active_requests": active_requests,
            "completed_requests": completed_requests,
            "last_error": {
                "request_id": _safe_str(last_error.get("request_id")),
                "error": _safe_str(last_error.get("error")),
            },
        }
    }
