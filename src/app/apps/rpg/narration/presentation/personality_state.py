"""Phase 10 — Deterministic personality state helpers.

Provides helpers to ensure and query personality state
within simulation state.
"""
from __future__ import annotations

from typing import Any, Dict, cast

from app.apps.rpg.narration.presentation.visual_state import ensure_visual_state
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str

_MAX_TRAITS = 8
_MAX_STYLE_TAGS = 8


def _normalize_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a personality profile to a consistent shape."""
    profile = _safe_dict(profile)
    return {
        "actor_id": _safe_str(profile.get("actor_id")),
        "display_name": _safe_str(profile.get("display_name")),
        "tone": _safe_str(profile.get("tone") or "neutral"),
        "voice_style": _safe_str(profile.get("voice_style") or "plain"),
        "traits": [
            _safe_str(v)
            for v in _safe_list(profile.get("traits"))[:_MAX_TRAITS]
            if _safe_str(v)
        ],
        "style_tags": [
            _safe_str(v)
            for v in _safe_list(profile.get("style_tags"))[:_MAX_STYLE_TAGS]
            if _safe_str(v)
        ],
        "temperature_hint": _safe_str(profile.get("temperature_hint") or "low"),
    }


def ensure_personality_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure simulation_state has normalized personality state.

    Returns the mutated simulation_state with normalized profiles.
    """
    if not isinstance(simulation_state, dict):
        simulation_state = {}
    presentation_state = simulation_state.setdefault("presentation_state", {})
    if not isinstance(presentation_state, dict):
        presentation_state = simulation_state["presentation_state"] = {}
    personality_state = presentation_state.setdefault("personality_state", {})
    if not isinstance(personality_state, dict):
        personality_state = presentation_state["personality_state"] = {}
    profiles_in = personality_state.get("profiles", {})
    if not isinstance(profiles_in, dict):
        profiles_in = {}

    profiles_out: Dict[str, Dict[str, Any]] = {}
    for actor_id in sorted(profiles_in.keys()):
        normalized = _normalize_profile(cast(Any, profiles_in.get(actor_id)))
        normalized["actor_id"] = _safe_str(actor_id or normalized.get("actor_id"))
        profiles_out[str(actor_id)] = normalized

    personality_state["profiles"] = profiles_out

    simulation_state = ensure_visual_state(simulation_state)
    return simulation_state


def get_actor_personality_profile(simulation_state: Dict[str, Any], actor_id: str, default_name: str = "") -> Dict[str, Any]:
    """Get a normalized personality profile for a given actor.

    Ensures personality state exists and returns the profile.
    """
    simulation_state = ensure_personality_state(simulation_state)
    actor_id = _safe_str(actor_id)
    presentation_state = simulation_state.setdefault("presentation_state", {})
    if not isinstance(presentation_state, dict):
        presentation_state = simulation_state["presentation_state"] = {}

    personality_state = presentation_state.setdefault("personality_state", {})
    if not isinstance(personality_state, dict):
        personality_state = presentation_state["personality_state"] = {}

    profiles = personality_state.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = personality_state["profiles"] = {}

    if actor_id and actor_id not in profiles:
        profile = {
            "actor_id": actor_id,
            "display_name": _safe_str(default_name or actor_id),
            "tone": "neutral",
            "voice_style": "plain",
            "traits": [],
            "style_tags": [],
            "temperature_hint": "low",
        }
        profiles[actor_id] = profile
        personality_state["profiles"] = profiles
        presentation_state["personality_state"] = personality_state
        simulation_state["presentation_state"] = presentation_state

    return _normalize_profile(profiles.get(actor_id, {}))


def build_personality_summary(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a summary of all personality profiles."""
    simulation_state = ensure_personality_state(simulation_state)
    profiles = _safe_dict(
        _safe_dict(_safe_dict(simulation_state.get("presentation_state")).get("personality_state")).get("profiles")
    )
    return {
        "profile_count": len(profiles),
        "actor_ids": sorted(profiles.keys())[:20],
    }


