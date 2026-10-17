from __future__ import annotations

"""Runtime NPC response architecture.

This is a compact, prompt-facing packet that tells the presentation layer which
NPC may speak, what the current-turn obligation is, and which profile/memory
facts may shape tone only.
"""

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _target_from_context(narration_context: Dict[str, Any]) -> Dict[str, str]:
    turn_contract = _safe_dict(narration_context.get("turn_contract"))
    interpreted = _safe_dict(turn_contract.get("interpreted_action"))
    npc_behavior = _safe_dict(narration_context.get("npc_behavior_context") or turn_contract.get("npc_behavior_context"))
    return {
        "npc_id": _safe_str(interpreted.get("target_id") or npc_behavior.get("target_id")),
        "name": _safe_str(npc_behavior.get("target_name") or interpreted.get("target_name") or interpreted.get("target_id")),
        "role": _safe_str(npc_behavior.get("role")),
    }


def _profile_for_target(narration_context: Dict[str, Any], target: Dict[str, str]) -> Dict[str, Any]:
    direct = _safe_dict(narration_context.get("npc_profile_summary"))
    if direct:
        return direct

    runtime_state = _safe_dict(narration_context.get("runtime_state"))
    loaded = _safe_dict(_safe_dict(runtime_state.get("npc_evolution")).get("loaded_profiles"))
    target_keys = {
        _safe_str(target.get("npc_id")),
        _safe_str(target.get("name")),
    }
    for npc_id, row_any in loaded.items():
        profile = _safe_dict(_safe_dict(row_any).get("profile") or row_any)
        names = {
            _safe_str(npc_id),
            _safe_str(profile.get("id")),
            _safe_str(profile.get("npc_id")),
            _safe_str(profile.get("name")),
            _safe_str(profile.get("display_name")),
        }
        if target_keys & names:
            return profile
    return {}


def build_runtime_npc_response_architecture(
    *,
    narration_context: Dict[str, Any] | None = None,
    current_turn_prompt_contract: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    narration_context = _safe_dict(narration_context)
    contract = _safe_dict(current_turn_prompt_contract)
    target = _target_from_context(narration_context)
    profile = _profile_for_target(narration_context, target)
    npc_behavior = _safe_dict(narration_context.get("npc_behavior_context") or _safe_dict(narration_context.get("turn_contract")).get("npc_behavior_context"))

    return {
        "format_version": "runtime_npc_response_architecture_v1",
        "target_npc": {
            "npc_id": _safe_str(target.get("npc_id")),
            "name": _safe_str(target.get("name") or profile.get("name") or profile.get("display_name")),
            "role": _safe_str(target.get("role") or profile.get("role") or profile.get("occupation")),
            "profile_available": bool(profile),
        },
        "persona": {
            "arc_stage": _safe_str(profile.get("arc_stage") or "stable"),
            "axes": _safe_dict(profile.get("axes")),
            "persona": _safe_dict(profile.get("persona") or profile.get("personality")),
            "mood": _safe_str(npc_behavior.get("mood")),
            "relationship_to_player": npc_behavior.get("relationship_to_player"),
            "trust": npc_behavior.get("trust"),
            "fear": npc_behavior.get("fear"),
        },
        "memory_context": {
            "usage": "tone_and_continuity_only",
            "recent_memories": _safe_list(profile.get("memories"))[-4:] or _safe_list(npc_behavior.get("recent_memories"))[-4:],
            "milestones": _safe_list(profile.get("milestones"))[-3:],
            "future_hooks": _safe_list(profile.get("future_hooks"))[-3:],
        },
        "required_focus": _safe_list(contract.get("required_focus"))[:10],
        "rules": [
            "answer_current_turn_first",
            "use_profile_for_tone_only",
            "do_not_invent_new_profile_memories",
            "do_not_create_authoritative_outcomes",
        ],
    }
