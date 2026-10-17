"""Recover playable quest and action state from a published opening scenario."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _scenario_entity(session: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _dict(session.get("manifest"))
    state = _dict(session.get("state"))
    published = _dict(state.get("published_world"))
    projection = _dict(session.get("campaign_bible_projection"))
    entities = _dict(projection.get("entities"))
    scenario_id = _text(manifest.get("scenario_id"))
    scenario_slug = scenario_id.rsplit(":", 1)[-1]
    starting_location_id = _text(published.get("starting_location_id"))
    candidates: list[dict[str, Any]] = []
    for entity_id, raw in entities.items():
        entity = _dict(raw)
        if _text(entity.get("kind")) != "opening_scenario":
            continue
        if scenario_slug and _text(entity_id).rsplit(":", 1)[-1] == scenario_slug:
            return entity
        if _text(entity.get("starting_place_id")) == starting_location_id:
            candidates.append(entity)
    return candidates[0] if candidates else {}


def _opening_quest(scenario: Mapping[str, Any], scenario_id: str) -> dict[str, Any]:
    title = _text(scenario.get("name")) or "Opening Objective"
    premise = _text(
        scenario.get("premise")
        or scenario.get("short_summary")
        or scenario.get("description")
    )
    beats = [_text(item) for item in _list(scenario.get("beats")) if _text(item)]
    if not beats and premise:
        beats = [premise]
    objectives = [
        {
            "id": f"{scenario_id or 'opening'}:objective:{index + 1}",
            "objective_id": f"{scenario_id or 'opening'}:objective:{index + 1}",
            "title": beat,
            "description": beat,
            "status": "active" if index == 0 else "pending",
        }
        for index, beat in enumerate(beats)
    ]
    return {
        "id": f"quest:opening:{scenario_id.rsplit(':', 1)[-1] or 'scenario'}",
        "quest_id": f"quest:opening:{scenario_id.rsplit(':', 1)[-1] or 'scenario'}",
        "title": title,
        "name": title,
        "detail": premise,
        "description": premise,
        "status": "active",
        "next_step": beats[0] if beats else premise,
        "objectives": objectives,
        "source": "published_opening_scenario",
    }


def _opening_actions(
    scenario: Mapping[str, Any],
    *,
    location_name: str,
    entities: Mapping[str, Any],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    beats = [_text(item) for item in _list(scenario.get("beats")) if _text(item)]
    for beat in beats[:2]:
        actions.append(
            {
                "label": beat,
                "command": f"Investigate {beat.casefold()} at {location_name}",
                "kind": "objective",
            }
        )
    actor_ids = _list(
        scenario.get("initial_actor_ids") or scenario.get("initial_npc_ids")
    )
    for actor_id in actor_ids[:1]:
        actor = _dict(entities.get(_text(actor_id)))
        actor_name = _text(actor.get("name"))
        if actor_name:
            actions.append(
                {
                    "label": f"Talk to {actor_name}",
                    "command": f"Talk to {actor_name} about the current crisis",
                    "kind": "dialogue",
                }
            )
    actions.append(
        {
            "label": f"Inspect {location_name}",
            "command": f"Inspect the immediate situation at {location_name}",
            "kind": "observation",
        }
    )
    return actions[:4]


def ensure_published_opening_progress(
    session: dict[str, Any],
) -> dict[str, Any]:
    """Add missing opening progress without replacing progressed campaign state."""

    state = _dict(session.get("state"))
    published = _dict(state.get("published_world"))
    if not published:
        return session
    scenario = _scenario_entity(session)
    if not scenario:
        return session

    manifest = _dict(session.get("manifest"))
    scenario_id = _text(manifest.get("scenario_id"))
    if not _list(state.get("quests")):
        state["quests"] = [_opening_quest(scenario, scenario_id)]

    current_actions = _list(state.get("quick_actions"))
    placeholder_actions = all(
        isinstance(item, str)
        and item.startswith(
            ("Survey ", "Follow ", "Talk to someone", "Check local routes")
        )
        for item in current_actions
    )
    if not current_actions or placeholder_actions:
        projection = _dict(session.get("campaign_bible_projection"))
        entities = _dict(projection.get("entities"))
        location_name = _text(
            state.get("current_location_name")
            or state.get("current_location")
            or state.get("location")
        ) or "the current location"
        state["quick_actions"] = _opening_actions(
            scenario,
            location_name=location_name,
            entities=entities,
        )

    simulation = _dict(session.get("simulation_state"))
    quest_state = _dict(simulation.get("quest_state"))
    if not _list(quest_state.get("quests")):
        quest_state["quests"] = deepcopy(_list(state.get("quests")))
        simulation["quest_state"] = quest_state
    session["state"] = state
    session["simulation_state"] = simulation
    return session
