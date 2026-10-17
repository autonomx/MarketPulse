"""Safe permanent-deletion routes for disposable RPG world projects."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Request

from app.apps.rpg.genesis.worlds.lifecycle_service import (
    delete_world_project,
    world_deletion_eligibility,
)

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgWorldDeletionEligibilityResponse,
    RpgWorldDeletionResponse,
)

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgDeleteWorldRequestBody(_TypedRequestModel):
    acknowledge_permanent: _TypedRequestAny = None
    confirmation_title: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_world_deletion_routes_registered"


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


def register_rpg_world_deletion_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get(
        "/api/rpg/worlds/{world_id}/deletion-eligibility",
        tags=["rpg-world"],
        responses=documented(RpgWorldDeletionEligibilityResponse),
    )
    def rpg_world_deletion_eligibility(world_id: str) -> dict[str, Any]:
        try:
            return world_deletion_eligibility(world_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.delete(
        "/api/rpg/worlds/{world_id}",
        tags=["rpg-world"],
        responses=documented(RpgWorldDeletionResponse),
    )
    def rpg_delete_world(world_id: str, request: Request, request_body: RpgDeleteWorldRequestBody) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        confirmation_title = str(payload.get("confirmation_title") or "")
        if not confirmation_title:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "confirmation_title_required"},
            )
        if payload.get("acknowledge_permanent") is not True:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "permanent_deletion_acknowledgement_required"},
            )
        try:
            return delete_world_project(
                world_id,
                confirmation_title=confirmation_title,
                acknowledge_permanent=True,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise
