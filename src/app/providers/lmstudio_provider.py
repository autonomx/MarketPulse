"""LM Studio provider plugin."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import json
from typing import Any, Dict, Iterator, List, Optional, Union
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.runtime.cancellation import CancellationToken
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
from .http_calls import transport_errors
from .provider_trace import provider_call_enter, provider_call_exit
from .structured.transport import (
    pop_structured_transport_options,
    raise_if_structured_mode_rejected,
)

_NATIVE_CHAT_COMPLETIONS_ENDPOINT = "/api/v0/chat/completions"
_NATIVE_V1_CHAT_ENDPOINT = "/api/v1/chat"
_OPENAI_CHAT_COMPLETIONS_ENDPOINT = "/v1/chat/completions"
_DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_STREAM_CANCEL_POLL_SECONDS = 0.025


def _native_v1_usage(stats: Any) -> Dict[str, int] | None:
    if not isinstance(stats, dict):
        return None
    input_tokens = stats.get("input_tokens")
    output_tokens = stats.get("total_output_tokens")
    usage: Dict[str, int] = {}
    if isinstance(input_tokens, int) and input_tokens >= 0:
        usage["prompt_tokens"] = input_tokens
    if isinstance(output_tokens, int) and output_tokens >= 0:
        usage["completion_tokens"] = output_tokens
    if "prompt_tokens" in usage and "completion_tokens" in usage:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage or None


def _native_v1_output_text(output: Any, item_type: str) -> str:
    if not isinstance(output, list):
        return ""
    return "".join(
        str(item.get("content") or "")
        for item in output
        if isinstance(item, dict) and item.get("type") == item_type
    )


class LMStudioProvider(BaseProvider):
    """Provider for local LM Studio OpenAI-compatible servers."""

    provider_name = "lmstudio"
    provider_display_name = "LM Studio"
    provider_description = "Local LM Studio instance with OpenAI-compatible API"
    default_capabilities = [
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.MODELS,
    ]

    def _validate_config(self):
        if not self.config.base_url:
            self.config.base_url = "http://localhost:1234"
        self.config.base_url = self._normalize_local_base_url(
            self.config.base_url.rstrip("/")
        )

    @staticmethod
    def _normalize_local_base_url(base_url: str) -> str:
        parsed = urlsplit(base_url)
        if parsed.hostname not in {"localhost", "localhost.localdomain"}:
            return base_url
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit(
            (parsed.scheme, f"127.0.0.1{port}", parsed.path, parsed.query, parsed.fragment)
        )

    def _metrics_chat_completion_endpoint(self) -> str:
        configured = str(
            self.config.extra_params.get("metrics_chat_completions_endpoint") or ""
        ).strip()
        if not configured:
            return _NATIVE_CHAT_COMPLETIONS_ENDPOINT
        return configured if configured.startswith("/") else f"/{configured}"

    def _api_token(self) -> str:
        """Return the configured LM Studio token without requiring one locally."""

        configured = str(self.config.api_key or "").strip()
        return configured or _env_str("LM_API_TOKEN", "").strip()

    def _make_request(
        self,
        method: str,
        endpoint: str,
        *,
        stream: bool = False,
        cancel: CancellationToken | None = None,
        **kwargs,
    ) -> Any:
        """Send a request; with ``stream`` the caller closes the returned response."""
        url = f"{self.config.base_url}{endpoint}"
        try:
            check_outbound_url(url, resolve=True)
        except UrlPolicyError as exc:
            raise ConnectionError(f"Blocked by the outbound URL policy: {exc}") from exc
        timeout = kwargs.pop("timeout", self.config.timeout)
        headers = dict(kwargs.pop("headers", {}) or {})
        api_token = self._api_token()
        if api_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {api_token}"
        if headers:
            kwargs["headers"] = headers
        with transport_errors(f"LM Studio at {url}", timeout_target="LM Studio"):
            if stream:
                response = self.http.open_stream(method, url, timeout=timeout, cancel=cancel, **kwargs)
            else:
                response = self.http.request(method, url, timeout=timeout, cancel=cancel, **kwargs)
        try:
            self._raise_for_status(response)
        except BaseException:
            response.close()
            raise
        return response

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        if response.is_success:
            return
        status = response.status_code
        try:
            body = response.read().decode("utf-8", "replace")[:2000]
        except Exception:
            body = ""
        error = httpx.HTTPStatusError(
            f"{status} {response.reason_phrase}",
            request=response.request,
            response=getattr(response, "response", response),  # an OpenStream wraps one
        )
        raise_if_structured_mode_rejected(status_code=status, response_body=body, error=error)
        if status == 401:
            raise AuthenticationError(
                "LM Studio rejected the request as unauthorized. "
                "Set LM_API_TOKEN or provide ProviderConfig.api_key."
            ) from error
        raise ConnectionError(f"HTTP error {status}: {error}; response_body={body}") from error

    def _make_chat_completion_request(
        self,
        payload: Dict[str, Any],
        *,
        stream: bool,
        include_metrics: bool,
        timeout: float | None = None,
        cancel: CancellationToken | None = None,
    ) -> Any:
        request_kwargs: Dict[str, Any] = {"json": payload, "stream": stream, "cancel": cancel}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        if not include_metrics:
            return self._make_request(
                "post",
                _OPENAI_CHAT_COMPLETIONS_ENDPOINT,
                **request_kwargs,
            )
        endpoint = self._metrics_chat_completion_endpoint()
        try:
            return self._make_request("post", endpoint, **request_kwargs)
        except ConnectionError as exc:
            if endpoint != _NATIVE_CHAT_COMPLETIONS_ENDPOINT or "HTTP error 404" not in str(exc):
                raise
            fallback_payload = dict(payload)
            if stream:
                fallback_payload["stream_options"] = {"include_usage": True}
            request_kwargs["json"] = fallback_payload
            return self._make_request(
                "post",
                _OPENAI_CHAT_COMPLETIONS_ENDPOINT,
                **request_kwargs,
            )

    def _native_v1_payload(
        self,
        messages: List[ChatMessage],
        *,
        model: str | None,
        stream: bool,
        store: bool,
        previous_response_id: str | None,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        system_parts: list[str] = []
        user_parts: list[str] = []
        for message in messages:
            role = str(message.role or "").strip().lower()
            if role == "system":
                if message.content:
                    system_parts.append(str(message.content))
                continue
            if role != "user":
                raise ValueError(
                    "LM Studio native v1 chat cannot seed assistant/tool history; "
                    "use the default chat-completions transport or continue from a response id"
                )
            user_parts.append(str(message.content or ""))

        if not user_parts:
            raise ValueError("LM Studio native v1 chat requires at least one user input")
        if previous_response_id:
            previous_response_id = str(previous_response_id).strip()
            if not previous_response_id.startswith("resp_"):
                raise ValueError("LM Studio previous_response_id must start with 'resp_'")
            if len(user_parts) != 1:
                raise ValueError(
                    "LM Studio native v1 continuation requires exactly one new user input"
                )

        input_value: Any
        if len(user_parts) == 1:
            input_value = user_parts[0]
        else:
            input_value = [
                {"type": "message", "content": content}
                for content in user_parts
            ]

        payload: Dict[str, Any] = {
            "input": input_value,
            "stream": stream,
            # Omnix keeps native provider state opt-in. Speculation and ordinary
            # chat remain side-effect-free unless a caller explicitly requests
            # LM Studio response state.
            "store": bool(store),
            "temperature": kwargs.get("temperature", 0.7),
        }
        if model:
            payload["model"] = model
        if system_parts:
            payload["system_prompt"] = "\n\n".join(system_parts)
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        if "top_p" in kwargs:
            payload["top_p"] = kwargs["top_p"]
        if "top_k" in kwargs:
            payload["top_k"] = kwargs["top_k"]
        if "max_tokens" in kwargs:
            payload["max_output_tokens"] = kwargs["max_tokens"]
        if "context_length" in kwargs:
            payload["context_length"] = kwargs["context_length"]
        if "reasoning" in kwargs:
            payload["reasoning"] = kwargs["reasoning"]
        else:
            template_kwargs = kwargs.get("chat_template_kwargs")
            if isinstance(template_kwargs, dict) and template_kwargs.get("enable_thinking") is False:
                payload["reasoning"] = "off"
        return payload

    def chat_completion(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[ChatResponse, Iterator[ChatResponse]]:
        use_configured_model = bool(kwargs.pop("_use_configured_model", True))
        native_v1 = bool(kwargs.pop("_lmstudio_native_v1", False))
        native_store = bool(kwargs.pop("_lmstudio_store", False))
        previous_response_id = kwargs.pop("_lmstudio_previous_response_id", None)
        cancel = kwargs.pop("cancel", None)
        trace_row = provider_call_enter(
            provider="lmstudio",
            method="chat_completion",
            model=model,
            messages=messages,
            extra={
                "stream": bool(stream),
                "kwargs_keys": sorted(kwargs),
                "use_configured_model": use_configured_model,
                "native_v1": native_v1,
                "native_store": native_store,
                "has_previous_response_id": bool(previous_response_id),
                "cancellable_stream": bool(stream and cancel is not None),
            },
        )
        try:
            if not messages:
                raise ValueError("Messages list cannot be empty")
            transport = pop_structured_transport_options(kwargs)
            resolved_model = model or (
                self.config.model if use_configured_model else None
            )
            timeout = transport.request_timeout_seconds
            if native_v1:
                if transport.payload_options:
                    raise ValueError(
                        "Structured transport options are not supported by LM Studio native v1 chat"
                    )
                payload = self._native_v1_payload(
                    messages,
                    model=resolved_model,
                    stream=stream,
                    store=native_store,
                    previous_response_id=(
                        str(previous_response_id) if previous_response_id else None
                    ),
                    kwargs=kwargs,
                )
                result = (
                    self._native_v1_stream_completion(
                        payload,
                        timeout=timeout,
                        cancel=cancel,
                    )
                    if stream
                    else self._native_v1_non_stream_completion(payload, timeout=timeout)
                )
            else:
                include_metrics = bool(kwargs.get("include_metrics", False))
                payload = {
                    "messages": [message.to_dict() for message in messages],
                    "temperature": kwargs.get("temperature", 0.7),
                    "stream": stream,
                    **transport.payload_options,
                }
                if resolved_model:
                    payload["model"] = resolved_model
                for key in ["max_tokens", "top_p", "chat_template_kwargs"]:
                    if key in kwargs:
                        payload[key] = kwargs[key]
                result = (
                    self._stream_completion(
                        payload,
                        include_metrics=include_metrics,
                        timeout=timeout,
                        cancel=cancel,
                    )
                    if stream
                    else self._non_stream_completion(
                        payload,
                        include_metrics=include_metrics,
                        timeout=timeout,
                    )
                )
            provider_call_exit(trace_row, ok=True)
            return result
        except Exception as exc:
            provider_call_exit(trace_row, ok=False, error=f"{type(exc).__name__}: {exc}")
            raise

    def _native_v1_non_stream_completion(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> ChatResponse:
        request_kwargs: Dict[str, Any] = {"json": payload, "stream": False}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        response = self._make_request(
            "post",
            _NATIVE_V1_CHAT_ENDPOINT,
            **request_kwargs,
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise ConnectionError(f"Invalid LM Studio native v1 JSON response: {exc}") from exc
        if not isinstance(data, dict):
            raise ConnectionError("Invalid LM Studio native v1 response payload")
        return self._native_v1_response(data)

    def _native_v1_response(self, data: Dict[str, Any]) -> ChatResponse:
        content = _native_v1_output_text(data.get("output"), "message")
        reasoning = _native_v1_output_text(data.get("output"), "reasoning")
        return ChatResponse(
            content=content,
            model=str(data.get("model_instance_id") or data.get("model") or ""),
            usage=_native_v1_usage(data.get("stats")),
            thinking=reasoning or None,
            reasoning=reasoning or None,
            raw_response=data,
        )

    def _native_v1_stream_completion(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float | None = None,
        cancel: CancellationToken | None = None,
    ) -> Iterator[ChatResponse]:
        request_kwargs: Dict[str, Any] = {"json": payload, "stream": True, "cancel": cancel}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        try:
            response = self._make_request(
                "post",
                _NATIVE_V1_CHAT_ENDPOINT,
                **request_kwargs,
            )
        except Exception as exc:
            raise ConnectionError(f"Failed to start LM Studio native v1 stream: {exc}") from exc

        model_name = str(payload.get("model") or "")
        event_name = ""
        stream_error: str | None = None
        try:
            for line in response.iter_lines():
                if cancel is not None and cancel.cancelled:
                    return
                if not line:
                    continue
                line_text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
                if line_text.startswith("event:"):
                    event_name = line_text[6:].strip()
                    continue
                if not line_text.startswith("data:"):
                    continue
                data_text = line_text[5:].strip()
                try:
                    event = json.loads(data_text)
                except (ValueError, TypeError):
                    event_name = ""
                    continue
                if not isinstance(event, dict):
                    event_name = ""
                    continue
                event_type = str(event.get("type") or event_name or "")
                event_name = ""
                if event_type == "chat.start":
                    model_name = str(event.get("model_instance_id") or model_name)
                    continue
                if event_type == "message.delta":
                    content = event.get("content")
                    if isinstance(content, str) and content:
                        yield ChatResponse(
                            content=content,
                            model=model_name,
                            raw_response=event,
                        )
                    continue
                if event_type == "reasoning.delta":
                    reasoning = event.get("content")
                    if isinstance(reasoning, str) and reasoning:
                        yield ChatResponse(
                            content="",
                            model=model_name,
                            thinking=reasoning,
                            reasoning=reasoning,
                            raw_response=event,
                        )
                    continue
                if event_type == "error":
                    error = event.get("error")
                    stream_error = (
                        str(error.get("message") or error.get("type") or "native v1 stream error")
                        if isinstance(error, dict)
                        else str(error or "native v1 stream error")
                    )
                    continue
                if event_type == "chat.end":
                    result = event.get("result")
                    if not isinstance(result, dict):
                        raise ConnectionError("LM Studio native v1 chat.end did not include a result")
                    final = self._native_v1_response(result)
                    if final.model:
                        model_name = final.model
                    yield ChatResponse(
                        content="",
                        model=model_name,
                        usage=final.usage,
                        thinking=final.thinking,
                        reasoning=final.reasoning,
                        raw_response=result,
                    )
                    if stream_error:
                        raise ConnectionError(f"LM Studio native v1 stream error: {stream_error}")
                    return
            if cancel is not None and cancel.cancelled:
                return
            if stream_error:
                raise ConnectionError(f"LM Studio native v1 stream error: {stream_error}")
            raise ConnectionError("LM Studio native v1 stream ended without chat.end")
        except ConnectionError:
            raise
        except Exception as exc:
            if cancel is not None and cancel.cancelled:
                return
            raise ConnectionError(f"LM Studio native v1 stream error: {exc}") from exc
        finally:
            response.close()

    def _non_stream_completion(
        self,
        payload: Dict[str, Any],
        *,
        include_metrics: bool = False,
        timeout: float | None = None,
    ) -> ChatResponse:
        response = self._make_chat_completion_request(
            payload,
            stream=False,
            include_metrics=include_metrics,
            timeout=timeout,
        )
        max_response_bytes = max(
            1,
            int(
                self.config.extra_params.get("max_response_bytes")
                or _DEFAULT_MAX_RESPONSE_BYTES
            ),
        )
        try:
            declared_length = int(response.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > max_response_bytes:
            raise ConnectionError(
                "LM Studio response exceeds configured byte limit: "
                f"{declared_length}>{max_response_bytes}"
            )
        if len(response.content) > max_response_bytes:
            raise ConnectionError(
                "LM Studio response exceeds configured byte limit: "
                f"{len(response.content)}>{max_response_bytes}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ConnectionError(f"Invalid JSON response: {exc}") from exc
        choices = data.get("choices", [])
        if not choices:
            raise ConnectionError("No choices in response")
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
        include_metrics: bool = False,
        timeout: float | None = None,
        cancel: CancellationToken | None = None,
    ) -> Iterator[ChatResponse]:
        try:
            response = self._make_chat_completion_request(
                payload,
                stream=True,
                include_metrics=include_metrics,
                timeout=timeout,
                cancel=cancel,
            )
        except Exception as exc:
            raise ConnectionError(f"Failed to start stream: {exc}") from exc
        thinking_buffer = ""
        usage: Dict[str, Any] | None = None
        finish_reason: str | None = None
        resolved_model = str(payload.get("model") or "")
        last_response: Dict[str, Any] | None = None
        try:
            for line in response.iter_lines():
                if cancel is not None and cancel.cancelled:
                    return
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
                    choices = data.get("choices")
                    choice = (
                        choices[0]
                        if isinstance(choices, list)
                        and choices
                        and isinstance(choices[0], dict)
                        else {}
                    )
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    reasoning = delta.get("reasoning")
                    if isinstance(reasoning, str):
                        thinking_buffer += reasoning
                    content = delta.get("content")
                    if not isinstance(content, str):
                        content = ""
                    chunk_usage = data.get("usage")
                    if isinstance(chunk_usage, dict):
                        usage = chunk_usage
                    chunk_finish_reason = choice.get("finish_reason")
                    if chunk_finish_reason:
                        finish_reason = str(chunk_finish_reason)
                    resolved_model = str(data.get("model") or resolved_model)
                    last_response = data
                    yield ChatResponse(
                        content=content,
                        model=resolved_model,
                        usage=chunk_usage if isinstance(chunk_usage, dict) else None,
                        thinking=reasoning if isinstance(reasoning, str) else None,
                        reasoning=reasoning if isinstance(reasoning, str) else None,
                        tool_calls=(
                            delta.get("tool_calls")
                            if isinstance(delta.get("tool_calls"), list)
                            else None
                        ),
                        finish_reason=(
                            str(chunk_finish_reason) if chunk_finish_reason else None
                        ),
                        raw_response=data,
                    )
                except (ValueError, SyntaxError, KeyError, IndexError, TypeError):
                    continue
            if cancel is not None and cancel.cancelled:
                return
            yield ChatResponse(
                content="",
                model=resolved_model,
                usage=usage,
                thinking=thinking_buffer if thinking_buffer else None,
                reasoning=thinking_buffer if thinking_buffer else None,
                finish_reason=finish_reason,
                raw_response=last_response,
            )
        except Exception as exc:
            if cancel is not None and cancel.cancelled:
                return
            raise ConnectionError(f"Stream error: {exc}") from exc
        finally:
            response.close()

    def get_models(self) -> List[ModelInfo]:
        try:
            response = self._make_request("get", "/v1/models")
            data = response.json()
            models = [
                ModelInfo(
                    id=model_data.get("id", ""),
                    name=model_data.get("id", ""),
                    provider=self.provider_name,
                    context_length=model_data.get("context_length"),
                    capabilities=[],
                    description=model_data.get("description", ""),
                    metadata={
                        "owned_by": model_data.get("owned_by", ""),
                        "permission": model_data.get("permission", []),
                    },
                )
                for model_data in data.get("data", [])
            ]
            return models or self._get_models_alternate()
        except Exception as exc:
            raise ConnectionError(f"Failed to fetch models: {exc}") from exc

    def _get_models_alternate(self) -> List[ModelInfo]:
        try:
            response = self._make_request("get", "/api/v0/models")
            data = response.json()
            models: List[ModelInfo] = []
            for model_data in data:
                if isinstance(model_data, dict):
                    models.append(
                        ModelInfo(
                            id=model_data.get("model", model_data.get("id", "")),
                            name=model_data.get("name", model_data.get("model", "")),
                            provider=self.provider_name,
                            context_length=model_data.get("context_length"),
                            description=model_data.get("description", ""),
                            metadata={},
                        )
                    )
            return models
        except Exception:
            return []

    def test_connection(self) -> bool:
        try:
            response = self._make_request("get", "/v1/models", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "provider_type": self.provider_name,
            "display_name": self.provider_display_name,
            "description": self.provider_description,
            "fields": [
                {
                    "name": "base_url",
                    "type": "string",
                    "label": "Base URL",
                    "default": "http://localhost:1234",
                    "required": True,
                    "description": "URL of the LM Studio server",
                },
                {
                    "name": "model",
                    "type": "string",
                    "label": "Default Model",
                    "required": False,
                    "description": "Fallback model used when LM Studio has no loaded LLM",
                },
            ],
        }
