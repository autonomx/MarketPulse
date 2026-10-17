from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.items.inventory_state import (
    find_inventory_item,
    normalize_inventory_state,
    remove_inventory_item,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_survival_consumption"

FOOD_ITEM_IDS = ("ration", "trail_ration", "bread", "dried_meat")
WATER_ITEM_IDS = ("water_skin", "water_flask", "clean_water")

DEFAULT_FOOD_RECOVERY = 35
DEFAULT_WATER_RECOVERY = 40
MAX_SURVIVAL_VALUE = 100
PRESSURE_INTERVAL_TICKS = 4
HUNGER_PRESSURE_PER_INTERVAL = 6
THIRST_PRESSURE_PER_INTERVAL = 8
FATIGUE_PRESSURE_THRESHOLD = 60
CRITICAL_THRESHOLD = 85


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clamp_survival_value(value: Any) -> int:
    return max(0, min(MAX_SURVIVAL_VALUE, _safe_int(value, 0)))


def _ensure_player_state(state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(state.get("player_state"))
    if not player_state:
        player_state = {}
        state["player_state"] = player_state
    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))
    player_state["inventory_state"] = inventory_state
    state["player_state"] = player_state
    return player_state


def normalize_survival_state(survival_state: Dict[str, Any] | None = None) -> Dict[str, Any]:
    survival_state = _safe_dict(survival_state)
    warnings = [
        _safe_str(warning)
        for warning in _safe_list(survival_state.get("warnings"))
        if _safe_str(warning)
    ][:8]
    return {
        "hunger": _clamp_survival_value(survival_state.get("hunger")),
        "thirst": _clamp_survival_value(survival_state.get("thirst")),
        "fatigue": _clamp_survival_value(survival_state.get("fatigue")),
        "last_food_tick": max(0, _safe_int(survival_state.get("last_food_tick"), 0)),
        "last_water_tick": max(0, _safe_int(survival_state.get("last_water_tick"), 0)),
        "last_pressure_tick": max(0, _safe_int(survival_state.get("last_pressure_tick"), 0)),
        "starvation_pressure": max(0, _safe_int(survival_state.get("starvation_pressure"), 0)),
        "dehydration_pressure": max(0, _safe_int(survival_state.get("dehydration_pressure"), 0)),
        "warnings": warnings,
        "source": _safe_str(survival_state.get("source") or SOURCE),
    }


