"""Persistence normalization for authoritative RPG map session state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.apps.rpg.world.map_projection import MAP_STATE_SCHEMA_VERSION, initial_map_session_state
from app.apps.rpg.world.map_world_integration import integrate_canonical_world_map_state

_DEMO_PRESET_ID = "demo_glimmerdeep_pass_lvl14"
_DEMO_STARTING_LOCATION_ID = "glimmerdeep_pass"


def ensure_session_map_state(session: dict[str, Any]) -> dict[str, Any]:
    """Normalize explicit canonical map state without inferring IDs from labels."""

    state = raw_state if isinstance(raw_state := session.get("state"), dict) else None
    if state is None:
        return session

    if isinstance(state.get("world_graph"), dict):
        return integrate_canonical_world_map_state(session)

    current = state.get("map_state")
    if isinstance(current, dict) and current.get("schema_version") == MAP_STATE_SCHEMA_VERSION:
        return session

    starting_location_id = _explicit_starting_location_id(session)
    if not starting_location_id:
        return session
    try:
        map_state = initial_map_session_state(starting_location_id)
    except (KeyError, ValueError):
        return session

    state["map_state"] = map_state
    state["current_location_id"] = starting_location_id
    player = raw_player if isinstance(raw_player := state.get("player"), dict) else {}
    player["location_id"] = starting_location_id
    state["player"] = player
    session["state"] = state
    return session


def _explicit_starting_location_id(session: Mapping[str, Any]) -> str:
    setup = _mapping(session.get("setup_payload"))
    direct = _text(setup.get("starting_location"))
    if direct:
        return direct
    request = _mapping(setup.get("request"))
    requested = _text(request.get("starting_location"))
    if requested:
        return requested
    genesis = _mapping(setup.get("genesis"))
    genesis_location = _text(genesis.get("starting_location"))
    if genesis_location:
        return genesis_location
    manifest = _mapping(session.get("manifest"))
    preset_id = _text(setup.get("preset_id") or manifest.get("created_from_preset"))
    if preset_id == _DEMO_PRESET_ID:
        return _DEMO_STARTING_LOCATION_ID
    return ""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
