"""Llama.cpp provider plugin with local server lifecycle management."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

import httpx
from app.runtime.net import bind_host

from .base import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ConnectionError,
    ModelInfo,
    ModelNotFoundError,
    ProviderCapability,
)
from .http_calls import transport_errors
from .structured.transport import (
    pop_structured_transport_options,
    raise_if_structured_mode_rejected,
)


def _probe_host(host: str) -> str:
    """Where to check for an existing listener: a wildcard bind means loopback."""
    try:
        return "127.0.0.1" if ipaddress.ip_address(host).is_unspecified else host
    except ValueError:
        return host


def _drain(stream: Any, tail: deque) -> None:
    """Read the server's output so it never blocks on a full pipe; keep the tail."""
    with stream:
        for line in stream:
            tail.append(line.rstrip())


class LlamaCppProvider(BaseProvider):
    """Provider for a local llama.cpp OpenAI-compatible server."""

    provider_name = "llamacpp"
    provider_display_name = "Llama.cpp"
    provider_description = "Local Llama.cpp server with binary management"
    default_capabilities = [
        ProviderCapability.CHAT,
        ProviderCapability.STREAMING,
        ProviderCapability.MODELS,
    ]
    SERVER_BINARY_NAMES = ["llama-server.exe", "llama-server", "llama.exe", "llama"]
    STARTUP_TIMEOUT_SECONDS = 120.0
    LOG_TAIL_LINES = 200

    def _validate_config(self):
        if not self.config.base_url:
            self.config.base_url = "http://localhost:8180"
        self.config.base_url = self.config.base_url.rstrip("/")
        if not self.config.extra_params.get("model_dir"):
            base_dir = Path(__file__).parent.parent.parent
            download_location = self.config.extra_params.get("download_location", "server")
            self.config.extra_params["model_dir"] = str(
                base_dir / "resources" / "models" / download_location
            )

    def _find_server_binary(self) -> Optional[Path]:
        model_dir = Path(self.config.extra_params.get("model_dir", ""))
        server_dir = model_dir if model_dir.name == "server" else model_dir.parent / "server"
        for binary_name in self.SERVER_BINARY_NAMES:
            binary_path = server_dir / binary_name
            if binary_path.exists():
                return binary_path
        return None

    def _is_server_running(self) -> bool:
        try:
            response = self.http.request("GET", f"{self.config.base_url}/v1/models", timeout=2, retry=False)
            return response.status_code == 200
        except Exception:
            return False

    def _port(self) -> int:
        try:
            return int(self.config.base_url.split(":")[-1])
        except (TypeError, ValueError):
            return 8180

    def _start_server(self, model_path: str) -> Optional[int]:
        host = bind_host()
        binary = self._find_server_binary()
        if not binary:
            raise ConnectionError("Llama.cpp server binary not found")
        port = self._port()
        # Never stop a process Omnix did not start (WP-4.10): a busy port is
        # an error for the operator to resolve.
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((_probe_host(host), port)) == 0:
                raise ConnectionError(
                    f"Port {port} is in use by another process; stop it or configure another llama.cpp port"
                )
        try:
            proc = subprocess.Popen(
                [
                    str(binary),
                    "-m",
                    str(Path(model_path).resolve()),
                    "-c",
                    "4096",
                    "-ngl",
                    "99",
                    "--host",
                    host,
                    "--port",
                    str(port),
                ],
                cwd=binary.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:
            raise ConnectionError(f"Failed to start server: {exc}") from exc
        self._server_process = proc
        self._server_log: deque[str] = deque(maxlen=self.LOG_TAIL_LINES)
        self._server_log_reader = threading.Thread(
            target=_drain, args=(proc.stdout, self._server_log),
            name=f"llamacpp-log-{proc.pid}", daemon=True,
        )
        self._server_log_reader.start()
        return proc.pid

    def server_log_tail(self) -> list[str]:
        """The last lines the started server printed, for diagnostics."""
        return list(getattr(self, "_server_log", ()))

    def _ensure_server(self, model_path: Path) -> None:
        """Use a running server; start one only when ``auto_start`` is enabled."""
        if self._is_server_running():
            return
        if not self.config.extra_params.get("auto_start"):
            raise ConnectionError(
                f"llama.cpp server is not running at {self.config.base_url}; "
                "start it or enable auto_start"
            )
        if not self._start_server(str(model_path)):
            raise ConnectionError("Failed to start llama.cpp server")
        self._wait_until_ready()

    def _wait_until_ready(self) -> None:
        """Poll until the started server answers; fail early if it exits."""
        proc = getattr(self, "_server_process", None)
        deadline = time.monotonic() + self.STARTUP_TIMEOUT_SECONDS
        pause = threading.Event()
        while time.monotonic() < deadline:
            if self._is_server_running():
                return
            if proc is not None and proc.poll() is not None:
                # Let the reader collect the server's last words before reporting.
                self._server_log_reader.join(timeout=2.0)
                last = self.server_log_tail()[-1:] or ["no output"]
                self._server_process = None
                raise ConnectionError(
                    f"llama.cpp server exited during startup (code {proc.returncode}): {last[0]}"
                )
            pause.wait(0.5)
        self._stop_server()
        raise ConnectionError(
            f"llama.cpp server did not answer within {self.STARTUP_TIMEOUT_SECONDS:.0f} s"
        )

    def _stop_server(self) -> bool:
        """Stop the server this provider started, and nothing else."""
        proc = getattr(self, "_server_process", None)
        if proc is None:
            return False
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            return False
        finally:
            self._server_process = None
        return True

    def _resolve_model_path(self, model_name: str) -> Path:
        model_dir = Path(self.config.extra_params.get("model_dir", ""))
        possible_paths: list[Path] = []
        if os.path.isabs(model_name):
            # Model files come from the models directory only (WP-4.10).
            candidate = Path(model_name).resolve()
            if not candidate.is_relative_to(model_dir.resolve()):
                raise ModelNotFoundError(f"Model must be inside the models directory: {model_name}")
            possible_paths.append(candidate)
        else:
            direct_path = (model_dir / model_name).resolve()
            if not direct_path.is_relative_to(model_dir.resolve()):
                raise ModelNotFoundError(f"Model must be inside the models directory: {model_name}")
            if direct_path.exists():
                possible_paths.append(direct_path)
            for file_path in model_dir.rglob("*.gguf"):
                if file_path.name == model_name:
                    possible_paths.append(file_path)
                    break
        for path in possible_paths:
            if path.exists():
                return path
        raise ModelNotFoundError(f"Model not found: {model_name}")

    def chat_completion(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[ChatResponse, Iterator[ChatResponse]]:
        if not messages:
            raise ValueError("Messages list cannot be empty")
        model_name = model or self.config.model
        if not model_name:
            raise ModelNotFoundError("No model specified")
        model_path = self._resolve_model_path(model_name)
        self._ensure_server(model_path)
        transport = pop_structured_transport_options(kwargs)
        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": [message.to_dict() for message in messages],
            "stream": stream,
            **transport.payload_options,
        }
        for key in [
            "temperature",
            "max_tokens",
            "top_p",
            "repeat_penalty",
            "presence_penalty",
            "frequency_penalty",
            "chat_template_kwargs",
        ]:
            if key in kwargs:
                payload[key] = kwargs[key]
            elif key in self.config.extra_params:
                payload[key] = self.config.extra_params[key]
        timeout = transport.request_timeout_seconds
        if stream:
            return self._stream_completion(payload, timeout=timeout)
        return self._non_stream_completion(payload, timeout=timeout)

    def _post_chat(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float | None,
        stream: bool,
    ) -> Any:
        """POST a chat completion; with ``stream`` the caller closes the response."""
        url = f"{self.config.base_url}/v1/chat/completions"
        timeout = timeout if timeout is not None else self.config.timeout
        with transport_errors("llama.cpp server", timeout_target="llama.cpp"):
            if stream:
                response = self.http.open_stream("POST", url, json=payload, timeout=timeout)
            else:
                response = self.http.request("POST", url, json=payload, timeout=timeout)
        if response.is_success:
            return response
        try:
            body = response.read().decode("utf-8", "replace")[:2000]
        except Exception:
            body = ""
        finally:
            response.close()
        error = httpx.HTTPStatusError(
            f"{response.status_code} {response.reason_phrase}",
            request=response.request,
            response=getattr(response, "response", response),
        )
        raise_if_structured_mode_rejected(status_code=response.status_code, response_body=body, error=error)
        raise ConnectionError(f"HTTP error {response.status_code}: {error}; response_body={body}") from error

    def _non_stream_completion(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> ChatResponse:
        response = self._post_chat(payload, timeout=timeout, stream=False)
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
        timeout: float | None = None,
    ) -> Iterator[ChatResponse]:
        try:
            response = self._post_chat(payload, timeout=timeout, stream=True)
        except Exception as exc:
            raise ConnectionError(f"Failed to start stream: {exc}") from exc
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
        finally:
            response.close()

    def get_models(self) -> List[ModelInfo]:
        try:
            model_dir = Path(self.config.extra_params.get("model_dir", ""))
            if not model_dir.exists():
                return []
            models: list[ModelInfo] = []
            for gguf_file in list(model_dir.rglob("*.gguf"))[:100]:
                try:
                    size = gguf_file.stat().st_size
                except OSError:
                    size = 0
                models.append(
                    ModelInfo(
                        id=gguf_file.name,
                        name=gguf_file.name,
                        provider=self.provider_name,
                        context_length=None,
                        description="Local GGUF model",
                        metadata={
                            "path": str(gguf_file),
                            "size": size,
                            "size_formatted": self._format_size(size),
                        },
                    )
                )
            return models
        except Exception as exc:
            raise ConnectionError(f"Failed to list models: {exc}") from exc

    def test_connection(self) -> bool:
        return self._is_server_running()

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "provider_type": self.provider_name,
            "display_name": self.provider_display_name,
            "description": self.provider_description,
            "fields": [
                {
                    "name": "base_url",
                    "type": "string",
                    "label": "Server URL",
                    "default": "http://localhost:8180",
                    "required": True,
                    "description": "URL of the llama.cpp server",
                },
                {
                    "name": "model",
                    "type": "select",
                    "label": "Model",
                    "required": False,
                    "description": "Select a model (auto-discovered from models directory)",
                },
                {
                    "name": "download_location",
                    "type": "select",
                    "label": "Models Location",
                    "default": "server",
                    "required": False,
                    "description": "Where to store downloaded models",
                    "options": [
                        {"value": "server", "label": "resources/models/server (recommended)"},
                        {"value": "llm", "label": "resources/models/llm"},
                    ],
                },
                {
                    "name": "auto_start",
                    "type": "boolean",
                    "label": "Auto-start Server",
                    "default": False,
                    "required": False,
                    "description": "Automatically start server when provider is selected",
                },
            ],
        }

    def _format_size(self, bytes_size: int) -> str:
        value = float(bytes_size)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if value < 1024.0:
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} PB"

    def get_capabilities(self) -> List[ProviderCapability]:
        return self.default_capabilities.copy()

    def requires_api_key(self) -> bool:
        return False
