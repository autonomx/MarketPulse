"""Time-aware deterministic retrieval for companion memory."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, datetime, time as clock_time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.contracts import PromptMemoryItem

from app.conversation.memory_contracts import MemoryRecord, MemoryScopeContext
from .owner_service import OwnerAwareMemoryService

_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}")
_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_PRELOAD_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="omnix-companion-preload",
)
_PRELOAD_LOCK = threading.RLock()
_PRELOAD_CACHE: dict[str, tuple[float, tuple[MemoryRecord, ...]]] = {}
_MAX_CACHE_ENTRIES = 512
_CACHE_TTL_SECONDS = 300.0
_DEFAULT_DEADLINE_MS = 50.0
_DEFAULT_BUCKET_MINUTES = 15


class TemporalRetrievalItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    kind: str
    score: int
    reasons: tuple[str, ...]
    record: MemoryRecord


class TemporalRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[TemporalRetrievalItem, ...]
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    preload_cache_hit: bool = False
    preload_timed_out: bool = False
    preload_ms: float = Field(ge=0.0)
    rank_ms: float = Field(ge=0.0)
    deadline_ms: float = Field(ge=0.0)
    timezone: str

    @property
    def prompt_memory(self) -> list[PromptMemoryItem]:
        result: list[PromptMemoryItem] = []
        for item in self.items:
            record = item.record
            source = "character" if record.owner_type == "character" else "system"
            result.append(
                PromptMemoryItem(
                    memory_id=record.id,
                    content=record.content,
                    scope=record.scope,
                    category=record.category,
                    revision=record.revision,
                    source=cast(Any, source),
                )
            )
        return result

    def content_free_diagnostics(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "selected_memory_ids": [item.memory_id for item in self.items],
            "selection_reasons": [list(item.reasons) for item in self.items],
            "scores": [item.score for item in self.items],
            "preload_cache_hit": self.preload_cache_hit,
            "preload_timed_out": self.preload_timed_out,
            "preload_ms": round(self.preload_ms, 3),
            "rank_ms": round(self.rank_ms, 3),
            "deadline_ms": round(self.deadline_ms, 3),
            "timezone": self.timezone,
        }


def resolve_companion_timezone(value: str | None = None) -> ZoneInfo:
    configured = (value or _env_str("OMNIX_USER_TIMEZONE") or "UTC").strip()
    try:
        return ZoneInfo(configured)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _aware_now(now: datetime | None, zone: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def _bucket(now: datetime, minutes: int) -> str:
    bounded = max(1, minutes)
    minute = now.minute - now.minute % bounded
    return now.replace(minute=minute, second=0, microsecond=0).isoformat()


def _cache_key(context: MemoryScopeContext, now: datetime, bucket_minutes: int) -> str:
    return "\x1f".join(
        [
            context.profile_id,
            context.workspace_id,
            context.project_id or "none",
            context.session_id,
            context.owner_type,
            context.owner_id,
            _bucket(now, bucket_minutes),
        ]
    )


def invalidate_temporal_retrieval(
    *,
    owner_type: str | None = None,
    owner_id: str | None = None,
) -> None:
    with _PRELOAD_LOCK:
        if owner_type is None and owner_id is None:
            _PRELOAD_CACHE.clear()
            return
        for key in list(_PRELOAD_CACHE):
            parts = key.split("\x1f")
            if len(parts) < 6:
                continue
            if owner_type is not None and parts[4] != owner_type:
                continue
            if owner_id is not None and parts[5] != owner_id:
                continue
            _PRELOAD_CACHE.pop(key, None)


def _preload_now() -> float:
    return time.monotonic()


def _prune_preload_cache_locked(now: float) -> None:
    for key, (expires_at, _records) in list(_PRELOAD_CACHE.items()):
        if expires_at <= now:
            _PRELOAD_CACHE.pop(key, None)


def _store_preload(key: str, records: list[MemoryRecord]) -> tuple[MemoryRecord, ...]:
    frozen = tuple(records)
    with _PRELOAD_LOCK:
        _prune_preload_cache_locked(_preload_now())
        if len(_PRELOAD_CACHE) >= _MAX_CACHE_ENTRIES:
            _PRELOAD_CACHE.pop(next(iter(_PRELOAD_CACHE)))
        _PRELOAD_CACHE[key] = (_preload_now() + _CACHE_TTL_SECONDS, frozen)
    return frozen


def _get_cached_preload(key: str) -> tuple[MemoryRecord, ...] | None:
    now = _preload_now()
    with _PRELOAD_LOCK:
        _prune_preload_cache_locked(now)
        cached = _PRELOAD_CACHE.get(key)
        return cached[1] if cached is not None else None


def _load_records(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
) -> list[MemoryRecord]:
    return service.list_active(context)


def preload_temporal_records(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
    deadline_ms: float = _DEFAULT_DEADLINE_MS,
    bucket_minutes: int = _DEFAULT_BUCKET_MINUTES,
) -> tuple[tuple[MemoryRecord, ...], bool, bool, float, str]:
    """Return cached records or fall back at the deadline while warming the cache."""

    zone = resolve_companion_timezone(timezone_name)
    local_now = _aware_now(now, zone)
    key = _cache_key(context, local_now, bucket_minutes)
    started = time.perf_counter()
    cached = _get_cached_preload(key)
    if cached is not None:
        return cached, True, False, (time.perf_counter() - started) * 1000.0, zone.key

    future = _PRELOAD_EXECUTOR.submit(_load_records, service, context)
    try:
        loaded = future.result(timeout=max(0.0, deadline_ms) / 1000.0)
    except TimeoutError:
        future.add_done_callback(
            lambda completed: _store_preload(key, completed.result())
            if completed.exception() is None
            else None
        )
        return (), False, True, (time.perf_counter() - started) * 1000.0, zone.key
    return (
        _store_preload(key, loaded),
        False,
        False,
        (time.perf_counter() - started) * 1000.0,
        zone.key,
    )


def _terms(value: str) -> frozenset[str]:
    return frozenset(term.casefold() for term in _TERM_PATTERN.findall(value))


def _parse_datetime(value: Any, zone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _parse_clock(value: Any) -> clock_time | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def _parse_exception_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _routine_exception_active(payload: dict[str, Any], now: datetime) -> bool:
    current_date = now.date()
    for raw in payload.get("exceptions") or []:
        value = str(raw).strip()
        if not value:
            continue
        if "/" in value:
            start_value, end_value = value.split("/", 1)
            start_date = _parse_exception_date(start_value)
            end_date = _parse_exception_date(end_value)
            if start_date is not None and end_date is not None and start_date <= current_date <= end_date:
                return True
            continue
        exception_date = _parse_exception_date(value)
        if exception_date == current_date:
            return True
    return False


def _routine_active(record: MemoryRecord, now: datetime) -> tuple[bool, int, list[str]]:
    payload = record.structured_payload
    if _routine_exception_active(payload, now):
        return False, 0, ["routine_exception"]
    days = [str(item).upper() for item in payload.get("days") or []]
    if days and _WEEKDAYS[now.weekday()] not in days:
        return False, 0, ["routine_wrong_weekday"]
    start = _parse_clock(payload.get("start_time"))
    end = _parse_clock(payload.get("end_time"))
    current = now.time().replace(tzinfo=None)
    if start is None and end is None:
        return True, 350, ["routine_day_active"]
    if start is not None and end is None:
        start_dt = datetime.combine(now.date(), start, tzinfo=now.tzinfo)
        delta = abs((now - start_dt).total_seconds())
        if delta <= 45 * 60:
            proximity = max(0, 300 - int(delta // 60) * 5)
            return True, 500 + proximity, ["routine_start_window"]
        return False, 0, ["routine_outside_window"]
    if start is None or end is None:
        return False, 0, ["routine_invalid_window"]
    active = start <= current <= end if start <= end else current >= start or current <= end
    return (
        (True, 700, ["routine_time_window"])
        if active
        else (False, 0, ["routine_outside_window"])
    )


def _temporal_score(record: MemoryRecord, now: datetime) -> tuple[int, list[str], bool]:
    payload = record.structured_payload
    reasons: list[str] = []
    score = int(record.confidence * 100)
    if record.pinned:
        score += 500
        reasons.append("pinned")
    if record.kind == "routine":
        active, bonus, routine_reasons = _routine_active(record, now)
        reasons.extend(routine_reasons)
        if not active:
            return score, reasons, False
        score += bonus
    elif record.kind == "open_loop":
        if payload.get("state", "open") != "open":
            return score, ["open_loop_closed"], False
        score += 350
        reasons.append("open_loop_active")
        due = _parse_datetime(payload.get("due_at"), now.tzinfo)  # type: ignore[arg-type]
        if due is not None:
            distance = due - now
            if timedelta(0) <= distance <= timedelta(days=1):
                score += 400
                reasons.append("open_loop_due_soon")
            elif distance < timedelta(0):
                score += 250
                reasons.append("open_loop_overdue")
    elif record.kind == "goal":
        if payload.get("state", "active") != "active":
            return score, ["goal_inactive"], False
        score += 275 + int(payload.get("priority") or 0)
        reasons.append("goal_active")
    elif record.kind == "episode":
        occurred = _parse_datetime(payload.get("occurred_at"), now.tzinfo)  # type: ignore[arg-type]
        if occurred is not None:
            age = now - occurred
            if age > timedelta(days=30):
                return score, ["episode_stale"], False
            score += max(0, 300 - age.days * 10)
            reasons.append("recent_episode")
        score += int(payload.get("importance") or 0)
        score += int(payload.get("emotional_relevance") or 0)
    elif record.kind == "temporal_fact":
        valid_from = _parse_datetime(payload.get("valid_from"), now.tzinfo)  # type: ignore[arg-type]
        valid_until = _parse_datetime(payload.get("valid_until"), now.tzinfo)  # type: ignore[arg-type]
        if valid_from is not None and now < valid_from:
            return score, ["temporal_fact_not_started"], False
        if valid_until is not None and now > valid_until:
            return score, ["temporal_fact_expired"], False
        score += 325
        reasons.append("temporal_fact_active")
    elif record.kind == "relationship_state":
        score += 200
        reasons.append("relationship_context")
    elif record.kind in {"instruction", "preference", "pronunciation"}:
        score += 225
        reasons.append("stable_companion_preference")
    else:
        score += 100
        reasons.append("stable_memory")
    return score, reasons, True


def rank_temporal_records(
    records: tuple[MemoryRecord, ...] | list[MemoryRecord],
    query: str,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
    limit: int = 12,
) -> tuple[TemporalRetrievalItem, ...]:
    zone = resolve_companion_timezone(timezone_name)
    local_now = _aware_now(now, zone)
    query_terms = _terms(query)
    ranked: list[TemporalRetrievalItem] = []
    for record in records:
        if record.status != "active":
            continue
        score, reasons, allowed = _temporal_score(record, local_now)
        if not allowed:
            continue
        overlap = len(query_terms & _terms(record.content))
        if overlap:
            score += overlap * 175
            reasons.append("current_turn_term_overlap")
        ranked.append(
            TemporalRetrievalItem(
                memory_id=record.id,
                kind=record.kind,
                score=score,
                reasons=tuple(reasons),
                record=record,
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.memory_id))
    return tuple(ranked[: max(0, min(int(limit), 50))])


def retrieve_temporal_context(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
    query: str,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
    deadline_ms: float = _DEFAULT_DEADLINE_MS,
    limit: int = 12,
) -> TemporalRetrievalResult:
    records, cache_hit, timed_out, preload_ms, resolved_zone = preload_temporal_records(
        service,
        context,
        now=now,
        timezone_name=timezone_name,
        deadline_ms=deadline_ms,
    )
    rank_started = time.perf_counter()
    items = (
        rank_temporal_records(
            records,
            query,
            now=now,
            timezone_name=resolved_zone,
            limit=limit,
        )
        if not timed_out
        else ()
    )
    rank_ms = (time.perf_counter() - rank_started) * 1000.0
    return TemporalRetrievalResult(
        items=items,
        candidate_count=len(records),
        selected_count=len(items),
        preload_cache_hit=cache_hit,
        preload_timed_out=timed_out,
        preload_ms=preload_ms,
        rank_ms=rank_ms,
        deadline_ms=max(0.0, deadline_ms),
        timezone=resolved_zone,
    )


__all__ = [
    "TemporalRetrievalItem",
    "TemporalRetrievalResult",
    "invalidate_temporal_retrieval",
    "preload_temporal_records",
    "rank_temporal_records",
    "resolve_companion_timezone",
    "retrieve_temporal_context",
]
