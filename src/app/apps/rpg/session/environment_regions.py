"""Region-level environment helpers for RPG Environment 2.0."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.environment_snapshot import derive_environment_snapshot

DEFAULT_ACTIVE_REGION_ID = "active"


def get_active_region_environment(world_state: dict[str, Any]) -> dict[str, Any]:
    world = world_state if isinstance(world_state, dict) else {}
    active_region_id = get_active_region_id(world)
    region_environment = _region_environment(world, active_region_id)
    if region_environment is not None:
        return region_environment
    environment = raw_environment if isinstance(raw_environment := world.get("environment"), dict) else {}
    return deepcopy(environment)


def derive_active_region_snapshot(
    world_state: dict[str, Any],
    scene_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    environment = get_active_region_environment(world_state)
    return derive_environment_snapshot(environment, scene_context)


def get_active_region_id(world_state: dict[str, Any]) -> str:
    world = world_state if isinstance(world_state, dict) else {}
    environment = raw_environment if isinstance(raw_environment := world.get("environment"), dict) else {}
    active_region_id = environment.get("active_region_id") or environment.get("region_id")
    return str(active_region_id or DEFAULT_ACTIVE_REGION_ID)


def _region_environment(world_state: dict[str, Any], region_id: str) -> dict[str, Any] | None:
    regions = raw_regions if isinstance(raw_regions := world_state.get("regions"), dict) else {}
    region = regions.get(region_id) if isinstance(regions.get(region_id), dict) else None
    if not region:
        return None
    environment = raw_environment if isinstance(raw_environment := region.get("environment"), dict) else None
    return deepcopy(environment) if environment is not None else None
