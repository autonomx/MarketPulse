"""Hidden measured performance route for campaign grid map instances."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Request
from pydantic import ValidationError

from app.apps.rpg.world.grid_runtime_performance import GridRuntimeBudget
from app.apps.rpg.world.grid_runtime_performance_service import profile_campaign_grid_runtime

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.apps.rpg.edge.api.route_contracts import (
    RpgGridPerformanceProfileResponse,
)
from app.apps.rpg.edge.api.web_contracts import documented

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgGridPerformanceProfileRequestBody(_TypedRequestModel):
    budget: _TypedRequestAny = None
    observer_actor_id: _TypedRequestAny = None
    path_probe_actor_id: _TypedRequestAny = None
    path_probe_destination: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_grid_performance_routes_registered"


def _body(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "request_body_must_be_object"},
        )
    return value


def register_rpg_grid_performance_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.post(
        "/api/rpg/map-instances/{map_instance_id}/performance-profile",
        tags=["rpg-grid-performance"],
        responses=documented(RpgGridPerformanceProfileResponse),
    )
    def rpg_grid_performance_profile(
        map_instance_id: str,
        request: Request, request_body: RpgGridPerformanceProfileRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        destination = payload.get("path_probe_destination")
        if destination is not None:
            if not isinstance(destination, (list, tuple)) or len(destination) != 2:
                raise HTTPException(
                    status_code=422,
                    detail={"ok": False, "error": "path_probe_destination_invalid"},
                )
            path_probe_destination = (int(destination[0]), int(destination[1]))
        else:
            path_probe_destination = None
        try:
            budget = (
                GridRuntimeBudget.model_validate(payload["budget"])
                if isinstance(payload.get("budget"), Mapping)
                else None
            )
            return profile_campaign_grid_runtime(
                map_instance_id,
                observer_actor_id=str(payload.get("observer_actor_id") or ""),
                path_probe_actor_id=str(payload.get("path_probe_actor_id") or ""),
                path_probe_destination=path_probe_destination,
                budget=budget,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"ok": False, "error": str(exc).strip("'")},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"ok": False, "error": str(exc)},
            ) from exc
