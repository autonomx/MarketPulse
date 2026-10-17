from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from app.apps.trading.models import MarketBar


INTERVAL_DELTAS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "1w": timedelta(weeks=1),
    "1mo": timedelta(days=30),
}


def missing_finalized_ranges(
    bars: Iterable[MarketBar],
    *,
    interval: str,
    start_time: datetime,
    end_time: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return contiguous missing finalized-bar ranges in half-open interval form."""
    delta = INTERVAL_DELTAS.get(interval)
    if delta is None:
        raise ValueError(f"unsupported interval: {interval}")
    if end_time <= start_time:
        return []
    finalized = {bar.start_time for bar in bars if bar.is_final and bar.interval == interval}
    missing: list[datetime] = []
    cursor = start_time
    while cursor < end_time:
        if cursor not in finalized:
            missing.append(cursor)
        cursor += delta
    if not missing:
        return []
    ranges: list[tuple[datetime, datetime]] = []
    range_start = missing[0]
    previous = missing[0]
    for current in missing[1:]:
        if current != previous + delta:
            ranges.append((range_start, previous + delta))
            range_start = current
        previous = current
    ranges.append((range_start, previous + delta))
    return ranges


def reconcile_market_bars(
    existing: Iterable[MarketBar],
    incoming: Iterable[MarketBar],
) -> list[MarketBar]:
    """Apply Omnix ingestion revision ordering and return strict chronological bars."""
    by_key: dict[tuple[str, str, datetime], MarketBar] = {
        (bar.instrument_id, bar.interval, bar.start_time): bar for bar in existing
    }
    for bar in incoming:
        key = (bar.instrument_id, bar.interval, bar.start_time)
        current = by_key.get(key)
        if current is None or bar.ingestion_revision >= current.ingestion_revision:
            by_key[key] = bar
    return sorted(by_key.values(), key=lambda bar: (bar.instrument_id, bar.interval, bar.start_time))


def recovery_window(
    last_finalized_start: datetime | None,
    first_stream_start: datetime,
    *,
    interval: str,
) -> tuple[datetime, datetime] | None:
    delta = INTERVAL_DELTAS.get(interval)
    if delta is None:
        raise ValueError(f"unsupported interval: {interval}")
    if last_finalized_start is None:
        return None
    start = last_finalized_start + delta
    if start >= first_stream_start:
        return None
    return start, first_stream_start
