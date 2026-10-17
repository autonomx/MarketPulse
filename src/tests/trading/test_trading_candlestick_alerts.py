"""Alerts on candlestick patterns (TVP-6.3): every pattern output is a server alert source."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.apps.trading.alert_conditions import AlertConditionSpec, validate_conditions_against_registry
from app.apps.trading.alerts_evaluation import evaluate_conditions, required_bars, validate_conditions_can_fire
from app.apps.trading.indicators.server_indicators.candlestick_patterns import PATTERN_KEYS, SIGNAL_WARMUP

INDICATOR = "tv-all-candlestick-patterns"
# The web dialog's "Appears": the output greater than this (alertIndicatorSources.ts APPEARS_VALUE).
APPEARS = "-1000000000000000000"
START = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)


def appears(pattern: str) -> AlertConditionSpec:
    return AlertConditionSpec.model_validate({
        "source": {"kind": "indicator", "indicator_id": INDICATOR, "inputs": {"period": 1}, "output": f"{INDICATOR}:{pattern}"},
        "operator": "greater_than",
        "target": {"kind": "value", "value": APPEARS},
    })


@pytest.mark.parametrize("pattern", PATTERN_KEYS)
def test_every_pattern_output_can_be_an_alert(pattern: str) -> None:
    conditions = [appears(pattern)]
    validate_conditions_against_registry(conditions)
    validate_conditions_can_fire(conditions)
    # The declared warm-up (SMA50 and four bars of lookback), not the first bar a pattern happens to appear on.
    assert required_bars(conditions) == SIGNAL_WARMUP + 1


def bars(count: int, *, last_doji: bool):
    result = []
    for index in range(count):
        close = Decimal(100 + index % 7)
        doji = last_doji and index == count - 1
        result.append(SimpleNamespace(
            start_time=START + timedelta(minutes=index),
            end_time=START + timedelta(minutes=index + 1),
            # A doji: no body, equal shadows. Other bars have a body of 1 and short shadows.
            open=close if doji else close - 1,
            high=close + 1 if doji else close + Decimal("0.1"),
            low=close - 1 if doji else close - Decimal("1.1"),
            close=close,
            volume=Decimal("1000"),
            is_final=True,
        ))
    return result


def test_an_appears_alert_fires_on_the_bar_the_pattern_completes_on_only() -> None:
    condition = appears("doji")
    fired = evaluate_conditions([condition], bars(80, last_doji=True), final_only=True)
    quiet = evaluate_conditions([condition], bars(80, last_doji=False), final_only=True)
    assert fired is not None and fired.met is True
    assert quiet is not None and quiet.met is False


def test_an_unknown_pattern_output_is_rejected() -> None:
    with pytest.raises(ValueError, match="has no output"):
        validate_conditions_against_registry([appears("no-such-pattern")])
