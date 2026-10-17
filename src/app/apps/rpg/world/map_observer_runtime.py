"""Deterministic observer knowledge, detection, line of sight, and projection."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.apps.rpg.world.map_actor_footprints import actor_footprint_cells
from app.apps.rpg.world.map_effective_geometry import (
    effective_blocks_sight,
    effective_terrain_rows,
)
from app.apps.rpg.world.map_grid_contracts import GridMapDefinition, GridPoint
from app.apps.rpg.world.map_instance_runtime import CampaignMapInstanceSnapshot, FrozenRuntimeModel


class ObserverPerceptionPolicy(FrozenRuntimeModel):
    sight_radius: int = Field(default=8, ge=1, le=128)
    detection_radius: int = Field(default=3, ge=0, le=128)
    remember_terrain: bool = True


class ObserverMapKnowledge(FrozenRuntimeModel):
    campaign_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    observer_actor_id: str = Field(min_length=1)
    knowledge_revision: int = Field(default=0, ge=0)
    observation_sequence: int = Field(default=0, ge=0)
    observed_map_state_revision: int = Field(default=0, ge=0)
    policy: ObserverPerceptionPolicy = Field(default_factory=ObserverPerceptionPolicy)
    visible_cells: tuple[GridPoint, ...] = ()
    known_cells: tuple[GridPoint, ...] = ()
    detected_actor_ids: tuple[str, ...] = ()
    known_portal_ids: tuple[str, ...] = ()
    known_spawn_point_ids: tuple[str, ...] = ()
    known_zone_ids: tuple[str, ...] = ()


class ObserverMapObservedEvent(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    event_type: str = "observer_map_observed"
    campaign_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    observer_actor_id: str = Field(min_length=1)
    observation_sequence: int = Field(ge=1)
    map_state_revision: int = Field(ge=0)
    visible_cells: tuple[GridPoint, ...]
    newly_known_cells: tuple[GridPoint, ...]
    detected_actor_ids: tuple[str, ...]
    newly_detected_actor_ids: tuple[str, ...]
    discovered_portal_ids: tuple[str, ...]
    discovered_spawn_point_ids: tuple[str, ...]
    discovered_zone_ids: tuple[str, ...]


def grid_distance(left: GridPoint, right: GridPoint) -> int:
    return max(abs(left[0] - right[0]), abs(left[1] - right[1]))


def line_cells(start: GridPoint, end: GridPoint) -> tuple[GridPoint, ...]:
    """Return deterministic supercover cells crossed by a grid line."""

    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0
    nx = abs(dx)
    ny = abs(dy)
    sign_x = 1 if dx > 0 else -1 if dx < 0 else 0
    sign_y = 1 if dy > 0 else -1 if dy < 0 else 0
    x, y = x0, y0
    ix = iy = 0
    cells: list[GridPoint] = [(x, y)]
    while ix < nx or iy < ny:
        left = (1 + 2 * ix) * ny
        right = (1 + 2 * iy) * nx
        if left == right:
            x += sign_x
            y += sign_y
            ix += 1
            iy += 1
        elif left < right:
            x += sign_x
            ix += 1
        else:
            y += sign_y
            iy += 1
        if cells[-1] != (x, y):
            cells.append((x, y))
    return tuple(cells)


def has_line_of_sight(
    definition: GridMapDefinition,
    start: GridPoint,
    end: GridPoint,
    *,
    snapshot: CampaignMapInstanceSnapshot | None = None,
) -> bool:
    definition.require_inside(start)
    definition.require_inside(end)
    cells = line_cells(start, end)
    for cell in cells[1:-1]:
        blocked = (
            effective_blocks_sight(definition, snapshot, cell)
            if snapshot is not None
            else definition.terrain_rule(cell).blocks_sight
        )
        if blocked:
            return False
    return True


def visible_cells(
    definition: GridMapDefinition,
    origin: GridPoint,
    sight_radius: int,
    *,
    snapshot: CampaignMapInstanceSnapshot | None = None,
) -> tuple[GridPoint, ...]:
    definition.require_inside(origin)
    cells = []
    for row in range(definition.height):
        for column in range(definition.width):
            cell = (column, row)
            if grid_distance(origin, cell) > sight_radius:
                continue
            if has_line_of_sight(
                definition,
                origin,
                cell,
                snapshot=snapshot,
            ):
                cells.append(cell)
    return tuple(sorted(cells, key=lambda cell: (cell[1], cell[0])))


def _feature_cell(
    definition: GridMapDefinition,
    portal_id: str,
) -> GridPoint | None:
    portal = next(
        (row for row in definition.portals if row.portal_id == portal_id),
        None,
    )
    if portal is None:
        return None
    if portal.source.map_id == definition.map_id:
        return portal.source.cell
    if portal.target.map_id == definition.map_id:
        return portal.target.cell
    return None


def _detectable(
    cell: GridPoint,
    *,
    origin: GridPoint,
    visible: set[GridPoint],
    detection_radius: int,
) -> bool:
    return cell in visible and grid_distance(origin, cell) <= detection_radius


def observe_map(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    *,
    observer_actor_id: str,
    previous: ObserverMapKnowledge | None = None,
    policy: ObserverPerceptionPolicy | None = None,
) -> tuple[ObserverMapKnowledge, ObserverMapObservedEvent]:
    observer = snapshot.actor(observer_actor_id)
    perception = policy or (
        previous.policy if previous is not None else ObserverPerceptionPolicy()
    )
    if perception.detection_radius > perception.sight_radius:
        raise ValueError("observer_detection_radius_exceeds_sight_radius")
    current_visible = visible_cells(
        definition,
        observer.cell,
        perception.sight_radius,
        snapshot=snapshot,
    )
    visible_set = set(current_visible)
    previous_known_cells = set(previous.known_cells if previous is not None else ())
    known_cells = (
        previous_known_cells | visible_set
        if perception.remember_terrain
        else visible_set
    )
    previous_actors = set(previous.detected_actor_ids if previous is not None else ())
    explicitly_revealed_actors = set(snapshot.revealed_actor_ids)
    detected_actors = []
    for actor in snapshot.actors:
        visible_actor_cells = set(actor_footprint_cells(actor)) & visible_set
        if not visible_actor_cells:
            continue
        if not actor.hidden or actor.actor_id == observer_actor_id:
            detected_actors.append(actor.actor_id)
            continue
        if (
            actor.actor_id in explicitly_revealed_actors
            or actor.actor_id in previous_actors
            or any(
                _detectable(
                    cell,
                    origin=observer.cell,
                    visible=visible_set,
                    detection_radius=perception.detection_radius,
                )
                for cell in visible_actor_cells
            )
        ):
            detected_actors.append(actor.actor_id)
    detected_actor_ids = tuple(sorted(set(detected_actors)))

    revealed_secrets = set(snapshot.revealed_secret_ids)
    previous_portals = set(previous.known_portal_ids if previous is not None else ())
    known_portals = set(previous_portals)
    for portal in definition.portals:
        cell = _feature_cell(definition, portal.portal_id)
        if cell is None or cell not in visible_set:
            continue
        if (
            not portal.secret
            or portal.portal_id in revealed_secrets
            or portal.portal_id in previous_portals
            or _detectable(
                cell,
                origin=observer.cell,
                visible=visible_set,
                detection_radius=perception.detection_radius,
            )
        ):
            known_portals.add(portal.portal_id)

    previous_spawns = set(
        previous.known_spawn_point_ids if previous is not None else ()
    )
    known_spawns = set(previous_spawns)
    for spawn in definition.spawn_points:
        if spawn.cell not in visible_set:
            continue
        if (
            not spawn.secret
            or spawn.spawn_point_id in revealed_secrets
            or spawn.spawn_point_id in previous_spawns
            or _detectable(
                spawn.cell,
                origin=observer.cell,
                visible=visible_set,
                detection_radius=perception.detection_radius,
            )
        ):
            known_spawns.add(spawn.spawn_point_id)

    previous_zones = set(previous.known_zone_ids if previous is not None else ())
    known_zones = set(previous_zones)
    for zone in definition.zones:
        visible_zone_cells = set(zone.cells) & visible_set
        if not visible_zone_cells:
            continue
        detected = any(
            _detectable(
                cell,
                origin=observer.cell,
                visible=visible_set,
                detection_radius=perception.detection_radius,
            )
            for cell in visible_zone_cells
        )
        if (
            not zone.secret
            or zone.zone_id in revealed_secrets
            or zone.zone_id in previous_zones
            or detected
        ):
            known_zones.add(zone.zone_id)

    prior_sequence = previous.observation_sequence if previous is not None else 0
    prior_revision = previous.knowledge_revision if previous is not None else 0
    knowledge = ObserverMapKnowledge(
        campaign_id=snapshot.campaign_id,
        map_instance_id=snapshot.map_instance_id,
        observer_actor_id=observer_actor_id,
        knowledge_revision=prior_revision + 1,
        observation_sequence=prior_sequence + 1,
        observed_map_state_revision=snapshot.map_state_revision,
        policy=perception,
        visible_cells=current_visible,
        known_cells=tuple(sorted(known_cells, key=lambda cell: (cell[1], cell[0]))),
        detected_actor_ids=detected_actor_ids,
        known_portal_ids=tuple(sorted(known_portals)),
        known_spawn_point_ids=tuple(sorted(known_spawns)),
        known_zone_ids=tuple(sorted(known_zones)),
    )
    event = ObserverMapObservedEvent(
        event_id=(
            f"observation:{snapshot.map_instance_id}:{observer_actor_id}:"
            f"{knowledge.observation_sequence}"
        ),
        campaign_id=snapshot.campaign_id,
        map_instance_id=snapshot.map_instance_id,
        observer_actor_id=observer_actor_id,
        observation_sequence=knowledge.observation_sequence,
        map_state_revision=snapshot.map_state_revision,
        visible_cells=current_visible,
        newly_known_cells=tuple(
            sorted(
                known_cells - previous_known_cells,
                key=lambda cell: (cell[1], cell[0]),
            )
        ),
        detected_actor_ids=detected_actor_ids,
        newly_detected_actor_ids=tuple(
            sorted(set(detected_actor_ids) - previous_actors)
        ),
        discovered_portal_ids=tuple(sorted(known_portals - previous_portals)),
        discovered_spawn_point_ids=tuple(sorted(known_spawns - previous_spawns)),
        discovered_zone_ids=tuple(sorted(known_zones - previous_zones)),
    )
    return knowledge, event


def project_observer_knowledge(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    knowledge: ObserverMapKnowledge,
) -> dict[str, Any]:
    if knowledge.campaign_id != snapshot.campaign_id:
        raise ValueError("observer_knowledge_campaign_mismatch")
    if knowledge.map_instance_id != snapshot.map_instance_id:
        raise ValueError("observer_knowledge_map_instance_mismatch")
    visible_actor_ids = set(knowledge.detected_actor_ids)
    known_portals = set(knowledge.known_portal_ids)
    known_spawns = set(knowledge.known_spawn_point_ids)
    known_zones = set(knowledge.known_zone_ids)
    return {
        "map_instance_id": snapshot.map_instance_id,
        "map_id": definition.map_id,
        "definition_revision": definition.definition_revision,
        "definition_hash": definition.definition_hash,
        "map_state_revision": snapshot.map_state_revision,
        "observer_actor_id": knowledge.observer_actor_id,
        "knowledge_revision": knowledge.knowledge_revision,
        "observation_sequence": knowledge.observation_sequence,
        "observed_map_state_revision": knowledge.observed_map_state_revision,
        "visible_cells": [list(cell) for cell in knowledge.visible_cells],
        "known_cells": [list(cell) for cell in knowledge.known_cells],
        "grid": {
            "width": definition.width,
            "height": definition.height,
            "transform": definition.transform.model_dump(mode="json"),
            "terrain_rows": effective_terrain_rows(
                definition,
                snapshot,
                unknown_cells=set(knowledge.known_cells),
            ),
            "terrain_palette": [
                rule.model_dump(mode="json") for rule in definition.terrain_palette
            ],
            "unknown_code": "?",
        },
        "portals": [
            portal.model_dump(mode="json")
            for portal in definition.portals
            if portal.portal_id in known_portals
        ],
        "spawn_points": [
            spawn.model_dump(mode="json")
            for spawn in definition.spawn_points
            if spawn.spawn_point_id in known_spawns
        ],
        "zones": [
            zone.model_dump(mode="json")
            for zone in definition.zones
            if zone.zone_id in known_zones
        ],
        "actors": [
            actor.model_dump(mode="json")
            for actor in snapshot.actors
            if actor.actor_id in visible_actor_ids
        ],
        "object_states": {},
        "route_states": {
            key: dict(value)
            for key, value in snapshot.route_states.items()
            if key in known_portals
        },
    }
