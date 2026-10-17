from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


DEFAULT_CONVERSATION_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "autonomous_ticks_enabled": False,
    "show_ambient_conversations": True,
    "frequency": "normal",
    "min_ticks_between_conversations": 3,
    "thread_cooldown_ticks": 8,
    "max_active_threads": 2,
    "max_beats_per_thread": 4,
    "conversation_chance_percent": 30,
    "allow_player_addressed": True,
    "allow_player_invited": False,
    "player_inclusion_chance_percent": 10,
    "require_relevant_memory_to_address_player": True,
    "pending_response_timeout_ticks": 3,
    "allow_world_signals": True,
    "allow_world_events": True,
    "allow_relationship_effects": False,
    "allow_quest_discussion": True,
    "allow_event_discussion": True,
    "allow_rumor_discussion": True,
    "allow_memory_discussion": True,
    "max_world_signals_per_thread": 2,
    "max_world_events_per_thread": 4,
    "signal_strength_cap": 1,
    # Bundle N-O-P — NPC Biography Roleplay
    "npc_roleplay_use_llm": False,
    "npc_roleplay_max_line_chars": 240,
    "npc_roleplay_fallback_on_invalid": True,
    "test_force_conversation_speaker_id": "",
    "test_force_conversation_listener_id": "",
    # Bundle H — NPC response beats + topic pivoting
    "allow_npc_response_beats": True,
    "npc_response_style_influence": True,
    # Bundle KLM — NPC goals + scene activities
    "avoid_repeated_npc_response_lines": True,
    "allow_npc_goal_influence": True,
    "goal_player_invitation_bias_cap": 20,
    # Bundle QRS — NPC history, reputation, conversation director
    "npc_history_enabled": True,
    "npc_history_max_entries_per_npc": 20,
    "npc_history_ttl_ticks": 1000,
    "npc_reputation_enabled": True,
    "conversation_director_enabled": True,
    "conversation_director_cooldown_ticks": 4,
    # Bundle J — rumor propagation caps
    "allow_rumor_propagation": True,
    "max_rumor_seeds": 24,
    "max_rumor_mentions_per_location": 3,
    "max_signal_age_ticks": 80,
    "allow_scene_activities": True,
    "scene_activity_interval_ticks": 2,
    "scene_activity_cooldown_ticks": 3,
    "allow_scene_activity_world_events": True,
    "allow_scene_activity_world_signals": True,
    # Bundle T-U-V — NPC Schedule, Location Presence, and Dynamic Scene Population
    "npc_schedule_enabled": True,
    "npc_presence_enabled": True,
    "scene_population_enabled": True,
    # Bundle W-X-Y — Quest/NPC Knowledge, Dialogue Memory Recall, and Scene Continuity
    "npc_knowledge_enabled": True,
    "npc_knowledge_max_facts_per_npc": 24,
    "npc_knowledge_ttl_ticks": 2000,
    "npc_dialogue_recall_enabled": True,
    "npc_dialogue_recall_cooldown_ticks": 4,
    "scene_continuity_enabled": True,
    # Bundle Z-AA-AB — Quest-Aware NPC Agency + Player Reputation Consequences
    "quest_conversation_access_enabled": True,
    "player_reputation_consequences_enabled": True,
    "quest_access_min_detail_level": 0,
    # Bundle AC-AD-AE — Quest Rumor Propagation, NPC Referrals, Consequence Signals
    "quest_rumor_propagation_enabled": True,
    "quest_rumor_ttl_ticks": 120,
    "npc_referrals_enabled": True,
    "consequence_signals_enabled": True,
    # Bundle AF-AG-AH — File-Based NPC Profiles + NPC Evolution Arcs
    "npc_file_profiles_enabled": True,
    "npc_evolution_enabled": True,
    "npc_evolution_from_reputation_enabled": True,
    "npc_evolution_from_world_events_enabled": True,
    # Bundle AI-AJ-AK — Companions, Arc Continuity
    "npc_party_eligibility_enabled": True,
    "companion_join_intent_enabled": True,
    "npc_arc_continuity_enabled": True,
    "companion_acceptance_enabled": True,
    "companion_dialogue_enabled": True,
}


