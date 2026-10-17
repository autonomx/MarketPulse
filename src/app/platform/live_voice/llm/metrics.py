"""Persist LM Studio usage, statistics, and low-latency text deltas."""
from __future__ import annotations

import time
from typing import Any, Iterator

from app.platform.chat.contracts import (
    merge_provider_response_metrics,
    model_key,
    provider_key,
    provider_turn_deadline,
    remaining_turn_seconds,
)
from app.platform.live_voice.llm.stream import LowLatencyTextChunker

from app.observability.tts_stream_diagnostics import stream_log

def _resolve_provider(provider_id: str | None) -> Any:
    from app.providers.service import get_provider

    return get_provider(provider_key(provider_id))


def is_lmstudio_provider(provider: Any) -> bool:
    """The provider reports the server's own timing stats (LM Studio)."""
    from app.providers.catalog import RUNTIME_STATS, provider_supports

    return provider_supports(provider, RUNTIME_STATS)


def is_lmstudio(provider_id: str | None) -> bool:
    return is_lmstudio_provider(_resolve_provider(provider_id))


def _metrics_provider_id(provider_id: str | None) -> str:
    return provider_id or "lmstudio"


def _chat_completion(
    provider: Any,
    messages: list[Any],
    *,
    model: str | None,
    stream: bool,
    kwargs: dict[str, Any],
) -> Any:
    from app.providers.lmstudio_provider import LMStudioProvider

    if isinstance(provider, LMStudioProvider):
        from app.platform.live_voice.llm import lmstudio_model_resolution

        return lmstudio_model_resolution.chat_completion_with_loaded_model(
            provider,
            messages,
            model=model,
            stream=stream,
            **kwargs,
        )
    return provider.chat_completion(
        messages=messages,
        model=model,
        stream=stream,
        **kwargs,
    )


def generate_lmstudio_reply(
    self: Any,
    session: Any,
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
    context_items: list[dict[str, Any]],
    provider: Any | None = None,
    routing_deadline_at: float | None = None,
) -> dict[str, Any]:
    from app.providers import ChatMessage as ProviderMessage

    provider = provider or _resolve_provider(provider_id)
    if provider is None:
        raise RuntimeError("Chat provider is not available")
    assembly, rendered = self.build_provider_prompt(session, user_message, context_items)
    messages = [
        ProviderMessage(role=message.role, content=message.content)
        for message in rendered.messages
    ]
    model_name = model_key(model_id)
    from app.providers.structured.errors import ProviderTimeout

    deadline = provider_turn_deadline(
        provider_id,
        session_provider_id=getattr(session, "provider_id", None),
        existing_deadline_at=routing_deadline_at,
    )
    remaining = remaining_turn_seconds(deadline)
    if remaining is not None and remaining <= 0:
        raise ProviderTimeout("chat turn deadline has expired")
    from app.platform.live_voice.llm.policy import lmstudio_live_voice_options

    completion_kwargs: dict[str, Any] = {"include_metrics": True}
    completion_kwargs.update(lmstudio_live_voice_options(user_message))
    if remaining is not None:
        completion_kwargs["request_timeout_seconds"] = remaining
    response = _chat_completion(
        provider,
        messages,
        model=model_name,
        stream=False,
        kwargs=completion_kwargs,
    )
    content = (getattr(response, "content", "") or "").strip()
    if not content:
        raise RuntimeError("Chat response was empty")

    metadata: dict[str, Any] = {
        "generation_status": "completed",
        "provider_id": provider_id,
        "model_id": model_id,
        "resolved_model": getattr(response, "model", None) or model_name,
        **self._active_memory_metadata(assembly, rendered),
        **self._active_history_metadata(assembly),
    }
    usage = getattr(response, "usage", None)
    if usage:
        metadata["usage"] = usage
    provider_metrics = merge_provider_response_metrics(
        None,
        response,
        provider_id=_metrics_provider_id(provider_id),
    )
    if provider_metrics:
        metadata["provider_metrics"] = provider_metrics
    thinking = getattr(response, "thinking", None) or getattr(response, "reasoning", None)
    if thinking:
        metadata["thinking"] = thinking
    return {"content": content, "metadata": metadata}


