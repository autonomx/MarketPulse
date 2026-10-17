"""Indicator on indicator (TVP-6.5): the server computes it like the browser.

The fixture is produced by ``web/src/features/trading/indicators/goldens/indicatorSourceGoldens.test.ts``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.apps.trading.alert_conditions import (
    AlertConditionSpec,
    IndicatorOutputRef,
    IndicatorSource,
    IndicatorSourceInputs,
    compute_source_indicator,
    validate_conditions_against_registry,
)
from app.apps.trading.alerts_evaluation import required_bars
from app.apps.trading.indicators.registry import BarSeries
from app.apps.trading.indicators.sources import SOURCE_TARGET_IDS

GOLDEN_ROOT = Path(__file__).resolve().parents[3] / "resources" / "trading" / "indicator_goldens"
FIXTURE = json.loads((GOLDEN_ROOT / "sources.json").read_text(encoding="utf-8"))


def _dataset(name: str) -> BarSeries:
    bars = json.loads((GOLDEN_ROOT / "datasets" / f"{name}.json").read_text(encoding="utf-8"))["bars"]
    return BarSeries(
        start_times=tuple(datetime.fromisoformat(bar["start_time"].replace("Z", "+00:00")) for bar in bars),
        open=tuple(float(bar["open"]) for bar in bars),
        high=tuple(float(bar["high"]) for bar in bars),
        low=tuple(float(bar["low"]) for bar in bars),
        close=tuple(float(bar["close"]) for bar in bars),
        volume=tuple(float(bar["volume"]) for bar in bars),
        sessions=tuple(bar.get("session", "regular") for bar in bars),
    )


def _inputs(raw: dict[str, Any], source: IndicatorOutputRef | None = None) -> IndicatorSourceInputs:
    return IndicatorSourceInputs(
        period=raw["period"],
        fast_period=raw.get("fastPeriod"),
        slow_period=raw.get("slowPeriod"),
        signal_period=raw.get("signalPeriod"),
        standard_deviations=raw.get("standardDeviations"),
        source=source,
    )


def _source_inputs(case: dict[str, Any]) -> IndicatorSourceInputs:
    source = case["source"]
    reference = IndicatorOutputRef(indicator_id=source["id"], inputs=_inputs(source["inputs"]), output=source["output"])
    return _inputs(case["target"]["inputs"], reference)


def test_the_browser_and_server_take_sources_on_the_same_indicators() -> None:
    assert set(FIXTURE["targets"]) == SOURCE_TARGET_IDS


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: f"{case['target']['id']}-of-{case['source']['id']}")
def test_server_matches_the_browser(case: dict[str, Any]) -> None:
    outputs = compute_source_indicator(case["target"]["id"], _dataset(case["dataset"]), _source_inputs(case))
    actual = {output.key: list(output.points) for output in outputs}
    assert sorted(actual) == sorted(output["key"] for output in case["outputs"])
    for expected in case["outputs"]:
        points = actual[expected["key"]]
        assert [index for index, _ in points] == [index for index, _ in expected["points"]], expected["key"]
        for (_, value), (_, want) in zip(points, expected["points"], strict=True):
            if want is None:
                assert not math.isfinite(value)
            else:
                # RSI, MFI and the like call exp/log/pow; allow the last bits of the platform maths library.
                assert math.isclose(value, float(want), rel_tol=1e-12, abs_tol=1e-12), expected["key"]


def test_an_alert_can_read_an_indicator_on_another() -> None:
    case = FIXTURE["cases"][0]
    source = IndicatorSource(kind="indicator", indicator_id=case["target"]["id"], inputs=_source_inputs(case), output=case["outputs"][0]["key"])
    spec = AlertConditionSpec(source=source, operator="crossing_up", target={"kind": "value", "value": "50"})
    validate_conditions_against_registry([spec])
    # SMA(10) of RSI(14): the RSI's warm-up and then the SMA's.
    assert required_bars([spec]) >= 14 + 10


def test_sources_are_one_level_and_only_on_indicators_that_take_one() -> None:
    rsi = IndicatorOutputRef(indicator_id="rsi", inputs=IndicatorSourceInputs(period=14), output="rsi:14")
    with pytest.raises(ValueError, match="cannot itself read another"):
        IndicatorSourceInputs(period=10, source=IndicatorOutputRef(indicator_id="sma", inputs=IndicatorSourceInputs(period=5, source=rsi), output="sma:5"))
    atr_of_rsi = IndicatorSource(kind="indicator", indicator_id="atr", inputs=IndicatorSourceInputs(period=14, source=rsi), output="atr:14")
    with pytest.raises(ValueError, match="does not take another indicator"):
        validate_conditions_against_registry([AlertConditionSpec(source=atr_of_rsi, operator="greater_than", target={"kind": "value", "value": "1"})])
    missing = IndicatorSource(
        kind="indicator", indicator_id="sma", inputs=IndicatorSourceInputs(period=10, source=rsi.model_copy(update={"output": "rsi:99"})), output="sma:10"
    )
    with pytest.raises(ValueError, match="has no output"):
        validate_conditions_against_registry([AlertConditionSpec(source=missing, operator="greater_than", target={"kind": "value", "value": "1"})])


def test_a_source_line_is_found_by_name_when_its_inputs_change() -> None:
    from app.apps.trading.indicators.registry import IndicatorOutputSeries
    from app.apps.trading.indicators.sources import find_output

    outputs = [IndicatorOutputSeries("macd:10:26:line", ()), IndicatorOutputSeries("macd:10:26:histogram", ())]
    assert find_output(outputs, "macd:12:26:histogram").key == "macd:10:26:histogram"
    assert find_output(outputs, "macd:10:26:line").key == "macd:10:26:line"
    assert find_output([], "rsi:14") is None
