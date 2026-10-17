from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.apps.trading import strategy_stoch_rsi_5m as stoch_module
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_monitor import TradingStrategyMonitor
from app.apps.trading.strategy_repository import StrategyEvent, TradingStrategyConfigDocument
from app.apps.trading.strategies.models import StochRsi5mConfig


INSTRUMENT = "equity:NASDAQ:TEST"
SESSION_DATE = date(2026, 9, 10)
START = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
HISTORY_START = datetime(2026, 9, 9, 13, 30, tzinfo=timezone.utc)


class MemoryRepository:
    def __init__(self) -> None:
        self.events: list[StrategyEvent] = []

    def append_event(self, event: StrategyEvent) -> bool:
        if any(item.idempotency_key == event.idempotency_key for item in self.events):
            return False
        self.events.append(event)
        return True

    def get_universe(self, universe_id: str):
        assert universe_id == "stoch-universe"
        return _universe()


class FixtureMarketService:
    def __init__(self) -> None:
        self.calls = 0

    def bars(self, instrument_id, interval, limit, binding_id):
        assert instrument_id == INSTRUMENT
        assert interval == "5m"
        assert limit == 500
        self.calls += 1
        bars = []
        for index in range(60):
            start = (
                HISTORY_START + timedelta(minutes=5 * index)
                if index < 50
                else START + timedelta(minutes=5 * (index - 50))
            )
            bars.append(
                MarketBar(
                    instrument_id=INSTRUMENT,
                    interval="5m",
                    start_time=start,
                    end_time=start + timedelta(minutes=5),
                    open=Decimal("10"),
                    high=Decimal("10"),
                    low=Decimal("10"),
                    close=Decimal("10"),
                    volume=Decimal("1000"),
                    session="regular",
                    provider="fixture",
                    received_at=start + timedelta(minutes=5),
                )
            )
        return SimpleNamespace(
            bars=bars,
            provenance=SimpleNamespace(
                resolved_binding=binding_id,
                dataset_fingerprint="fixture-5m",
                as_of=bars[-1].end_time,
            ),
        )


def _universe():
    return SimpleNamespace(
        universe_id="stoch-universe",
        session_date=SESSION_DATE,
        discovery_source="manual",
        candidates=[
            SimpleNamespace(
                instrument_id=INSTRUMENT,
                binding_id="fixture:TEST",
                market_data_complete=True,
            )
        ],
    )


def _config(*, mode: str = "shadow") -> TradingStrategyConfigDocument:
    config = StochRsi5mConfig()
    return TradingStrategyConfigDocument(
        strategy_id="stoch-rsi-5min",
        account_id="paper-test",
        strategy_kind="stoch_rsi_5m_v1",
        strategy_version="1.0.0",
        mode=mode,
        active_universe_id="stoch-universe",
        config=config,
    )


def test_strategy_document_is_shadow_only() -> None:
    with pytest.raises(ValueError, match="stoch_rsi_5m_is_shadow_only"):
        _config(mode="auto_paper")


@pytest.mark.anyio
async def test_monitor_persists_stoch_rsi_evidence_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        stoch_module,
        "_stochastic_rsi_aligned",
        lambda values, **kwargs: (
            [Decimal("50")] * (len(values) - 3)
            + [Decimal("5"), Decimal("9"), Decimal("22")],
            [Decimal("50")] * (len(values) - 3)
            + [Decimal("8"), Decimal("7"), Decimal("15")],
        ),
    )
    repository = MemoryRepository()
    market = FixtureMarketService()
    monitor = TradingStrategyMonitor(interval_seconds=30)
    monitor.current_run_id = "stoch-test-run"

    await monitor._run_stoch_rsi_5m_config(
        _config(),
        repository,
        market,
        now_utc=datetime(2026, 9, 10, 13, 45, tzinfo=timezone.utc),
    )

    assert market.calls == 1
    assert len(repository.events) == 1
    event = repository.events[0]
    assert event.event_type == "stoch_rsi_5m"
    assert event.state == "entry_armed"
    assert event.reason_code == "STOCH_RSI_5M_WAITING_PRICE_CONFIRMATION"
    assert event.payload["execution_authority"] is False
    assert event.payload["research_only"] is True
    assert event.payload["entry_policy"] == {
        "oversold_arm_threshold": "18",
        "recovery_confirmation_threshold": "20",
        "entry_above_ema_period": 5,
    }


@pytest.mark.anyio
async def test_monitor_uses_early_single_evaluator_for_early_single_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.apps.trading import strategy_stoch_rsi_5m_monitor as monitor_module
    from app.apps.trading.strategy_stoch_rsi_5m import StochRsi5mSnapshot

    calls: list[str] = []
    snapshot = StochRsi5mSnapshot(
        state="waiting_oversold",
        reason_code="STOCH_RSI_5M_WAITING_OVERSOLD_ARM",
    )

    def early_single(bars, config):
        calls.append(config.trade_selection)
        return snapshot

    def canonical(bars, config):
        raise AssertionError("early-single child must not use the sequential evaluator")

    monkeypatch.setattr(monitor_module, "evaluate_stoch_rsi_5m_early_single", early_single)
    monkeypatch.setattr(monitor_module, "evaluate_stoch_rsi_5m", canonical)
    document = _config().model_copy(
        update={
            "strategy_id": "stoch-rsi-5min-early-single",
            "parent_strategy_id": "interday-trading-strategy-shadow",
            "config": StochRsi5mConfig(trade_selection="early_single"),
        }
    )
    repository = MemoryRepository()
    monitor = TradingStrategyMonitor(interval_seconds=30)
    monitor.current_run_id = "stoch-early-single-run"

    await monitor._run_stoch_rsi_5m_config(
        document,
        repository,
        FixtureMarketService(),
        now_utc=datetime(2026, 9, 10, 13, 45, tzinfo=timezone.utc),
    )

    assert calls == ["early_single"]
    payload = repository.events[0].payload
    assert payload["trade_selection"] == "early_single"
    assert payload["exit_policy"]["allow_sequential_trades_per_symbol"] is False
    assert payload["execution_authority"] is False
