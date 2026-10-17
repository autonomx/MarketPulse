"""Volume Delta and Cumulative Volume Delta on the server (TVP-6.4), for alerts and the screener.

The server side of the chart's ``intrabarIndicators.ts``, step for step, so an alert or a screen reads the value the
chart draws: each chart bar's lower-timeframe bars (``intrabar.py``'s loader) count their volume as buying when they
close above their open (or, unchanged, above the previous close) and as selling below; unchanged bars keep the previous
direction, starting as buying. Volume Delta is a bar's buying minus selling volume; Cumulative Volume Delta adds the
deltas up within each anchor period (session day, week or month).

The lower bars reach back ``MAX_INTRABAR_BARS`` from the last closed bar, as on the chart; on auto above 1h the latest
bars read TradingView's finer interval and older ones the coarse one. Outputs are keyed as the chart keys them
(``tv-volume-delta:delta``, ``tv-cumulative-volume-delta:cvd``); the value is the candle's close.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from ..intrabar import MAX_INTRABAR_BARS
from ..providers.bar_semantics import interval_duration
from .registry import BarSeries, TradingSession
from .server_indicators._sessions import epoch_ms, session_clock, session_periods

INTRABAR_OUTPUTS: Mapping[str, str] = {
    "tv-volume-delta": "tv-volume-delta:delta",
    "tv-cumulative-volume-delta": "tv-cumulative-volume-delta:cvd",
}
# Lower intervals offered besides auto (the chart's INTRABAR_LOWER_INTERVALS).
LOWER_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
_ANCHORS = ("D", "W", "M")
_MINUTE = timedelta(minutes=1)
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)
_WEEK = timedelta(days=7)

# The lower bars of ``lower_interval`` starting in [start, end) (``intrabar.intrabar_bars``).
IntrabarLoader = Callable[[str, datetime, datetime], Sequence[Any]]


def is_intrabar_indicator(indicator_id: str) -> bool:
    return indicator_id in INTRABAR_OUTPUTS


def intrabar_output_keys(indicator_id: str) -> tuple[str, ...]:
    output = INTRABAR_OUTPUTS.get(indicator_id)
    return (output,) if output else ()


def _duration(interval: str) -> timedelta | None:
    try:
        duration = interval_duration(interval)
    except ValueError:
        return None
    return duration if duration.total_seconds() > 0 else None


def auto_intrabar_interval(interval: str) -> str | None:
    """The chart's ``autoIntrabarInterval``: 1m up to 1h, 5m below a day, 1h below a week, else 1d; none at 1m."""
    duration = _duration(interval)
    if duration is None or duration <= _MINUTE:
        return None
    if duration <= _HOUR:
        return "1m"
    if duration < _DAY:
        return "5m"
    if duration < _WEEK:
        return "1h"
    return "1d"


def trading_view_intrabar_interval(interval: str) -> str | None:
    """TradingView's lower interval on auto (``tradingViewIntrabarInterval``): 1m intraday, 5m daily, 1h above."""
    duration = _duration(interval)
    if duration is None or duration <= _MINUTE:
        return None
    if duration < _DAY:
        return "1m"
    if duration < _WEEK:
        return "5m"
    return "1h"


def fits_inside(interval: str, lower_interval: str) -> bool:
    """Whether ``lower_interval`` is shorter than ``interval`` and divides it (``isIntrabarInterval``)."""
    chart = _duration(interval)
    lower = _duration(lower_interval)
    return chart is not None and lower is not None and lower < chart and chart % lower == timedelta(0)


def chosen_lower_interval(params: Mapping[str, Any], interval: str) -> str | None:
    """The lower interval an instance reads: its own when it fits, else auto; None reads the chart's bars."""
    chosen = params.get("lowerInterval")
    if isinstance(chosen, str) and chosen != "auto" and fits_inside(interval, chosen):
        return chosen
    auto = auto_intrabar_interval(interval)
    return auto if auto and fits_inside(interval, auto) else None


def validate_intrabar_params(params: Mapping[str, Any]) -> None:
    lower = params.get("lowerInterval")
    if lower is not None and lower != "auto" and lower not in LOWER_INTERVALS:
        raise ValueError(f"lowerInterval must be auto or one of {', '.join(LOWER_INTERVALS)}")
    anchor = params.get("anchor")
    if anchor is not None and anchor not in _ANCHORS:
        raise ValueError("anchor must be D, W or M")


def lookback_time(indicator_id: str, params: Mapping[str, Any]) -> timedelta:
    """How far back an alert reads: a bar for Volume Delta, the anchor period for Cumulative Volume Delta."""
    if indicator_id != "tv-cumulative-volume-delta":
        return timedelta(0)
    return {"W": timedelta(days=7), "M": timedelta(days=31)}.get(str(params.get("anchor")), timedelta(days=1))


def _ms(moment: datetime) -> int:
    return epoch_ms(moment)


def _group(chart_bars: Sequence[Any], lower_bars: Sequence[Any], interval: str) -> dict[int, list[Any]]:
    """Each chart bar's lower bars, by the chart bar's start (``groupIntrabars``)."""
    groups: dict[int, list[Any]] = {}
    duration = _duration(interval)
    if duration is None or not chart_bars:
        return groups
    step = duration // timedelta(milliseconds=1)
    starts = [_ms(bar.start_time) for bar in chart_bars]
    index = 0
    for lower in sorted(lower_bars, key=lambda bar: _ms(bar.start_time)):
        time = _ms(lower.start_time)
        while index + 1 < len(starts) and starts[index + 1] <= time:
            index += 1
        start = starts[index]
        if time < start:
            continue
        end_time = getattr(chart_bars[index], "end_time", None)
        end = _ms(end_time) if end_time is not None else start + step
        if time >= end:
            continue
        groups.setdefault(start, []).append(lower)
    return groups


