"""Migration v5 -> v6: Add companion intelligence fields.

This migration adds HP, max_hp, loyalty, morale, status, role, and equipment
fields to existing companion records.

Fix #4: Migration now uses _normalize_companion to ensure full normalization
of companion records, including proper equipment structure, edge cases for
missing fields, and malformed companions from earlier saves.
"""
from typing import Any

from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


# The v6 companion normalization, frozen here: a migration must keep producing
# the v6 shape whatever the party rules become (copied from
# rules.party.party_state, R-3).
VALID_SLOTS = {"weapon", "armor", "consumable"}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_companion(companion: dict[str, Any]) -> dict[str, Any]:
    """Normalize a companion dict to ensure all fields are present and valid."""
    companion = _safe_dict(companion)
    max_hp = max(1, _safe_int(companion.get("max_hp"), 100))
    hp = _clamp(_safe_int(companion.get("hp"), max_hp), 0, max_hp)
    loyalty = _clamp(_safe_float(companion.get("loyalty"), 0.5), -1.0, 1.0)
    morale = _clamp(_safe_float(companion.get("morale"), 0.5), 0.0, 1.0)
    status = _safe_str(companion.get("status") or "active")
    role = _safe_str(companion.get("role") or "ally")
    equipment = _safe_dict(companion.get("equipment"))

    # Fix #1: Equipment stores only item_id (pointer), not qty. Inventory owns quantity.
    clean_equipment = {}
    for slot in VALID_SLOTS:
        entry = equipment.get(slot)
        if isinstance(entry, dict) and entry.get("item_id"):
            clean_equipment[slot] = _safe_str(entry.get("item_id"))
    return {
        "npc_id": _safe_str(companion.get("npc_id")),
        "name": _safe_str(companion.get("name") or companion.get("npc_id") or "Companion"),
        "hp": int(hp),
        "max_hp": max_hp,
        "loyalty": loyalty,
        "morale": morale,
        "role": role,
        "status": status,
        "equipment": clean_equipment,
        "source": _safe_str(companion.get("source")),
        "joined_tick": _safe_int(companion.get("joined_tick"), 0),
        "follow_mode": _safe_str(companion.get("follow_mode") or "following_player"),
        "location_id": _safe_str(companion.get("location_id")),
        "identity_arc": _safe_str(companion.get("identity_arc")),
        "current_role": _safe_str(companion.get("current_role")),
        "active_motivations": _safe_list(companion.get("active_motivations"))[:4],
    }


def migrate_v5_to_v6(package: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v5 save package to v6 schema.

    Uses _normalize_companion for full record normalization.
    """
    package = dict(package or {})
    state = _safe_dict(package.get("state"))
    simulation_state = _safe_dict(state.get("simulation_state"))
    player_state = _safe_dict(simulation_state.get("player_state"))
    party_state = _safe_dict(player_state.get("party_state"))

    # Fix #4: Use _normalize_companion for complete normalization
    companions = []
    seen_ids = set()
    for comp in party_state.get("companions") or []:
        if isinstance(comp, dict) and comp.get("npc_id"):
            npc_id = comp.get("npc_id")
            if npc_id not in seen_ids:
                seen_ids.add(npc_id)
                companions.append(_normalize_companion(comp))

    # Deduplicated, normalized companion list
    companions = sorted(companions, key=lambda c: str(c.get("npc_id")))
    companions = companions[:6]  # Cap at 6 companions

    party_state["companions"] = companions
    party_state.setdefault("max_size", 3)
    player_state["party_state"] = party_state
    simulation_state["player_state"] = player_state
    state["simulation_state"] = simulation_state
    package["state"] = state
    package["schema_version"] = 6
    return package