"""Live chat SSE response behavior owned by the live-voice feature."""
from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from typing import Any

from starlette.responses import StreamingResponse

from app.config.env import env_str
from app.platform.chat.contracts import eager_async_sse_stream
from app.observability.tts_stream_diagnostics import stream_log

_DEFAULT_PREAMBLE_BYTES = 2_048
_MAX_PREAMBLE_BYTES = 8_192
_TRANSPORT_VERSION = "immediate-v4"


def _configured_preamble_bytes() -> int:
    raw = env_str("OMNIX_SSE_FLUSH_PREAMBLE_BYTES")
    try:
        value = int(raw) if raw is not None else _DEFAULT_PREAMBLE_BYTES
    except (TypeError, ValueError):
        value = _DEFAULT_PREAMBLE_BYTES
    return max(0, min(_MAX_PREAMBLE_BYTES, value))


def _flush_preamble(size: int) -> bytes:
    if size <= 0:
        return b""
    if size <= 3:
        return b":\n\n"[:size]
    return b":" + (b" " * (size - 3)) + b"\n\n"


def _is_event_stream(
    media_type: str | None,
    headers: Mapping[str, str] | None,
) -> bool:
    if str(media_type or "").lower().startswith("text/event-stream"):
        return True
    return any(
        name.lower() == "content-type"
        and str(value).lower().startswith("text/event-stream")
        for name, value in (headers or {}).items()
    )


def _event_stream_headers(
    headers: Mapping[str, str] | None,
    *,
    execution_mode: str,
) -> dict[str, str]:
    result = dict(headers or {})
    cache_key = next(
        (name for name in result if name.lower() == "cache-control"),
        "Cache-Control",
    )
    cache_tokens = {
        token.strip().lower()
        for token in str(result.get(cache_key, "")).split(",")
        if token.strip()
    }
    cache_tokens.update({"no-cache", "no-transform"})
    result[cache_key] = ", ".join(sorted(cache_tokens))
    if not any(name.lower() == "x-accel-buffering" for name in result):
        result["X-Accel-Buffering"] = "no"
    if not any(name.lower() == "connection" for name in result):
        result["Connection"] = "keep-alive"
    result["X-Omnix-SSE-Transport"] = _TRANSPORT_VERSION
    result["X-Omnix-SSE-Execution"] = execution_mode
    return result


async def _prepend_async(content: AsyncIterable[Any], preamble: bytes):
    if preamble:
        yield preamble
    async for chunk in content:
        yield chunk


def _prepend_sync(content: Iterable[Any], preamble: bytes):
    if preamble:
        yield preamble
    yield from content


class OmnixStreamingResponse(StreamingResponse):
    """SSE response with explicit eager execution for the live-chat route only."""

    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: Any | None = None,
        *,
        eager_sync: bool = False,
        diagnostic_context: dict[str, Any] | None = None,
    ) -> None:
        if _is_event_stream(media_type, headers):
            preamble = _flush_preamble(_configured_preamble_bytes())
            if isinstance(content, AsyncIterable):
                content = _prepend_async(content, preamble)
                execution_mode = "async-native"
            elif eager_sync:
                source = content

                def produce() -> Iterable[Any]:
                    return _prepend_sync(source, preamble)

                content = eager_async_sse_stream(
                    produce,
                    diagnostic_context=diagnostic_context,
                )
                execution_mode = "eager-route"
            else:
                content = _prepend_sync(content, preamble)
                execution_mode = "starlette-sync"
            headers = _event_stream_headers(
                headers,
                execution_mode=execution_mode,
            )
            stream_log(
                "gateway-live-chat-async-sse",
                "runtime",
                "live_chat_sse_response_created",
                execution_mode=execution_mode,
                **(diagnostic_context or {}),
            )
        super().__init__(
            content,
            status_code=status_code,
            headers=dict(headers or {}),
            media_type=media_type,
            background=background,
        )


__all__ = ["OmnixStreamingResponse"]