def bar_deltas(chart_bars: Sequence[Any], lower_bars: Sequence[Any], interval: str) -> dict[int, tuple[float, float, float]]:
    """Each chart bar's (delta, running max, running min), by its start (``intrabarDeltas``)."""
    deltas: dict[int, tuple[float, float, float]] = {}
    previous_close: float | None = None
    direction = 1  # TradingView starts as buying
    for start, group in _group(chart_bars, lower_bars, interval).items():
        running = 0.0
        high = 0.0
        low = 0.0
        for bar in group:
            open_ = float(bar.open)
            close = float(bar.close)
            volume = float(bar.volume or 0)
            if close > open_:
                direction = 1
            elif close < open_:
                direction = -1
            elif previous_close is not None and close > previous_close:
                direction = 1
            elif previous_close is not None and close < previous_close:
                direction = -1
            running += direction * volume
            high = max(high, running)
            low = min(low, running)
            previous_close = close
        deltas[start] = (running, high, low)
    return deltas


def _lower_bars(
    bars: Sequence[Any], lower_interval: str, load: IntrabarLoader, with_forming: bool = True,
) -> tuple[list[Any], int] | None:
    """The lower bars for the chart bars and the earliest chart bar start they fully cover (``lowerBarsFor``, live)."""
    lower = _duration(lower_interval)
    if lower is None or not bars:
        return None
    last = bars[-1]
    span = lower * MAX_INTRABAR_BARS
    closed_end = last.start_time if not last.is_final else last.end_time
    loaded = list(load(lower_interval, closed_end - span, closed_end))
    if not last.is_final and with_forming:
        loaded += list(load(lower_interval, last.start_time, last.end_time))
    return loaded, _ms(closed_end - span)


def intrabar_values(
    indicator_id: str,
    bars: Sequence[Any],
    interval: str,
    params: Mapping[str, Any],
    session: TradingSession | None,
    load: IntrabarLoader | None,
) -> dict[int, float]:
    """The output's value by bar index (``calculateIntrabarIndicatorOutputs``); empty without a loader or lower bars."""
    if not is_intrabar_indicator(indicator_id) or not bars:
        return {}
    lower_interval = chosen_lower_interval(params, interval)
    auto = not isinstance(params.get("lowerInterval"), str) or params.get("lowerInterval") == "auto"
    fine_interval = trading_view_intrabar_interval(interval) if auto else None
    mixed = fine_interval is not None and lower_interval is not None and fine_interval != lower_interval and fits_inside(interval, fine_interval)
    if lower_interval is None:
        loaded: tuple[list[Any], int] | None = (list(bars), -(2**62))  # a 1m chart: each bar is its own intrabar
    elif load is None:
        return {}
    else:
        try:
            loaded = _lower_bars(bars, lower_interval, load, with_forming=not mixed)
        except Exception:  # no lower bars (provider error, unknown interval): no value, as on the chart
            loaded = None
    if loaded is None:
        return {}
    lower_bars, covered_from = loaded
    deltas = bar_deltas([bar for bar in bars if _ms(bar.start_time) >= covered_from], lower_bars, interval)
    if mixed and load is not None and fine_interval is not None:
        try:
            fine = _lower_bars(bars, fine_interval, load)
        except Exception:
            fine = None
        if fine is not None and fine[0]:
            # The fine bars cover from where the provider's history of them starts, if later than asked.
            fine_from = max(fine[1], min(_ms(bar.start_time) for bar in fine[0]))
            deltas.update(bar_deltas([bar for bar in bars if _ms(bar.start_time) >= fine_from], fine[0], interval))
    starts = [_ms(bar.start_time) for bar in bars]
    if indicator_id == "tv-volume-delta":
        return {index: deltas[start][0] for index, start in enumerate(starts) if start in deltas}
    anchor = str(params.get("anchor")) if params.get("anchor") in ("W", "M") else "D"
    series = BarSeries(
        start_times=tuple(bar.start_time for bar in bars), open=(0.0,) * len(bars), high=(0.0,) * len(bars),
        low=(0.0,) * len(bars), close=(0.0,) * len(bars), volume=(0.0,) * len(bars),
        sessions=tuple(getattr(bar, "session", "") or "" for bar in bars),
    )
    keys, _ = session_periods(session_clock(series, session), anchor, session)
    values: dict[int, float] = {}
    total = 0.0
    for index, start in enumerate(starts):
        if index == 0 or keys[index] != keys[index - 1]:
            total = 0.0
        delta = deltas.get(start)
        if delta is None:
            continue
        total += delta[0]
        values[index] = total
    return values


__all__ = [
    "INTRABAR_OUTPUTS", "IntrabarLoader", "LOWER_INTERVALS", "auto_intrabar_interval", "bar_deltas", "chosen_lower_interval",
    "fits_inside", "intrabar_output_keys", "intrabar_values", "is_intrabar_indicator", "lookback_time",
    "trading_view_intrabar_interval", "validate_intrabar_params",
]
