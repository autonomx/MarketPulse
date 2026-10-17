"""Clock-aligned bar aggregation for charts (TVP-2.5).

Count-mode aggregation (``aggregation.aggregate_market_bars``) groups a fixed number
of base bars from the first one a provider returns; the strategy runner relies on
it and it stays unchanged. Charts instead need buckets on the clock, so a ``7m``
chart keeps the same candle edges however much history is fetched:

- 24x7 markets bucket intraday intervals from UTC midnight (a length that doesn't
  divide the day leaves a short last bucket), N days from a fixed epoch, weeks
  from a fixed Monday and months by calendar month, all on the UTC date.
- U.S. equities bucket by the exchange-local session date. Intraday buckets start
  at 04:00 with extended hours or 09:30 without, and are cut at the pre-market,
  regular and post-market boundaries (early closes included), so each bucket
  carries one session. Daily and longer buckets hold regular-session bars and run
  from the first session open to the last session close of their period; N-day
  buckets count trading days from a fixed epoch, so they skip weekends and holidays.
- Partial buckets end at their boundary (clipped at the session close). A partial
  first bucket is dropped unless the provider says history is complete; the last
  bucket is kept and is not final until its end has passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.market_session_status import US_EQUITY_CALENDARS
from app.apps.trading.models import MarketBar
from app.apps.trading.us_equity_calendar import (
    early_close_time,
    regular_close_time,
    regular_holidays,
    us_equity_session,
)
from app.caching.bounded_cache import bounded_lru_cache

from .bar_semantics import interval_duration

_INTERVAL = re.compile(r"^([1-9][0-9]*)(mo|m|h|d|w)$")
_EPOCH = date(1970, 1, 1)
_EPOCH_MONDAY = date(1970, 1, 5)
_DAY = timedelta(days=1)
_PRE_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)


@dataclass(frozen=True)
class _Bucket:
    key: tuple[object, ...]
    start: datetime
    end: datetime
    session: str


@dataclass(frozen=True)
class _Shape:
    kind: str  # "intraday", "days", "weeks", "months" or "span"
    count: int
    duration: timedelta


def _shape(target_interval: str) -> _Shape:
    match = _INTERVAL.fullmatch(target_interval.strip().lower())
    if match is None:
        raise ValueError(f"unsupported Trading interval: {target_interval}")
    count = int(match.group(1))
    unit = match.group(2)
    duration = interval_duration(target_interval)
    if unit == "mo":
        return _Shape("months", count, duration)
    if unit == "w":
        return _Shape("weeks", count, duration)
    if duration < _DAY:
        return _Shape("intraday", count, duration)
    if duration % _DAY == timedelta(0):
        return _Shape("days", duration // _DAY, duration)
    return _Shape("span", count, duration)


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=timezone.utc)


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _month_start(index: int) -> date:
    return date(index // 12, index % 12 + 1, 1)


def _period(day: date, shape: _Shape) -> tuple[tuple[object, ...], date, date]:
    """The calendar period (key, first day, day after the last) holding a date."""
    if shape.kind == "days":
        index = (day - _EPOCH).days // shape.count
        first = _EPOCH + timedelta(days=index * shape.count)
        return ("days", index), first, first + timedelta(days=shape.count)
    if shape.kind == "weeks":
        index = (day - _EPOCH_MONDAY).days // (7 * shape.count)
        first = _EPOCH_MONDAY + timedelta(days=index * 7 * shape.count)
        return ("weeks", index), first, first + timedelta(days=7 * shape.count)
    index = _month_index(day) // shape.count
    return ("months", index), _month_start(index * shape.count), _month_start((index + 1) * shape.count)


def _bucket_24x7(bar: MarketBar, shape: _Shape) -> _Bucket:
    start = bar.start_time.astimezone(timezone.utc)
    day = start.date()
    if shape.kind == "intraday":
        midnight = _utc_midnight(day)
        index = (start - midnight) // shape.duration
        bucket_start = midnight + shape.duration * index
        bucket_end = min(bucket_start + shape.duration, midnight + _DAY)
        return _Bucket((day, index), bucket_start, bucket_end, bar.session)
    if shape.kind == "span":
        epoch = _utc_midnight(_EPOCH)
        index = (start - epoch) // shape.duration
        bucket_start = epoch + shape.duration * index
        return _Bucket(("span", index), bucket_start, bucket_start + shape.duration, bar.session)
    key, first, after = _period(day, shape)
    return _Bucket(key, _utc_midnight(first), _utc_midnight(after), bar.session)


@bounded_lru_cache(max_entries=256, ttl_seconds=86_400.0)
def _weekday_holidays(year: int) -> frozenset[date]:
    return frozenset(day for day in regular_holidays(year) if day.weekday() < 5)


def _is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _weekday_holidays(day.year)


_TRADING_EPOCH = date(2000, 1, 3)


def _trading_day_ordinal(day: date) -> int:
    """Trading days from a fixed epoch to `day`, so multi-day equity buckets count sessions, not calendar days."""
    weeks, extra = divmod((day - _TRADING_EPOCH).days, 7)
    ordinal = weeks * 5 + sum(1 for offset in range(extra) if (_TRADING_EPOCH.weekday() + offset) % 7 < 5)
    for year in range(_TRADING_EPOCH.year, day.year + 1):
        ordinal -= sum(1 for holiday in _weekday_holidays(year) if _TRADING_EPOCH <= holiday < day)
    return ordinal


def _trading_days_bucket(day: date, count: int) -> tuple[int, date, date]:
    """The N-trading-day bucket holding a trading day: its index and its first and last trading days."""
    ordinal = _trading_day_ordinal(day)
    index = ordinal // count
    first = day
    for _ in range(ordinal - index * count):
        first -= timedelta(days=1)
        while not _is_trading_day(first):
            first -= timedelta(days=1)
    last = day
    for _ in range((index + 1) * count - 1 - ordinal):
        last += timedelta(days=1)
        while not _is_trading_day(last):
            last += timedelta(days=1)
    return index, first, last


def _local(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """A wall-clock time on a local date, in UTC; ZoneInfo applies that date's DST."""
    return datetime.combine(day, clock, tzinfo=zone).astimezone(timezone.utc)


