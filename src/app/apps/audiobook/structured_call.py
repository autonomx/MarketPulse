"""Audiobook model calls through the structured-output gateway (WP-8.4).

The audiobook classifiers expect one JSON object per call. The gateway decodes
it (exactly one bare object, as before), detects truncation and records
diagnostics; the callers' parsers check each task's fields and repair known
mistakes.

The request reaching the provider is the one the classifiers sent before the
gateway: no response format, no temperature, no model override. Provider-native
JSON modes would change how reasoning models answer these prompts and need a
quality evaluation first.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.providers import ChatMessage
from app.providers.base import ChatResponse
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


class _JsonObject(BaseModel):
    """One JSON object; the caller's parser validates its fields."""

    model_config = ConfigDict(extra="allow")


class _UnchangedRequestProvider:
    """Present a provider to the gateway without changing the request it receives."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.served_model: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def get_structured_capabilities(self, model: str | None = None) -> StructuredCapabilities:
        return StructuredCapabilities(preferred_modes=(StructuredMode.TEXT_JSON,))

    def chat_completion(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        for added in ("response_format", "temperature", "model"):
            kwargs.pop(added, None)
        response = self._provider.chat_completion(messages=messages, **kwargs)
        self.served_model = getattr(response, "model", None)
        if isinstance(response, ChatResponse):
            return response
        return ChatResponse(
            content=getattr(response, "content", "") or "",
            model=self.served_model or "",
            usage=getattr(response, "usage", None),
            finish_reason=getattr(response, "finish_reason", None),
        )


def json_object_call(
    provider: Any,
    messages: list[ChatMessage],
    *,
    contract_id: str,
    request_timeout_seconds: float,
    options: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Make one provider call for a JSON object; return it and the serving model.

    One provider call per request: the callers own retries, repairs and
    escalation. Unusable output (not one JSON object, empty, truncated) raises
    ``ValueError`` like the parse failure it is; a provider failure is raised
    unchanged, as before the gateway.
    """
    unchanged = _UnchangedRequestProvider(provider)
    contract = StructuredContract(
        contract_id=contract_id,
        version=1,
        output_model=_JsonObject,
        regenerate_on_semantic_failure=False,
        exact_json_object=True,
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
        value = StructuredOutputGateway(unchanged).generate(
            messages,
            contract=contract,
            retry_budget=budget,
            provider_options={"request_timeout_seconds": request_timeout_seconds, **options},
        )
    except StructuredOutputExhausted as exc:
        cause = exc.last_error
        if cause is not None and not isinstance(cause, (ProviderEmptyResponse, ProviderTruncatedResponse)):
            raise cause from None
        raise ValueError(f"{contract_id} returned an unusable response: {cause or exc}") from exc
    except StructuredOutputError as exc:
        raise ValueError(f"{contract_id} returned an unusable response: {exc}") from exc
    return value.model_dump(), unchanged.served_model
