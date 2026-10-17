"""What dialogue quality reads from an RPG session: NPC profiles, presence and recent turns."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


def npc_profile_by_id(session: dict[str, Any], npc_id: str) -> dict[str, Any]:
    simulation = as_dict(session.get("simulation_state"))
    runtime = as_dict(session.get("runtime_state"))
    for container in (
        as_dict(simulation.get("npc_index")),
        as_dict(simulation.get("npcs")),
        as_dict(runtime.get("npc_index")),
        as_dict(as_dict(simulation.get("social_state")).get("profiles")),
        as_dict(as_dict(runtime.get("social_state")).get("profiles")),
    ):
        profile = container.get(npc_id)
        if isinstance(profile, dict):
            return deepcopy(profile)
    return {}


def npc_profile_by_name(session: dict[str, Any], name: str) -> dict[str, Any]:
    normalized = normalize(name)
    if not normalized:
        return {}
    for profile in all_npc_profiles(session):
        if normalized in {
            normalize(profile.get("name")),
            normalize(profile.get("npc_id")),
            normalize(profile.get("id")),
        }:
            return profile
    return {}


def referenced_profiles_from_session(
    session: dict[str, Any],
    player_input: str,
) -> list[dict[str, Any]]:
    normalized_input = normalize(player_input)
    present_ids = present_npc_ids(session)
    profiles = []
    for profile in all_npc_profiles(session):
        npc_id = as_text(profile.get("npc_id") or profile.get("id"))
        name = as_text(profile.get("name"))
        if (
            npc_id in present_ids
            and name
            and re.search(rf"\b{re.escape(normalize(name))}\b", normalized_input)
        ):
            profiles.append(profile)
    return profiles[:3]


def all_npc_profiles(session: dict[str, Any]) -> list[dict[str, Any]]:
    simulation = as_dict(session.get("simulation_state"))
    runtime = as_dict(session.get("runtime_state"))
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for container in (
        as_dict(simulation.get("npc_index")),
        as_dict(simulation.get("npcs")),
        as_dict(runtime.get("npc_index")),
        as_dict(as_dict(simulation.get("social_state")).get("profiles")),
        as_dict(as_dict(runtime.get("social_state")).get("profiles")),
    ):
        for key, value in container.items():
            if not isinstance(value, dict):
                continue
            profile = {"id": key, **value}
            npc_id = as_text(profile.get("npc_id") or profile.get("id"))
            if npc_id and npc_id not in seen:
                seen.add(npc_id)
                profiles.append(profile)
    return profiles


def present_npc_ids(session: dict[str, Any]) -> set[str]:
    simulation = as_dict(session.get("simulation_state"))
    runtime = as_dict(session.get("runtime_state"))
    scene = as_dict(runtime.get("current_scene")) or as_dict(runtime.get("scene")) or as_dict(simulation.get("scene"))
    player = as_dict(simulation.get("player_state"))
    values = [
        *list(scene.get("present_npc_ids") or []),
        *list(player.get("nearby_npc_ids") or []),
        *list(runtime.get("present_npc_ids") or []),
        *list(runtime.get("nearby_npc_ids") or []),
    ]
    for row in list(scene.get("nearby_npcs") or []) + list(scene.get("npcs") or []):
        if isinstance(row, dict):
            values.append(row.get("npc_id") or row.get("id"))
        elif isinstance(row, str):
            values.append(row)
    return {as_text(value) for value in values if as_text(value)}


def recent_interactions(session: dict[str, Any]) -> list[dict[str, Any]]:
    runtime = as_dict(session.get("runtime_state"))
    value = runtime.get("recent_interactions")
    if not isinstance(value, list):
        value = as_dict(runtime.get("interaction_timeline")).get("events")
    return [item for item in (value or []) if isinstance(item, dict)][-12:]


def location_name(session: dict[str, Any]) -> str:
    state = as_dict(session.get("state"))
    simulation = as_dict(session.get("simulation_state"))
    scene = as_dict(state.get("scene")) or as_dict(simulation.get("scene"))
    return as_text(scene.get("location_name") or scene.get("location") or state.get("location"))


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", as_text(value).casefold()).strip()


def as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
