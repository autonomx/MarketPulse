"""Alert condition model (TVP-1.2) and alert frequencies (TVP-1.1).

An alert holds one to five conditions, combined with AND. Each condition has a
``source`` (price, percent change, an indicator output from the server
registry, or a trendline), an ``operator`` and, depending on the operator, a
``target`` (a value, a second source, or a channel of two bounds) or a moving
``amount`` within ``bars``.

Alerts created before TVP-1.2 describe their single condition with
``condition_type``, ``threshold`` and ``parameters``. ``legacy_conditions``
turns those fields into the same model with the meaning they have always had,
so evaluation reads one model.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.caching.bounded_cache import bounded_lru_cache

if TYPE_CHECKING:
    from .indicators.registry import BarSeries, IndicatorInputs, IndicatorOutputSeries, TradingSession

MAX_ALERT_CONDITIONS = 5

AlertFrequency = Literal["once", "every_time", "once_per_bar", "once_per_bar_close", "once_per_minute"]
ALERT_FREQUENCIES: tuple[str, ...] = ("once", "every_time", "once_per_bar", "once_per_bar_close", "once_per_minute")

AlertOperator = Literal[
    "crossing",
    "crossing_up",
    "crossing_down",
    "greater_than",
    "less_than",
    "entering_channel",
    "exiting_channel",
    "inside_channel",
    "outside_channel",
    "moving_up",
    "moving_down",
    "moving_up_percent",
    "moving_down_percent",
]
CHANNEL_OPERATORS = frozenset({"entering_channel", "exiting_channel", "inside_channel", "outside_channel"})
MOVING_OPERATORS = frozenset({"moving_up", "moving_down", "moving_up_percent", "moving_down_percent"})

PriceField = Literal["close", "open", "high", "low", "hl2", "hlc3", "ohlc4", "volume"]


class TrendlineAlertPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    price: Decimal


class PriceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["price"]
    field: PriceField = "close"


class ChangePercentSource(BaseModel):
    """Close against the close ``lookback_bars`` earlier, in percent."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["change_percent"]
    lookback_bars: int = Field(default=1, ge=1, le=500)


class IndicatorSourceInputs(BaseModel):
    """Indicator inputs as the chart stores them.

    ``anchor_bars_ago`` anchors an anchored indicator (VWAP) that many bars
    before the bar being evaluated, so the anchor moves with the bar.
    """

    model_config = ConfigDict(extra="forbid")

    period: int | float = Field(default=14, gt=0, le=1000)
    fast_period: int | float | None = Field(default=None, gt=0, le=1000)
    slow_period: int | float | None = Field(default=None, gt=0, le=1000)
    signal_period: int | float | None = Field(default=None, gt=0, le=1000)
    standard_deviations: float | None = Field(default=None, gt=0, le=100)
    anchor_time: str | None = Field(default=None, max_length=64)
    anchor_bars_ago: int | None = Field(default=None, ge=0, le=999)
    # Indicator on indicator (TVP-6.5): read this output of another indicator instead of the close.
    source: IndicatorOutputRef | None = None
    # The indicator's own inputs, as the chart's ``params`` (a mode, a second length...); the registry falls back to the
    # default for a missing or invalid one, as the chart does.
    params: dict[str, float | int | str] = Field(default_factory=dict, max_length=20)
    # The second symbol an indicator reads (Correlation Coefficient), loaded on the alert's interval.
    compare_symbol: str | None = Field(default=None, min_length=3, max_length=200)

    @model_validator(mode="after")
    def validate_anchor(self) -> IndicatorSourceInputs:
        if self.anchor_time is not None and self.anchor_bars_ago is not None:
            raise ValueError("indicator inputs take anchor_time or anchor_bars_ago, not both")
        if self.source is not None and self.source.inputs.source is not None:
            raise ValueError("an indicator's source cannot itself read another indicator")
        for key, value in self.params.items():
            if not key or len(key) > 40 or isinstance(value, bool) or (isinstance(value, str) and len(value) > 40):
                raise ValueError(f"indicator param {key!r} must be a short name with a number or a short text")
        return self

    def registry_inputs(self, anchor_time: str | None = None, session: TradingSession | None = None) -> IndicatorInputs:
        from .indicators.registry import IndicatorInputs

        return IndicatorInputs(
            period=self.period,
            fast_period=self.fast_period,
            slow_period=self.slow_period,
            signal_period=self.signal_period,
            standard_deviations=self.standard_deviations,
            anchor_time=anchor_time if anchor_time is not None else self.anchor_time,
            compare_symbol=self.compare_symbol,
            params=dict(self.params),
            session=session,
        )


