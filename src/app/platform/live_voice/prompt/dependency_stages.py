"""Request-local settings reuse and direct timing for live prompt dependencies."""
from __future__ import annotations

import copy
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from app.config.env import environment
from app.providers import service as provider_service
from app.settings.access import current_settings_service
from app.platform.assistant_memory.contracts import (
    ASSISTANT_MEMORY_SETTINGS_KEY,
    AssistantMemoryRuntimeSettings,
    load_memory_runtime_settings,
    use_memory_runtime_settings,
)
from app.observability.tts_stream_diagnostics import stream_log

_MAX_SETTINGS_CACHE_ENTRIES = 8
_MEMORY_SETTINGS_CACHE_TTL_SECONDS = 5.0
_SETTINGS_ENV_NAMES = (
    "OMNIX_CHAT_MEMORY_ENABLED",
    "OMNIX_CHAT_MEMORY_SUGGESTIONS_ENABLED",
    "OMNIX_CHAT_HISTORY_RECALL_ENABLED",
    "OMNIX_CHAT_COMPACTION_ENABLED",
    "OMNIX_HERMES_MEMORY_SYNC_ENABLED",
    "OMNIX_MEMORY_AUTOMATIC_DIRECT_ASSERTIONS",
    "OMNIX_COMPANION_PROACTIVE_MEMORY_ENABLED",
    "OMNIX_COMPANION_PARALINGUISTIC_ENABLED",
    "OMNIX_CHAT_TRANSCRIPT_RETENTION_ENABLED",
    "OMNIX_COMPANION_MASTER_ENABLED",
    "OMNIX_COMPANION_ROLLOUT_STAGE",
    "OMNIX_CHAT_MEMORY_TOKEN_BUDGET",
    "OMNIX_CHAT_HISTORY_TOKEN_BUDGET",
)
_STAGE_NAMES = (
    "settings_ms",
    "memory_ms",
    "profile_ms",
    "packet_ms",
    "assembly_ms",
    "render_ms",
    "identity_ms",
)

_CACHE_LOCK = threading.RLock()
_MEMORY_SETTINGS_CACHE: OrderedDict[tuple[Any, ...], tuple[float, Any]] = OrderedDict()
_MEMORY_SETTINGS_OVERRIDE_REVISION = 0
_SETTINGS_SUBSCRIPTIONS: weakref.WeakSet[Any] = weakref.WeakSet()
_DEPENDENCY_TIMINGS: ContextVar[dict[str, Any] | None] = ContextVar(
    "omnix_live_prompt_dependency_timings",
    default=None,
)


def _clone(value: Any) -> Any:
    model_copy = getattr(value, "model_copy", None)
    if callable(model_copy):
        return model_copy(deep=True)
    return copy.deepcopy(value)


def _settings_cache_key() -> tuple[Any, ...]:
    try:
        service_key: tuple[Any, ...] = (
            "service",
            id(current_settings_service()),
            _MEMORY_SETTINGS_OVERRIDE_REVISION,
        )
    except RuntimeError:
        service_key = ("unavailable", _MEMORY_SETTINGS_OVERRIDE_REVISION)
    environment_values = tuple((name, environment().get(name)) for name in _SETTINGS_ENV_NAMES)
    return (*service_key, environment_values)


def _record_stage(name: str, elapsed_ms: float) -> None:
    timings = _DEPENDENCY_TIMINGS.get()
    if timings is not None:
        timings[name] = float(timings.get(name, 0.0) or 0.0) + elapsed_ms


def record_dependency_stage_time(name: str, elapsed_ms: float) -> None:
    _record_stage(name, elapsed_ms)


def _record_flag(name: str, value: Any) -> None:
    timings = _DEPENDENCY_TIMINGS.get()
    if timings is not None:
        timings[name] = value


