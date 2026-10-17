"""Session-state bridge for deterministic RPG item combat effects."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_combat_integration import resolve_actor_item_damage
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


_TRACE_LIMIT = 50


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = state.get("mechanics")
    if not isinstance(mechanics, dict):
        mechanics = {}
        state["mechanics"] = mechanics
    return mechanics


def _prepend_limited(mapping: dict[str, Any], key: str, value: dict[str, Any]) -> None:
    traces = mapping.get(key)
    if not isinstance(traces, list):
        traces = []
    mapping[key] = [deepcopy(value), *traces[: _TRACE_LIMIT - 1]]


def _actor_key(actor: dict[str, Any], fallback: str) -> str:
    return _text(actor.get("id") or actor.get("actor_id") or actor.get("name") or actor.get("display_name"), fallback)


def _matches_actor(actor: dict[str, Any], query: str) -> bool:
    wanted = _norm(query)
    if not wanted:
        return False
    candidates = (
        actor.get("id"),
        actor.get("actor_id"),
        actor.get("name"),
        actor.get("display_name"),
    )
    return any(_norm(candidate) == wanted for candidate in candidates)


def _state_actor_slots(state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    slots: list[tuple[str, dict[str, Any]]] = []
    player = state.get("player")
    if isinstance(player, dict):
        slots.append(("player", player))
    for index, npc in enumerate(_safe_list(state.get("npcs"))):
        if isinstance(npc, dict):
            slots.append((f"npcs[{index}]", npc))
    actors = _safe_dict(state.get("actors"))
    for key, actor in actors.items():
        if isinstance(actor, dict):
            slots.append((f"actors.{key}", actor))
    participants = _safe_dict(state.get("combat")).get("participants")
    if isinstance(participants, list):
        for index, actor in enumerate(participants):
            if isinstance(actor, dict):
                slots.append((f"combat.participants[{index}]", actor))
    elif isinstance(participants, dict):
        for key, actor in participants.items():
            if isinstance(actor, dict):
                slots.append((f"combat.participants.{key}", actor))
    return slots


def find_session_combat_actor(state: dict[str, Any], query: str | None, *, default_player: bool = False) -> dict[str, Any] | None:
    """Find a mutable actor mapping in a session state."""
    state = _safe_dict(state)
    if default_player and not _text(query):
        player = state.get("player")
        return player if isinstance(player, dict) else None
    if not _text(query):
        return None
    for _slot, actor in _state_actor_slots(state):
        if _matches_actor(actor, str(query)):
            return actor
    return None


def _resource_mapping(actor: dict[str, Any]) -> dict[str, Any]:
    resources = actor.get("resources")
    if not isinstance(resources, dict):
        resources = {}
        actor["resources"] = resources
    return resources


def _apply_resource_change(actor: dict[str, Any], effect: dict[str, Any]) -> dict[str, Any]:
    resource = _text(effect.get("resource"), "health")
    after = _as_int(effect.get("after"), 0)
    before = _as_int(effect.get("before"), 0)
    resources = _resource_mapping(actor)
    current = resources.get(resource)
    if isinstance(current, dict):
        updated = dict(current)
        updated["current"] = after
        if "max" not in updated:
            updated["max"] = max(before, after)
        resources[resource] = updated
    elif current is not None:
        resources[resource] = after
    elif actor.get(resource) is not None:
        actor[resource] = after
    else:
        resources[resource] = {"current": after, "max": max(before, after)}
    return {"resource": resource, "before": before, "after": after, "delta": _as_int(effect.get("delta"), after - before)}


def apply_session_item_combat(
    state: dict[str, Any],
    *,
    attacker_id: str | None = None,
    defender_id: str | None = None,
    source_item: dict[str, Any] | None = None,
    preferred_slot: str = "Weapon",
) -> dict[str, Any]:
    """Resolve and apply deterministic item damage to mutable session state."""
    state = _safe_dict(state)
    attacker = find_session_combat_actor(state, attacker_id, default_player=True)
    defender = find_session_combat_actor(state, defender_id, default_player=False)
    if attacker is None:
        return {"ok": False, "error": "attacker_not_found", "detail": "No attacker was found for the item combat action."}
    if defender is None:
        return {"ok": False, "error": "defender_not_found", "detail": "No defender was found for the item combat action."}

    result = resolve_actor_item_damage(attacker, defender, source_item=source_item)
    applied_effects = [_apply_resource_change(defender, effect) for effect in _safe_list(result.get("effects")) if isinstance(effect, dict)]
    mechanics = _mechanics(state)
    trace = deepcopy(_safe_dict(result.get("trace")))
    trace.update(
        {
            "event": "session_item_combat_applied",
            "attacker_id": _actor_key(attacker, "attacker"),
            "defender_id": _actor_key(defender, "defender"),
            "preferred_slot": preferred_slot,
            "applied_effects": applied_effects,
            "session_turn": state.get("turn") or state.get("turn_count"),
            "mechanics_source": "engine_session_item_combat_v1",
        }
    )
    _prepend_limited(mechanics, "item_combat_traces", trace)
    _prepend_limited(mechanics, "item_traces", trace)
    detail = f"{result.get('attacker')} used {trace.get('source_item_name')} against {result.get('defender')} for {trace.get('total_resolved', 0)} damage."
    return {
        "ok": True,
        "detail": detail,
        "attacker_id": trace["attacker_id"],
        "defender_id": trace["defender_id"],
        "source_item": result.get("source_item"),
        "resolution": result.get("resolution"),
        "effects": applied_effects,
        "defeated": bool(result.get("defeated")),
        "mechanics_trace": trace,
        "mechanics_source": "engine_session_item_combat_v1",
    }
