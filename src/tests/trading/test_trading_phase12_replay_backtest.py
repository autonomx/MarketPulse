from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.apps.trading.backtest import BacktestRequest, MovingAverageCrossStrategy, run_backtest
from app.apps.trading.models import (
    AssetClass,
    BarsResponse,
    CanonicalInstrument,
    DatasetProvenance,
    FeedType,
    InstrumentType,
    MarketBar,
    ProviderBinding,
    UsageScope,
)
from app.apps.trading.replay import ReplayClock, freeze_bars_response
from app.apps.trading.replay_api import create_trading_replay_router


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
INSTRUMENT = "crypto:FIXTURE:spot:TEST-USD"


def bars_response(*, gap: bool = False) -> BarsResponse:
    closes = [10, 10, 10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 9, 10, 11]
    instrument = CanonicalInstrument(
        instrument_id=INSTRUMENT,
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.SPOT,
        venue="FIXTURE",
        venue_symbol="TEST-USD",
        display_symbol="TESTUSD",
        base_currency="TEST",
        quote_currency="USD",
        exchange_timezone="UTC",
        session_calendar="24x7",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
    )
    binding = ProviderBinding(
        binding_id="fixture:test",
        instrument_id=INSTRUMENT,
        provider="fixture",
        provider_symbol="TEST-USD",
        feed_type=FeedType.HISTORICAL_POLLING,
        realtime_scope="fixture history",
        supported_intervals=("1d",),
        usage_scope=UsageScope.PERSONAL_LOCAL,
        is_official_api=True,
    )
    bars = []
    for index, close in enumerate(closes):
        day = index + (1 if gap and index >= 8 else 0)
        start = NOW + timedelta(days=day)
        bars.append(
            MarketBar(
                instrument_id=INSTRUMENT,
                interval="1d",
                start_time=start,
                end_time=start + timedelta(days=1),
                open=Decimal(close),
                high=Decimal(close) + 1,
                low=Decimal(close) - 1,
                close=Decimal(close),
                volume=Decimal(1000 + index),
                is_final=True,
                provider="fixture",
                received_at=start + timedelta(days=1),
            )
        )
    return BarsResponse(
        instrument=instrument,
        binding=binding,
        provenance=DatasetProvenance(
            instrument_id=INSTRUMENT,
            requested_binding=binding.binding_id,
            resolved_binding=binding.binding_id,
            dataset_fingerprint="provider-fingerprint",
            freshness_mode="polled",
            as_of=bars[-1].end_time,
            received_at=bars[-1].received_at,
            cached=False,
            history_complete=True,
        ),
        interval="1d",
        bars=bars,
    )


def snapshot(gap_policy="fail", gap=False):
    return freeze_bars_response(
        dataset_id="dataset-1",
        response=bars_response(gap=gap),
        requested_binding_id="fixture:test",
        gap_policy=gap_policy,
    )


def test_frozen_dataset_is_fingerprint_validated_and_immutable() -> None:
    frozen = snapshot()
    assert frozen.dataset_fingerprint
    assert frozen.provider == "fixture"
    assert frozen.resolved_binding_id == "fixture:test"
    with pytest.raises(ValidationError):
        frozen.model_copy(update={"dataset_fingerprint": "tampered"}).model_validate(
            frozen.model_copy(update={"dataset_fingerprint": "tampered"}).model_dump()
        )
    with pytest.raises(ValidationError):
        frozen.bars[0].close = Decimal("999")


def test_gap_policy_is_explicit_and_replay_clock_is_deterministic() -> None:
    with pytest.raises(ValidationError, match="gap"):
        snapshot(gap_policy="fail", gap=True)
    skipped = snapshot(gap_policy="skip", gap=True)
    clock = ReplayClock(skipped)
    assert clock.tick() is None
    clock.set_speed("4")
    clock.play()
    first = clock.tick()
    assert first is not None and first.sequence == 0
    clock.pause()
    assert clock.tick() is None
    stepped = clock.step(2)
    assert [event.sequence for event in stepped] == [1, 2]
    clock.reset()
    assert clock.step(1)[0].sequence == 0


