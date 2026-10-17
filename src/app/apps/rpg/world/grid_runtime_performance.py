"""Measured grid-runtime profiling and evidence-based renderer escalation."""
from __future__ import annotations

import json
from time import perf_counter_ns
from typing import Any, Literal, cast

from pydantic import Field

from app.apps.rpg.world.map_actor_footprints import actor_footprint_cells
from app.apps.rpg.world.map_grid_contracts import GridMapDefinition, GridPoint
from app.apps.rpg.world.map_instance_runtime import (
    CampaignMapInstanceSnapshot,
    FrozenRuntimeModel,
    MapMovementError,
    MoveActorCommand,
    project_observer_map,
    resolve_move_command,
)
from app.apps.rpg.world.map_observer_runtime import ObserverMapKnowledge, project_observer_knowledge


class GridRuntimeBudget(FrozenRuntimeModel):
    max_svg_cells: int = Field(default=4_096, ge=1)
    max_svg_actors: int = Field(default=256, ge=1)
    max_svg_footprint_cells: int = Field(default=512, ge=1)
    max_svg_terrain_overrides: int = Field(default=1_024, ge=0)
    max_svg_visible_cells: int = Field(default=4_096, ge=1)
    max_svg_projection_bytes: int = Field(default=1_500_000, ge=1)
    max_projection_ms: float = Field(default=16.0, gt=0)
    max_pathfinding_ms: float = Field(default=50.0, gt=0)
    max_event_count: int = Field(default=10_000, ge=1)


class GridRuntimeTimings(FrozenRuntimeModel):
    projection_ms: float = Field(ge=0)
    pathfinding_ms: float | None = Field(default=None, ge=0)


class GridRuntimeMetrics(FrozenRuntimeModel):
    cells: int = Field(ge=0)
    actors: int = Field(ge=0)
    footprint_cells: int = Field(ge=0)
    terrain_overrides: int = Field(ge=0)
    object_states: int = Field(ge=0)
    route_states: int = Field(ge=0)
    portals: int = Field(ge=0)
    spawn_points: int = Field(ge=0)
    zones: int = Field(ge=0)
    visible_cells: int = Field(ge=0)
    known_cells: int = Field(ge=0)
    projection_bytes: int = Field(ge=0)
    event_count: int = Field(ge=0)
    path_probe_status: Literal["not_requested", "resolved", "rejected"]
    path_probe_length: int = Field(default=0, ge=0)
    path_probe_cost: int = Field(default=0, ge=0)
    path_probe_error: str = ""


class GridRendererDecision(FrozenRuntimeModel):
    renderer: Literal["svg", "pixi"]
    recommendation: Literal["retain_svg", "escalate_to_pixi"]
    renderer_reasons: tuple[str, ...]
    runtime_warnings: tuple[str, ...]
    metrics: GridRuntimeMetrics
    timings: GridRuntimeTimings
    budget: GridRuntimeBudget
    decision_version: int = 1


class GridRuntimeProfile(FrozenRuntimeModel):
    map_instance_id: str
    map_id: str
    definition_revision: int
    map_state_revision: int
    observer_actor_id: str = ""
    path_probe_actor_id: str = ""
    path_probe_destination: GridPoint | None = None
    decision: GridRendererDecision
    profile_version: int = 1


def assess_grid_renderer(
    metrics: GridRuntimeMetrics,
    timings: GridRuntimeTimings,
    *,
    budget: GridRuntimeBudget | None = None,
) -> GridRendererDecision:
    limits = budget or GridRuntimeBudget()
    renderer_reasons = tuple(
        _limit_reasons(
            (
                ("cells", metrics.cells, limits.max_svg_cells),
                ("actors", metrics.actors, limits.max_svg_actors),
                (
                    "footprint_cells",
                    metrics.footprint_cells,
                    limits.max_svg_footprint_cells,
                ),
                (
                    "terrain_overrides",
                    metrics.terrain_overrides,
                    limits.max_svg_terrain_overrides,
                ),
                (
                    "visible_cells",
                    metrics.visible_cells,
                    limits.max_svg_visible_cells,
                ),
                (
                    "projection_bytes",
                    metrics.projection_bytes,
                    limits.max_svg_projection_bytes,
                ),
            ),
            suffix="svg_budget",
        )
    )
    runtime_warnings = []
    if timings.projection_ms > limits.max_projection_ms:
        runtime_warnings.append(
            "projection_ms_exceeds_runtime_budget:"
            f"{timings.projection_ms:.3f}>{limits.max_projection_ms:.3f}"
        )
    if (
        timings.pathfinding_ms is not None
        and timings.pathfinding_ms > limits.max_pathfinding_ms
    ):
        runtime_warnings.append(
            "pathfinding_ms_exceeds_runtime_budget:"
            f"{timings.pathfinding_ms:.3f}>{limits.max_pathfinding_ms:.3f}"
        )
    if metrics.event_count > limits.max_event_count:
        runtime_warnings.append(
            f"event_count_exceeds_runtime_budget:{metrics.event_count}>"
            f"{limits.max_event_count}"
        )
    renderer = "pixi" if renderer_reasons else "svg"
    return GridRendererDecision(
        renderer=cast(Any, renderer),
        recommendation="escalate_to_pixi" if renderer_reasons else "retain_svg",
        renderer_reasons=renderer_reasons,
        runtime_warnings=tuple(runtime_warnings),
        metrics=metrics,
        timings=timings,
        budget=limits,
    )


