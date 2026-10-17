"""Live-speech API feature declaration."""
from __future__ import annotations

from fastapi import APIRouter

from app.runtime.features import FeatureContext, FeatureModule


def _router(_context: FeatureContext) -> APIRouter:
    from .api_stub import create_live_speech_router

    return create_live_speech_router()


FEATURE = FeatureModule(
    id="live-speech",
    title="Live Speech",
    tier="platform",
    # The speech contract's STT and TTS session ports (PA-3.3).
    uses=("voice",),
    routers=(_router,),
)
