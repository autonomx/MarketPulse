"""Live-voice adapters injected into Chat through the shared conversation port."""
from __future__ import annotations

from typing import Any, Iterator

from app.conversation.contracts import LiveVoiceChatPort

from .llm.routing import ROUTE_METADATA_KEY


class LiveVoiceChatIntegration:
    """Bind voice prompt and provider stages without patching Chat classes."""

    route_metadata_key = ROUTE_METADATA_KEY

    def is_live_voice_message(self, user_message: Any) -> bool:
        from .pipeline import is_live_voice_message

        return is_live_voice_message(user_message)

    def build_live_voice_prompt(
        self,
        store: Any,
        session: Any,
        user_message: Any,
        context_items: list[dict[str, Any]] | None,
    ) -> tuple[Any, Any]:
        from .pipeline import build_live_voice_prompt

        return build_live_voice_prompt(store, session, user_message, context_items)

    def record_rendered_prompt(self, assembly: Any, rendered: Any) -> None:
        from .llm.lmstudio_diagnostics import record_rendered_prompt

        record_rendered_prompt(assembly, rendered)

    def begin_routed_user_message(
        self,
        store: Any,
        session_id: str,
        request: Any,
        *,
        persist: Any,
    ) -> Any:
        from .llm.routing import begin_routed_user_message

        return begin_routed_user_message(store, session_id, request, persist=persist)

    def resolve_generation_route(self, user_message: Any, **kwargs: Any) -> Any:
        from .llm.routing import resolve_generation_route

        return resolve_generation_route(user_message, **kwargs)

    def resolve_stream_route(self, user_message: Any, **kwargs: Any) -> Any:
        from .llm.routing import resolve_stream_route

        return resolve_stream_route(user_message, **kwargs)

    def log_provider_route(self, **kwargs: Any) -> None:
        from .llm.routing import log_provider_route

        log_provider_route(**kwargs)

    def stream_with_retry(
        self,
        stream_factory: Any,
        fallback_factory: Any,
        *,
        provider_id: str | None,
        model_id: str | None,
    ) -> Iterator[Any]:
        from .llm.retry import stream_with_retry

        yield from stream_with_retry(
            stream_factory,
            fallback_factory,
            provider_id=provider_id,
            model_id=model_id,
        )

    def is_lmstudio_provider(self, provider: Any) -> bool:
        from .llm.metrics import is_lmstudio_provider

        return is_lmstudio_provider(provider)

    def generate_lmstudio_reply(
        self,
        store: Any,
        session: Any,
        user_message: Any,
        **kwargs: Any,
    ) -> Any:
        from .llm import lmstudio_diagnostics, metrics

        return lmstudio_diagnostics.generate_lmstudio_reply(
            store,
            session,
            user_message,
            fallback_generate=metrics.generate_lmstudio_reply,
            **kwargs,
        )

    def stream_lmstudio_reply(
        self,
        store: Any,
        session: Any,
        user_message: Any,
        **kwargs: Any,
    ) -> Iterator[Any]:
        from .llm import lmstudio_responses, metrics

        yield from lmstudio_responses.stream_lmstudio_reply(
            store,
            session,
            user_message,
            fallback_stream=metrics.stream_lmstudio_reply,
            **kwargs,
        )

    def stream_low_latency_reply(
        self,
        store: Any,
        session: Any,
        user_message: Any,
        **kwargs: Any,
    ) -> Iterator[Any]:
        from .llm.stream import stream_low_latency_reply

        yield from stream_low_latency_reply(store, session, user_message, **kwargs)

    def lmstudio_live_voice_options(self, user_message: Any) -> dict[str, Any]:
        from .llm.policy import lmstudio_live_voice_options

        return lmstudio_live_voice_options(user_message)

    def observe_live_voice_provider_stream(self, response: Any) -> Iterator[Any]:
        from .llm.stream import observe_live_voice_provider_stream

        yield from observe_live_voice_provider_stream(response)

    def new_text_chunker(self) -> Any:
        from .llm.stream import LowLatencyTextChunker

        return LowLatencyTextChunker()


def create_live_voice_chat_port() -> LiveVoiceChatPort:
    return LiveVoiceChatIntegration()


__all__ = ["LiveVoiceChatIntegration", "create_live_voice_chat_port"]
