"""Router registration for Character avatar generation and governed voice backfill."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from .avatar_generation_logging import (
    avatar_generation_log_path,
    avatar_generation_logger,
    avatar_generation_payload,
)
from .avatar_generation_models import (
    BackfillClonedVoiceCharactersRequest,
    BackfillClonedVoiceCharactersResponse,
    CharacterAvatarGenerationBatch,
    CharacterAvatarGenerationListResponse,
    CreateCharacterAvatarGenerationRequest,
)
from .avatar_generation_service import (
    CharacterAvatarGenerationNotFoundError,
    CharacterAvatarGenerationService,
    default_character_avatar_generation_service,
)
from .repository import CharacterNotFoundError


def _queue_failure_detail(exc: RuntimeError) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"avatar_generation_queue_unavailable:{message}"


def register_character_avatar_generation_routes(
    router: APIRouter,
    *,
    service_factory: Callable[
        [], CharacterAvatarGenerationService
    ] = default_character_avatar_generation_service,
) -> None:
    diagnostics = avatar_generation_logger()
    diagnostics.info(
        "event=avatar_generation_routes_registered %s",
        avatar_generation_payload(log_path=avatar_generation_log_path()),
    )

    @router.post(
        "/api/characters/{character_id}/avatar-generations",
        response_model=CharacterAvatarGenerationBatch,
        status_code=202,
        tags=["characters"],
    )
    def create_character_avatar_generation(
        character_id: str,
        request: CreateCharacterAvatarGenerationRequest,
    ) -> CharacterAvatarGenerationBatch:
        request_context = avatar_generation_payload(
            character_id=character_id,
            request=request.model_dump(mode="json"),
        )
        diagnostics.info(
            "event=avatar_generation_request_started %s",
            request_context,
        )
        try:
            batch = service_factory().create(character_id, request)
        except CharacterNotFoundError as exc:
            diagnostics.warning(
                "event=avatar_generation_character_not_found %s error=%r",
                request_context,
                exc,
            )
            raise HTTPException(status_code=404, detail="character not found") from exc
        except ValueError as exc:
            diagnostics.warning(
                "event=avatar_generation_request_rejected %s error=%r",
                request_context,
                exc,
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            diagnostics.exception(
                "event=avatar_generation_queue_unavailable %s",
                request_context,
            )
            raise HTTPException(status_code=503, detail=_queue_failure_detail(exc)) from exc
        except Exception:
            diagnostics.exception(
                "event=avatar_generation_request_failed %s",
                request_context,
            )
            raise

        diagnostics.info(
            "event=avatar_generation_request_accepted %s",
            avatar_generation_payload(
                character_id=character_id,
                batch_id=batch.id,
                base_job_id=batch.base_job_id,
                status=batch.status,
            ),
        )
        return batch

    @router.get(
        "/api/characters/{character_id}/avatar-generations",
        response_model=CharacterAvatarGenerationListResponse,
        tags=["characters"],
    )
    def list_character_avatar_generations(
        character_id: str,
    ) -> CharacterAvatarGenerationListResponse:
        try:
            result = service_factory().list(character_id)
        except CharacterNotFoundError as exc:
            raise HTTPException(status_code=404, detail="character not found") from exc
        except Exception:
            diagnostics.exception(
                "event=avatar_generation_list_failed %s",
                avatar_generation_payload(character_id=character_id),
            )
            raise
        diagnostics.info(
            "event=avatar_generation_list_completed %s",
            avatar_generation_payload(
                character_id=character_id,
                batch_count=len(result.batches),
            ),
        )
        return result

    @router.get(
        "/api/character-avatar-generations/{batch_id}",
        response_model=CharacterAvatarGenerationBatch,
        tags=["characters"],
    )
    def get_character_avatar_generation(
        batch_id: str,
    ) -> CharacterAvatarGenerationBatch:
        try:
            result = service_factory().get(batch_id)
        except CharacterAvatarGenerationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="avatar generation not found") from exc
        except Exception:
            diagnostics.exception(
                "event=avatar_generation_status_failed %s",
                avatar_generation_payload(batch_id=batch_id),
            )
            raise
        diagnostics.info(
            "event=avatar_generation_status_completed %s",
            avatar_generation_payload(
                batch_id=batch_id,
                character_id=result.character_id,
                status=result.status,
                error=result.error,
            ),
        )
        return result

    @router.post(
        "/api/characters/backfill-cloned-voices",
        response_model=BackfillClonedVoiceCharactersResponse,
        tags=["characters"],
    )
    def backfill_cloned_voice_characters(
        request: BackfillClonedVoiceCharactersRequest,
    ) -> BackfillClonedVoiceCharactersResponse:
        request_context = avatar_generation_payload(
            request=request.model_dump(mode="json"),
        )
        diagnostics.info(
            "event=avatar_backfill_request_started %s",
            request_context,
        )
        try:
            result = service_factory().backfill_cloned_voices(request)
        except Exception:
            diagnostics.exception(
                "event=avatar_backfill_request_failed %s",
                request_context,
            )
            raise
        diagnostics.info(
            "event=avatar_backfill_request_completed %s",
            avatar_generation_payload(item_count=len(result.items)),
        )
        return result


__all__ = ["register_character_avatar_generation_routes"]
