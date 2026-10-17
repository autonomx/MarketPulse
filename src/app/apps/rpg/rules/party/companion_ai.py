"""Phase 9.2 — Companion AI for encounters.

Provides deterministic companion AI with role-based action selection.

Fixes applied:
- Fix #2: Execution duplication guard (_companions_ran marker + phase tracking)
- Fix #5: Loyalty uses explicit bands (-1..-0.3 hostile, -0.3..0.3 hesitant, 0.3..1 cooperative)
- Fix #7: Target selection is deterministic (sorted by id)
- Fix #9: Phase tracking for companion phase
- Fix #11: Morale is integrated into AI decision logic
"""
from typing import Any, Dict, List

from .party_state import (
    get_active_companions,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _get_hostile_targets(encounter_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Get hostile targets from encounter state, sorted deterministically by id."""
    participants = _safe_list(encounter_state.get("participants"))
    hostiles = [
        p for p in participants
        if isinstance(p, dict)
        and (_safe_str(p.get("role")) == "enemy" or _safe_str(p.get("disposition")) == "hostile")
    ]
    # Fix #7: Sort by id for deterministic target selection
    return sorted(hostiles, key=lambda p: str(p.get("id") or p.get("npc_id") or ""))


def choose_companion_action(companion: Dict[str, Any], encounter_state: Dict[str, Any]) -> Dict[str, Any]:
    """Choose deterministic action for a companion based on state and role.

    Priority:
    - Hostile loyalty (< -0.3): hesitate (refuse to fight)
    - Low HP (<= 25%): defend
    - Low morale (< 0.3): hesitate (fear)
    - Support role with low HP: heal_self/support
    - Has hostile targets: attack
    - Default: defend
    """
    companion = _safe_dict(companion)
    encounter_state = _safe_dict(encounter_state)

    loyalty = float(companion.get("loyalty", 0.0))
    hp = int(companion.get("hp", 0))
    max_hp = max(1, int(companion.get("max_hp", 100)))
    morale = float(companion.get("morale", 0.5))
    role = _safe_str(companion.get("role") or "ally")
    health_ratio = float(hp) / float(max_hp)

    # Fix #5: Loyalty bands with explicit thresholds
    # < -0.3: hostile companion behavior
    # -0.3 to 0.3: hesitant/indifferent
    # > 0.3: cooperative

    # Fix #11: Morale integration
    # < 0.3: scared companion, unlikely to act

    # Find hostile targets (sorted)
    hostiles = _get_hostile_targets(encounter_state)

    # Fix #1: Check for healing potion by item_id pointer only
    equipment = _safe_dict(companion.get("equipment"))
    has_healing_potion = _safe_str(equipment.get("consumable")) == "healing_potion"

    # Decision tree with explicit loyalty bands
    if loyalty < -0.3:
        # Fix #5: Hostile companion refuses to act
        return {"action_type": "hesitate", "target_id": "", "summary": f"{companion.get('name')} refuses to cooperate."}

    if health_ratio <= 0.25:
        return {"action_type": "defend", "target_id": "player", "summary": f"{companion.get('name')} falls back to protect the party."}

    # Fix #11: Morale-based hesitation
    if morale < 0.3:
        return {"action_type": "hesitate", "target_id": "", "summary": f"{companion.get('name')} is too frightened to act."}

    if role == "support" and has_healing_potion and health_ratio < 0.6:
        return {"action_type": "heal_self", "target_id": companion.get("npc_id"), "summary": f"{companion.get('name')} uses a healing potion."}

    if role == "support" and health_ratio < 0.6:
        return {"action_type": "support", "target_id": "player", "summary": f"{companion.get('name')} supports the player."}

    # Fix #7: Use first target from sorted list
    if hostiles:
        target = hostiles[0]
        target_id = str(target.get("id") or target.get("npc_id") or "")
        return {"action_type": "attack", "target_id": target_id, "summary": f"{companion.get('name')} attacks {target_id}."}

    return {"action_type": "defend", "target_id": "player", "summary": f"{companion.get('name')} watches for danger."}


def run_companion_turns(simulation_state: Dict[str, Any], encounter_state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute companion actions for the current encounter tick.

    Only runs if encounter is still active and companions haven't already run this tick.
    Fix #2: Execution duplication guard.
    Fix #9: Phase tracking.
    """
    player_state = simulation_state.get("player_state") or {}
    companions = get_active_companions(player_state)

    if not companions:
        return encounter_state

    encounter_state = _safe_dict(encounter_state)

    # Fix #2: Guard against resolved encounters
    if str(encounter_state.get("status") or "") == "resolved":
        return encounter_state

    # Fix #2: Guard against double execution
    if encounter_state.get("_companions_ran"):
        return encounter_state

    encounter_state.setdefault("log", [])

    # Fix #9: Mark companion phase
    encounter_state["phase"] = "companion"

    # Limit to max 3 companions, sorted deterministically
    for comp in sorted(companions, key=lambda c: str(c.get("npc_id")))[:3]:
        action = choose_companion_action(comp, encounter_state)
        encounter_state["log"].append({
            "type": "companion_action",
            "npc_id": comp.get("npc_id"),
            "action_type": action.get("action_type"),
            "target_id": action.get("target_id"),
            "summary": action.get("summary"),
        })

    # Fix #2: Mark companions as having run this tick
    encounter_state["_companions_ran"] = True

    return encounter_state