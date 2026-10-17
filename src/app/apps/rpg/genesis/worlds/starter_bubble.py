"""Deterministic starter-bubble planning and navigable placeholder maps."""
from __future__ import annotations

import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.apps.rpg.world.map_grid_contracts import (
    GridMapDefinition,
    GridPortal,
    GridPortalEndpoint,
    GridSpawnPoint,
    GridZone,
    TerrainRule,
    with_grid_definition_hashes,
)

SimulationReadiness = Literal["stub", "semantic", "navigable", "certified", "failed"]
PresentationReadiness = Literal["placeholder", "assets_pending", "ready", "failed"]
StarterLocationRole = Literal[
    "region",
    "settlement",
    "interior",
    "neighbor",
    "frontier",
]

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.:-]+")


class FrozenStarterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StarterLocationSlot(FrozenStarterModel):
    location_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role: StarterLocationRole
    map_id: str | None = None
    map_level: Literal["settlement", "dungeon", "interior", "encounter"] | None = None
    connected_location_ids: tuple[str, ...] = ()
    deferred: bool = False
    simulation_readiness: SimulationReadiness = "stub"
    presentation_readiness: PresentationReadiness = "placeholder"
    predictive_score: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StarterBubblePlan(FrozenStarterModel):
    schema_version: Literal["rpg_starter_bubble_v1"] = "rpg_starter_bubble_v1"
    world_id: str = Field(min_length=1)
    source_world_revision: int = Field(ge=1)
    starting_location_id: str = Field(min_length=1)
    slots: tuple[StarterLocationSlot, ...]
    topology: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def slot(self, location_id: str) -> StarterLocationSlot:
        for slot in self.slots:
            if slot.location_id == location_id:
                return slot
        raise KeyError(location_id)

    def map_slots(self, *, include_deferred: bool = False) -> tuple[StarterLocationSlot, ...]:
        return tuple(
            slot
            for slot in self.slots
            if slot.map_id and (include_deferred or not slot.deferred)
        )


def _safe(value: str) -> str:
    return _SAFE_ID.sub("-", value).strip("-") or "location"


def _title(value: str) -> str:
    leaf = value.rsplit(":", 1)[-1].replace("_", "-")
    return " ".join(part.capitalize() for part in leaf.split("-") if part) or value


def build_starter_bubble(
    *,
    world_id: str,
    source_world_revision: int,
    starting_location_id: str,
    neighboring_location_id: str | None = None,
) -> StarterBubblePlan:
    """Plan one region, settlement, interior, neighbor, and deferred frontier."""

    safe = _safe(starting_location_id)
    region_id = f"region:{safe}:starter"
    interior_id = f"{starting_location_id}:interior"
    neighbor_id = neighboring_location_id or f"{starting_location_id}:outskirts"
    frontier_id = f"{neighbor_id}:frontier"
    slots = (
        StarterLocationSlot(
            location_id=region_id,
            title=f"{_title(starting_location_id)} Region",
            role="region",
            connected_location_ids=(starting_location_id, neighbor_id),
            simulation_readiness="semantic",
            presentation_readiness="placeholder",
            metadata={"owns_world_graph": True},
        ),
        StarterLocationSlot(
            location_id=starting_location_id,
            title=_title(starting_location_id),
            role="settlement",
            map_id=f"map:{safe}:settlement",
            map_level="settlement",
            connected_location_ids=(interior_id, neighbor_id),
            simulation_readiness="navigable",
            presentation_readiness="placeholder",
            predictive_score=1.0,
            metadata={"required_before_launch": True},
        ),
        StarterLocationSlot(
            location_id=interior_id,
            title=f"{_title(starting_location_id)} Interior",
            role="interior",
            map_id=f"map:{safe}:interior",
            map_level="interior",
            connected_location_ids=(starting_location_id,),
            simulation_readiness="navigable",
            presentation_readiness="placeholder",
            predictive_score=0.95,
            metadata={"required_before_launch": True},
        ),
        StarterLocationSlot(
            location_id=neighbor_id,
            title=_title(neighbor_id),
            role="neighbor",
            map_id=f"map:{_safe(neighbor_id)}:neighbor",
            map_level="settlement",
            connected_location_ids=(starting_location_id, frontier_id),
            simulation_readiness="navigable",
            presentation_readiness="assets_pending",
            predictive_score=0.8,
            metadata={"required_before_launch": True, "art_optional": True},
        ),
        StarterLocationSlot(
            location_id=frontier_id,
            title=_title(frontier_id),
            role="frontier",
            map_id=f"map:{_safe(frontier_id)}:frontier",
            map_level="encounter",
            connected_location_ids=(neighbor_id,),
            deferred=True,
            simulation_readiness="semantic",
            presentation_readiness="assets_pending",
            predictive_score=0.45,
            metadata={"materialize_on_approach": True, "art_optional": True},
        ),
    )
    routes = [
        {
            "route_id": f"route:{_safe(source)}:{_safe(target)}",
            "source_location_id": source,
            "target_location_id": target,
            "state": "open",
        }
        for source, target in (
            (starting_location_id, interior_id),
            (starting_location_id, neighbor_id),
            (neighbor_id, frontier_id),
        )
    ]
    return StarterBubblePlan(
        world_id=world_id,
        source_world_revision=source_world_revision,
        starting_location_id=starting_location_id,
        slots=slots,
        topology={
            "schema_version": "rpg_progressive_topology_v1",
            "region_id": region_id,
            "locations": [slot.location_id for slot in slots],
            "routes": routes,
            "deferred_location_ids": [
                slot.location_id for slot in slots if slot.deferred
            ],
        },
        metadata={
            "simulation_and_presentation_readiness_independent": True,
            "optional_art_never_blocks_navigation": True,
        },
    )


