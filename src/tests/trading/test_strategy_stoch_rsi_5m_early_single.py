from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading import strategy_stoch_rsi_5m_early_single as early_single
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_stoch_rsi_5m import StochRsi5mSnapshot, StochRsi5mTrade


def _bar(*, high: str, low: str) -> MarketBar:
    start = datetime(2026, 9, 10, 16, 55, tzinfo=timezone.utc)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=Decimal("10"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("11"),
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=5),
    )


def _trade(
    *,
    entry_price: str,
    exit_price: str,
    reason: str,
    entry_time: datetime | None = None,
) -> StochRsi5mTrade:
    actual_entry = entry_time or datetime(2026, 9, 10, 17, 0, tzinfo=timezone.utc)
    exit_time = actual_entry + timedelta(minutes=30)
    return StochRsi5mTrade(
        oversold_arm_time=actual_entry,
        momentum_cross_time=actual_entry,
        entry_signal_time=actual_entry,
        entry_time=actual_entry,
        entry_price=Decimal(entry_price),
        exit_signal_time=exit_time,
        exit_time=exit_time,
        exit_price=Decimal(exit_price),
        exit_reason_code=reason,
        return_pct=(Decimal(exit_price) - Decimal(entry_price))
        / Decimal(entry_price)
        * Decimal("100"),
    )


def test_early_single_keeps_only_first_completed_trade(monkeypatch) -> None:
    first = _trade(
        entry_price="10",
        exit_price="11",
        reason="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA",
    )
    second = _trade(
        entry_price="12",
        exit_price="13",
        reason="STOCH_RSI_5M_CROSS_DOWN_BELOW_80",
    )
    snapshot = StochRsi5mSnapshot(
        state="exited",
        reason_code=second.exit_reason_code,
        session_date="2026-09-10",
        trades=(first, second),
    )
    monkeypatch.setattr(
        early_single,
        "evaluate_stoch_rsi_5m",
        lambda bars, config: snapshot,
    )

    result = early_single.evaluate_stoch_rsi_5m_early_single(
        [_bar(high="25", low="10")]
    )

    assert result.state == "exited"
    assert result.reason_code == first.exit_reason_code
    assert result.trades == (first,)
    assert result.entry_time == first.entry_time
    assert result.exit_time == first.exit_time
    assert result.return_pct == first.return_pct


def test_early_single_ignores_prior_session_trade_from_gap_recovery(monkeypatch) -> None:
    prior = _trade(
        entry_price="10",
        exit_price="9",
        reason="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA",
        entry_time=datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    )
    current = _trade(
        entry_price="20",
        exit_price="22",
        reason="STOCH_RSI_5M_OVERBOUGHT_CROSS_DOWN",
        entry_time=datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
    )
    snapshot = StochRsi5mSnapshot(
        state="exited",
        reason_code=current.exit_reason_code,
        session_date="2026-09-01",
        trades=(prior, current),
    )
    monkeypatch.setattr(
        early_single,
        "evaluate_stoch_rsi_5m",
        lambda bars, config: snapshot,
    )

    result = early_single.evaluate_stoch_rsi_5m_early_single([])

    assert result.trades == (current,)
    assert result.entry_time == current.entry_time
    assert result.return_pct == current.return_pct


def test_early_single_clears_prior_session_only_trade(monkeypatch) -> None:
    prior = _trade(
        entry_price="10",
        exit_price="9",
        reason="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA",
        entry_time=datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    )
    snapshot = StochRsi5mSnapshot(
        state="exited",
        reason_code=prior.exit_reason_code,
        session_date="2026-09-01",
        entry_time=prior.entry_time,
        entry_price=prior.entry_price,
        exit_time=prior.exit_time,
        exit_price=prior.exit_price,
        return_pct=prior.return_pct,
        trades=(prior,),
    )
    monkeypatch.setattr(
        early_single,
        "evaluate_stoch_rsi_5m",
        lambda bars, config: snapshot,
    )

    result = early_single.evaluate_stoch_rsi_5m_early_single([])

    assert result.state == "waiting_oversold"
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_NO_CURRENT_SESSION_TRADE"
    assert result.trades == ()
    assert result.entry_time is None
    assert result.exit_time is None
    assert result.return_pct is None


