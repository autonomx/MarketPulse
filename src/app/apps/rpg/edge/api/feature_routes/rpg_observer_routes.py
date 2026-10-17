"""Hidden gateway routes for observer knowledge and safe map projections."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Query, Request
from pydantic import ValidationError

from app.apps.rpg.world.map_observer_runtime import ObserverPerceptionPolicy
from app.apps.rpg.world.map_observer_service import (
    load_campaign_observer_projection,
    observe_campaign_map,
)

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict, Field as _typed_field
from app.apps.rpg.edge.api.route_contracts import (
    RpgObserveMapResponse,
    RpgObserverProjectionResponse,
)
from app.apps.rpg.edge.api.web_contracts import documented

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgObserveMapRequestBody(_TypedRequestModel):
    sight_radius: int | None = _typed_field(default=None, ge=1, le=128)
    detection_radius: int | None = _typed_field(default=None, ge=0, le=128)
    remember_terrain: bool | None = None
    expected_knowledge_revision: int | None = _typed_field(default=None, ge=0)


_ROUTE_SENTINEL = "_omnix_rpg_observer_routes_registered"


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


def register_rpg_observer_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.post(
        "/api/rpg/map-instances/{map_instance_id}/observers/{observer_actor_id}/observe",
        responses=documented(RpgObserveMapResponse),
    )
    def rpg_observe_map(
        map_instance_id: str,
        observer_actor_id: str,
        request: Request, request_body: RpgObserveMapRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        expected = payload.pop("expected_knowledge_revision", None)
        try:
            policy = ObserverPerceptionPolicy.model_validate(payload)
            return observe_campaign_map(
                map_instance_id,
                observer_actor_id=observer_actor_id,
                policy=policy,
                expected_knowledge_revision=(
                    int(expected) if expected is not None else None
                ),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.get(
        "/api/rpg/map-instances/{map_instance_id}/observers/{observer_actor_id}/projection",
        responses=documented(RpgObserverProjectionResponse),
    )
    def rpg_observer_projection(
        map_instance_id: str,
        observer_actor_id: str,
        _known_revision: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        try:
            return load_campaign_observer_projection(
                map_instance_id,
                observer_actor_id=observer_actor_id,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise
