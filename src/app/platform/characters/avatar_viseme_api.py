"""Router registration for expanded Character avatar viseme generation."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from .avatar_viseme_generation import (
    CharacterVisemeGenerationBatch,
    CharacterVisemeGenerationService,
)
from .repository import CharacterNotFoundError


def register_character_avatar_viseme_routes(
    router: APIRouter,
    *,
    service_factory: Callable[[], CharacterVisemeGenerationService] = CharacterVisemeGenerationService,
) -> None:
    @router.post(
        "/api/characters/{character_id}/avatar-visemes",
        response_model=CharacterVisemeGenerationBatch,
        status_code=202,
        tags=["characters"],
    )
    def create_character_avatar_visemes(character_id: str) -> CharacterVisemeGenerationBatch:
        try:
            return service_factory().create(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail="character or avatar pack not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/api/character-avatar-visemes/{batch_id}",
        response_model=CharacterVisemeGenerationBatch,
        tags=["characters"],
    )
    def get_character_avatar_visemes(batch_id: str) -> CharacterVisemeGenerationBatch:
        try:
            return service_factory().get(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="viseme generation not found") from exc


__all__ = ["register_character_avatar_viseme_routes"]
