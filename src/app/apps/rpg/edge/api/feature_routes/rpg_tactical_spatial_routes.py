"""Hidden tactical movement and attack routes for campaign map instances."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Literal, Mapping

from fastapi import HTTPException, Request
from pydantic import ValidationError

from app.apps.rpg.world.tactical_spatial import (
    TacticalAttackCommand,
    TacticalMoveCommand,
    TacticalSpatialError,
    TacticalSpatialPolicy,
)
from app.apps.rpg.world.tactical_spatial_service import attack_tactically, move_actor_tactically

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict, Field as _typed_field
from app.apps.rpg.edge.api.route_contracts import (
    RpgTacticalResponse,
)
from app.apps.rpg.edge.api.web_contracts import documented

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgTacticalMoveRequestBody(_TypedRequestModel):
    submission_id: str = _typed_field(min_length=1)
    command_id: str = _typed_field(min_length=1)
    actor_id: str = _typed_field(min_length=1)
    destination: tuple[int, int]
    expected_map_state_revision: int = _typed_field(ge=0)
    expected_campaign_revision: int = _typed_field(ge=0)
    policy: TacticalSpatialPolicy | None = None

class RpgTacticalAttackRequestBody(_TypedRequestModel):
    submission_id: str = _typed_field(min_length=1)
    command_id: str = _typed_field(min_length=1)
    actor_id: str = _typed_field(min_length=1)
    target_id: str = _typed_field(min_length=1)
    action_type: Literal["melee_attack", "ranged_attack", "unarmed_attack"] = "melee_attack"
    expected_campaign_revision: int = _typed_field(ge=0)
    expected_map_state_revision: int = _typed_field(ge=0)
    policy: TacticalSpatialPolicy | None = None


_ROUTE_SENTINEL = "_omnix_rpg_tactical_spatial_routes_registered"


def _body(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "request_body_must_be_object"},
        )
    return value


def _policy(value: object) -> TacticalSpatialPolicy | None:
    return TacticalSpatialPolicy.model_validate(value) if isinstance(value, Mapping) else None


def _raise_domain_error(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": str(exc).strip("'")},
        ) from exc
    if isinstance(exc, (TacticalSpatialError, ValueError)):
        raise HTTPException(
            status_code=409,
            detail={"ok": False, "error": str(exc)},
        ) from exc
    raise exc


def register_rpg_tactical_spatial_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.post(
        "/api/rpg/map-instances/{map_instance_id}/tactical/move",
        tags=["rpg-tactical-spatial"],
        responses=documented(RpgTacticalResponse),
    )
    def rpg_tactical_move(
        map_instance_id: str,
        request: Request, request_body: RpgTacticalMoveRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        raw_policy = payload.pop("policy", None)
        try:
            command = TacticalMoveCommand.model_validate(payload)
            return move_actor_tactically(
                map_instance_id,
                command,
                policy=_policy(raw_policy),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/map-instances/{map_instance_id}/tactical/attack",
        tags=["rpg-tactical-spatial"],
        responses=documented(RpgTacticalResponse),
    )
    def rpg_tactical_attack(
        map_instance_id: str,
        request: Request, request_body: RpgTacticalAttackRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        raw_policy = payload.pop("policy", None)
        expected_map_state_revision = payload.pop("expected_map_state_revision", None)
        if expected_map_state_revision is None:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "expected_map_state_revision_required"},
            )
        try:
            command = TacticalAttackCommand.model_validate(payload)
            return attack_tactically(
                map_instance_id,
                command,
                expected_map_state_revision=int(expected_map_state_revision),
                policy=_policy(raw_policy),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except Exception as exc:
            _raise_domain_error(exc)
            raise
