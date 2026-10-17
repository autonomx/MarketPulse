"""Hidden gateway routes for predictive deferred-map materialization."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Query, Request

from app.apps.rpg.genesis.worlds.progressive_materialization import materialize_deferred_location
from app.apps.rpg.genesis.worlds.progressive_materialization_job_service import (
    materialization_job_telemetry,
    schedule_campaign_predictive_materialization,
    schedule_predictive_materialization,
)

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgDeferredMaterializationResponse,
)
from app.apps.rpg.edge.api.route_contracts import (
    RpgMaterializationScheduleResponse,
    RpgMaterializationTelemetryResponse,
)

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgMaterializeDeferredLocationRequestBody(_TypedRequestModel):
    source_world_revision: _TypedRequestAny = None

class RpgScheduleWorldMaterializationRequestBody(_TypedRequestModel):
    current_location_id: _TypedRequestAny = None
    kick_worker: bool | None = True
    minimum_score: float | None = 0.35
    route_intent_location_id: _TypedRequestAny = None
    source_world_revision: _TypedRequestAny = None

class RpgScheduleCampaignMaterializationRequestBody(_TypedRequestModel):
    current_location_id: _TypedRequestAny = None
    kick_worker: bool | None = True
    minimum_score: float | None = 0.35
    route_intent_location_id: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_progressive_map_routes_registered"


def _body(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "request_body_must_be_object"},
        )
    return value


def _raise_domain_error(exc: Exception) -> None:
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


def register_rpg_progressive_map_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.post(
        "/api/rpg/worlds/{world_id}/deferred-locations/{location_id}/materialize",
        responses=documented(RpgDeferredMaterializationResponse),
    )
    def rpg_materialize_deferred_location(
        world_id: str,
        location_id: str,
        request: Request, request_body: RpgMaterializeDeferredLocationRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return materialize_deferred_location(
                world_id=world_id,
                source_world_revision=int(payload.get("source_world_revision") or 0),
                location_id=location_id,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/materialization-jobs/schedule",
        responses=documented(RpgMaterializationScheduleResponse),
    )
    def rpg_schedule_world_materialization(
        world_id: str,
        request: Request, request_body: RpgScheduleWorldMaterializationRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        current_location_id = str(payload.get("current_location_id") or "").strip()
        if not current_location_id:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "current_location_id_required"},
            )
        try:
            return schedule_predictive_materialization(
                world_id=world_id,
                source_world_revision=int(payload.get("source_world_revision") or 0),
                current_location_id=current_location_id,
                route_intent_location_id=(
                    str(payload["route_intent_location_id"])
                    if payload.get("route_intent_location_id")
                    else None
                ),
                minimum_score=float(payload.get("minimum_score", 0.35)),
                kick_worker=bool(payload.get("kick_worker", True)),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/campaigns/{campaign_id}/materialization-signals",
        responses=documented(RpgMaterializationScheduleResponse),
    )
    def rpg_schedule_campaign_materialization(
        campaign_id: str,
        request: Request, request_body: RpgScheduleCampaignMaterializationRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        current_location_id = str(payload.get("current_location_id") or "").strip()
        if not current_location_id:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "current_location_id_required"},
            )
        try:
            return schedule_campaign_predictive_materialization(
                campaign_id,
                current_location_id=current_location_id,
                route_intent_location_id=(
                    str(payload["route_intent_location_id"])
                    if payload.get("route_intent_location_id")
                    else None
                ),
                minimum_score=float(payload.get("minimum_score", 0.35)),
                kick_worker=bool(payload.get("kick_worker", True)),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.get(
        "/api/rpg/worlds/{world_id}/materialization-jobs",
        responses=documented(RpgMaterializationTelemetryResponse),
    )
    def rpg_materialization_telemetry(
        world_id: str,
        source_world_revision: int | None = Query(default=None, ge=1),
    ) -> dict[str, Any]:
        return materialization_job_telemetry(
            world_id=world_id,
            source_world_revision=source_world_revision,
        )
