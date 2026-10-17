"""Hidden gateway routes for campaign-owned geometry patch events."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Request
from pydantic import ValidationError

from app.apps.rpg.world.map_geometry_patch import ApplyGeometryPatchCommand
from app.apps.rpg.world.map_geometry_patch_service import apply_campaign_geometry_patch
from app.apps.rpg.edge.api.route_contracts import (
    RpgGeometryPatchResponse,
)
from app.apps.rpg.edge.api.web_contracts import documented

RpgApplyGeometryPatchRequestBody = ApplyGeometryPatchCommand


_ROUTE_SENTINEL = "_omnix_rpg_geometry_patch_routes_registered"


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


def register_rpg_geometry_patch_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.post(
        "/api/rpg/map-instances/{map_instance_id}/geometry-patches",
        responses=documented(RpgGeometryPatchResponse),
    )
    def rpg_apply_geometry_patch(
        map_instance_id: str,
        request: Request, request_body: RpgApplyGeometryPatchRequestBody,
    ) -> dict[str, Any]:
        try:
            command = ApplyGeometryPatchCommand.model_validate(
                _body(request_body.model_dump(exclude_unset=True, by_alias=True))
            )
            event, snapshot = apply_campaign_geometry_patch(
                map_instance_id,
                command,
            )
            return {
                "ok": True,
                "event": event.model_dump(mode="json"),
                "snapshot": snapshot.model_dump(mode="json"),
            }
        except Exception as exc:
            _raise_domain_error(exc)
            raise
