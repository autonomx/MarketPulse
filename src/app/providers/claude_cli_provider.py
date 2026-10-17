"""Claude models through the locally installed Claude Code CLI (``claude -p``).

Like the ChatGPT Codex provider, this one reads, copies and stores no
credentials: authentication stays with the local Claude client (``claude``
login or its own configuration). Each call runs one ``claude -p`` process in an
empty working directory, with every tool, setting source, MCP server, slash
command and session file disabled, so the model answers as a plain LLM with the
system prompt Omnix supplies. Output arrives as ``stream-json`` events, which
serve both streaming and single responses. A JSON Schema response format is
passed to the CLI's own ``--json-schema`` validation as well as to the prompt.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from app.config.env import environment_copy as _process_environment

from .base import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ConnectionError,
    ModelInfo,
    ProviderCapability,
    ProviderError,
    current_turn_owner,
)
from .provider_trace import provider_call_enter, provider_call_exit

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_MODEL = "sonnet"
DEFAULT_CLAUDE_PATH = "claude"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
MODEL_ALIASES = ("sonnet", "opus", "haiku", "fable")
# The models the Claude Code CLI offers in its picker (its own catalog, newest first), as (id, name, description).
CLAUDE_CLI_MODELS = (
    ("claude-opus-5-5", "Opus 5.5", "For complex work and everyday tasks"),
    ("claude-fable-5-1", "Fable 5.1", "For your toughest challenges"),
    ("claude-sonnet-5-5", "Sonnet 5.5", "Most efficient for simpler tasks"),
    ("claude-haiku-5-5", "Haiku 5.5", "Fastest for quick answers"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5", "Fastest for quick answers"),
    ("claude-sonnet-5", "Sonnet 5", "Efficient for routine tasks"),
    ("claude-opus-5", "Opus 5", "Previous Opus version"),
    ("claude-fable-5", "Fable 5", "Previous Fable version"),
    ("claude-opus-4-8", "Opus 4.8", "Legacy"),
    ("claude-opus-4-7", "Opus 4.7", "Legacy"),
    ("claude-opus-4-6", "Opus 4.6", "Legacy"),
    ("claude-sonnet-4-6", "Sonnet 4.6", "Legacy"),
    ("claude-opus-4-1-20250805", "Opus 4.1", "Legacy"),
)
_ALIAS_NAMES = {alias: f"Latest {alias.capitalize()} ({alias})" for alias in MODEL_ALIASES}
_ROLE_LABELS = {"user": "USER", "assistant": "ASSISTANT", "tool": "TOOL"}
# Windows limits a command line to 32,767 characters; a larger schema stays in the prompt only.
_MAX_SCHEMA_ARGUMENT = 16_000


class ClaudeCliTimeout(ProviderError):
    """The Claude CLI did not finish within the request's deadline."""


def _bundled_executable_candidates() -> list[Path]:
    """Where a Claude CLI is installed when ``claude`` is not on PATH (newest first)."""
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / "claude.exe",
        home / ".local" / "bin" / "claude",
        home / ".claude" / "local" / "claude",
        home / "AppData" / "Roaming" / "npm" / "claude.cmd",
    ]
    extensions = home / ".vscode" / "extensions"
    if extensions.is_dir():
        bundled = sorted(
            (path for path in extensions.glob("anthropic.claude-code-*/resources/native-binary/claude*")
             if path.suffix in {"", ".exe"}),
            key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parts[-4])),
            reverse=True,
        )
        candidates.extend(bundled)
    return candidates


def resolve_claude_executable(claude_path: str) -> str | None:
    value = str(claude_path or DEFAULT_CLAUDE_PATH).strip()
    if not value:
        return None
    if os.path.isabs(value) or any(sep in value for sep in (os.sep, "/", "\\")):
        path = Path(value).expanduser()
        return str(path.resolve()) if path.is_file() else None
    resolved = shutil.which(value)
    if resolved:
        return str(Path(resolved).resolve())
    if value.lower() in {DEFAULT_CLAUDE_PATH, f"{DEFAULT_CLAUDE_PATH}.exe"}:
        for candidate in _bundled_executable_candidates():
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def claude_cli_models(configured: str = DEFAULT_CLAUDE_MODEL, provider_name: str = "claude_cli") -> list[ModelInfo]:
    """Every model the CLI offers, then the aliases; a configured model outside them comes first."""
    rows = [*CLAUDE_CLI_MODELS, *((alias, _ALIAS_NAMES[alias], "Always the newest model of this family")
                                   for alias in MODEL_ALIASES)]
    configured = str(configured or "").strip()
    if configured and all(model_id != configured for model_id, _, _ in rows):
        rows.insert(0, (configured, configured, "Configured model"))
    return [
        ModelInfo(id=model_id, name=name, provider=provider_name, description=description,
                  capabilities=[ProviderCapability.CHAT])
        for model_id, name, description in rows
    ]


def _response_schema(response_format: Any) -> dict[str, Any] | None:
    from .chatgpt_codex_provider import _schema_from_response_format

    return _schema_from_response_format(response_format)


