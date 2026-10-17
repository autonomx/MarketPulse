"""Phase 10.7 — Live provider state to presentation bridge.

Pure read-only bridge exposing compact provider execution diagnostics.
"""
from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.narration.live_provider_state import get_live_provider_state
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _build_execution_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    item = _safe_dict(item)
    events = [
        _safe_dict(v)
        for v in _safe_list(item.get("events"))
        if isinstance(v, dict)
    ]
    events = sorted(
        events,
        key=lambda event: (
            _safe_int(event.get("event_index"), 0),
            _safe_str(event.get("event_type")),
        ),
    )
    return {
        "execution_id": _safe_str(item.get("execution_id")),
        "request_id": _safe_str(item.get("request_id")),
        "tick": _safe_int(item.get("tick"), 0),
        "provider": _safe_str(item.get("provider")),
        "model": _safe_str(item.get("model")),
        "status": _safe_str(item.get("status")),
        "output_text": _safe_str(item.get("output_text")),
        "error": _safe_str(item.get("error")),
        "event_count": len(events),
        "is_complete": _safe_str(item.get("status")) == "complete",
        "is_failed": _safe_str(item.get("status")) == "failed",
        "has_output_text": bool(_safe_str(item.get("output_text"))),
    }


def build_live_provider_presentation_payload(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build pure presentation payload for live provider execution state."""
    live_state = get_live_provider_state(simulation_state)
    executions = [
        _build_execution_payload(v)
        for v in _safe_list(live_state.get("executions"))
        if isinstance(v, dict)
    ]
    executions = sorted(
        executions,
        key=lambda item: (
            _safe_int(item.get("tick"), 0),
            _safe_str(item.get("execution_id")),
        ),
    )
    complete_count = sum(1 for item in executions if bool(item.get("is_complete")))
    failed_count = sum(1 for item in executions if bool(item.get("is_failed")))
    streaming_count = sum(1 for item in executions if _safe_str(item.get("status")) == "streaming")
    return {
        "live_provider": {
            "execution_count": len(executions),
            "complete_count": complete_count,
            "failed_count": failed_count,
            "streaming_count": streaming_count,
            "executions": executions,
        }
    }