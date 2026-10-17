"""World-scale RPG ability effects.

N127 layer: high-level abilities can persistently affect factions, settlements,
economies, rumors, quest branches, and world events without allowing freeform AI
mechanics. AI may name and describe world-scale powers; this module owns the
validated deterministic state writes.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field

from app.apps.rpg.session.ability_system import ALLOWED_DIMENSIONS
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

WORLD_SCALE_DIMENSIONS = {"relationships", "access", "narrative", "economy", "world", "information"}
WORLD_SCALE_EFFECT_OPS = {
    "modify_faction_alert",
    "modify_faction_relationship",
    "modify_economy_price",
    "modify_economy_availability",
    "modify_settlement_state",
    "add_world_event",
    "propagate_rumor",
    "open_quest_branch",
    "record_world_opportunity",
}


class RpgWorldScaleEffectResult(BaseModel):
    ok: bool
    ability_id: str | None = None
    name: str | None = None
    detail: str
    errors: list[str] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _append(target: dict[str, Any], key: str, value: dict[str, Any], *, limit: int = 80) -> None:
    values = _safe_list(target.get(key))
    values.insert(0, value)
    target[key] = values[:limit]


def _ability_name(ability: dict[str, Any]) -> str:
    return _text(ability.get("name"), _text(ability.get("ability_id"), "World Ability"))


def _ability_id(ability: dict[str, Any]) -> str:
    return _text(ability.get("ability_id"), "world_ability")


def _target_id(op: dict[str, Any], target: str | None = None, *, fallback: str = "") -> str:
    return _text(op.get("target_id") or op.get("faction_id") or op.get("location_id") or op.get("settlement_id") or target, fallback)


def validate_world_scale_ability(ability: dict[str, Any]) -> list[str]:
    """Return validation errors for a world-scale ability payload.

    This validator intentionally focuses on persistent world dimensions and world
    operations. Flavor-only abilities are rejected here just as active abilities
    are rejected by the core ability validator.
    """
    errors: list[str] = []
    ability_id = _ability_id(ability)
    dimensions = [str(value) for value in _safe_list(ability.get("dimensions"))]
    effect_ops = [_safe_dict(value) for value in _safe_list(ability.get("effect_ops"))]
    if not _text(ability.get("ability_id")):
        errors.append("missing ability_id")
    if not dimensions:
        errors.append(f"{ability_id}: missing dimensions")
    for dimension in dimensions:
        if dimension not in ALLOWED_DIMENSIONS:
            errors.append(f"{ability_id}: unsupported dimension {dimension}")
        elif dimension not in WORLD_SCALE_DIMENSIONS:
            errors.append(f"{ability_id}: dimension {dimension} is not world-scale")
    if not effect_ops:
        errors.append(f"{ability_id}: world-scale ability has no effect_ops")
    for op in effect_ops:
        op_name = str(op.get("op") or "")
        dimension = str(op.get("dimension") or "")
        if op_name not in WORLD_SCALE_EFFECT_OPS:
            errors.append(f"{ability_id}: unsupported world-scale effect op {op_name}")
        if dimension not in ALLOWED_DIMENSIONS:
            errors.append(f"{ability_id}: effect has unsupported dimension {dimension}")
        elif dimension not in dimensions:
            errors.append(f"{ability_id}: effect dimension {dimension} missing from ability dimensions")
        if op_name in {"modify_faction_alert", "modify_faction_relationship"} and not _target_id(op):
            errors.append(f"{ability_id}: {op_name} requires a faction target")
        if op_name == "modify_settlement_state" and not _text(op.get("settlement_id") or op.get("location_id") or op.get("target_id")):
            errors.append(f"{ability_id}: modify_settlement_state requires settlement_id or location_id")
        if op_name == "open_quest_branch" and not _text(op.get("quest_id") or op.get("target_id")):
            errors.append(f"{ability_id}: open_quest_branch requires quest_id")
    return errors


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _record_trace(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], result: dict[str, Any]) -> None:
    _append(
        _mechanics(state),
        "world_effect_trace",
        {
            "ability_id": _ability_id(ability),
            "ability_name": _ability_name(ability),
            "dimension": result.get("dimension") or op.get("dimension"),
            "op": result.get("op") or op.get("op"),
            "target": result.get("target") or op.get("target_id") or op.get("target"),
            "applied": result.get("applied") is not False,
            "error": result.get("error"),
            "created_at": _utc_now(),
        },
    )


def _append_timeline_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    turn = _safe_int(state.get("current_turn") or state.get("turn_count"), 0)
    record = {"turn": turn, "timestamp": _utc_now(), **event}
    timeline = _safe_list(state.get("timeline"))
    state["timeline"] = [record, *timeline][:80]
    journal = _safe_dict(state.get("journal"))
    entries = _safe_list(journal.get("entries"))
    journal["entries"] = [record, *entries][:80]
    state["journal"] = journal


def _faction_state(state: dict[str, Any]) -> dict[str, Any]:
    faction_state = _safe_dict(state.get("faction_state"))
    faction_state.setdefault("factions", {})
    faction_state.setdefault("relations", {})
    state["faction_state"] = faction_state
    return faction_state


def _modify_faction_alert(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target: str | None) -> dict[str, Any]:
    faction_id = _target_id(op, target)
    faction_state = _faction_state(state)
    factions = _safe_dict(faction_state.get("factions"))
    faction = _safe_dict(factions.get(faction_id))
    before = _safe_int(faction.get("alert"))
    after = before + _safe_int(op.get("amount"))
    faction.update({"faction_id": faction_id, "alert": after, "updated_by": _ability_name(ability), "updated_at": _utc_now()})
    factions[faction_id] = faction
    faction_state["factions"] = factions
    return {"target": faction_id, "faction_id": faction_id, "before": before, "after": after, "applied": True}


def _modify_faction_relationship(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target: str | None) -> dict[str, Any]:
    source_faction = _target_id(op, target)
    target_faction = _text(op.get("relationship") or op.get("target_faction_id") or op.get("tag"), "local_power")
    relation_id = f"{source_faction}:{target_faction}"
    faction_state = _faction_state(state)
    relations = _safe_dict(faction_state.get("relations"))
    relation = _safe_dict(relations.get(relation_id))
    before = _safe_int(relation.get("score"))
    after = before + _safe_int(op.get("amount"))
    relation.update(
        {
            "relation_id": relation_id,
            "source_faction": source_faction,
            "target_faction": target_faction,
            "score": after,
            "updated_by": _ability_name(ability),
            "updated_at": _utc_now(),
        }
    )
    relations[relation_id] = relation
    faction_state["relations"] = relations
    return {"target": relation_id, "relation_id": relation_id, "before": before, "after": after, "applied": True}


def _economy(state: dict[str, Any]) -> dict[str, Any]:
    economy = _safe_dict(state.get("economy"))
    state["economy"] = economy
    return economy


def _modify_economy_price(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target: str | None) -> dict[str, Any]:
    modifier_id = _text(op.get("tag") or op.get("target_id") or target, "general")
    economy = _economy(state)
    modifiers = _safe_dict(economy.get("price_modifiers"))
    modifier = _safe_dict(modifiers.get(modifier_id))
    before = _safe_int(modifier.get("amount"))
    after = before + _safe_int(op.get("amount"))
    modifier.update({"amount": after, "source": _ability_name(ability), "updated_at": _utc_now()})
    modifiers[modifier_id] = modifier
    economy["price_modifiers"] = modifiers
    return {"target": modifier_id, "modifier_id": modifier_id, "before": before, "after": after, "applied": True}


def _modify_economy_availability(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target: str | None) -> dict[str, Any]:
    modifier_id = _text(op.get("tag") or op.get("target_id") or target, "general")
    economy = _economy(state)
    modifiers = _safe_dict(economy.get("availability_modifiers"))
    modifier = _safe_dict(modifiers.get(modifier_id))
    before = _safe_int(modifier.get("amount"))
    after = before + _safe_int(op.get("amount"))
    modifier.update({"amount": after, "source": _ability_name(ability), "updated_at": _utc_now()})
    modifiers[modifier_id] = modifier
    economy["availability_modifiers"] = modifiers
    return {"target": modifier_id, "modifier_id": modifier_id, "before": before, "after": after, "applied": True}


def _modify_settlement_state(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target: str | None) -> dict[str, Any]:
    settlement_id = _text(op.get("settlement_id") or op.get("location_id") or op.get("target_id") or target)
    settlements = _safe_dict(state.get("settlements"))
    settlement = _safe_dict(settlements.get(settlement_id))
    state_key = _text(op.get("state_key") or op.get("tag"), "state")
    settlement_state = _safe_dict(settlement.get("state"))
    before = settlement_state.get(state_key)
    settlement_state[state_key] = op.get("state_value", True)
    settlement.update({"settlement_id": settlement_id, "state": settlement_state, "updated_by": _ability_name(ability), "updated_at": _utc_now()})
    settlements[settlement_id] = settlement
    state["settlements"] = settlements
    return {"target": settlement_id, "settlement_id": settlement_id, "state_key": state_key, "before": before, "after": settlement_state[state_key], "applied": True}


def _world(state: dict[str, Any]) -> dict[str, Any]:
    world = _safe_dict(state.get("world"))
    state["world"] = world
    return world


def _add_world_event(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    world = _world(state)
    event_id = _text(op.get("event_id") or op.get("tag"), f"world_event:{len(_safe_list(world.get('events'))) + 1}")
    event = {
        "event_id": event_id,
        "event_type": _text(op.get("event_type") or op.get("state_key"), "ability_world_event"),
        "title": _text(op.get("title") or op.get("rumor") or op.get("state_value"), f"World effect: {_ability_name(ability)}"),
        "source": _ability_name(ability),
        "faction_id": op.get("faction_id"),
        "location_id": op.get("location_id"),
        "strength": op.get("strength"),
        "created_at": _utc_now(),
    }
    _append(world, "events", event, limit=100)
    _append_timeline_event(
        state,
        {
            "title": event["title"],
            "actor": "World",
            "detail": f"{_ability_name(ability)} changed world state.",
            "kind": "world_effect",
            "event_id": event_id,
        },
    )
    return {"target": event_id, "event_id": event_id, "applied": True}


def _propagate_rumor(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    world = _world(state)
    rumor_id = _text(op.get("rumor_id") or op.get("tag"), f"rumor:{len(_safe_list(world.get('rumors'))) + 1}")
    rumor = {
        "rumor_id": rumor_id,
        "text": _text(op.get("rumor") or op.get("state_value") or op.get("tag"), "A new rumor spreads."),
        "source": _ability_name(ability),
        "faction_id": op.get("faction_id"),
        "location_id": op.get("location_id"),
        "created_at": _utc_now(),
    }
    _append(world, "rumors", rumor, limit=100)
    settlement_id = _text(op.get("settlement_id") or op.get("location_id"))
    if settlement_id:
        settlements = _safe_dict(state.get("settlements"))
        settlement = _safe_dict(settlements.get(settlement_id))
        _append(settlement, "rumors", rumor, limit=40)
        settlement.setdefault("settlement_id", settlement_id)
        settlements[settlement_id] = settlement
        state["settlements"] = settlements
    return {"target": rumor_id, "rumor_id": rumor_id, "applied": True}


def _open_quest_branch(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target: str | None) -> dict[str, Any]:
    quest_id = _text(op.get("quest_id") or op.get("target_id") or target)
    branch_id = _text(op.get("branch_id") or op.get("tag"), "ability_branch")
    branch = {
        "quest_id": quest_id,
        "branch_id": branch_id,
        "source": _ability_name(ability),
        "label": _text(op.get("state_value"), branch_id.replace("_", " ").title()),
        "created_at": _utc_now(),
    }
    branches = _safe_list(state.get("quest_branches"))
    state["quest_branches"] = [branch, *branches][:80]
    for quest in _safe_list(state.get("quests")):
        quest_record = _safe_dict(quest)
        if _text(quest_record.get("quest_id") or quest_record.get("id")) == quest_id:
            quest_branches = _safe_list(quest_record.get("branches"))
            quest_record["branches"] = [branch, *quest_branches][:20]
            break
    return {"target": quest_id, "quest_id": quest_id, "branch_id": branch_id, "applied": True}


def _record_world_opportunity(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    affordances = _safe_dict(state.get("narrative_affordances"))
    tag = _text(op.get("affordance") or op.get("option_tag") or op.get("tag"), "world_opportunity")
    _append(affordances, "world", {"tag": tag, "source": _ability_name(ability), "duration_turns": op.get("duration_turns"), "created_at": _utc_now()})
    state["narrative_affordances"] = affordances
    return {"target": tag, "tag": tag, "applied": True}


def apply_world_scale_effect_ops(state: dict[str, Any], ability: dict[str, Any], *, target: str | None = None) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for raw_op in _safe_list(ability.get("effect_ops")):
        op = deepcopy(_safe_dict(raw_op))
        op_name = str(op.get("op") or "")
        result: dict[str, Any] = {"dimension": op.get("dimension"), "op": op_name, "target": op.get("target_id") or op.get("target") or target}
        if op_name == "modify_faction_alert":
            result.update(_modify_faction_alert(state, ability, op, target))
        elif op_name == "modify_faction_relationship":
            result.update(_modify_faction_relationship(state, ability, op, target))
        elif op_name == "modify_economy_price":
            result.update(_modify_economy_price(state, ability, op, target))
        elif op_name == "modify_economy_availability":
            result.update(_modify_economy_availability(state, ability, op, target))
        elif op_name == "modify_settlement_state":
            result.update(_modify_settlement_state(state, ability, op, target))
        elif op_name == "add_world_event":
            result.update(_add_world_event(state, ability, op))
        elif op_name == "propagate_rumor":
            result.update(_propagate_rumor(state, ability, op))
        elif op_name == "open_quest_branch":
            result.update(_open_quest_branch(state, ability, op, target))
        elif op_name == "record_world_opportunity":
            result.update(_record_world_opportunity(state, ability, op))
        else:
            result.update({"applied": False, "error": "unsupported_world_scale_effect_op"})
        _record_trace(state, ability, op, result)
        effects.append(result)
    return effects


def apply_world_scale_ability_to_state(state: dict[str, Any], ability: dict[str, Any], *, target: str | None = None) -> RpgWorldScaleEffectResult:
    errors = validate_world_scale_ability(ability)
    if errors:
        return RpgWorldScaleEffectResult(ok=False, ability_id=_ability_id(ability), name=_ability_name(ability), detail="World-scale ability failed validation.", errors=errors)
    effects = apply_world_scale_effect_ops(state, ability, target=target)
    applied = [effect for effect in effects if effect.get("applied") is not False]
    if not applied:
        return RpgWorldScaleEffectResult(ok=False, ability_id=_ability_id(ability), name=_ability_name(ability), detail="No world-scale effects were applied.", effects=effects, errors=[str(effect.get("error") or "effect_failed") for effect in effects])
    return RpgWorldScaleEffectResult(ok=True, ability_id=_ability_id(ability), name=_ability_name(ability), detail=f"Applied {_ability_name(ability)} to persistent world state.", effects=effects)


def build_world_scale_ability_templates() -> list[dict[str, Any]]:
    """Return deterministic examples for high-level world/economy/faction powers."""
    return [
        {
            "ability_id": "influence_broker_truce",
            "kind": "active",
            "name": "Broker Truce",
            "description": "Use leverage to reduce faction hostility and create a diplomatic opening.",
            "capability": "influence",
            "power_source": "social_power",
            "purpose": "world_influence",
            "dimensions": ["relationships", "world", "narrative"],
            "effect_ops": [
                {"dimension": "relationships", "op": "modify_faction_relationship", "target_id": "town_guard", "relationship": "road_gang", "amount": 2},
                {"dimension": "world", "op": "add_world_event", "tag": "truce_brokered", "event_type": "faction_diplomacy", "state_value": "A fragile truce has been brokered."},
                {"dimension": "narrative", "op": "record_world_opportunity", "tag": "negotiate_truce_terms"},
            ],
        },
        {
            "ability_id": "technical_sabotage_supply_line",
            "kind": "active",
            "name": "Sabotage Supply Line",
            "description": "Disrupt supplies, changing prices, availability, and faction alert.",
            "capability": "technical",
            "power_source": "technology",
            "purpose": "economic_advantage",
            "dimensions": ["economy", "world", "relationships"],
            "effect_ops": [
                {"dimension": "economy", "op": "modify_economy_availability", "tag": "black_market_parts", "amount": -2},
                {"dimension": "economy", "op": "modify_economy_price", "tag": "security_gear", "amount": 1},
                {"dimension": "relationships", "op": "modify_faction_alert", "target_id": "corporate_security", "amount": 2},
                {"dimension": "world", "op": "propagate_rumor", "tag": "supply_line_hit", "rumor": "Someone hit a protected supply line."},
            ],
        },
        {
            "ability_id": "survival_found_safehouse",
            "kind": "active",
            "name": "Found Safehouse",
            "description": "Establish a persistent refuge that changes settlement access.",
            "capability": "survival",
            "power_source": "mundane",
            "purpose": "survival",
            "dimensions": ["access", "world", "economy"],
            "effect_ops": [
                {"dimension": "access", "op": "modify_settlement_state", "settlement_id": "current_settlement", "state_key": "safehouse", "state_value": True},
                {"dimension": "economy", "op": "modify_economy_availability", "tag": "rest_supplies", "amount": 1},
                {"dimension": "world", "op": "add_world_event", "tag": "safehouse_founded", "event_type": "settlement_change", "state_value": "A safehouse is now available."},
            ],
        },
    ]
