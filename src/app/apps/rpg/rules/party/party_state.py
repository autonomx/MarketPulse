"""Phase 9.2 — Party state management.

Provides deterministic, serialisable party/companion state that sits on top
of the player state.

Key guarantees:
    - party_state is always present and well-formed
    - companions list is bounded to max_size (default 3)
    - all values are safe (str / dict / list) even if upstream data is dirty
    - state updates are pure functions returning a new player_state dict
    - equipment is a pointer (item_id only), inventory owns quantity
    - VALID_SLOTS enforced for equipment
"""
from typing import Any, Dict, List, Optional, cast
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

# Valid equipment slots — centralised for UI/balance parity
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


def _normalize_companion(companion: Dict[str, Any]) -> Dict[str, Any]:
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


def _is_companion_downed(companion: Dict[str, Any]) -> bool:
    """Check if a companion is downed and cannot act."""
    status = _safe_str(companion.get("status"))
    hp = _safe_int(companion.get("hp"), 0)
    return status == "downed" or hp <= 0


def ensure_party_state(player_state: Dict[str, Any]) -> Dict[str, Any]:  # noqa: D401
    """Ensure *player_state* has a well-formed ``party_state`` subtree.

    Idempotent — safe to call multiple times. Deduplicates companions by npc_id.
    """
    player_state = _safe_dict(player_state)

    party = _safe_dict(player_state.get("party_state"))

    # Deduplicate companions by npc_id, keeping last occurrence
    seen_ids = set()
    companions = []
    for comp in _safe_list(party.get("companions"))[:6]:
        if isinstance(comp, dict) and comp.get("npc_id"):
            npc_id = comp.get("npc_id")
            if npc_id not in seen_ids:
                seen_ids.add(npc_id)
                companions.append(_normalize_companion(comp))
    party["companions"] = companions
    party.setdefault("max_size", 3)

    player_state["party_state"] = party
    return player_state