def _load_memory_runtime_settings_cached() -> AssistantMemoryRuntimeSettings:
    started = time.perf_counter()
    key = _settings_cache_key()
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _MEMORY_SETTINGS_CACHE.get(key)
        if cached is not None and now - cached[0] <= _MEMORY_SETTINGS_CACHE_TTL_SECONDS:
            _MEMORY_SETTINGS_CACHE.move_to_end(key)
            _record_flag("settings_cache_hit", True)
            _record_stage("settings_ms", (time.perf_counter() - started) * 1000.0)
            return _clone(cached[1])
        if cached is not None:
            _MEMORY_SETTINGS_CACHE.pop(key, None)

    result = _ORIGINAL_LOAD_MEMORY_SETTINGS()
    with _CACHE_LOCK:
        _MEMORY_SETTINGS_CACHE[key] = (now, _clone(result))
        _MEMORY_SETTINGS_CACHE.move_to_end(key)
        while len(_MEMORY_SETTINGS_CACHE) > _MAX_SETTINGS_CACHE_ENTRIES:
            _MEMORY_SETTINGS_CACHE.popitem(last=False)
    _record_flag("settings_cache_hit", False)
    _record_stage("settings_ms", (time.perf_counter() - started) * 1000.0)
    return _clone(result)


def _invalidate_memory_settings_cache() -> None:
    global _MEMORY_SETTINGS_OVERRIDE_REVISION
    with _CACHE_LOCK:
        _MEMORY_SETTINGS_OVERRIDE_REVISION += 1
        _MEMORY_SETTINGS_CACHE.clear()


def _ensure_settings_subscription() -> None:
    try:
        service = current_settings_service()
    except RuntimeError:
        return
    with _CACHE_LOCK:
        if service in _SETTINGS_SUBSCRIPTIONS:
            return
        service.subscribe(
            ASSISTANT_MEMORY_SETTINGS_KEY,
            lambda _key, _value: _invalidate_memory_settings_cache(),
        )
        _SETTINGS_SUBSCRIPTIONS.add(service)


@contextmanager
def use_cached_memory_runtime_settings() -> Iterator[None]:
    """Use the bounded cache throughout one live prompt, without patching imports."""
    _ensure_settings_subscription()
    with use_memory_runtime_settings(_load_memory_runtime_settings_cached):
        yield


def time_prompt_dependency(
    name: str,
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    started = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        _record_stage(name, (time.perf_counter() - started) * 1000.0)


def begin_dependency_timings() -> tuple[Token[dict[str, Any] | None], dict[str, Any]]:
    timings: dict[str, Any] = {}
    return _DEPENDENCY_TIMINGS.set(timings), timings


def end_dependency_timings(
    token: Token[dict[str, Any] | None],
    timings: dict[str, Any],
    *,
    session: Any,
    total_ms: float,
) -> None:
    prompt_cache = provider_service.global_system_prompt_cache_state()
    timings["global_prompt_cache_hit"] = prompt_cache["hit"]
    timings["global_prompt_cache_mode"] = prompt_cache["mode"]
    _DEPENDENCY_TIMINGS.reset(token)
    accounted_ms = sum(float(timings.get(name, 0.0) or 0.0) for name in _STAGE_NAMES)
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_prompt_dependency_stages",
        session_message_count=len(getattr(session, "messages", []) or []),
        total_ms=round(total_ms, 3),
        settings_cache_hit=timings.get("settings_cache_hit"),
        global_prompt_cache_hit=timings.get("global_prompt_cache_hit"),
        global_prompt_cache_mode=timings.get("global_prompt_cache_mode"),
        **{
            name: round(float(timings.get(name, 0.0) or 0.0), 3)
            for name in _STAGE_NAMES
        },
        unclassified_ms=round(max(0.0, total_ms - accounted_ms), 3),
    )


def _reset_live_prompt_dependency_state_for_tests() -> None:
    global _MEMORY_SETTINGS_OVERRIDE_REVISION
    with _CACHE_LOCK:
        _MEMORY_SETTINGS_CACHE.clear()
        _MEMORY_SETTINGS_OVERRIDE_REVISION = 0
        _SETTINGS_SUBSCRIPTIONS.clear()
    provider_service.invalidate_global_system_prompt_cache()


_ORIGINAL_LOAD_MEMORY_SETTINGS = load_memory_runtime_settings

__all__ = [
    "_ORIGINAL_LOAD_MEMORY_SETTINGS",
    "_invalidate_memory_settings_cache",
    "_load_memory_runtime_settings_cached",
    "_reset_live_prompt_dependency_state_for_tests",
    "begin_dependency_timings",
    "end_dependency_timings",
    "record_dependency_stage_time",
    "time_prompt_dependency",
    "use_cached_memory_runtime_settings",
]
