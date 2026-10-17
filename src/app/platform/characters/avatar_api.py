"""Router registration for Character Mode live-avatar packs."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from .avatar_models import (
    CharacterAvatarPack,
    DeleteCharacterAvatarPackResponse,
    UpsertCharacterAvatarPackRequest,
)
from .avatar_service import (
    CharacterAvatarAssetError,
    CharacterAvatarService,
    default_character_avatar_service,
)
from .repository import CharacterConflictError, CharacterNotFoundError


def register_character_avatar_routes(
    router: APIRouter,
    *,
    service_factory: Callable[[], CharacterAvatarService] = default_character_avatar_service,
) -> None:
    @router.get(
        "/api/characters/{character_id}/avatar-pack/optional",
        response_model=CharacterAvatarPack | None,
        tags=["characters"],
    )
    def get_optional_character_avatar_pack(
        character_id: str,
    ) -> CharacterAvatarPack | None:
        try:
            return service_factory().optional_get(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/api/characters/{character_id}/avatar-pack",
        response_model=CharacterAvatarPack,
        tags=["characters"],
    )
    def get_character_avatar_pack(character_id: str) -> CharacterAvatarPack:
        try:
            return service_factory().get(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put(
        "/api/characters/{character_id}/avatar-pack",
        response_model=CharacterAvatarPack,
        tags=["characters"],
    )
    def upsert_character_avatar_pack(
        character_id: str,
        request: UpsertCharacterAvatarPackRequest,
    ) -> CharacterAvatarPack:
        try:
            return service_factory().upsert(character_id, request)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CharacterConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CharacterAvatarAssetError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete(
        "/api/characters/{character_id}/avatar-pack",
        response_model=DeleteCharacterAvatarPackResponse,
        tags=["characters"],
    )
    def delete_character_avatar_pack(
        character_id: str,
    ) -> DeleteCharacterAvatarPackResponse:
        try:
            service_factory().delete(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return DeleteCharacterAvatarPackResponse(character_id=character_id)


__all__ = ["register_character_avatar_routes"]
