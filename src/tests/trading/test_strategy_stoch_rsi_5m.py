from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.models import MarketBar
from app.apps.trading import strategy_stoch_rsi_5m as strategy
from app.apps.trading.strategies.models import StochRsi5mConfig


START = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)  # 09:30 ET
HISTORY_START = datetime(2026, 9, 9, 13, 30, tzinfo=timezone.utc)


def _bar(
    index: int,
    *,
    open_: str = "10",
    close: str = "10",
    high_: str | None = None,
    low_: str | None = None,
) -> MarketBar:
    start = START + timedelta(minutes=5 * index)
    open_value = Decimal(open_)
    close_value = Decimal(close)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=open_value,
        high=Decimal(high_) if high_ is not None else max(open_value, close_value),
        low=Decimal(low_) if low_ is not None else min(open_value, close_value),
        close=close_value,
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=5),
    )


def _with_ema_history(bars: list[MarketBar], count: int = 50) -> list[MarketBar]:
    close_value = Decimal("10")
    history = []
    for index in range(count):
        start = HISTORY_START + timedelta(minutes=5 * index)
        history.append(
            MarketBar(
                instrument_id="equity:NASDAQ:TEST",
                interval="5m",
                start_time=start,
                end_time=start + timedelta(minutes=5),
                open=close_value,
                high=close_value,
                low=close_value,
                close=close_value,
                volume=Decimal("1000"),
                is_final=True,
                session="regular",
                provider="fixture",
                received_at=start + timedelta(minutes=5),
            )
        )
    return [*history, *bars]


def _indicator_values(
    values: list[Decimal],
    k_tail: list[str],
    d_tail: list[str],
) -> tuple[list[Decimal], list[Decimal]]:
    prefix = len(values) - len(k_tail)
    return [
        *([Decimal("50")] * prefix),
        *(Decimal(value) for value in k_tail),
    ], [
        *([Decimal("50")] * (len(values) - len(d_tail))),
        *(Decimal(value) for value in d_tail),
    ]


def test_buys_on_oversold_cross_up_and_exits_on_overbought_cross_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [
        _bar(0),
        _bar(1, open_="9.8", close="10"),
        _bar(2, open_="10", close="10.2"),
        _bar(3, open_="10.20", close="10.3"),
        _bar(4, close="10.4"),
        _bar(5, close="10.5"),
        _bar(6, open_="10.80", close="10.6"),
    ]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["5", "9", "22", "50", "97", "96", "96"],
            ["8", "7", "15", "50", "94", "98", "97"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "exited"
    assert snapshot.reason_code == "STOCH_RSI_5M_OVERBOUGHT_CROSS_DOWN"
    assert snapshot.oversold_arm_time == signal_bars[0].end_time
    assert snapshot.momentum_cross_time == signal_bars[1].end_time
    assert snapshot.entry_signal_time == signal_bars[2].end_time
    assert snapshot.entry_time == signal_bars[3].start_time
    assert snapshot.entry_price == Decimal("10.20")
    assert snapshot.exit_signal_time == signal_bars[5].end_time
    assert snapshot.exit_time == signal_bars[6].start_time
    assert snapshot.exit_price == Decimal("10.80")
    assert snapshot.return_pct == Decimal("0.60") / Decimal("10.20") * Decimal("100")
    assert snapshot.execution_authority is False


def test_allows_a_second_trade_after_the_first_trade_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [
        _bar(0),
        _bar(1, open_="9.8", close="10"),
        _bar(2, open_="10", close="10.2"),
        _bar(3, open_="10.2", close="10.3"),
        _bar(4, open_="10.3", close="10.4"),
        _bar(5, open_="10.4", close="10.5"),
        _bar(6, open_="10.6", close="10.6"),
        _bar(7, open_="10.6", close="10.7"),
        _bar(8, open_="10.7", close="10.8"),
        _bar(9, open_="10.8", close="11"),
        _bar(10, open_="11", close="11.1"),
        _bar(11, open_="11.1", close="11.2"),
        _bar(12, open_="11.2", close="11.3"),
        _bar(13, open_="11.4", close="11.4"),
    ]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["5", "9", "22", "40", "70", "75", "75", "5", "9", "22", "40", "70", "97", "97"],
            ["8", "7", "15", "35", "65", "80", "78", "8", "7", "15", "35", "65", "98", "97"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "exited"
    assert len(snapshot.trades) == 2
    assert snapshot.trades[0].entry_time == signal_bars[3].start_time
    assert snapshot.trades[0].exit_time == signal_bars[6].start_time
    assert snapshot.trades[0].exit_reason_code == "STOCH_RSI_5M_CROSS_DOWN_BELOW_80"
    assert snapshot.trades[1].entry_time == signal_bars[10].start_time
    assert snapshot.trades[1].exit_time == signal_bars[13].start_time
    assert snapshot.trades[1].exit_reason_code == "STOCH_RSI_5M_OVERBOUGHT_CROSS_DOWN"
    assert snapshot.entry_time == signal_bars[10].start_time
    assert snapshot.exit_time == signal_bars[13].start_time


def test_signal_without_next_bar_is_armed_and_not_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [
        _bar(0),
        _bar(1, open_="9.8", close="10"),
        _bar(2, open_="10", close="10.2"),
    ]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["5", "9", "22"],
            ["8", "7", "15"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "entry_armed"
    assert snapshot.reason_code == "STOCH_RSI_5M_RECOVERY_20_CONFIRMED"
    assert snapshot.entry_signal_time == signal_bars[2].end_time
    assert snapshot.entry_time is None
    assert snapshot.entry_price is None


def test_oversold_arm_threshold_is_strictly_below_eighteen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _with_ema_history([_bar(0), _bar(1, open_="9.8", close="10")])
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["18", "25"],
            ["20", "20"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "waiting_oversold"
    assert snapshot.reason_code == "STOCH_RSI_5M_WAITING_OVERSOLD_ARM"


def test_waits_for_recovery_above_twenty_after_momentum_cross(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [_bar(0), _bar(1), _bar(2)]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["5", "9", "15"],
            ["8", "7", "10"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "setup_armed"
    assert snapshot.reason_code == "STOCH_RSI_5M_WAITING_RECOVERY_20"
    assert snapshot.oversold_arm_time == signal_bars[0].end_time
    assert snapshot.momentum_cross_time == signal_bars[1].end_time
    assert snapshot.entry_time is None


def test_a_single_missing_five_minute_bar_is_stepped_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [_bar(0), _bar(2)]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: ([], []),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    # A short halt or a quiet bucket does not reset the indicators.
    assert snapshot.reason_code != "STOCH_RSI_5M_POST_GAP_STOCH_RSI_WARMUP"
    assert snapshot.data_gap_start is None


def test_a_longer_gap_still_requires_post_gap_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [_bar(0), _bar(3)]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: ([], []),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "data_gap"
    assert snapshot.reason_code == "STOCH_RSI_5M_POST_GAP_STOCH_RSI_WARMUP"
    assert snapshot.data_gap_start == signal_bars[0].end_time


def test_rejects_entry_open_at_or_below_signal_ema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [
        _bar(0),
        _bar(1, open_="9.5", close="9.7"),
        _bar(2, open_="9.8", close="9.9"),
        _bar(3, open_="9.8", close="9.7"),
    ]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["5", "9", "22", "30"],
            ["8", "7", "15", "20"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "waiting_oversold"
    assert snapshot.reason_code == "STOCH_RSI_5M_PRICE_CONFIRMATION_REJECTED"
    assert snapshot.entry_time is None


def test_non_bullish_signal_waits_for_confirmed_high_breakout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [
        _bar(0),
        _bar(1, open_="10", close="10.5"),
        _bar(2, open_="10.5", close="10.3", high_="11", low_="9.5"),
        _bar(3, open_="10.3", close="10.7", high_="10.9", low_="10.2"),
        _bar(4, open_="10.7", close="11.1", high_="11.2", low_="10.6"),
        _bar(5, open_="11.3", close="11.4"),
    ]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["5", "9", "22", "25", "30", "40"],
            ["8", "7", "15", "20", "25", "30"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "long_active"
    assert snapshot.entry_signal_time == signal_bars[2].end_time
    assert snapshot.entry_time == signal_bars[5].start_time
    assert snapshot.entry_price == Decimal("11.3")


def test_exits_after_five_minute_close_below_50_period_five_minute_ema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [
        _bar(0),
        _bar(1),
        _bar(2, open_="9.8", close="10.1"),
        _bar(3, open_="10.1", close="9.8"),
        _bar(4, open_="9.7", close="9.7"),
    ]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["5", "9", "22", "50", "50"],
            ["8", "7", "15", "50", "50"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "exited"
    assert snapshot.reason_code == "STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA"
    assert snapshot.ema_50_5m < Decimal("10")
    assert snapshot.entry_time == signal_bars[3].start_time
    assert snapshot.entry_price == Decimal("10.1")
    assert snapshot.exit_signal_time == signal_bars[3].end_time
    assert snapshot.exit_time == signal_bars[4].start_time
    assert snapshot.exit_price == Decimal("9.7")


def test_exits_on_stoch_rsi_cross_down_below_eighty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [
        _bar(0),
        _bar(1, open_="9.8", close="10"),
        _bar(2, open_="10", close="10.2"),
        _bar(3, open_="10.20", close="10.3"),
        _bar(4, open_="10.3", close="10.4"),
        _bar(5, open_="10.4", close="10.5"),
        _bar(6, open_="10.6", close="10.6"),
    ]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["5", "9", "22", "40", "70", "75", "75"],
            ["8", "7", "15", "35", "65", "80", "78"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "exited"
    assert snapshot.reason_code == "STOCH_RSI_5M_CROSS_DOWN_BELOW_80"
    assert snapshot.entry_time == signal_bars[3].start_time
    assert snapshot.entry_price == Decimal("10.20")
    assert snapshot.exit_signal_time == signal_bars[5].end_time
    assert snapshot.exit_time == signal_bars[6].start_time
    assert snapshot.exit_price == Decimal("10.6")


def test_lower_low_does_not_veto_a_confirmed_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal_bars = [
        _bar(0, open_="10.3", close="10.3", high_="10.4", low_="10.2"),
        _bar(1, open_="10.1", close="10.05", high_="10.2", low_="10"),
        _bar(2, open_="10.1", close="10.4", high_="10.5", low_="10.1"),
        _bar(3, open_="10", close="9.75", high_="10.3", low_="9.7"),
        _bar(4, open_="9.8", close="9.95", high_="10", low_="9.8"),
        _bar(5, open_="9.95", close="10.1", high_="10.2", low_="9.9"),
        _bar(6, open_="10.1", close="10.2"),
    ]
    bars = _with_ema_history(signal_bars)
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: _indicator_values(
            values,
            ["15", "12", "8", "5", "9", "22", "30"],
            ["20", "15", "10", "8", "7", "15", "20"],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "long_active"
    assert snapshot.entry_signal_time == signal_bars[5].end_time
    assert snapshot.entry_time == signal_bars[6].start_time


def test_missing_five_minute_ema_history_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = [_bar(0), _bar(1, open_="9.8", close="10.1"), _bar(2)]
    monkeypatch.setattr(
        strategy,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: (
            [Decimal("5"), Decimal("9"), Decimal("20")],
            [Decimal("8"), Decimal("7"), Decimal("15")],
        ),
    )

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "waiting_data"
    assert snapshot.reason_code == "STOCH_RSI_5M_5_5M_EMA_WARMUP"


def test_recovery_threshold_must_be_between_entry_extremes() -> None:
    with pytest.raises(ValueError, match="recovery_threshold must exceed"):
        StochRsi5mConfig(recovery_threshold=Decimal("10"))
    with pytest.raises(ValueError, match="recovery_threshold must be below"):
        StochRsi5mConfig(recovery_threshold=Decimal("95"))


def test_removed_lower_low_config_fields_are_ignored_for_compatibility() -> None:
    config = StochRsi5mConfig(
        minimum_swing_bounce_pct=Decimal("1"),
        lower_low_close_break_pct=Decimal("0.25"),
    )

    assert "minimum_swing_bounce_pct" not in config.model_dump()
    assert "lower_low_close_break_pct" not in config.model_dump()
