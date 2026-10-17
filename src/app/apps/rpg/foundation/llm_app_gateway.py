from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional, TypeVar

from pydantic import BaseModel

from app.providers.base import ChatMessage, ChatResponse
from app.providers.structured import (
    StructuredContract,
    StructuredDiagnostics,
    StructuredOutputGateway,
    StructuredRetryBudget,
)
from app.providers.structured.parsing import decode_json_object
from app.apps.rpg.foundation.semantic_packet_contract import SemanticPacketEnvelope

logger = logging.getLogger(__name__)
_RPG_LLM_TIMING_CONTEXT: ContextVar[Dict[str, Any]] = ContextVar(
    "RPG_LLM_TIMING_CONTEXT",
    default={},
)
T = TypeVar("T", bound=BaseModel)


@contextmanager
def rpg_llm_timing_context(**context: Any) -> Iterator[None]:
    token = _RPG_LLM_TIMING_CONTEXT.set(dict(context))
    try:
        yield
    finally:
        _RPG_LLM_TIMING_CONTEXT.reset(token)


def _current_llm_timing_context() -> Dict[str, Any]:
    value = _RPG_LLM_TIMING_CONTEXT.get()
    return value if isinstance(value, dict) else {}


class AppLLMGateway:
    """Adapter from the centralized provider API to RPG generation operations."""

    def __init__(
        self,
        provider: Any,
        *,
        global_system_prompt: str = "",
        default_temperature: Optional[float] = None,
    ):
        self.provider = provider
        self.global_system_prompt = global_system_prompt or ""
        self.default_temperature = default_temperature
        self.last_structured_diagnostics: StructuredDiagnostics | None = None

    def _build_messages(
        self,
        prompt: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[ChatMessage]:
        logger.debug(
            "[RPG GATEWAY] Building messages, prompt length: %d, context keys: %s",
            len(prompt),
            list(context.keys()) if context else [],
        )
        system_text = (
            "You are a deterministic RPG narration engine. "
            "Your only task is to generate structured RPG narration responses. "
            "Return only the requested content in the exact format specified. "
            "Do not add extra text, explanations, or commentary."
        )
        user_parts: List[str] = [prompt.strip()]
        if context:
            try:
                context_text = json.dumps(context, ensure_ascii=False, sort_keys=True)
                logger.debug("[RPG GATEWAY] Context JSON length: %d", len(context_text))
            except Exception as exc:
                logger.warning("[RPG GATEWAY] Failed to serialize context: %s", exc)
                context_text = "{}"
            user_parts.append("Context JSON:")
            user_parts.append(context_text)
        user_text = "\n\n".join(part for part in user_parts if part).strip()
        messages: List[ChatMessage] = []
        if system_text:
            messages.append(ChatMessage(role="system", content=system_text))
        messages.append(ChatMessage(role="user", content=user_text))
        logger.debug("[RPG GATEWAY] Built %d messages", len(messages))
        return messages

    def generate(
        self,
        prompt: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        timeout_s: Optional[float] = None,
        provider_options: Optional[Dict[str, Any]] = None,
    ) -> str:
        t0 = time.monotonic()
        timing = _current_llm_timing_context()
        request_started_at = timing.get("request_started_at")
        request_to_generate_start_s = None
        if isinstance(request_started_at, (int, float)):
            request_to_generate_start_s = max(0.0, t0 - float(request_started_at))
        logger.info(
            "[RPG GATEWAY] generate_start prompt_len=%d timeout_s=%s "
            "request_to_generate_start_s=%s request_id=%s stage=%s",
            len(prompt),
            timeout_s,
            (
                f"{request_to_generate_start_s:.3f}"
                if request_to_generate_start_s is not None
                else ""
            ),
            timing.get("request_id", ""),
            timing.get("stage", ""),
        )
        messages = self._build_messages(prompt, context=context)
        try:
            response = self.provider.chat_completion(
                messages=messages,
                stream=False,
                **dict(provider_options or {}),
            )
            logger.info(
                "[RPG GATEWAY] generate_end dt=%.3fs response_type=%s",
                time.monotonic() - t0,
                type(response).__name__,
            )
        except Exception:
            logger.exception("[RPG GATEWAY] Provider call failed")
            raise
        if isinstance(response, ChatResponse):
            content = (response.content or "").strip()
            logger.info("[RPG GATEWAY] ChatResponse content length: %d", len(content))
            return content
        if response is None:
            logger.warning("[RPG GATEWAY] Provider returned None")
            return ""
        return str(response).strip()

    def generate_typed(
        self,
        prompt: str,
        *,
        output_model: type[T],
        contract_id: str,
        contract_version: int = 1,
        context: Optional[Dict[str, Any]] = None,
        timeout_s: float = 90.0,
        max_provider_calls: int = 3,
        max_format_downgrades: int = 1,
        max_validation_regenerations: int = 1,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        schema_profile: str = "default",
        schema_name: str = "",
        semantic_validator: Any | None = None,
        provider_options: Optional[Dict[str, Any]] = None,
    ) -> T:
        """Return one validated Pydantic value through the shared boundary."""

        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(self.provider)
        config = getattr(self.provider, "config", None)
        outcome = gateway.try_generate(
            self._build_messages(prompt, context=context),
            contract=StructuredContract(
                contract_id=contract_id,
                version=contract_version,
                output_model=output_model,
                semantic_validator=semantic_validator,
                temperature=temperature,
                max_tokens=max_tokens,
                schema_profile=schema_profile,
                schema_name=schema_name,
            ),
            model=str(getattr(config, "model", "") or "") or None,
            retry_budget=StructuredRetryBudget(
                max_provider_calls=max(1, int(max_provider_calls)),
                max_transport_retries=max(0, int(max_provider_calls) - 1),
                max_format_downgrades=max(0, int(max_format_downgrades)),
                max_validation_regenerations=max(
                    0, int(max_validation_regenerations)
                ),
                deadline_seconds=max(0.1, float(timeout_s)),
            ),
            provider_options=provider_options,
        )
        self.last_structured_diagnostics = outcome.diagnostics
        if outcome.error is not None:
            raise outcome.error
        assert outcome.value is not None
        return outcome.value

    def generate_stream(
        self,
        prompt: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        timeout_s: Optional[float] = None,
    ) -> Iterator[Dict[str, Any]]:
        t0 = time.monotonic()
        chunk_count = 0
        first_chunk_at = None
        timing = _current_llm_timing_context()
        request_started_at = timing.get("request_started_at")
        request_to_stream_start_s = None
        if isinstance(request_started_at, (int, float)):
            request_to_stream_start_s = max(0.0, t0 - float(request_started_at))
        logger.info(
            "[RPG GATEWAY] stream_start prompt_len=%d timeout_s=%s "
            "request_to_stream_start_s=%s request_id=%s stage=%s",
            len(prompt),
            timeout_s,
            (
                f"{request_to_stream_start_s:.3f}"
                if request_to_stream_start_s is not None
                else ""
            ),
            timing.get("request_id", ""),
            timing.get("stage", ""),
        )
        messages = self._build_messages(prompt, context=context)
        try:
            for chunk in self.provider.chat_completion(messages=messages, stream=True):
                if first_chunk_at is None:
                    first_chunk_at = time.monotonic()
                    logger.info(
                        "[RPG GATEWAY] stream_first_chunk dt=%.3fs",
                        first_chunk_at - t0,
                    )
                chunk_count += 1
                if isinstance(chunk, ChatResponse):
                    content = chunk.content or ""
                    if content:
                        yield {"text": content}
                else:
                    logger.warning(
                        "[RPG GATEWAY] Unexpected chunk type during streaming: %s",
                        type(chunk),
                    )
            logger.info(
                "[RPG GATEWAY] stream_end total_dt=%.3fs chunk_count=%d",
                time.monotonic() - t0,
                chunk_count,
            )
        except Exception:
            logger.exception("[RPG GATEWAY] Streaming provider call failed")
            raise

    def complete(self, prompt: str) -> Dict[str, Any]:
        response = self.generate(prompt)
        if isinstance(response, dict):
            return {
                "text": str(response.get("text") or response.get("content") or ""),
                "raw": response,
            }
        return {"text": str(response or ""), "raw": response}

    def complete_json(self, prompt: str) -> Dict[str, Any]:
        """Deprecated compatibility method that fails explicitly on invalid output.

        Registered structured operations must use `generate_typed`. This wrapper is
        retained for legacy callers, but malformed or empty content can no longer be
        confused with a legitimate empty object.
        """

        response = self.complete(prompt)
        return decode_json_object(str(response.get("text") or ""))

    def complete_semantic_packet(
        self,
        prompt: str,
        *,
        response_schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return a fully nested, strictly typed semantic packet.

        ``response_schema`` remains accepted for caller compatibility. The Pydantic
        contract is authoritative and rejects nested coercion, invalid enums, missing
        fields, and unexpected fields before normalization can observe the packet.
        """

        del response_schema
        value = self.generate_typed(
            prompt,
            output_model=SemanticPacketEnvelope,
            contract_id="rpg.semantic_action.packet",
            contract_version=3,
            timeout_s=15.0,
            max_provider_calls=2,
            max_format_downgrades=1,
            max_validation_regenerations=1,
            temperature=0.3,
            max_tokens=2400,
            schema_profile="local",
            schema_name="rpg_semantic_packet",
            provider_options={
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        payload = value.model_dump(mode="python")
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return {"text": text, "raw": payload}

    def call(
        self,
        method: str,
        prompt: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if method == "generate":
            return self.generate(prompt, context=context)
        if method == "generate_stream":
            return self.generate_stream(prompt, context=context)
        raise ValueError(f"Unsupported AppLLMGateway method: {method}")


def build_app_llm_gateway() -> Optional[AppLLMGateway]:
    """Build an RPG gateway from the application's centralized provider layer."""

    try:
        from app.apps.rpg.foundation.provider_access import routed_provider
        from app.providers import service as provider_service

        provider = routed_provider()
        if not provider:
            logger.debug(
                "RPG LLM gateway unavailable: app.provider_service.get_provider() returned no provider"
            )
            return None
        logger.debug("RPG LLM gateway created using centralized app provider")
        global_system_prompt = ""
        try:
            global_system_prompt = provider_service.get_global_system_prompt() or ""
        except Exception:
            logger.debug("No global system prompt available", exc_info=True)
        return AppLLMGateway(
            provider,
            global_system_prompt=global_system_prompt,
        )
    except Exception:
        logger.exception("Failed to build app LLM gateway for RPG runtime")
        return None
