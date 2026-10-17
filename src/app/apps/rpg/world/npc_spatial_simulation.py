"""Deterministic level-of-detail scheduling for living NPC map goals.

The scheduler deliberately reuses the authoritative map movement boundary. It
chooses when an NPC may act, then emits the same fully resolved movement events
used by direct commands. Replay therefore applies recorded events and never
reruns this scheduler, AI, or pathfinding.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Final, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.apps.rpg.world.map_grid_contracts import GridMapDefinition, GridPoint
from app.apps.rpg.world.map_instance_runtime import (
    ActorMovedEvent,
    CampaignMapInstanceSnapshot,
    MapMovementError,
    MoveActorCommand,
    replay_map_events,
    resolve_move_command,
)

NPC_SPATIAL_SIMULATION_VERSION: Final = 1
SpatialSimulationTier = Literal["active", "coarse", "dormant"]
SpatialDecisionStatus = Literal[
    "moved",
    "completed",
    "deferred_cadence",
    "deferred_budget",
    "dormant",
    "blocked",
    "already_applied",
]


class FrozenSpatialModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NpcSpatialGoal(FrozenSpatialModel):
    goal_id: str = Field(min_length=1)
    goal_revision: int = Field(default=1, ge=1)
    actor_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    goal_type: Literal["move_to_cell"] = "move_to_cell"
    target_cell: GridPoint
    priority: int = 0
    issued_tick: int = Field(default=0, ge=0)
    not_before_tick: int = Field(default=0, ge=0)
    expires_after_tick: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_ticks(self) -> "NpcSpatialGoal":
        if (
            self.expires_after_tick is not None
            and self.expires_after_tick < self.not_before_tick
        ):
            raise ValueError("npc_spatial_goal_expiry_before_start")
        return self

    def is_available(self, world_tick: int) -> bool:
        if world_tick < self.not_before_tick:
            return False
        return self.expires_after_tick is None or world_tick <= self.expires_after_tick


class NpcSpatialSimulationContext(FrozenSpatialModel):
    active_map_instance_ids: tuple[str, ...] = ()
    coarse_map_instance_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def disjoint_tiers(self) -> "NpcSpatialSimulationContext":
        overlap = set(self.active_map_instance_ids) & set(self.coarse_map_instance_ids)
        if overlap:
            raise ValueError("npc_spatial_context_tier_overlap")
        return self

    def tier_for(self, map_instance_id: str) -> SpatialSimulationTier:
        if map_instance_id in self.active_map_instance_ids:
            return "active"
        if map_instance_id in self.coarse_map_instance_ids:
            return "coarse"
        return "dormant"


class NpcSpatialSimulationPolicy(FrozenSpatialModel):
    simulation_version: Literal[1] = NPC_SPATIAL_SIMULATION_VERSION
    active_actor_budget: int = Field(default=16, ge=1)
    coarse_actor_budget: int = Field(default=4, ge=1)
    coarse_tick_interval: int = Field(default=5, ge=1)

    def actor_budget(self, tier: SpatialSimulationTier) -> int:
        if tier == "active":
            return self.active_actor_budget
        if tier == "coarse":
            return self.coarse_actor_budget
        return 0


class NpcSpatialDecision(FrozenSpatialModel):
    actor_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    tier: SpatialSimulationTier
    status: SpatialDecisionStatus
    command_id: str | None = None
    event_id: str | None = None
    error_code: str | None = None


class NpcSpatialTickResult(FrozenSpatialModel):
    world_tick: int = Field(ge=0)
    tier: SpatialSimulationTier
    simulation_version: Literal[1] = NPC_SPATIAL_SIMULATION_VERSION
    snapshot: CampaignMapInstanceSnapshot
    events: tuple[ActorMovedEvent, ...] = ()
    decisions: tuple[NpcSpatialDecision, ...] = ()

    def replay(self, initial: CampaignMapInstanceSnapshot) -> CampaignMapInstanceSnapshot:
        return replay_map_events(initial, self.events)


def advance_npc_spatial_tick(
    definition: GridMapDefinition,
    snapshot: CampaignMapInstanceSnapshot,
    goals: Iterable[NpcSpatialGoal],
    *,
    world_tick: int,
    context: NpcSpatialSimulationContext,
    policy: NpcSpatialSimulationPolicy | None = None,
) -> NpcSpatialTickResult:
    """Advance at most one selected movement goal per scheduled NPC.

    Selection is stable by actor id, then goal priority, issue tick, and goal id.
    Active maps are evaluated every tick. Coarse maps are evaluated only on the
    configured cadence and with a smaller actor budget. Dormant maps do no map
    mutation work.
    """

    effective_policy = policy or NpcSpatialSimulationPolicy()
    tier = context.tier_for(snapshot.map_instance_id)
    selected = _selected_goals(snapshot, goals, world_tick)
    if not selected:
        return NpcSpatialTickResult(
            world_tick=world_tick,
            tier=tier,
            snapshot=snapshot,
        )

    if tier == "dormant":
        return NpcSpatialTickResult(
            world_tick=world_tick,
            tier=tier,
            snapshot=snapshot,
            decisions=tuple(
                _decision(goal, tier=tier, status="dormant")
                for goal in selected
            ),
        )

    if tier == "coarse" and world_tick % effective_policy.coarse_tick_interval:
        return NpcSpatialTickResult(
            world_tick=world_tick,
            tier=tier,
            snapshot=snapshot,
            decisions=tuple(
                _decision(goal, tier=tier, status="deferred_cadence")
                for goal in selected
            ),
        )

    current = snapshot
    events: list[ActorMovedEvent] = []
    decisions: list[NpcSpatialDecision] = []
    budget = effective_policy.actor_budget(tier)
    for index, goal in enumerate(selected):
        if index >= budget:
            decisions.append(_decision(goal, tier=tier, status="deferred_budget"))
            continue
        actor = current.actor(goal.actor_id)
        if actor.cell == goal.target_cell:
            decisions.append(_decision(goal, tier=tier, status="completed"))
            continue
        command_id = _command_id(current, goal, world_tick)
        if command_id in current.applied_command_ids:
            decisions.append(
                _decision(
                    goal,
                    tier=tier,
                    status="already_applied",
                    command_id=command_id,
                )
            )
            continue
        try:
            event, current = resolve_move_command(
                definition,
                current,
                MoveActorCommand(
                    command_id=command_id,
                    actor_id=goal.actor_id,
                    destination=goal.target_cell,
                    expected_map_state_revision=current.map_state_revision,
                ),
            )
        except MapMovementError as exc:
            decisions.append(
                _decision(
                    goal,
                    tier=tier,
                    status="blocked",
                    command_id=command_id,
                    error_code=exc.code,
                )
            )
            continue
        events.append(event)
        decisions.append(
            _decision(
                goal,
                tier=tier,
                status="moved",
                command_id=command_id,
                event_id=event.event_id,
            )
        )

    return NpcSpatialTickResult(
        world_tick=world_tick,
        tier=tier,
        snapshot=current,
        events=tuple(events),
        decisions=tuple(decisions),
    )


def _selected_goals(
    snapshot: CampaignMapInstanceSnapshot,
    goals: Iterable[NpcSpatialGoal],
    world_tick: int,
) -> tuple[NpcSpatialGoal, ...]:
    actor_ids = {actor.actor_id for actor in snapshot.actors}
    by_actor: dict[str, list[NpcSpatialGoal]] = defaultdict(list)
    for goal in goals:
        if goal.map_instance_id != snapshot.map_instance_id:
            continue
        if goal.actor_id not in actor_ids or not goal.is_available(world_tick):
            continue
        by_actor[goal.actor_id].append(goal)

    selected: list[NpcSpatialGoal] = []
    for actor_id in sorted(by_actor):
        actor_goals = sorted(
            by_actor[actor_id],
            key=lambda row: (-row.priority, row.issued_tick, row.goal_id),
        )
        selected.append(actor_goals[0])
    return tuple(selected)


def _command_id(
    snapshot: CampaignMapInstanceSnapshot,
    goal: NpcSpatialGoal,
    world_tick: int,
) -> str:
    return (
        f"npc-spatial:{snapshot.map_instance_id}:{world_tick}:"
        f"{goal.actor_id}:{goal.goal_id}:r{goal.goal_revision}"
    )


def _decision(
    goal: NpcSpatialGoal,
    *,
    tier: SpatialSimulationTier,
    status: SpatialDecisionStatus,
    command_id: str | None = None,
    event_id: str | None = None,
    error_code: str | None = None,
) -> NpcSpatialDecision:
    return NpcSpatialDecision(
        actor_id=goal.actor_id,
        goal_id=goal.goal_id,
        tier=tier,
        status=status,
        command_id=command_id,
        event_id=event_id,
        error_code=error_code,
    )
