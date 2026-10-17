"""Phase 10 — LLM payload builders.

Provides deterministic LLM payload builders for dialogue and scene narration.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .personality_state import get_actor_personality_profile
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _compact_history(messages: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    """Compact dialogue history to last N messages."""
    normalized = []
    for msg in _safe_list(messages):
        if not isinstance(msg, dict):
            continue
        normalized.append({
            "speaker": _safe_str(msg.get("speaker")),
            "text": _safe_str(msg.get("text")),
        })

    normalized = sorted(
        normalized,
        key=lambda m: (
            _safe_str(m.get("speaker")),
            _safe_str(m.get("text")),
        ),
    )
    return normalized[-limit:]


def build_dialogue_llm_payload(simulation_state: Dict[str, Any], dialogue_state: Dict[str, Any], actor_id: str, actor_name: str = "") -> Dict[str, Any]:
    """Build LLM payload for a dialogue turn.

    Includes personality context, dialogue history, and instructions.
    """
    simulation_state = _safe_dict(simulation_state)
    dialogue_state = _safe_dict(dialogue_state)
    profile = get_actor_personality_profile(simulation_state, actor_id, default_name=actor_name)

    return {
        "mode": "dialogue",
        "actor_id": _safe_str(actor_id),
        "actor_name": _safe_str(actor_name or profile.get("display_name")),
        "personality": profile,
        "dialogue_context": {
            "scene_id": _safe_str(dialogue_state.get("scene_id")),
            "location_id": _safe_str(dialogue_state.get("location_id")),
            "topic": _safe_str(dialogue_state.get("topic")),
            "history": _compact_history(_safe_list(dialogue_state.get("transcript"))),
        },
        "instructions": {
            "stay_in_character": True,
            "respect_simulation_authority": True,
            "do_not_invent_world_facts": True,
            "prefer_short_responses": True,
        },
    }


def build_scene_llm_payload(simulation_state: Dict[str, Any], scene_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build LLM payload for scene narration.

    Includes scene context and grounded instructions.
    """
    simulation_state = _safe_dict(simulation_state)
    scene_state = _safe_dict(scene_state)
    return {
        "mode": "scene_narration",
        "scene_context": {
            "scene_id": _safe_str(scene_state.get("scene_id")),
            "location_id": _safe_str(scene_state.get("location_id")),
            "tone": _safe_str(scene_state.get("tone")),
            "summary": _safe_str(scene_state.get("summary")),
        },
        "instructions": {
            "respect_simulation_authority": True,
            "do_not_resolve_uncommitted_actions": True,
            "prefer_grounded sensory detail": True,
        },
    }