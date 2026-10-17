"""Semantic validation and deterministic scenario map initialization."""
from __future__ import annotations

from collections.abc import Mapping

from app.apps.rpg.world.map_grid_contracts import (
    GridActorPlacement,
    GridMapDefinition,
    GridPoint,
)
from app.apps.rpg.world.map_instance_runtime import (
    CampaignMapInstanceSnapshot,
    create_map_instance_snapshot,
)

from .contracts import (
    MapInitializationOperation,
    ScenarioRevisionDocument,
    WorldReleaseDocument,
    WorldRevisionDocument,
    canonical_content_hash,
)
from .release_artifact_refresh import refresh_release_runtime_artifacts
from typing import Any, cast

_PROTAGONIST_IDS = {"protagonist", "actor:protagonist", "player:protagonist"}


class WorldSemanticError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def _declared_ids(definition: GridMapDefinition, *keys: str) -> set[str]:
    result: set[str] = set()
    for key in keys:
        value = definition.metadata.get(key)
        if isinstance(value, Mapping):
            result.update(str(item) for item in value)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, Mapping):
                    identity = item.get("id") or item.get(f"{key.rstrip('s')}_id")
                    if identity:
                        result.add(str(identity))
                elif item:
                    result.add(str(item))
    return result


def _definition_ids(definition: GridMapDefinition) -> dict[str, set[str]]:
    portal_ids = {portal.portal_id for portal in definition.portals}
    return {
        "portal": portal_ids,
        "spawn": {spawn.spawn_point_id for spawn in definition.spawn_points},
        "zone": {zone.zone_id for zone in definition.zones},
        "route": portal_ids
        | _declared_ids(definition, "semantic_route_ids", "route_ids", "routes"),
        "object": _declared_ids(
            definition,
            "semantic_object_ids",
            "object_ids",
            "objects",
        ),
        "hazard": _declared_ids(
            definition,
            "semantic_hazard_ids",
            "hazard_ids",
            "hazards",
        ),
    }


def validate_release_bindings(
    world_revision: WorldRevisionDocument,
    release: WorldReleaseDocument,
    definitions: Mapping[str, GridMapDefinition],
) -> None:
    if release.world_id != world_revision.world_id:
        raise WorldSemanticError("world_release_world_mismatch")
    if release.world_revision != world_revision.revision:
        raise WorldSemanticError("world_release_revision_mismatch")
    if release.world_revision_hash != world_revision.content_hash:
        raise WorldSemanticError("world_release_hash_mismatch")
    for binding in release.map_bindings:
        definition = definitions.get(binding.map_id)
        if definition is None:
            raise WorldSemanticError("world_release_map_definition_missing", binding.map_id)
        if definition.definition_revision != binding.definition_revision:
            raise WorldSemanticError(
                "world_release_map_revision_mismatch",
                binding.map_id,
            )
        if definition.definition_hash != binding.definition_hash:
            raise WorldSemanticError("world_release_map_hash_mismatch", binding.map_id)
        if definition.semantic_interface_hash != binding.semantic_interface_hash:
            raise WorldSemanticError(
                "world_release_semantic_interface_mismatch",
                binding.map_id,
            )
        if definition.world_id != world_revision.world_id:
            raise WorldSemanticError("world_release_map_world_mismatch", binding.map_id)
        if definition.world_revision != world_revision.revision:
            raise WorldSemanticError(
                "world_release_map_world_revision_mismatch",
                binding.map_id,
            )


def release_launch_findings(
    world_revision: WorldRevisionDocument,
    release: WorldReleaseDocument,
) -> tuple[str, ...]:
    bound = {binding.map_id for binding in release.map_bindings}
    findings: list[str] = []
    for requirement in world_revision.blueprint_requirements:
        map_id = str(requirement.get("map_id") or "")
        if not map_id or bool(requirement.get("deferred")):
            continue
        if requirement.get("required_before_launch") is False:
            continue
        readiness = str(requirement.get("simulation_readiness") or "semantic")
        if readiness in {"stub", "semantic", "failed"} or map_id not in bound:
            findings.append(f"map_not_launch_ready:{map_id}")
    return tuple(sorted(set(findings)))


def certify_world_release(
    world_revision: WorldRevisionDocument,
    release: WorldReleaseDocument,
    definitions: Mapping[str, GridMapDefinition],
) -> WorldReleaseDocument:
    release = refresh_release_runtime_artifacts(world_revision, release)
    validate_release_bindings(world_revision, release, definitions)
    findings = release_launch_findings(world_revision, release)
    certification = dict(release.certification)
    existing = [str(item) for item in certification.get("missing_requirements") or ()]
    missing = sorted(set((*existing, *findings)))
    certification.update(
        {
            "semantic_validation": {
                "passed": True,
                "map_binding_count": len(release.map_bindings),
            },
            "missing_requirements": missing,
            "launch_ready": bool(certification.get("launch_ready")) and not missing,
        }
    )
    payload = release.model_dump(mode="json")
    payload["certification"] = certification
    payload["release_hash"] = ""
    payload["release_hash"] = canonical_content_hash(payload)
    return WorldReleaseDocument.model_validate(payload)


def resolve_starting_definition(
    scenario: ScenarioRevisionDocument,
    definitions: Mapping[str, GridMapDefinition],
) -> GridMapDefinition:
    matches = [
        definition
        for definition in definitions.values()
        if str(definition.metadata.get("location_id") or "")
        == scenario.starting_location_id
        or definition.map_id == scenario.starting_location_id
    ]
    if not matches:
        raise WorldSemanticError(
            "scenario_starting_map_missing",
            scenario.starting_location_id,
        )
    if len(matches) != 1:
        raise WorldSemanticError(
            "scenario_starting_map_ambiguous",
            scenario.starting_location_id,
        )
    return matches[0]


