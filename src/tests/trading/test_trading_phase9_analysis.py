from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.apps.trading.indicators.engine import (
    CORE_INDICATOR_FORMULA_VERSION,
    anchored_volume_weighted_average_price,
    average_true_range,
    bollinger_bands,
    moving_average_convergence_divergence,
    stochastic_rsi,
)


FIXTURE = json.loads(
    Path("web/src/features/trading/indicators/fixtures/advancedIndicators.json").read_text()
)


def assert_close(actual, expected, tolerance=Decimal("0.000000001")):
    assert len(actual) == len(expected)
    for left, right in zip(actual, expected):
        assert abs(Decimal(left) - Decimal(str(right))) <= tolerance


def test_advanced_formula_version_and_bollinger_parity() -> None:
    assert CORE_INDICATOR_FORMULA_VERSION == FIXTURE["formulaVersion"]
    result = bollinger_bands(
        FIXTURE["closes"],
        FIXTURE["bollinger"]["period"],
        FIXTURE["bollinger"]["deviations"],
    )
    assert_close([item[0] for item in result], FIXTURE["bollinger"]["middle"])
    assert_close([item[1] for item in result], FIXTURE["bollinger"]["upper"])
    assert_close([item[2] for item in result], FIXTURE["bollinger"]["lower"])


def test_atr_macd_and_vwap_match_the_shared_fixture() -> None:
    assert_close(
        average_true_range(
            FIXTURE["highs"], FIXTURE["lows"], FIXTURE["closes"], FIXTURE["atr"]["period"]
        ),
        FIXTURE["atr"]["values"],
    )
    macd = moving_average_convergence_divergence(
        FIXTURE["closes"],
        FIXTURE["macd"]["fast"],
        FIXTURE["macd"]["slow"],
        FIXTURE["macd"]["signalPeriod"],
    )
    assert_close([item[0] for item in macd], FIXTURE["macd"]["line"])
    assert_close([item[1] for item in macd], FIXTURE["macd"]["signal"])
    assert_close([item[2] for item in macd], FIXTURE["macd"]["histogram"])
    assert_close(
        anchored_volume_weighted_average_price(
            FIXTURE["highs"], FIXTURE["lows"], FIXTURE["closes"], FIXTURE["volumes"]
        ),
        FIXTURE["vwap"],
    )


def test_stochastic_rsi_is_bounded_and_flat_series_is_neutral() -> None:
    values = stochastic_rsi(range(40), period=5, smoothing=3, signal=3)
    assert values
    assert all(Decimal("0") <= value <= Decimal("100") for pair in values for value in pair)
    assert stochastic_rsi([100] * 40, period=14, smoothing=3, signal=3)[-1] == (
        Decimal("50"),
        Decimal("50"),
    )