def _segments(day: date, zone: ZoneInfo, include_extended: bool) -> dict[str, tuple[datetime, datetime]]:
    close = regular_close_time(day)
    extended_close = time(17, 0) if early_close_time(day) is not None else time(20, 0)
    segments = {"regular": (_local(day, _REGULAR_OPEN, zone), _local(day, close, zone))}
    if include_extended:
        segments["extended_pre"] = (_local(day, _PRE_OPEN, zone), _local(day, _REGULAR_OPEN, zone))
        segments["extended_post"] = (_local(day, close, zone), _local(day, extended_close, zone))
    return segments


class _EquityBuckets:
    def __init__(self, shape: _Shape, zone: ZoneInfo, include_extended: bool, base_intraday: bool) -> None:
        self.shape = shape
        self.zone = zone
        self.include_extended = include_extended
        self.base_intraday = base_intraday
        self._periods: dict[tuple[object, ...], tuple[datetime, datetime] | None] = {}

    def bucket(self, bar: MarketBar) -> _Bucket | None:
        start = bar.start_time.astimezone(timezone.utc)
        if self.shape.kind == "intraday":
            return self._intraday(start)
        if self.shape.kind == "span":
            raise ValueError("equity intervals of a day or longer must be whole days, weeks or months")
        if self.base_intraday and us_equity_session(start) != "regular":
            return None
        day = start.astimezone(self.zone).date()
        if self.shape.kind == "days" and self.shape.count > 1:
            if not _is_trading_day(day):
                return None
            index, first_day, last_day = _trading_days_bucket(day, self.shape.count)
            return _Bucket(
                ("trading_days", index),
                _local(first_day, _REGULAR_OPEN, self.zone),
                _local(last_day, regular_close_time(last_day), self.zone),
                "regular",
            )
        key, first, after = _period(day, self.shape)
        bounds = self._period_bounds(key, first, after)
        if bounds is None:
            return None
        return _Bucket(key, bounds[0], bounds[1], "regular")

    def _intraday(self, start: datetime) -> _Bucket | None:
        session = us_equity_session(start)
        if session == "closed" or (session != "regular" and not self.include_extended):
            return None
        day = start.astimezone(self.zone).date()
        segment_start, segment_end = _segments(day, self.zone, self.include_extended)[session]
        anchor = _local(day, _PRE_OPEN if self.include_extended else _REGULAR_OPEN, self.zone)
        index = (start - anchor) // self.shape.duration
        raw_start = anchor + self.shape.duration * index
        bucket_start = max(raw_start, segment_start)
        bucket_end = min(raw_start + self.shape.duration, segment_end)
        return _Bucket((day, session, index), bucket_start, bucket_end, session)

    def _period_bounds(self, key: tuple[object, ...], first: date, after: date) -> tuple[datetime, datetime] | None:
        if key not in self._periods:
            days = [first + timedelta(days=offset) for offset in range((after - first).days)]
            trading = [day for day in days if _is_trading_day(day)]
            self._periods[key] = (
                (_local(trading[0], _REGULAR_OPEN, self.zone), _local(trading[-1], regular_close_time(trading[-1]), self.zone))
                if trading
                else None
            )
        return self._periods[key]


