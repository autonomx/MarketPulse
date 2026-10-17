"""Bounded caches for stable inputs used by the live-voice prompt pipeline."""
from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.platform.characters.contracts import (
    InteractionSelection,
    LiveConversationProfileStore,
    default_character_service,
    resolve_interaction_context,
)
from app.platform.chat.contracts import resolve_system_session_identity
from app.observability.tts_stream_diagnostics import stream_log

_MAX_CACHE_ENTRIES = 256
_CACHE_TTL_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    expires_at: float
    value: Any


_CACHE_LOCK = threading.RLock()
_CHARACTER_SNAPSHOTS: OrderedDict[tuple[str, int], "_CacheEntry"] = OrderedDict()
_IDENTITY_CONTEXTS: OrderedDict[tuple[Any, ...], _CacheEntry] = OrderedDict()
_PROFILE_ENVELOPES: OrderedDict[tuple[Any, ...], _CacheEntry] = OrderedDict()
_PROMPT_STAGE_TIMINGS: ContextVar[dict[str, Any] | None] = ContextVar(
    "omnix_live_prompt_stage_timings",
    default=None,
)


def _clone(value: Any) -> Any:
    model_copy = getattr(value, "model_copy", None)
    if callable(model_copy):
        return model_copy(deep=True)
    return copy.deepcopy(value)


def _bounded_put(cache: OrderedDict[Any, Any], key: Any, value: Any) -> None:
    now = _cache_now()
    with _CACHE_LOCK:
        _prune_expired_locked(now)
        cache[key] = _CacheEntry(now + _CACHE_TTL_SECONDS, _clone(value))
        cache.move_to_end(key)
        while len(cache) > _MAX_CACHE_ENTRIES:
            cache.popitem(last=False)


def _cache_now() -> float:
    return time.monotonic()


def _prune_expired_locked(now: float) -> None:
    for cache in (_CHARACTER_SNAPSHOTS, _IDENTITY_CONTEXTS, _PROFILE_ENVELOPES):
        for key, entry in list(cache.items()):
            if entry.expires_at <= now:
                cache.pop(key, None)


def _cache_get(cache: OrderedDict[Any, _CacheEntry], key: Any) -> Any | None:
    with _CACHE_LOCK:
        now = _cache_now()
        _prune_expired_locked(now)
        entry = cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            cache.pop(key, None)
            return None
        cache.move_to_end(key)
        return _clone(entry.value)


def _character_snapshot_version(snapshot: Any) -> int:
    try:
        return int(getattr(snapshot, "version", 0) or 0)
    except (TypeError, ValueError):
        return 0


def cache_character_snapshot(snapshot: Any) -> None:
    """Seed the prompt cache with an immutable, version-pinned character."""
    character_id = str(getattr(snapshot, "id", "") or "").strip()
    version = _character_snapshot_version(snapshot)
    if not character_id or version < 1:
        return
    with _CACHE_LOCK:
        _bounded_put(_CHARACTER_SNAPSHOTS, (character_id, version), snapshot)


def _invalidate_character(character_id: str) -> None:
    normalized = str(character_id or "").strip()
    if not normalized:
        return
    with _CACHE_LOCK:
        for key in [key for key in _CHARACTER_SNAPSHOTS if key[0] == normalized]:
            _CHARACTER_SNAPSHOTS.pop(key, None)
        for key in [key for key in _IDENTITY_CONTEXTS if key[1] == normalized]:
            _IDENTITY_CONTEXTS.pop(key, None)


class CharacterSnapshotCacheObserver:
    """Keeps the prompt cache in step with characters (characters' snapshot observer port, PA-3.4)."""

    def on_resolve(self, snapshot: Any) -> None:
        cache_character_snapshot(snapshot)

    def on_change(self, character_id: str) -> None:
        _invalidate_character(character_id)


def _identity_key(session: Any) -> tuple[Any, ...]:
    return (
        str(getattr(session, "interaction_mode", "system") or "system"),
        str(getattr(session, "character_id", "") or ""),
        getattr(session, "character_profile_version", None),
        str(getattr(session, "effective_identity_hash", "") or ""),
        str(getattr(session, "voice_asset_id", "") or ""),
        bool(getattr(session, "read_memory", False)),
        bool(getattr(session, "write_memory", False)),
        str(getattr(session, "shared_memory_access", "none") or "none"),
        str(getattr(session, "transcript_policy", "persistent") or "persistent"),
    )


def _record_stage(name: str, elapsed_ms: float) -> None:
    timings = _PROMPT_STAGE_TIMINGS.get()
    if timings is not None:
        timings[name] = timings.get(name, 0.0) + elapsed_ms


def record_prompt_stage_time(name: str, elapsed_ms: float) -> None:
    _record_stage(name, elapsed_ms)


def _record_flag(name: str, value: bool) -> None:
    timings = _PROMPT_STAGE_TIMINGS.get()
    if timings is not None:
        timings[name] = value


