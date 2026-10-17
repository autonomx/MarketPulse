"""Composition feature for routes joining chat, characters, memory, and initiative."""
from __future__ import annotations

from fastapi import APIRouter

from app.runtime.features import FeatureContext, FeatureModule

from .routes import register_character_integration_routes


def _router(context: FeatureContext) -> APIRouter:
    router = APIRouter()
    register_character_integration_routes(
        router,
        chat_store_factory=lambda: context.services.chat,
    )
    return router


FEATURE = FeatureModule(
    id="character-interactions",
    title="Character interactions",
    tier="app",
    depends_on=("characters", "chat", "assistant-memory", "companion-activity"),
    routers=(_router,),
)
