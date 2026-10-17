"""Server indicator registry (TVP-0.2).

Chart indicators computed on the server with the same results as the browser
engine (``web/src/features/trading/indicators``), so alerts, the screener and
scripts can use any indicator a chart shows. Equality is proved by the shared
goldens in ``resources/trading/indicator_goldens``.

This module is separate from ``engine.py``: strategies use ``engine.py``
(``Decimal``) as part of their evidence, and it stays unchanged.
"""

from __future__ import annotations

import importlib
import math
import pkgutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast

NumericClass = Literal["exact", "recursive", "transcendental"]


class _BarLike(Protocol):
    @property
    def start_time(self) -> datetime: ...
    @property
    def open(self) -> object: ...
    @property
    def high(self) -> object: ...
    @property
    def low(self) -> object: ...
    @property
    def close(self) -> object: ...
    @property
    def volume(self) -> object: ...


def _number(value: object) -> float:
    # Mirrors the browser's Number(value) for decimal strings and Decimals; non-finite becomes 0 like `nums()`.
    try:
        number = float(str(value))
    except ValueError:
        return 0.0
    return number if math.isfinite(number) else 0.0


@dataclass(frozen=True)
class BarSeries:
    start_times: tuple[datetime, ...]
    open: tuple[float, ...]
    high: tuple[float, ...]
    low: tuple[float, ...]
    close: tuple[float, ...]
    volume: tuple[float, ...]
    # Each bar's session label (``regular``, ``extended_pre``, ``extended_post``, ``24x7``…) as ``MarketBar.session``
    # carries it; session pivots skip extended-hours bars when the session calendar asks for regular hours only.
    sessions: tuple[str, ...] | None = None

    def __len__(self) -> int:
        return len(self.close)

    @classmethod
    def from_bars(cls, bars: Sequence[_BarLike]) -> BarSeries:
        return cls(
            start_times=tuple(bar.start_time for bar in bars),
            open=tuple(_number(bar.open) for bar in bars),
            high=tuple(_number(bar.high) for bar in bars),
            low=tuple(_number(bar.low) for bar in bars),
            close=tuple(_number(bar.close) for bar in bars),
            volume=tuple(_number(bar.volume) for bar in bars),
            sessions=tuple(str(getattr(bar, "session", "") or "") for bar in bars),
        )


FORMULA_VERSION = "omnix-indicators-v2"


def _whole_number(value: object) -> object:
    # The browser's Number.isInteger(20.0) is true, so a whole float must behave exactly like the int (including in keys).
    return int(value) if isinstance(value, float) and value.is_integer() else value


@dataclass(frozen=True)
class TradingSession:
    """A session calendar, as ``tradingSessions.ts`` defines it (``TradingSessionSpec``).

    A bar's session is its calendar date in ``timezone``; a trading day that starts the evening before (futures and
    forex) sets ``start_minute`` (1080 is 18:00 of the previous day). Intraday pivot periods are anchored at
    ``regular_start_minute`` when set, and ``regular_only`` makes session levels skip extended-hours bars.
    """

    timezone: str = "UTC"
    start_minute: int = 0
    regular_start_minute: int | None = None
    regular_only: bool = False


UTC_SESSION = TradingSession()


def session_for_instrument(
    asset_class: str | None,
    session_calendar: str | None = None,
    exchange_timezone: str | None = None,
    instrument_type: str | None = None,
) -> TradingSession:
    """The session calendar of an instrument, like ``sessionForInstrument`` in the browser: UTC for 24x7 markets,
    the regular session in New York for US equities, the 17:00 ET (forex) or 18:00 ET (commodities) roll."""
    # Asset class decides before the calendar: catalog futures are tagged "24x7" but trade from the 18:00 ET roll.
    if asset_class == "crypto" or asset_class is None:
        return UTC_SESSION
    if asset_class == "forex":
        return TradingSession("America/New_York", 1020)
    if asset_class == "commodity":
        return TradingSession("America/New_York", 1080)
    if session_calendar == "24x7":
        return UTC_SESSION
    if asset_class == "equity" or instrument_type == "equity":
        return TradingSession(exchange_timezone or "America/New_York", 0, 570, True)
    return UTC_SESSION


ParamValue = int | float | str
Params = tuple[tuple[str, ParamValue], ...]


@dataclass(frozen=True)
class IndicatorInputs:
    """Indicator inputs as the chart stores them. Periods may be non-integers; each indicator falls back or rejects them like the browser."""

    period: int | float
    fast_period: int | float | None = None
    slow_period: int | float | None = None
    signal_period: int | float | None = None
    standard_deviations: float | None = None
    anchor_time: str | None = None
    # Instrument id of the second series an indicator reads (Correlation Coefficient). The caller loads that
    # symbol's bars and passes them to ``compute_indicator(..., compare_bars=...)``; this field only records the choice.
    compare_symbol: str | None = None
    # Indicator-specific inputs (the browser's ``params``), by key; missing or invalid values use the defaults.
    # A mapping is accepted and stored as sorted pairs, so inputs stay hashable.
    params: Mapping[str, ParamValue] | Params = ()
    # The chart instrument's session calendar; None means UTC days.
    session: TradingSession | None = None

    def __post_init__(self) -> None:
        for name in ("period", "fast_period", "slow_period", "signal_period"):
            object.__setattr__(self, name, _whole_number(getattr(self, name)))
        pairs = self.params.items() if isinstance(self.params, Mapping) else self.params
        object.__setattr__(self, "params", tuple(sorted(pairs, key=lambda item: item[0])))

    def param(self, key: str) -> ParamValue | None:
        return dict(cast(Params, self.params)).get(key)