def predictive_materialization_queue(
    plan: StarterBubblePlan,
    *,
    current_location_id: str,
    minimum_score: float = 0.35,
) -> tuple[dict[str, Any], ...]:
    """Return deterministic background candidates before the player reaches them."""

    current = plan.slot(current_location_id)
    candidates = [
        slot
        for slot in plan.slots
        if slot.deferred
        and slot.predictive_score >= minimum_score
        and (
            slot.location_id in current.connected_location_ids
            or set(slot.connected_location_ids).intersection(current.connected_location_ids)
        )
    ]
    return tuple(
        {
            "location_id": slot.location_id,
            "map_id": slot.map_id,
            "priority": round(slot.predictive_score, 3),
            "resource_class": "cpu",
            "presentation_optional": True,
            "fallback": "navigable_placeholder",
        }
        for slot in sorted(
            candidates,
            key=lambda item: (-item.predictive_score, item.location_id),
        )
    )


def _portal_cells(count: int) -> tuple[tuple[int, int], ...]:
    cells = ((4, 0), (8, 4), (4, 8), (0, 4))
    return tuple(cells[index % len(cells)] for index in range(count))


def build_starter_map_definitions(
    plan: StarterBubblePlan,
    *,
    target_world_revision: int,
    definition_revisions: Mapping[str, int] | None = None,
    include_deferred: bool = False,
) -> tuple[GridMapDefinition, ...]:
    """Materialize deterministic navigable grids while visual assets remain optional."""

    revisions = dict(definition_revisions or {})
    map_by_location = {
        slot.location_id: slot.map_id
        for slot in plan.slots
        if slot.map_id and (include_deferred or not slot.deferred)
    }
    definitions: list[GridMapDefinition] = []
    terrain_rows = (
        "####.####",
        "#.......#",
        "#.......#",
        "#.......#",
        ".........",
        "#.......#",
        "#.......#",
        "#.......#",
        "####.####",
    )
    for slot in plan.slots:
        if not slot.map_id or slot.location_id not in map_by_location:
            continue
        targets = [
            location_id
            for location_id in slot.connected_location_ids
            if location_id in map_by_location
        ]
        source_cells = _portal_cells(len(targets))
        portals = tuple(
            GridPortal(
                portal_id=f"portal:{_safe(slot.location_id)}:{_safe(target_id)}",
                source=GridPortalEndpoint(
                    map_id=slot.map_id,
                    cell=source_cells[index],
                ),
                target=GridPortalEndpoint(
                    map_id=str(map_by_location[target_id]),
                    cell=(4, 4),
                ),
            )
            for index, target_id in enumerate(targets)
        )
        definition = GridMapDefinition(
            map_id=slot.map_id,
            level=slot.map_level or "encounter",
            definition_revision=int(revisions.get(slot.map_id, 1)),
            world_id=plan.world_id,
            world_revision=target_world_revision,
            width=9,
            height=9,
            terrain_palette=(
                TerrainRule(code=".", terrain_id="floor", walkable=True),
                TerrainRule(
                    code="#",
                    terrain_id="wall",
                    walkable=False,
                    blocks_sight=True,
                ),
            ),
            terrain_rows=terrain_rows,
            portals=portals,
            spawn_points=(
                GridSpawnPoint(
                    spawn_point_id=f"spawn:{_safe(slot.location_id)}:arrival",
                    cell=(4, 4),
                    tags=("arrival", "starter_bubble"),
                ),
            ),
            zones=(
                GridZone(
                    zone_id=f"zone:{_safe(slot.location_id)}:core",
                    name=slot.title,
                    cells=((4, 4), (4, 3), (4, 5), (3, 4), (5, 4)),
                ),
            ),
            metadata={
                "location_id": slot.location_id,
                "starter_role": slot.role,
                "simulation_readiness": "navigable",
                "presentation_readiness": slot.presentation_readiness,
                "presentation_fallback": "semantic_grid_placeholder",
                "art_optional": True,
                "source_world_revision": plan.source_world_revision,
            },
        )
        definitions.append(with_grid_definition_hashes(definition))
    return tuple(definitions)


def starter_bubble_certification(
    plan: StarterBubblePlan,
    definitions: tuple[GridMapDefinition, ...],
) -> dict[str, Any]:
    definition_locations = {
        str(definition.metadata.get("location_id") or "") for definition in definitions
    }
    required = {
        slot.location_id
        for slot in plan.slots
        if bool(slot.metadata.get("required_before_launch"))
    }
    missing = sorted(required.difference(definition_locations))
    failed = sorted(
        slot.location_id
        for slot in plan.slots
        if slot.simulation_readiness == "failed"
    )
    return {
        "schema_version": "rpg_starter_bubble_certification_v1",
        "simulation_certified": not missing and not failed,
        "presentation_complete": all(
            slot.presentation_readiness == "ready"
            for slot in plan.slots
            if slot.location_id in required
        ),
        "required_location_ids": sorted(required),
        "materialized_location_ids": sorted(definition_locations),
        "deferred_location_ids": sorted(
            slot.location_id for slot in plan.slots if slot.deferred
        ),
        "missing_location_ids": missing,
        "failed_location_ids": failed,
        "optional_art_blocks_gameplay": False,
    }