def stream_lmstudio_reply(
    self: Any,
    session: Any,
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
    context_items: list[dict[str, Any]] | None,
    provider: Any | None = None,
    routing_deadline_at: float | None = None,
) -> Iterator[dict[str, Any]]:
    from app.providers import ChatMessage as ProviderMessage

    started = time.perf_counter()
    provider = provider or _resolve_provider(provider_id)
    if provider is None:
        raise RuntimeError("Chat provider is not available")

    prompt_started = time.perf_counter()
    assembly, rendered = self.build_provider_prompt(
        session,
        user_message,
        context_items or [],
    )
    prompt_build_ms = (time.perf_counter() - prompt_started) * 1000.0
    messages = [
        ProviderMessage(role=message.role, content=message.content)
        for message in rendered.messages
    ]
    model_name = model_key(model_id)
    deadline = provider_turn_deadline(
        provider_id,
        session_provider_id=getattr(session, "provider_id", None),
        existing_deadline_at=routing_deadline_at,
    )
    remaining_budget = remaining_turn_seconds(deadline)
    if remaining_budget is not None and remaining_budget <= 0:
        from app.providers.structured.errors import ProviderTimeout

        raise ProviderTimeout("chat turn deadline has expired")
    from app.platform.live_voice.llm.policy import lmstudio_live_voice_options

    completion_kwargs: dict[str, Any] = {"include_metrics": True}
    completion_kwargs.update(lmstudio_live_voice_options(user_message))
    if remaining_budget is not None:
        completion_kwargs["request_timeout_seconds"] = remaining_budget
    response = _chat_completion(
        provider,
        messages,
        model=model_name,
        stream=True,
        kwargs=completion_kwargs,
    )
    chunker = LowLatencyTextChunker()
    full_text = ""
    resolved_model = model_name
    usage = None
    provider_metrics: dict[str, Any] = {}
    provider_iteration_started = time.perf_counter()
    first_provider_text_ms: float | None = None
    first_client_chunk_ms: float | None = None

    for chunk in response:
        resolved_model = getattr(chunk, "model", None) or resolved_model
        usage = getattr(chunk, "usage", None) or usage
        provider_metrics = merge_provider_response_metrics(
            provider_metrics,
            chunk,
            provider_id=_metrics_provider_id(provider_id),
        )
        text = getattr(chunk, "content", "") or ""
        if not text:
            continue
        if first_provider_text_ms is None:
            first_provider_text_ms = (time.perf_counter() - provider_iteration_started) * 1000.0
            stream_log(
                "gateway-live-chat-first-token",
                "runtime",
                "live_chat_lmstudio_first_provider_text",
                prompt_build_ms=round(prompt_build_ms, 3),
                provider_first_text_ms=round(first_provider_text_ms, 3),
            )
        full_text += text
        for ready in chunker.push(text):
            if first_client_chunk_ms is None:
                first_client_chunk_ms = (time.perf_counter() - started) * 1000.0
                stream_log(
                    "gateway-live-chat-first-token",
                    "runtime",
                    "live_chat_lmstudio_first_client_chunk",
                    first_client_chunk_ms=round(first_client_chunk_ms, 3),
                    prompt_build_ms=round(prompt_build_ms, 3),
                    provider_first_text_ms=(
                        round(first_provider_text_ms, 3)
                        if first_provider_text_ms is not None
                        else None
                    ),
                    text_chars=len(ready),
                )
            yield {"type": "text_chunk", "text": ready}

    remaining = chunker.flush()
    if remaining:
        if first_client_chunk_ms is None:
            first_client_chunk_ms = (time.perf_counter() - started) * 1000.0
        yield {"type": "text_chunk", "text": remaining}

    native_ttft = provider_metrics.get("time_to_first_token_seconds")
    native_generation = provider_metrics.get("generation_time_seconds")
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_lmstudio_stream_completed",
        prompt_build_ms=round(prompt_build_ms, 3),
        provider_first_text_ms=(
            round(first_provider_text_ms, 3) if first_provider_text_ms is not None else None
        ),
        first_client_chunk_ms=(
            round(first_client_chunk_ms, 3) if first_client_chunk_ms is not None else None
        ),
        native_ttft_ms=(
            round(float(native_ttft) * 1000.0, 3)
            if isinstance(native_ttft, (int, float))
            else None
        ),
        native_generation_ms=(
            round(float(native_generation) * 1000.0, 3)
            if isinstance(native_generation, (int, float))
            else None
        ),
        output_tokens=provider_metrics.get("output_tokens"),
        total_ms=round((time.perf_counter() - started) * 1000.0, 3),
    )
    yield {
        "type": "complete",
        "content": full_text.strip(),
        "metadata": {
            "generation_status": "completed",
            "provider_id": provider_id,
            "model_id": model_id,
            "resolved_model": resolved_model,
            **self._active_memory_metadata(assembly, rendered),
            **self._active_history_metadata(assembly),
            **({"usage": usage} if usage else {}),
            **({"provider_metrics": provider_metrics} if provider_metrics else {}),
        },
    }
