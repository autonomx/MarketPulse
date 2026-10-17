from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.models import MarketBar
from app.apps.trading import strategy_stoch_rsi_5m_early_single_loss_controls as controls
from app.apps.trading.strategy_stoch_rsi_5m import StochRsi5mSnapshot, StochRsi5mTrade


DAY_START = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
HISTORY_START = datetime(2026, 9, 9, 13, 30, tzinfo=timezone.utc)


def _bar(
    start: datetime,
    *,
    open_: str = "10",
    high: str = "10.1",
    low: str = "9.9",
    close: str = "10",
    volume: str = "100",
) -> MarketBar:
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=5),
    )


def _history(*, rising: bool = False) -> list[MarketBar]:
    bars: list[MarketBar] = []
    for index in range(50):
        close = (
            Decimal("8") + Decimal(index) / Decimal("100")
            if rising
            else Decimal("10")
        )
        start = HISTORY_START + timedelta(minutes=5 * index)
        bars.append(
            _bar(
                start,
                open_=str(close),
                high=str(close + Decimal("0.02")),
                low=str(close - Decimal("0.02")),
                close=str(close),
                volume="100",
            )
        )
    return bars


def _session_bars(
    *,
    signal_volume: str = "100",
    signal_high: str = "10.2",
    signal_close: str = "10.1",
    entry_open: str = "10.15",
) -> list[MarketBar]:
    return [
        _bar(DAY_START, open_="10", high="10.1", low="9.5", close="9.8", volume="100"),
        _bar(DAY_START + timedelta(minutes=5), open_="9.8", high="10.0", low="9.7", close="9.9", volume="100"),
        _bar(DAY_START + timedelta(minutes=10), open_="9.9", high=signal_high, low="9.8", close=signal_close, volume=signal_volume),
        _bar(DAY_START + timedelta(minutes=15), open_=entry_open, high="10.25", low="9.5", close="10.2", volume="100"),
        _bar(DAY_START + timedelta(minutes=20), open_="10.2", high="10.3", low="10.0", close="10.1", volume="100"),
        _bar(DAY_START + timedelta(minutes=25), open_="10.1", high="10.2", low="9.9", close="10.0", volume="100"),
        _bar(DAY_START + timedelta(minutes=30), open_="10.0", high="10.1", low="9.9", close="10.0", volume="100"),
    ]


def _snapshot(session: list[MarketBar]) -> StochRsi5mSnapshot:
    price = session[3].open
    trade = StochRsi5mTrade(
        oversold_arm_time=session[0].end_time,
        momentum_cross_time=session[1].end_time,
        entry_signal_time=session[2].end_time,
        entry_time=session[3].start_time,
        entry_price=price,
        exit_signal_time=session[5].end_time,
        exit_time=session[6].start_time,
        exit_price=session[6].open,
        exit_reason_code="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA",
        return_pct=(session[6].open - price) / price * Decimal("100"),
    )
    return StochRsi5mSnapshot(
        state="exited",
        reason_code=trade.exit_reason_code,
        oversold_arm_time=trade.oversold_arm_time,
        momentum_cross_time=trade.momentum_cross_time,
        entry_signal_time=trade.entry_signal_time,
        entry_time=trade.entry_time,
        entry_price=trade.entry_price,
        exit_signal_time=trade.exit_signal_time,
        exit_time=trade.exit_time,
        exit_price=trade.exit_price,
        return_pct=trade.return_pct,
        trades=(trade,),
    )


def _install_parent(monkeypatch: pytest.MonkeyPatch, session: list[MarketBar]) -> None:
    snapshot = _snapshot(session)
    monkeypatch.setattr(
        controls,
        "evaluate_stoch_rsi_5m_early_single",
        lambda bars, config: snapshot,
    )


