"""OpenRouter provider plugin."""
from __future__ import annotations

import json
import logging
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

_LOGGER = logging.getLogger(__name__)


class OpenRouterProvider(BaseProvider):
    provider_name = "openrouter"
    provider_display_name = "OpenRouter"
    provider_description = "OpenRouter API with access to multiple LLM providers"
    default_capabilities = [
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.MODELS,
    ]

    API_BASE_URL = "https://openrouter.ai/api/v1"

    def _validate_config(self):
        if not self.config.base_url:
            self.config.base_url = self.API_BASE_URL
        if not self.config.api_key:
            raise AuthenticationError("OpenRouter requires an API key")
        self.config.base_url = self.config.base_url.rstrip("/")

    def _request_target(self, endpoint: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, str]]:
        url = f"{self.config.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.config.api_key}"
        headers.setdefault("HTTP-Referer", "http://localhost:5000")
        headers.setdefault("X-Title", "Omnix")
        kwargs.setdefault("timeout", self.config.timeout or 60)
        return url, headers

    def _make_request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        url, headers = self._request_target(endpoint, kwargs)
        with transport_errors(f"OpenRouter at {url}", timeout_target="OpenRouter"):
            response = self.http.request(method, url, headers=headers, **kwargs)
        raise_for_provider_status(response)
        return response

    @contextmanager
    def _stream_request(self, endpoint: str, **kwargs) -> Iterator[httpx.Response]:
        url, headers = self._request_target(endpoint, kwargs)
        with ExitStack() as stack:
            with transport_errors(f"OpenRouter at {url}", timeout_target="OpenRouter"):
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
        optional_params = [
            "temperature",
            "top_p",
            "max_tokens",
            "top_k",
            "presence_penalty",
            "frequency_penalty",
            "thinking",
            "chat_template_kwargs",
        ]
        for key in optional_params:
            if key in kwargs:
                payload[key] = kwargs[key]
            elif key in self.config.extra_params:
                payload[key] = self.config.extra_params[key]
        thinking_budget = kwargs.get(
            "thinking_budget",
            self.config.extra_params.get("thinking_budget", 0),
        )
        if thinking_budget and thinking_budget > 0:
            payload["max_tokens"] = thinking_budget
            payload.setdefault(
                "thinking",
                {"type": "enabled", "budget_tokens": thinking_budget},
            )
        timeout = transport.request_timeout_seconds
        _LOGGER.info(
            "OpenRouter chat_completion model=%s messages=%s",
            payload.get("model"),
            len(messages),
        )
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
        response = self._make_request("post", "/chat/completions", **request_kwargs)
        try:
            data = response.json()
        except ValueError as exc:
            raise ConnectionError(f"Invalid JSON response: {exc}") from exc
        choices = data.get("choices", [])
        if not choices:
            raise ConnectionError("No choices in OpenRouter response")
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
                response = stack.enter_context(self._stream_request("/chat/completions", **request_kwargs))
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
            response = self._make_request("get", "/models")
            data = response.json()
            return [
                ModelInfo(
                    id=model_data.get("id", ""),
                    name=model_data.get("name", model_data.get("id", "")),
                    provider=self.provider_name,
                    context_length=model_data.get("context_length"),
                    description=model_data.get("description", ""),
                    metadata={
                        "owned_by": model_data.get("owned_by", ""),
                        "top_provider": model_data.get("top_provider", False),
                        "pricing": model_data.get("pricing", {}),
                    },
                )
                for model_data in data.get("data", [])
            ]
        except Exception as exc:
            if isinstance(exc, (AuthenticationError, ConnectionError)):
                raise
            raise ConnectionError(f"Failed to fetch models from OpenRouter: {exc}") from exc

    def test_connection(self) -> bool:
        try:
            response = self._make_request("get", "/models", timeout=5)
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
                    "description": "OpenRouter API key",
                },
                {
                    "name": "model",
                    "type": "select",
                    "label": "Model",
                    "required": True,
                    "description": "Select a model",
                    "options": [],
                },
                {
                    "name": "thinking_budget",
                    "type": "number",
                    "label": "Thinking Budget (tokens)",
                    "default": 0,
                    "required": False,
                    "description": "Additional tokens for thinking/reasoning (0 to disable)",
                },
            ],
        }

    def supports_thinking_budget(self) -> bool:
        return True
