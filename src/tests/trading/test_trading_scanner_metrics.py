"""Screener metrics and columns (TVP-9.1)."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.apps.trading.scanner import evaluate_scanner_dataset

from .test_trading_phase11_scanner import definition, response

SYMBOL = "crypto:FIXTURE:spot:AAA-USD"


def _rule(rule_id: str, metric: str, **fields) -> dict:
    return {"rule_id": rule_id, "metric": metric, "operator": "gt", "threshold": "-1000", "period": 20, "lookback_bars": 1, **fields}


def test_new_metrics_compute_on_the_bars() -> None:
    # Fixture bars: close 100..129 rising by 1, high = close + 1, low = close - 1, open = close, volume 1000..1029.
    scanner = definition([SYMBOL], rules=[
        _rule("rvol", "relative_volume"),
        _rule("gap", "gap_percent"),
        _rule("high", "high_distance_percent"),
        _rule("low", "low_distance_percent"),
    ])
    result = evaluate_scanner_dataset(scanner, "run", response(SYMBOL), None)
    assert result is not None
    metrics = result.metrics
    # Against the 20 bars before the last one.
    assert metrics["rule:rvol"] == Decimal(1029) / (sum(Decimal(v) for v in range(1009, 1029)) / 20)
    assert metrics["rule:gap"] == (Decimal(129) / Decimal(128) - 1) * 100
    assert metrics["rule:high"] == (Decimal(129) / Decimal(130) - 1) * 100
    assert metrics["rule:low"] == (Decimal(129) / Decimal(109) - 1) * 100


def test_an_indicator_rule_filters_on_a_registry_indicator_line() -> None:
    rsi = {"kind": "indicator", "indicator_id": "rsi", "inputs": {"period": 14}, "output": "rsi:14"}
    rising = definition([SYMBOL], rules=[_rule("rsi", "indicator", source=rsi, operator="gt", threshold="70")])
    result = evaluate_scanner_dataset(rising, "run", response(SYMBOL), None)
    assert result is not None and result.metrics["rule:rsi"] > 70
    assert result.metrics["indicator:rsi:14"] == result.metrics["rule:rsi"]
    falling = definition([SYMBOL], rules=[_rule("rsi", "indicator", source=rsi, operator="lt", threshold="30")])
    assert evaluate_scanner_dataset(falling, "run", response(SYMBOL), None) is None


def test_columns_are_shown_but_never_filter() -> None:
    scanner = definition([SYMBOL], rules=[
        _rule("up", "percent_change", operator="gt", threshold="0", lookback_bars=5),
        _rule("vol", "volume", role="column", operator="lt", threshold="0"),
    ])
    result = evaluate_scanner_dataset(scanner, "run", response(SYMBOL), None)
    assert result is not None
    assert result.matched_rules == ["up"]
    assert result.metrics["rule:vol"] == Decimal(1029)


def test_rules_are_validated() -> None:
    with pytest.raises(ValidationError, match="indicator rule needs a source"):
        definition([SYMBOL], rules=[_rule("x", "indicator")])
    with pytest.raises(ValidationError, match="no output"):
        definition([SYMBOL], rules=[_rule("x", "indicator", source={"kind": "indicator", "indicator_id": "rsi", "inputs": {"period": 14}, "output": "rsi:99"})])
    with pytest.raises(ValidationError, match="at least one filter"):
        definition([SYMBOL], rules=[_rule("x", "close", role="column")])
    with pytest.raises(ValidationError, match="unique"):
        definition([SYMBOL], rules=[_rule("x", "close"), _rule("x", "volume")])