def _set_survival_state(player_state: Dict[str, Any], survival_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state["survival_state"] = normalize_survival_state(survival_state)
    return player_state


def _find_first_matching_item(inventory_state: Dict[str, Any], item_ids: tuple[str, ...]) -> Dict[str, Any]:
    for item_id in item_ids:
        item = find_inventory_item(inventory_state, item_id)
        if item.get("item_id"):
            return item
    return {}


def _append_survival_log(state: Dict[str, Any], log_entry: Dict[str, Any]) -> None:
    economy_state = _safe_dict(state.get("economy_state"))
    survival_log = list(economy_state.get("survival_log") or [])
    survival_log.append(deepcopy(log_entry))
    economy_state["survival_log"] = survival_log[-50:]
    state["economy_state"] = economy_state


def consume_food(
    simulation_state: Dict[str, Any],
    *,
    item_id: str = "",
    tick: int = 0,
    recovery: int = DEFAULT_FOOD_RECOVERY,
) -> Dict[str, Any]:
    return _consume_survival_item(
        simulation_state,
        action_type="consume_food",
        item_ids=(item_id,) if item_id else FOOD_ITEM_IDS,
        survival_field="hunger",
        last_tick_field="last_food_tick",
        missing_reason="no_food_item",
        completed_reason="food_consumed",
        recovery=recovery,
        tick=tick,
    )


def consume_water(
    simulation_state: Dict[str, Any],
    *,
    item_id: str = "",
    tick: int = 0,
    recovery: int = DEFAULT_WATER_RECOVERY,
) -> Dict[str, Any]:
    return _consume_survival_item(
        simulation_state,
        action_type="consume_water",
        item_ids=(item_id,) if item_id else WATER_ITEM_IDS,
        survival_field="thirst",
        last_tick_field="last_water_tick",
        missing_reason="no_water_item",
        completed_reason="water_consumed",
        recovery=recovery,
        tick=tick,
    )


def _consume_survival_item(
    simulation_state: Dict[str, Any],
    *,
    action_type: str,
    item_ids: tuple[str, ...],
    survival_field: str,
    last_tick_field: str,
    missing_reason: str,
    completed_reason: str,
    recovery: int,
    tick: int,
) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    read_player_state = _safe_dict(state.get("player_state"))
    before_inventory_state = normalize_inventory_state(_safe_dict(read_player_state.get("inventory_state")))
    before_survival_state = normalize_survival_state(_safe_dict(read_player_state.get("survival_state")))

    item = _find_first_matching_item(before_inventory_state, item_ids)
    if not item.get("item_id"):
        return _survival_response(
            False,
            action_type,
            missing_reason,
            state,
            before_survival_state=before_survival_state,
            after_survival_state=before_survival_state,
            item_id="",
            tick=tick,
        )

    player_state = _ensure_player_state(state)
    consumed_item_id = _safe_str(item.get("item_id"))
    after_inventory_state = remove_inventory_item(before_inventory_state, consumed_item_id, 1)
    player_state["inventory_state"] = after_inventory_state

    after_survival_state = normalize_survival_state(before_survival_state)
    after_survival_state[survival_field] = _clamp_survival_value(
        _safe_int(after_survival_state.get(survival_field), 0) - max(1, _safe_int(recovery, 1))
    )
    after_survival_state[last_tick_field] = max(0, _safe_int(tick, 0))
    after_survival_state["source"] = SOURCE
    after_survival_state["warnings"] = _derive_warnings(after_survival_state)
    _set_survival_state(player_state, after_survival_state)
    state["player_state"] = player_state

    log_entry = _survival_log_entry(
        action_type=action_type,
        reason=completed_reason,
        item_id=consumed_item_id,
        tick=tick,
        before_survival_state=before_survival_state,
        after_survival_state=after_survival_state,
    )
    _append_survival_log(state, log_entry)

    return _survival_response(
        True,
        action_type,
        completed_reason,
        state,
        before_survival_state=before_survival_state,
        after_survival_state=after_survival_state,
        item_id=consumed_item_id,
        log_entry=log_entry,
        tick=tick,
    )


def _derive_warnings(survival_state: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    if _safe_int(survival_state.get("hunger"), 0) >= FATIGUE_PRESSURE_THRESHOLD:
        warnings.append("hungry")
    if _safe_int(survival_state.get("thirst"), 0) >= FATIGUE_PRESSURE_THRESHOLD:
        warnings.append("thirsty")
    if _safe_int(survival_state.get("hunger"), 0) >= CRITICAL_THRESHOLD:
        warnings.append("starvation_risk")
    if _safe_int(survival_state.get("thirst"), 0) >= CRITICAL_THRESHOLD:
        warnings.append("dehydration_risk")
    return warnings[:8]


def _survival_log_entry(
    *,
    action_type: str,
    reason: str,
    item_id: str,
    tick: int,
    before_survival_state: Dict[str, Any],
    after_survival_state: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "kind": "survival",
        "action_type": action_type,
        "reason": reason,
        "item_id": _safe_str(item_id),
        "qty": 1 if item_id else 0,
        "tick": max(0, _safe_int(tick, 0)),
        "before_survival_state": deepcopy(normalize_survival_state(before_survival_state)),
        "after_survival_state": deepcopy(normalize_survival_state(after_survival_state)),
        "source": SOURCE,
    }


def _survival_response(
    resolved: bool,
    action_type: str,
    reason: str,
    simulation_state: Dict[str, Any],
    *,
    before_survival_state: Dict[str, Any],
    after_survival_state: Dict[str, Any],
    item_id: str,
    log_entry: Dict[str, Any] | None = None,
    tick: int = 0,
) -> Dict[str, Any]:
    return {
        "resolved": resolved,
        "changed_state": bool(resolved),
        "action_type": action_type,
        "reason": reason,
        "item_id": _safe_str(item_id),
        "before_survival_state": deepcopy(normalize_survival_state(before_survival_state)),
        "after_survival_state": deepcopy(normalize_survival_state(after_survival_state)),
        "log_entry": deepcopy(_safe_dict(log_entry)),
        "simulation_state": simulation_state,
        "tick": max(0, _safe_int(tick, 0)),
        "source": SOURCE,
    }
