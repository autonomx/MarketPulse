from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from app.apps.trading import strategy_stoch_rsi_5m_late_stage as late_stage
from app.apps.trading.models import MarketBar
from app.apps.trading.strategies.models import StochRsi5mConfig


def _bar(start: datetime, *, open_: str, close: str) -> MarketBar:
    open_value = Decimal(open_)
    close_value = Decimal(close)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=open_value,
        high=max(open_value, close_value),
        low=min(open_value, close_value),
        close=close_value,
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=5),
    )


def _bars_through_10am_pt(*, cutoff_close: str) -> list[MarketBar]:
    return [
        _bar(
            datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc),
            open_="10",
            close="10",
        ),
        _bar(
            datetime(2026, 9, 10, 16, 55, tzinfo=timezone.utc),
            open_="15",
            close=cutoff_close,
        ),
    ]


def test_late_stage_clamps_entry_start_to_10am_pacific(
    monkeypatch,
) -> None:
    captured = {}

    def fake_evaluate(bars, config):
        captured["config"] = config
        return "snapshot"

    monkeypatch.setattr(late_stage, "evaluate_stoch_rsi_5m", fake_evaluate)

    result = late_stage.evaluate_stoch_rsi_5m_late_stage(
        _bars_through_10am_pt(cutoff_close="15"),
        StochRsi5mConfig(entry_start_et=time(9, 35)),
    )

    assert result == "snapshot"
    assert captured["config"].entry_start_et == time(13, 0)


def test_late_stage_does_not_move_a_later_config_start(monkeypatch) -> None:
    captured = {}

    def fake_evaluate(bars, config):
        captured["config"] = config
        return "snapshot"

    monkeypatch.setattr(late_stage, "evaluate_stoch_rsi_5m", fake_evaluate)

    late_stage.evaluate_stoch_rsi_5m_late_stage(
        _bars_through_10am_pt(cutoff_close="15"),
        StochRsi5mConfig(entry_start_et=time(14, 0)),
    )

    assert captured["config"].entry_start_et == time(14, 0)


def test_late_stage_rejects_when_10am_open_gain_is_below_20_percent() -> None:
    result = late_stage.evaluate_stoch_rsi_5m_late_stage(
        _bars_through_10am_pt(cutoff_close="14.99"),
    )

    assert result.state == "waiting_oversold"
    assert result.reason_code == (
        "STOCH_RSI_5M_LATE_STAGE_OPEN_GAIN_BELOW_THRESHOLD"
    )
