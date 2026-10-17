"""Privacy-safe diagnostics for synchronous LM Studio chat completions.

LM Studio's UI may reduce an inference failure to ``Channel Error``. Capture the
model, rendered prompt size, and a normalized failure category without logging
conversation text. The wrapper is installed after the provider metrics and prompt
hooks, so it observes the final rendered prompt while retaining the established
provider behavior.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from app.platform.chat.contracts import model_key
from app.observability.tts_stream_diagnostics import stream_log

_ACTIVE_CALL: ContextVar[dict[str, Any] | None] = ContextVar(
    "omnix_lmstudio_nonstream_diagnostics",
    default=None,
)
_WHITESPACE = re.compile(r"\s+")


def _configured_model(provider: Any, model_id: str | None) -> str | None:
    explicit = model_key(model_id)
    if explicit:
        return explicit
    config = getattr(provider, "config", None)
    configured = str(getattr(config, "model", "") or "").strip()
    return configured or None


def _classify_lmstudio_error(error: BaseException) -> tuple[str, str]:
    message = _WHITESPACE.sub(" ", str(error or "")).strip().casefold()
    if "channel error" in message:
        return "lmstudio_channel_error", "LM Studio inference channel closed."
    if any(
        marker in message
        for marker in (
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "n_ctx",
            "prompt is too long",
        )
    ):
        return "lmstudio_context_error", "LM Studio rejected the prompt context."
    if "timed out" in message or "timeout" in message:
        return "lmstudio_timeout", "LM Studio request timed out."
    if "cuda" in message or "out of memory" in message or "oom" in message:
        return "lmstudio_resource_error", "LM Studio reported a GPU or memory failure."
    if "http error" in message:
        return "lmstudio_http_error", "LM Studio returned an HTTP error."
    return "lmstudio_request_failed", "LM Studio chat completion failed."


def _rendered_prompt_fields(assembly: Any, rendered: Any) -> dict[str, Any]:
    messages = list(getattr(rendered, "messages", []) or [])
    diagnostics = getattr(rendered, "diagnostics", None)
    assembly_diagnostics = getattr(assembly, "diagnostics", None)
    if not isinstance(assembly_diagnostics, dict):
        assembly_diagnostics = {}
    return {
        "rendered_message_count": len(messages),
        "prompt_chars": sum(
            len(str(getattr(message, "content", "") or ""))
            for message in messages
        ),
        "estimated_input_tokens": getattr(diagnostics, "estimated_tokens", None),
        "usable_input_tokens": getattr(diagnostics, "usable_input_tokens", None),
        "truncated_sections": list(
            getattr(diagnostics, "truncated_sections", []) or []
        ),
        "recent_message_count": assembly_diagnostics.get("recent_message_count"),
    }


def _run_lmstudio_nonstream_diagnostics(
    original: Callable[..., dict[str, Any]],
    self: Any,
    session: Any,
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
    context_items: list[dict[str, Any]],
    provider: Any | None,
    routing_deadline_at: float | None = None,
) -> dict[str, Any]:
    provider_instance = provider
    model_name = _configured_model(provider_instance, model_id)
    state: dict[str, Any] = {
        "provider_id": provider_id,
        "model_id": model_name,
        "session_message_count": len(getattr(session, "messages", []) or []),
        "user_message_chars": len(str(getattr(user_message, "content", "") or "")),
        "context_item_count": len(context_items),
    }
    token = _ACTIVE_CALL.set(state)
    started = time.perf_counter()
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_lmstudio_nonstream_started",
        **state,
    )
    try:
        generate_kwargs: dict[str, Any] = {
            "provider_id": provider_id,
            "model_id": model_id,
            "context_items": context_items,
            "provider": provider,
        }
        if routing_deadline_at is not None:
            generate_kwargs["routing_deadline_at"] = routing_deadline_at
        result = original(
            self,
            session,
            user_message,
            **generate_kwargs,
        )
    except Exception as exc:
        error_code, error_summary = _classify_lmstudio_error(exc)
        stream_log(
            "gateway-live-chat-first-token",
            "runtime",
            "live_chat_lmstudio_nonstream_failed",
            **dict(_ACTIVE_CALL.get() or state),
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
            error_type=type(exc).__name__,
            error_code=error_code,
            error_summary=error_summary,
        )
        raise
    else:
        metadata = result.get("metadata") if isinstance(result, dict) else None
        provider_metrics = (
            metadata.get("provider_metrics")
            if isinstance(metadata, dict)
            and isinstance(metadata.get("provider_metrics"), dict)
            else {}
        )
        content = result.get("content") if isinstance(result, dict) else ""
        stream_log(
            "gateway-live-chat-first-token",
            "runtime",
            "live_chat_lmstudio_nonstream_completed",
            **dict(_ACTIVE_CALL.get() or state),
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
            response_chars=len(str(content or "")),
            input_tokens=provider_metrics.get("input_tokens"),
            output_tokens=provider_metrics.get("output_tokens"),
            tokens_per_second=provider_metrics.get("tokens_per_second"),
        )
        return result
    finally:
        _ACTIVE_CALL.reset(token)


def record_rendered_prompt(assembly: Any, rendered: Any) -> None:
    """Capture final prompt dimensions during an instrumented LM Studio call."""
    active = _ACTIVE_CALL.get()
    if active is not None:
        active.update(_rendered_prompt_fields(assembly, rendered))


def generate_lmstudio_reply(
    self: Any,
    session: Any,
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
    context_items: list[dict[str, Any]],
    provider: Any | None,
    fallback_generate: Callable[..., dict[str, Any]],
    routing_deadline_at: float | None = None,
) -> dict[str, Any]:
    """Add privacy-safe diagnostics around the direct provider metrics adapter."""
    return _run_lmstudio_nonstream_diagnostics(
        fallback_generate,
        self,
        session,
        user_message,
        provider_id=provider_id,
        model_id=model_id,
        context_items=context_items,
        provider=provider,
        routing_deadline_at=routing_deadline_at,
    )


__all__ = [
    "generate_lmstudio_reply",
    "record_rendered_prompt",
]