def profile_grid_runtime(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    *,
    observer_knowledge: ObserverMapKnowledge | None = None,
    observer_actor_id: str = "",
    path_probe_actor_id: str = "",
    path_probe_destination: GridPoint | None = None,
    event_count: int = 0,
    budget: GridRuntimeBudget | None = None,
) -> GridRuntimeProfile:
    projection_started = perf_counter_ns()
    projection = (
        project_observer_knowledge(definition, snapshot, observer_knowledge)
        if observer_knowledge is not None
        else project_observer_map(
            definition,
            snapshot,
            observer_actor_id=observer_actor_id or "performance-profile",
        )
    )
    projection_ms = _elapsed_ms(projection_started)
    projection_bytes = len(
        json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )

    pathfinding_ms: float | None = None
    path_probe_status: Literal["not_requested", "resolved", "rejected"] = (
        "not_requested"
    )
    path_probe_length = 0
    path_probe_cost = 0
    path_probe_error = ""
    if path_probe_actor_id and path_probe_destination is not None:
        path_started = perf_counter_ns()
        try:
            event, _ = resolve_move_command(
                definition,
                snapshot,
                MoveActorCommand(
                    command_id=(
                        f"performance-probe:{snapshot.map_instance_id}:"
                        f"{snapshot.map_state_revision}:{path_probe_actor_id}:"
                        f"{path_probe_destination[0]}:{path_probe_destination[1]}"
                    ),
                    actor_id=path_probe_actor_id,
                    destination=path_probe_destination,
                    expected_map_state_revision=snapshot.map_state_revision,
                ),
            )
            path_probe_status = "resolved"
            path_probe_length = len(event.path)
            path_probe_cost = event.movement_cost
        except (MapMovementError, ValueError) as exc:
            path_probe_status = "rejected"
            path_probe_error = str(exc)
        finally:
            pathfinding_ms = _elapsed_ms(path_started)

    visible_cells = (
        len(observer_knowledge.visible_cells)
        if observer_knowledge is not None
        else definition.width * definition.height
    )
    known_cells = (
        len(observer_knowledge.known_cells)
        if observer_knowledge is not None
        else visible_cells
    )
    metrics = GridRuntimeMetrics(
        cells=definition.width * definition.height,
        actors=len(snapshot.actors),
        footprint_cells=sum(
            len(actor_footprint_cells(actor)) for actor in snapshot.actors
        ),
        terrain_overrides=len(snapshot.terrain_overrides),
        object_states=len(snapshot.object_states),
        route_states=len(snapshot.route_states),
        portals=len(definition.portals),
        spawn_points=len(definition.spawn_points),
        zones=len(definition.zones),
        visible_cells=visible_cells,
        known_cells=known_cells,
        projection_bytes=projection_bytes,
        event_count=max(0, int(event_count)),
        path_probe_status=path_probe_status,
        path_probe_length=path_probe_length,
        path_probe_cost=path_probe_cost,
        path_probe_error=path_probe_error,
    )
    timings = GridRuntimeTimings(
        projection_ms=projection_ms,
        pathfinding_ms=pathfinding_ms,
    )
    return GridRuntimeProfile(
        map_instance_id=snapshot.map_instance_id,
        map_id=definition.map_id,
        definition_revision=definition.definition_revision,
        map_state_revision=snapshot.map_state_revision,
        observer_actor_id=(
            observer_knowledge.observer_actor_id
            if observer_knowledge is not None
            else observer_actor_id
        ),
        path_probe_actor_id=path_probe_actor_id,
        path_probe_destination=path_probe_destination,
        decision=assess_grid_renderer(metrics, timings, budget=budget),
    )


def _limit_reasons(
    checks: tuple[tuple[str, int, int], ...],
    *,
    suffix: str,
) -> list[str]:
    return [
        f"{name}_exceeds_{suffix}:{actual}>{limit}"
        for name, actual, limit in checks
        if actual > limit
    ]


def _elapsed_ms(start_ns: int) -> float:
    return max(0.0, (perf_counter_ns() - start_ns) / 1_000_000.0)
