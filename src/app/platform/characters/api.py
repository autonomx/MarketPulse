"""Router factories for durable Character profiles and session mode."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query

from .live_conversation_profile import (
    LiveConversationProfile,
    LiveConversationProfileStore,
    LiveConversationProfileUpdate,
    default_live_conversation_profile_store,
)
from .models import (
    ArchiveCharacterResponse,
    CharacterListResponse,
    CharacterProfile,
    CharacterVersionListResponse,
    CreateCharacterRequest,
    UpdateCharacterRequest,
)
from .repository import CharacterConflictError, CharacterNotFoundError
from .service import (
    CharacterService,
    CharacterVoiceAssetError,
    default_character_service,
)
from .voice_consent import (
    UpdateVoiceProfileGovernanceRequest,
    VoiceConsentError,
    VoiceProfileGovernance,
    default_voice_governance_service,
)













def register_character_routes(
    router: APIRouter,
    *,
    service_factory: Callable[[], CharacterService] = default_character_service,
    live_conversation_profile_store_factory: Callable[[], LiveConversationProfileStore] = default_live_conversation_profile_store,
) -> None:
    """Register typed routes while keeping the flagged feature out of public OpenAPI."""

    @router.get("/api/characters", response_model=CharacterListResponse, tags=["characters"])
    def list_characters(include_archived: bool = Query(default=False)) -> CharacterListResponse:
        return service_factory().list(include_archived=include_archived)

    @router.post("/api/characters", response_model=CharacterProfile, status_code=201, tags=["characters"])
    def create_character(request: CreateCharacterRequest) -> CharacterProfile:
        try:
            return service_factory().create(request)
        except CharacterConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CharacterVoiceAssetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/characters/{character_id}", response_model=CharacterProfile, tags=["characters"])
    def get_character(character_id: str, include_archived: bool = Query(default=False)) -> CharacterProfile:
        try:
            return service_factory().get(character_id, include_archived=include_archived)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail="character not found") from exc

    @router.patch("/api/characters/{character_id}", response_model=CharacterProfile, tags=["characters"])
    def update_character(character_id: str, request: UpdateCharacterRequest) -> CharacterProfile:
        try:
            return service_factory().update(character_id, request)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail="character not found") from exc
        except CharacterConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CharacterVoiceAssetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/api/characters/{character_id}", response_model=ArchiveCharacterResponse, tags=["characters"])
    def archive_character(character_id: str) -> ArchiveCharacterResponse:
        try:
            return service_factory().archive(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail="character not found") from exc

    @router.get("/api/characters/{character_id}/versions", response_model=CharacterVersionListResponse, tags=["characters"])
    def list_character_versions(character_id: str) -> CharacterVersionListResponse:
        try:
            return service_factory().versions(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail="character not found") from exc




    @router.get(
        "/api/voice-profiles/{asset_id}/governance",
        response_model=VoiceProfileGovernance,
        tags=["characters"],
    )
    def get_voice_profile_governance(asset_id: str) -> VoiceProfileGovernance:
        try:
            return default_voice_governance_service().get(asset_id)
        except VoiceConsentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch(
        "/api/voice-profiles/{asset_id}/governance",
        response_model=VoiceProfileGovernance,
        tags=["characters"],
    )
    def update_voice_profile_governance(
        asset_id: str,
        request: UpdateVoiceProfileGovernanceRequest,
    ) -> VoiceProfileGovernance:
        try:
            return default_voice_governance_service().update(asset_id, request)
        except VoiceConsentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/api/live-chat/profile/defaults",
        response_model=LiveConversationProfile,
        tags=["live-chat"],
    )
    def get_live_conversation_defaults() -> LiveConversationProfile:
        return live_conversation_profile_store_factory().get_defaults()

    @router.patch(
        "/api/live-chat/profile/defaults",
        response_model=LiveConversationProfile,
        tags=["live-chat"],
    )
    def update_live_conversation_defaults(request: LiveConversationProfileUpdate) -> LiveConversationProfile:
        return live_conversation_profile_store_factory().update_defaults(request)















__all__ = ["register_character_routes"]
