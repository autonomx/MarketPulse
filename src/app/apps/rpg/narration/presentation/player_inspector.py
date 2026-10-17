"""Product Layer A4 — Player-safe inspector overlay.

Read-only summary overlay for player-facing state visibility.
This is intentionally lighter than GM/debug inspector tooling.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list


def _safe_str(v: Any, default: str = "") -> str:
    return str(v) if v is not None else default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _band(value: float) -> str:
    value = _clamp(value)
    if value < 0.2:
        return "low"
    if value < 0.4:
        return "guarded"
    if value < 0.6:
        return "steady"
    if value < 0.8:
        return "rising"
    return "high"


def build_player_inspector_overlay_payload(
    simulation_state: Dict[str, Any] | None = None,
    runtime_payload: Dict[str, Any] | None = None,
    orchestration_payload: Dict[str, Any] | None = None,
    live_provider_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build player-safe inspector overlay payload for state visibility."""
    simulation_state = _safe_dict(simulation_state)
    runtime_payload = _safe_dict(runtime_payload)
    orchestration_payload = _safe_dict(orchestration_payload)
    live_provider_payload = _safe_dict(live_provider_payload)

    runtime_dialogue = _safe_dict(runtime_payload.get("runtime_dialogue"))
    llm_orchestration = _safe_dict(orchestration_payload.get("llm_orchestration"))
    live_provider = _safe_dict(live_provider_payload.get("live_provider"))

    director_state = _safe_dict(simulation_state.get("director_state"))
    global_tension = _clamp(_safe_float(director_state.get("global_tension"), 0.3))

    turns = _safe_list(runtime_dialogue.get("turns"))
    last_turn = _safe_dict(turns[-1]) if turns else {}

    scene_state = _safe_dict(simulation_state.get("scene_state"))
    relationship_hint = _safe_str(last_turn.get("emotion"), "neutral")
    provider_mode = _safe_str(llm_orchestration.get("provider_mode"), "disabled")
    execution_count = len(_safe_list(live_provider.get("executions")))

    overlay = {
        "player_overlay": {
            "scene": {
                "scene_id": _safe_str(scene_state.get("scene_id")),
                "location_name": _safe_str(scene_state.get("location_name")),
            },
            "tension": {
                "value": global_tension,
                "band": _band(global_tension),
            },
            "conversation": {
                "turn_cursor": runtime_dialogue.get("turn_cursor", 0),
                "latest_speaker": _safe_str(last_turn.get("speaker_name")),
                "latest_emotion": relationship_hint,
            },
            "relationship_hint": {
                "label": relationship_hint,
            },
            "system_status": {
                "provider_mode": provider_mode,
                "live_execution_count": execution_count,
            },
        }
    }
    return overlay