"""Explicit compatibility adapter from provider transport to RPG LLM calls."""
from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from app.providers.base import BaseProvider, ChatMessage, ChatResponse


class LLMGatewayAdapter:
    """Expose RPG generate/call methods while keeping provider transport intact."""

    def __init__(self, provider: BaseProvider) -> None:
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        return self._provider.chat_completion(*args, **kwargs)

    def _messages(
        self,
        prompt: str,
        context: Mapping[str, Any] | None,
    ) -> list[ChatMessage]:
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a deterministic RPG narration engine. "
                    "Return concise player-facing RPG narration or NPC dialogue."
                ),
            ),
            ChatMessage(role="user", content=str(prompt or "")),
        ]
        if context:
            messages.append(
                ChatMessage(
                    role="user",
                    content="Context JSON:\n"
                    + json.dumps(context, ensure_ascii=False, default=str),
                )
            )
        return messages

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, ChatResponse):
            return str(value.content or "")
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            for key in ("text", "content", "response"):
                text = value.get(key)
                if isinstance(text, str):
                    return text
        return str(getattr(value, "content", "") or "")

    def generate(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        response = self._provider.chat_completion(
            messages=self._messages(prompt, context),
            stream=False,
            **kwargs,
        )
        return self._text(response).strip()

    def generate_stream(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[dict[str, str]]:
        response = self._provider.chat_completion(
            messages=self._messages(prompt, context),
            stream=True,
            **kwargs,
        )
        if isinstance(response, (str, ChatResponse, Mapping)):
            text = self._text(response)
            if text:
                yield {"text": text}
            return
        for chunk in response:
            text = self._text(chunk)
            if text:
                yield {"text": text}

    def call(
        self,
        method: str,
        prompt: str,
        *,
        context: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        if method in {"generate", "complete"}:
            return self.generate(prompt, context=context, **kwargs)
        if method == "generate_stream":
            return self.generate_stream(prompt, context=context, **kwargs)
        raise ValueError(f"Unsupported RPG provider method: {method}")


def adapt_base_provider(provider: Any) -> Any:
    """Wrap any BaseProvider with the RPG generation interface it lacks."""

    if isinstance(provider, LLMGatewayAdapter) or not isinstance(provider, BaseProvider):
        return provider
    return LLMGatewayAdapter(provider)
