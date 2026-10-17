"""Chat feature declaration."""
from fastapi import APIRouter

from app.runtime.features import FeatureModule
from app.platform.chat.persistence.repository_specs import CHAT_REPOSITORY_SPECS


def _chat_context_router(context):
    from app.persistence.runtime import uses_postgresql_runtime
    from app.platform.chat.assistant_context.routes import register_assistant_context_routes
    from .live_call_prewarm import register_live_call_prewarm_routes
    from .live_chat_evaluation_routes import create_live_chat_evaluation_router
    from .live_chat_speculation import register_live_chat_speculation_routes
    from .live_chat_speculation_handshake import register_live_chat_speculation_handshake_routes
    from .live_chat_speculation_inline_stream import register_live_chat_speculation_inline_stream_routes
    from .live_material_context import create_live_material_context_router
    from .live_observation_generation import create_live_observation_generation_router

    if uses_postgresql_runtime():
        from app.platform.chat.persistence.legacy_sessions import install_postgresql_legacy_session_callbacks
        install_postgresql_legacy_session_callbacks()

    router = APIRouter()
    router.include_router(create_live_material_context_router())
    router.include_router(create_live_observation_generation_router())
    services = context.services
    register_assistant_context_routes(
        router,
        chat_store_factory=lambda: services.chat,
        job_store_factory=lambda: services.jobs,
    )
    chat_store_factory = lambda: services.chat
    register_live_chat_speculation_routes(router, chat_store_factory=chat_store_factory)
    register_live_chat_speculation_handshake_routes(router, chat_store_factory=chat_store_factory)
    register_live_chat_speculation_inline_stream_routes(router, chat_store_factory=chat_store_factory)
    register_live_call_prewarm_routes(router, chat_store_factory=chat_store_factory)
    router.include_router(create_live_chat_evaluation_router())
    return router


def _research_mode_router(context):
    from .research_mode_routes import create_research_mode_router

    return create_research_mode_router(chat_store_factory=lambda: context.services.chat)


FEATURE = FeatureModule(
    id="chat",
    title="Chat and Realtime",
    tier="platform",
    routers=(_chat_context_router, _research_mode_router),
    repositories=CHAT_REPOSITORY_SPECS,
)