def resolve_system_session_identity_cached(session: Any) -> Any:
    """Resolve identity using a version-keyed snapshot and a bounded LRU."""
    started = time.perf_counter()
    key = _identity_key(session)
    with _CACHE_LOCK:
        cached = _cache_get(_IDENTITY_CONTEXTS, key)
        if cached is not None:
            result = cached
            _record_flag("identity_cache_hit", True)
            _record_stage("identity_ms", (time.perf_counter() - started) * 1000.0)
            return result

    if key[0] != "character":
        result = resolve_system_session_identity(session)
    else:
        character_id = key[1]
        expected_version = key[2]
        snapshot = None
        if character_id and isinstance(expected_version, int) and expected_version > 0:
            with _CACHE_LOCK:
                snapshot = _cache_get(_CHARACTER_SNAPSHOTS, (character_id, expected_version))
        if snapshot is None:
            snapshot = default_character_service().resolve_snapshot(character_id)
            cache_character_snapshot(snapshot)
        selection = InteractionSelection(
            interaction_mode="character",
            character_id=character_id,
            voice_asset_id=getattr(session, "voice_asset_id", None),
            read_memory=bool(getattr(session, "read_memory", False)),
            write_memory=bool(getattr(session, "write_memory", False)),
            shared_memory_access=getattr(session, "shared_memory_access", "none"),
            transcript_policy=getattr(session, "transcript_policy", "persistent"),
        )
        result = resolve_interaction_context(selection, character=snapshot)

    with _CACHE_LOCK:
        _bounded_put(_IDENTITY_CONTEXTS, key, result)
    _record_flag("identity_cache_hit", False)
    _record_stage("identity_ms", (time.perf_counter() - started) * 1000.0)
    return _clone(result)


def _profile_signature(store: Any) -> tuple[str, int, int] | None:
    path = getattr(store, "path", None)
    if not isinstance(path, Path):
        return None
    try:
        stat = path.stat()
        return str(path), int(stat.st_mtime_ns), int(stat.st_size)
    except FileNotFoundError:
        return str(path), 0, 0
    except OSError:
        return None


def get_live_conversation_profile_cached(
    store: LiveConversationProfileStore,
    session_id: str,
) -> Any:
    """Reuse a file-backed profile while its backing signature is unchanged."""
    started = time.perf_counter()
    signature = _profile_signature(store)
    key = (id(store), signature, session_id)
    if signature is not None:
        with _CACHE_LOCK:
            cached = _cache_get(_PROFILE_ENVELOPES, key)
            if cached is not None:
                _record_flag("profile_cache_hit", True)
                _record_stage("profile_store_ms", (time.perf_counter() - started) * 1000.0)
                return cached
    result = store.get(session_id)
    if signature is not None:
        refreshed_key = (id(store), _profile_signature(store), session_id)
        with _CACHE_LOCK:
            _bounded_put(_PROFILE_ENVELOPES, refreshed_key, result)
    _record_flag("profile_cache_hit", False)
    _record_stage("profile_store_ms", (time.perf_counter() - started) * 1000.0)
    return result


def time_prompt_dependency(name: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Time one explicitly invoked prompt dependency without wrapping its owner."""
    started = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        _record_stage(name, (time.perf_counter() - started) * 1000.0)


def begin_prompt_stage_timings() -> tuple[Token[dict[str, Any] | None], dict[str, Any]]:
    timings: dict[str, Any] = {}
    return _PROMPT_STAGE_TIMINGS.set(timings), timings


def end_prompt_stage_timings(
    token: Token[dict[str, Any] | None],
    timings: dict[str, Any],
    *,
    session: Any,
    total_ms: float,
) -> None:
    _PROMPT_STAGE_TIMINGS.reset(token)
    accounted_ms = sum(
        float(timings.get(name, 0.0) or 0.0)
        for name in ("memory_ms", "profile_ms", "assembly_ms", "render_ms")
    )
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_prompt_stages",
        session_message_count=len(getattr(session, "messages", []) or []),
        total_ms=round(total_ms, 3),
        memory_ms=round(float(timings.get("memory_ms", 0.0) or 0.0), 3),
        profile_ms=round(float(timings.get("profile_ms", 0.0) or 0.0), 3),
        profile_store_ms=round(float(timings.get("profile_store_ms", 0.0) or 0.0), 3),
        profile_cache_hit=timings.get("profile_cache_hit"),
        assembly_ms=round(float(timings.get("assembly_ms", 0.0) or 0.0), 3),
        identity_ms=round(float(timings.get("identity_ms", 0.0) or 0.0), 3),
        identity_cache_hit=timings.get("identity_cache_hit"),
        render_ms=round(float(timings.get("render_ms", 0.0) or 0.0), 3),
        residual_ms=round(max(0.0, total_ms - accounted_ms), 3),
    )


def clear_live_prompt_caches() -> None:
    """Clear cached prompt dependencies after owner changes or process policy updates."""
    with _CACHE_LOCK:
        _CHARACTER_SNAPSHOTS.clear()
        _IDENTITY_CONTEXTS.clear()
        _PROFILE_ENVELOPES.clear()


def _reset_live_prompt_cache_for_tests() -> None:
    clear_live_prompt_caches()


__all__ = [
    "_reset_live_prompt_cache_for_tests",
    "cache_character_snapshot",
    "end_prompt_stage_timings",
    "CharacterSnapshotCacheObserver",
    "get_live_conversation_profile_cached",
    "record_prompt_stage_time",
    "resolve_system_session_identity_cached",
    "time_prompt_dependency",
    "begin_prompt_stage_timings",
    "clear_live_prompt_caches",
]
