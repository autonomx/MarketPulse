"""Session-level recipe discovery bridge for RPG item actions.

This module keeps recipe discovery deterministic and route-free. Existing
loadout, world, or service actions can call it after they mutate inventory or
narrative affordances, and the helper mirrors newly discovered recipe traces
into mechanics so reports/autoplay can observe item-system progress.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.recipe_discovery import apply_recipe_discovery
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _turn(state: dict[str, Any]) -> int:
    return int(state.get("current_turn") or state.get("turn_count") or 0)


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _prepend_trace(mechanics: dict[str, Any], key: str, trace: dict[str, Any]) -> None:
    traces = _safe_list(mechanics.get(key))
    mechanics[key] = [trace, *traces][:50]


def apply_recipe_discovery_for_session(
    state: dict[str, Any],
    player: dict[str, Any] | None = None,
    *,
    source: str = "item_action",
    record_empty: bool = False,
) -> dict[str, Any]:
    """Apply recipe discovery and mirror meaningful traces into mechanics.

    ``apply_recipe_discovery`` remains the source of truth for
    ``state["crafting"]["known_recipes"]``. This bridge adds action-source and
    turn metadata and records discovery traces only when new recipes are found,
    unless ``record_empty`` is set for diagnostics.
    """

    state = _safe_dict(state)
    player = _safe_dict(player or state.get("player"))
    result = apply_recipe_discovery(state, player)
    discovered = deepcopy(_safe_list(result.get("discovered")))
    trace = deepcopy(_safe_dict(result.get("trace")))
    trace["event"] = "recipe_discovery_session_checked"
    trace["source"] = str(source or "item_action")
    trace["turn"] = _turn(state)
    trace["known_after"] = list(result.get("known_after") or [])

    if discovered or record_empty:
        mechanics = _mechanics(state)
        _prepend_trace(mechanics, "recipe_discovery_traces", trace)
        _prepend_trace(mechanics, "item_traces", trace)

    return {
        "ok": True,
        "source": trace["source"],
        "known_before": list(result.get("known_before") or []),
        "known_after": list(result.get("known_after") or []),
        "discovered": discovered,
        "trace": trace,
        "recorded": bool(discovered or record_empty),
    }
