"""Alert condition model and bar evaluator (TVP-1.1, TVP-1.2)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.apps.trading.alert_conditions import (
    AlertConditionSpec,
    IndicatorSource,
    IndicatorSourceInputs,
    legacy_conditions,
)
from app.apps.trading.alerts import (
    TradingAlert,
    TradingAlertCreate,
    TradingAlertParameters,
    alert_trigger_key,
    cooldown_elapsed,
    frequency_allows,
)
from app.apps.trading.alerts_evaluation import (
    AlertConditionOutcome,
    evaluate_conditions,
    history_limit,
    operator_met,
    required_bars,
)
from app.apps.trading.indicators.registry import BarSeries, IndicatorInputs, compute_indicator

START = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
D = Decimal


def bars_from(closes, *, volumes=None, final=True, highs=None, lows=None):
    bars = []
    for index, close in enumerate(closes):
        close = D(str(close))
        bars.append(
            SimpleNamespace(
                start_time=START + timedelta(minutes=index),
                end_time=START + timedelta(minutes=index + 1),
                open=close,
                high=D(str(highs[index])) if highs else close + 1,
                low=D(str(lows[index])) if lows else close - 1,
                close=close,
                volume=D(str(volumes[index])) if volumes else D("1000"),
                is_final=final if isinstance(final, bool) else final[index],
            )
        )
    return bars


def spec(**data) -> AlertConditionSpec:
    return AlertConditionSpec.model_validate(data)


def value(number) -> dict:
    return {"kind": "value", "value": str(number)}


CLOSE = {"kind": "price", "field": "close"}


def met(condition: AlertConditionSpec, closes, **kwargs) -> bool:
    outcome = evaluate_conditions([condition], bars_from(closes, **kwargs), final_only=False)
    assert outcome is not None
    return outcome.met


# --- Operator truth tables -----------------------------------------------------


@pytest.mark.parametrize(
    ("operator", "previous", "current", "expected"),
    [
        ("crossing_up", 99, 100, True),  # touch from below counts
        ("crossing_up", 99, 101, True),
        ("crossing_up", 100, 101, False),  # already at the level
        ("crossing_up", 101, 99, False),
        ("crossing_up", 95, 105, True),  # gap across the level
        ("crossing_down", 101, 100, True),
        ("crossing_down", 105, 95, True),
        ("crossing_down", 100, 99, False),
        ("crossing_down", 99, 101, False),
        ("crossing", 99, 100, True),
        ("crossing", 101, 100, True),
        ("crossing", 100, 100, False),
        ("crossing", 101, 102, False),
        ("greater_than", 0, 101, True),
        ("greater_than", 0, 100, False),  # strict
        ("less_than", 0, 99, True),
        ("less_than", 0, 100, False),
    ],
)
def test_threshold_operators(operator, previous, current, expected) -> None:
    condition = spec(source=CLOSE, operator=operator, target=value(100))
    assert met(condition, [previous, current]) is expected


@pytest.mark.parametrize(
    ("operator", "previous", "current", "expected"),
    [
        ("entering_channel", 111, 110, True),  # touching the upper bound is inside
        ("entering_channel", 89, 95, True),
        ("entering_channel", 120, 80, False),  # jumped over the channel
        ("entering_channel", 95, 96, False),
        ("exiting_channel", 95, 111, True),
        ("exiting_channel", 90, 89, True),
        ("exiting_channel", 95, 110, False),
        ("exiting_channel", 120, 130, False),
        ("inside_channel", 0, 90, True),
        ("inside_channel", 0, 110, True),
        ("inside_channel", 0, 110.01, False),
        ("outside_channel", 0, 89.99, True),
        ("outside_channel", 0, 100, False),
    ],
)
def test_channel_operators(operator, previous, current, expected) -> None:
    condition = spec(source=CLOSE, operator=operator, target={"kind": "channel", "upper": value(110), "lower": value(90)})
    assert met(condition, [previous, current]) is expected


def test_channel_bounds_may_be_sources_and_are_order_independent() -> None:
    swapped = spec(source=CLOSE, operator="inside_channel", target={"kind": "channel", "upper": value(90), "lower": value(110)})
    assert met(swapped, [0, 100])
    # Close against its own bar's high/low: always inside.
    band = spec(
        source=CLOSE,
        operator="inside_channel",
        target={
            "kind": "channel",
            "upper": {"kind": "source", "source": {"kind": "price", "field": "high"}},
            "lower": {"kind": "source", "source": {"kind": "price", "field": "low"}},
        },
    )
    assert met(band, [10, 12])


def test_a_line_target_moves_with_each_bar() -> None:
    """Close crossing an SMA (TVP-1.3): both sides are read on the same two bars, as the dialog's line targets send."""
    sma = {"kind": "source", "source": {"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 3}, "output": "sma:3"}}
    crossing = spec(source=CLOSE, operator="crossing_up", target=sma)
    # SMA(3) is 9.67 then 10.33: the close goes from below it (9) to above it (12).
    assert met(crossing, [10, 10, 10, 9, 12])
    # Above the SMA on both bars: no cross, though greater_than holds.
    assert not met(crossing, [10, 10, 10, 12, 13])
    assert met(spec(source=CLOSE, operator="greater_than", target=sma), [10, 10, 10, 12, 13])
    # Before the SMA has two values there is nothing to cross.
    assert not met(crossing, [10, 10, 12])


@pytest.mark.parametrize(
    ("operator", "amount", "closes", "expected"),
    [
        ("moving_up", "5", [100, 101, 105], True),  # exactly the amount
        ("moving_up", "5", [100, 101, 104.99], False),
        ("moving_up", "1.1", [100.0, 101.1], True),  # decimal arithmetic, no float residue
        ("moving_down", "5", [100, 99, 95], True),
        ("moving_down", "5", [100, 99, 96], False),
        ("moving_up_percent", "10", [50, 52, 55], True),
        ("moving_up_percent", "10", [50, 52, 54.99], False),
        ("moving_down_percent", "10", [-50, -52, -55], True),  # |base| for negative values
        ("moving_down_percent", "10", [50, 47, 45], True),
        ("moving_up_percent", "10", [0, 1, 2], False),  # zero base
    ],
)
def test_moving_operators(operator, amount, closes, expected) -> None:
    bars = 1 if len(closes) == 2 else 2
    condition = spec(source=CLOSE, operator=operator, amount=amount, bars=bars)
    assert met(condition, closes) is expected


def test_missing_values_make_a_condition_false() -> None:
    sma = {"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 5}, "output": "sma:5"}
    warm_up = spec(source=sma, operator="greater_than", target=value(0))
    assert not met(warm_up, [10, 11, 12, 13])  # SMA(5) has no value yet
    assert met(warm_up, [10, 11, 12, 13, 14])
    crossing = spec(source=sma, operator="crossing_up", target=value(0))
    assert not met(crossing, [10, 11, 12, 13, 14])  # no previous SMA value
    one_bar = spec(source=CLOSE, operator="crossing_up", target=value(5))
    assert not met(one_bar, [10])
    moving = spec(source=CLOSE, operator="moving_up", amount="1", bars=5)
    assert not met(moving, [1, 2, 3])


def test_operator_met_rejects_missing_inputs() -> None:
    assert not operator_met("crossing_up", (None, D(1)), target=(D(0), D(0)))
    assert not operator_met("greater_than", (None, None), target=(D(0), D(0)))
    assert not operator_met("entering_channel", (None, D(1)), upper=(D(2), D(2)), lower=(D(0), D(0)))
    assert operator_met("inside_channel", (None, D(1)), upper=(D(2), D(2)), lower=(D(0), D(0)))


def test_conditions_are_combined_with_and() -> None:
    above = spec(source=CLOSE, operator="greater_than", target=value(100))
    below = spec(source=CLOSE, operator="less_than", target=value(105))
    volume = spec(source={"kind": "price", "field": "volume"}, operator="greater_than", target=value(500))
    bars = bars_from([99, 101], volumes=[100, 1000])
    outcome = evaluate_conditions([above, below, volume], bars, final_only=False)
    assert outcome is not None and outcome.met
    assert [observation.met for observation in outcome.observations] == [True, True, True]
    bars = bars_from([99, 106], volumes=[100, 1000])
    outcome = evaluate_conditions([above, below, volume], bars, final_only=False)
    assert outcome is not None and not outcome.met
    assert [observation.met for observation in outcome.observations] == [True, False, True]


def test_bar_close_evaluates_every_condition_on_the_closed_bar() -> None:
    """Once per bar close (TVP-1.6): all the conditions are read on the last closed bar, none on the forming one."""
    crossing = spec(source=CLOSE, operator="crossing_up", target=value(100))
    volume = spec(source={"kind": "price", "field": "volume"}, operator="greater_than", target=value(500))
    # The closed bar crosses but its volume is low; the forming bar would satisfy both.
    bars = bars_from([99, 101, 102], volumes=[100, 200, 900], final=[True, True, False])
    assert evaluate_conditions([crossing, volume], bars, final_only=False) is not None
    closed = evaluate_conditions([crossing, volume], bars, final_only=True)
    assert closed is not None and not closed.met
    assert [observation.met for observation in closed.observations] == [True, False]
    # Both hold on the closed bar: it fires.
    bars = bars_from([99, 101, 102], volumes=[100, 900, 100], final=[True, True, False])
    closed = evaluate_conditions([crossing, volume], bars, final_only=True)
    assert closed is not None and closed.met and closed.bar_start == bars[1].start_time


def test_price_fields_and_change_percent() -> None:
    bars = bars_from([100, 110], highs=[101, 120], lows=[99, 100])
    outcome = evaluate_conditions(
        [
            spec(source={"kind": "price", "field": "hl2"}, operator="greater_than", target=value("109.99")),
            spec(source={"kind": "price", "field": "hlc3"}, operator="greater_than", target=value(0)),
            spec(source={"kind": "change_percent", "lookback_bars": 1}, operator="greater_than", target=value("9.99")),
        ],
        bars,
        final_only=False,
    )
    assert outcome is not None and outcome.met
    assert outcome.observations[0].source == D("110")
    assert outcome.observations[1].source == D("110")
    assert outcome.observations[2].source == D("10")


def test_bar_close_evaluation_ignores_the_forming_bar() -> None:
    condition = spec(source=CLOSE, operator="crossing_up", target=value(100))
    # Intrabar the forming bar crosses, but the last closed bar does not.
    bars = bars_from([98, 99, 101], final=[True, True, False])
    assert evaluate_conditions([condition], bars, final_only=False).met
    closed = evaluate_conditions([condition], bars, final_only=True)
    assert closed is not None and not closed.met
    assert closed.bar_start == bars[1].start_time and closed.bar_is_final
    # When that bar closes above the level, the close evaluation fires.
    bars = bars_from([98, 99, 101, 101], final=[True, True, True, False])
    closed = evaluate_conditions([condition], bars, final_only=True)
    assert closed.met and closed.bar_start == bars[2].start_time
    assert evaluate_conditions([condition], bars_from([1], final=False), final_only=True) is None


def test_indicator_values_equal_the_registry_and_ignore_later_bars() -> None:
    closes = [100 + (index % 7) * 1.5 - index * 0.1 for index in range(80)]
    bars = bars_from(closes, final=[True] * 79 + [False])
    source = {"kind": "indicator", "indicator_id": "macd", "inputs": {"fast_period": 12, "slow_period": 26, "signal_period": 9}, "output": "macd:12:26:signal"}
    outcome = evaluate_conditions([spec(source=source, operator="greater_than", target=value(-1000))], bars, final_only=True)
    expected = dict(
        next(
            series.points
            for series in compute_indicator(
                "macd", BarSeries.from_bars(bars[:79]), IndicatorInputs(period=14, fast_period=12, slow_period=26, signal_period=9)
            )
            if series.key == "macd:12:26:signal"
        )
    )
    assert outcome.observations[0].source == D(repr(expected[78]))
    assert outcome.observations[0].source_previous == D(repr(expected[77]))


def test_rolling_vwap_anchor_moves_with_the_bar() -> None:
    bars = bars_from([10, 20, 30, 40], volumes=[1, 1, 1, 1])
    source = {"kind": "indicator", "indicator_id": "vwap", "inputs": {"period": 1, "anchor_bars_ago": 1}, "output": "vwap:dataset"}
    outcome = evaluate_conditions([spec(source=source, operator="greater_than", target=value(0))], bars, final_only=False)
    # Bar 3 anchored at bar 2 averages typical prices 30 and 40; bar 2 anchored at bar 1 averages 20 and 30.
    assert outcome.observations[0].source == D("35")
    assert outcome.observations[0].source_previous == D("25")


def test_trendline_target_is_the_line_at_the_bar_end() -> None:
    line = {"kind": "trendline", "points": [{"time": START.isoformat(), "price": "100"}, {"time": (START + timedelta(minutes=10)).isoformat(), "price": "110"}]}
    condition = spec(source=CLOSE, operator="crossing_up", target={"kind": "source", "source": line})
    outcome = evaluate_conditions([condition], bars_from([100, 103]), final_only=False)
    assert outcome.observations[0].target_previous == D("101")
    assert outcome.observations[0].target == D("102")
    assert outcome.met


# --- Validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        {"source": CLOSE, "operator": "inside_channel", "target": value(1)},
        {"source": CLOSE, "operator": "crossing", "target": {"kind": "channel", "upper": value(2), "lower": value(1)}},
        {"source": CLOSE, "operator": "moving_up", "amount": "0", "bars": 1},
        {"source": CLOSE, "operator": "moving_up", "amount": "1"},
        {"source": CLOSE, "operator": "moving_up", "amount": "1", "bars": 1, "target": value(1)},
        {"source": CLOSE, "operator": "crossing"},
        {"source": CLOSE, "operator": "crossing", "target": value(1), "bars": 2},
    ],
)
def test_operator_and_target_must_fit(data) -> None:
    with pytest.raises(ValidationError):
        spec(**data)


def _create(**data):
    return TradingAlertCreate(alert_id="a", instrument_id="crypto:BINANCE:spot:BTC-USDT", **data)


def test_requests_check_indicators_against_the_registry_and_limit_conditions() -> None:
    good = {"kind": "indicator", "indicator_id": "tv-ichimoku-cloud", "inputs": {"period": 26}, "output": "tv-ichimoku-cloud:span-b"}
    _create(conditions=[{"source": good, "operator": "greater_than", "target": value(0)}])
    for source, message in (
        ({**good, "indicator_id": "not-an-indicator"}, "not available on the server"),
        ({**good, "output": "tv-ichimoku-cloud:nope"}, "has no output"),
        ({"kind": "indicator", "indicator_id": "macd", "inputs": {"fast_period": 30, "slow_period": 26}, "output": "macd:30:26:line"}, "invalid inputs"),
    ):
        with pytest.raises(ValidationError, match=message):
            _create(conditions=[{"source": source, "operator": "greater_than", "target": value(0)}])
    condition = {"source": CLOSE, "operator": "greater_than", "target": value(0)}
    _create(conditions=[condition] * 5)
    with pytest.raises(ValidationError):
        _create(conditions=[condition] * 6)
    with pytest.raises(ValidationError, match="one to five"):
        _create(conditions=[])
    with pytest.raises(ValidationError, match="zero threshold"):
        _create(conditions=[condition], threshold="5")


def test_legacy_requests_derive_conditions_and_reject_disagreeing_ones() -> None:
    created = _create(condition_type="price_above", threshold="100")
    assert created.conditions == [spec(source=CLOSE, operator="crossing_up", target=value(100))]
    # Sending back the derived conditions is fine; different ones are not.
    _create(condition_type="price_above", threshold="100", conditions=[c.model_dump() for c in created.conditions])
    with pytest.raises(ValidationError, match="disagree"):
        _create(condition_type="price_above", threshold="100", conditions=[{"source": CLOSE, "operator": "less_than", "target": value(1)}])


@pytest.mark.parametrize(
    ("source", "extra", "message"),
    [
        ({"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 1000}, "output": "sma:1000"}, {}, "at most 500"),
        ({"kind": "indicator", "indicator_id": "stochastic-rsi", "inputs": {"period": 500, "fast_period": 3, "signal_period": 3}, "output": "stochastic-rsi:k"}, {}, "bars of history"),
        ({"kind": "indicator", "indicator_id": "macd", "inputs": {"fast_period": 500, "slow_period": 501, "signal_period": 9}, "output": "macd:500:501:line"}, {}, "at most 500"),
        ({"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 500}, "output": "sma:500"}, {"operator": "moving_up", "amount": "1", "bars": 500}, "bars of history"),
        ({"kind": "change_percent", "lookback_bars": 500}, {"operator": "moving_up", "amount": "1", "bars": 500}, "bars of history"),
    ],
)
def test_conditions_the_monitor_could_never_evaluate_are_rejected(source, extra, message) -> None:
    condition = {"source": source, "operator": "greater_than", "target": value(0), **extra}
    if "bars" in extra:
        condition.pop("target")
    with pytest.raises(ValidationError, match=message):
        _create(conditions=[condition])


def test_an_output_without_any_value_is_rejected(monkeypatch) -> None:
    from app.apps.trading import alerts_evaluation

    monkeypatch.setattr(alerts_evaluation, "indicator_output_profile", lambda source: (("sma:5", None),))
    with pytest.raises(ValidationError, match="never has a value"):
        _create(conditions=[{"source": {"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 5}, "output": "sma:5"}, "operator": "greater_than", "target": value(0)}])


def test_the_largest_accepted_conditions_fit_one_history_fetch() -> None:
    created = _create(conditions=[{"source": {"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 500}, "output": "sma:500"}, "operator": "crossing_up", "target": value(0)}])
    assert required_bars(created.conditions) + 2 <= 1000


def test_only_bar_close_alerts_wait_for_closed_bars() -> None:
    for frequency in ("once", "every_time", "once_per_bar", "once_per_minute"):
        request = _create(condition_type="price_above", threshold="1", frequency=frequency, evaluation_policy={"allow_partial_bars": False})
        assert request.evaluation_policy.allow_partial_bars is True
    request = _create(condition_type="price_above", threshold="1", frequency="once_per_bar_close", evaluation_policy={"allow_partial_bars": True})
    assert request.evaluation_policy.allow_partial_bars is False
    # Stored alerts keep what they have.
    stored = TradingAlert(alert_id="s", instrument_id="crypto:X:Y", condition_type="price_above", threshold=D(1), evaluation_policy={"allow_partial_bars": False})
    assert stored.evaluation_policy.allow_partial_bars is False


def test_stored_alerts_that_break_write_rules_still_read() -> None:
    alert = TradingAlert(
        alert_id="old",
        instrument_id="crypto:X:Y",
        condition_type="indicator_above",
        threshold=D(1),
        parameters={"delivery": {"webhook": {"url": "http://169.254.169.254/x"}}},
        evaluation_policy={"formula_version": "omnix-indicators-v1"},
    )
    assert alert.conditions == []  # no indicator_id: never fires


def test_frequency_comes_from_trigger_policy_when_omitted_and_is_mirrored() -> None:
    assert _create(condition_type="price_above", threshold="1").frequency == "every_time"
    legacy = _create(condition_type="price_above", threshold="1", parameters={"trigger_policy": "once_per_bar"})
    assert legacy.frequency == "once_per_bar"
    explicit = _create(condition_type="price_above", threshold="1", frequency="once_per_bar_close", parameters={"trigger_policy": "once"})
    assert explicit.frequency == "once_per_bar_close"
    assert explicit.parameters.trigger_policy == "once_per_bar_close"


def test_channels_and_delivery_settings() -> None:
    parameters = TradingAlertParameters(
        notification_channels=["app", "webhook", "email", "push"],
        delivery={"webhook": {"url": "https://hooks.example.com/x"}, "email": {"to": "me@example.com"}, "sound": {"name": "chime"}},
    )
    assert parameters.delivery.webhook.has_secret is False
    with pytest.raises(ValidationError):
        TradingAlertParameters(notification_channels=["fax"])
    # The URL policy is a write rule: a stored URL stays readable.
    assert TradingAlertParameters(delivery={"webhook": {"url": "file:///etc/passwd"}}).delivery.webhook.url == "file:///etc/passwd"
    with pytest.raises(ValidationError, match="not allowed"):
        _create(condition_type="price_above", threshold="1", parameters={"delivery": {"webhook": {"url": "file:///etc/passwd"}}})
    with pytest.raises(ValidationError):
        TradingAlertParameters(delivery={"email": {"to": "not-an-address"}})
    request = _create(condition_type="price_above", threshold="1", webhook_secret="s3cret")
    assert "webhook_secret" not in request.model_dump()
    assert "s3cret" not in request.model_dump_json()


# --- Legacy adapter ------------------------------------------------------------


def legacy_alert(condition_type: str, threshold="0", **parameters) -> TradingAlert:
    return TradingAlert(
        alert_id="legacy",
        instrument_id="crypto:BINANCE:spot:BTC-USDT",
        condition_type=condition_type,
        threshold=D(str(threshold)),
        parameters=parameters,
    )


def _met(alert: TradingAlert, bars) -> bool:
    outcome = evaluate_conditions(alert.conditions, bars, final_only=False)
    return outcome is not None and outcome.met


def _between_polls(previous, current, threshold, above: bool) -> bool:
    """The pre-TVP-1.2 rule, with one poll per bar."""
    if previous is None or current is None:
        return False
    return previous < threshold <= current if above else previous > threshold >= current


@pytest.mark.parametrize("direction", ["above", "below"])
def test_legacy_price_volume_and_percent_alerts_keep_their_meaning(direction) -> None:
    above = direction == "above"
    closes = [100, 98, 101, 103, 99, 100, 97, 104]
    volumes = [10, 30, 20, 50, 40, 60, 20, 80]
    for index in range(1, len(closes)):
        window = bars_from(closes[: index + 1], volumes=volumes[: index + 1])
        assert _met(legacy_alert(f"price_{direction}", 100), window) == _between_polls(closes[index - 1], closes[index], 100, above)
        assert _met(legacy_alert(f"volume_{direction}", 40), window) == _between_polls(volumes[index - 1], volumes[index], 40, above)
        change = [None if i < 2 else (D(closes[i]) / D(closes[i - 2]) - 1) * 100 for i in range(len(closes))]
        assert _met(legacy_alert(f"percent_change_{direction}", 1, lookback_bars=2), window) == _between_polls(
            change[index - 1], change[index], 1, above
        )


LEGACY_INDICATORS = [
    ({"indicator_id": "sma", "period": 5}, "sma", "sma:5", IndicatorInputs(period=5)),
    ({"indicator_id": "ema", "period": 5}, "ema", "ema:5", IndicatorInputs(period=5)),
    ({"indicator_id": "rsi", "period": 5}, "rsi", "rsi:5", IndicatorInputs(period=5)),
    ({"indicator_id": "atr", "period": 5}, "atr", "atr:5", IndicatorInputs(period=5)),
    ({"indicator_id": "bollinger", "period": 5, "component": "upper"}, "bollinger", "bollinger:5:upper", IndicatorInputs(period=5, standard_deviations=2.0)),
    ({"indicator_id": "bollinger", "period": 5}, "bollinger", "bollinger:5:middle", IndicatorInputs(period=5, standard_deviations=2.0)),
    (
        {"indicator_id": "macd", "fast_period": 3, "slow_period": 6, "signal_period": 3, "component": "histogram"},
        "macd",
        "macd:3:6:histogram",
        IndicatorInputs(period=14, fast_period=3, slow_period=6, signal_period=3),
    ),
    (
        {"indicator_id": "stochastic-rsi", "period": 3, "fast_period": 2, "signal_period": 2},
        "stochastic-rsi",
        "stochastic-rsi:k",
        IndicatorInputs(period=3, fast_period=2, signal_period=2),
    ),
]


@pytest.mark.parametrize(("parameters", "indicator_id", "output", "inputs"), LEGACY_INDICATORS)
@pytest.mark.parametrize("condition_type", ["indicator_above", "indicator_below", "indicator_cross_above", "indicator_cross_below"])
def test_legacy_indicator_alerts_cross_the_registry_value(parameters, indicator_id, output, inputs, condition_type) -> None:
    closes = [round(100 + 8 * math.sin(index / 2.5) - index * 0.2, 2) for index in range(60)]
    bars = bars_from(closes)
    values = dict(
        next(series.points for series in compute_indicator(indicator_id, BarSeries.from_bars(bars), inputs) if series.key == output)
    )
    threshold = (D(repr(min(values.values()))) + D(repr(max(values.values())))) / 2
    alert = legacy_alert(condition_type, threshold, **parameters)
    assert alert.conditions[0].source.output == output
    above = condition_type.endswith("_above")
    fired = 0
    for index in range(1, len(bars)):
        previous = values.get(index - 1)
        current = values.get(index)
        expected = _between_polls(
            None if previous is None else D(repr(previous)), None if current is None else D(repr(current)), threshold, above
        )
        fired += expected
        assert _met(alert, bars[: index + 1]) == expected
    assert fired > 0


def test_legacy_vwap_keeps_its_bars_ago_anchor() -> None:
    alert = legacy_alert("indicator_cross_above", "16", indicator_id="vwap", anchor_bars_ago=1)
    assert alert.conditions[0].source.inputs.anchor_bars_ago == 1
    # Two-bar VWAPs: 10 at bar 0 (anchor clamped), 15 at bar 1, 25 at bar 2.
    assert not _met(alert, bars_from([10, 20], volumes=[1, 1]))
    assert _met(alert, bars_from([10, 20, 30], volumes=[1, 1, 1]))


@pytest.mark.parametrize(
    ("condition_type", "closes", "expected"),
    [
        ("trendline_crossing_up", [100, 103], True),  # line 101 -> 102
        ("trendline_crossing_up", [102, 103], False),
        ("trendline_above", [100, 103], True),  # fires on the crossing, as before
        ("trendline_above", [102, 103], False),
        ("trendline_crossing_down", [102, 101], True),
        ("trendline_below", [102, 101], True),
        ("trendline_below", [100, 99], False),
        ("trendline_crossing", [102, 101], True),
        ("trendline_crossing", [100, 103], True),
    ],
)
def test_legacy_trendline_alerts(condition_type, closes, expected) -> None:
    points = [{"time": START.isoformat(), "price": "100"}, {"time": (START + timedelta(minutes=10)).isoformat(), "price": "110"}]
    alert = legacy_alert(condition_type, 0, trendline_points=points)
    assert _met(alert, bars_from(closes)) is expected


def test_legacy_adapter_matches_the_migration_backfill_mapping() -> None:
    migration = Path("src/app/apps/trading/migrations/0141_trading_alert_conditions.sql").read_text(encoding="utf-8")
    for fragment in ("'stochastic-rsi:k'", "'vwap:dataset'", "'standard_deviations', 2.0", "WHEN 'trendline_above' THEN 'crossing_up'"):
        assert fragment in migration
    assert legacy_conditions("trendline_below", D(0), TradingAlertParameters(trendline_points=[
        {"time": START.isoformat(), "price": "1"}, {"time": (START + timedelta(minutes=1)).isoformat(), "price": "2"},
    ]))[0].operator == "crossing_down"


# --- History and keys ----------------------------------------------------------


def test_history_limit_rule() -> None:
    assert history_limit(1) == 53
    assert history_limit(40) == 170
    assert history_limit(400) == 1000
    rsi = spec(source={"kind": "indicator", "indicator_id": "rsi", "inputs": {"period": 14}, "output": "rsi:14"}, operator="greater_than", target=value(1))
    assert required_bars([rsi]) == 16  # first RSI value at bar 14, plus the previous bar
    macd = spec(
        source={"kind": "indicator", "indicator_id": "macd", "inputs": {"fast_period": 12, "slow_period": 26, "signal_period": 9}, "output": "macd:12:26:line"},
        operator="greater_than",
        target=value(1),
    )
    assert required_bars([macd]) == 36
    stoch = spec(
        source={"kind": "indicator", "indicator_id": "stochastic-rsi", "inputs": {"period": 14, "fast_period": 3, "signal_period": 3}, "output": "stochastic-rsi:k"},
        operator="greater_than",
        target=value(1),
    )
    assert required_bars([stoch]) == 35
    # The empirical warm-up covers indicators whose outputs need far more than the period.
    ribbon = IndicatorSource(kind="indicator", indicator_id="tv-moving-average-ribbon", inputs=IndicatorSourceInputs(period=20), output="tv-moving-average-ribbon:sma-200")
    assert required_bars([spec(source=ribbon.model_dump(), operator="greater_than", target=value(1))]) >= 201
    moving = spec(source={"kind": "change_percent", "lookback_bars": 10}, operator="moving_up", amount="1", bars=20)
    assert required_bars([moving]) == 30


def _outcome(bar_minute: int, close: str = "1", final: bool = False) -> AlertConditionOutcome:
    from app.apps.trading.alerts_evaluation import ConditionObservation

    return AlertConditionOutcome(
        met=True,
        bar_start=START + timedelta(minutes=bar_minute),
        bar_end=START + timedelta(minutes=bar_minute + 1),
        bar_is_final=final,
        close=D(close),
        volume=D(0),
        observations=(ConditionObservation(0, "greater_than", True, D(close)),),
    )


def test_trigger_keys_follow_the_frequency() -> None:
    def key(frequency, minute, close="1", revision=1, final=False, definition=1):
        return alert_trigger_key(
            "a", frequency, _outcome(minute, close, final), revision=revision, definition_revision=definition
        )

    assert key("once", 0) == key("once", 5, "2")
    assert key("once", 0) != key("once", 0, revision=2)  # re-enabling re-arms "once"
    assert key("once", 0) == key("once", 0, definition=2)
    for per_bar in ("once_per_bar", "once_per_bar_close"):
        assert key(per_bar, 0) == key(per_bar, 0, "2")
        assert key(per_bar, 0) != key(per_bar, 1)
        # A notification or lifecycle edit bumps the revision only: same bar, same key.
        assert key(per_bar, 0) == key(per_bar, 0, revision=5)
        assert key(per_bar, 0) != key(per_bar, 0, definition=2)
    for value_based in ("every_time", "once_per_minute"):
        assert key(value_based, 0) == key(value_based, 0)
        assert key(value_based, 0) != key(value_based, 0, "2")  # forming bar: one per value
        # A closed bar is keyed by the bar alone: a revised or re-served bar cannot fire twice.
        assert key(value_based, 0, final=True) == key(value_based, 0, "2", final=True)
        assert key(value_based, 0, final=True) != key(value_based, 1, final=True)
        assert key(value_based, 0, final=True) != key(value_based, 0)
    assert len({key(frequency, 0) for frequency in ("once", "once_per_bar", "every_time")}) == 3


def test_once_per_minute_and_cooldown_windows() -> None:
    assert frequency_allows("once_per_minute", None, START)
    assert not frequency_allows("once_per_minute", START, START + timedelta(seconds=59))
    assert frequency_allows("once_per_minute", START, START + timedelta(seconds=60))
    assert frequency_allows("every_time", START, START)
    assert not cooldown_elapsed(START, START + timedelta(seconds=59), 60)
    assert cooldown_elapsed(START, START + timedelta(seconds=60), 60)


def test_observation_payload_is_json() -> None:
    outcome = evaluate_conditions([spec(source=CLOSE, operator="crossing_up", target=value(100))], bars_from([99, 101]), final_only=False)
    payload = json.loads(json.dumps(outcome.observation_payload()))
    assert payload == [{"position": 0, "operator": "crossing_up", "met": True, "source": "101", "source_previous": "99", "target": "100", "target_previous": "100"}]


def test_kind_is_required_on_sources_and_targets() -> None:
    for data in (
        {"source": {}, "operator": "greater_than", "target": value(1)},
        {"source": {"field": "close"}, "operator": "greater_than", "target": value(1)},
        {"source": CLOSE, "operator": "greater_than", "target": {"value": "1"}},
        {"source": CLOSE, "operator": "inside_channel", "target": {"upper": value(2), "lower": value(1)}},
    ):
        with pytest.raises(ValidationError):
            spec(**data)


def test_sources_and_targets_resolve_by_kind() -> None:
    from app.apps.trading.alert_conditions import ChangePercentSource, ChannelTarget, PriceSource, SourceTarget

    assert isinstance(spec(source={"kind": "change_percent"}, operator="greater_than", target=value(1)).source, ChangePercentSource)
    assert isinstance(spec(source={"kind": "price"}, operator="greater_than", target=value(1)).source, PriceSource)
    target = spec(source=CLOSE, operator="inside_channel", target={"kind": "channel", "upper": value(2), "lower": {"kind": "source", "source": CLOSE}}).target
    assert isinstance(target, ChannelTarget) and isinstance(target.lower, SourceTarget)
    for bad in ({"kind": "price", "lookback_bars": 3}, {"kind": "volume"}):
        with pytest.raises(ValidationError):
            spec(source=bad, operator="greater_than", target=value(1))


def test_alert_ids_cannot_contain_separators() -> None:
    _create(condition_type="price_above", threshold="1")  # alert_id "a"
    for alert_id in ("x/y", "../x", "x y", "", "/x"):
        with pytest.raises(ValidationError):
            TradingAlertCreate(alert_id=alert_id, instrument_id="crypto:BINANCE:spot:BTC-USDT", condition_type="price_above", threshold="1")
    TradingAlertCreate(alert_id="chart-alert-0f1e:2.a_b", instrument_id="crypto:BINANCE:spot:BTC-USDT", condition_type="price_above", threshold="1")


def test_webhook_prefixes_never_cross_alerts_or_workspaces() -> None:
    from app.apps.trading.alerts_channels import alert_webhook_prefix

    refs = [alert_webhook_prefix(ws, alert) + "r" for ws, alert in (("w", "x"), ("w", "x.y"), ("w/x", "y"), ("w", "xy"))]
    prefix = alert_webhook_prefix("w", "x")
    assert [ref for ref in refs if ref.startswith(prefix)] == [refs[0]]
