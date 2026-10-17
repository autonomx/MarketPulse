"""Characters feature declaration."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from typing import Any

from app.runtime.features import FeatureContext, FeatureModule
from app.platform.chat.contracts import CHARACTER_RESOLVER
from app.platform.image.contracts import CHARACTER_AVATAR_FINISHER
from app.runtime.ports import ContributionSpec
from app.platform.characters.persistence.repository_specs import CHARACTER_REPOSITORY_SPECS

from .api import register_character_routes
from .avatar_api import register_character_avatar_routes
from .avatar_generation_api import register_character_avatar_generation_routes
from .avatar_viseme_api import register_character_avatar_viseme_routes
from .live2d_avatar import register_character_live2d_avatar_routes
from .live_conversation_rendering import register_live_conversation_rendering_routes

logger = logging.getLogger(__name__)


def _router(context: FeatureContext) -> APIRouter:
    router = APIRouter()
    chat_kwargs = {}
    if context.services is not None and getattr(context.services, "chat", None) is not None:
        chat_kwargs["chat_store_factory"] = lambda: context.services.chat
    register_character_routes(router)
    register_character_avatar_routes(router)
    register_character_avatar_generation_routes(router)
    register_character_avatar_viseme_routes(router)
    register_character_live2d_avatar_routes(router)
    register_live_conversation_rendering_routes(router, **chat_kwargs)
    return router


def _stabilize_avatar_frame(
    job: Any,
    request: Any,
    storage_path: str,
    request_metadata: dict[str, Any],
    store: Any,
) -> dict[str, Any]:
    if getattr(job, "module", "") != "character-avatar" or not getattr(request, "reference_asset_ids", None):
        return {}
    variant = str(
        request_metadata.get("avatar_viseme")
        or request_metadata.get("avatar_variant")
        or request_metadata.get("avatar_viseme_base")
        or ""
    ).strip()
    if not variant:
        return {}
    from .avatar_frame_stabilization import stabilize_generated_avatar_frame

    mouth_anchor = request_metadata.get("avatar_mouth_anchor")
    return stabilize_generated_avatar_frame(
        storage_path,
        reference_asset_id=request.reference_asset_ids[0],
        variant=variant,
        store=store,
        mouth_anchor=mouth_anchor if isinstance(mouth_anchor, dict) else None,
        articulation_percent=request_metadata.get("avatar_viseme_articulation_percent"),
    )


def _avatar_generation_completed(job: Any) -> None:
    if getattr(job, "module", "") != "character-avatar":
        return
    payload = getattr(job, "input_payload", None) or {}
    character_id = str((payload.get("metadata") or {}).get("character_id") or "").strip()
    if not character_id:
        return
    try:
        from .avatar_generation_service import CharacterAvatarGenerationService
        from .avatar_viseme_generation import CharacterVisemeGenerationService

        CharacterAvatarGenerationService().list(character_id)
        CharacterVisemeGenerationService().reconcile_character(character_id)
    except Exception:
        logger.debug("Avatar generation completion reconciliation failed", exc_info=True)
        return


class _AvatarFinisher:
    """Characters' implementation of image's CharacterAvatarFinisher port."""

    def stabilize(
        self, job: Any, request: Any, storage_path: str, request_metadata: dict[str, Any], store: Any,
    ) -> dict[str, Any]:
        return _stabilize_avatar_frame(job, request, storage_path, request_metadata, store)

    def completed(self, job: Any) -> None:
        _avatar_generation_completed(job)


def _character_resolver(_context: FeatureContext) -> object:
    from .interaction import CharacterChatResolver

    return CharacterChatResolver()


FEATURE = FeatureModule(
    id="characters",
    title="Characters",
    tier="platform",
    # Implements image's avatar finisher port, so it depends on image (ADR-0016).
    depends_on=("chat", "assistant-memory", "companion-activity", "image"),
    routers=(_router,),
    repositories=CHARACTER_REPOSITORY_SPECS,
    contributions=(
        ContributionSpec(CHARACTER_AVATAR_FINISHER, lambda _context: _AvatarFinisher()),
        ContributionSpec(CHARACTER_RESOLVER, _character_resolver),
    ),
)
