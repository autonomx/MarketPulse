"""HTTP client for the Hermes sidecar, shared by every feature that consults it.

The client knows the sidecar's endpoints and how to get one validated JSON
object back through the structured-output gateway. What to ask (the prompt and
the output model) belongs to the feature asking.
"""
from __future__ import annotations

import json
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
from app.runtime.http_client import shared_http_client


class HermesSidecarError(RuntimeError):
    pass


class JsonObject(BaseModel):
    """One JSON object, checked further by the caller."""

    model_config = ConfigDict(extra="allow")


class _HermesChatProvider:
    """The sidecar's chat endpoint as a provider for the structured-output gateway.

    The request is the one the client sent before the gateway: model
    ``hermes-agent``, no temperature, and ``response_format`` JSON object only
    for the calls that asked for it.
    """

    provider_name = "hermes"
    config = None

    def __init__(self, client: "HermesSidecarClient", *, json_mode: bool, timeout: float) -> None:
        self._client = client
        self._json_mode = json_mode
        self._timeout = timeout

    def get_structured_capabilities(self, model: str | None = None) -> StructuredCapabilities:
        mode = StructuredMode.JSON_OBJECT if self._json_mode else StructuredMode.TEXT_JSON
        return StructuredCapabilities(preferred_modes=(mode,))

    def chat_completion(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": "hermes-agent",
            "stream": False,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        timeout = min(self._timeout, float(kwargs.get("request_timeout_seconds") or self._timeout))
        response = self._client.http.post(
            f"{self._client.base_url}/v1/chat/completions",
            headers=self._client._headers(),
            data=json.dumps(payload),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = self._client._extract_content(data)
        choice = data["choices"][0] if isinstance(data.get("choices"), list) and data["choices"] else {}
        return ChatResponse(
            content=content if isinstance(content, str) else "",
            model=str(data.get("model") or "hermes-agent"),
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
            finish_reason=choice.get("finish_reason") if isinstance(choice, dict) else None,
        )


class HermesSidecarClient:
    """Small HTTP client for the Hermes sidecar API.

    Omnix asks Hermes for structured declarative plans and keeps execution,
    policy, budgets, and state ownership inside Omnix.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8642", api_key: str | None = None, timeout: float = 45.0):
        self.base_url = base_url.rstrip("/"); self.api_key = api_key; self.timeout = timeout
        self.http = shared_http_client("hermes")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key: headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def health(self) -> dict[str, Any]:
        response=self.http.get(f"{self.base_url}/health",headers=self._headers(),timeout=min(5.0,self.timeout));response.raise_for_status();return response.json()

    def capabilities(self) -> dict[str, Any]:
        response=self.http.get(f"{self.base_url}/v1/capabilities",headers=self._headers(),timeout=min(5.0,self.timeout));response.raise_for_status();return response.json()

    def rpg_plan(self, request: dict[str, Any]) -> dict[str, Any]:
        response=self.http.post(f"{self.base_url}/v1/rpg/plan",headers=self._headers(),data=json.dumps(request),timeout=self.timeout);response.raise_for_status();data=response.json()
        if not isinstance(data,dict): raise HermesSidecarError("Hermes RPG planner response was not an object")
        return data

    def structured(
        self,
        messages: list[ChatMessage],
        *,
        output_model: type[BaseModel],
        contract_id: str,
        json_mode: bool,
        timeout: float,
        error: str,
    ) -> Any:
        """One sidecar call for a JSON object validated against ``output_model``.

        Unusable output raises ``HermesSidecarError(error)``; a transport or HTTP
        failure is raised unchanged, as before the gateway.
        """
        contract = StructuredContract(
            contract_id=contract_id,
            version=1,
            output_model=output_model,
            regenerate_on_semantic_failure=False,
        )
        budget = StructuredRetryBudget(
            max_provider_calls=1,
            max_transport_retries=0,
            max_format_downgrades=0,
            max_validation_regenerations=0,
            deadline_seconds=timeout + 5.0,
        )
        provider = _HermesChatProvider(self, json_mode=json_mode, timeout=timeout)
        try:
            return StructuredOutputGateway(provider).generate(
                messages, contract=contract, retry_budget=budget,
                provider_options={"request_timeout_seconds": timeout},
            )
        except StructuredOutputExhausted as exc:
            cause = exc.last_error
            if cause is not None and not isinstance(cause, (ProviderEmptyResponse, ProviderTruncatedResponse)):
                raise cause from None
            raise HermesSidecarError(error) from exc
        except StructuredOutputError as exc:
            raise HermesSidecarError(error) from exc

    def _extract_content(self, data: dict[str, Any]) -> str:
        try:return data["choices"][0]["message"]["content"]
        except Exception as exc:raise HermesSidecarError("Hermes response did not include message content") from exc