@dataclass(frozen=True)
class IndicatorOutputSeries:
    """One plotted line. Points are (bar index, value); a value may be NaN where the browser plots a non-finite number."""

    key: str
    points: tuple[tuple[int, float], ...]

    def latest(self) -> tuple[int, float] | None:
        return self.points[-1] if self.points else None


ComputeFunction = Callable[[BarSeries, IndicatorInputs], list[IndicatorOutputSeries]]
# For indicators that also read a second series; it is None when the caller has none, like a chart with no compare symbol.
CompareComputeFunction = Callable[[BarSeries, IndicatorInputs, BarSeries | None], list[IndicatorOutputSeries]]


@dataclass(frozen=True)
class ServerIndicator:
    id: str
    name: str
    numeric_class: NumericClass
    compute: ComputeFunction
    compare_compute: CompareComputeFunction | None = None
    # Signal indicators (candlestick patterns): outputs with a value only on the bars where the signal appears,
    # so a series may never show one. Alerts take this warm-up (bars) instead of measuring the first value.
    signal_warmup: int | None = None
    # Indicators anchored near the latest bar (swings, a lookback window): the bars they read back from it. Their first
    # value on a long series is late by design, so alerts take this instead of measuring it.
    lookback: Callable[[IndicatorInputs], int] | None = None
    # Indicators over sessions or days (pivots of the previous period, a day of volume): the time they read back. Alerts
    # turn it into bars with their interval.
    lookback_time: Callable[[IndicatorInputs], timedelta] | None = None

    @property
    def uses_compare_series(self) -> bool:
        return self.compare_compute is not None


_REGISTRY: dict[str, ServerIndicator] = {}
_LOADED = False


def _add(indicator: ServerIndicator) -> None:
    if indicator.id in _REGISTRY:
        raise ValueError(f"indicator {indicator.id!r} is registered twice")
    _REGISTRY[indicator.id] = indicator


def register(
    indicator_id: str,
    name: str,
    numeric_class: NumericClass,
    signal_warmup: int | None = None,
    lookback: Callable[[IndicatorInputs], int] | None = None,
    lookback_time: Callable[[IndicatorInputs], timedelta] | None = None,
) -> Callable[[ComputeFunction], ComputeFunction]:
    def decorate(compute: ComputeFunction) -> ComputeFunction:
        _add(
            ServerIndicator(
                indicator_id, name, numeric_class, compute, signal_warmup=signal_warmup, lookback=lookback, lookback_time=lookback_time
            )
        )
        return compute

    return decorate


def register_with_compare_series(
    indicator_id: str, name: str, numeric_class: NumericClass
) -> Callable[[CompareComputeFunction], CompareComputeFunction]:
    """Registers an indicator that reads a second series. Its plain ``compute`` runs it without one."""

    def decorate(compute: CompareComputeFunction) -> CompareComputeFunction:
        def without_compare_series(bars: BarSeries, inputs: IndicatorInputs) -> list[IndicatorOutputSeries]:
            return compute(bars, inputs, None)

        _add(ServerIndicator(indicator_id, name, numeric_class, without_compare_series, compute))
        return compute

    return decorate


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    from . import server_indicators

    for module in pkgutil.iter_modules(server_indicators.__path__):
        if not module.name.startswith("_"):
            importlib.import_module(f"{server_indicators.__name__}.{module.name}")
    _LOADED = True


def server_indicator(indicator_id: str) -> ServerIndicator | None:
    _load()
    return _REGISTRY.get(indicator_id)


def server_indicator_ids() -> list[str]:
    _load()
    return sorted(_REGISTRY)


CompareBarsFetcher = Callable[[str, int], Sequence[_BarLike]]


def load_compare_bars(
    fetch: CompareBarsFetcher,
    compare_symbol: str | None,
    bars: BarSeries,
    max_bars: int = 5_000,
) -> BarSeries | None:
    """Loads an indicator's second series for alerts and the screener, like the chart does.

    ``fetch(instrument_id, limit)`` returns the latest ``limit`` bars of that instrument on the chart's interval. The
    limit covers the time range of ``bars``: its span divided by the bar interval (the smallest gap between bar
    starts), plus one, at most ``max_bars``. No symbol or no bars gives None.
    """
    if not compare_symbol or len(bars) == 0:
        return None
    times = bars.start_times
    gaps = [(later - earlier).total_seconds() for earlier, later in zip(times, times[1:], strict=False)]
    step = min((gap for gap in gaps if gap > 0), default=None)
    span = (times[-1] - times[0]).total_seconds()
    limit = 1 if step is None else min(max_bars, math.ceil(span / step) + 1)
    return BarSeries.from_bars(fetch(compare_symbol, limit))


def compute_indicator(
    indicator_id: str,
    bars: BarSeries,
    inputs: IndicatorInputs,
    compare_bars: BarSeries | None = None,
) -> list[IndicatorOutputSeries]:
    """Computes one indicator. ``compare_bars`` are the bars of ``inputs.compare_symbol`` for indicators that read a
    second series (``ServerIndicator.uses_compare_series``), in any order and over any range: the indicator aligns
    them to ``bars`` by start time, carrying the last close forward. Other indicators ignore them."""
    indicator = server_indicator(indicator_id)
    if indicator is None:
        raise KeyError(f"no server implementation for indicator {indicator_id!r}")
    if indicator.compare_compute is not None:
        return indicator.compare_compute(bars, inputs, compare_bars)
    return indicator.compute(bars, inputs)
