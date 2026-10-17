"""OpenAI-compatible provider plugin."""
from __future__ import annotations

import logging

import json
from contextlib import ExitStack, contextmanager
from typing import Any, Dict, Iterator, List, Optional, Union

import httpx

from app.security.url_policy import UrlPolicyError, check_outbound_url

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
from .provider_trace import provider_call_enter, provider_call_exit
from .structured.transport import (
    pop_structured_transport_options,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(BaseProvider):
    """Provider for APIs implementing the OpenAI chat-completions contract."""

    provider_name = "openai_compatible"
    provider_display_name = "OpenAI Compatible"
    provider_description = "OpenAI-compatible API (Azure OpenAI, custom deployments, etc.)"
    default_capabilities = [
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.MODELS,
    ]

    def _validate_config(self):
        if not self.config.base_url:
            raise ValueError("OpenAI-compatible provider requires a base URL")
        if not self.config.api_key:
            raise AuthenticationError("OpenAI-compatible provider requires an API key")
        if not self.config.model:
            raise ValueError("OpenAI-compatible provider requires a model ID")
        self.config.base_url = self.config.base_url.rstrip("/")

    def _request_target(self, endpoint: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, str]]:
        url = f"{self.config.base_url}{endpoint}"
        try:
            check_outbound_url(url, resolve=True)
        except UrlPolicyError as exc:
            raise ConnectionError(f"Blocked by the outbound URL policy: {exc}") from exc
        headers = kwargs.pop("headers", {})
        if "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        headers["Content-Type"] = "application/json"
        custom_headers = self.config.extra_params.get("custom_headers", {})
        if isinstance(custom_headers, dict):
            headers.update(custom_headers)
        kwargs.setdefault("timeout", self.config.timeout)
        return url, headers

    def _make_request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        url, headers = self._request_target(endpoint, kwargs)
        with transport_errors(url):
            response = self.http.request(method, url, headers=headers, **kwargs)
        raise_for_provider_status(response)
        return response

    @contextmanager
    def _stream_request(self, endpoint: str, **kwargs) -> Iterator[httpx.Response]:
        url, headers = self._request_target(endpoint, kwargs)
        with ExitStack() as stack:
            with transport_errors(url):
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
        trace_row = provider_call_enter(
            provider="openai_compatible",
            method="chat_completion",
            model=model,
            messages=messages,
            extra={"stream": bool(stream), "kwargs_keys": sorted(kwargs)},
        )
        try:
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
                "chat_template_kwargs",
            ]
            for key in optional_params:
                if key in kwargs:
                    payload[key] = kwargs[key]
                elif key in self.config.extra_params:
                    payload[key] = self.config.extra_params[key]
            for key, value in self.config.extra_params.items():
                if key not in {"custom_headers", "thinking_budget"} and key not in payload:
                    payload[key] = value
            thinking_budget = kwargs.get(
                "thinking_budget",
                self.config.extra_params.get("thinking_budget", 0),
            )
            if thinking_budget and thinking_budget > 0:
                payload["max_tokens"] = thinking_budget
            timeout = transport.request_timeout_seconds
            result = (
                self._stream_completion(payload, timeout=timeout)
                if stream
                else self._non_stream_completion(payload, timeout=timeout)
            )
            provider_call_exit(trace_row, ok=True)
            return result
        except Exception as exc:
            provider_call_exit(trace_row, ok=False, error=f"{type(exc).__name__}: {exc}")
            raise

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
            raise ConnectionError("No choices in response")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        thinking = (
            message.get("reasoning")
            or message.get("thinking")
            or message.get("analysis")
            or message.get("thoughts")
        )
        tool_calls = message.get("tool_calls")
        return ChatResponse(
            content=content,
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
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    thinking = (
                        delta.get("reasoning")
                        or delta.get("thinking")
                        or delta.get("analysis")
                    )
                    yield ChatResponse(
                        content=delta.get("content", ""),
                        model=payload.get("model", ""),
                        thinking=thinking,
                        reasoning=thinking,
                        tool_calls=(
                            delta.get("tool_calls")
                            if isinstance(delta.get("tool_calls"), list)
                            else None
                        ),
                        raw_response=data,
                    )
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
        except Exception as exc:
            raise ConnectionError(f"Stream error: {exc}") from exc

    def get_models(self) -> List[ModelInfo]:
        models: List[ModelInfo] = []
        try:
            response = self._make_request("get", "/models")
            data = response.json()
            for model_data in data.get("data", []):
                models.append(
                    ModelInfo(
                        id=model_data.get("id", ""),
                        name=model_data.get("name", model_data.get("id", "")),
                        provider=self.provider_name,
                        context_length=model_data.get("context_length"),
                        description=model_data.get("description", ""),
                        metadata=model_data.get("metadata", {}),
                    )
                )
        except Exception:
            logger.debug("suppressed error in %s", "OpenAICompatibleProvider.get_models", exc_info=True)
        if self.config.model and not any(model.id == self.config.model for model in models):
            models.append(
                ModelInfo(
                    id=self.config.model,
                    name=self.config.model,
                    provider=self.provider_name,
                    description="Configured model",
                    metadata={"configured": True},
                )
            )
        return models

    def test_connection(self) -> bool:
        try:
            response = self._make_request("get", "/models", timeout=5)
            return response.status_code == 200
        except AuthenticationError:
            raise
        except Exception:
            try:
                payload = {
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 1,
                }
                response = self._make_request(
                    "post",
                    "/chat/completions",
                    json=payload,
                    timeout=5,
                )
                return response.status_code == 200
            except Exception:
                return False

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "provider_type": self.provider_name,
            "display_name": self.provider_display_name,
            "description": self.provider_description,
            "fields": [
                {
                    "name": "base_url",
                    "type": "text",
                    "label": "API Base URL",
                    "required": True,
                    "description": "Base URL for the OpenAI-compatible API (e.g., https://your-api.com/v1)",
                },
                {
                    "name": "api_key",
                    "type": "password",
                    "label": "API Key",
                    "required": True,
                    "description": "API key for authentication",
                },
                {
                    "name": "model",
                    "type": "text",
                    "label": "Model ID",
                    "required": True,
                    "description": "Model identifier (e.g., gpt-4 or your custom model)",
                },
                {
                    "name": "custom_headers",
                    "type": "object",
                    "label": "Custom Headers",
                    "required": False,
                    "description": "Additional headers to send with requests",
                    "properties": {
                        "key": {"type": "text", "label": "Header Name"},
                        "value": {"type": "text", "label": "Header Value"},
                    },
                },
                {
                    "name": "thinking_budget",
                    "type": "number",
                    "label": "Thinking Budget (tokens)",
                    "default": 0,
                    "required": False,
                    "description": "Additional tokens for thinking/reasoning",
                },
                {
                    "name": "timeout",
                    "type": "number",
                    "label": "Timeout (seconds)",
                    "default": 300,
                    "required": False,
                    "description": "Request timeout in seconds",
                },
            ],
        }

    def supports_thinking_budget(self) -> bool:
        return True
