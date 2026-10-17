"""Recover live chat turns that fail before the first provider token.

Live voice users should not need to repeat an utterance because a provider stream
failed before yielding any text. Retries are deliberately disabled after the
first text chunk so a partially delivered answer can never be duplicated.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import time
from collections.abc import Callable, Iterator
from typing import Any

from app.providers.exceptions import RateLimitError

from app.observability.tts_stream_diagnostics import stream_log

_DEFAULT_STREAM_ATTEMPTS = 4
_MAX_STREAM_ATTEMPTS = 5
_DEFAULT_RETRY_BASE_DELAY_MS = 250.0
_DEFAULT_RETRY_MAX_DELAY_MS = 1_000.0
_DEFAULT_FALLBACK_DELAY_MS = 1_000.0
_MAX_CONFIGURED_DELAY_MS = 5_000.0


class EmptyProviderStreamError(RuntimeError):
    """Raised when a provider completes without delivering assistant text."""


def _stream_attempts() -> int:
    try:
        value = int(
            _env_str(
                "OMNIX_LIVE_CHAT_STREAM_ATTEMPTS",
                str(_DEFAULT_STREAM_ATTEMPTS),
            )
            or _DEFAULT_STREAM_ATTEMPTS
        )
    except (TypeError, ValueError):
        value = _DEFAULT_STREAM_ATTEMPTS
    return max(1, min(_MAX_STREAM_ATTEMPTS, value))


def _configured_delay_ms(name: str, default: float) -> float:
    try:
        value = float(_env_str(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(_MAX_CONFIGURED_DELAY_MS, value))


def _retry_base_delay_ms() -> float:
    return _configured_delay_ms(
        "OMNIX_LIVE_CHAT_RETRY_BASE_DELAY_MS",
        _DEFAULT_RETRY_BASE_DELAY_MS,
    )


def _retry_max_delay_ms() -> float:
    return _configured_delay_ms(
        "OMNIX_LIVE_CHAT_RETRY_MAX_DELAY_MS",
        _DEFAULT_RETRY_MAX_DELAY_MS,
    )


def _fallback_delay_ms() -> float:
    return _configured_delay_ms(
        "OMNIX_LIVE_CHAT_FALLBACK_DELAY_MS",
        _DEFAULT_FALLBACK_DELAY_MS,
    )


def _event_text(event: dict[str, Any]) -> str:
    if event.get("type") == "text_chunk":
        return str(event.get("text") or "").strip()
    if event.get("type") == "complete":
        return str(event.get("content") or "").strip()
    return ""


def _bounded_retry_delay_ms(attempt: int, *, base_ms: float, maximum_ms: float) -> float:
    if base_ms <= 0.0 or maximum_ms <= 0.0:
        return 0.0
    return min(maximum_ms, base_ms * (2 ** max(0, attempt - 1)))


def _rate_limit_error(error: BaseException) -> RateLimitError | None:
    """Find a provider rate-limit error even when a transport wrapper hides it."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RateLimitError):
            return current
        current = current.__cause__ or current.__context__
    return None