def aggregate_market_bars_clock(
    bars: list[MarketBar],
    *,
    target_interval: str,
    base_interval: str,
    calendar: str,
    exchange_tz: str,
    include_extended: bool,
    history_complete: bool = False,
) -> list[MarketBar]:
    """Aggregate base bars into clock-aligned buckets of ``target_interval`` (see the module doc)."""
    if not bars:
        return []
    shape = _shape(target_interval)
    equity = calendar.strip().upper() in US_EQUITY_CALENDARS
    if equity:
        buckets = _EquityBuckets(
            shape,
            ZoneInfo(exchange_tz or "America/New_York"),
            include_extended,
            base_intraday=interval_duration(base_interval) < _DAY,
        )
        bucket_for = buckets.bucket
    else:
        def bucket_for(bar: MarketBar) -> _Bucket | None:
            return _bucket_24x7(bar, shape)

    groups: dict[tuple[object, ...], tuple[_Bucket, list[MarketBar]]] = {}
    for bar in sorted(bars, key=lambda item: item.start_time):
        bucket = bucket_for(bar)
        if bucket is None:
            continue
        groups.setdefault(bucket.key, (bucket, []))[1].append(bar)
    ordered = sorted(groups.values(), key=lambda item: item[0].start)
    if ordered and not history_complete:
        first_bucket, first_bars = ordered[0]
        if first_bars[0].start_time > first_bucket.start:
            ordered = ordered[1:]

    result: list[MarketBar] = []
    for bucket, group in ordered:
        first = group[0]
        last = group[-1]
        received_at = max(bar.received_at for bar in group)
        result.append(
            MarketBar(
                instrument_id=first.instrument_id,
                interval=target_interval,
                start_time=bucket.start,
                end_time=bucket.end,
                open=first.open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=last.close,
                volume=sum((bar.volume for bar in group), Decimal("0")),
                is_final=bucket.end <= received_at and last.is_final,
                adjustment_mode=first.adjustment_mode,
                session=bucket.session,
                provider=first.provider,
                provider_event_id=(
                    f"aggregate-clock:{base_interval}:{first.provider_event_id or first.start_time.isoformat()}"
                    f":{last.provider_event_id or last.start_time.isoformat()}"
                ),
                provider_sequence=last.provider_sequence,
                ingestion_revision=max(bar.ingestion_revision for bar in group),
                received_at=received_at,
            )
        )
    return result


def clock_aggregated_dataset_fingerprint(
    base_fingerprint: str,
    *,
    target_interval: str,
    base_interval: str,
    include_extended: bool,
) -> str:
    return TradingMarketDataCache.fingerprint(
        {
            "alignment": "clock",
            "base_fingerprint": base_fingerprint,
            "base_interval": base_interval,
            "include_extended": include_extended,
            "target_interval": target_interval,
        }
    )
