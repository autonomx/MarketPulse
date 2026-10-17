"""Player-safe Campaign Genesis progress and Lore routes."""
from __future__ import annotations
from fastapi import APIRouter


from typing import Any, Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from app.apps.rpg.genesis.forge.campaign_lore_api import (
    LoreDocumentForbidden,
    LoreDocumentNotFound,
    campaign_genesis_progress_payload,
    campaign_lore_document_payload,
    campaign_lore_payload,
    transition_lore_discovery,
)
from app.apps.rpg.genesis.forge.campaign_lore_store import (
    LoreRegenerationUnavailable,
    load_campaign_lore,
    persist_campaign_lore,
    regenerate_campaign_lore_document,
)
from app.apps.rpg.genesis.forge.runtime_materialization import (
    RuntimeMaterializationConflict,
    RuntimeMaterializationUnavailable,
    materialize_runtime_lore,
)
from app.apps.rpg.session.service import load_session
from app.apps.rpg.session.service import SESSION_CAMPAIGN_SESSIONS
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgCampaignLoreResponse,
)
from app.apps.rpg.edge.api.route_contracts import (
    RpgCampaignGenesisResponse,
    RpgLoreDiscoveryResponse,
    RpgLoreDocumentResponse,
    RpgLoreMaterializationResponse,
    RpgLoreRegenerationResponse,
)


class LoreDiscoveryRequest(BaseModel):
    document_id: str
    status: str
    source: str = "gameplay"


class LoreRegenerationRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=300)
    direction: str = Field(default="", max_length=1000)


class LoreMaterializationRequest(BaseModel):
    kind: Literal["creature", "location"]
    name: str = Field(min_length=1, max_length=120)
    direction: str = Field(default="", max_length=1000)
    document_id: str = Field(default="", max_length=300)


def _session_or_404(session_id: str) -> dict[str, Any]:
    session = load_session(session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "session_not_found",
                "session_id": session_id,
            },
        )
    return session


def _kick_genesis_recovery() -> None:
    from app.apps.rpg.genesis.forge.async_coordinator import (
        campaign_genesis_async_enabled,
        kick_campaign_genesis_worker,
    )

    if campaign_genesis_async_enabled():
        kick_campaign_genesis_worker(sessions=SESSION_CAMPAIGN_SESSIONS)


def _lore_error(exc: Exception, session_id: str) -> HTTPException:
    if isinstance(exc, LoreDocumentNotFound):
        return HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "lore_document_not_found",
                "session_id": session_id,
            },
        )
    if isinstance(exc, LoreDocumentForbidden):
        return HTTPException(
            status_code=403,
            detail={
                "ok": False,
                "error": "lore_document_not_visible",
                "session_id": session_id,
            },
        )
    return HTTPException(
        status_code=400,
        detail={
            "ok": False,
            "error": "invalid_lore_discovery_transition",
            "session_id": session_id,
            "message": str(exc),
        },
    )