class IndicatorOutputRef(BaseModel):
    """Another indicator's output on the same chart, as an indicator's source (TVP-6.5)."""

    model_config = ConfigDict(extra="forbid")

    indicator_id: str = Field(min_length=1, max_length=120)
    inputs: IndicatorSourceInputs = Field(default_factory=IndicatorSourceInputs)
    output: str = Field(min_length=1, max_length=240)


IndicatorSourceInputs.model_rebuild()


class IndicatorSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["indicator"]
    indicator_id: str = Field(min_length=1, max_length=120)
    inputs: IndicatorSourceInputs = Field(default_factory=IndicatorSourceInputs)
    output: str = Field(min_length=1, max_length=240)


class TrendlineSource(BaseModel):
    """A line through two points; its value at a bar is the line at the bar's end time."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["trendline"]
    points: list[TrendlineAlertPoint] = Field(min_length=2, max_length=2)


ScriptInputValue = bool | int | float | str


class ScriptSource(BaseModel):
    """An Omnix Script's output (TVP-11.4), from the script as saved at ``revision`` (its later edits don't change the alert).

    ``output``: ``plot:<i>`` is the value of the plot call at position i among the script's output calls (plotshape
    and plotchar only where they show); ``alertcondition:<i>`` and ``alert`` (any ``alert()`` call) have a value only
    on the bars where they fire.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["script"]
    script_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    inputs: dict[str, ScriptInputValue] = Field(default_factory=dict, max_length=50)
    output: str = Field(pattern=r"^(plot:\d{1,4}|alertcondition:\d{1,4}|alert)$")


# The unions are told apart by each member's required literal ``kind``
# (pydantic's smart union). A ``discriminator`` would be faster, but FastAPI's split input/output
# schemas then publish a mapping that names the wrong schemas, and the
# generated TypeScript types become unusable.
AlertSource = PriceSource | ChangePercentSource | IndicatorSource | TrendlineSource | ScriptSource


class ValueTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["value"]
    value: Decimal


class SourceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["source"]
    source: AlertSource


ChannelBound = ValueTarget | SourceTarget


class ChannelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["channel"]
    upper: ChannelBound
    lower: ChannelBound


AlertTarget = ValueTarget | SourceTarget | ChannelTarget


class AlertConditionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: AlertSource
    operator: AlertOperator
    target: AlertTarget | None = None
    amount: Decimal | None = None
    bars: int | None = Field(default=None, ge=1, le=500)

    @model_validator(mode="after")
    def validate_operator_contract(self) -> AlertConditionSpec:
        if self.operator in MOVING_OPERATORS:
            if self.target is not None:
                raise ValueError(f"{self.operator} takes an amount and bars, not a target")
            if self.amount is None or self.amount <= 0:
                raise ValueError(f"{self.operator} needs an amount greater than zero")
            if self.bars is None:
                raise ValueError(f"{self.operator} needs bars of at least 1")
            return self
        if self.amount is not None or self.bars is not None:
            raise ValueError(f"{self.operator} does not take an amount or bars")
        if self.target is None:
            raise ValueError(f"{self.operator} needs a target")
        is_channel = isinstance(self.target, ChannelTarget)
        if self.operator in CHANNEL_OPERATORS and not is_channel:
            raise ValueError(f"{self.operator} needs a channel target")
        if self.operator not in CHANNEL_OPERATORS and is_channel:
            raise ValueError(f"a channel target needs a channel operator, not {self.operator}")
        return self


def condition_sources(condition: AlertConditionSpec) -> list[Any]:
    """Every source a condition reads: its own and those inside its target."""
    sources: list[Any] = [condition.source]
    target = condition.target
    if isinstance(target, SourceTarget):
        sources.append(target.source)
    elif isinstance(target, ChannelTarget):
        sources.extend(bound.source for bound in (target.upper, target.lower) if isinstance(bound, SourceTarget))
    return sources


# --- Indicator registry checks -------------------------------------------------

_SYNTHETIC_BARS = 1200


