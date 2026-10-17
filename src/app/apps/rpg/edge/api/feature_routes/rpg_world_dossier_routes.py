"""Editorial-only rich dossier routes for reusable RPG world entities."""
from __future__ import annotations
from fastapi import APIRouter

from typing import Any, Mapping

from fastapi import HTTPException, Request

from app.apps.rpg.genesis.worlds.dossier_authoring import (
    regenerate_world_entity_dossier,
    update_world_entity_dossier,
)
from app.apps.rpg.genesis.worlds.dossier_quality_service import (
    enrich_world_dossiers,
    world_dossier_quality,
)
from app.apps.rpg.genesis.worlds.dossier_regeneration_preview import (
    preview_world_entity_dossier_regeneration,
)

from pydantic import BaseModel as _TypedRequestBaseModel, ConfigDict as _TypedRequestConfigDict
from typing import Any as _TypedRequestAny
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgAuthoringDossierPreviewResponse,
    RpgAuthoringEntityMutationResponse,
    RpgWorldDossierEnrichmentResponse,
    RpgWorldDossierQualityResponse,
)

class _TypedRequestModel(_TypedRequestBaseModel):
    model_config = _TypedRequestConfigDict(extra="allow", populate_by_name=True)

class RpgEnrichWorldDossiersRequestBody(_TypedRequestModel):
    all_candidates: bool | None = False
    candidates: _TypedRequestAny = None
    directives: _TypedRequestAny = None
    dry_run: bool | None = True
    limit: _TypedRequestAny = None

class RpgUpdateWorldEntityDossierRequestBody(_TypedRequestModel):
    dossier: _TypedRequestAny = None
    short_summary: _TypedRequestAny = None

class RpgPreviewWorldEntityDossierRegenerationRequestBody(_TypedRequestModel):
    directives: _TypedRequestAny = None

class RpgRegenerateWorldEntityDossierRequestBody(_TypedRequestModel):
    directives: _TypedRequestAny = None


_ROUTE_SENTINEL = "_omnix_rpg_world_dossier_routes_registered"


def _body(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error": "request_body_must_be_object"},
        )
    return value


def _expected(payload: Mapping[str, Any]) -> tuple[int, str]:
    revision = int(payload.get("expected_draft_revision") or 0)
    content_hash = str(payload.get("expected_content_hash") or "").strip()
    if revision < 1 or not content_hash:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "expected_draft_revision_and_content_hash_required",
            },
        )
    return revision, content_hash


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


def register_rpg_world_dossier_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get(
        "/api/rpg/worlds/{world_id}/dossier-quality",
        tags=["rpg-world"], responses=documented(RpgWorldDossierQualityResponse),
    )
    def rpg_world_dossier_quality(world_id: str) -> dict[str, Any]:
        try:
            return world_dossier_quality(world_id)
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/enrich-dossiers",
        tags=["rpg-world"], responses=documented(RpgWorldDossierEnrichmentResponse),
    )
    def rpg_enrich_world_dossiers(
        world_id: str,
        request: Request, request_body: RpgEnrichWorldDossiersRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        directives = payload.get("directives")
        if directives is not None and not isinstance(directives, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "entity_directives_must_be_object"},
            )
        candidates = payload.get("candidates")
        if candidates is not None and not isinstance(candidates, list):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "dossier_candidates_must_be_array"},
            )
        try:
            enrichment_kwargs: dict[str, Any] = {
                "limit": max(1, min(int(payload.get("limit") or 10), 25)),
                "all_candidates": bool(payload.get("all_candidates", False)),
                "dry_run": bool(payload.get("dry_run", True)),
                "directives": dict(directives or {}),
            }
            if candidates is not None:
                enrichment_kwargs["requested_candidates"] = [
                    dict(candidate) for candidate in candidates if isinstance(candidate, Mapping)
                ]
            return enrich_world_dossiers(
                world_id,
                **enrichment_kwargs,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.patch(
        "/api/rpg/worlds/{world_id}/topics/{topic_id}/entities/{entity_id}/dossier",
        tags=["rpg-world"], responses=documented(RpgAuthoringEntityMutationResponse),
    )
    def rpg_update_world_entity_dossier(
        world_id: str,
        topic_id: str,
        entity_id: str,
        request: Request, request_body: RpgUpdateWorldEntityDossierRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        revision, content_hash = _expected(payload)
        dossier = payload.get("dossier")
        if not isinstance(dossier, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "entity_dossier_required"},
            )
        try:
            return update_world_entity_dossier(
                world_id,
                topic_id,
                entity_id,
                expected_draft_revision=revision,
                expected_content_hash=content_hash,
                short_summary=str(payload.get("short_summary") or ""),
                dossier=dossier,
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/topics/{topic_id}/entities/{entity_id}/regenerate-dossier-preview",
        tags=["rpg-world"], responses=documented(RpgAuthoringDossierPreviewResponse),
    )
    def rpg_preview_world_entity_dossier_regeneration(
        world_id: str,
        topic_id: str,
        entity_id: str,
        request: Request, request_body: RpgPreviewWorldEntityDossierRegenerationRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        revision, content_hash = _expected(payload)
        directives = payload.get("directives")
        if directives is not None and not isinstance(directives, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "entity_directives_must_be_object"},
            )
        try:
            return preview_world_entity_dossier_regeneration(
                world_id,
                topic_id,
                entity_id,
                expected_draft_revision=revision,
                expected_content_hash=content_hash,
                directives=dict(directives or {}),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise

    @router.post(
        "/api/rpg/worlds/{world_id}/topics/{topic_id}/entities/{entity_id}/regenerate-dossier",
        tags=["rpg-world"], responses=documented(RpgAuthoringEntityMutationResponse),
    )
    def rpg_regenerate_world_entity_dossier(
        world_id: str,
        topic_id: str,
        entity_id: str,
        request: Request, request_body: RpgRegenerateWorldEntityDossierRequestBody,
    ) -> dict[str, Any]:
        payload = dict(_body(request_body.model_dump(exclude_unset=True, by_alias=True)))
        revision, content_hash = _expected(payload)
        directives = payload.get("directives")
        if directives is not None and not isinstance(directives, Mapping):
            raise HTTPException(
                status_code=422,
                detail={"ok": False, "error": "entity_directives_must_be_object"},
            )
        try:
            return regenerate_world_entity_dossier(
                world_id,
                topic_id,
                entity_id,
                expected_draft_revision=revision,
                expected_content_hash=content_hash,
                directives=dict(directives or {}),
            )
        except Exception as exc:
            _raise_domain_error(exc)
            raise
