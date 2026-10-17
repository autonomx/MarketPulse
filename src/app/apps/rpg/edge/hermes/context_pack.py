from __future__ import annotations

from copy import deepcopy
from typing import Any

SOURCE = "hermes_rpg_context_pack"
DEFAULT_ITEM_LIMIT = 8
DEFAULT_CHAR_BUDGET = 4000


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _bounded_list(value: Any, limit: int) -> list[Any]:
    return deepcopy(_list(value)[:limit])


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        mapping = _mapping(value)
        if mapping:
            return mapping
    return {}


def _stable_player_stats(state: dict[str, Any]) -> dict[str, Any]:
    player = _first_mapping(state.get("player"), state.get("hero"), state.get("character"))
    stats = _first_mapping(player.get("stats"), state.get("stats"))
    return {key: stats[key] for key in sorted(stats)}


def _truncate_pack(pack: dict[str, Any], char_budget: int) -> dict[str, Any]:
    text = repr(pack)
    if len(text) <= char_budget:
        return pack
    trimmed = deepcopy(pack)
    for key in ("recent_events", "inventory", "known_npcs", "party", "active_quests"):
        values = _list(trimmed.get(key))
        while values and len(repr(trimmed)) > char_budget:
            values.pop()
            trimmed[key] = values
    trimmed["truncated"] = len(repr(trimmed)) > char_budget
    return trimmed


def build_hermes_rpg_context_pack(
    session: dict[str, Any] | None,
    *,
    item_limit: int = DEFAULT_ITEM_LIMIT,
    char_budget: int = DEFAULT_CHAR_BUDGET,
) -> dict[str, Any]:
    source = _mapping(session)
    state = _first_mapping(source.get("state"), source.get("simulation_state"), source.get("runtime_state"), source)
    location = _first_mapping(state.get("location"), state.get("current_location"))
    pack = {
        "ok": True,
        "source": SOURCE,
        "version": "hermes_rpg_context_pack_v1",
        "session_id": _text(source.get("session_id")) or _text(source.get("id")),
        "current_location": _text(location.get("name")) or _text(state.get("current_location")) or _text(state.get("location")),
        "player_stats": _stable_player_stats(state),
        "inventory": _bounded_list(state.get("inventory"), item_limit),
        "party": _bounded_list(state.get("party"), item_limit),
        "active_quests": _bounded_list(state.get("active_quests") or state.get("quests"), item_limit),
        "recent_events": _bounded_list(state.get("recent_events") or state.get("events"), item_limit),
        "known_npcs": _bounded_list(state.get("known_npcs") or state.get("npcs"), item_limit),
        "active_combat": _mapping(state.get("combat") or state.get("encounter")),
        "active_service": _mapping(state.get("service_state") or state.get("service")),
        "active_travel": _mapping(state.get("travel_state") or state.get("travel")),
        "item_limit": item_limit,
        "char_budget": char_budget,
        "truncated": False,
    }
    return _truncate_pack(pack, char_budget)