def test_early_single_rejects_trade_above_pre_entry_range_cap(monkeypatch) -> None:
    trade = _trade(
        entry_price="10",
        exit_price="11",
        reason="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA",
    )
    snapshot = StochRsi5mSnapshot(
        state="exited",
        reason_code=trade.exit_reason_code,
        trades=(trade,),
    )
    monkeypatch.setattr(
        early_single,
        "evaluate_stoch_rsi_5m",
        lambda bars, config: snapshot,
    )

    result = early_single.evaluate_stoch_rsi_5m_early_single(
        [_bar(high="25.01", low="10")]
    )

    assert result.state == "waiting_oversold"
    assert result.reason_code == (
        "STOCH_RSI_5M_EARLY_SINGLE_PRE_ENTRY_RANGE_ABOVE_150"
    )
    assert result.trades == ()
    assert result.entry_time is None
    assert result.return_pct is None


def test_early_single_passes_through_snapshot_without_trades(monkeypatch) -> None:
    snapshot = StochRsi5mSnapshot(
        state="waiting_oversold",
        reason_code="STOCH_RSI_5M_WAITING_OVERSOLD_ARM",
    )
    monkeypatch.setattr(
        early_single,
        "evaluate_stoch_rsi_5m",
        lambda bars, config: snapshot,
    )

    result = early_single.evaluate_stoch_rsi_5m_early_single([])

    assert result is snapshot


def _open_snapshot() -> StochRsi5mSnapshot:
    entry_time = datetime(2026, 9, 10, 17, 0, tzinfo=timezone.utc)
    return StochRsi5mSnapshot(
        state="long_active",
        reason_code="STOCH_RSI_5M_LONG_ACTIVE",
        session_date="2026-09-10",
        entry_signal_time=entry_time,
        entry_time=entry_time,
        entry_price=Decimal("11"),
    )


def test_early_single_vetoes_open_first_trade_above_pre_entry_range_cap(monkeypatch) -> None:
    snapshot = _open_snapshot()
    monkeypatch.setattr(
        early_single,
        "evaluate_stoch_rsi_5m",
        lambda bars, config: snapshot,
    )

    result = early_single.evaluate_stoch_rsi_5m_early_single(
        [_bar(high="26", low="10")]
    )

    assert result.state == "waiting_oversold"
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_PRE_ENTRY_RANGE_ABOVE_150"
    assert result.entry_time is None
    assert result.trades == ()


def test_early_single_keeps_open_first_trade_within_pre_entry_range_cap(monkeypatch) -> None:
    snapshot = _open_snapshot()
    monkeypatch.setattr(
        early_single,
        "evaluate_stoch_rsi_5m",
        lambda bars, config: snapshot,
    )

    result = early_single.evaluate_stoch_rsi_5m_early_single(
        [_bar(high="25", low="10")]
    )

    assert result is snapshot


def test_early_single_takes_first_trade_allowed_by_admission(monkeypatch) -> None:
    first = _trade(entry_price="10", exit_price="9", reason="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA")
    second = _trade(
        entry_price="12",
        exit_price="13",
        reason="STOCH_RSI_5M_CROSS_DOWN_BELOW_80",
        entry_time=datetime(2026, 9, 10, 18, 0, tzinfo=timezone.utc),
    )
    snapshot = StochRsi5mSnapshot(
        state="exited",
        reason_code=second.exit_reason_code,
        session_date="2026-09-10",
        trades=(first, second),
    )
    monkeypatch.setattr(early_single, "evaluate_stoch_rsi_5m", lambda bars, config: snapshot)
    admitted_at = datetime(2026, 9, 10, 17, 30, tzinfo=timezone.utc)

    result = early_single.evaluate_stoch_rsi_5m_early_single(
        [_bar(high="12", low="10")],
        entry_allowed=lambda entry_time: entry_time >= admitted_at,
    )

    assert result.trades == (second,)
    assert result.entry_time == second.entry_time


def test_early_single_clears_open_trade_entered_before_admission(monkeypatch) -> None:
    snapshot = _open_snapshot()
    monkeypatch.setattr(early_single, "evaluate_stoch_rsi_5m", lambda bars, config: snapshot)

    result = early_single.evaluate_stoch_rsi_5m_early_single(
        [_bar(high="11", low="10")],
        entry_allowed=lambda entry_time: False,
    )

    assert result.state == "waiting_oversold"
    assert result.reason_code == "STOCH_RSI_5M_EARLY_SINGLE_ENTRY_NOT_ADMITTED"
    assert result.entry_time is None
