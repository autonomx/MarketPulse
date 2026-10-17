"""Server indicator registry against the browser goldens (TVP-0.2).

The goldens are produced by ``web/src/features/trading/indicators/goldens/indicatorGoldens.test.ts``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.apps.trading.indicators.engine import CORE_INDICATOR_FORMULA_VERSION
from app.apps.trading.indicators.registry import (
    FORMULA_VERSION,
    BarSeries,
    IndicatorInputs,
    TradingSession,
    compute_indicator,
    load_compare_bars,
    server_indicator,
    server_indicator_ids,
)
from app.apps.trading.models import MarketBar

GOLDEN_ROOT = Path(__file__).resolve().parents[3] / "resources" / "trading" / "indicator_goldens"

# The server follows the browser's order of operations on the same inputs, so values must be identical.
# Only indicators that call exp/log/pow may differ, in the last bits of the platform maths library.
TRANSCENDENTAL_TOLERANCE = 1e-12

ALERT_INDICATORS = {"sma", "ema", "rsi", "macd", "bollinger", "atr", "vwap", "stochastic-rsi"}


@cache
def _index() -> dict[str, Any]:
    return json.loads((GOLDEN_ROOT / "index.json").read_text(encoding="utf-8"))


@cache
def _dataset(name: str) -> BarSeries:
    payload = json.loads((GOLDEN_ROOT / "datasets" / f"{name}.json").read_text(encoding="utf-8"))
    bars = payload["bars"]
    return BarSeries(
        start_times=tuple(datetime.fromisoformat(bar["start_time"].replace("Z", "+00:00")) for bar in bars),
        open=tuple(float(bar["open"]) for bar in bars),
        high=tuple(float(bar["high"]) for bar in bars),
        low=tuple(float(bar["low"]) for bar in bars),
        close=tuple(float(bar["close"]) for bar in bars),
        volume=tuple(float(bar["volume"]) for bar in bars),
        # MarketBar's default label, so a dataset converts like MarketBar rows do.
        sessions=tuple(bar.get("session", "regular") for bar in bars),
    )


def _session(raw: dict[str, Any] | None) -> TradingSession | None:
    if raw is None:
        return None
    return TradingSession(
        timezone=raw["timezone"],
        start_minute=raw["startMinute"],
        regular_start_minute=raw.get("regularStartMinute"),
        regular_only=raw.get("regularOnly", False),
    )


def _golden(indicator_id: str) -> dict[str, Any]:
    return json.loads((GOLDEN_ROOT / "indicators" / f"{indicator_id}.json").read_text(encoding="utf-8"))


def _inputs(raw: dict[str, Any]) -> IndicatorInputs:
    return IndicatorInputs(
        period=raw["period"],
        fast_period=raw.get("fastPeriod"),
        slow_period=raw.get("slowPeriod"),
        signal_period=raw.get("signalPeriod"),
        standard_deviations=raw.get("standardDeviations"),
        anchor_time=raw.get("anchorTime"),
        compare_symbol=raw.get("compareSymbol"),
        params=raw.get("params", {}),
        session=_session(raw.get("session")),
    )


def _same(expected: float | None, actual: float, transcendental: bool) -> bool:
    if expected is None:
        return not math.isfinite(actual)
    # JSON integers (JS prints values below 1e21 without an exponent) must compare as the double they denote.
    expected = float(expected)
    if transcendental:
        return math.isclose(expected, actual, rel_tol=TRANSCENDENTAL_TOLERANCE, abs_tol=TRANSCENDENTAL_TOLERANCE)
    return actual == expected


def test_goldens_match_the_server_formula_version() -> None:
    assert _index()["formula_version"] == FORMULA_VERSION == CORE_INDICATOR_FORMULA_VERSION


def test_server_coverage_only_grows() -> None:
    recorded = json.loads((GOLDEN_ROOT / "server_coverage.json").read_text(encoding="utf-8"))
    assert set(recorded) <= set(server_indicator_ids()), "an indicator lost its server implementation"


def test_whole_number_float_periods_behave_like_integers() -> None:
    bars = _dataset("random-walk-300")
    as_int = compute_indicator("sma", bars, IndicatorInputs(period=20))
    as_float = compute_indicator("sma", bars, IndicatorInputs(period=20.0))
    assert as_float == as_int
    assert as_float[0].key == "sma:20"


PATHOLOGICAL_SERIES = {
    "zero-crossing": [0.0, 5.0, -5.0] * 10,
    "extreme-magnitudes": [1e300, 1e-300] * 15,
    "subnormal": [5e-324, 1e-320, 0.0] * 10,
    "single-bar": [1.0],
}


@pytest.mark.parametrize("series_name", sorted(PATHOLOGICAL_SERIES))
def test_indicators_return_non_finite_values_instead_of_raising(series_name: str) -> None:
    values = PATHOLOGICAL_SERIES[series_name]
    times = tuple(datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(hours=index) for index in range(len(values)))
    volume = tuple(-1.0 if index % 3 == 2 else 1.0 for index in range(len(values)))
    bars = BarSeries(times, tuple(values), tuple(values), tuple(values), tuple(values), volume)
    for indicator_id in server_indicator_ids():
        for inputs in (
            IndicatorInputs(period=2, compare_symbol="pathological"),
            IndicatorInputs(period=14, fast_period=3, slow_period=5, signal_period=2, compare_symbol="pathological"),
        ):
            try:
                compute_indicator(indicator_id, bars, inputs, bars)
            except ValueError as error:
                assert "must be" in str(error), f"{indicator_id} {series_name}: {error}"


def test_compare_series_indicators_align_the_second_series_by_time() -> None:
    indicator = server_indicator("tv-correlation-coefficient-cc")
    assert indicator is not None and indicator.uses_compare_series
    bars = _dataset("random-walk-300")
    with_symbol = IndicatorInputs(period=20, compare_symbol="golden:compare")
    # Itself, in reverse order: the series is sorted and aligned by start time, so every window correlates perfectly.
    reversed_bars = BarSeries(*(tuple(reversed(column)) for column in (bars.start_times, bars.open, bars.high, bars.low, bars.close, bars.volume)))
    [series] = compute_indicator(indicator.id, bars, with_symbol, reversed_bars)
    assert len(series.points) == len(bars) - 19
    assert all(math.isclose(value, 1.0, rel_tol=1e-12) for _, value in series.points)
    # Without a second series, or without a symbol naming one, there is nothing to plot; other indicators ignore it.
    assert compute_indicator(indicator.id, bars, with_symbol)[0].points == ()
    assert compute_indicator(indicator.id, bars, IndicatorInputs(period=20), bars)[0].points == ()
    assert indicator.compute(bars, with_symbol)[0].points == ()
    assert compute_indicator("sma", bars, IndicatorInputs(period=20), bars) == compute_indicator("sma", bars, IndicatorInputs(period=20))


def test_load_compare_bars_covers_the_chart_time_range() -> None:
    bars = _dataset("random-walk-300")
    compare = json.loads((GOLDEN_ROOT / "datasets" / "compare-walk-291.json").read_text(encoding="utf-8"))["bars"]
    rows = [MarketBar(instrument_id="golden:compare", interval="1h", provider="golden", **bar) for bar in compare]
    requests: list[tuple[str, int]] = []

    def fetch(instrument_id: str, limit: int) -> list[MarketBar]:
        requests.append((instrument_id, limit))
        return rows[-limit:]

    loaded = load_compare_bars(fetch, "golden:compare", bars)
    # 299 hourly gaps across the chart, so 300 bars: the chart's time range, not its bar count or a fixed size.
    assert requests == [("golden:compare", 300)]
    assert loaded is not None and loaded.close == tuple(float(bar["close"]) for bar in compare[-300:])
    assert load_compare_bars(fetch, "golden:compare", bars, max_bars=50) is not None and requests[-1] == ("golden:compare", 50)
    assert load_compare_bars(fetch, None, bars) is None
    assert load_compare_bars(fetch, "golden:compare", _dataset("empty-0")) is None
    inputs = IndicatorInputs(period=20, compare_symbol="golden:compare")
    assert compute_indicator("tv-correlation-coefficient-cc", bars, inputs, loaded)[0].points


def test_sessions_follow_the_exchange_calendar_across_daylight_saving() -> None:
    from app.apps.trading.indicators.registry import session_for_instrument
    from app.apps.trading.indicators.server_indicators._sessions import session_clock, session_periods

    equity = session_for_instrument("equity", "XNYS", "America/New_York", "equity")
    assert equity == TradingSession("America/New_York", 0, 570, True)
    assert session_for_instrument("crypto", "24x7") == TradingSession()
    # 13:30 UTC is 08:30 EST before 2026-03-08 and 09:30 EDT after; the 22:00 UTC futures bar belongs to the next day.
    starts = ["2026-03-06T14:30:00+00:00", "2026-03-09T13:30:00+00:00", "2026-03-09T22:00:00+00:00"]
    times = tuple(datetime.fromisoformat(start) for start in starts)
    bars = BarSeries(times, (1.0,) * 3, (1.0,) * 3, (1.0,) * 3, (1.0,) * 3, (1.0,) * 3, ("regular", "regular", "extended_post"))
    clock = session_clock(bars, equity)
    assert [offset // 60_000 for offset in clock.offset] == [570, 570, 1080]
    assert clock.counts == [True, True, False]
    assert session_periods(clock, 1, equity)[1] == [0, 0, 1_800_000]
    futures = session_clock(bars, session_for_instrument("commodity"))
    assert futures.day[2] == futures.day[1] + 1
    weeks = session_periods(clock, "W", equity)[0]
    assert weeks[0] != weeks[1]


def test_catalog_instruments_get_their_market_session() -> None:
    from app.apps.trading.catalog import all_instruments
    from app.apps.trading.indicators.registry import UTC_SESSION, session_for_instrument

    by_class = {}
    for instrument in all_instruments():
        by_class.setdefault(instrument.asset_class.value, instrument)
    commodity = by_class["commodity"]
    assert commodity.session_calendar == "24x7"  # the catalog tags futures 24x7, which must not win
    session = session_for_instrument(commodity.asset_class.value, commodity.session_calendar, commodity.exchange_timezone, commodity.instrument_type.value)
    assert (session.timezone, session.start_minute) == ("America/New_York", 1080)
    crypto = by_class["crypto"]
    assert session_for_instrument(crypto.asset_class.value, crypto.session_calendar, crypto.exchange_timezone, crypto.instrument_type.value) == UTC_SESSION


def test_params_accept_a_mapping_and_stay_hashable() -> None:
    inputs = IndicatorInputs(period=14, params={"upper": 50, "lower": 50})
    assert inputs.params == (("lower", 50), ("upper", 50))
    assert inputs.param("upper") == 50 and inputs.param("missing") is None
    assert hash(inputs) == hash(IndicatorInputs(period=14, params=(("upper", 50), ("lower", 50))))


def test_market_bars_convert_like_the_golden_datasets() -> None:
    payload = json.loads((GOLDEN_ROOT / "datasets" / "gaps-flats-zero-volume-160.json").read_text(encoding="utf-8"))
    bars = [MarketBar(instrument_id="golden:test", interval="1h", provider="golden", **bar) for bar in payload["bars"]]
    assert BarSeries.from_bars(bars) == _dataset("gaps-flats-zero-volume-160")


def test_every_server_indicator_is_a_known_chart_indicator() -> None:
    known = {item["id"] for item in _index()["indicators"]}
    assert set(server_indicator_ids()) <= known


def test_alert_indicators_have_server_implementations() -> None:
    assert ALERT_INDICATORS <= set(server_indicator_ids())


@pytest.mark.parametrize("indicator_id", server_indicator_ids())
def test_server_indicator_matches_browser_goldens(indicator_id: str) -> None:
    indicator = server_indicator(indicator_id)
    assert indicator is not None
    transcendental = indicator.numeric_class == "transcendental"
    golden = _golden(indicator_id)
    failures: list[str] = []
    for case in golden["cases"]:
        bars = _dataset(case["dataset"])
        compare = _dataset(case["compare_dataset"]) if "compare_dataset" in case else None
        inputs = _inputs(case["inputs"])
        label = f"{indicator_id} {case['case_id']}"
        if case.get("error"):
            with pytest.raises(ValueError):
                compute_indicator(indicator_id, bars, inputs, compare)
            continue
        actual = {series.key: series.points for series in compute_indicator(indicator_id, bars, inputs, compare)}
        expected = {output["key"]: output["points"] for output in case["outputs"]}
        if list(actual) != list(expected):
            failures.append(f"{label}: output keys {list(actual)} != {list(expected)}")
            continue
        for key, expected_points in expected.items():
            actual_points = actual[key]
            if [index for index, _ in actual_points] != [index for index, _ in expected_points]:
                failures.append(f"{label} {key}: points at different bars ({len(actual_points)} vs {len(expected_points)})")
                continue
            for (index, value), (_, expected_value) in zip(actual_points, expected_points, strict=True):
                if not _same(expected_value, value, transcendental):
                    failures.append(f"{label} {key} bar {index}: {value!r} != {expected_value!r}")
                    break
    assert not failures, "\n".join(failures[:20])
