"""Intrabar data loader (TVP-0.6): lower-timeframe bars inside a range of chart bars.

Volume delta (TVP-6.4) and sub-bar replay (TVP-8.1) read the bars of a lower
interval (1m bars inside a 1h bar, 1s inside 1m). Providers serve the latest N
bars of an interval, so the loader asks for enough recent lower bars to reach
back to the range start, capped by the provider limit, and keeps those inside
the range. A range older than that reach is answered with what is available and
``complete`` false, so callers can say where the intrabar data stops.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel

from .models import MarketBar
from .providers.bar_semantics import interval_duration

# The most lower-timeframe bars one request reads (the provider limit of /bars).
MAX_INTRABAR_BARS = 5_000


class IntrabarResponse(BaseModel):
    instrument_id: str
    interval: str
    lower_interval: str
    start: datetime
    end: datetime
    bars: list[MarketBar]
    # Whether the bars reach back to ``start`` (or the provider has no older history).
    complete: bool
    # The start of the earliest lower bar the provider returned; None when it returned none.
    available_from: datetime | None


class _BarsSource(Protocol):
    def bars(self, instrument_id: str, interval: str, limit: int = 500, binding_id: str | None = None, *args, **kwargs): ...


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def intrabar_bars(
    service: _BarsSource,
    *,
    instrument_id: str,
    interval: str,
    lower_interval: str,
    start: datetime,
    end: datetime,
    now: datetime,
    binding_id: str | None = None,
) -> IntrabarResponse:
    """The ``lower_interval`` bars starting in ``[start, end)``; ValueError for an invalid request."""
    lower = interval_duration(lower_interval)
    chart = interval_duration(interval)
    if lower.total_seconds() <= 0 or lower >= chart:
        raise ValueError("the lower interval must be shorter than the chart interval")
    start, end, now = _utc(start), _utc(end), _utc(now)
    if end <= start:
        raise ValueError("end must be after start")
    if (end - start) / lower > MAX_INTRABAR_BARS:
        raise ValueError(f"the range holds more than {MAX_INTRABAR_BARS} {lower_interval} bars")
    # Enough of the latest lower bars to reach back to the range start, within the provider limit.
    needed = math.ceil(max(0.0, (now - start) / lower)) + 1
    limit = max(1, min(MAX_INTRABAR_BARS, needed))
    response = service.bars(instrument_id, lower_interval, limit, binding_id, alignment="clock")
    fetched = sorted(response.bars, key=lambda bar: bar.start_time)
    available_from = _utc(fetched[0].start_time) if fetched else None
    inside = [bar for bar in fetched if start <= _utc(bar.start_time) < end]
    # Short of the range start, the bars are complete only when the provider says it has no older ones: providers that
    # cap their history (Yahoo keeps about a week of 1m bars) also return fewer bars than asked.
    history_complete = bool(getattr(response.provenance, "history_complete", False))
    complete = available_from is not None and (available_from <= start or history_complete)
    return IntrabarResponse(
        instrument_id=instrument_id,
        interval=interval,
        lower_interval=lower_interval,
        start=start,
        end=end,
        bars=inside,
        complete=complete,
        available_from=available_from,
    )
