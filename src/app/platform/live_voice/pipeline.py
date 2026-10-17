"""Explicit live-voice prompt pipeline.

The former runtime hook order was profile, spoken style, companion context,
prompt cache, then dependency timings. The profile wrapper selected the live
path; companion context replaced its prompt builder; the cache and dependency
wrappers surrounded that builder; spoken style ran after rendering. Keep that
effective call order here as ordinary feature-owned stages.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import time
from typing import Any
from contextvars import Token

from app.platform.chat.contracts import ChatMessage, ChatSession, PromptAssembly, RenderedPrompt


@dataclass(slots=True)
class LiveVoicePromptContext:
    store: Any
    session: ChatSession
    user_message: ChatMessage
    context_items: list[dict[str, Any]] | None
    assembly: PromptAssembly | None = None
    rendered: RenderedPrompt | None = None
    settings_scope: AbstractContextManager[None] | None = None
    dependency_token: Token[dict[str, Any] | None] | None = None
    dependency_timings: dict[str, Any] | None = None
    cache_token: Token[dict[str, Any] | None] | None = None
    cache_timings: dict[str, Any] | None = None
    started_at: float | None = None


@dataclass(frozen=True, slots=True)
class LiveVoicePromptStage:
    name: str
    apply: Callable[[LiveVoicePromptContext], LiveVoicePromptContext]


def is_live_voice_message(user_message: Any) -> bool:
    metadata = getattr(user_message, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    speech_segment_id = str(metadata.get("speech_segment_id") or "").strip()
    user_turn_id = str(metadata.get("user_turn_id") or "").strip()
    return bool(speech_segment_id or user_turn_id.startswith("voice-user-turn:"))


def _profile_selection_stage(
    context: LiveVoicePromptContext,
) -> LiveVoicePromptContext:
    if not is_live_voice_message(context.user_message):
        raise ValueError("live_voice_prompt_requires_voice_turn")
    return context


def _dependency_settings_stage(
    context: LiveVoicePromptContext,
) -> LiveVoicePromptContext:
    from app.platform.live_voice.prompt.dependency_stages import (
        begin_dependency_timings,
        use_cached_memory_runtime_settings,
    )

    context.dependency_token, context.dependency_timings = begin_dependency_timings()
    context.settings_scope = use_cached_memory_runtime_settings()
    context.settings_scope.__enter__()
    return context


def _prompt_cache_stage(
    context: LiveVoicePromptContext,
) -> LiveVoicePromptContext:
    from app.platform.live_voice.prompt.cache import begin_prompt_stage_timings

    context.started_at = time.perf_counter()
    context.cache_token, context.cache_timings = begin_prompt_stage_timings()
    return context


def _companion_context_stage(
    context: LiveVoicePromptContext,
) -> LiveVoicePromptContext:
    from app.platform.live_voice.prompt.companion_context import build_companion_prompt

    context.assembly, context.rendered = build_companion_prompt(
        context.store,
        context.session,
        context.user_message,
        context.context_items,
    )
    return context


def _spoken_style_stage(
    context: LiveVoicePromptContext,
) -> LiveVoicePromptContext:
    from app.platform.live_voice.prompt.spoken_style import (
        apply_live_voice_spoken_style,
        record_spoken_style_diagnostics,
    )

    if context.assembly is None or context.rendered is None:
        raise RuntimeError("live_voice_prompt_assembly_missing")
    apply_live_voice_spoken_style(context.rendered)
    record_spoken_style_diagnostics(context.assembly, context.rendered)
    return context


def _finish_prompt_stages(context: LiveVoicePromptContext) -> None:
    total_ms = (
        (time.perf_counter() - context.started_at) * 1000.0
        if context.started_at is not None
        else 0.0
    )
    from app.platform.live_voice.prompt.dependency_stages import end_dependency_timings

    try:
        if context.started_at is not None:
            from app.platform.live_voice.prompt.cache import end_prompt_stage_timings

            if context.cache_token is not None and context.cache_timings is not None:
                end_prompt_stage_timings(
                    context.cache_token,
                    context.cache_timings,
                    session=context.session,
                    total_ms=total_ms,
                )
    finally:
        try:
            if context.dependency_token is not None and context.dependency_timings is not None:
                end_dependency_timings(
                    context.dependency_token,
                    context.dependency_timings,
                    session=context.session,
                    total_ms=total_ms,
                )
        finally:
            if context.settings_scope is not None:
                context.settings_scope.__exit__(None, None, None)


# Keep the stage list executable so prompt composition cannot depend on module
# import or gateway initialization order.
LIVE_VOICE_PROMPT_STAGES = (
    LiveVoicePromptStage("profile_selection", _profile_selection_stage),
    LiveVoicePromptStage("dependency_settings", _dependency_settings_stage),
    LiveVoicePromptStage("prompt_cache", _prompt_cache_stage),
    LiveVoicePromptStage("companion_context", _companion_context_stage),
    LiveVoicePromptStage("spoken_style", _spoken_style_stage),
)


def build_live_voice_prompt(
    store: Any,
    session: ChatSession,
    user_message: ChatMessage,
    context_items: list[dict[str, Any]] | None = None,
) -> tuple[PromptAssembly, RenderedPrompt]:
    """Run each feature-owned stage and return its assembled prompt."""
    context = LiveVoicePromptContext(
        store=store,
        session=session,
        user_message=user_message,
        context_items=context_items,
    )
    try:
        for stage in LIVE_VOICE_PROMPT_STAGES:
            context = stage.apply(context)
    finally:
        _finish_prompt_stages(context)
    if context.assembly is None or context.rendered is None:
        raise RuntimeError("live_voice_prompt_pipeline_incomplete")
    return context.assembly, context.rendered


__all__ = [
    "LIVE_VOICE_PROMPT_STAGES",
    "LiveVoicePromptContext",
    "LiveVoicePromptStage",
    "build_live_voice_prompt",
    "is_live_voice_message",
]