def test_backtest_is_economically_deterministic_and_has_no_lookahead() -> None:
    frozen = snapshot()
    request = BacktestRequest(
        strategy=MovingAverageCrossStrategy(fast_period=2, slow_period=3),
        execution_policy={
            "fill_timing": "next_bar_open",
            "commission_bps": "10",
            "slippage_bps": "5",
            "position_size_fraction": "1",
            "allow_short": False,
            "use_finalized_bars_only": True,
        },
        initial_cash=Decimal("10000"),
    )
    first = run_backtest(frozen, request, run_id="run-1", now=NOW)
    second = run_backtest(frozen, request, run_id="run-1", now=NOW)
    assert first.status == "completed"
    assert first.trades == second.trades
    assert first.equity_curve == second.equity_curve
    assert first.final_equity == second.final_equity
    assert first.max_drawdown_percent == second.max_drawdown_percent
    assert first.win_rate_percent == second.win_rate_percent
    assert first.exposure_percent == second.exposure_percent
    assert Decimal("0") <= first.win_rate_percent <= Decimal("100")
    assert Decimal("0") <= first.exposure_percent <= Decimal("100")
    assert first.dataset_fingerprint == frozen.dataset_fingerprint
    assert first.trade_count > 0
    for trade in first.trades:
        assert trade.fill_bar_index == trade.signal_bar_index + 1
        assert trade.signal_time == frozen.bars[trade.signal_bar_index].end_time
        assert trade.fill_time == frozen.bars[trade.fill_bar_index].start_time
        assert trade.fill_time >= trade.signal_time
    source = Path("src/app/apps/trading/backtest.py").read_text()
    for forbidden in (
        "import requests",
        "import httpx",
        "from .service",
        "market_service",
        "default_market_data_service",
    ):
        assert forbidden not in source


class FakeReplayRepository:
    def __init__(self):
        self.dataset = snapshot()
        self.saved = []

    def create_dataset(self, value): self.dataset = value; return value
    def list_datasets(self, limit=100): return [self.dataset]
    def get_dataset(self, dataset_id): return self.dataset if dataset_id == self.dataset.dataset_id else None
    def save_backtest(self, result): self.saved.append(result); return result
    def list_backtests(self, limit=100):
        return [{
            "run_id": self.saved[-1].run_id,
            "dataset_id": self.saved[-1].dataset_id,
            "status": self.saved[-1].status,
        }] if self.saved else []
    def get_backtest(self, run_id):
        return next((item for item in self.saved if item.run_id == run_id), None)


class FakeMarketService:
    def bars(self, instrument_id, interval, limit, binding_id=None):
        assert instrument_id == INSTRUMENT
        assert binding_id == "fixture:test"
        return bars_response()


def test_replay_api_freezes_then_runs_only_the_stored_snapshot() -> None:
    repository = FakeReplayRepository()
    app = FastAPI()
    app.include_router(
        create_trading_replay_router(
            repository_factory=lambda: repository,
            market_service_factory=lambda: FakeMarketService(),
        )
    )
    client = TestClient(app)
    frozen = client.post(
        "/api/trading/replay/datasets",
        json={
            "dataset_id": "dataset-1",
            "instrument_id": INSTRUMENT,
            "binding_id": "fixture:test",
            "interval": "1d",
            "limit": 100,
            "gap_policy": "fail",
        },
    )
    assert frozen.status_code == 201
    assert frozen.json()["dataset_fingerprint"] == repository.dataset.dataset_fingerprint
    run = client.post(
        "/api/trading/replay/backtests",
        json={
            "dataset_id": "dataset-1",
            "request": {
                "strategy": {"strategy_id": "sma_cross", "fast_period": 2, "slow_period": 3},
                "execution_policy": {
                    "fill_timing": "next_bar_open",
                    "commission_bps": "0",
                    "slippage_bps": "0",
                    "position_size_fraction": "1",
                    "allow_short": False,
                    "use_finalized_bars_only": True,
                },
                "initial_cash": "10000",
                "formula_version": "omnix-indicators-v2",
            },
        },
    )
    assert run.status_code == 201
    run_id = run.json()["run_id"]
    assert repository.saved[0].dataset_fingerprint == repository.dataset.dataset_fingerprint
    assert client.get("/api/trading/replay/backtests").json()["runs"][0]["run_id"] == run_id
    assert client.get(f"/api/trading/replay/backtests/{run_id}").json()["trade_count"] >= 1


def test_replay_migration_persists_complete_run_evidence() -> None:
    migration = Path(
        "src/app/apps/trading/migrations/0022_trading_replay_backtests.sql"
    ).read_text()
    for table in (
        "omnix_trading_datasets",
        "omnix_trading_backtest_runs",
        "omnix_trading_backtest_trades",
        "omnix_trading_backtest_equity",
        "omnix_trading_backtest_logs",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
    sequencing = Path(
        "src/app/apps/trading/migrations/0024_trading_backtest_bar_indices.sql"
    ).read_text()
    assert "signal_bar_index" in sequencing
    assert "fill_bar_index" in sequencing
    assert "fill_bar_index = signal_bar_index + 1" in sequencing
    artifacts = Path(
        "src/app/apps/trading/migrations/0025_trading_backtest_artifacts.sql"
    ).read_text()
    assert "win_rate_percent" in artifacts
    assert "exposure_percent" in artifacts
    assert "artifact_checksum_sha256" in artifacts