def test_cap150_rejects_extreme_pre_entry_range(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session_bars(signal_high="30")
    session[0] = _bar(DAY_START, open_="10", high="10", low="9", close="9.8")
    _install_parent(monkeypatch, session)

    result = controls.evaluate_stoch_rsi_5m_early_single_loss_control(
        [*_history(), *session],
        "baseline_cap150",
    )

    assert result.trades == ()
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_PRE_ENTRY_RANGE_ABOVE_150"


def test_structural_stop_only_moves_exit_earlier(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session_bars()
    session[4] = _bar(
        DAY_START + timedelta(minutes=20),
        open_="10.2",
        high="10.25",
        low="9.3",
        close="9.4",
    )
    session[5] = _bar(
        DAY_START + timedelta(minutes=25),
        open_="9.35",
        high="9.5",
        low="9.2",
        close="9.3",
    )
    _install_parent(monkeypatch, session)

    result = controls.evaluate_stoch_rsi_5m_early_single_loss_control(
        [*_history(), *session],
        "structural_stop",
    )

    assert result.trades[0].exit_time == session[5].start_time
    assert result.trades[0].exit_price == Decimal("9.35")
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_STRUCTURAL_STOP"


def test_early_failure_requires_weak_mfe_and_below_entry_and_vwap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session_bars(entry_open="10.10")
    session[3] = _bar(
        DAY_START + timedelta(minutes=15),
        open_="10.10",
        high="10.15",
        low="9.98",
        close="10.00",
    )
    session[4] = _bar(
        DAY_START + timedelta(minutes=20),
        open_="10.00",
        high="10.12",
        low="9.80",
        close="9.85",
    )
    session[5] = _bar(
        DAY_START + timedelta(minutes=25),
        open_="9.96",
        high="10.0",
        low="9.8",
        close="9.9",
    )
    _install_parent(monkeypatch, session)

    result = controls.evaluate_stoch_rsi_5m_early_single_loss_control(
        [*_history(), *session],
        "early_failure_exit",
    )

    assert result.trades[0].exit_time == session[5].start_time
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_EARLY_FAILURE_10M"


def test_positive_ema_slope_filter_rejects_non_positive_slope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session_bars()
    _install_parent(monkeypatch, session)

    result = controls.evaluate_stoch_rsi_5m_early_single_loss_control(
        [*_history(rising=False), *session],
        "ema_slope_positive",
    )

    assert result.trades == ()
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_EMA_SLOPE_NOT_POSITIVE"


def test_above_vwap_rejects_signal_below_session_vwap(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session_bars(signal_close="9.6", entry_open="9.7")
    session[0] = _bar(DAY_START, open_="10", high="10.4", low="9.9", close="10.3")
    session[1] = _bar(DAY_START + timedelta(minutes=5), open_="10.3", high="10.4", low="10.1", close="10.2")
    _install_parent(monkeypatch, session)

    result = controls.evaluate_stoch_rsi_5m_early_single_loss_control(
        [*_history(), *session],
        "above_vwap",
    )

    assert result.trades == ()
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_NOT_ABOVE_VWAP"


def test_recovery_volume_thresholds_are_separate_arms(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session_bars(signal_volume="110")
    _install_parent(monkeypatch, session)
    bars = [*_history(), *session]

    one_x = controls.evaluate_stoch_rsi_5m_early_single_loss_control(
        bars,
        "recovery_volume_1x",
    )
    one_25_x = controls.evaluate_stoch_rsi_5m_early_single_loss_control(
        bars,
        "recovery_volume_1_25x",
    )

    assert len(one_x.trades) == 1
    assert one_25_x.trades == ()
    assert one_25_x.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_RECOVERY_VOLUME_BELOW_1_25"


def test_recovery_high_break_delays_entry_to_following_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session_bars(signal_high="10.2", signal_close="10.1", entry_open="10.15")
    session[3] = _bar(
        DAY_START + timedelta(minutes=15),
        open_="10.15",
        high="10.35",
        low="10.05",
        close="10.30",
    )
    session[4] = _bar(
        DAY_START + timedelta(minutes=20),
        open_="10.40",
        high="10.5",
        low="10.3",
        close="10.45",
    )
    _install_parent(monkeypatch, session)
    monkeypatch.setattr(
        controls,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: (
            [Decimal("50")] * len(values),
            [Decimal("40")] * len(values),
        ),
    )

    result = controls.evaluate_stoch_rsi_5m_early_single_loss_control(
        [*_history(), *session],
        "recovery_high_break",
    )

    assert result.trades[0].entry_time == session[4].start_time
    assert result.trades[0].entry_price == Decimal("10.40")
    assert result.trades[0].exit_time == session[6].start_time