def _operation_cell(
    definition: GridMapDefinition,
    operation: MapInitializationOperation,
) -> GridPoint:
    spawn_id = str(operation.payload.get("spawn_point_id") or "")
    if spawn_id:
        for spawn in definition.spawn_points:
            if spawn.spawn_point_id == spawn_id:
                return spawn.cell
        raise WorldSemanticError("scenario_spawn_point_missing", spawn_id)
    raw_cell = operation.payload.get("cell")
    if not isinstance(raw_cell, (list, tuple)) or len(raw_cell) != 2:
        raise WorldSemanticError(
            "scenario_actor_placement_target_missing",
            operation.operation_id,
        )
    cell = (int(raw_cell[0]), int(raw_cell[1]))
    definition.require_inside(cell)
    if not definition.is_walkable(cell):
        raise WorldSemanticError("scenario_actor_placement_blocked", operation.operation_id)
    return cell


def validate_scenario_against_release(
    scenario: ScenarioRevisionDocument,
    release: WorldReleaseDocument,
    definitions: Mapping[str, GridMapDefinition],
) -> GridMapDefinition:
    if scenario.world_id != release.world_id:
        raise WorldSemanticError("scenario_world_mismatch")
    if scenario.world_revision != release.world_revision:
        raise WorldSemanticError("scenario_world_revision_mismatch")
    if scenario.world_revision_hash != release.world_revision_hash:
        raise WorldSemanticError("scenario_world_hash_mismatch")
    if scenario.compatible_release not in {None, release.release}:
        raise WorldSemanticError("scenario_release_incompatible")
    for operation in scenario.map_initialization:
        definition = definitions.get(operation.map_id)
        if definition is None:
            raise WorldSemanticError(
                "scenario_initialization_map_missing",
                operation.map_id,
            )
        ids = _definition_ids(definition)
        if operation.type == "place_actor":
            _operation_cell(definition, operation)
        elif operation.type == "set_route_state" and operation.target_id not in ids["route"]:
            raise WorldSemanticError("scenario_route_missing", operation.target_id)
        elif operation.type == "set_object_state" and operation.target_id not in ids["object"]:
            raise WorldSemanticError("scenario_object_missing", operation.target_id)
        elif operation.type == "set_hazard_state" and operation.target_id not in ids["hazard"]:
            raise WorldSemanticError("scenario_hazard_missing", operation.target_id)
    return resolve_starting_definition(scenario, definitions)


def _default_spawn(definition: GridMapDefinition) -> GridPoint:
    candidates = [spawn for spawn in definition.spawn_points if not spawn.secret]
    if not candidates:
        raise WorldSemanticError("scenario_player_spawn_missing", definition.map_id)
    candidates.sort(
        key=lambda spawn: (
            0
            if set(spawn.tags).intersection({"player", "start", "arrival"})
            else 1,
            spawn.spawn_point_id,
        )
    )
    return candidates[0].cell


def initialize_starting_map_snapshot(
    *,
    map_instance_id: str,
    campaign_id: str,
    protagonist_actor_id: str,
    scenario: ScenarioRevisionDocument,
    definition: GridMapDefinition,
) -> CampaignMapInstanceSnapshot:
    operations = tuple(
        operation
        for operation in scenario.map_initialization
        if operation.map_id == definition.map_id
    )
    protagonist_placed = any(
        operation.type == "place_actor" and operation.target_id in _PROTAGONIST_IDS
        for operation in operations
    )
    initial_actors = (
        ()
        if protagonist_placed
        else (
            GridActorPlacement(
                actor_id=protagonist_actor_id,
                cell=_default_spawn(definition),
            ),
        )
    )
    snapshot = create_map_instance_snapshot(
        map_instance_id=map_instance_id,
        campaign_id=campaign_id,
        location_id=scenario.starting_location_id,
        definition=definition,
        actors=initial_actors,
    )
    actors = list(snapshot.actors)
    object_states = dict(snapshot.object_states)
    route_states = dict(snapshot.route_states)
    hazard_states = dict(snapshot.hazard_states)
    applied: list[str] = []
    for operation in operations:
        if operation.type == "place_actor":
            actor_id = (
                protagonist_actor_id
                if operation.target_id in _PROTAGONIST_IDS
                else operation.target_id
            )
            if any(actor.actor_id == actor_id for actor in actors):
                raise WorldSemanticError("scenario_actor_placed_twice", actor_id)
            actors.append(
                GridActorPlacement(
                    actor_id=actor_id,
                    cell=_operation_cell(definition, operation),
                    facing=cast(Any, str(operation.payload.get("facing") or "south")),
                    hidden=bool(operation.payload.get("hidden", False)),
                )
            )
        elif operation.type == "set_object_state":
            object_states[operation.target_id] = dict(operation.payload)
        elif operation.type == "set_route_state":
            route_states[operation.target_id] = dict(operation.payload)
        elif operation.type == "set_hazard_state":
            hazard_states[operation.target_id] = dict(operation.payload)
        applied.append(operation.operation_id)
    payload = snapshot.model_dump(mode="json")
    payload.update(
        {
            "actors": [actor.model_dump(mode="json") for actor in actors],
            "object_states": object_states,
            "route_states": route_states,
            "hazard_states": hazard_states,
            "initialization_operation_ids": applied,
        }
    )
    return CampaignMapInstanceSnapshot.model_validate(payload)
