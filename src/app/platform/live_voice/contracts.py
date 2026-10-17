"""Ports used to compose the live-voice conversation pipeline.

The TTS ports belong to the speech capability (``app.platform.voice.contracts``, PA-3.3).
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Protocol


class TranscriptStore(Protocol):
    def get_session(self, session_id: str) -> Any | None: ...
    def append_user_message(self, *args: Any, **kwargs: Any) -> Any: ...
    def append_assistant_message(self, *args: Any, **kwargs: Any) -> Any: ...


class PromptBuilder(Protocol):
    def build(self, *args: Any, **kwargs: Any) -> Sequence[Any]: ...


class LLMStream(Protocol):
    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]: ...


class SpeculationCache(Protocol):
    def clear(self) -> None: ...
    def snapshot(self) -> Sequence[dict[str, Any]]: ...


class Metrics(Protocol):
    def emit(self, event: str, **details: Any) -> None: ...
