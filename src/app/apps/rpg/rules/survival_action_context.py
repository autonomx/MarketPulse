"""Runtime survival action-context projection.

Bundle BD keeps survival pressure in the live runtime contract instead of
campaign/autoplay fragments.  The functions here are pure projection helpers:
they do not mutate needs or inventory, and they only surface bounded action
context when the canonical BA survival state says pressure is meaningful.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping

from app.apps.rpg.rules.survival import survival_pressure, survival_state_snapshot
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SURVIVAL_CONTEXT_SOURCE = "runtime_survival_action_context"
SURVIVAL_CONTEXT_VERSION = "survival_action_context_v1"
SURVIVAL_ACTION_CONTEXT_THRESHOLD = 25
SURVIVAL_ACTION_SUGGESTION_THRESHOLD = 50
SURVIVAL_ACTION_LIMIT = 3

_NEED_ORDER = ("thirst", "hunger", "fatigue")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _norm(value: Any) -> str:
    return _safe_str(value).strip().lower().replace("_", " ").replace(":", " ")


def _inventory_items(simulation_state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    player_state = _safe_dict(_safe_dict(simulation_state).get("player_state"))
    inventory = _safe_dict(player_state.get("inventory"))
    items = _safe_list(inventory.get("items"))
    if items:
        return [_safe_dict(item) for item in items if isinstance(item, dict)]
    inventory_state = _safe_dict(player_state.get("inventory_state"))
    return [_safe_dict(item) for item in _safe_list(inventory_state.get("items")) if isinstance(item, dict)]


def _item_text(item: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for key in ("item_id", "definition_id", "id", "name", "display_name", "kind"):
        value = _safe_str(_safe_dict(item).get(key))
        if value:
            parts.append(value)
    for key in ("tags", "aliases"):
        for value in _safe_list(_safe_dict(item).get(key)):
            text = _safe_str(value)
            if text:
                parts.append(text)
    return _norm(" ".join(parts))


def _has_item_matching(items: Iterable[Mapping[str, Any]], *terms: str) -> bool:
    for item in items:
        text = _item_text(item)
        if not text:
            continue
        for term in terms:
            term_text = _norm(term)
            if term_text and term_text in text:
                return True
    return False


def _has_charged_waterskin(items: Iterable[Mapping[str, Any]]) -> bool:
    for item in items:
        if not _has_item_matching([item], "waterskin", "water skin"):
            continue
        metadata = _safe_dict(_safe_dict(item).get("metadata"))
        state = _safe_dict(_safe_dict(item).get("state"))
        charges = _safe_int(metadata.get("water_charges", state.get("water_charges", 0)), 0)
        if charges > 0:
            return True
    return False


def _recommended_action_for_need(need: str, simulation_state: Mapping[str, Any]) -> Dict[str, Any]:
    items = _inventory_items(simulation_state)
    if need == "thirst":
        if _has_item_matching(items, "water"):
            action = "drink water"
            action_id = "survival:drink_water"
        elif _has_charged_waterskin(items):
            action = "drink from waterskin"
            action_id = "survival:drink_from_waterskin"
        else:
            action = "buy water"
            action_id = "survival:buy_water"
    elif need == "hunger":
        if _has_item_matching(items, "ration", "rations", "food", "provisions"):
            action = "eat rations"
            action_id = "survival:eat_rations"
        else:
            action = "buy rations"
            action_id = "survival:buy_rations"
    elif need == "fatigue":
        action = "rest"
        action_id = "survival:rest"
    else:
        action = "check condition"
        action_id = "survival:check_condition"

    return {
        "action_id": action_id,
        "action": action,
        "action_type": "survival",
        "category": "survival",
        "need": need,
        "source": SURVIVAL_CONTEXT_SOURCE,
    }


def _pressure_priority(label: str, value: int) -> int:
    base = {
        "critical": 300,
        "high": 200,
        "moderate": 100,
        "low": 0,
    }.get(label, 0)
    return base + max(0, min(100, value))


def _need_rows(simulation_state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    state = survival_state_snapshot(simulation_state)
    pressure = survival_pressure(state)
    rows: List[Dict[str, Any]] = []
    for need in _NEED_ORDER:
        value = max(0, min(100, _safe_int(state.get(need), 0)))
        label = _safe_str(pressure.get(need) or "low")
        action = _recommended_action_for_need(need, simulation_state)
        action.update(
            {
                "value": value,
                "pressure": label,
                "priority": _pressure_priority(label, value),
                "reason": f"{need}_{label}",
            }
        )
        rows.append(action)
    rows.sort(key=lambda row: (-_safe_int(row.get("priority"), 0), _safe_str(row.get("need"))))
    return rows


def build_survival_action_context(simulation_state: Mapping[str, Any]) -> Dict[str, Any]:
    """Build bounded survival pressure/action context from canonical BA state."""
    state = survival_state_snapshot(simulation_state)
    if not state.get("enabled", True):
        return {
            "format_version": SURVIVAL_CONTEXT_VERSION,
            "enabled": False,
            "survival": state,
            "survival_pressure": survival_pressure(state),
            "recommended_actions": [],
            "suggested_actions": [],
            "next_actions": [],
            "autoplay_pressure": {
                "should_respond": False,
                "pressure_score": 0,
                "top_action": "",
                "source": SURVIVAL_CONTEXT_SOURCE,
            },
            "source": SURVIVAL_CONTEXT_SOURCE,
        }

    rows = _need_rows(simulation_state)
    recommended = [row for row in rows if _safe_int(row.get("value"), 0) >= SURVIVAL_ACTION_CONTEXT_THRESHOLD]
    suggested = [row for row in rows if _safe_int(row.get("value"), 0) >= SURVIVAL_ACTION_SUGGESTION_THRESHOLD]
    recommended = recommended[:SURVIVAL_ACTION_LIMIT]
    suggested = suggested[:SURVIVAL_ACTION_LIMIT]
    top = suggested[0] if suggested else {}
    pressure_score = max([_safe_int(row.get("value"), 0) for row in rows] or [0])
    return {
        "format_version": SURVIVAL_CONTEXT_VERSION,
        "enabled": True,
        "survival": state,
        "survival_pressure": survival_pressure(state),
        "recommended_actions": deepcopy(recommended),
        "suggested_actions": deepcopy(suggested),
        "next_actions": deepcopy(suggested),
        "autoplay_pressure": {
            "should_respond": bool(suggested),
            "pressure_score": pressure_score,
            "top_action": _safe_str(top.get("action")),
            "top_need": _safe_str(top.get("need")),
            "source": SURVIVAL_CONTEXT_SOURCE,
        },
        "source": SURVIVAL_CONTEXT_SOURCE,
    }


def _merge_action_lists(existing: Any, incoming: List[Dict[str, Any]], *, limit: int = 12) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in _safe_list(existing) + _safe_list(incoming):
        row = _safe_dict(row)
        if not row:
            continue
        key = _safe_str(row.get("action_id") or row.get("action") or row.get("id")).strip().lower()
        if not key:
            key = repr(sorted(row.items()))
        if key in seen:
            continue
        seen.add(key)
        merged.append(deepcopy(row))
        if len(merged) >= limit:
            break
    return merged


def attach_survival_action_context(
    payload: MutableMapping[str, Any],
    simulation_state: Mapping[str, Any],
) -> Dict[str, Any]:
    """Attach survival action context to a result or turn-contract payload."""
    target = dict(_safe_dict(payload))
    context = build_survival_action_context(simulation_state)
    target["survival_pressure"] = deepcopy(context.get("survival_pressure"))
    target["survival_action_context"] = deepcopy(context)
    target["autoplay_survival_pressure"] = deepcopy(context.get("autoplay_pressure"))
    suggested = _safe_list(context.get("suggested_actions"))
    if suggested:
        target["suggested_actions"] = _merge_action_lists(target.get("suggested_actions"), suggested)
        target["next_actions"] = _merge_action_lists(target.get("next_actions"), suggested)
    return target
