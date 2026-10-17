"""Pure alert condition evaluation on bars (TVP-1.1, TVP-1.2).

An alert is evaluated at one bar ``k``: the last final bar when the alert
fires only on closed bars (``once_per_bar_close``, or ``allow_partial_bars``
false), otherwise the last bar, which may still be forming. Crossing and
channel operators compare bar ``k`` with bar ``k - 1``; moving operators
compare bar ``k`` with bar ``k - bars``. A value that is missing (indicator
warm-up, not enough bars, a zero base) makes its condition false. All
conditions must hold (AND).

Values are compared as ``Decimal``: price fields come from the bars exactly,
indicator values from the server registry (``indicators/registry.py``, the
same numbers the chart plots) through their shortest decimal representation.
External-data indicators (open interest, analyst targets, breadth...) come
from their metric series through an injected loader (``external_series.py``),
so this module stays free of I/O.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol

from .alert_conditions import (
    MOVING_OPERATORS,
    CompareBars,
    AlertConditionSpec,
    ChangePercentSource,
    ChannelTarget,
    IndicatorSource,
    ScriptSource,
    PriceSource,
    SourceTarget,
    TrendlineSource,
    ValueTarget,
    compute_source_indicator,
    condition_sources,
    indicator_output_profile,
)
from .providers.bar_semantics import interval_duration

if TYPE_CHECKING:
    from .indicators.intrabar import IntrabarLoader
    from .indicators.registry import BarSeries, TradingSession

HISTORY_LIMIT_MAX = 1000
_HUNDRED = Decimal("100")

# An external-data indicator's values at bar indexes: (indicator id, output, bars) -> {index: value}.
ExternalValues = Callable[[str, str, Sequence[Any]], dict[int, Any]]


class AlertBar(Protocol):
    @property
    def start_time(self) -> datetime: ...
    @property
    def end_time(self) -> datetime: ...
    @property
    def open(self) -> Any: ...
    @property
    def high(self) -> Any: ...
    @property
    def low(self) -> Any: ...
    @property
    def close(self) -> Any: ...
    @property
    def volume(self) -> Any: ...
    @property
    def is_final(self) -> bool: ...


Pair = tuple[Decimal | None, Decimal | None]  # (previous, current)


@dataclass(frozen=True)
class ConditionObservation:
    position: int
    operator: str
    met: bool
    source: Decimal | None
    source_previous: Decimal | None = None
    target: Decimal | None = None
    target_previous: Decimal | None = None
    upper: Decimal | None = None
    lower: Decimal | None = None
    upper_previous: Decimal | None = None
    lower_previous: Decimal | None = None
    base: Decimal | None = None
    # A script's alert() or alertcondition message on this bar (TVP-11.4).
    message: str | None = None

    def payload(self) -> dict[str, Any]:
        values = {
            "source": self.source,
            "source_previous": self.source_previous,
            "target": self.target,
            "target_previous": self.target_previous,
            "upper": self.upper,
            "lower": self.lower,
            "upper_previous": self.upper_previous,
            "lower_previous": self.lower_previous,
            "base": self.base,
        }
        return {
            "position": self.position,
            "operator": self.operator,
            "met": self.met,
            **{name: str(value) for name, value in values.items() if value is not None},
            **({"message": self.message} if self.message is not None else {}),
        }


@dataclass(frozen=True)
class AlertConditionOutcome:
    """The result of evaluating every condition of one alert at one bar."""

    met: bool
    bar_start: datetime
    bar_end: datetime
    bar_is_final: bool
    close: Decimal
    volume: Decimal
    observations: tuple[ConditionObservation, ...] = field(default_factory=tuple)
    bar_index: int | None = None
    # The bar's other prices, for message placeholders (TVP-1.5); absent for a legacy quote evaluation.
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    # False when the observation had no volume (a legacy quote): ``volume`` is then a stand-in 0.
    volume_known: bool = True

    @property
    def primary_value(self) -> Decimal | None:
        return self.observations[0].source if self.observations else None

    def observation_payload(self) -> list[dict[str, Any]]:
        return [observation.payload() for observation in self.observations]


# --- Operators -----------------------------------------------------------------


def _inside(value: Decimal, lower: Decimal, upper: Decimal) -> bool:
    low, high = (lower, upper) if lower <= upper else (upper, lower)
    return low <= value <= high


def operator_met(
    operator: str,
    source: Pair,
    *,
    target: Pair = (None, None),
    upper: Pair = (None, None),
    lower: Pair = (None, None),
    base: Decimal | None = None,
    amount: Decimal | None = None,
) -> bool:
    """One operator on (previous, current) values; any missing value is false."""
    previous, current = source
    if current is None:
        return False
    if operator in MOVING_OPERATORS:
        if base is None or amount is None:
            return False
        if operator == "moving_up":
            return current - base >= amount
        if operator == "moving_down":
            return base - current >= amount
        if base == 0:
            return False
        change = (current - base) / abs(base) * _HUNDRED
        return change >= amount if operator == "moving_up_percent" else -change >= amount
    if operator in {"greater_than", "less_than"}:
        if target[1] is None:
            return False
        return current > target[1] if operator == "greater_than" else current < target[1]
    if operator in {"crossing", "crossing_up", "crossing_down"}:
        previous_target, current_target = target
        if previous is None or previous_target is None or current_target is None:
            return False
        up = previous < previous_target and current >= current_target
        down = previous > previous_target and current <= current_target
        if operator == "crossing_up":
            return up
        if operator == "crossing_down":
            return down
        return up or down
    # Channel operators.
    if upper[1] is None or lower[1] is None:
        return False
    inside_now = _inside(current, lower[1], upper[1])
    if operator == "inside_channel":
        return inside_now
    if operator == "outside_channel":
        return not inside_now
    if previous is None or upper[0] is None or lower[0] is None:
        return False
    inside_before = _inside(previous, lower[0], upper[0])
    if operator == "entering_channel":
        return inside_now and not inside_before
    return inside_before and not inside_now  # exiting_channel


# --- Values --------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(repr(value))
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


class _BarValues:
    """Source values at bar indexes of one bar list, with per-source caches."""

    def __init__(
        self,
        bars: Sequence[AlertBar],
        external: ExternalValues | None = None,
        session: TradingSession | None = None,
        compare: CompareBars | None = None,
        script_context: Any = None,
        intrabar: IntrabarLoader | None = None,
    ) -> None:
        self.bars = bars
        self.external = external
        self.session = session
        self.compare = compare
        # Lower-timeframe bars for Volume Delta and CVD (indicator_context.intrabar_loader); without it they have no value.
        self.intrabar = intrabar
        # A script alert's instrument, interval and market data (alerts_scripts.ScriptAlertContext).
        self.script_context = script_context
        self._series: BarSeries | None = None
        self._indicator_cache: dict[tuple[Any, ...], dict[int, float]] = {}
        self._script_cache: dict[str, Any] = {}

    def series(self) -> BarSeries:
        from .indicators.registry import BarSeries

        if self._series is None:
            self._series = BarSeries.from_bars(self.bars)
        return self._series

    def value(self, source: Any, index: int) -> Decimal | None:
        if index < 0 or index >= len(self.bars):
            return None
        if isinstance(source, PriceSource):
            return self._price(source.field, index)
        if isinstance(source, ChangePercentSource):
            base_index = index - source.lookback_bars
            if base_index < 0:
                return None
            previous = _decimal(self.bars[base_index].close)
            current = _decimal(self.bars[index].close)
            if previous is None or current is None or previous == 0:
                return None
            return (current / previous - Decimal("1")) * _HUNDRED
        if isinstance(source, TrendlineSource):
            return _trendline_value(source, self.bars[index].end_time)
        if isinstance(source, IndicatorSource):
            return self._indicator(source, index)
        if isinstance(source, ScriptSource):
            value = self._script(source).values.get(index)
            return None if value is None else _decimal(value)
        return None

    def _script(self, source: ScriptSource) -> Any:
        """The script run once per source and bar list, in a worker process (``alerts_scripts.py``)."""
        key = source.model_dump_json()
        series = self._script_cache.get(key)
        if series is None:
            from .alerts_scripts import script_alert_series

            series = script_alert_series(source, self.bars, context=self.script_context)
            self._script_cache[key] = series
        return series

    def script_message(self, source: Any, index: int) -> str | None:
        return self._script(source).messages.get(index) if isinstance(source, ScriptSource) else None

    def _price(self, name: str, index: int) -> Decimal | None:
        bar = self.bars[index]
        if name in {"close", "open", "high", "low", "volume"}:
            return _decimal(getattr(bar, name))
        high, low, close = _decimal(bar.high), _decimal(bar.low), _decimal(bar.close)
        if high is None or low is None or close is None:
            return None
        if name == "hl2":
            return (high + low) / 2
        if name == "hlc3":
            return (high + low + close) / 3
        opening = _decimal(bar.open)
        return None if opening is None else (opening + high + low + close) / 4

    def _indicator(self, source: IndicatorSource, index: int) -> Decimal | None:
        anchor_bars_ago = source.inputs.anchor_bars_ago
        anchor_time: str | None = None
        if anchor_bars_ago is not None:
            anchor_index = max(0, index - anchor_bars_ago)
            anchor_time = self.bars[anchor_index].start_time.astimezone(timezone.utc).isoformat()
        cache_key = (source.model_dump_json(), anchor_time)
        points = self._indicator_cache.get(cache_key)
        if points is None:
            points = self._compute_indicator(source, anchor_time)
            self._indicator_cache[cache_key] = points
        value = points.get(index)
        return None if value is None else _decimal(value)

    def _compute_indicator(self, source: IndicatorSource, anchor_time: str | None) -> dict[int, Any]:
        from .indicators.external import external_indicator
        from .indicators.intrabar import intrabar_values, is_intrabar_indicator

        if is_intrabar_indicator(source.indicator_id):
            interval = str(getattr(self.bars[0], "interval", "")) if self.bars else ""
            return intrabar_values(source.indicator_id, self.bars, interval, source.inputs.params or {}, self.session, self.intrabar)
        if external_indicator(source.indicator_id) is not None:
            # No loader (a pushed quote, a test): no data, so the condition is false.
            return self.external(source.indicator_id, source.output, self.bars) if self.external else {}
        try:
            outputs = compute_source_indicator(
                source.indicator_id, self.series(), source.inputs, anchor_time, session=self.session, compare=self.compare
            )
        except Exception:  # an indicator that cannot compute these bars has no value: the condition is false
            return {}
        chosen = next((output for output in outputs if output.key == source.output), None)
        if chosen is None and anchor_time is not None:
            # A moving anchor changes the key (vwap:<anchor>); the output keeps its position.
            keys = [key for key, _ in indicator_output_profile(source)]
            if source.output in keys and len(outputs) == len(keys):
                chosen = outputs[keys.index(source.output)]
        if chosen is None:
            return {}
        return {index: value for index, value in chosen.points}


def _trendline_value(source: TrendlineSource, at: datetime) -> Decimal | None:
    first, second = source.points
    first_time = first.time.astimezone(timezone.utc)
    second_time = second.time.astimezone(timezone.utc)
    duration = Decimal(str((second_time - first_time).total_seconds()))
    if duration == 0:
        return None
    elapsed = Decimal(str((at.astimezone(timezone.utc) - first_time).total_seconds()))
    return first.price + (second.price - first.price) * elapsed / duration


def _target_pair(values: _BarValues, bound: Any, index: int) -> Pair:
    if isinstance(bound, ValueTarget):
        return (bound.value, bound.value)
    if isinstance(bound, SourceTarget):
        return (values.value(bound.source, index - 1), values.value(bound.source, index))
    return (None, None)


def _evaluate_condition(values: _BarValues, position: int, condition: AlertConditionSpec, index: int) -> ConditionObservation:
    source = (values.value(condition.source, index - 1), values.value(condition.source, index))
    target: Pair = (None, None)
    upper: Pair = (None, None)
    lower: Pair = (None, None)
    base: Decimal | None = None
    if condition.operator in MOVING_OPERATORS:
        base = values.value(condition.source, index - (condition.bars or 1))
    elif isinstance(condition.target, ChannelTarget):
        upper = _target_pair(values, condition.target.upper, index)
        lower = _target_pair(values, condition.target.lower, index)
    else:
        target = _target_pair(values, condition.target, index)
    met = operator_met(
        condition.operator, source, target=target, upper=upper, lower=lower, base=base, amount=condition.amount
    )
    return ConditionObservation(
        message=values.script_message(condition.source, index) if met else None,
        position=position,
        operator=condition.operator,
        met=met,
        source=source[1],
        source_previous=source[0],
        target=target[1],
        target_previous=target[0],
        upper=upper[1],
        lower=lower[1],
        upper_previous=upper[0],
        lower_previous=lower[0],
        base=base,
    )


def evaluation_index(bars: Sequence[AlertBar], *, final_only: bool) -> int | None:
    """The bar to evaluate: the last bar, or the last final bar when only closed bars count."""
    if not bars:
        return None
    if not final_only:
        return len(bars) - 1
    for index in range(len(bars) - 1, -1, -1):
        if bars[index].is_final:
            return index
    return None


def evaluate_conditions(
    conditions: Sequence[AlertConditionSpec],
    bars: Sequence[AlertBar],
    *,
    final_only: bool,
    external: ExternalValues | None = None,
    session: TradingSession | None = None,
    compare: CompareBars | None = None,
    script_context: Any = None,
    intrabar: IntrabarLoader | None = None,
) -> AlertConditionOutcome | None:
    """Evaluate every condition at the evaluation bar; ``None`` when there is no bar to evaluate.

    ``external`` supplies external-data indicator values (``external_series.ExternalSeries``); ``session`` is the
    instrument's session calendar and ``compare`` loads compare symbols' bars (``indicator_context``), as the chart has
    them. Without them indicators run on UTC days with no second series."""
    index = evaluation_index(bars, final_only=final_only)
    if index is None:
        return None
    # Indicators see bars up to the evaluated bar only, so a forming bar after it cannot leak in.
    values = _BarValues(bars[: index + 1], external, session, compare, script_context, intrabar)
    observations = tuple(_evaluate_condition(values, position, condition, index) for position, condition in enumerate(conditions))
    bar = bars[index]
    return AlertConditionOutcome(
        met=bool(observations) and all(observation.met for observation in observations),
        bar_start=bar.start_time,
        bar_end=bar.end_time,
        bar_is_final=bool(bar.is_final),
        close=_decimal(bar.close) or Decimal("0"),
        volume=_decimal(bar.volume) or Decimal("0"),
        observations=observations,
        bar_index=index,
        open=_decimal(bar.open),
        high=_decimal(bar.high),
        low=_decimal(bar.low),
    )


# --- History -------------------------------------------------------------------


# Bars a script alert reads before the evaluated bar: scripts declare no warm-up, so a fixed, generous history.
SCRIPT_ALERT_LOOKBACK = 300


# A time look-back in bars is capped so the conditions still fit one history fetch (previous bar and forming bar
# included): an indicator over sessions then evaluates once the fetched bars cover what it needs.
_TIME_LOOKBACK_CAP = HISTORY_LIMIT_MAX - 4


def _bars_back(duration: timedelta, interval: str | None) -> int:
    """Bars covering ``duration`` on ``interval``, capped; the cap when the interval has no fixed length (ticks)."""
    try:
        step = interval_duration(interval) if interval else None
    except ValueError:
        step = None
    if step is None or step.total_seconds() <= 0:
        return _TIME_LOOKBACK_CAP
    return min(_TIME_LOOKBACK_CAP, math.ceil(duration / step) + 1)


def _source_lookback(source: Any, interval: str | None = None) -> int:
    from .indicators.external import external_indicator
    from .indicators.intrabar import is_intrabar_indicator, lookback_time as intrabar_lookback_time
    from .indicators.registry import server_indicator

    if isinstance(source, ChangePercentSource):
        return source.lookback_bars
    if isinstance(source, ScriptSource):
        return SCRIPT_ALERT_LOOKBACK
    if not isinstance(source, IndicatorSource):
        return 0
    if external_indicator(source.indicator_id) is not None:
        return 1  # a data series, with a value from its first point
    if is_intrabar_indicator(source.indicator_id):
        # A bar's own lower bars; CVD adds up from its anchor period's start.
        return max(1, _bars_back(intrabar_lookback_time(source.indicator_id, source.inputs.params or {}), interval))
    inputs = source.inputs
    periods = [value for value in (inputs.period, inputs.fast_period, inputs.slow_period, inputs.signal_period) if value]
    required = math.ceil(max(periods, default=1))
    if source.indicator_id == "macd":
        required = max(required, math.ceil((inputs.slow_period or 26) + (inputs.signal_period or 9)))
    elif source.indicator_id == "stochastic-rsi":
        required = max(
            required,
            math.ceil(2 * (inputs.period or 14) + (inputs.fast_period or 3) + (inputs.signal_period or 3)),
        )
    if inputs.anchor_bars_ago is not None:
        required = max(required, inputs.anchor_bars_ago + 1)
    if inputs.source is not None:
        # Indicator on indicator: the source's warm-up comes first, then the indicator's own.
        reference = inputs.source
        inner = IndicatorSource(kind="indicator", indicator_id=reference.indicator_id, inputs=reference.inputs, output=reference.output)
        required += _source_lookback(inner, interval)
    indicator = server_indicator(source.indicator_id)
    if indicator is not None and indicator.lookback is not None:
        # Anchored near the latest bar (TVP-6.2's swings and windows): its own look-back, not a measured first value.
        return max(required, indicator.lookback(inputs.registry_inputs()))
    if indicator is not None and indicator.lookback_time is not None:
        # Over sessions or days: the time it reads back, in bars of the alert's interval.
        return max(required, _bars_back(indicator.lookback_time(inputs.registry_inputs()), interval))
    if indicator is not None and indicator.signal_warmup is not None:
        # A signal output's first value on the synthetic series is when it happens to appear, not its warm-up.
        return max(required, indicator.signal_warmup)
    try:
        # The first bar this output has a value on, measured on a synthetic series,
        # covers indicators whose warm-up is not a simple function of the period.
        first_valid = dict(indicator_output_profile(source)).get(source.output)
    except Exception:  # validated at write time; a failure here only loses the measured warm-up
        first_valid = None
    if first_valid is not None:
        required = max(required, first_valid + 1)
    return required


def required_bars(conditions: Sequence[AlertConditionSpec], interval: str | None = None) -> int:
    """Bars the conditions need before the evaluated bar has a value and a previous value, on ``interval``."""
    required = 1
    for condition in conditions:
        lookback = max(_source_lookback(source, interval) for source in condition_sources(condition))
        if condition.operator in MOVING_OPERATORS:
            lookback += condition.bars or 1
        else:
            lookback += 1  # the previous bar
        required = max(required, lookback)
    return required


MAX_INDICATOR_PERIOD = 500


def validate_conditions_can_fire(conditions: Sequence[AlertConditionSpec], interval: str | None = None) -> None:
    """Reject conditions the monitor could never evaluate (write-time check).

    Periods are capped at 500 like the legacy parameters; every indicator
    output must produce a value; and the bars the conditions need, plus the
    previous bar and room for a forming bar, must fit in one history fetch.
    """
    from .indicators.external import external_indicator
    from .indicators.intrabar import is_intrabar_indicator
    from .indicators.registry import server_indicator

    for condition in conditions:
        for source in condition_sources(condition):
            if not isinstance(source, IndicatorSource) or external_indicator(source.indicator_id) is not None:
                continue
            if is_intrabar_indicator(source.indicator_id):
                continue  # no periods; a value wherever the lower bars reach
            inputs = source.inputs
            for name in ("period", "fast_period", "slow_period", "signal_period"):
                value = getattr(inputs, name)
                if value is not None and value > MAX_INDICATOR_PERIOD:
                    raise ValueError(f"indicator {name} must be at most {MAX_INDICATOR_PERIOD}")
            indicator = server_indicator(source.indicator_id)
            declared = indicator is not None and (
                indicator.signal_warmup is not None or indicator.lookback is not None or indicator.lookback_time is not None
            )
            # A signal (a candlestick pattern) has a value only where it appears, which a series may never show; an
            # indicator with its own look-back (a day of bars, the latest swings) may start after the synthetic series
            # ends; an anchor time decides where a line starts, and the synthetic series is years before any real anchor.
            if not declared and inputs.anchor_time is None and dict(indicator_output_profile(source)).get(source.output) is None:
                raise ValueError(f"indicator output {source.output!r} never has a value with these inputs")
    required = required_bars(conditions, interval)
    if required + 2 > HISTORY_LIMIT_MAX:
        raise ValueError(
            f"these conditions need {required} bars of history; alerts can use at most {HISTORY_LIMIT_MAX - 2}"
        )


def history_limit(required: int) -> int:
    """Bars to fetch: the requirement plus a warm-up so recursive indicators converge.

    ``min(1000, max(required + 2, 3 * required + 50))``: recursive indicators
    (EMA, RSI, ATR, MACD...) depend on every earlier bar, so three times the
    requirement plus 50 bars keeps their seed's influence negligible, and the
    extra two bars leave room for a forming bar after the last final bar.
    """
    return min(HISTORY_LIMIT_MAX, max(required + 2, 3 * required + 50))


__all__ = [
    "HISTORY_LIMIT_MAX",
    "AlertBar",
    "AlertConditionOutcome",
    "ConditionObservation",
    "evaluate_conditions",
    "evaluation_index",
    "history_limit",
    "operator_met",
    "required_bars",
    "validate_conditions_can_fire",
]
