"""Audiobook FeatureModule declaration."""
from __future__ import annotations

from app.runtime.features import FeatureContext, FeatureModule

from .routes import create_audiobook_background_worker, create_audiobook_router
from .streaming import create_audiobook_streaming_router


def _http_router(_context: FeatureContext):
    return create_audiobook_router()


def _streaming_router(_context: FeatureContext):
    return create_audiobook_streaming_router()


def _background_worker(_context: FeatureContext):
    return create_audiobook_background_worker()


FEATURE = FeatureModule(
    id="audiobook",
    title="Audiobook",
    tier="app",
    routers=(_http_router, _streaming_router),
    background_workers=(_background_worker,),
)
