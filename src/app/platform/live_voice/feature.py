"""Live voice transport and speech runtime feature declaration."""
from __future__ import annotations

from fastapi import APIRouter

from app.platform.characters.contracts import CHARACTER_SNAPSHOT_OBSERVERS
from app.runtime.features import FeatureContext, FeatureModule
from app.runtime.ports import ContributionSpec


def _snapshot_cache_observer(_context: FeatureContext) -> object:
    from .prompt.cache import CharacterSnapshotCacheObserver

    return CharacterSnapshotCacheObserver()


def _live_voice_router(context: FeatureContext) -> APIRouter:
    from .speech.runtime_offload import register_live_voice_runtime_offload
    from .speech.speculative_tts import register_live_voice_execution_lane_routes
    from .transport.capabilities import register_tts_live_capability_routes
    from .transport.cue_asset_routes import register_live_voice_cue_asset_routes
    from .transport.diagnostics_routes import register_live_voice_diagnostics_routes
    from .transport.websocket import register_tts_live_call_websocket

    router = APIRouter()
    state = context.runtime_state
    if state is None:
        raise RuntimeError("live_voice_runtime_state_required")
    register_live_voice_runtime_offload(router, state)
    provider_resolver = getattr(state, "live_voice_tts_provider_resolver", None)
    if provider_resolver is None:
        raise RuntimeError("live_voice_tts_provider_resolver_required")
    register_tts_live_call_websocket(
        router,
        state,
        provider_resolver=provider_resolver,
    )
    register_live_voice_execution_lane_routes(router, state)
    register_live_voice_diagnostics_routes(router, state)
    register_live_voice_cue_asset_routes(router, state)
    register_tts_live_capability_routes(router, state)
    return router


FEATURE = FeatureModule(
    id="live-voice",
    title="Live voice",
    tier="platform",
    depends_on=("chat", "characters", "assistant-memory"),
    # The speech contract's TTS ports (PA-3.3).
    uses=("voice",),
    contributions=(ContributionSpec(CHARACTER_SNAPSHOT_OBSERVERS, _snapshot_cache_observer),),
    routers=(_live_voice_router,),
)
