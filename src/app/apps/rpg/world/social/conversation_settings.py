from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value) if not isinstance(value, str) else value


# ── Allowed values for validated settings ─────────────────────────────────

_ALLOWED_FREQUENCIES = ("sparse", "normal", "lively")
_FREQUENCY_MULTIPLIERS = {"sparse": 0.5, "normal": 1.0, "lively": 1.5}
_ALLOWED_AMBIENT_DELAYS = (5, 10, 15, 30, 60, 300, 600)


def get_default_conversation_settings() -> Dict[str, Any]:
    return {
        # Original settings
        "ambient_conversations_enabled": True,
        "party_reaction_interrupts_enabled": True,
        "player_intervention_enabled": True,
        "avg_conversation_turns": 4,
        "max_conversation_turns": 8,
        "min_ticks_between_ambient_conversations_per_location": 2,
        "max_active_conversations_per_location": 1,
        "llm_expand_npc_conversations": False,
        # 4C-F: Expanded settings
        "ambient_delay_after_player_turn": 15,   # seconds before ambient convos resume after player turn
        "max_concurrent_ambient_threads": 3,     # 0-3 simultaneous conversations
        "max_beats_per_ambient_thread": 5,       # 2-6 beats per ambient thread
        "allow_npc_address_player": True,         # allow NPCs to auto-address player
        "allow_conversation_world_signals": True, # allow conversations to generate world signals
        "conversation_frequency": "normal",       # sparse | normal | lively
        "combat_suppression": True,               # suppress conversations during combat
        "stealth_suppression": True,              # suppress conversations during stealth
    }


def resolve_conversation_settings(simulation_state: Dict[str, Any], runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    defaults = get_default_conversation_settings()
    sim_settings = _safe_dict(_safe_dict(simulation_state).get("conversation_settings"))
    runtime_settings = _safe_dict(_safe_dict(runtime_state).get("conversation_settings"))

    merged = dict(defaults)
    merged.update(sim_settings)
    merged.update(runtime_settings)

    # Validate frequency
    frequency = _safe_str(merged.get("conversation_frequency"), "normal").lower()
    if frequency not in _ALLOWED_FREQUENCIES:
        frequency = "normal"

    return {
        # Original settings
        "ambient_conversations_enabled": _safe_bool(merged.get("ambient_conversations_enabled"), True),
        "party_reaction_interrupts_enabled": _safe_bool(merged.get("party_reaction_interrupts_enabled"), True),
        "player_intervention_enabled": _safe_bool(merged.get("player_intervention_enabled"), True),
        "avg_conversation_turns": max(1, _safe_int(merged.get("avg_conversation_turns"), 4)),
        "max_conversation_turns": max(1, _safe_int(merged.get("max_conversation_turns"), 8)),
        "min_ticks_between_ambient_conversations_per_location": max(0, _safe_int(merged.get("min_ticks_between_ambient_conversations_per_location"), 2)),
        "max_active_conversations_per_location": max(1, _safe_int(merged.get("max_active_conversations_per_location"), 1)),
        "llm_expand_npc_conversations": _safe_bool(merged.get("llm_expand_npc_conversations"), False),
        # 4C-F: Expanded settings
        "ambient_delay_after_player_turn": max(0, _safe_int(merged.get("ambient_delay_after_player_turn"), 15)),
        "max_concurrent_ambient_threads": max(0, min(3, _safe_int(merged.get("max_concurrent_ambient_threads"), 3))),
        "max_beats_per_ambient_thread": max(2, min(6, _safe_int(merged.get("max_beats_per_ambient_thread"), 5))),
        "allow_npc_address_player": _safe_bool(merged.get("allow_npc_address_player"), True),
        "allow_conversation_world_signals": _safe_bool(merged.get("allow_conversation_world_signals"), True),
        "conversation_frequency": frequency,
        "combat_suppression": _safe_bool(merged.get("combat_suppression"), True),
        "stealth_suppression": _safe_bool(merged.get("stealth_suppression"), True),
    }


def get_frequency_multiplier(settings: Dict[str, Any]) -> float:
    """Return the conversation frequency multiplier for scheduling decisions."""
    settings = _safe_dict(settings)
    frequency = _safe_str(settings.get("conversation_frequency"), "normal")
    return _FREQUENCY_MULTIPLIERS.get(frequency, 1.0)


def is_combat_active(simulation_state: Dict[str, Any]) -> bool:
    """Check if combat is currently active in the simulation."""
    simulation_state = _safe_dict(simulation_state)
    # Check common combat indicators
    combat_state = _safe_dict(simulation_state.get("combat_state"))
    if _safe_bool(combat_state.get("active")):
        return True
    active_interactions = simulation_state.get("active_interactions")
    if isinstance(active_interactions, list):
        for interaction in active_interactions:
            if isinstance(interaction, dict) and _safe_str(interaction.get("type")) == "combat":
                return True
    return False


def is_stealth_active(simulation_state: Dict[str, Any]) -> bool:
    """Check if stealth mode is currently active."""
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    return _safe_bool(player_state.get("stealth_active"))


def should_suppress_conversations(
    simulation_state: Dict[str, Any],
    settings: Dict[str, Any],
) -> bool:
    """Check if conversations should be suppressed due to combat or stealth."""
    settings = _safe_dict(settings)
    if _safe_bool(settings.get("combat_suppression")) and is_combat_active(simulation_state):
        return True
    if _safe_bool(settings.get("stealth_suppression")) and is_stealth_active(simulation_state):
        return True
    return False
