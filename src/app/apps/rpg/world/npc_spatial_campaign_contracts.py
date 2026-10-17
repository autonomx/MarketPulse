"""Durable campaign contracts for living NPC spatial simulation."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.apps.rpg.world.map_grid_contracts import GridPoint
from app.apps.rpg.world.npc_spatial_simulation import NpcSpatialGoal, NpcSpatialSimulationPolicy

SpatialGoalType = Literal["move_to_cell", "transition_via_portal"]
SpatialGoalStatus = Literal["active", "completed", "blocked", "canceled", "expired"]


class FrozenCampaignSpatialModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CampaignNpcSpatialPolicy(FrozenCampaignSpatialModel):
    active_actor_budget: int = Field(default=16, ge=1)
    coarse_actor_budget: int = Field(default=4, ge=1)
    coarse_tick_interval: int = Field(default=5, ge=1)
    transition_actor_budget: int = Field(default=4, ge=1)
    max_blocked_attempts: int = Field(default=3, ge=1)

    def movement_policy(self) -> NpcSpatialSimulationPolicy:
        return NpcSpatialSimulationPolicy(
            active_actor_budget=self.active_actor_budget,
            coarse_actor_budget=self.coarse_actor_budget,
            coarse_tick_interval=self.coarse_tick_interval,
        )


class CampaignNpcSpatialGoal(FrozenCampaignSpatialModel):
    goal_id: str = Field(min_length=1)
    goal_revision: int = Field(default=1, ge=1)
    campaign_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    goal_type: SpatialGoalType
    target_cell: GridPoint | None = None
    portal_id: str | None = None
    target_map_instance_id: str | None = None
    priority: int = 0
    issued_tick: int = Field(default=0, ge=0)
    not_before_tick: int = Field(default=0, ge=0)
    expires_after_tick: int | None = Field(default=None, ge=0)
    status: SpatialGoalStatus = "active"
    routine_id: str | None = None
    blocked_attempts: int = Field(default=0, ge=0)
    last_decision: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = Field(default=None, exclude=True)
    updated_at: str | None = Field(default=None, exclude=True)
    completed_at: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_goal_shape(self) -> "CampaignNpcSpatialGoal":
        if (
            self.expires_after_tick is not None
            and self.expires_after_tick < self.not_before_tick
        ):
            raise ValueError("npc_spatial_goal_expiry_before_start")
        if self.goal_type == "move_to_cell":
            if self.target_cell is None:
                raise ValueError("npc_spatial_move_target_required")
            if self.portal_id is not None or self.target_map_instance_id is not None:
                raise ValueError("npc_spatial_move_transition_fields_forbidden")
        elif (
            not self.portal_id
            or not self.target_map_instance_id
            or self.target_cell is not None
        ):
            raise ValueError("npc_spatial_transition_fields_required")
        return self

    def is_available(self, world_tick: int) -> bool:
        if self.status != "active" or world_tick < self.not_before_tick:
            return False
        return self.expires_after_tick is None or world_tick <= self.expires_after_tick

    def movement_goal(self) -> NpcSpatialGoal:
        if self.goal_type != "move_to_cell" or self.target_cell is None:
            raise ValueError("npc_spatial_goal_not_movement")
        return NpcSpatialGoal(
            goal_id=self.goal_id,
            goal_revision=self.goal_revision,
            actor_id=self.actor_id,
            map_instance_id=self.map_instance_id,
            target_cell=self.target_cell,
            priority=self.priority,
            issued_tick=self.issued_tick,
            not_before_tick=self.not_before_tick,
            expires_after_tick=self.expires_after_tick,
        )


class NpcSpatialRoutineStep(FrozenCampaignSpatialModel):
    step_id: str = Field(min_length=1)
    map_instance_id: str = Field(min_length=1)
    goal_type: SpatialGoalType
    target_cell: GridPoint | None = None
    portal_id: str | None = None
    target_map_instance_id: str | None = None
    priority: int = 0
    expires_after_ticks: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_step_shape(self) -> "NpcSpatialRoutineStep":
        CampaignNpcSpatialGoal(
            goal_id=f"routine-step:{self.step_id}",
            campaign_id="validation",
            actor_id="validation",
            map_instance_id=self.map_instance_id,
            goal_type=self.goal_type,
            target_cell=self.target_cell,
            portal_id=self.portal_id,
            target_map_instance_id=self.target_map_instance_id,
        )
        return self


class CampaignNpcSpatialRoutine(FrozenCampaignSpatialModel):
    routine_id: str = Field(min_length=1)
    routine_revision: int = Field(default=1, ge=1)
    campaign_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    enabled: bool = True
    interval_ticks: int = Field(default=1, ge=1)
    steps: tuple[NpcSpatialRoutineStep, ...] = Field(min_length=1)
    next_step_index: int = Field(default=0, ge=0)
    emission_count: int = Field(default=0, ge=0)
    next_due_tick: int = Field(default=0, ge=0)
    last_issued_tick: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = Field(default=None, exclude=True)
    updated_at: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_next_step(self) -> "CampaignNpcSpatialRoutine":
        if self.next_step_index >= len(self.steps):
            raise ValueError("npc_spatial_routine_step_index_out_of_range")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("duplicate_npc_spatial_routine_step_id")
        return self

    def emitted_goal(self, world_tick: int) -> CampaignNpcSpatialGoal:
        step = self.steps[self.next_step_index]
        expires = (
            world_tick + step.expires_after_ticks
            if step.expires_after_ticks is not None
            else None
        )
        return CampaignNpcSpatialGoal(
            goal_id=(
                f"routine:{self.routine_id}:emission:{self.emission_count + 1}:"
                f"step:{step.step_id}"
            ),
            campaign_id=self.campaign_id,
            actor_id=self.actor_id,
            map_instance_id=step.map_instance_id,
            goal_type=step.goal_type,
            target_cell=step.target_cell,
            portal_id=step.portal_id,
            target_map_instance_id=step.target_map_instance_id,
            priority=step.priority,
            issued_tick=world_tick,
            not_before_tick=world_tick,
            expires_after_tick=expires,
            routine_id=self.routine_id,
            metadata={"routine_step_id": step.step_id, **step.metadata},
        )


class CampaignSpatialTickRequest(FrozenCampaignSpatialModel):
    expected_world_tick: int = Field(ge=0)
    active_map_instance_ids: tuple[str, ...] = ()
    coarse_map_instance_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_tiers(self) -> "CampaignSpatialTickRequest":
        overlap = set(self.active_map_instance_ids) & set(self.coarse_map_instance_ids)
        if overlap:
            raise ValueError("npc_spatial_context_tier_overlap")
        return self