class _SyntheticBar:
    __slots__ = ("start_time", "open", "high", "low", "close", "volume")

    def __init__(self, index: int, previous_close: float) -> None:
        close = 100 + 10 * math.sin(index / 7) + 3 * math.sin(index / 2.3) + index * 0.01
        self.start_time = datetime(2000, 1, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
        self.open = previous_close
        self.high = max(previous_close, close) + 0.5 + (index % 3) * 0.1
        self.low = min(previous_close, close) - 0.5 - (index % 5) * 0.1
        self.close = close
        self.volume = 1000 + (index % 11) * 37


@bounded_lru_cache(max_entries=1, ttl_seconds=86_400.0)
def _synthetic_series() -> BarSeries:
    from .indicators.registry import BarSeries

    bars: list[_SyntheticBar] = []
    previous = 100.0
    for index in range(_SYNTHETIC_BARS):
        bar = _SyntheticBar(index, previous)
        previous = bar.close
        bars.append(bar)
    return BarSeries.from_bars(bars)


def _inputs_key(inputs: IndicatorSourceInputs) -> tuple[Any, ...]:
    return (
        inputs.period,
        inputs.fast_period,
        inputs.slow_period,
        inputs.signal_period,
        inputs.standard_deviations,
        inputs.anchor_time,
        inputs.source.model_dump_json() if inputs.source is not None else None,
        tuple(sorted(inputs.params.items())),
        inputs.compare_symbol,
    )


# A compare symbol's bars covering the bars an indicator runs on (see ``indicator_context.compare_bars_loader``).
CompareBars = Callable[[str, "BarSeries"], "BarSeries | None"]


def compute_source_indicator(
    indicator_id: str,
    bars: BarSeries,
    inputs: IndicatorSourceInputs,
    anchor_time: str | None = None,
    *,
    session: TradingSession | None = None,
    compare: CompareBars | None = None,
) -> list[IndicatorOutputSeries]:
    """An indicator on ``bars`` with these inputs: on its source indicator's output when it has one (TVP-6.5).

    ``session`` is the instrument's session calendar (None: UTC days); ``compare`` loads a compare symbol's bars.
    A source output the source indicator doesn't produce gives no outputs."""

    from .indicators.registry import compute_indicator
    from .indicators.sources import compute_on_source, find_output

    def run(indicator: str, indicator_inputs: IndicatorSourceInputs, anchor: str | None) -> list[IndicatorOutputSeries]:
        symbol = indicator_inputs.compare_symbol
        compare_bars = compare(symbol, bars) if compare is not None and symbol else None
        return compute_indicator(indicator, bars, indicator_inputs.registry_inputs(anchor, session), compare_bars)

    if inputs.source is None:
        return run(indicator_id, inputs, anchor_time)
    reference = inputs.source
    source = find_output(run(reference.indicator_id, reference.inputs, None), reference.output)
    if source is None:
        return []
    return compute_on_source(indicator_id, bars, inputs.registry_inputs(anchor_time, session), source)


@bounded_lru_cache(max_entries=512, ttl_seconds=86_400.0)
def _output_profile(indicator_id: str, inputs_key: tuple[Any, ...]) -> tuple[tuple[str, int | None], ...]:
    period, fast, slow, signal, deviations, anchor_time, source, params, compare_symbol = inputs_key
    outputs = compute_source_indicator(
        indicator_id,
        _synthetic_series(),
        IndicatorSourceInputs(
            period=period,
            fast_period=fast,
            slow_period=slow,
            signal_period=signal,
            standard_deviations=deviations,
            anchor_time=anchor_time,
            source=IndicatorOutputRef.model_validate_json(source) if source is not None else None,
            params=dict(params),
            compare_symbol=compare_symbol,
        ),
        # A compare symbol's profile reads the synthetic series as its second series too.
        compare=lambda _symbol, bars: bars,
    )
    profile: list[tuple[str, int | None]] = []
    for output in outputs:
        first = next((index for index, value in output.points if math.isfinite(value)), None)
        profile.append((output.key, first))
    return tuple(profile)


def indicator_output_profile(source: IndicatorSource) -> tuple[tuple[str, int | None], ...]:
    """The output keys an indicator produces for these inputs, with each key's first valid bar.

    Computed once per (indicator, inputs) on a synthetic series. ``anchor_bars_ago``
    is left out: its keys are those of the unanchored indicator.
    """
    return _output_profile(source.indicator_id, _inputs_key(source.inputs))


def validate_indicator_source(source: IndicatorSource) -> None:
    from .indicators.external import external_indicator
    from .indicators.intrabar import intrabar_output_keys, is_intrabar_indicator, validate_intrabar_params
    from .indicators.registry import server_indicator
    from .indicators.sources import accepts_source

    if is_intrabar_indicator(source.indicator_id):
        # Volume Delta and CVD (TVP-6.4): computed from lower-timeframe bars, with a lower interval and an anchor.
        if source.inputs.source is not None:
            raise ValueError(f"indicator {source.indicator_id!r} reads lower-timeframe bars and does not take a source")
        if source.output not in intrabar_output_keys(source.indicator_id):
            raise ValueError(
                f"indicator {source.indicator_id!r} has no output {source.output!r}; outputs: {', '.join(intrabar_output_keys(source.indicator_id))}"
            )
        validate_intrabar_params(source.inputs.params or {})
        return
    external = external_indicator(source.indicator_id)
    if external is not None:
        # An external-data series (TVP-0.2): no formula, no inputs that matter, its outputs fixed by its metric.
        if source.inputs.source is not None:
            raise ValueError(f"indicator {source.indicator_id!r} is a data series and does not take a source")
        if source.output not in external.output_keys:
            raise ValueError(
                f"indicator {source.indicator_id!r} has no output {source.output!r}; outputs: {', '.join(external.output_keys)}"
            )
        return
    reference = source.inputs.source
    if reference is not None and external_indicator(reference.indicator_id) is not None:
        raise ValueError(f"indicator {source.indicator_id!r} cannot read the data series {reference.indicator_id!r} as its source yet")
    if server_indicator(source.indicator_id) is None:
        raise ValueError(f"indicator {source.indicator_id!r} is not available on the server")
    reference = source.inputs.source
    if reference is not None:
        if not accepts_source(source.indicator_id):
            raise ValueError(f"indicator {source.indicator_id!r} does not take another indicator as its source")
        validate_indicator_source(
            IndicatorSource(kind="indicator", indicator_id=reference.indicator_id, inputs=reference.inputs, output=reference.output)
        )
    try:
        profile = indicator_output_profile(source)
    except Exception as exc:  # any failure of the indicator on these inputs is the caller's input error
        raise ValueError(f"invalid inputs for indicator {source.indicator_id!r}: {exc}") from exc
    keys = [key for key, _ in profile]
    if source.output not in keys:
        raise ValueError(
            f"indicator {source.indicator_id!r} has no output {source.output!r}; outputs: {', '.join(keys) or 'none'}"
        )


def validate_conditions_against_registry(conditions: list[AlertConditionSpec]) -> None:
    for condition in conditions:
        for source in condition_sources(condition):
            if isinstance(source, IndicatorSource):
                validate_indicator_source(source)


def validate_external_scope(instrument_id: str, conditions: list[AlertConditionSpec]) -> None:
    """External-data indicators only on the instruments their data exists for (open interest on Binance crypto...)."""
    from .indicators.external import external_available_for, external_indicator, external_scope_name

    for condition in conditions:
        for source in condition_sources(condition):
            if isinstance(source, IndicatorSource) and external_indicator(source.indicator_id) is not None:
                if not external_available_for(source.indicator_id, instrument_id):
                    raise ValueError(
                        f"indicator {source.indicator_id!r} has data for {external_scope_name(source.indicator_id)} only"
                    )


# --- Legacy adapter ------------------------------------------------------------

LegacyAlertCondition = Literal[
    "price_above",
    "price_below",
    "percent_change_above",
    "percent_change_below",
    "indicator_above",
    "indicator_below",
    "indicator_cross_above",
    "indicator_cross_below",
    "volume_above",
    "volume_below",
    "trendline_crossing",
    "trendline_crossing_up",
    "trendline_crossing_down",
    "trendline_above",
    "trendline_below",
]

_TRENDLINE_OPERATORS: dict[str, AlertOperator] = {
    "trendline_crossing": "crossing",
    "trendline_crossing_up": "crossing_up",
    "trendline_crossing_down": "crossing_down",
    # Before TVP-1.2 "above"/"below" trendline alerts fired on the crossing, and they still do.
    "trendline_above": "crossing_up",
    "trendline_below": "crossing_down",
}


def legacy_indicator_source(parameters: Any) -> IndicatorSource:
    """The registry indicator a pre-TVP-1.2 indicator alert means."""
    indicator_id = parameters.indicator_id
    period = parameters.period
    component = parameters.component
    if indicator_id in {"sma", "ema", "rsi", "atr"}:
        return IndicatorSource(kind="indicator", indicator_id=indicator_id, inputs=IndicatorSourceInputs(period=period), output=f"{indicator_id}:{period}")
    if indicator_id == "bollinger":
        band = component if component in {"upper", "middle", "lower"} else "middle"
        return IndicatorSource(
            kind="indicator",
            indicator_id="bollinger",
            inputs=IndicatorSourceInputs(period=period, standard_deviations=2.0),
            output=f"bollinger:{period}:{band}",
        )
    if indicator_id == "macd":
        line = component if component in {"line", "signal", "histogram"} else "line"
        fast, slow = parameters.fast_period, parameters.slow_period
        return IndicatorSource(
            kind="indicator",
            indicator_id="macd",
            inputs=IndicatorSourceInputs(period=period, fast_period=fast, slow_period=slow, signal_period=parameters.signal_period),
            output=f"macd:{fast}:{slow}:{line}",
        )
    if indicator_id == "stochastic-rsi":
        return IndicatorSource(
            kind="indicator",
            indicator_id="stochastic-rsi",
            inputs=IndicatorSourceInputs(period=period, fast_period=parameters.fast_period, signal_period=parameters.signal_period),
            output="stochastic-rsi:k",
        )
    if indicator_id == "vwap":
        return IndicatorSource(
            kind="indicator",
            indicator_id="vwap",
            inputs=IndicatorSourceInputs(period=1, anchor_bars_ago=parameters.anchor_bars_ago),
            output="vwap:dataset",
        )
    raise ValueError(f"unsupported legacy indicator {indicator_id!r}")


def legacy_conditions(condition_type: str, threshold: Decimal, parameters: Any) -> list[AlertConditionSpec]:
    """Conditions with the meaning a pre-TVP-1.2 alert has always had.

    Every legacy family fires on a crossing: "_above" types when the value
    crosses up through the threshold, "_below" types when it crosses down.
    """
    if condition_type.startswith("trendline_"):
        points = parameters.trendline_points
        if points is None or len(points) != 2:
            raise ValueError("trendline conditions require two trendline points")
        return [
            AlertConditionSpec(
                source=PriceSource(kind="price", field="close"),
                operator=_TRENDLINE_OPERATORS[condition_type],
                target=SourceTarget(kind="source", source=TrendlineSource(kind="trendline", points=list(points))),
            )
        ]
    operator: AlertOperator = "crossing_up" if condition_type.endswith("_above") else "crossing_down"
    source: Any
    if condition_type.startswith("price_"):
        source = PriceSource(kind="price", field="close")
    elif condition_type.startswith("volume_"):
        source = PriceSource(kind="price", field="volume")
    elif condition_type.startswith("percent_change_"):
        source = ChangePercentSource(kind="change_percent", lookback_bars=parameters.lookback_bars)
    elif condition_type.startswith("indicator_"):
        source = legacy_indicator_source(parameters)
    else:
        raise ValueError(f"unsupported legacy condition type {condition_type!r}")
    return [AlertConditionSpec(source=source, operator=operator, target=ValueTarget(kind="value", value=threshold))]


__all__ = [
    "CompareBars",
    "ALERT_FREQUENCIES",
    "CHANNEL_OPERATORS",
    "MAX_ALERT_CONDITIONS",
    "MOVING_OPERATORS",
    "AlertConditionSpec",
    "AlertFrequency",
    "AlertOperator",
    "AlertSource",
    "AlertTarget",
    "ChangePercentSource",
    "ChannelTarget",
    "IndicatorSource",
    "IndicatorSourceInputs",
    "LegacyAlertCondition",
    "PriceSource",
    "SourceTarget",
    "TrendlineAlertPoint",
    "TrendlineSource",
    "ValueTarget",
    "condition_sources",
    "indicator_output_profile",
    "legacy_conditions",
    "legacy_indicator_source",
    "validate_conditions_against_registry",
    "validate_external_scope",
    "validate_indicator_source",
]
