"""Runtime ability progression and deterministic effect execution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.ability_models import (
    ALLOWED_XP_SOURCES,
    DEFAULT_SKILL_XP_PER_ABILITY_USE,
    RpgAbilityStateResult,
    RpgAbilityUseResult,
    RpgProgressionResult,
)
from app.apps.rpg.session.ability_tree import validate_ability
from app.apps.rpg.session.ability_utils import (
    _append,
    _is_plain_int,
    _norm,
    _safe_int,
    _text,
    _utc_now,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _ability_index(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(ability.get("ability_id")): _safe_dict(ability) for ability in _safe_list(tree.get("abilities"))}


def _ability_by_id(state: dict[str, Any], ability_id: str) -> dict[str, Any] | None:
    return _ability_index(_safe_dict(state.get("ability_tree"))).get(str(ability_id or ""))


def _ability_state(state: dict[str, Any]) -> dict[str, Any]:
    ability_state = _safe_dict(state.get("ability_state"))
    ability_state.setdefault("ability_points", 0)
    ability_state.setdefault("unlocked", [])
    ability_state.setdefault("ranks", {})
    ability_state.setdefault("cooldowns", {})
    ability_state.setdefault("active_effects", [])
    state["ability_state"] = ability_state
    return ability_state


def _player(state: dict[str, Any]) -> dict[str, Any]:
    player = _safe_dict(state.get("player"))
    state["player"] = player
    return player


def _player_level(state: dict[str, Any]) -> int:
    return max(1, _safe_int(_player(state).get("level"), 1))


def _rank_for_ability(ability_state: dict[str, Any], ability: dict[str, Any]) -> int:
    ability_id = str(ability.get("ability_id") or "")
    ranks = _safe_dict(ability_state.get("ranks"))
    rank = _safe_int(ranks.get(ability_id), _safe_int(ability.get("rank"), 1))
    max_rank = max(1, _safe_int(ability.get("max_rank"), 1))
    return max(1, min(max_rank, rank))


def _state_result(ok: bool, ability_state: dict[str, Any], detail: str, *, ability_id: str | None = None, error: str | None = None, slot: str | None = None) -> RpgAbilityStateResult:
    return RpgAbilityStateResult(ok=ok, ability_id=ability_id, detail=detail, error=error, slot=slot, ability_state=deepcopy(ability_state))


def unlock_ability_in_state(state: dict[str, Any], ability_id: str) -> RpgAbilityStateResult:
    ability_state = _ability_state(state)
    ability = _ability_by_id(state, ability_id)
    if not ability:
        return _state_result(False, ability_state, "Ability was not found in the session ability tree.", ability_id=ability_id, error="unknown_ability")
    ability_id = str(ability.get("ability_id"))
    unlocked = [str(value) for value in _safe_list(ability_state.get("unlocked"))]
    if ability_id in unlocked:
        return _state_result(True, ability_state, f"{ability.get('name')} is already unlocked.", ability_id=ability_id)
    required_level = _safe_int(ability.get("level_required"), 1)
    if _player_level(state) < required_level:
        return _state_result(False, ability_state, f"{ability.get('name')} requires level {required_level}.", ability_id=ability_id, error="level_required")
    missing = [str(value) for value in _safe_list(ability.get("prerequisites")) if str(value) not in unlocked]
    if missing:
        return _state_result(False, ability_state, f"{ability.get('name')} requires {', '.join(missing)} first.", ability_id=ability_id, error="missing_prerequisites")
    points = _safe_int(ability_state.get("ability_points"))
    if points <= 0:
        return _state_result(False, ability_state, "No ability points are available.", ability_id=ability_id, error="insufficient_ability_points")
    unlocked.append(ability_id)
    ability_state["unlocked"] = unlocked
    ability_state["ability_points"] = points - 1
    ranks = _safe_dict(ability_state.get("ranks"))
    ranks.setdefault(ability_id, 1)
    ability_state["ranks"] = ranks
    return _state_result(True, ability_state, f"Unlocked {ability.get('name')}.", ability_id=ability_id)


def upgrade_ability_rank_in_state(state: dict[str, Any], ability_id: str) -> RpgAbilityStateResult:
    ability_state = _ability_state(state)
    ability = _ability_by_id(state, ability_id)
    if not ability:
        return _state_result(False, ability_state, "Ability was not found in the session ability tree.", ability_id=ability_id, error="unknown_ability")
    ability_id = str(ability.get("ability_id"))
    unlocked = {str(value) for value in _safe_list(ability_state.get("unlocked"))}
    if ability_id not in unlocked:
        return _state_result(False, ability_state, f"{ability.get('name')} is not unlocked yet.", ability_id=ability_id, error="ability_locked")
    ranks = _safe_dict(ability_state.get("ranks"))
    current_rank = _safe_int(ranks.get(ability_id), 1)
    max_rank = max(1, _safe_int(ability.get("max_rank"), 1))
    if current_rank >= max_rank:
        return _state_result(False, ability_state, f"{ability.get('name')} is already at max rank.", ability_id=ability_id, error="max_rank")
    points = _safe_int(ability_state.get("ability_points"))
    if points <= 0:
        return _state_result(False, ability_state, "No ability points are available.", ability_id=ability_id, error="insufficient_ability_points")
    ranks[ability_id] = current_rank + 1
    ability_state["ranks"] = ranks
    ability_state["ability_points"] = points - 1
    return _state_result(True, ability_state, f"Upgraded {ability.get('name')} to rank {ranks[ability_id]}.", ability_id=ability_id)


def assign_ability_to_hotbar(state: dict[str, Any], ability_id: str, slot: str | int) -> RpgAbilityStateResult:
    ability_state = _ability_state(state)
    ability = _ability_by_id(state, ability_id)
    slot_key = str(slot)
    if not slot_key.isdigit() or int(slot_key) < 1 or int(slot_key) > 10:
        return _state_result(False, ability_state, "Hotbar slot must be between 1 and 10.", ability_id=ability_id, error="invalid_hotbar_slot", slot=slot_key)
    if not ability:
        return _state_result(False, ability_state, "Ability was not found in the session ability tree.", ability_id=ability_id, error="unknown_ability", slot=slot_key)
    ability_id = str(ability.get("ability_id"))
    if ability.get("kind") != "active":
        return _state_result(False, ability_state, f"{ability.get('name')} is not an active ability.", ability_id=ability_id, error="hotbar_requires_active_ability", slot=slot_key)
    if ability_id not in {str(value) for value in _safe_list(ability_state.get("unlocked"))}:
        return _state_result(False, ability_state, f"{ability.get('name')} is not unlocked yet.", ability_id=ability_id, error="ability_locked", slot=slot_key)
    hotbar = _safe_dict(state.get("hotbar")) or _safe_dict(ability_state.get("hotbar"))
    hotbar[slot_key] = ability_id
    state["hotbar"] = hotbar
    ability_state["hotbar"] = hotbar
    return _state_result(True, ability_state, f"Assigned {ability.get('name')} to hotbar slot {slot_key}.", ability_id=ability_id, slot=slot_key)


def remove_hotbar_slot(state: dict[str, Any], slot: str | int) -> RpgAbilityStateResult:
    ability_state = _ability_state(state)
    slot_key = str(slot)
    hotbar = _safe_dict(state.get("hotbar")) or _safe_dict(ability_state.get("hotbar"))
    removed = hotbar.pop(slot_key, None)
    state["hotbar"] = hotbar
    ability_state["hotbar"] = hotbar
    return _state_result(True, ability_state, f"Removed hotbar slot {slot_key}.", ability_id=str(removed) if removed else None, slot=slot_key)


def _find_ability(state: dict[str, Any], *, ability_name: str | None = None, hotbar_slot: str | int | None = None) -> dict[str, Any] | None:
    index = _ability_index(_safe_dict(state.get("ability_tree")))
    ability_state = _ability_state(state)
    hotbar = _safe_dict(state.get("hotbar")) or _safe_dict(ability_state.get("hotbar"))
    if hotbar_slot is not None and str(hotbar_slot) in hotbar:
        return index.get(str(hotbar[str(hotbar_slot)]))
    wanted = _norm(ability_name)
    if not wanted:
        return None
    for ability in index.values():
        if _norm(ability.get("name")) == wanted or _norm(ability.get("ability_id")) == wanted:
            return ability
    return next((ability for ability in index.values() if wanted in _norm(ability.get("name")) or wanted in _norm(ability.get("ability_id"))), None)


def _resource_metric(player: dict[str, Any], resource: str) -> dict[str, Any]:
    resources = _safe_dict(player.get("resources"))
    player["resources"] = resources
    metric = _safe_dict(resources.get(resource))
    metric.setdefault("current", 0)
    metric.setdefault("max", metric.get("current", 0))
    resources[resource] = metric
    return metric


def _xp_metric(player: dict[str, Any]) -> dict[str, int]:
    xp = _safe_dict(player.get("xp"))
    current = max(0, _safe_int(xp.get("current")))
    maximum = max(1, _safe_int(xp.get("max"), 100))
    player["xp"] = {"current": current, "max": maximum}
    return player["xp"]


def _next_level_xp_max(current_max: int) -> int:
    return max(100, int(current_max) + 100)


def grant_player_xp(state: dict[str, Any], amount: int, *, source: str) -> RpgProgressionResult:
    source_key = _norm(source)
    if source_key not in ALLOWED_XP_SOURCES:
        return RpgProgressionResult(ok=False, detail=f"XP source {source} is not supported for level progression.", error="unsupported_xp_source", source=source_key)
    xp_gained = max(0, int(amount or 0))
    if xp_gained <= 0:
        return RpgProgressionResult(ok=False, detail="XP amount must be positive.", error="invalid_xp_amount", source=source_key)
    player = _player(state)
    xp = _xp_metric(player)
    level = max(1, _safe_int(player.get("level"), 1))
    xp["current"] += xp_gained
    level_ups: list[dict[str, int]] = []
    while xp["current"] >= xp["max"]:
        before_level = level
        xp["current"] -= xp["max"]
        level += 1
        xp["max"] = _next_level_xp_max(xp["max"])
        level_ups.append({"from": before_level, "to": level})
    player["level"] = level
    ability_points_granted = len(level_ups)
    if ability_points_granted:
        ability_state = _ability_state(state)
        ability_state["ability_points"] = _safe_int(ability_state.get("ability_points")) + ability_points_granted
    return RpgProgressionResult(ok=True, detail=f"Granted {xp_gained} XP from {source_key}.", xp_gained=xp_gained, source=source_key, level_ups=level_ups, ability_points_granted=ability_points_granted)


def _skill_progression(state: dict[str, Any]) -> dict[str, Any]:
    progression = _safe_dict(state.get("skill_progression"))
    state["skill_progression"] = progression
    return progression


def grant_skill_xp(state: dict[str, Any], skill_id: str, amount: int, *, source: str = "ability_use") -> RpgProgressionResult:
    skill_key = _norm(skill_id)
    xp_gained = max(0, int(amount or 0))
    if not skill_key:
        return RpgProgressionResult(ok=False, detail="Skill id is required.", error="missing_skill_id")
    if xp_gained <= 0:
        return RpgProgressionResult(ok=False, detail="Skill XP amount must be positive.", error="invalid_skill_xp_amount", source=source)
    progression = _skill_progression(state)
    entry = _safe_dict(progression.get(skill_key))
    rank = max(1, _safe_int(entry.get("rank"), 1))
    xp = max(0, _safe_int(entry.get("xp"))) + xp_gained
    level_ups: list[dict[str, int]] = []
    threshold = max(25, rank * 100)
    while xp >= threshold:
        xp -= threshold
        before_rank = rank
        rank += 1
        level_ups.append({"from": before_rank, "to": rank})
        threshold = max(25, rank * 100)
    entry.update({"xp": xp, "rank": rank, "last_source": source})
    progression[skill_key] = entry
    return RpgProgressionResult(ok=True, detail=f"Granted {xp_gained} skill XP to {skill_key}.", xp_gained=0, source=source, skill_awards={skill_key: {"xp_gained": xp_gained, "xp": xp, "rank": rank}}, skill_level_ups=[{"skill": skill_key, **level_up} for level_up in level_ups])


def skill_modifier_for_check(state: dict[str, Any], check: str | None) -> int:
    skill_key = _norm(check)
    if not skill_key:
        return 0
    entry = _safe_dict(_skill_progression(state).get(skill_key))
    return max(0, _safe_int(entry.get("rank"), 1) - 1)


def _target_unavailable(op_name: str, target_name: str, detail: str = "") -> dict[str, Any]:
    result = {"applied": False, "error": "target_unavailable", "op": op_name, "target": target_name}
    if detail:
        result["detail"] = detail
    return result


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _record_effect_trace(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], result: dict[str, Any]) -> None:
    trace = {"ability_id": ability.get("ability_id"), "ability_name": ability.get("name"), "dimension": result.get("dimension") or op.get("dimension"), "op": result.get("op") or op.get("op"), "target": result.get("target") or op.get("target"), "applied": result.get("applied") is not False, "error": result.get("error"), "created_at": _utc_now()}
    _append(_mechanics(state), "ability_effect_trace", trace, limit=80)


def _append_player_visible_ability_event(state: dict[str, Any], ability: dict[str, Any], effects: list[dict[str, Any]]) -> None:
    applied = [effect for effect in effects if effect.get("applied") is not False]
    if not applied:
        return
    turn = _safe_int(state.get("current_turn") or state.get("turn_count"), 0)
    event = {"turn": turn, "time": _safe_dict(state.get("world")).get("time") or f"Turn {turn}", "title": f"Ability effect: {ability.get('name')}", "actor": "Player", "detail": f"{ability.get('name')} changed {', '.join(str(value) for value in ability.get('dimensions', []))}.", "kind": "ability_effect", "effects": effects, "timestamp": _utc_now()}
    state["timeline"] = [event, *_safe_list(state.get("timeline"))][:50]
    journal = _safe_dict(state.get("journal"))
    journal["entries"] = [event, *_safe_list(journal.get("entries"))][:50]
    state["journal"] = journal


def _status_bucket(state: dict[str, Any], target_name: str) -> tuple[dict[str, Any], str]:
    if target_name in {"", "self", "player", "the current situation"}:
        return _player(state), "statuses"
    encounter = _safe_dict(state.get("encounter"))
    state["encounter"] = encounter
    return encounter, "target_statuses"


def _resource_delta(state: dict[str, Any], resource: str, amount: int, *, target: str | None = None) -> dict[str, Any]:
    if target and target not in {"self", "player", "party", "the current situation"}:
        encounter = _safe_dict(state.get("encounter"))
        _append(encounter, "target_effects", {"target": target, "resource": resource, "amount": amount, "created_at": _utc_now()})
        state["encounter"] = encounter
        return {"target": target, "resource": resource, "amount": amount, "applied": True, "mode": "target_trace"}
    metric = _resource_metric(_player(state), resource)
    before = int(metric.get("current") or 0)
    maximum = int(metric.get("max") or before)
    metric["current"] = max(0, min(maximum, before + int(amount)))
    return {"target": "self", "resource": resource, "before": before, "after": metric["current"], "max": maximum, "applied": True}


def _apply_status(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target_name: str) -> dict[str, Any]:
    status = _text(op.get("status") or op.get("tag"), "status")
    target_state, key = _status_bucket(state, target_name)
    _append(target_state, key, {"status": status, "target": target_name, "source": ability.get("name"), "duration_turns": op.get("duration_turns"), "created_at": _utc_now()})
    return {"status": status, "target": target_name, "applied": True}


def _clear_status(state: dict[str, Any], op: dict[str, Any], target_name: str) -> dict[str, Any]:
    status = _text(op.get("status") or op.get("tag"), "status")
    target_state, key = _status_bucket(state, target_name)
    before = _safe_list(target_state.get(key))
    target_state[key] = [item for item in before if _safe_dict(item).get("status") != status]
    return {"status": status, "target": target_name, "removed": len(before) - len(target_state[key]), "applied": True}


def _ranked_amount(value: Any, rank: int) -> Any:
    if not _is_plain_int(value) or rank <= 1:
        return value
    bonus = rank - 1
    return int(value) + bonus if int(value) > 0 else int(value) - bonus if int(value) < 0 else value


def _ranked_effect_op(op: dict[str, Any], rank: int) -> dict[str, Any]:
    ranked = deepcopy(op)
    if "amount" in ranked:
        ranked["amount"] = _ranked_amount(ranked.get("amount"), rank)
    return ranked


def _modify_relationship(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target_name: str) -> dict[str, Any]:
    relationships = _safe_list(state.get("relationships"))
    relation_name = _text(op.get("relationship") or op.get("target_id") or target_name, "local_contacts")
    amount = _safe_int(op.get("amount"))
    for relation in relationships:
        if _safe_dict(relation).get("name") == relation_name:
            before = _safe_int(relation.get("score"))
            relation["score"] = before + amount
            relation.setdefault("stance", "Noted")
            break
    else:
        before = 0
        relationships.append({"name": relation_name, "stance": "Noted", "score": amount})
    state["relationships"] = relationships
    return {"relationship": relation_name, "before": before, "after": before + amount, "source": ability.get("name"), "applied": True}


def _modify_reputation(state: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    world = _safe_dict(state.get("world"))
    reputation = _safe_dict(world.get("reputation"))
    before = _safe_int(reputation.get("score"))
    reputation["score"] = before + _safe_int(op.get("amount"))
    reputation.setdefault("label", "Unknown")
    world["reputation"] = reputation
    state["world"] = world
    return {"before": before, "after": reputation["score"], "applied": True}


def _modify_faction_alert(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target_name: str) -> dict[str, Any]:
    faction_id = _text(op.get("faction_id") or op.get("target_id") or target_name)
    if not faction_id or faction_id == "the current situation":
        return _target_unavailable("modify_faction_alert", target_name, "missing faction_id")
    faction_state = _safe_dict(state.get("faction_state"))
    factions = _safe_dict(faction_state.get("factions"))
    faction = _safe_dict(factions.get(faction_id))
    before = _safe_int(faction.get("alert"))
    faction.update({"faction_id": faction_id, "alert": before + _safe_int(op.get("amount")), "updated_by": ability.get("name")})
    factions[faction_id] = faction
    faction_state["factions"] = factions
    state["faction_state"] = faction_state
    return {"target": faction_id, "faction_id": faction_id, "before": before, "after": faction["alert"], "applied": True}


def _modify_price_modifier(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target_name: str) -> dict[str, Any]:
    modifier_id = _text(op.get("tag") or op.get("target_id") or target_name, "general")
    economy = _safe_dict(state.get("economy"))
    modifiers = _safe_dict(economy.get("price_modifiers"))
    modifier = _safe_dict(modifiers.get(modifier_id))
    before = _safe_int(modifier.get("amount"))
    modifier.update({"amount": before + _safe_int(op.get("amount")), "source": ability.get("name"), "updated_at": _utc_now()})
    modifiers[modifier_id] = modifier
    economy["price_modifiers"] = modifiers
    state["economy"] = economy
    return {"target": modifier_id, "modifier_id": modifier_id, "before": before, "after": modifier["amount"], "applied": True}


def _apply_scene_status(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    scene_state = _safe_dict(state.get("scene_state"))
    status = _text(op.get("status") or op.get("tag"), "changed")
    _append(scene_state, "statuses", {"status": status, "source": ability.get("name"), "duration_turns": op.get("duration_turns"), "created_at": _utc_now()})
    state["scene_state"] = scene_state
    return {"status": status, "applied": True}


def _create_hazard(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    scene_state = _safe_dict(state.get("scene_state"))
    hazard = _text(op.get("hazard") or op.get("status") or op.get("tag"), "hazard")
    _append(scene_state, "hazards", {"hazard": hazard, "source": ability.get("name"), "strength": op.get("strength"), "duration_turns": op.get("duration_turns"), "created_at": _utc_now()})
    state["scene_state"] = scene_state
    return {"hazard": hazard, "applied": True}


def _clear_hazard(state: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    scene_state = _safe_dict(state.get("scene_state"))
    hazard = _text(op.get("hazard") or op.get("status") or op.get("tag"), "hazard")
    before = _safe_list(scene_state.get("hazards"))
    scene_state["hazards"] = [item for item in before if _safe_dict(item).get("hazard") != hazard]
    state["scene_state"] = scene_state
    return {"hazard": hazard, "removed": len(before) - len(scene_state["hazards"]), "applied": True}


def _change_location_state(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target_name: str) -> dict[str, Any]:
    location_id = _text(op.get("location_id") or op.get("target_id") or target_name)
    if not location_id or location_id == "the current situation":
        return _target_unavailable("change_location_state", target_name, "missing location_id")
    state_key = _text(op.get("state_key") or op.get("tag"), "state")
    locations = _safe_dict(state.get("locations"))
    location = _safe_dict(locations.get(location_id))
    location.setdefault("location_id", location_id)
    location_state = _safe_dict(location.get("state"))
    before = location_state.get(state_key)
    location_state[state_key] = op.get("state_value", True)
    location["state"] = location_state
    location["updated_by"] = ability.get("name")
    locations[location_id] = location
    state["locations"] = locations
    return {"target": location_id, "location_id": location_id, "state_key": state_key, "before": before, "after": location_state[state_key], "applied": True}


def _add_world_rumor(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    world = _safe_dict(state.get("world"))
    rumor = {"rumor_id": _text(op.get("rumor_id") or op.get("tag"), f"rumor:{len(_safe_list(world.get('rumors'))) + 1}"), "text": _text(op.get("rumor") or op.get("state_value") or op.get("tag"), "A new rumor spreads."), "source": ability.get("name"), "created_at": _utc_now()}
    _append(world, "rumors", rumor, limit=80)
    state["world"] = world
    return {"rumor_id": rumor["rumor_id"], "applied": True}


def _advance_quest_signal(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target_name: str) -> dict[str, Any]:
    quest_id = _text(op.get("quest_id") or op.get("target_id") or target_name)
    if not quest_id or quest_id == "the current situation":
        return _target_unavailable("advance_quest_signal", target_name, "missing quest_id")
    signal = _text(op.get("signal") or op.get("tag"), "ability_signal")
    record = {"quest_id": quest_id, "signal": signal, "source": ability.get("name"), "created_at": _utc_now()}
    state["quest_signals"] = [record, *_safe_list(state.get("quest_signals"))][:80]
    for quest in _safe_list(state.get("quests")):
        quest_record = _safe_dict(quest)
        if _text(quest_record.get("quest_id") or quest_record.get("id")) == quest_id:
            quest_record["signals"] = [record, *_safe_list(quest_record.get("signals"))][:20]
            break
    return {"target": quest_id, "quest_id": quest_id, "signal": signal, "applied": True}


def _complete_objective(state: dict[str, Any], ability: dict[str, Any], op: dict[str, Any], target_name: str) -> dict[str, Any]:
    quest_id = _text(op.get("quest_id") or op.get("target_id") or target_name)
    objective_id = _text(op.get("objective_id") or op.get("tag"))
    if not quest_id or quest_id == "the current situation" or not objective_id:
        return _target_unavailable("complete_objective", target_name, "missing quest_id or objective_id")
    for quest in _safe_list(state.get("quests")):
        quest_record = _safe_dict(quest)
        if _text(quest_record.get("quest_id") or quest_record.get("id")) != quest_id:
            continue
        for objective in _safe_list(quest_record.get("objectives")):
            objective_record = _safe_dict(objective)
            if _text(objective_record.get("objective_id") or objective_record.get("id")) == objective_id:
                objective_record["status"] = "completed"
                objective_record["completed"] = True
                objective_record["completed_by"] = ability.get("name")
                objective_record["completed_at"] = _utc_now()
                return {"target": quest_id, "quest_id": quest_id, "objective_id": objective_id, "applied": True}
        return _target_unavailable("complete_objective", quest_id, f"missing objective {objective_id}")
    return _target_unavailable("complete_objective", quest_id, f"missing quest {quest_id}")


def execute_effect_ops(state: dict[str, Any], ability: dict[str, Any], *, target: str | None = None) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    rank = max(1, _safe_int(ability.get("rank"), 1))
    for raw_op in _safe_list(ability.get("effect_ops")):
        op = _ranked_effect_op(_safe_dict(raw_op), rank)
        op_name = str(op.get("op") or "")
        dimension = str(op.get("dimension") or "")
        target_name = _text(op.get("target_id") or op.get("target") or target, "self")
        result: dict[str, Any] = {"dimension": dimension, "op": op_name, "target": target_name}
        if op_name == "resource_delta":
            result.update(_resource_delta(state, str(op.get("resource") or "hp"), _safe_int(op.get("amount")), target=target_name if op.get("target") == "target" else op.get("target") or target_name))
        elif op_name == "modify_next_check":
            runtime = _safe_dict(state.get("runtime"))
            check = str(op.get("check") or "")
            skill_modifier = skill_modifier_for_check(state, check)
            effect = {"source": ability.get("name"), "dimension": dimension, "check": check, "amount": _safe_int(op.get("amount")) + skill_modifier, "skill_modifier": skill_modifier, "duration_turns": op.get("duration_turns", 1), "created_at": _utc_now()}
            _append(runtime, "effects", effect)
            state["runtime"] = runtime
            result.update({"check": effect["check"], "amount": effect["amount"], "skill_modifier": skill_modifier, "duration_turns": effect["duration_turns"], "applied": True})
        elif op_name == "apply_status":
            result.update(_apply_status(state, ability, op, target_name))
        elif op_name == "clear_status":
            result.update(_clear_status(state, op, target_name))
        elif op_name == "reveal_clue":
            clue_tag = _text(op.get("clue_tag") or op.get("tag"), "clue")
            _append(state, "clues", {"source": ability.get("name"), "tag": clue_tag, "strength": op.get("strength", 1), "created_at": _utc_now()})
            result.update({"clue_tag": clue_tag, "applied": True})
        elif op_name in {"unlock_dialogue_option", "unlock_travel_option", "unlock_scene_affordance", "grant_temp_affordance"}:
            affordances = _safe_dict(state.get("narrative_affordances"))
            bucket = "dialogue" if op_name == "unlock_dialogue_option" else "travel" if op_name == "unlock_travel_option" else "scene"
            tag = _text(op.get("option_tag") or op.get("affordance") or op.get("tag"), op_name)
            _append(affordances, bucket, {"source": ability.get("name"), "tag": tag, "duration_turns": op.get("duration_turns"), "created_at": _utc_now()})
            state["narrative_affordances"] = affordances
            result.update({"bucket": bucket, "tag": tag, "applied": True})
        elif op_name == "modify_relationship":
            result.update(_modify_relationship(state, ability, op, target_name))
        elif op_name == "modify_reputation":
            result.update(_modify_reputation(state, op))
        elif op_name == "modify_faction_alert":
            result.update(_modify_faction_alert(state, ability, op, target_name))
        elif op_name == "modify_price_modifier":
            result.update(_modify_price_modifier(state, ability, op, target_name))
        elif op_name == "apply_scene_status":
            result.update(_apply_scene_status(state, ability, op))
        elif op_name == "create_hazard":
            result.update(_create_hazard(state, ability, op))
        elif op_name == "clear_hazard":
            result.update(_clear_hazard(state, op))
        elif op_name == "change_location_state":
            result.update(_change_location_state(state, ability, op, target_name))
        elif op_name == "add_world_rumor":
            result.update(_add_world_rumor(state, ability, op))
        elif op_name == "advance_quest_signal":
            result.update(_advance_quest_signal(state, ability, op, target_name))
        elif op_name == "complete_objective":
            result.update(_complete_objective(state, ability, op, target_name))
        else:
            result.update({"applied": False, "error": "unsupported_effect_op"})
            _append(_mechanics(state), "pending_dimension_effects", {**deepcopy(op), "source": ability.get("name"), "created_at": _utc_now()})
        _record_effect_trace(state, ability, op, result)
        applied.append(result)
    return applied


def _tick_cooldowns(ability_state: dict[str, Any]) -> None:
    cooldowns = _safe_dict(ability_state.get("cooldowns"))
    ability_state["cooldowns"] = {str(key): max(0, int(value or 0) - 1) for key, value in cooldowns.items() if max(0, int(value or 0) - 1) > 0}


def tick_ability_state(state: dict[str, Any]) -> RpgAbilityStateResult:
    ability_state = _ability_state(state)
    _tick_cooldowns(ability_state)
    active_effects: list[dict[str, Any]] = []
    for raw_effect in _safe_list(ability_state.get("active_effects")):
        effect = deepcopy(_safe_dict(raw_effect))
        if "remaining_turns" not in effect:
            active_effects.append(effect)
            continue
        remaining = _safe_int(effect.get("remaining_turns")) - 1
        if remaining > 0:
            effect["remaining_turns"] = remaining
            active_effects.append(effect)
    ability_state["active_effects"] = active_effects[:20]
    return _state_result(True, ability_state, "Ability cooldowns and active effects advanced by one turn.")


def apply_ability_to_state(state: dict[str, Any], *, ability_name: str | None = None, hotbar_slot: str | int | None = None, target: str | None = None) -> RpgAbilityUseResult:
    ability = _find_ability(state, ability_name=ability_name, hotbar_slot=hotbar_slot)
    if not ability:
        return RpgAbilityUseResult(ok=False, error="unknown_ability", detail="Ability was not found in the session ability tree.")
    errors = validate_ability(ability)
    if errors:
        return RpgAbilityUseResult(ok=False, ability_id=ability.get("ability_id"), name=ability.get("name"), error="invalid_ability", detail="; ".join(errors))
    ability_id = str(ability.get("ability_id"))
    ability_state = _ability_state(state)
    unlocked = set(str(value) for value in _safe_list(ability_state.get("unlocked")))
    if ability_id not in unlocked:
        return RpgAbilityUseResult(ok=False, ability_id=ability_id, name=ability.get("name"), error="ability_locked", detail=f"{ability.get('name')} is not unlocked yet.")
    cooldowns = _safe_dict(ability_state.get("cooldowns"))
    if int(cooldowns.get(ability_id) or 0) > 0:
        return RpgAbilityUseResult(ok=False, ability_id=ability_id, name=ability.get("name"), error="ability_on_cooldown", detail=f"{ability.get('name')} is on cooldown for {cooldowns[ability_id]} more turn(s).")
    player = _player(state)
    cost_parts: list[str] = []
    for resource, cost in _safe_dict(ability.get("resource_cost")).items():
        metric = _resource_metric(player, str(resource))
        current = int(metric.get("current") or 0)
        if current < int(cost):
            return RpgAbilityUseResult(ok=False, ability_id=ability_id, name=ability.get("name"), error="insufficient_resource", detail=f"{ability.get('name')} requires {cost} {resource}, but only {current}/{metric.get('max')} is available.")
    for resource, cost in _safe_dict(ability.get("resource_cost")).items():
        metric = _resource_metric(player, str(resource))
        metric["current"] = max(0, int(metric.get("current") or 0) - int(cost))
        cost_parts.append(f"{resource}: {metric['current']}/{metric.get('max')}")
    ranked_ability = deepcopy(ability)
    ranked_ability["rank"] = _rank_for_ability(ability_state, ability)
    effects = execute_effect_ops(state, ranked_ability, target=target or "the current situation")
    if effects and not any(effect.get("applied") is not False for effect in effects):
        details = "; ".join(str(effect.get("detail") or effect.get("error") or "effect failed") for effect in effects)
        return RpgAbilityUseResult(ok=False, ability_id=ability_id, name=ability.get("name"), error="effect_target_unavailable", detail=details, effects=effects)
    grant_skill_xp(state, str(ability.get("capability") or "ability"), DEFAULT_SKILL_XP_PER_ABILITY_USE, source=ability_id)
    _append_player_visible_ability_event(state, ranked_ability, effects)
    tick_ability_state(state)
    cooldown = int(ability.get("cooldown_turns") or 0)
    if cooldown > 0:
        ability_state.setdefault("cooldowns", {})[ability_id] = cooldown
    active_effects = _safe_list(ability_state.get("active_effects"))
    duration = max([_safe_int(_safe_dict(op).get("duration_turns")) for op in _safe_list(ability.get("effect_ops")) if _safe_int(_safe_dict(op).get("duration_turns")) > 0] or [0])
    active_effect = {"ability_id": ability_id, "name": ability.get("name"), "rank": ranked_ability["rank"], "dimensions": ability.get("dimensions", []), "purpose": ability.get("purpose"), "target": target or "the current situation", "created_at": _utc_now()}
    if duration > 0:
        active_effect["duration_turns"] = duration
        active_effect["remaining_turns"] = duration
    active_effects.insert(0, active_effect)
    ability_state["active_effects"] = active_effects[:20]
    dimensions = ", ".join(str(value) for value in ability.get("dimensions", []))
    cost_detail = f" Costs now {', '.join(cost_parts)}." if cost_parts else ""
    return RpgAbilityUseResult(ok=True, ability_id=ability_id, name=str(ability.get("name")), detail=f"You used {ability.get('name')} on {target or 'the current situation'}, changing {dimensions}.{cost_detail}", effects=effects)


def hotbar_preview_from_state(state: dict[str, Any]) -> list[dict[str, str]]:
    ability_index = _ability_index(_safe_dict(state.get("ability_tree")))
    ability_state = _safe_dict(state.get("ability_state"))
    hotbar = _safe_dict(state.get("hotbar")) or _safe_dict(ability_state.get("hotbar"))
    previews: list[dict[str, str]] = []
    for slot in sorted(hotbar, key=lambda value: int(value) if str(value).isdigit() else 999):
        ability = ability_index.get(str(hotbar[slot]))
        if ability:
            previews.append({"key": str(slot), "icon": str(ability.get("icon") or "✦"), "label": str(ability.get("name") or ability.get("ability_id"))})
    return previews
