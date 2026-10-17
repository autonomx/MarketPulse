"""World-authoring image target, generation, and review routes."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Request

from app.apps.rpg.genesis.worlds.profile_aware_world_images import (
    generate_world_images,
    read_world_image_targets,
    regenerate_world_image_prompts,
    update_world_image_target,
)

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.prompts import prompt_template
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgWorldImageTargetsResponse,
)
from app.apps.rpg.edge.api.route_contracts import (
    RpgWorldImageGenerationResponse,
    RpgWorldImagePromptsResponse,
)

_PROMPT_1 = prompt_template('rpg.api_feature_routes_rpg_world_image_routes.rpg_regenerate_world_image_prompts', "1", "/api/rpg/worlds/{world_id}/image-prompts/regenerate")

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgWorldImageGenerationRequestBody(_TypedRequestModel):
    height: _TypedRequestAny = None
    no_cache: bool | None = False
    prompts: _TypedRequestAny = None
    provider_id: _TypedRequestAny = None
    style: _TypedRequestAny = None
    target_ids: _TypedRequestAny = None
    width: _TypedRequestAny = None

class RpgRegenerateWorldImagePromptsRequestBody(_TypedRequestModel):
    target_ids: _TypedRequestAny = None

class RpgUpdateWorldImageTargetRequestBody(_TypedRequestModel):
    active_asset_id: _TypedRequestAny = None
    review_state: _TypedRequestAny = None
    suggested_prompt: _TypedRequestAny = None

class RpgRegenerateWorldImageTargetRequestBody(_TypedRequestModel):
    height: _TypedRequestAny = None
    no_cache: bool | None = True
    prompt: _TypedRequestAny = None
    provider_id: _TypedRequestAny = None
    style: _TypedRequestAny = None
    width: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_world_image_routes_registered"


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


def register_rpg_world_image_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get(
        "/api/rpg/worlds/{world_id}/image-targets",
        tags=["rpg-world"],
        responses=documented(RpgWorldImageTargetsResponse),
    )
    def rpg_world_image_targets(world_id: str) -> dict[str, Any]:
        try:
            return read_world_image_targets(world_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/image-generation",
        tags=["rpg-world"],
        responses=documented(RpgWorldImageGenerationResponse),
    )
    def rpg_world_image_generation(
        world_id: str,
        request: Request, request_body: RpgWorldImageGenerationRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        target_ids = [
            str(value)
            for value in payload.get("target_ids") or ()
            if str(value).strip()
        ]
        prompts = payload.get("prompts")
        try:
            return generate_world_images(
                world_id,
                target_ids=target_ids,
                prompts=prompts if isinstance(prompts, Mapping) else {},
                provider_id=str(payload.get("provider_id") or ""),
                width=max(64, int(payload.get("width") or 768)),
                height=max(64, int(payload.get("height") or 768)),
                style=str(payload.get("style") or ""),
                no_cache=bool(payload.get("no_cache", False)),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        _PROMPT_1.text,
        tags=["rpg-world"],
        responses=documented(RpgWorldImagePromptsResponse),
    )
    def rpg_regenerate_world_image_prompts(
        world_id: str,
        request: Request, request_body: RpgRegenerateWorldImagePromptsRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        target_ids_value = payload.get("target_ids")
        if target_ids_value is not None and not isinstance(target_ids_value, list):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "target_ids_must_be_array"},
            )
        target_ids = (
            [str(value) for value in target_ids_value if str(value).strip()]
            if target_ids_value is not None
            else None
        )
        try:
            return regenerate_world_image_prompts(world_id, target_ids=target_ids)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.patch(
        "/api/rpg/worlds/{world_id}/image-targets/{target_id:path}",
        tags=["rpg-world"],
        responses=documented(RpgWorldImageTargetsResponse),
    )
    def rpg_update_world_image_target(
        world_id: str,
        target_id: str,
        request: Request, request_body: RpgUpdateWorldImageTargetRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        try:
            return update_world_image_target(
                world_id,
                target_id,
                review_state=(
                    str(payload.get("review_state"))
                    if payload.get("review_state") is not None
                    else None
                ),
                active_asset_id=(
                    str(payload.get("active_asset_id"))
                    if payload.get("active_asset_id")
                    else None
                ),
                suggested_prompt=(
                    str(payload.get("suggested_prompt"))
                    if payload.get("suggested_prompt") is not None
                    else None
                ),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/image-targets/{target_id:path}/regenerate",
        tags=["rpg-world"],
        responses=documented(RpgWorldImageGenerationResponse),
    )
    def rpg_regenerate_world_image_target(
        world_id: str,
        target_id: str,
        request: Request, request_body: RpgRegenerateWorldImageTargetRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        prompt = str(payload.get("prompt") or "").strip()
        try:
            return generate_world_images(
                world_id,
                target_ids=[target_id],
                prompts={target_id: prompt} if prompt else {},
                provider_id=str(payload.get("provider_id") or ""),
                width=max(64, int(payload.get("width") or 768)),
                height=max(64, int(payload.get("height") or 768)),
                style=str(payload.get("style") or ""),
                no_cache=bool(payload.get("no_cache", True)),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise
