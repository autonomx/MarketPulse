from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.apps.trading.indicator_signals import IndicatorSnapshot, MultiTimeframeIndicatorContext
from app.apps.trading.strategy_adaptive_exit_research import adaptive_exit_deterioration


def _snapshot(
    interval: str,
    *,
    close: str = "10",
    ema9: str = "9.8",
    ema20: str = "9.5",
    ema9_rising: bool | None = True,
    macd_bullish: bool | None = True,
    histogram: str | None = "0.1",
    k: str | None = "70",
    d: str | None = "60",
) -> IndicatorSnapshot:
    close_d = Decimal(close)
    ema9_d = Decimal(ema9)
    ema20_d = Decimal(ema20)
    return IndicatorSnapshot(
        interval=interval,  # type: ignore[arg-type]
        as_of=datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc),
        bar_count=100,
        close=close_d,
        ema9=ema9_d,
        ema20=ema20_d,
        ema9_change=Decimal("0.01") if ema9_rising else Decimal("-0.01") if ema9_rising is False else None,
        macd=Decimal("0.2") if macd_bullish else Decimal("-0.2") if macd_bullish is False else None,
        macd_signal=Decimal("0.1") if macd_bullish else Decimal("-0.1") if macd_bullish is False else None,
        macd_histogram=Decimal(histogram) if histogram is not None else None,
        stochastic_rsi_k=Decimal(k) if k is not None else None,
        stochastic_rsi_d=Decimal(d) if d is not None else None,
        price_above_ema9=close_d > ema9_d,
        ema9_above_ema20=ema9_d > ema20_d,
        ema9_rising=ema9_rising,
        macd_bullish=macd_bullish,
        stochastic_rsi_bullish=(Decimal(k) >= Decimal(d)) if k is not None and d is not None else None,
    )


def _context(one: IndicatorSnapshot, five: IndicatorSnapshot) -> MultiTimeframeIndicatorContext:
    return MultiTimeframeIndicatorContext(
        source_bar_count=200,
        session_date="2026-01-02",
        one_minute=one,
        five_minute=five,
    )


def test_overbought_stoch_is_not_an_exit_while_trend_is_intact() -> None:
    previous = _context(
        _snapshot("1m", k="92", d="88"),
        _snapshot("5m", k="91", d="87"),
    )
    current = _context(
        _snapshot("1m", k="95", d="90"),
        _snapshot("5m", k="93", d="89"),
    )

    decision = adaptive_exit_deterioration(current, previous)

    assert decision.exit is False
    assert decision.reason_codes == ()


def test_one_minute_noise_cannot_exit_without_five_minute_break() -> None:
    previous = _context(_snapshot("1m", k="90", d="85"), _snapshot("5m"))
    current = _context(
        _snapshot(
            "1m",
            close="9.4",
            ema9="9.6",
            ema20="9.5",
            ema9_rising=False,
            macd_bullish=False,
            histogram="-0.2",
            k="70",
            d="75",
        ),
        _snapshot("5m"),
    )

    decision = adaptive_exit_deterioration(current, previous)

    assert decision.one_minute_warning_count == 3
    assert decision.five_minute_trend_break is False
    assert decision.exit is False


def test_five_minute_break_plus_two_one_minute_warnings_exits() -> None:
    previous = _context(_snapshot("1m", k="70", d="65"), _snapshot("5m"))
    current = _context(
        _snapshot(
            "1m",
            close="9.4",
            ema9="9.6",
            ema20="9.5",
            ema9_rising=False,
            macd_bullish=False,
            histogram="-0.2",
            k="55",
            d="50",
        ),
        _snapshot(
            "5m",
            close="9.4",
            ema9="9.6",
            ema20="9.2",
            ema9_rising=False,
        ),
    )

    decision = adaptive_exit_deterioration(current, previous)

    assert decision.exit is True
    assert decision.five_minute_trend_break is True
    assert decision.one_minute_warning_count == 2
    assert "ADAPTIVE_EXIT_5M_TREND_BREAK" in decision.reason_codes
    assert "ADAPTIVE_EXIT_1M_EMA_WEAKNESS" in decision.reason_codes
    assert "ADAPTIVE_EXIT_1M_MACD_BEARISH" in decision.reason_codes


def test_overbought_cross_down_counts_as_tactical_warning() -> None:
    previous = _context(
        _snapshot("1m", k="90", d="85"),
        _snapshot("5m"),
    )
    current = _context(
        _snapshot(
            "1m",
            close="9.7",
            ema9="9.6",
            ema20="9.5",
            ema9_rising=True,
            macd_bullish=False,
            histogram="-0.1",
            k="70",
            d="75",
        ),
        _snapshot(
            "5m",
            close="9.4",
            ema9="9.6",
            ema20="9.2",
            ema9_rising=False,
        ),
    )

    decision = adaptive_exit_deterioration(current, previous)

    assert decision.exit is True
    assert decision.one_minute_warning_count == 2
    assert "ADAPTIVE_EXIT_1M_STOCH_OVERBOUGHT_CROSS_DOWN" in decision.reason_codes


def test_strong_five_minute_break_needs_only_one_tactical_warning() -> None:
    previous = _context(_snapshot("1m"), _snapshot("5m"))
    current = _context(
        _snapshot(
            "1m",
            close="9.7",
            ema9="9.8",
            ema20="9.5",
            ema9_rising=False,
            macd_bullish=True,
            histogram="0.1",
        ),
        _snapshot(
            "5m",
            close="9.0",
            ema9="9.4",
            ema20="9.2",
            ema9_rising=False,
            macd_bullish=True,
        ),
    )

    decision = adaptive_exit_deterioration(current, previous)

    assert decision.exit is True
    assert decision.one_minute_warning_count == 1
    assert decision.five_minute_strong_break is True
    assert "ADAPTIVE_EXIT_5M_PRICE_BELOW_EMA20" in decision.reason_codes
