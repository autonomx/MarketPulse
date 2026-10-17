"""Trading research model calls through the structured-output gateway (WP-8.4).

The trading research analyzers (AI shadow v2/v3, intraday LLM) ask
for one JSON object per call. The gateway decodes it (a fenced object is
accepted, as before), detects truncation, validates it against the caller's
model and records diagnostics; the analyzers keep their own checks of which
instruments were answered.

The request reaching the provider is the one the analyzers sent before the
gateway: a strict JSON schema for providers that validate closed object
schemas (``CLOSED_OBJECT_SCHEMA``), otherwise a JSON-object response format,
temperature 0 and the caller's token limit; a provider that does not accept
those arguments is called with the messages and model only. One provider call
per request: the analyzers own retries.

Model output is research evidence only; nothing here grants execution
authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

from app.providers import ChatMessage
from app.providers.base import ChatResponse
from app.providers.catalog import CLOSED_OBJECT_SCHEMA, NATIVE_JSON_SCHEMA, provider_id_of, provider_supports
from app.providers.structured import (
    ProviderEmptyResponse,
    ProviderTruncatedResponse,
    StructuredCapabilities,
    StructuredContract,
    StructuredMode,
    StructuredOutputError,
    StructuredOutputExhausted,
    StructuredOutputGateway,
    StructuredRetryBudget,
)
from app.providers.structured.errors import StructuredDecodeError, StructuredSchemaError
from app.providers.structured.schema_projection import project_provider_schema

from .trading_ai_budget import TradingAIBudget, default_trading_ai_budget

T = TypeVar("T", bound=BaseModel)
OutputFailure = Literal["empty", "truncated", "syntax", "schema"]


class TradingModelOutputError(ValueError):
    """The model answered, but not with one valid object for the contract."""

    def __init__(self, reason: OutputFailure, detail: str, *, response: Any = None) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.response = response


@dataclass(frozen=True)
class TradingModelReply(Generic[T]):
    value: T
    response: Any  # the provider's reply: content, usage and serving model


# Providers whose native schema support validates the reply before returning it:
# the Claude CLI passes ``--json-schema`` and returns ``structured_output``.
_VALIDATING_NATIVE_SCHEMA_PROVIDERS = frozenset({"claude_cli"})


def sends_json_schema(provider: Any) -> bool:
    """Whether the provider receives the output's JSON schema rather than a bare JSON-object request.

    The Claude CLI gets it too: without it the model saw only a prose description
    of the shape, and most of its research replies failed schema validation.
    """
    if provider_supports(provider, CLOSED_OBJECT_SCHEMA):
        return True
    return provider_supports(provider, NATIVE_JSON_SCHEMA) and (
        provider_id_of(provider) in _VALIDATING_NATIVE_SCHEMA_PROVIDERS
    )


def trading_response_format(provider: Any, output_model: type[BaseModel], schema_name: str) -> dict[str, Any]:
    """The response format the analyzers send: the output's schema, or a JSON object."""
    if not sends_json_schema(provider):
        return {"type": "json_object"}
    schema = project_provider_schema(
        output_model.model_json_schema(),
        mode=StructuredMode.JSON_SCHEMA,
        provider_name=provider_id_of(provider),
    )
    return {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "strict": True, "schema": schema},
    }


class _AnalyzerRequest:
    """Present a provider to the gateway while sending the analyzer's own request."""

    def __init__(self, provider: Any, request: dict[str, Any]) -> None:
        self._provider = provider
        self._request = request
        self.response: Any = None
        self.error: BaseException | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def get_structured_capabilities(self, model: str | None = None) -> StructuredCapabilities:
        return StructuredCapabilities(preferred_modes=(StructuredMode.TEXT_JSON,))

    def chat_completion(self, messages: list[ChatMessage], **_gateway_options: Any) -> ChatResponse:
        model = self._request.get("model")
        try:
            try:
                response = self._provider.chat_completion(messages=messages, stream=False, **self._request)
            except TypeError:
                response = self._provider.chat_completion(messages=messages, model=model, stream=False)
        except BaseException as exc:
            # The gateway classifies provider failures; callers get the original.
            self.error = exc
            raise
        self.response = response
        if isinstance(response, ChatResponse):
            return response
        return ChatResponse(
            content=str(getattr(response, "content", "") or ""),
            model=str(getattr(response, "model", "") or model or ""),
            usage=getattr(response, "usage", None),
            finish_reason=getattr(response, "finish_reason", None),
        )


def trading_model_call(
    provider: Any,
    messages: list[ChatMessage],
    *,
    output_model: type[T],
    contract_id: str,
    schema_name: str,
    model: str | None,
    max_tokens: int,
    request_timeout_seconds: float = 45.0,
    response_format: dict[str, Any] | None = None,
    budget: TradingAIBudget | None = None,
) -> TradingModelReply[T]:
    """One provider call for one ``output_model`` object.

    Unusable output raises ``TradingModelOutputError`` (its ``response`` is
    the provider reply when there was one); a provider failure is raised
    unchanged. A call over the day's research budget for its priority raises
    ``TradingAIBudgetExhausted`` before reaching the provider.
    """
    (budget or default_trading_ai_budget()).acquire(contract_id)
    request = {
        "model": model,
        "response_format": response_format or trading_response_format(provider, output_model, schema_name),
        "request_timeout_seconds": request_timeout_seconds,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    analyzer_request = _AnalyzerRequest(provider, request)
    contract = StructuredContract(
        contract_id=contract_id,
        version=1,
        output_model=output_model,
        schema_name=schema_name,
        regenerate_on_semantic_failure=False,
    )
    budget = StructuredRetryBudget(
        max_provider_calls=1,
        max_transport_retries=0,
        max_format_downgrades=0,
        max_validation_regenerations=0,
        # Longer than the request timeout, so the provider receives all of it.
        deadline_seconds=request_timeout_seconds + 5.0,
    )
    try:
        value = StructuredOutputGateway[T](analyzer_request).generate(
            messages, contract=contract, model=model, retry_budget=budget,
        )
    except (StructuredOutputExhausted, StructuredOutputError) as exc:
        cause = exc.last_error if isinstance(exc, StructuredOutputExhausted) else exc
        reason: OutputFailure | None = (
            "empty" if isinstance(cause, ProviderEmptyResponse)
            else "truncated" if isinstance(cause, ProviderTruncatedResponse)
            else "syntax" if isinstance(cause, StructuredDecodeError)
            else "schema" if isinstance(cause, StructuredSchemaError)
            else None
        )
        if reason is None:
            if analyzer_request.error is not None:
                raise analyzer_request.error from None
            if isinstance(cause, Exception) and cause is not exc:
                raise cause from None
            raise
        raise TradingModelOutputError(reason, str(cause), response=analyzer_request.response) from cause
    return TradingModelReply(value=value, response=analyzer_request.response)


__all__ = [
    "TradingModelOutputError",
    "sends_json_schema",
    "TradingModelReply",
    "trading_model_call",
    "trading_response_format",
]