FREQUENCY_TO_CHANCE = {
    "off": 0,
    "rare": 15,
    "normal": 30,
    "frequent": 60,
    "always": 100,
}


def conversation_settings_from_runtime(runtime_state: Dict[str, Any] | None) -> Dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    runtime_settings = _safe_dict(runtime_state.get("runtime_settings"))
    explicit = _safe_dict(runtime_settings.get("conversation_settings"))
    living_world = _safe_dict(runtime_settings.get("living_world"))
    nested = _safe_dict(living_world.get("conversation_settings"))

    settings = deepcopy(DEFAULT_CONVERSATION_SETTINGS)
    settings.update(nested)
    settings.update(explicit)
    result = normalize_conversation_settings(settings)
    # Pass through npc_profile_generation so companion offer gating can read it.
    npc_profile_gen = _safe_dict(runtime_settings.get("npc_profile_generation"))
    if npc_profile_gen:
        result["npc_profile_generation"] = npc_profile_gen
    return result


def normalize_conversation_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    settings = deepcopy(_safe_dict(settings))
    merged = deepcopy(DEFAULT_CONVERSATION_SETTINGS)
    merged.update(settings)

    frequency = str(merged.get("frequency") or "normal").strip().lower()
    if frequency not in FREQUENCY_TO_CHANCE:
        frequency = "normal"
    merged["frequency"] = frequency

    merged["enabled"] = _safe_bool(merged.get("enabled"), True)
    merged["autonomous_ticks_enabled"] = _safe_bool(merged.get("autonomous_ticks_enabled"), False)
    merged["show_ambient_conversations"] = _safe_bool(merged.get("show_ambient_conversations"), True)

    merged["min_ticks_between_conversations"] = max(0, _safe_int(merged.get("min_ticks_between_conversations"), 3))
    merged["thread_cooldown_ticks"] = max(0, _safe_int(merged.get("thread_cooldown_ticks"), 8))
    merged["max_active_threads"] = max(1, _safe_int(merged.get("max_active_threads"), 2))
    merged["max_beats_per_thread"] = max(1, _safe_int(merged.get("max_beats_per_thread"), 4))
    merged["conversation_chance_percent"] = max(
        0,
        min(100, _safe_int(merged.get("conversation_chance_percent"), FREQUENCY_TO_CHANCE[frequency])),
    )

    merged["allow_player_addressed"] = _safe_bool(merged.get("allow_player_addressed"), True)
    merged["allow_player_invited"] = _safe_bool(merged.get("allow_player_invited"), False)
    merged["player_inclusion_chance_percent"] = max(
        0,
        min(100, _safe_int(merged.get("player_inclusion_chance_percent"), 10)),
    )
    merged["require_relevant_memory_to_address_player"] = _safe_bool(
        merged.get("require_relevant_memory_to_address_player"),
        True,
    )
    merged["pending_response_timeout_ticks"] = max(1, _safe_int(merged.get("pending_response_timeout_ticks"), 3))

    merged["allow_world_signals"] = _safe_bool(merged.get("allow_world_signals"), True)
    merged["allow_world_events"] = _safe_bool(merged.get("allow_world_events"), True)
    merged["allow_relationship_effects"] = _safe_bool(merged.get("allow_relationship_effects"), False)
    merged["allow_quest_discussion"] = _safe_bool(merged.get("allow_quest_discussion"), True)
    merged["allow_event_discussion"] = _safe_bool(merged.get("allow_event_discussion"), True)
    merged["allow_rumor_discussion"] = _safe_bool(merged.get("allow_rumor_discussion"), True)
    merged["allow_memory_discussion"] = _safe_bool(merged.get("allow_memory_discussion"), True)
    merged["max_world_signals_per_thread"] = max(0, _safe_int(merged.get("max_world_signals_per_thread"), 2))
    merged["max_world_events_per_thread"] = max(0, _safe_int(merged.get("max_world_events_per_thread"), 4))
    merged["signal_strength_cap"] = max(1, _safe_int(merged.get("signal_strength_cap"), 1))
    merged["npc_roleplay_use_llm"] = _safe_bool(merged.get("npc_roleplay_use_llm"), False)
    merged["npc_roleplay_max_line_chars"] = max(
        80,
        min(600, _safe_int(merged.get("npc_roleplay_max_line_chars"), 240)),
    )
    merged["npc_roleplay_fallback_on_invalid"] = _safe_bool(
        merged.get("npc_roleplay_fallback_on_invalid"),
        True,
    )
    merged["test_force_conversation_speaker_id"] = _safe_str(
        merged.get("test_force_conversation_speaker_id")
    )
    merged["test_force_conversation_listener_id"] = _safe_str(
        merged.get("test_force_conversation_listener_id")
    )
    # Bundle H
    merged["allow_npc_response_beats"] = _safe_bool(merged.get("allow_npc_response_beats"), True)
    merged["npc_response_style_influence"] = _safe_bool(merged.get("npc_response_style_influence"), True)
    # Bundle KLM
    merged["avoid_repeated_npc_response_lines"] = _safe_bool(merged.get("avoid_repeated_npc_response_lines"), True)
    merged["allow_npc_goal_influence"] = _safe_bool(merged.get("allow_npc_goal_influence"), True)
    merged["goal_player_invitation_bias_cap"] = max(0, min(50, _safe_int(merged.get("goal_player_invitation_bias_cap"), 20)))
    # Bundle QRS
    merged["npc_history_enabled"] = _safe_bool(merged.get("npc_history_enabled"), True)
    merged["npc_history_max_entries_per_npc"] = max(1, min(50, _safe_int(merged.get("npc_history_max_entries_per_npc"), 20)))
    merged["npc_history_ttl_ticks"] = max(10, min(100000, _safe_int(merged.get("npc_history_ttl_ticks"), 1000)))
    merged["npc_reputation_enabled"] = _safe_bool(merged.get("npc_reputation_enabled"), True)
    merged["conversation_director_enabled"] = _safe_bool(merged.get("conversation_director_enabled"), True)
    merged["conversation_director_cooldown_ticks"] = max(1, min(100, _safe_int(merged.get("conversation_director_cooldown_ticks"), 4)))
    # Bundle J
    merged["allow_rumor_propagation"] = _safe_bool(merged.get("allow_rumor_propagation"), True)
    merged["max_rumor_seeds"] = max(1, _safe_int(merged.get("max_rumor_seeds"), 24))
    merged["max_rumor_mentions_per_location"] = max(1, _safe_int(merged.get("max_rumor_mentions_per_location"), 3))
    merged["max_signal_age_ticks"] = max(1, _safe_int(merged.get("max_signal_age_ticks"), 80))
    merged["allow_scene_activities"] = _safe_bool(merged.get("allow_scene_activities"), True)
    merged["scene_activity_interval_ticks"] = max(1, _safe_int(merged.get("scene_activity_interval_ticks"), 2))
    merged["scene_activity_cooldown_ticks"] = max(0, _safe_int(merged.get("scene_activity_cooldown_ticks"), 3))
    merged["allow_scene_activity_world_events"] = _safe_bool(merged.get("allow_scene_activity_world_events"), True)
    merged["allow_scene_activity_world_signals"] = _safe_bool(merged.get("allow_scene_activity_world_signals"), True)
    # Bundle T-U-V
    merged["npc_schedule_enabled"] = _safe_bool(merged.get("npc_schedule_enabled"), True)
    merged["npc_presence_enabled"] = _safe_bool(merged.get("npc_presence_enabled"), True)
    merged["scene_population_enabled"] = _safe_bool(merged.get("scene_population_enabled"), True)
    # Bundle W-X-Y
    merged["npc_knowledge_enabled"] = _safe_bool(merged.get("npc_knowledge_enabled"), True)
    merged["npc_knowledge_max_facts_per_npc"] = max(1, min(60, _safe_int(merged.get("npc_knowledge_max_facts_per_npc"), 24)))
    merged["npc_knowledge_ttl_ticks"] = max(10, min(100000, _safe_int(merged.get("npc_knowledge_ttl_ticks"), 2000)))
    merged["npc_dialogue_recall_enabled"] = _safe_bool(merged.get("npc_dialogue_recall_enabled"), True)
    merged["npc_dialogue_recall_cooldown_ticks"] = max(1, min(100, _safe_int(merged.get("npc_dialogue_recall_cooldown_ticks"), 4)))
    merged["scene_continuity_enabled"] = _safe_bool(merged.get("scene_continuity_enabled"), True)
    # Bundle Z-AA-AB
    merged["quest_conversation_access_enabled"] = _safe_bool(
        merged.get("quest_conversation_access_enabled"),
        True,
    )
    merged["player_reputation_consequences_enabled"] = _safe_bool(
        merged.get("player_reputation_consequences_enabled"),
        True,
    )
    merged["quest_access_min_detail_level"] = max(
        0,
        min(3, _safe_int(merged.get("quest_access_min_detail_level"), 0)),
    )
    # Bundle AC-AD-AE
    merged["quest_rumor_propagation_enabled"] = _safe_bool(
        merged.get("quest_rumor_propagation_enabled"),
        True,
    )
    merged["quest_rumor_ttl_ticks"] = max(
        4,
        min(10000, _safe_int(merged.get("quest_rumor_ttl_ticks"), 120)),
    )
    merged["npc_referrals_enabled"] = _safe_bool(
        merged.get("npc_referrals_enabled"),
        True,
    )
    merged["consequence_signals_enabled"] = _safe_bool(
        merged.get("consequence_signals_enabled"),
        True,
    )
    # Bundle AF-AG-AH
    merged["npc_file_profiles_enabled"] = _safe_bool(
        merged.get("npc_file_profiles_enabled"),
        True,
    )
    merged["npc_evolution_enabled"] = _safe_bool(
        merged.get("npc_evolution_enabled"),
        True,
    )
    merged["npc_evolution_from_reputation_enabled"] = _safe_bool(
        merged.get("npc_evolution_from_reputation_enabled"),
        True,
    )
    merged["npc_evolution_from_world_events_enabled"] = _safe_bool(
        merged.get("npc_evolution_from_world_events_enabled"),
        True,
    )
    # Bundle AI-AJ-AK
    merged["npc_party_eligibility_enabled"] = _safe_bool(
        merged.get("npc_party_eligibility_enabled"),
        True,
    )
    merged["companion_join_intent_enabled"] = _safe_bool(
        merged.get("companion_join_intent_enabled"),
        True,
    )
    merged["npc_arc_continuity_enabled"] = _safe_bool(
        merged.get("npc_arc_continuity_enabled"),
        True,
    )
    merged["companion_acceptance_enabled"] = _safe_bool(
        merged.get("companion_acceptance_enabled"),
        True,
    )
    merged["companion_dialogue_enabled"] = _safe_bool(
        merged.get("companion_dialogue_enabled"),
        True,
    )
    return merged


def should_attempt_autonomous_conversation(
    *,
    tick: int,
    last_conversation_tick: int,
    settings: Dict[str, Any],
    force: bool = False,
) -> bool:
    settings = normalize_conversation_settings(settings)
    if not settings.get("enabled"):
        return False
    if force:
        return True
    if not settings.get("autonomous_ticks_enabled"):
        return False
    min_delta = int(settings.get("min_ticks_between_conversations") or 0)
    if last_conversation_tick and int(tick or 0) - int(last_conversation_tick) < min_delta:
        return False

    chance = int(settings.get("conversation_chance_percent") or 0)
    if chance <= 0:
        return False
    if chance >= 100:
        return True

    # Deterministic pseudo-randomness from tick. This is replay-safe enough for
    # the current deterministic harness and avoids global RNG.
    bucket = (int(tick or 0) * 37 + 17) % 100
    return bucket < chance