def register_rpg_campaign_lore_routes(router: APIRouter, state) -> None:
    @router.get(
        "/api/rpg/sessions/{session_id}/campaign-genesis",
        tags=["rpg-session"],
        responses=documented(RpgCampaignGenesisResponse),
    )
    def rpg_campaign_genesis(session_id: str) -> dict[str, Any]:
        _kick_genesis_recovery()
        session = _session_or_404(session_id)
        session, storage = load_campaign_lore(
            session_id,
            session,
            ensure_current_location=False,
            sessions=SESSION_CAMPAIGN_SESSIONS,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "generation": campaign_genesis_progress_payload(session),
            "storage": storage,
        }

    @router.get(
        "/api/rpg/sessions/{session_id}/lore",
        tags=["rpg-session"],
        responses=documented(RpgCampaignLoreResponse),
    )
    def rpg_campaign_lore(session_id: str) -> dict[str, Any]:
        session = _session_or_404(session_id)
        session, storage = load_campaign_lore(
            session_id,
            session,
            ensure_current_location=True,
            sessions=SESSION_CAMPAIGN_SESSIONS,
        )
        return {
            **campaign_lore_payload(session),
            "session_id": session_id,
            "storage": storage,
        }

    @router.get(
        "/api/rpg/sessions/{session_id}/lore/document",
        tags=["rpg-session"],
        responses=documented(RpgLoreDocumentResponse),
    )
    def rpg_campaign_lore_document(
        session_id: str,
        document_id: str = Query(min_length=1, max_length=300),
    ) -> dict[str, Any]:
        session = _session_or_404(session_id)
        session, storage = load_campaign_lore(
            session_id,
            session,
            ensure_current_location=True,
            sessions=SESSION_CAMPAIGN_SESSIONS,
        )
        try:
            return {
                **campaign_lore_document_payload(
                    session,
                    document_id,
                ),
                "session_id": session_id,
                "storage": storage,
            }
        except Exception as exc:
            raise _lore_error(exc, session_id) from exc

    @router.post(
        "/api/rpg/sessions/{session_id}/lore/regenerate",
        tags=["rpg-session"],
        responses=documented(RpgLoreRegenerationResponse),
    )
    def rpg_campaign_lore_regenerate(
        session_id: str,
        request: LoreRegenerationRequest,
    ) -> dict[str, Any]:
        session = _session_or_404(session_id)
        try:
            updated, storage = regenerate_campaign_lore_document(
                session_id,
                session,
                document_id=request.document_id,
                direction=request.direction,
                sessions=SESSION_CAMPAIGN_SESSIONS,
            )
            return {
                "ok": True,
                "session_id": session_id,
                "document": campaign_lore_document_payload(
                    updated,
                    request.document_id,
                )["document"],
                "lore": {
                    **campaign_lore_payload(updated),
                    "session_id": session_id,
                    "storage": storage,
                },
                "storage": storage,
            }
        except (LoreDocumentNotFound, LoreDocumentForbidden) as exc:
            raise _lore_error(exc, session_id) from exc
        except LoreRegenerationUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "ok": False,
                    "error": "lore_regeneration_unavailable",
                    "session_id": session_id,
                    "message": str(exc),
                },
            ) from exc

    @router.post(
        "/api/rpg/sessions/{session_id}/lore/discovery",
        tags=["rpg-session"],
        responses=documented(RpgLoreDiscoveryResponse),
    )
    def rpg_campaign_lore_discovery(
        session_id: str,
        request: LoreDiscoveryRequest,
    ) -> dict[str, Any]:
        session = _session_or_404(session_id)
        session, _storage = load_campaign_lore(
            session_id,
            session,
            ensure_current_location=True,
            sessions=SESSION_CAMPAIGN_SESSIONS,
        )
        try:
            updated = transition_lore_discovery(
                session,
                document_id=request.document_id,
                status=request.status,
                source=request.source,
            )
            saved, storage = persist_campaign_lore(session_id, updated, sessions=SESSION_CAMPAIGN_SESSIONS)
            return {
                "ok": True,
                "session_id": session_id,
                "document_id": request.document_id,
                "status": request.status,
                "lore": campaign_lore_payload(saved),
                "storage": storage,
            }
        except Exception as exc:
            raise _lore_error(exc, session_id) from exc

    @router.post(
        "/api/rpg/sessions/{session_id}/lore/materialize",
        tags=["rpg-session"],
        responses=documented(RpgLoreMaterializationResponse),
    )
    def rpg_campaign_lore_materialize(
        session_id: str,
        request: LoreMaterializationRequest,
    ) -> dict[str, Any]:
        session = _session_or_404(session_id)
        try:
            updated, storage = materialize_runtime_lore(
                session_id,
                session,
                kind=request.kind,
                name=request.name,
                direction=request.direction,
                document_id=request.document_id,
                sessions=SESSION_CAMPAIGN_SESSIONS,
            )
            document_id = str(storage["document_id"])
            return {
                "ok": True,
                "session_id": session_id,
                "document": campaign_lore_document_payload(
                    updated,
                    document_id,
                )["document"],
                "definition": storage["definition"],
                "lore": {
                    **campaign_lore_payload(updated),
                    "session_id": session_id,
                    "storage": storage,
                },
                "storage": storage,
            }
        except RuntimeMaterializationConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "error": "runtime_materialization_conflict",
                    "session_id": session_id,
                    "message": str(exc),
                },
            ) from exc
        except RuntimeMaterializationUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "ok": False,
                    "error": "runtime_materialization_unavailable",
                    "session_id": session_id,
                    "message": str(exc),
                },
            ) from exc