def add_companion(
    player_state: Dict[str, Any],
    npc_id: str,
    name: str,
    *,
    role: str = "ally",
    source: str = "",
    joined_tick: int = 0,
    follow_mode: str = "following_player",
    location_id: str = "",
    identity_arc: str = "",
    current_role: str = "",
    active_motivations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Add a companion to the party if there is room and not already present."""
    player_state = ensure_party_state(player_state)
    party = player_state["party_state"]

    companions = _safe_list(party.get("companions"))

    # Check for duplicates
    if any(c.get("npc_id") == npc_id for c in companions):
        return player_state

    max_size = _safe_int(party.get("max_size"), 3)
    if max_size < 1:
        max_size = 1

    if len(companions) >= max_size:
        party["last_add_companion_result"] = {
            "added": False,
            "npc_id": npc_id,
            "reason": "party_full",
            "party_size": len(companions),
            "max_size": max_size,
            "source": "deterministic_party_state",
        }
        player_state["party_state"] = party
        return player_state

    companions.append(_normalize_companion({
        "npc_id": npc_id,
        "name": name,
        "hp": 100,
        "max_hp": 100,
        "loyalty": 0.5,
        "morale": 0.5,
        "role": role or "ally",
        "status": "active",
        "equipment": {},
        "source": source,
        "joined_tick": int(joined_tick or 0),
        "follow_mode": follow_mode or "following_player",
        "location_id": location_id,
        "identity_arc": identity_arc,
        "current_role": current_role,
        "active_motivations": _safe_list(active_motivations)[:4],
    }))
    companions = sorted(companions, key=lambda c: str(c.get("npc_id")))

    party["companions"] = companions
    party["last_add_companion_result"] = {
        "added": True,
        "npc_id": npc_id,
        "reason": "companion_added",
        "party_size": len(companions),
        "max_size": max_size,
        "source": "deterministic_party_state",
    }
    player_state["party_state"] = party
    return player_state


def remove_companion(player_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    """Remove a companion from the party by npc_id."""
    player_state = ensure_party_state(player_state)
    party = player_state["party_state"]

    companions = [
        c for c in _safe_list(party.get("companions"))
        if c.get("npc_id") != npc_id
    ]

    party["companions"] = companions
    player_state["party_state"] = party
    return player_state


def get_active_companions(player_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the list of active companions (not downed, hp > 0)."""
    player_state = ensure_party_state(player_state)
    companions = _safe_list(player_state["party_state"].get("companions"))
    return [
        _normalize_companion(comp)
        for comp in companions
        if not _is_companion_downed(comp)
    ]


def update_companion_hp(player_state: Dict[str, Any], npc_id: str, delta: int) -> Dict[str, Any]:
    """Update companion HP by delta, clamping to [0, max_hp] and setting status to 'downed' if HP <= 0."""
    player_state = ensure_party_state(player_state)
    party = _safe_dict(player_state.get("party_state"))
    out = []
    for comp in _safe_list(party.get("companions")):
        comp = _normalize_companion(comp)
        if comp.get("npc_id") == npc_id:
            comp["hp"] = int(_clamp(comp.get("hp", 0) + _safe_int(delta, 0), 0, comp.get("max_hp", 100)))
            if comp["hp"] <= 0:
                comp["status"] = "downed"
            elif comp["status"] == "downed" and comp["hp"] > 0:
                comp["status"] = "active"
        out.append(comp)
    party["companions"] = out
    player_state["party_state"] = party
    return player_state


def update_companion_loyalty(player_state: Dict[str, Any], npc_id: str, delta: float) -> Dict[str, Any]:
    """Update companion loyalty by delta, clamping to [-1.0, 1.0]."""
    player_state = ensure_party_state(player_state)
    party = _safe_dict(player_state.get("party_state"))
    out = []
    for comp in _safe_list(party.get("companions")):
        comp = _normalize_companion(comp)
        # Fix #6: Allow loyalty changes even for downed companions
        if comp.get("npc_id") == npc_id:
            comp["loyalty"] = _clamp(comp.get("loyalty", 0.5) + _safe_float(delta, 0.0), -1.0, 1.0)
        out.append(comp)
    party["companions"] = out
    player_state["party_state"] = party
    return player_state


def update_companion_morale(player_state: Dict[str, Any], npc_id: str, delta: float) -> Dict[str, Any]:
    """Update companion morale by delta, clamping to [0.0, 1.0]."""
    player_state = ensure_party_state(player_state)
    party = _safe_dict(player_state.get("party_state"))
    out = []
    for comp in _safe_list(party.get("companions")):
        comp = _normalize_companion(comp)
        # Fix #6: Allow morale changes even for downed companions
        if comp.get("npc_id") == npc_id:
            comp["morale"] = _clamp(comp.get("morale", 0.5) + _safe_float(delta, 0.0), 0.0, 1.0)
        out.append(comp)
    party["companions"] = out
    player_state["party_state"] = party
    return player_state


def set_companion_status(player_state: Dict[str, Any], npc_id: str, status: str) -> Dict[str, Any]:
    """Set companion status (active, downed, absent)."""
    player_state = ensure_party_state(player_state)
    party = _safe_dict(player_state.get("party_state"))
    out = []
    for comp in _safe_list(party.get("companions")):
        comp = _normalize_companion(comp)
        if comp.get("npc_id") == npc_id:
            comp["status"] = _safe_str(status or "active")
        out.append(comp)
    party["companions"] = out
    player_state["party_state"] = party
    return player_state


def set_companion_equipment(player_state: Dict[str, Any], npc_id: str, slot: str, item_id: str) -> Dict[str, Any]:
    """Set equipment for a companion in a given slot.

    Fix #1: Equipment stores item_id only (pointer), inventory owns quantity.
    Fix #6: Cannot equip if companion is downed.
    Fix #10: Validates slot name against VALID_SLOTS.
    """
    player_state = ensure_party_state(player_state)
    party = _safe_dict(player_state.get("party_state"))
    slot = _safe_str(slot)

    # Fix #10: Validate slot name
    if slot not in VALID_SLOTS:
        return player_state  # Reject invalid slot silently

    out = []
    for comp in _safe_list(party.get("companions")):
        comp = _normalize_companion(comp)
        # Fix #6: Cannot equip while downed
        if comp.get("npc_id") == npc_id and not _is_companion_downed(comp):
            equipment = dict(cast(Any, comp.get("equipment")))
            # Fix #1: Store item_id only, no qty
            equipment[slot] = _safe_str(item_id)
            comp["equipment"] = equipment
        out.append(comp)
    party["companions"] = out
    player_state["party_state"] = party
    return player_state


def clear_companion_equipment(player_state: Dict[str, Any], npc_id: str, slot: str) -> Dict[str, Any]:
    """Clear equipment for a companion in a given slot.

    Fix #6: Cannot unequip if companion is downed.
    Fix #10: Validates slot name.
    """
    player_state = ensure_party_state(player_state)
    party = _safe_dict(player_state.get("party_state"))
    slot = _safe_str(slot)

    # Fix #10: Validate slot name
    if slot not in VALID_SLOTS:
        return player_state

    out = []
    for comp in _safe_list(party.get("companions")):
        comp = _normalize_companion(comp)
        # Fix #6: Cannot unequip while downed (equipment persists in downed state)
        if comp.get("npc_id") == npc_id and not _is_companion_downed(comp):
            equipment = dict(cast(Any, comp.get("equipment")))
            equipment.pop(slot, None)
            comp["equipment"] = equipment
        out.append(comp)
    party["companions"] = out
    player_state["party_state"] = party
    return player_state


def get_companion_by_id(player_state: Dict[str, Any], npc_id: str) -> Optional[Dict[str, Any]]:
    """Look up a companion by npc_id, or None if not found."""
    player_state = ensure_party_state(player_state)
    party = _safe_dict(player_state.get("party_state"))
    for comp in _safe_list(party.get("companions")):
        comp = _normalize_companion(comp)
        if comp.get("npc_id") == npc_id:
            return comp
    return None


def build_party_summary(player_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a summary of the party state for UI/timeline display.

    Always recomputed from normalized state — never cached.
    """
    player_state = ensure_party_state(player_state)
    party = _safe_dict(player_state.get("party_state"))
    companions = [_normalize_companion(c) for c in _safe_list(party.get("companions"))]
    return {
        "size": len(companions),
        "active_count": len([c for c in companions if not _is_companion_downed(c)]),
        "downed_count": len([c for c in companions if _is_companion_downed(c)]),
        "avg_loyalty": round(
            sum(float(c.get("loyalty", 0.0)) for c in companions) / max(1, len(companions)),
            3,
        ),
        "avg_morale": round(
            sum(float(c.get("morale", 0.0)) for c in companions) / max(1, len(companions)),
            3,
        ),
    }