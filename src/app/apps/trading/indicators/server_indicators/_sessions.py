"""Trading sessions for session-aware indicators: the server side of ``tradingSessions.ts``.

A bar's session is a calendar date in the session's timezone (zoneinfo, the same IANA rules the browser's ``Intl``
reads), shifted so a trading day that starts the evening before belongs to the next date. Times are epoch
milliseconds, like the browser's ``Date.parse``.
"""

from __future__ import annotations
from app.caching.bounded_cache import bounded_lru_cache

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..registry import UTC_SESSION, BarSeries, TradingSession

DAY_MS = 86_400_000
_MINUTE_MS = 60_000
_HOUR_MS = 3_600_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NAIVE_EPOCH = datetime(1970, 1, 1)
_EPOCH_DATE = date(1970, 1, 1)
_MILLISECOND = timedelta(milliseconds=1)
_OUTSIDE_REGULAR_HOURS = frozenset({"extended_pre", "extended_post", "closed"})

SessionPeriod = int | str  # whole hours, or "D", "W", "M"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def epoch_ms(moment: datetime) -> int:
    """``Date.parse`` of the API's UTC ISO string; a naive time is taken as UTC."""
    return (_aware(moment) - _EPOCH) // _MILLISECOND


@bounded_lru_cache(max_entries=256, ttl_seconds=86_400.0)
def _zone(timezone: str) -> ZoneInfo | None:
    if timezone == "UTC":
        return None
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # Intl rejects an unknown zone too; both sides then use UTC.
        return None


def wall_clock_ms(moment: datetime, timezone: str) -> int:
    """Local wall-clock time of an instant, as epoch milliseconds of the same wall clock in UTC (``wallClockMs``)."""
    zone = _zone(timezone)
    if zone is None:
        return epoch_ms(moment)
    return (_aware(moment).astimezone(zone).replace(tzinfo=None) - _NAIVE_EPOCH) // _MILLISECOND


@dataclass(frozen=True)
class SessionClock:
    day: list[int]
    offset: list[int]
    counts: list[bool]


def session_clock(bars: BarSeries, session: TradingSession | None) -> SessionClock:
    """Each bar's session day, time into the session, and whether its high, low and close count (``sessionClock``)."""
    spec = session or UTC_SESSION
    shift = (1440 - spec.start_minute) * _MINUTE_MS if spec.start_minute > 0 else 0
    labels = bars.sessions or ()
    clock = SessionClock([], [], [])
    for i, start_time in enumerate(bars.start_times):
        shifted = wall_clock_ms(start_time, spec.timezone) + shift
        day = shifted // DAY_MS
        clock.day.append(day)
        clock.offset.append(shifted - day * DAY_MS)
        label = labels[i] if i < len(labels) else ""
        clock.counts.append(not (spec.regular_only and label in _OUTSIDE_REGULAR_HOURS))
    return clock


def session_periods(clock: SessionClock, period: SessionPeriod, session: TradingSession | None) -> tuple[list[int], list[int]]:
    """Each bar's period key and its time since that period started (``sessionPeriods``)."""
    spec = session or UTC_SESSION
    anchor = 0 if spec.regular_start_minute is None else ((spec.regular_start_minute - spec.start_minute) % 1440) * _MINUTE_MS
    keys: list[int] = []
    since: list[int] = []
    for day, offset in zip(clock.day, clock.offset, strict=True):
        if period == "D":
            keys.append(day)
            since.append(offset)
        elif period == "W":
            start = day - (day + 3) % 7  # Monday; 1970-01-01 was a Thursday
            keys.append(start)
            since.append((day - start) * DAY_MS + offset)
        elif period == "M":
            calendar_date = _EPOCH_DATE + timedelta(days=day)
            keys.append(calendar_date.year * 12 + calendar_date.month - 1)
            since.append((calendar_date.day - 1) * DAY_MS + offset)
        else:
            length = int(period) * _HOUR_MS
            block = (offset - anchor) // length
            keys.append(day * 100 + block + 50)
            since.append(offset - anchor - block * length)
    return keys, since
