"""Omnix-owned model transport for agent runtimes.

The endpoint is intentionally intelligence-only. It can invoke configured
BaseProvider implementations, but it cannot execute capabilities or widen a run's
authority. Requests are bound to an existing durable agent run and its ModelRef.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, cast

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.observability.tracing import set_span_attributes
from app.providers.base import ChatMessage, ChatResponse
from app.providers.catalog import CONVERSATION_SESSIONS, provider_supports
from app.providers.service import get_provider

from .budget import AgentBudgetError, default_agent_budget_manager
from .service import default_agent_run_service

router = APIRouter(prefix="/api/agent-model/v1", tags=["agent-model"])

_STREAM_END = object()
_STREAM_ITEM = object()
_STREAM_ERROR = object()
_STREAM_BUFFER = 32


# Returned by an extracted step that did not settle its caller.
_CONTINUE = object()


def normalize_llm_provider_id(provider_id: str) -> str:
    value = str(provider_id or "").strip()
    return value.removeprefix("llm:") if value.startswith("llm:") else value


def normalize_llm_model_id(provider_id: str, model_id: str) -> str:
    provider = normalize_llm_provider_id(provider_id)
    value = str(model_id or "").strip()
    prefix = f"llm:{provider}:"
    return value[len(prefix):] if value.startswith(prefix) else value


def agent_conversation_id(run_id: str, session_id: str | None = None) -> str:
    """Bind Codex conversation state to one Pi process incarnation."""
    run_key = str(run_id or "").strip()
    session_key = str(session_id or "").strip()[:128]
    return f"agent:{run_key}:{session_key}" if session_key else f"agent:{run_key}"


def _next_stream_response(iterator: Any) -> Any:
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_END


async def _stream_responses(iterator: Any):
    """Advance a synchronous provider iterator on one dedicated thread.

    Some providers intentionally hold a thread-owned lock across generator
    yields. Dispatching each ``next()`` independently through the shared
    asyncio worker pool can resume the generator on a different thread and
    make its context manager release a lock that thread did not acquire.

    The thread hands items to the event loop directly (WP-7.1): waiting for
    the next chunk parks no executor thread. ``capacity`` bounds the items in
    flight, so a slow client applies backpressure to the provider.
    """

    loop = asyncio.get_running_loop()
    bridge: asyncio.Queue[tuple[object, Any]] = asyncio.Queue()
    capacity = threading.Semaphore(_STREAM_BUFFER)
    stopped = threading.Event()

    def publish(kind: object, value: Any) -> bool:
        while not stopped.is_set():
            if not capacity.acquire(timeout=0.1):
                continue
            try:
                loop.call_soon_threadsafe(bridge.put_nowait, (kind, value))
            except RuntimeError:  # the event loop has closed
                return False
            return True
        return False

    def consume() -> None:
        try:
            for response in iterator:
                if not publish(_STREAM_ITEM, response):
                    return
        except BaseException as exc:
            publish(_STREAM_ERROR, exc)
        finally:
            publish(_STREAM_END, None)

    threading.Thread(
        target=consume,
        name="omnix-agent-model-stream",
        daemon=True,
    ).start()
    try:
        while True:
            kind, value = await bridge.get()
            capacity.release()
            if kind is _STREAM_END:
                return
            if kind is _STREAM_ERROR:
                raise value
            yield value
    finally:
        stopped.set()


class AgentModelMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = ""
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class AgentChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[AgentModelMessage]
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None


def _target_for_run(run_id: str, requested_model: str) -> tuple[str, str, str | None]:
    snapshot = default_agent_run_service().get(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    expected = f"{snapshot.spec.model.provider_id}::{snapshot.spec.model.model_id}"
    if requested_model != expected:
        raise HTTPException(status_code=403, detail="agent_model_outside_run_spec")
    return (
        normalize_llm_provider_id(snapshot.spec.model.provider_id),
        normalize_llm_model_id(
            snapshot.spec.model.provider_id,
            snapshot.spec.model.model_id,
        ),
        snapshot.spec.model.reasoning_effort,
    )


def _messages(rows: list[AgentModelMessage]) -> list[ChatMessage]:
    result: list[ChatMessage] = []
    for row in rows:
        content = row.content
        vision_images: list[dict[str, Any]] = []
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                if item_type == "text":
                    text = str(item.get("text") or "")
                    if text:
                        text_parts.append(text)
                    continue
                if item_type == "image_url":
                    image_url = item.get("image_url")
                    if isinstance(image_url, dict):
                        url = str(image_url.get("url") or "").strip()
                        if url:
                            vision_images.append({"data": url})
                    continue
                if item_type == "image":
                    data = str(item.get("data") or "").strip()
                    mime_type = str(
                        item.get("mimeType") or item.get("mime_type") or ""
                    ).strip()
                    if data and mime_type:
                        vision_images.append(
                            {"data": f"data:{mime_type};base64,{data}"}
                        )
            content = "\n".join(text_parts)
        elif not isinstance(content, str):
            content = json.dumps(content, sort_keys=True, default=str)
        result.append(
            ChatMessage(
                role=row.role,
                content=content,
                name=row.name,
                tool_calls=row.tool_calls,
                tool_call_id=row.tool_call_id,
                vision_images=vision_images or None,
            )
        )
    return result


def _authoritative_run_context(spec: Any) -> ChatMessage:
    """Re-anchor every provider call to the durable Omnix task.

    Pi's conversation history can be compacted or reconstructed between tool
    turns. The gateway is the deterministic authority boundary, so repeat the
    active task here instead of relying on the model to retain the initial
    prompt forever.
    """
    task = str(spec.task or "").strip()
    objective = str(spec.objective or task).strip()
    return ChatMessage(
        role="system",
        content=(
            "Omnix authoritative runtime context. This context is supplied by "
            "deterministic runtime code and remains active for this model call.\n"
            f"Active task: {task}\n"
            f"Active objective: {objective}\n"
            "The implementation request is already present. Continue it from the "
            "current workspace; do not ask the user to restate the task or claim that "
            "no implementation request was included. If a genuine safe blocker "
            "remains, emit exactly `CLARIFICATION_REQUIRED: <concise question>` so "
            "Omnix can pause durably."
        ),
    )


def _kwargs(request: AgentChatCompletionRequest, default_effort: str | None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if request.tools is not None:
        values["tools"] = request.tools
    if request.tool_choice is not None:
        values["tool_choice"] = request.tool_choice
    if request.temperature is not None:
        values["temperature"] = request.temperature
    if request.max_tokens is not None:
        values["max_tokens"] = request.max_tokens
    effort = request.reasoning_effort or default_effort
    if effort:
        values["reasoning_effort"] = effort
    return values


def _output_tokens(response: ChatResponse) -> int | None:
    usage = response.usage if isinstance(response.usage, dict) else {}
    for key in ("completion_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        try:
            parsed = int(cast(Any, value))
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _input_tokens(response: ChatResponse) -> int | None:
    usage = response.usage if isinstance(response.usage, dict) else {}
    for key in ("prompt_tokens", "input_tokens"):
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        try:
            parsed = int(cast(Any, value))
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _bounded_max_tokens(
    requested: int | None,
    remaining: int | None,
) -> int | None:
    if remaining is None:
        return requested
    if remaining <= 0:
        return 0
    if requested is None:
        return remaining
    return max(1, min(int(requested), remaining))


def _choice(response: ChatResponse, *, delta: bool) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if response.content:
        body["content"] = response.content
    if response.tool_calls:
        body["tool_calls"] = response.tool_calls
    if response.thinking:
        body["reasoning_content"] = response.thinking
    elif response.reasoning:
        body["reasoning_content"] = response.reasoning
    return {
        "index": 0,
        "delta" if delta else "message": (
            body if delta else {"role": "assistant", **body}
        ),
        "finish_reason": response.finish_reason,
    }


def _budget_http_exception(reason: str) -> HTTPException:
    """Expose local Omnix budget exhaustion without pretending it is HTTP rate limiting."""

    code = str(reason or "agent_budget_exhausted")[:500]
    return HTTPException(
        status_code=409,
        detail={
            "type": "agent_budget_error",
            "code": code,
            "message": code,
            "retryable": False,
            "scope": "run",
        },
        headers={
            "X-Omnix-Error-Type": "agent_budget_error",
            "X-Omnix-Error-Code": code,
            "X-Omnix-Retryable": "false",
        },
    )


def _budget_stream_error(reason: str) -> dict[str, Any]:
    code = str(reason or "agent_budget_exhausted")[:500]
    return {
        "error": {
            "message": code,
            "type": "agent_budget_error",
            "code": code,
            "retryable": False,
            "scope": "run",
        }
    }


@router.get("/models")
def list_agent_models(x_omnix_agent_run_id: str = Header(alias="X-Omnix-Agent-Run-Id")) -> dict[str, Any]:
    snapshot = default_agent_run_service().get(x_omnix_agent_run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    key = f"{snapshot.spec.model.provider_id}::{snapshot.spec.model.model_id}"
    return {
        "object": "list",
        "data": [
            {
                "id": key,
                "object": "model",
                "owned_by": "omnix",
                "metadata": {
                    "provider_id": snapshot.spec.model.provider_id,
                    "model_id": snapshot.spec.model.model_id,
                },
            }
        ],
    }


@router.post("/chat/completions")
async def agent_chat_completion(
    request: AgentChatCompletionRequest,
    x_omnix_agent_run_id: str = Header(alias="X-Omnix-Agent-Run-Id"),
    x_omnix_agent_session_id: str | None = Header(
        default=None,
        alias="X-Omnix-Agent-Session-Id",
    ),
) -> Any:
    provider_id, model_id, default_effort = await asyncio.to_thread(
        _target_for_run,
        x_omnix_agent_run_id,
        request.model,
    )
    # Agent step for traces (WP-10.4): the server span names the run and model.
    set_span_attributes(
        agent_run_id=x_omnix_agent_run_id, agent_step="model", provider_id=provider_id, model_id=model_id,
    )
    snapshot = default_agent_run_service().get(x_omnix_agent_run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    budget = default_agent_budget_manager()
    try:
        await asyncio.to_thread(
            budget.authorize_model_call,
            x_omnix_agent_run_id,
            provider_id=provider_id,
        )
        remaining_tokens = await asyncio.to_thread(
            budget.remaining_output_tokens,
            x_omnix_agent_run_id,
        )
    except AgentBudgetError as exc:
        raise _budget_http_exception(str(exc)) from exc
    provider = await asyncio.to_thread(get_provider, provider_id)
    if provider is None:
        raise HTTPException(status_code=503, detail=f"agent_provider_unavailable:{provider_id}")
    messages = [_authoritative_run_context(snapshot.spec), *_messages(request.messages)]
    kwargs = _kwargs(request, default_effort)
    if provider_supports(provider_id, CONVERSATION_SESSIONS):
        kwargs["conversation_id"] = agent_conversation_id(
            x_omnix_agent_run_id,
            x_omnix_agent_session_id,
        )
    bounded_tokens = _bounded_max_tokens(
        kwargs.get("max_tokens"),
        remaining_tokens,
    )
    if bounded_tokens == 0:
        await asyncio.to_thread(
            budget.fail,
            x_omnix_agent_run_id,
            "budget_output_tokens_exhausted",
        )
        raise _budget_http_exception("budget_output_tokens_exhausted")
    if bounded_tokens is not None:
        kwargs["max_tokens"] = bounded_tokens
    completion_id = f"chatcmpl-omnix-{x_omnix_agent_run_id[:16]}"
    created = int(time.time())

    outcome = await _complete_without_streaming(request, provider, messages, model_id, kwargs, budget, x_omnix_agent_run_id, provider_id, completion_id, created)
    if outcome is not _CONTINUE:
        return outcome

    iterator = await asyncio.to_thread(
        cast(Any, provider.chat_completion),
        messages,
        model=model_id,
        stream=True,
        **kwargs,
    )

    return _stream_completion(iterator, completion_id, created, request, budget, x_omnix_agent_run_id, provider_id)


async def _complete_without_streaming(request, provider, messages, model_id, kwargs, budget, x_omnix_agent_run_id, provider_id, completion_id, created):
    """One provider call, metered against the run budget, as a chat.completion body."""
    if not request.stream:
        response = await asyncio.to_thread(
            provider.chat_completion,
            messages,
            model=model_id,
            stream=False,
            **kwargs,
        )
        if not isinstance(response, ChatResponse):
            raise HTTPException(status_code=502, detail="agent_provider_invalid_response")
        input_tokens = _input_tokens(response)
        output_tokens = _output_tokens(response)
        if output_tokens is None:
            if await asyncio.to_thread(
                budget.token_metering_required,
                x_omnix_agent_run_id,
            ):
                await asyncio.to_thread(
                    budget.fail,
                    x_omnix_agent_run_id,
                    "budget_output_tokens_unmeterable",
                )
                raise HTTPException(
                    status_code=502,
                    detail="budget_output_tokens_unmeterable",
                )
        if input_tokens is not None or output_tokens is not None:
            try:
                await asyncio.to_thread(
                    budget.record_token_usage,
                    x_omnix_agent_run_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    provider_id=provider_id,
                )
            except AgentBudgetError as exc:
                raise _budget_http_exception(str(exc)) from exc
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": request.model,
            "choices": [_choice(response, delta=False)],
            "usage": response.usage or {},
        }
    return _CONTINUE


def _stream_completion(iterator, completion_id, created, request, budget, x_omnix_agent_run_id, provider_id):
    """Stream the provider's chunks as SSE, then meter tokens; budget and provider errors end the stream with an error event."""
    async def generate():
        observed_input_tokens: int | None = None
        observed_output_tokens: int | None = None
        observed_finish_reason = False
        try:
            async for response in _stream_responses(iterator):
                if not isinstance(response, ChatResponse):
                    continue
                current_input_tokens = _input_tokens(response)
                if current_input_tokens is not None:
                    observed_input_tokens = current_input_tokens
                current_output_tokens = _output_tokens(response)
                if current_output_tokens is not None:
                    observed_output_tokens = max(
                        observed_output_tokens or 0,
                        current_output_tokens,
                    )
                if response.finish_reason:
                    observed_finish_reason = True
                payload = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model,
                    "choices": [_choice(response, delta=True)],
                }
                if response.usage:
                    payload["usage"] = response.usage
                yield f"data: {json.dumps(payload, sort_keys=True)}\n\n"
            if not observed_finish_reason:
                payload = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                }
                yield f"data: {json.dumps(payload, sort_keys=True)}\n\n"
            if observed_output_tokens is None:
                if await asyncio.to_thread(
                    budget.token_metering_required,
                    x_omnix_agent_run_id,
                ):
                    await asyncio.to_thread(
                        budget.fail,
                        x_omnix_agent_run_id,
                        "budget_output_tokens_unmeterable",
                    )
                    payload = _budget_stream_error("budget_output_tokens_unmeterable")
                    yield f"data: {json.dumps(payload, sort_keys=True)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
            if observed_input_tokens is not None or observed_output_tokens is not None:
                try:
                    await asyncio.to_thread(
                        budget.record_token_usage,
                        x_omnix_agent_run_id,
                        input_tokens=observed_input_tokens,
                        output_tokens=observed_output_tokens,
                        provider_id=provider_id,
                    )
                except AgentBudgetError as exc:
                    payload = _budget_stream_error(str(exc))
                    yield f"data: {json.dumps(payload, sort_keys=True)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
            yield "data: [DONE]\n\n"
        except AgentBudgetError as exc:
            payload = _budget_stream_error(str(exc))
            yield f"data: {json.dumps(payload, sort_keys=True)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            payload = {
                "error": {
                    "message": f"{type(exc).__name__}: {exc}"[:1000],
                    "type": "agent_model_error",
                }
            }
            yield f"data: {json.dumps(payload, sort_keys=True)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