def retry_provider_stream(
    stream_factory: Callable[[], Iterator[dict[str, Any]]],
    fallback_factory: Callable[[], dict[str, Any]] | None,
    *,
    attempts: int,
    retry_base_delay_ms: float = 0.0,
    retry_max_delay_ms: float = 0.0,
    fallback_delay_ms: float = 0.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    diagnostic_context: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Retry failures or empty completion only before any text is delivered."""
    max_attempts = max(1, min(_MAX_STREAM_ATTEMPTS, int(attempts)))
    last_error: Exception | None = None
    context = dict(diagnostic_context or {})

    for attempt in range(1, max_attempts + 1):
        delivered_text = False
        saw_completion = False
        try:
            for event in stream_factory():
                event_type = event.get("type")
                text = _event_text(event)
                if event_type == "text_chunk" and text:
                    delivered_text = True
                    yield event
                    continue
                if event_type == "complete":
                    if not text and not delivered_text:
                        raise EmptyProviderStreamError(
                            "Chat provider stream completed without assistant text"
                        )
                    if text and not delivered_text:
                        # Some providers return content only on completion. Preserve
                        # the streaming contract by synthesizing one text chunk.
                        delivered_text = True
                        yield {"type": "text_chunk", "text": text}
                    saw_completion = True
                    yield event
                    break
                yield event
            if saw_completion:
                if attempt > 1:
                    stream_log(
                        "gateway-live-chat-stream",
                        "runtime",
                        "live_chat_stream_retry_recovered",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        **context,
                    )
                return
            raise EmptyProviderStreamError(
                "Chat provider stream closed before completion"
            )
        except GeneratorExit:
            raise
        except Exception as exc:
            last_error = exc
            if delivered_text:
                stream_log(
                    "gateway-live-chat-stream",
                    "runtime",
                    "live_chat_stream_retry_skipped_after_text",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_type=type(exc).__name__,
                    **context,
                )
                raise
            rate_limit_error = _rate_limit_error(exc)
            if rate_limit_error is not None:
                stream_log(
                    "gateway-live-chat-stream",
                    "runtime",
                    "live_chat_stream_retry_suppressed",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_type=type(rate_limit_error).__name__,
                    reason="provider_rate_limited",
                    **context,
                )
                raise rate_limit_error
            if attempt < max_attempts:
                delay_ms = _bounded_retry_delay_ms(
                    attempt,
                    base_ms=max(0.0, retry_base_delay_ms),
                    maximum_ms=max(0.0, retry_max_delay_ms),
                )
                stream_log(
                    "gateway-live-chat-stream",
                    "runtime",
                    "live_chat_stream_retry_scheduled",
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    max_attempts=max_attempts,
                    delay_ms=round(delay_ms, 3),
                    error_type=type(exc).__name__,
                    **context,
                )
                if delay_ms > 0.0:
                    sleep_fn(delay_ms / 1_000.0)
                continue

    if fallback_factory is not None:
        cooldown_ms = max(0.0, fallback_delay_ms)
        if cooldown_ms > 0.0:
            stream_log(
                "gateway-live-chat-stream",
                "runtime",
                "live_chat_stream_fallback_scheduled",
                max_attempts=max_attempts,
                delay_ms=round(cooldown_ms, 3),
                error_type=type(last_error).__name__ if last_error is not None else "unknown",
                **context,
            )
            sleep_fn(cooldown_ms / 1_000.0)
        try:
            fallback = fallback_factory()
            content = str(fallback.get("content") or "").strip()
            if not content:
                raise EmptyProviderStreamError(
                    "Chat provider fallback completed without assistant text"
                )
            metadata = fallback.get("metadata")
            stream_log(
                "gateway-live-chat-stream",
                "runtime",
                "live_chat_stream_fallback_completed",
                max_attempts=max_attempts,
                response_chars=len(content),
                **context,
            )
            yield {"type": "text_chunk", "text": content}
            yield {
                "type": "complete",
                "content": content,
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
            return
        except Exception as exc:
            last_error = exc

    stream_log(
        "gateway-live-chat-stream",
        "runtime",
        "live_chat_stream_retry_exhausted",
        max_attempts=max_attempts,
        error_type=type(last_error).__name__ if last_error is not None else "unknown",
        **context,
    )
    if last_error is not None:
        raise last_error
    raise RuntimeError("Chat provider stream failed without an error")


def stream_with_retry(
    stream_factory: Callable[[], Iterator[dict[str, Any]]],
    fallback_factory: Callable[[], dict[str, Any]],
    *,
    provider_id: str | None,
    model_id: str | None,
) -> Iterator[dict[str, Any]]:
    """Apply bounded retry/fallback policy to the direct Chat stream stage."""
    return retry_provider_stream(
        stream_factory,
        fallback_factory,
        attempts=_stream_attempts(),
        retry_base_delay_ms=_retry_base_delay_ms(),
        retry_max_delay_ms=_retry_max_delay_ms(),
        fallback_delay_ms=_fallback_delay_ms(),
        diagnostic_context={
            "provider_id": provider_id or "default",
            "model_configured": bool(model_id),
        },
    )
