"""Desktop companion feature declaration."""
from __future__ import annotations

from fastapi import APIRouter

from app.runtime.features import FeatureContext, FeatureModule

from .routes import register_desktop_companion_routes


def _router(context: FeatureContext) -> APIRouter:
    router = APIRouter()
    kwargs = {}
    if context.services is not None and getattr(context.services, "chat", None) is not None:
        kwargs["chat_store_factory"] = lambda: context.services.chat
    register_desktop_companion_routes(router, **kwargs)  # type: ignore[arg-type]
    return router


FEATURE = FeatureModule(
    id="desktop-companion",
    title="Desktop Companion",
    tier="app",
    depends_on=("chat", "companion-activity"),
    # Reads and records companion memory when assistant-memory is enabled.
    uses=("assistant-memory",),
    routers=(_router,),
)
