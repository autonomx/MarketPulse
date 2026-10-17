"""World genre-profile preview, editing, validation, and approval routes."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Request

from app.apps.rpg.genesis.worlds.profile_authoring import (
    approve_world_profile_review,
    read_world_profile_review,
    update_world_profile_review,
)
from app.apps.rpg.genesis.worlds.profile_generation_jobs import retry_world_profile_creation

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgWorldProfileResponse,
)

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgUpdateWorldGenreProfileRequestBody(_TypedRequestModel):
    expected_profile_revision: _TypedRequestAny = None
    profile: _TypedRequestAny = None

class RpgApproveWorldGenreProfileRequestBody(_TypedRequestModel):
    approved_by: _TypedRequestAny = None
    expected_profile_revision: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_world_profile_routes_registered"


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


def register_rpg_world_profile_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get(
        "/api/rpg/worlds/{world_id}/genre-profile",
        tags=["rpg-world"],
        responses=documented(RpgWorldProfileResponse),
    )
    def rpg_read_world_genre_profile(world_id: str) -> dict[str, Any]:
        try:
            return read_world_profile_review(world_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.patch(
        "/api/rpg/worlds/{world_id}/genre-profile",
        tags=["rpg-world"],
        responses=documented(RpgWorldProfileResponse),
    )
    def rpg_update_world_genre_profile(
        world_id: str,
        request: Request, request_body: RpgUpdateWorldGenreProfileRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        expected_revision = int(payload.get("expected_profile_revision") or 0)
        profile = payload.get("profile")
        if expected_revision < 1:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "expected_profile_revision_required"},
            )
        if not isinstance(profile, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "genre_profile_required"},
            )
        try:
            return update_world_profile_review(
                world_id,
                expected_profile_revision=expected_revision,
                profile=profile,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/genre-profile/approve",
        tags=["rpg-world"],
        responses=documented(RpgWorldProfileResponse),
    )
    def rpg_approve_world_genre_profile(
        world_id: str,
        request: Request, request_body: RpgApproveWorldGenreProfileRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        expected_revision = int(payload.get("expected_profile_revision") or 0)
        if expected_revision < 1:
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "expected_profile_revision_required"},
            )
        try:
            return approve_world_profile_review(
                world_id,
                expected_profile_revision=expected_revision,
                approved_by=str(payload.get("approved_by") or "local-author"),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/genre-profile/retry",
        tags=["rpg-world"],
        responses=documented(RpgWorldProfileResponse),
    )
    def rpg_retry_world_genre_profile(world_id: str) -> dict[str, Any]:
        try:
            return retry_world_profile_creation(world_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise
