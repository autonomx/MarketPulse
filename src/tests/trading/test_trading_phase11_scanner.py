from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

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
from app.apps.trading.scanner import (
    AsyncScannerCancellation,
    TradingScannerDefinition,
    TradingScannerResult,
    TradingScannerRule,
    TradingScannerRun,
    execute_scanner,
    scanner_definition_fingerprint,
    scanner_metric_formula,
)
from app.apps.trading.scanner_api import create_trading_scanner_router


NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def definition(instrument_ids: list[str], **overrides) -> TradingScannerDefinition:
    payload = {
        "scanner_id": "scanner-1",
        "name": "Fixture scanner",
        "instrument_ids": instrument_ids,
        "interval": "1d",
        "history_limit": 30,
        "rules": [
            {
                "rule_id": "momentum",
                "metric": "percent_change",
                "operator": "gte",
                "threshold": "0",
                "lookback_bars": 5,
                "period": 14,
            }
        ],
        "max_concurrency": 4,
        "request_timeout_seconds": 5,
        "run_timeout_seconds": 30,
        "formula_version": "omnix-indicators-v2",
    }
    payload.update(overrides)
    return TradingScannerDefinition.model_validate(payload)


def response(instrument_id: str, offset: int = 0) -> BarsResponse:
    instrument = CanonicalInstrument(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.SPOT,
        venue="FIXTURE",
        venue_symbol=instrument_id,
        display_symbol=instrument_id,
        quote_currency="USD",
        exchange_timezone="UTC",
        session_calendar="24x7",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
    )
    binding = ProviderBinding(
        binding_id=f"fixture:{instrument_id}",
        instrument_id=instrument_id,
        provider="fixture",
        provider_symbol=instrument_id,
        feed_type=FeedType.HISTORICAL_POLLING,
        realtime_scope="fixture",
        supported_intervals=("1d",),
        usage_scope=UsageScope.PERSONAL_LOCAL,
        is_official_api=True,
    )
    bars = [
        MarketBar(
            instrument_id=instrument_id,
            interval="1d",
            start_time=NOW + timedelta(days=index),
            end_time=NOW + timedelta(days=index + 1),
            open=Decimal(100 + offset + index),
            high=Decimal(101 + offset + index),
            low=Decimal(99 + offset + index),
            close=Decimal(100 + offset + index),
            volume=Decimal(1_000 + index),
            is_final=True,
            provider="fixture",
            received_at=NOW + timedelta(days=index + 1),
        )
        for index in range(30)
    ]
    return BarsResponse(
        instrument=instrument,
        binding=binding,
        provenance=DatasetProvenance(
            instrument_id=instrument_id,
            requested_binding=binding.binding_id,
            resolved_binding=binding.binding_id,
            dataset_fingerprint=f"fingerprint-{instrument_id}",
            freshness_mode="polled",
            as_of=bars[-1].end_time,
            received_at=bars[-1].received_at,
            cached=False,
            history_complete=True,
        ),
        interval="1d",
        bars=bars,
    )


def test_scanner_rejects_unbounded_or_invalid_universes() -> None:
    with pytest.raises(ValidationError):
        definition([f"instrument-{index}" for index in range(201)])
    with pytest.raises(ValidationError):
        definition(["duplicate", "duplicate"])
    with pytest.raises(ValidationError):
        definition(["a"], binding_ids={"outside": "binding"})
    with pytest.raises(ValidationError):
        definition(["a"], max_concurrency=9)
    with pytest.raises(ValidationError):
        definition(["a"], history_limit=5)


def test_scanner_processes_fifty_instruments_with_bounded_concurrency() -> None:
    instruments = [f"fixture-{index:02d}" for index in range(50)]
    scanner = definition(instruments)
    lock = threading.Lock()
    active = 0
    maximum = 0

    def fetch(instrument_id: str, interval: str, limit: int, binding_id: str | None):
        nonlocal active, maximum
        assert interval == "1d"
        assert limit == 30
        assert binding_id is None
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.002)
            return response(instrument_id)
        finally:
            with lock:
                active -= 1

    summary = asyncio.run(
        execute_scanner(
            scanner,
            "run-50",
            fetch,
            AsyncScannerCancellation(),
        )
    )
    assert summary.status == "completed"
    assert summary.completed_count == 50
    assert len(summary.results) == 50
    assert maximum <= 4
    assert [item.rank for item in summary.results] == list(range(1, 51))
    assert {item.dataset_fingerprint for item in summary.results} == {
        f"fingerprint-{instrument}" for instrument in instruments
    }
    assert {item.formula_version for item in summary.results} == {
        "omnix-indicators-v2"
    }


