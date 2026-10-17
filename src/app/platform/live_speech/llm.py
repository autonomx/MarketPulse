"""Streaming text generation adapters for live speech."""
from __future__ import annotations

from app.config.env import environment

import json
import os
from dataclasses import dataclass
from typing import Iterable

from app.runtime.http_client import shared_http_client


class StreamingTextGenerator:
    def generate(self, prompt: str, *, instructions: str = "", generation: int = 0) -> Iterable[str]:
        raise NotImplementedError


@dataclass
class EchoTextGenerator(StreamingTextGenerator):
    prefix: str = "I heard:"

    def generate(self, prompt: str, *, instructions: str = "", generation: int = 0) -> Iterable[str]:
        clean = " ".join(prompt.split())
        yield f"{self.prefix} {clean}" if clean else "I am ready."


@dataclass
class OpenAICompatibleTextGenerator(StreamingTextGenerator):
    """Adapter for LM Studio and other OpenAI-compatible streaming endpoints."""

    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = "local-model"
    timeout_seconds: float = 60.0

    def generate(self, prompt: str, *, instructions: str = "", generation: int = 0) -> Iterable[str]:
        payload = {
            "model": self.model,
            "stream": True,
            "messages": [
                {"role": "system", "content": instructions or "You are a concise realtime voice assistant."},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            headers = {}
            api_token = environment().get("LM_API_TOKEN", "").strip()
            if api_token:
                headers["Authorization"] = f"Bearer {api_token}"
            with shared_http_client("live-speech-llm").stream(
                "POST",
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            ) as response:
                response.raise_for_status()
                yielded = False
                for line in response.iter_lines():
                    token = _parse_sse_delta(line)
                    if token:
                        yielded = True
                        yield token
                if not yielded:
                    yield from EchoTextGenerator().generate(prompt, instructions=instructions, generation=generation)
        except Exception:
            yield from EchoTextGenerator().generate(prompt, instructions=instructions, generation=generation)


# LIVE_SPEECH_LLM_PROVIDER values that select an OpenAI-compatible endpoint.
_OPENAI_COMPATIBLE_SETTINGS = frozenset({"openai", "openai_compatible", "lmstudio", "real"})


def create_text_generator_from_env() -> StreamingTextGenerator:
    provider = environment().get("LIVE_SPEECH_LLM_PROVIDER", "fake").strip().lower()
    if provider in _OPENAI_COMPATIBLE_SETTINGS:
        return OpenAICompatibleTextGenerator(
            base_url=environment().get("LIVE_SPEECH_LLM_BASE_URL", "http://127.0.0.1:1234/v1"),
            model=environment().get("LIVE_SPEECH_LLM_MODEL", "local-model"),
        )
    return EchoTextGenerator()


def _parse_sse_delta(line: str | bytes | None) -> str:
    if not line:
        return ""
    text = line.decode("utf-8") if isinstance(line, bytes) else line
    text = text.strip()
    if not text:
        return ""
    if text.startswith("data:"):
        text = text[5:].strip()
    if text == "[DONE]":
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")
