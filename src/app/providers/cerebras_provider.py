"""Cerebras OpenAI-compatible provider plugin."""
from __future__ import annotations

import logging

import json
from contextlib import ExitStack, contextmanager
from typing import Any, Dict, Iterator, List, Optional, Union

import httpx

from .base import (
    AuthenticationError,
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ConnectionError,
    ModelInfo,
    ProviderCapability,
)
from .http_calls import raise_for_provider_status, transport_errors
from .structured.transport import (
    pop_structured_transport_options,
)

logger = logging.getLogger(__name__)


class CerebrasProvider(BaseProvider):
    provider_name = "cerebras"
    provider_display_name = "Cerebras"
    provider_description = "Cerebras Cloud API with access to their LLM models"
    default_capabilities = [
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.MODELS,
    ]

    API_BASE_URL = "https://api.cerebras.ai"
    CHAT_ENDPOINT = "/v1/chat/completions"
    MODELS_ENDPOINT = "/v1/models"

    def _validate_config(self):
        if not self.config.base_url:
            self.config.base_url = self.API_BASE_URL
        if not self.config.api_key:
            raise AuthenticationError("Cerebras requires an API key")
        self.config.base_url = self.config.base_url.rstrip("/")

    def _request_target(self, endpoint: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, str]]:
        url = f"{self.config.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.config.api_key}"
        headers["Content-Type"] = "application/json"
        kwargs.setdefault("timeout", self.config.timeout)
        return url, headers

    def _make_request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        url, headers = self._request_target(endpoint, kwargs)
        with transport_errors(f"Cerebras at {url}", timeout_target="Cerebras"):
            response = self.http.request(method, url, headers=headers, **kwargs)
        raise_for_provider_status(response)
        return response

    @contextmanager
    def _stream_request(self, endpoint: str, **kwargs) -> Iterator[httpx.Response]:
        url, headers = self._request_target(endpoint, kwargs)
        with ExitStack() as stack:
            with transport_errors(f"Cerebras at {url}", timeout_target="Cerebras"):
                response = stack.enter_context(self.http.stream("POST", url, headers=headers, **kwargs))
            raise_for_provider_status(response)
            yield response

    def chat_completion(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[ChatResponse, Iterator[ChatResponse]]:
        if not messages:
            raise ValueError("Messages list cannot be empty")
        transport = pop_structured_transport_options(kwargs)
        payload: Dict[str, Any] = {
            "model": model or self.config.model,
            "messages": [message.to_dict() for message in messages],
            "stream": stream,
            **transport.payload_options,
        }
        for key in [
            "temperature",
            "top_p",
            "max_tokens",
            "top_k",
            "presence_penalty",
            "frequency_penalty",
        ]:
            if key in kwargs:
                payload[key] = kwargs[key]
            elif key in self.config.extra_params:
                payload[key] = self.config.extra_params[key]
        timeout = transport.request_timeout_seconds
        if stream:
            return self._stream_completion(payload, timeout=timeout)
        return self._non_stream_completion(payload, timeout=timeout)

    def _non_stream_completion(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> ChatResponse:
        request_kwargs: Dict[str, Any] = {"json": payload}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        response = self._make_request("post", self.CHAT_ENDPOINT, **request_kwargs)
        try:
            data = response.json()
        except ValueError as exc:
            raise ConnectionError(f"Invalid JSON response: {exc}") from exc
        choices = data.get("choices", [])
        if not choices:
            raise ConnectionError("No choices in Cerebras response")
        message = choices[0].get("message", {})
        thinking = message.get("reasoning") or message.get("thinking")
        tool_calls = message.get("tool_calls")
        return ChatResponse(
            content=message.get("content", ""),
            model=data.get("model", payload.get("model", "")),
            usage=data.get("usage"),
            thinking=thinking,
            reasoning=thinking,
            tool_calls=tool_calls if isinstance(tool_calls, list) else None,
            finish_reason=choices[0].get("finish_reason"),
            raw_response=data,
        )

    def _stream_completion(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Iterator[ChatResponse]:
        request_kwargs: Dict[str, Any] = {"json": payload}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        with ExitStack() as stack:
            try:
                response = stack.enter_context(self._stream_request(self.CHAT_ENDPOINT, **request_kwargs))
            except Exception as exc:
                raise ConnectionError(f"Failed to start stream: {exc}") from exc
            yield from self._stream_events(response, payload)

    def _stream_events(self, response: httpx.Response, payload: dict[str, Any]) -> Iterator[ChatResponse]:
        try:
            for line in response.iter_lines():
                if not line:
                    continue
                line_text = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line_text.startswith("data: "):
                    continue
                data_text = line_text[6:].strip()
                if data_text == "[DONE]":
                    break
                try:
                    data = json.loads(data_text)
                    if not isinstance(data, dict):
                        continue
                    choice = data.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    thinking = delta.get("reasoning") or delta.get("thinking")
                    yield ChatResponse(
                        content=delta.get("content", ""),
                        model=data.get("model", payload.get("model", "")),
                        usage=data.get("usage"),
                        thinking=thinking,
                        reasoning=thinking,
                        tool_calls=(
                            delta.get("tool_calls")
                            if isinstance(delta.get("tool_calls"), list)
                            else None
                        ),
                        finish_reason=choice.get("finish_reason"),
                        raw_response=data,
                    )
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
        except Exception as exc:
            raise ConnectionError(f"Stream error: {exc}") from exc

    def get_models(self) -> List[ModelInfo]:
        try:
            response = self._make_request("get", self.MODELS_ENDPOINT)
            data = response.json()
            return [
                ModelInfo(
                    id=model_data.get("id", ""),
                    name=model_data.get("name", model_data.get("id", "")),
                    provider=self.provider_name,
                    context_length=model_data.get("context_length"),
                    description=model_data.get("description", ""),
                    metadata={"owned_by": model_data.get("owned_by", "")},
                )
                for model_data in data.get("data", [])
            ]
        except Exception as exc:
            if isinstance(exc, (AuthenticationError, ConnectionError)):
                raise
            raise ConnectionError(f"Failed to fetch models from Cerebras: {exc}") from exc

    def test_connection(self) -> bool:
        try:
            response = self._make_request("get", self.MODELS_ENDPOINT, timeout=5)
            if response.status_code == 200:
                return True
        except AuthenticationError:
            raise
        except Exception:
            logger.debug("suppressed error in %s", "CerebrasProvider.test_connection", exc_info=True)
        try:
            test_payload = {
                "model": self.config.model or "llama-3.3-70b",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 1,
                "temperature": 0.0,
            }
            response = self._make_request(
                "post",
                self.CHAT_ENDPOINT,
                json=test_payload,
                timeout=10,
            )
            return response.status_code == 200
        except AuthenticationError:
            raise
        except Exception:
            return False

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "provider_type": self.provider_name,
            "display_name": self.provider_display_name,
            "description": self.provider_description,
            "fields": [
                {
                    "name": "api_key",
                    "type": "password",
                    "label": "API Key",
                    "required": True,
                    "description": "Cerebras API key",
                },
                {
                    "name": "model",
                    "type": "select",
                    "label": "Model",
                    "required": True,
                    "description": "Select a model",
                    "options": [],
                },
            ],
        }