def test_scanner_cancellation_and_request_timeout_are_terminal() -> None:
    cancelled = AsyncScannerCancellation()
    cancelled.set()
    cancelled_summary = asyncio.run(
        execute_scanner(
            definition(["fixture-cancel"]),
            "run-cancel",
            lambda *_: response("fixture-cancel"),
            cancelled,
        )
    )
    assert cancelled_summary.status == "cancelled"
    assert cancelled_summary.completed_count == 0

    timeout_definition = definition(
        ["fixture-timeout"],
        request_timeout_seconds=1,
        run_timeout_seconds=5,
    )

    def slow_fetch(*_):
        time.sleep(1.2)
        return response("fixture-timeout")

    timed_out = asyncio.run(
        execute_scanner(
            timeout_definition,
            "run-timeout",
            slow_fetch,
            AsyncScannerCancellation(),
        )
    )
    assert timed_out.status == "timed_out"
    assert "timeout" in (timed_out.error_message or "").lower()


def test_scanner_formula_and_definition_fingerprint_are_stable() -> None:
    scanner = definition(["fixture-a"])
    assert scanner_metric_formula(scanner.rules[0]) == "((close[t] / close[t-5]) - 1) * 100"
    assert scanner_definition_fingerprint(scanner) == scanner_definition_fingerprint(scanner.model_copy())


class FakeRepository:
    def __init__(self) -> None:
        self.scanner = definition(["fixture-a"])
        self.run = TradingScannerRun(
            run_id="run-1",
            scanner_id=self.scanner.scanner_id,
            status="completed",
            universe_count=1,
            completed_count=1,
            matched_count=1,
            definition_snapshot=self.scanner.model_dump(mode="json"),
        )
        self.result = TradingScannerResult(
            run_id="run-1",
            instrument_id="fixture-a",
            resolved_binding_id="fixture:fixture-a",
            provider="fixture",
            dataset_fingerprint="fingerprint-fixture-a",
            source_as_of=NOW,
            formula_version="omnix-indicators-v2",
            metrics={"percent_change:5": Decimal("5")},
            matched_rules=["momentum"],
            rank=1,
            score=Decimal("5"),
        )

    def list_definitions(self, limit=100): return [self.scanner]
    def create_definition(self, value): self.scanner = value; return value
    def update_definition(self, scanner_id, value, expected_revision): return value.model_copy(update={"revision": expected_revision + 1})
    def list_runs(self, scanner_id=None, limit=100): return [self.run]
    def list_results(self, run_id, limit=500): return [self.result]


class FakeManager:
    async def start_run(self, scanner_id):
        return FakeRepository().run

    async def cancel_run(self, run_id):
        return None


def test_scanner_routes_expose_persisted_definitions_runs_and_results() -> None:
    repository = FakeRepository()
    app = FastAPI()
    app.include_router(
        create_trading_scanner_router(
            repository_factory=lambda: repository,
            manager_factory=lambda: FakeManager(),
        )
    )
    client = TestClient(app)
    assert client.get("/api/trading/scanners").json()["scanners"][0]["scanner_id"] == "scanner-1"
    started = client.post("/api/trading/scanners/scanner-1/runs")
    assert started.status_code == 202
    assert started.json()["run_id"] == "run-1"
    assert client.get("/api/trading/scanners/runs").json()["runs"][0]["status"] == "completed"
    result = client.get("/api/trading/scanners/runs/run-1/results").json()["results"][0]
    assert result["dataset_fingerprint"] == "fingerprint-fixture-a"
    assert client.post("/api/trading/scanners/runs/run-1/cancel").status_code == 202