def _structured_instruction(response_format: Any) -> str:
    # The same structured contract text the Codex provider sends for a JSON response.
    from .chatgpt_codex_provider import ChatGPTCodexProvider

    return ChatGPTCodexProvider._structured_response_instruction(response_format)


def split_messages(messages: list[ChatMessage]) -> tuple[str, str]:
    """The system prompt and the user turn: one message as itself, a conversation as a labelled transcript."""
    system = "\n\n".join(str(message.content or "") for message in messages if message.role == "system").strip()
    turns = [message for message in messages if message.role != "system"]
    if len(turns) == 1 and turns[0].role == "user":
        return system, str(turns[0].content or "")
    rendered = [
        f"{_ROLE_LABELS.get(message.role, message.role.upper())}: {message.content or ''}" for message in turns
    ]
    return system, "\n\n".join(rendered)


class ClaudeCliProvider(BaseProvider):
    """Use the Claude Code CLI (subscription or its own configured key) as an Omnix LLM provider."""

    provider_name = "claude_cli"
    provider_display_name = "Claude (Claude Code CLI)"
    provider_description = (
        "Claude models through the locally installed Claude Code CLI. "
        "Omnix stores no Anthropic credentials; the CLI keeps its own login."
    )
    default_capabilities = [ProviderCapability.CHAT, ProviderCapability.STREAMING, ProviderCapability.MODELS]

    def __init__(self, config):
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._processes_lock = threading.Lock()
        super().__init__(config)

    def _validate_config(self):
        extra = self.config.extra_params
        claude_path = str(extra.get("claude_path") or DEFAULT_CLAUDE_PATH).strip()
        effort = str(extra.get("effort") or "").strip().lower()
        if not claude_path:
            raise ValueError("Claude CLI requires an executable path")
        if effort and effort not in EFFORT_LEVELS:
            raise ValueError(f"Claude CLI effort must be one of {', '.join(EFFORT_LEVELS)}")
        self.config.model = str(self.config.model or DEFAULT_CLAUDE_MODEL).strip() or DEFAULT_CLAUDE_MODEL
        extra["claude_path"] = claude_path
        extra["effort"] = effort

    def requires_api_key(self) -> bool:
        return False

    @property
    def claude_path(self) -> str:
        return str(self.config.extra_params.get("claude_path") or DEFAULT_CLAUDE_PATH)

    @property
    def effort(self) -> str:
        return str(self.config.extra_params.get("effort") or "")

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "provider_type": self.provider_name,
            "display_name": self.provider_display_name,
            "description": self.provider_description,
            "fields": [
                {"name": "model", "type": "string", "default": DEFAULT_CLAUDE_MODEL,
                 "description": "A model alias (sonnet, opus, haiku, fable) or a full model name."},
                {"name": "claude_path", "type": "string", "default": DEFAULT_CLAUDE_PATH,
                 "description": "The Claude CLI executable; found automatically when left as 'claude'."},
                {"name": "effort", "type": "string", "default": "", "enum": ["", *EFFORT_LEVELS],
                 "description": "Effort level; empty uses the CLI default."},
            ],
        }

    def get_models(self) -> list[ModelInfo]:
        return claude_cli_models(str(self.config.model or DEFAULT_CLAUDE_MODEL), self.provider_name)

    def test_connection(self) -> bool:
        executable = resolve_claude_executable(self.claude_path)
        if not executable:
            return False
        try:
            result = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, env=self._environment(),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def cancel_active_request(self, owner: Optional[str] = None) -> bool:
        with self._processes_lock:
            targets = [process for key, process in self._processes.items() if owner is None or key == owner]
        for process in targets:
            if process.poll() is None:
                process.kill()
        return bool(targets)

    @staticmethod
    def _environment() -> dict[str, str]:
        environment = _process_environment()
        # A Claude Code session exports these to its children; Omnix's call is not part of that session.
        for name in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
            environment.pop(name, None)
        return environment

    def _timeout_seconds(self, value: Any) -> float:
        configured = max(1.0, float(self.config.timeout))
        try:
            return min(configured, max(1.0, float(value))) if value is not None else configured
        except (TypeError, ValueError):
            return configured

    def chat_completion(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> ChatResponse | Iterator[ChatResponse]:
        if not messages:
            raise ValueError("Messages list cannot be empty")
        selected_model = str(model or self.config.model or DEFAULT_CLAUDE_MODEL).strip()
        system, prompt = split_messages(list(messages))
        instruction = _structured_instruction(kwargs.get("response_format"))
        if instruction:
            system = f"{system}\n\n{instruction}".strip()
        timeout = self._timeout_seconds(kwargs.get("request_timeout_seconds"))
        trace_row = provider_call_enter(
            provider=self.provider_name, method="chat_completion", model=selected_model,
            messages=list(messages), extra={"stream": bool(stream)},
        )
        schema = _response_schema(kwargs.get("response_format"))
        events = self._run(selected_model, system, prompt, timeout=timeout, owner=current_turn_owner(), schema=schema)
        if stream:
            def traced() -> Iterator[ChatResponse]:
                try:
                    yield from events
                    provider_call_exit(trace_row, ok=True)
                except Exception as exc:
                    provider_call_exit(trace_row, ok=False, error=f"{type(exc).__name__}: {exc}")
                    raise
            return traced()
        try:
            parts: list[str] = []
            final: ChatResponse | None = None
            for chunk in events:
                if chunk.finish_reason is not None:
                    final = chunk
                elif chunk.content:
                    parts.append(chunk.content)
            response = ChatResponse(
                content="".join(parts) or (final.content if final else ""),
                model=final.model if final else selected_model,
                usage=final.usage if final else None,
                finish_reason=final.finish_reason if final else "stop",
                raw_response=final.raw_response if final else None,
            )
        except Exception as exc:
            provider_call_exit(trace_row, ok=False, error=f"{type(exc).__name__}: {exc}")
            raise
        provider_call_exit(trace_row, ok=True)
        return response

    def _command(self, executable: str, model: str, system_file: Path, schema: dict[str, Any] | None = None) -> list[str]:
        command = [
            executable, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", model,
            "--system-prompt-file", str(system_file),
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--disable-slash-commands",
        ]
        if self.effort:
            command += ["--effort", self.effort]
        if schema is not None:
            encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            if len(encoded) <= _MAX_SCHEMA_ARGUMENT:
                command += ["--json-schema", encoded]
        return command

    def _run(
        self, model: str, system: str, prompt: str, *, timeout: float, owner: str | None,
        schema: dict[str, Any] | None = None,
    ) -> Iterator[ChatResponse]:
        """Run one ``claude -p`` call; yield text deltas, then one final chunk with the result.

        A schema-bound call yields only the final JSON: its text deltas may be
        commentary around the structured output.
        """
        executable = resolve_claude_executable(self.claude_path)
        if not executable:
            raise ConnectionError(f"Claude CLI executable not found: {self.claude_path}")
        workdir = tempfile.mkdtemp(prefix="omnix-claude-")
        key = owner or f"call:{id(threading.current_thread())}:{time.monotonic_ns()}"
        try:
            system_file = Path(workdir) / "system.txt"
            system_file.write_text(system or "You are a helpful assistant.", encoding="utf-8")
            kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                "cwd": workdir, "env": self._environment(), "text": True, "encoding": "utf-8", "errors": "replace",
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = subprocess.Popen(self._command(executable, model, system_file, schema), **kwargs)
            except OSError as exc:
                raise ConnectionError(f"Claude CLI could not start: {exc}") from exc
            with self._processes_lock:
                self._processes[key] = process
            timer = threading.Timer(timeout, process.kill)
            timer.daemon = True
            timer.start()
            stderr_tail: list[str] = []
            stderr_reader = threading.Thread(
                target=lambda: stderr_tail.extend(process.stderr.read().splitlines()[-20:]), daemon=True,
            )
            stderr_reader.start()
            started = time.monotonic()
            try:
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(prompt)
                process.stdin.close()
                result: dict[str, Any] | None = None
                resolved = model
                streamed = False
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "stream_event":
                        inner = event.get("event") if isinstance(event.get("event"), dict) else {}
                        delta = inner.get("delta") if isinstance(inner.get("delta"), dict) else {}
                        if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                            text = str(delta.get("text") or "")
                            if text and schema is None:
                                streamed = True
                                yield ChatResponse(content=text, model=model)
                    elif event.get("type") == "system" and event.get("subtype") == "init":
                        resolved = str(event.get("model") or model)
                    elif event.get("type") == "result":
                        result = event
                process.wait()
            finally:
                timer.cancel()
                stderr_reader.join(timeout=1)
            if result is None:
                if time.monotonic() - started >= timeout:
                    raise ClaudeCliTimeout(f"Claude CLI exceeded {timeout:.0f}s")
                detail = " ".join(stderr_tail)[-500:]
                raise ProviderError(f"Claude CLI ended without a result (exit {process.returncode}): {detail}")
            if result.get("is_error"):
                raise ProviderError(f"Claude CLI error: {str(result.get('result') or result.get('subtype'))[:500]}")
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            structured = result.get("structured_output")
            if structured is not None:
                text = json.dumps(structured, ensure_ascii=False)
            else:
                text = "" if streamed else str(result.get("result") or "")
            yield ChatResponse(
                content=text,
                model=str(resolved),
                usage={
                    "prompt_tokens": int(usage.get("input_tokens") or 0),
                    "completion_tokens": int(usage.get("output_tokens") or 0),
                },
                finish_reason=str(result.get("stop_reason") or "stop"),
                raw_response={"transport": "claude_cli", "duration_ms": result.get("duration_ms")},
            )
        finally:
            with self._processes_lock:
                self._processes.pop(key, None)
            shutil.rmtree(workdir, ignore_errors=True)
