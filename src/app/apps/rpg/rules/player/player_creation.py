"""Phase 18.3A — Player character creation contract."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .player_progression_state import (
    allocate_starting_stats,
    ensure_player_progression_state,
)

_DEFAULT_TOTAL_POINTS = 12
_STAT_NAMES = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]

def build_default_stat_allocation(template: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Return a default even-spread stat allocation."""
    template = dict(template or {})
    allocation = {}
    per_stat = _DEFAULT_TOTAL_POINTS // len(_STAT_NAMES)
    remainder = _DEFAULT_TOTAL_POINTS % len(_STAT_NAMES)
    for i, stat in enumerate(_STAT_NAMES):
        allocation[stat] = per_stat + (1 if i < remainder else 0)
    return allocation

def validate_stat_allocation(allocation: Dict[str, int], total_points: int = _DEFAULT_TOTAL_POINTS) -> Dict[str, Any]:
    """Validate a point-buy allocation. Returns {ok: bool, errors: [...], total: int}."""
    allocation = dict(allocation or {})
    errors = []
    total = 0
    for stat_name, pts in allocation.items():
        pts = int(pts) if isinstance(pts, (int, float)) else 0
        if stat_name not in _STAT_NAMES:
            errors.append(f"Unknown stat: {stat_name}")
        if pts < 0:
            errors.append(f"Negative points for {stat_name}")
        total += pts
    if total > total_points:
        errors.append(f"Total {total} exceeds budget {total_points}")
    return {"ok": len(errors) == 0, "errors": errors, "total": total}

def apply_character_creation(player_state: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply character creation choices: name, class, background, species, stat allocation."""
    player_state = ensure_player_progression_state(player_state)
    payload = dict(payload or {})
    if payload.get("name"):
        player_state["name"] = str(payload["name"])
    if payload.get("class_id"):
        player_state["class_id"] = str(payload["class_id"])
    if payload.get("background_id"):
        player_state["background_id"] = str(payload["background_id"])
    if payload.get("species_id"):
        player_state["species_id"] = str(payload["species_id"])
    allocation = payload.get("stat_allocation")
    if isinstance(allocation, dict):
        validation = validate_stat_allocation(allocation, payload.get("total_points", _DEFAULT_TOTAL_POINTS))
        if validation["ok"]:
            player_state = allocate_starting_stats(player_state, allocation)
    return player_state
