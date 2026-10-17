"""Consume campaign-local compiled mechanics in deterministic combat."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from app.apps.rpg.rules.combat.conditions import (
    add_status_effect_to_participant,
    build_condition_effect,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def campaign_mechanics_catalog(simulation_state: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(simulation_state.get("campaign_mechanics"))


def campaign_creature_definition(
    simulation_state: Mapping[str, Any],
    creature_ref: str,
) -> dict[str, Any] | None:
    creatures = _mapping(
        campaign_mechanics_catalog(simulation_state).get("creatures")
    )
    wanted = _text(creature_ref)
    wanted_slug = _slug(wanted.removeprefix("creature:"))
    for definition_id, raw in creatures.items():
        definition = _mapping(raw)
        names = {
            _text(definition_id).casefold(),
            _text(definition.get("definition_id")).casefold(),
            _text(definition.get("name")).casefold(),
            _slug(_text(definition.get("name"))),
        }
        if wanted.casefold() in names or wanted_slug in names:
            return definition
    return None


def instantiate_campaign_creature(
    simulation_state: Mapping[str, Any],
    creature_ref: str,
    *,
    instance_index: int = 1,
) -> dict[str, Any] | None:
    definition = campaign_creature_definition(simulation_state, creature_ref)
    if definition is None:
        return None
    definition_id = _text(definition.get("definition_id"))
    actor_id = f"enemy:{_slug(definition_id)}:{max(1, int(instance_index))}"
    hp = max(1, int(definition.get("hp") or 1))
    return {
        "actor_id": actor_id,
        "enemy_id": actor_id,
        "definition_id": definition_id,
        "definition_revision": int(definition.get("definition_revision") or 1),
        "name": _text(definition.get("name")) or definition_id,
        "side": "enemy",
        "level": int(definition.get("level") or 1),
        "hp": hp,
        "max_hp": hp,
        "defense": int(definition.get("defense") or 10),
        "armor": int(definition.get("armor") or 0),
        "damage_min": int(definition.get("damage_min") or 1),
        "damage_max": int(definition.get("damage_max") or 3),
        "accuracy_bonus": int(definition.get("accuracy_bonus") or 0),
        "initiative_bonus": int(definition.get("initiative_bonus") or 0),
        "morale_threshold": int(definition.get("morale_threshold") or 35),
        "tags": list(definition.get("tags") or ()),
        "loot_table_id": _text(definition.get("loot_table_id")),
        "xp_value": int(definition.get("xp_value") or 0),
        "budget_cost": int(definition.get("budget_cost") or 1),
        "condition_immunities": list(
            definition.get("condition_immunities") or ()
        ),
        "vulnerabilities": deepcopy(list(definition.get("vulnerabilities") or ())),
        "status": "active",
        "status_effects": [],
        "mechanics_source": "campaign_mechanics_catalog",
    }


def _matching_participant_id(
    participants: Mapping[str, Any],
    target_ref: str,
) -> str:
    wanted = _text(target_ref).casefold()
    wanted_slug = _slug(wanted)
    for actor_id, raw in participants.items():
        row = _mapping(raw)
        names = {
            _text(actor_id).casefold(),
            _text(row.get("name")).casefold(),
            _slug(_text(row.get("name"))),
            _text(row.get("definition_id")).casefold(),
        }
        if wanted in names or wanted_slug in names:
            return _text(actor_id)
    return ""


def campaign_vulnerability_trigger(
    simulation_state: Mapping[str, Any],
    *,
    target_ref: str,
    trigger_ref: str,
) -> dict[str, Any] | None:
    """Resolve a trigger without changing encounter state."""

    combat_state = _mapping(simulation_state.get("combat_state"))
    if combat_state.get("active") is not True:
        return None
    participants = _mapping(combat_state.get("participants"))
    target_id = _matching_participant_id(participants, target_ref)
    target = _mapping(participants.get(target_id))
    if not target:
        return None

    trigger = _text(trigger_ref).casefold()
    trigger_tokens = set(re.findall(r"[a-z0-9]+", trigger))
    for raw in target.get("vulnerabilities") or ():
        rule = _mapping(raw)
        aliases = [
            _text(rule.get("trigger_tag")),
            *[_text(value) for value in rule.get("aliases") or ()],
        ]
        if any(
            alias
            and (
                alias.casefold() in trigger
                or set(re.findall(r"[a-z0-9]+", alias.casefold())).issubset(
                    trigger_tokens
                )
            )
            for alias in aliases
        ):
            return {
                "target_id": target_id,
                "target": target,
                "rule": rule,
            }
    return None


def apply_campaign_vulnerability_trigger(
    simulation_state: dict[str, Any],
    *,
    target_ref: str,
    trigger_ref: str,
    actor_id: str = "player",
) -> dict[str, Any]:
    """Apply one validated weakness trigger to an active copied encounter."""

    combat_state = _mapping(simulation_state.get("combat_state"))
    if combat_state.get("active") is not True:
        return {"applied": False, "reason": "combat_not_active"}
    resolved = campaign_vulnerability_trigger(
        simulation_state,
        target_ref=target_ref,
        trigger_ref=trigger_ref,
    )
    if resolved is None:
        return {"applied": False, "reason": "no_matching_vulnerability"}
    participants = _mapping(combat_state.get("participants"))
    target_id = _text(resolved.get("target_id"))
    target = _mapping(resolved.get("target"))
    matched = _mapping(resolved.get("rule"))

    condition = _text(matched.get("condition")).casefold()
    immunities = {
        _text(value).casefold()
        for value in target.get("condition_immunities") or ()
    }
    if condition in immunities:
        return {
            "applied": False,
            "reason": "condition_immune",
            "target_id": target_id,
            "condition": condition,
        }
    effect = build_condition_effect(
        kind=condition,
        source_actor_id=actor_id,
        target_actor_id=target_id,
        duration_turns=int(matched.get("duration_turns") or 1),
        magnitude=int(matched.get("magnitude") or 1),
        source="campaign_creature_vulnerability",
    )
    target, condition_result = add_status_effect_to_participant(target, effect)
    participants[target_id] = target
    combat_state["participants"] = participants
    combat_state["last_vulnerability_result"] = {
        **condition_result,
        "target_id": target_id,
        "trigger": trigger_ref,
        "rule": deepcopy(matched),
        "definition_id": _text(target.get("definition_id")),
        "definition_revision": int(target.get("definition_revision") or 1),
    }
    simulation_state["combat_state"] = combat_state
    return {
        "applied": condition_result.get("applied") is True,
        "reason": _text(condition_result.get("reason")),
        "target_id": target_id,
        "trigger": trigger_ref,
        "condition": condition,
        "rule": deepcopy(matched),
        "condition_result": condition_result,
        "combat_state": deepcopy(combat_state),
        "source": "campaign_mechanics_catalog",
    }
