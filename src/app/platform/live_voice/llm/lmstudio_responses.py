"""Bounded LM Studio Responses state reuse for accepted live-chat turns.

The stable provider context remains authoritative: system/persona/memory/summary
content must match exactly. The live voice recent-message window is allowed to
advance only by dropping messages from its front while preserving an exact
suffix of the previous authoritative conversation tail. Provider-side state is
periodically reseeded so dropped rolling-window history cannot accumulate
without bound. Turns carrying ephemeral external/retrieved context are never
remembered for a later continuation.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from app.platform.chat.contracts import (
    merge_provider_response_metrics,
    model_key,
    provider_turn_deadline,
    remaining_turn_seconds,
)
from app.providers import ChatMessage as ProviderMessage
from app.providers.base import ChatResponse, ConnectionError
from app.providers.lmstudio_provider import LMStudioProvider

from app.platform.live_voice.llm.stream import LowLatencyTextChunker
from app.platform.live_voice.llm import lmstudio_model_resolution as model_resolution
from app.observability.tts_stream_diagnostics import stream_log

_RESPONSES_ENDPOINT = "/v1/responses"
_STATE_ENV = "OMNIX_LIVE_LMSTUDIO_STATEFUL_RESPONSES"
_STATE_TTL_ENV = "OMNIX_LIVE_LMSTUDIO_RESPONSE_STATE_TTL_SECONDS"
_MAX_CONTINUATIONS_ENV = "OMNIX_LIVE_LMSTUDIO_RESPONSE_MAX_CONTINUATIONS"
_DEFAULT_STATE_TTL_SECONDS = 900.0
_DEFAULT_MAX_CONTINUATIONS = 4
_MAX_STATES = 64
_ALLOWED_ROLES = frozenset({"system", "developer", "user", "assistant"})
_TURN_ROLES = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class _ResponseState:
    response_id: str
    model_id: str
    context_fingerprint: str
    conversation_tail_fingerprints: tuple[str, ...]
    continuation_count: int
    updated_at: float


_STATE_LOCK = threading.RLock()
_RESPONSE_STATES: OrderedDict[str, _ResponseState] = OrderedDict()


def stateful_responses_enabled() -> bool:
    raw = _env_str(_STATE_ENV)
    if raw is None:
        return True
    return raw.strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_live_voice_user_message(user_message: Any) -> bool:
    metadata = getattr(user_message, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    user_turn_id = str(metadata.get("user_turn_id") or "").strip()
    speech_segment_id = str(metadata.get("speech_segment_id") or "").strip()
    return user_turn_id.startswith("voice-user-turn:") or speech_segment_id.startswith(
        "voice-segment:"
    )


def _state_ttl_seconds() -> float:
    raw = (_env_str(_STATE_TTL_ENV) or "").strip()
    try:
        parsed = float(raw) if raw else _DEFAULT_STATE_TTL_SECONDS
    except ValueError:
        parsed = _DEFAULT_STATE_TTL_SECONDS
    return max(30.0, min(3600.0, parsed))


def _max_continuations() -> int:
    raw = (_env_str(_MAX_CONTINUATIONS_ENV) or "").strip()
    try:
        parsed = int(raw) if raw else _DEFAULT_MAX_CONTINUATIONS
    except ValueError:
        parsed = _DEFAULT_MAX_CONTINUATIONS
    return max(1, min(16, parsed))


def prompt_fingerprint(messages: list[ProviderMessage]) -> str:
    """Hash exact provider-visible role/content without logging prompt material."""
    canonical = [
        {
            "role": str(message.role or "").strip().casefold(),
            "content": str(message.content or ""),
        }
        for message in messages
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _message_fingerprint(message: ProviderMessage) -> str:
    return prompt_fingerprint([message])


def _split_live_prompt(
    messages: list[ProviderMessage],
) -> tuple[list[ProviderMessage], list[ProviderMessage], bool]:
    """Return stable leading context, recent turns, and structural support.

    Live voice rendering places all stable system/persona/memory/summary messages
    before a contiguous user/assistant tail and the current user input. If a
    system/developer message appears after conversation turns begin, fail closed
    because that shape cannot be continued safely with one new user input.
    """
    if not messages or str(messages[-1].role or "").strip().casefold() != "user":
        return [], [], False
    prefix = messages[:-1]
    first_turn = len(prefix)
    for index, message in enumerate(prefix):
        if str(message.role or "").strip().casefold() in _TURN_ROLES:
            first_turn = index
            break
    context = prefix[:first_turn]
    recent = prefix[first_turn:]
    supported = all(
        str(message.role or "").strip().casefold() in _TURN_ROLES
        for message in recent
    )
    return context, recent, supported


def _tail_fingerprints(messages: list[ProviderMessage]) -> tuple[str, ...]:
    return tuple(_message_fingerprint(message) for message in messages)


def _is_exact_suffix(candidate: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    if not candidate or len(candidate) > len(expected):
        return False
    return expected[-len(candidate) :] == candidate


def _prune_states_locked() -> None:
    cutoff = time.time() - _state_ttl_seconds()
    for session_id, state in list(_RESPONSE_STATES.items()):
        if state.updated_at < cutoff:
            _RESPONSE_STATES.pop(session_id, None)
    while len(_RESPONSE_STATES) > _MAX_STATES:
        _RESPONSE_STATES.popitem(last=False)


def _invalidate_state(session_id: str) -> None:
    normalized = str(session_id or "").strip()
    if not normalized:
        return
    with _STATE_LOCK:
        _RESPONSE_STATES.pop(normalized, None)


def _resolve_previous_response_id(
    *,
    session_id: str,
    model_id: str | None,
    messages: list[ProviderMessage],
) -> tuple[str | None, str, int, int]:
    normalized_session = str(session_id or "").strip()
    normalized_model = str(model_id or "").strip()
    if not normalized_session:
        return None, "session_id_missing", 0, 0
    if not normalized_model:
        _invalidate_state(normalized_session)
        return None, "model_unresolved", 0, 0

    context, recent, supported = _split_live_prompt(messages)
    if not supported:
        _invalidate_state(normalized_session)
        return None, "prompt_shape_unsupported", 0, 0
    context_fingerprint = prompt_fingerprint(context)
    current_tail = _tail_fingerprints(recent)

    with _STATE_LOCK:
        _prune_states_locked()
        state = _RESPONSE_STATES.get(normalized_session)
        if state is None:
            return None, "state_missing", 0, 0
        if state.model_id != normalized_model:
            _RESPONSE_STATES.pop(normalized_session, None)
            return None, "model_changed", 0, 0
        if state.context_fingerprint != context_fingerprint:
            _RESPONSE_STATES.pop(normalized_session, None)
            return None, "stable_context_changed", 0, 0
        if state.continuation_count >= _max_continuations():
            _RESPONSE_STATES.pop(normalized_session, None)
            return None, "continuation_limit", state.continuation_count, 0
        if not _is_exact_suffix(current_tail, state.conversation_tail_fingerprints):
            _RESPONSE_STATES.pop(normalized_session, None)
            return None, "conversation_tail_changed", state.continuation_count, 0
        rolled_off = len(state.conversation_tail_fingerprints) - len(current_tail)
        _RESPONSE_STATES.move_to_end(normalized_session)
        return state.response_id, "hit", state.continuation_count, rolled_off


def _remember_response_state(
    *,
    session_id: str,
    model_id: str | None,
    request_messages: list[ProviderMessage],
    assistant_text: str,
    response_id: str | None,
    prior_continuation_count: int = 0,
    state_hit: bool = False,
    carry_allowed: bool = True,
) -> bool:
    normalized_session = str(session_id or "").strip()
    normalized_model = str(model_id or "").strip()
    normalized_response = str(response_id or "").strip()
    normalized_assistant = str(assistant_text or "").strip()
    if not carry_allowed:
        _invalidate_state(normalized_session)
        return False
    if (
        not normalized_session
        or not normalized_model
        or not normalized_response.startswith("resp_")
        or not normalized_assistant
    ):
        _invalidate_state(normalized_session)
        return False

    context, recent, supported = _split_live_prompt(request_messages)
    if not supported:
        _invalidate_state(normalized_session)
        return False
    conversation_tail = [
        *recent,
        request_messages[-1],
        ProviderMessage(role="assistant", content=normalized_assistant),
    ]
    state = _ResponseState(
        response_id=normalized_response,
        model_id=normalized_model,
        context_fingerprint=prompt_fingerprint(context),
        conversation_tail_fingerprints=_tail_fingerprints(conversation_tail),
        continuation_count=(prior_continuation_count + 1 if state_hit else 0),
        updated_at=time.time(),
    )
    with _STATE_LOCK:
        _prune_states_locked()
        _RESPONSE_STATES[normalized_session] = state
        _RESPONSE_STATES.move_to_end(normalized_session)
        _prune_states_locked()
    return True


def _clear_response_states_for_tests() -> None:
    with _STATE_LOCK:
        _RESPONSE_STATES.clear()


def _response_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = str(payload.get("id") or payload.get("response_id") or "").strip()
    return value or None


def _response_model(payload: Any, fallback: str | None) -> str:
    if not isinstance(payload, dict):
        return str(fallback or "")
    return str(
        payload.get("model")
        or payload.get("model_instance_id")
        or fallback
        or ""
    )


def _response_usage(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    return dict(usage) if isinstance(usage, dict) else None


def _responses_input(messages: list[ProviderMessage]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for message in messages:
        role = str(message.role or "").strip().casefold()
        if role not in _ALLOWED_ROLES:
            raise ValueError(f"LM Studio Responses does not support seeded role: {role or 'empty'}")
        result.append({"role": role, "content": str(message.content or "")})
    if not result:
        raise ValueError("LM Studio Responses requires at least one input message")
    return result


def _responses_payload(
    *,
    messages: list[ProviderMessage],
    model: str | None,
    previous_response_id: str | None,
    stream: bool,
) -> dict[str, Any]:
    previous = str(previous_response_id or "").strip()
    if previous:
        if not previous.startswith("resp_"):
            raise ValueError("LM Studio previous_response_id must start with 'resp_'")
        if len(messages) != 1 or str(messages[0].role or "").strip().casefold() != "user":
            raise ValueError("LM Studio Responses continuation requires exactly one new user input")
        input_value: Any = str(messages[0].content or "")
    else:
        input_value = _responses_input(messages)

    payload: dict[str, Any] = {
        "input": input_value,
        "stream": bool(stream),
        "store": True,
        "temperature": 0.7,
    }
    if model:
        payload["model"] = model
    if previous:
        payload["previous_response_id"] = previous
    return payload


def _stream_responses(
    provider: LMStudioProvider,
    *,
    messages: list[ProviderMessage],
    model: str | None,
    previous_response_id: str | None,
    request_timeout_seconds: float | None = None,
) -> Iterator[ChatResponse]:
    payload = _responses_payload(
        messages=messages,
        model=model,
        previous_response_id=previous_response_id,
        stream=True,
    )
    try:
        request_kwargs: dict[str, Any] = {"json": payload, "stream": True}
        if request_timeout_seconds is not None:
            request_kwargs["timeout"] = request_timeout_seconds
        response = provider._make_request(
            "post",
            _RESPONSES_ENDPOINT,
            **request_kwargs,
        )
    except Exception as exc:
        raise ConnectionError(f"Failed to start LM Studio Responses stream: {exc}") from exc

    model_name = str(model or "")
    event_name = ""
    completed = False
    try:
        for line in response.iter_lines():
            if not line:
                continue
            line_text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
            if line_text.startswith("event:"):
                event_name = line_text[6:].strip()
                continue
            if not line_text.startswith("data:"):
                continue
            data_text = line_text[5:].strip()
            if not data_text or data_text == "[DONE]":
                event_name = ""
                continue
            try:
                event = json.loads(data_text)
            except (TypeError, ValueError):
                event_name = ""
                continue
            if not isinstance(event, dict):
                event_name = ""
                continue
            event_type = str(event.get("type") or event_name or "")
            event_name = ""

            if event_type == "response.created":
                created = event.get("response")
                model_name = _response_model(created, model_name)
                continue
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    yield ChatResponse(
                        content=delta,
                        model=model_name,
                        raw_response=event,
                    )
                continue
            if event_type in {
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            }:
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    yield ChatResponse(
                        content="",
                        model=model_name,
                        thinking=delta,
                        reasoning=delta,
                        raw_response=event,
                    )
                continue
            if event_type in {"error", "response.failed"}:
                error = event.get("error")
                failed_response = event.get("response")
                if isinstance(failed_response, dict) and not error:
                    error = failed_response.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or error.get("code") or "response failed")
                else:
                    message = str(error or "response failed")
                raise ConnectionError(f"LM Studio Responses stream failed: {message}")
            if event_type == "response.completed":
                final = event.get("response")
                if not isinstance(final, dict):
                    raise ConnectionError("LM Studio response.completed did not include a response")
                completed = True
                model_name = _response_model(final, model_name)
                yield ChatResponse(
                    content="",
                    model=model_name,
                    usage=_response_usage(final),
                    raw_response=final,
                )
                return
        if not completed:
            raise ConnectionError("LM Studio Responses stream ended without response.completed")
    except ConnectionError:
        raise
    except Exception as exc:
        raise ConnectionError(f"LM Studio Responses stream error: {exc}") from exc
    finally:
        response.close()


def _resolve_current_model(
    provider: LMStudioProvider,
    requested_model: str | None,
) -> tuple[str | None, dict[str, Any]]:
    resolved_model, diagnostics = model_resolution.resolve_lmstudio_model(
        provider,
        requested_model,
    )
    log_fields = dict(diagnostics)
    model_source = log_fields.pop("source")
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_lmstudio_model_resolved",
        stream=True,
        transport="responses",
        model_source=model_source,
        **log_fields,
    )
    model_resolution._update_active_diagnostics(resolved_model, diagnostics)
    return resolved_model, diagnostics


def _stream_stateful_lmstudio_reply(
    self: Any,
    session: Any,
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
    context_items: list[dict[str, Any]] | None,
    provider: LMStudioProvider,
    routing_deadline_at: float | None = None,
) -> Iterator[dict[str, Any]]:
    started = time.perf_counter()
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
    requested_model = model_key(model_id)
    resolved_model, _ = _resolve_current_model(provider, requested_model)
    session_id = str(getattr(session, "id", "") or "").strip()
    previous_response_id, state_reason, prior_continuation_count, rolled_off = (
        _resolve_previous_response_id(
            session_id=session_id,
            model_id=resolved_model,
            messages=messages,
        )
    )
    state_hit = previous_response_id is not None
    carry_allowed = not bool(getattr(assembly, "external_context", None)) and not bool(
        getattr(assembly, "retrieved_history", None)
    )
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_lmstudio_response_state_resolved",
        session_id=session_id,
        state_hit=state_hit,
        reason=state_reason,
        model_id=resolved_model,
        prompt_message_count=len(messages),
        prompt_build_ms=round(prompt_build_ms, 3),
        continuation_count=prior_continuation_count,
        max_continuations=_max_continuations(),
        rolling_messages_dropped=rolled_off,
        carry_allowed=carry_allowed,
    )

    transport_messages = [messages[-1]] if state_hit else messages
    deadline = provider_turn_deadline(
        provider_id,
        session_provider_id=getattr(session, "provider_id", None),
        existing_deadline_at=routing_deadline_at,
    )
    remaining_budget = remaining_turn_seconds(deadline)
    if remaining_budget is not None and remaining_budget <= 0:
        from app.providers.structured.errors import ProviderTimeout

        raise ProviderTimeout("chat turn deadline has expired")
    response = _stream_responses(
        provider,
        messages=transport_messages,
        model=resolved_model,
        previous_response_id=previous_response_id,
        request_timeout_seconds=remaining_budget,
    )
    chunker = LowLatencyTextChunker()
    full_text = ""
    usage: dict[str, Any] | None = None
    provider_metrics: dict[str, Any] = {}
    provider_iteration_started = time.perf_counter()
    first_provider_text_ms: float | None = None
    first_client_chunk_ms: float | None = None
    final_raw: dict[str, Any] | None = None
    final_model = resolved_model

    for chunk in response:
        final_model = getattr(chunk, "model", None) or final_model
        usage = getattr(chunk, "usage", None) or usage
        provider_metrics = merge_provider_response_metrics(
            provider_metrics,
            chunk,
            provider_id="lmstudio",
        )
        raw = getattr(chunk, "raw_response", None)
        if isinstance(raw, dict) and _response_id(raw):
            final_raw = raw
        text = str(getattr(chunk, "content", "") or "")
        if not text:
            continue
        if first_provider_text_ms is None:
            first_provider_text_ms = (
                time.perf_counter() - provider_iteration_started
            ) * 1000.0
            stream_log(
                "gateway-live-chat-first-token",
                "runtime",
                "live_chat_lmstudio_first_provider_text",
                transport="responses",
                state_hit=state_hit,
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
                    transport="responses",
                    state_hit=state_hit,
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

    normalized_text = full_text.strip()
    if not normalized_text:
        _invalidate_state(session_id)
        raise RuntimeError("Chat response was empty")

    response_id = _response_id(final_raw)
    state_updated = _remember_response_state(
        session_id=session_id,
        model_id=resolved_model,
        request_messages=messages,
        assistant_text=normalized_text,
        response_id=response_id,
        prior_continuation_count=prior_continuation_count,
        state_hit=state_hit,
        carry_allowed=carry_allowed,
    )
    next_continuation_count = prior_continuation_count + 1 if state_hit else 0
    cached_input_tokens = provider_metrics.get("cached_input_tokens")
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_lmstudio_response_state_updated",
        session_id=session_id,
        state_hit=state_hit,
        state_updated=state_updated,
        response_id_available=bool(response_id),
        model_id=resolved_model,
        continuation_count=(next_continuation_count if state_updated else None),
        max_continuations=_max_continuations(),
        carry_allowed=carry_allowed,
        input_tokens=provider_metrics.get("input_tokens"),
        cached_input_tokens=cached_input_tokens,
        prompt_cache_hit_ratio=provider_metrics.get("prompt_cache_hit_ratio"),
    )
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_lmstudio_stream_completed",
        transport="responses",
        state_hit=state_hit,
        prompt_build_ms=round(prompt_build_ms, 3),
        provider_first_text_ms=(
            round(first_provider_text_ms, 3)
            if first_provider_text_ms is not None
            else None
        ),
        first_client_chunk_ms=(
            round(first_client_chunk_ms, 3)
            if first_client_chunk_ms is not None
            else None
        ),
        input_tokens=provider_metrics.get("input_tokens"),
        cached_input_tokens=cached_input_tokens,
        output_tokens=provider_metrics.get("output_tokens"),
        total_ms=round((time.perf_counter() - started) * 1000.0, 3),
    )
    yield {
        "type": "complete",
        "content": normalized_text,
        "metadata": {
            "generation_status": "completed",
            "provider_id": provider_id,
            "model_id": model_id,
            "resolved_model": final_model,
            "live_lmstudio_transport": "responses",
            "live_lmstudio_response_state_hit": state_hit,
            **self._active_memory_metadata(assembly, rendered),
            **self._active_history_metadata(assembly),
            **({"usage": usage} if usage else {}),
            **({"provider_metrics": provider_metrics} if provider_metrics else {}),
        },
    }


def stream_lmstudio_reply(
    self: Any,
    session: Any,
    user_message: Any,
    *,
    provider_id: str | None,
    model_id: str | None,
    context_items: list[dict[str, Any]] | None,
    provider: Any | None,
    fallback_stream: Callable[..., Iterator[dict[str, Any]]],
    routing_deadline_at: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Use bounded Responses state for accepted voice turns, then fail closed."""
    fallback_kwargs = {
        "provider_id": provider_id,
        "model_id": model_id,
        "context_items": context_items,
        "provider": provider,
        "routing_deadline_at": routing_deadline_at,
    }
    if (
        not stateful_responses_enabled()
        or not _is_live_voice_user_message(user_message)
        or not isinstance(provider, LMStudioProvider)
    ):
        yield from fallback_stream(self, session, user_message, **fallback_kwargs)
        return

    session_id = str(getattr(session, "id", "") or "").strip()
    emitted = False
    try:
        for event in _stream_stateful_lmstudio_reply(
            self,
            session,
            user_message,
            provider_id=provider_id,
            model_id=model_id,
            context_items=context_items,
            provider=provider,
            routing_deadline_at=routing_deadline_at,
        ):
            emitted = True
            yield event
    except Exception as exc:
        _invalidate_state(session_id)
        if emitted:
            raise
        stream_log(
            "gateway-live-chat-first-token",
            "runtime",
            "live_chat_lmstudio_responses_fallback",
            session_id=session_id,
            error_type=type(exc).__name__,
            fallback_transport="chat_completions",
        )
        yield from fallback_stream(self, session, user_message, **fallback_kwargs)


__all__ = [
    "prompt_fingerprint",
    "stateful_responses_enabled",
    "stream_lmstudio_reply",
]
