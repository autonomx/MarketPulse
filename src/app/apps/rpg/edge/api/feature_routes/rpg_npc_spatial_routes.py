"""Hidden gateway routes for durable campaign NPC spatial simulation."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Literal, Mapping

from fastapi import HTTPException, Query, Request
from pydantic import ValidationError

from app.apps.rpg.world.npc_spatial_campaign_authoring import (
    configure_campaign_spatial_policy,
    read_campaign_spatial_state,
    save_campaign_spatial_goal,
    save_campaign_spatial_routine,
)
from app.apps.rpg.world.npc_spatial_campaign_contracts import (
    CampaignNpcSpatialGoal,
    CampaignNpcSpatialPolicy,
    CampaignNpcSpatialRoutine,
    CampaignSpatialTickRequest,
    NpcSpatialRoutineStep,
)
from app.apps.rpg.world.npc_spatial_campaign_runtime import advance_campaign_spatial_tick

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict, Field as _typed_field
from app.apps.rpg.edge.api.route_contracts import (
    RpgSpatialClockResponse,
    RpgSpatialGoalResponse,
    RpgSpatialRoutineResponse,
    RpgSpatialStateResponse,
    RpgSpatialTickResponse,
)
from app.apps.rpg.edge.api.web_contracts import documented

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgSaveCampaignSpatialGoalRequestBody(_TypedRequestModel):
    goal_id: str = _typed_field(min_length=1)
    goal_revision: int = _typed_field(default=1, ge=1)
    actor_id: str = _typed_field(min_length=1)
    map_instance_id: str = _typed_field(min_length=1)
    goal_type: Literal["move_to_cell", "transition_via_portal"]
    target_cell: tuple[int, int] | None = None
    portal_id: str | None = None
    target_map_instance_id: str | None = None
    priority: int = 0
    issued_tick: int = _typed_field(default=0, ge=0)
    not_before_tick: int = _typed_field(default=0, ge=0)
    expires_after_tick: int | None = _typed_field(default=None, ge=0)
    status: Literal["active", "completed", "blocked", "canceled", "expired"] = "active"
    routine_id: str | None = None
    blocked_attempts: int = _typed_field(default=0, ge=0)
    last_decision: dict[str, Any] = _typed_field(default_factory=dict)
    metadata: dict[str, Any] = _typed_field(default_factory=dict)
    expected_revision: int = _typed_field(default=0, ge=0)

class RpgSaveCampaignSpatialRoutineRequestBody(_TypedRequestModel):
    routine_id: str = _typed_field(min_length=1)
    routine_revision: int = _typed_field(default=1, ge=1)
    actor_id: str = _typed_field(min_length=1)
    enabled: bool = True
    interval_ticks: int = _typed_field(default=1, ge=1)
    steps: tuple[NpcSpatialRoutineStep, ...] = _typed_field(min_length=1)
    next_step_index: int = _typed_field(default=0, ge=0)
    emission_count: int = _typed_field(default=0, ge=0)
    next_due_tick: int = _typed_field(default=0, ge=0)
    last_issued_tick: int | None = _typed_field(default=None, ge=0)
    metadata: dict[str, Any] = _typed_field(default_factory=dict)
    expected_revision: int = _typed_field(default=0, ge=0)

class RpgConfigureCampaignSpatialPolicyRequestBody(_TypedRequestModel):
    expected_world_tick: int = _typed_field(ge=0)
    active_actor_budget: int = _typed_field(default=16, ge=1)
    coarse_actor_budget: int = _typed_field(default=4, ge=1)
    coarse_tick_interval: int = _typed_field(default=5, ge=1)
    transition_actor_budget: int = _typed_field(default=4, ge=1)
    max_blocked_attempts: int = _typed_field(default=3, ge=1)

class RpgAdvanceCampaignSpatialTickRequestBody(_TypedRequestModel):
    expected_world_tick: int = _typed_field(ge=0)
    active_map_instance_ids: tuple[str, ...] = ()
    coarse_map_instance_ids: tuple[str, ...] = ()


_ROUTE_SENTINEL = "_omnix_rpg_npc_spatial_routes_registered"


def _body(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "request_body_must_be_object"},
        )
    return value


def _raise_domain_error(exc: Exception) -> None:
    if isinstance(exc, ValidationError):
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": str(exc).strip("'")},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=409,
            detail={"ok": False, "error": str(exc)},
        ) from exc
    raise exc


def register_rpg_npc_spatial_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.post(
        "/api/rpg/campaigns/{campaign_id}/spatial-goals",
        responses=documented(RpgSpatialGoalResponse),
    )
    def rpg_save_campaign_spatial_goal(
        campaign_id: str,
        request: Request, request_body: RpgSaveCampaignSpatialGoalRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        payload["campaign_id"] = campaign_id
        expected_revision = int(payload.pop("expected_revision", 0))
        try:
            goal = CampaignNpcSpatialGoal.model_validate(payload)
            return save_campaign_spatial_goal(
                goal,
                expected_revision=expected_revision,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/campaigns/{campaign_id}/spatial-routines",
        responses=documented(RpgSpatialRoutineResponse),
    )
    def rpg_save_campaign_spatial_routine(
        campaign_id: str,
        request: Request, request_body: RpgSaveCampaignSpatialRoutineRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        payload["campaign_id"] = campaign_id
        expected_revision = int(payload.pop("expected_revision", 0))
        try:
            routine = CampaignNpcSpatialRoutine.model_validate(payload)
            return save_campaign_spatial_routine(
                routine,
                expected_revision=expected_revision,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/campaigns/{campaign_id}/spatial-policy",
        responses=documented(RpgSpatialClockResponse),
    )
    def rpg_configure_campaign_spatial_policy(
        campaign_id: str,
        request: Request, request_body: RpgConfigureCampaignSpatialPolicyRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        expected_world_tick = int(payload.pop("expected_world_tick", -1))
        if expected_world_tick < 0:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "expected_world_tick_required"},
            )
        try:
            policy = CampaignNpcSpatialPolicy.model_validate(payload)
            return configure_campaign_spatial_policy(
                campaign_id,
                policy,
                expected_world_tick=expected_world_tick,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/campaigns/{campaign_id}/spatial-ticks",
        responses=documented(RpgSpatialTickResponse),
    )
    def rpg_advance_campaign_spatial_tick(
        campaign_id: str,
        request: Request, request_body: RpgAdvanceCampaignSpatialTickRequestBody,
    ) -> dict[str, Any]:
        try:
            tick_request = CampaignSpatialTickRequest.model_validate(
                _body(request_body.model_dump(exclude_unset=True, by_alias=True))
            )
            return advance_campaign_spatial_tick(campaign_id, tick_request)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.get(
        "/api/rpg/campaigns/{campaign_id}/spatial-state",
        responses=documented(RpgSpatialStateResponse),
    )
    def rpg_read_campaign_spatial_state(
        campaign_id: str,
        tick_limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        try:
            return read_campaign_spatial_state(
                campaign_id,
                tick_limit=tick_limit,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise
