from __future__ import annotations

import json
from pathlib import Path

from app.apps.trading.indicators.engine import (
    CORE_INDICATOR_FORMULA_VERSION,
    exponential_moving_average,
    relative_strength_index,
    simple_moving_average,
)


FIXTURE = Path(__file__).resolve().parents[3] / "web/src/features/trading/indicators/fixtures/coreIndicators.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def assert_close(actual, expected, tolerance=1e-10):
    assert len(actual) == len(expected)
    for left, right in zip(actual, expected):
        assert abs(float(left) - float(right)) <= tolerance


def test_python_sma_ema_rsi_match_shared_typescript_fixture() -> None:
    fixture = load_fixture()
    assert CORE_INDICATOR_FORMULA_VERSION == fixture["formulaVersion"]
    assert_close(simple_moving_average(fixture["closes"], fixture["sma"]["period"]), fixture["sma"]["values"])
    assert_close(exponential_moving_average(fixture["closes"], fixture["ema"]["period"]), fixture["ema"]["values"])
    assert_close(relative_strength_index(fixture["closes"], fixture["rsi"]["period"]), fixture["rsi"]["values"])


def test_indicator_warmup_and_invalid_period_behavior() -> None:
    assert simple_moving_average([1, 2], 3) == []
    assert relative_strength_index([1, 2, 3], 3) == []
    try:
        exponential_moving_average([1], 0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("invalid period must fail")
