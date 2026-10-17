import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.apps.trading import ibkr_market_data_monitor as monitor_module
from app.apps.trading.execution import ExecutionObservation, assess_execution_observation
from app.apps.trading.execution_observation_plane import ExecutionObservationPlane
from app.apps.trading.evidence_storage import MemoryIbkrSessionEvidence
from app.apps.trading.ibkr_evidence import IbkrEvidenceStore
from app.apps.trading.ibkr_market_data_monitor import TradingIbkrMarketDataMonitor
from app.apps.trading.streaming.manager import StreamingQuoteUpdate


ET = ZoneInfo("America/New_York")
INSTRUMENT = "equity:NASDAQ:AAPL"
NOW = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)


def _monitor(tmp_path):
    plane = ExecutionObservationPlane()
    store = IbkrEvidenceStore(MemoryIbkrSessionEvidence())
    monitor = TradingIbkrMarketDataMonitor(
        plane=plane,
        evidence_store=store,
        now_factory=lambda: NOW,
    )
    return monitor, plane, store


def test_ibkr_monitor_preserves_entitlement_and_contract_provenance(tmp_path):
    monitor, plane, store = _monitor(tmp_path)
    update = StreamingQuoteUpdate(
        binding_id="ibkr:socket:" + INSTRUMENT,
        instrument_id=INSTRUMENT,
        provider="ibkr",
        source_time=NOW,
        received_at=NOW,
        bid=Decimal("100.00"),
        ask=Decimal("100.02"),
        last=Decimal("100.01"),
        market_data_type="LIVE",
        live_entitled=True,
        contract_id="265598",
        primary_exchange="NASDAQ",
        local_symbol="AAPL",
        provider_sequence=7,
    )

    monitor._record_quote(update)

    envelope = plane.latest(INSTRUMENT)
    assert envelope is not None
    observation = envelope.observation
    assert observation.binding_purpose == "LIVE_DATA"
    assert observation.market_data_type == "LIVE"
    assert observation.live_entitled is True
    assert observation.contract_id == "265598"
    assert observation.primary_exchange == "NASDAQ"
    assert observation.local_symbol == "AAPL"
    assert observation.market_data_eligible is True
    assert observation.broker_execution_authorized is False

    metrics = store.session_diagnostics(NOW.astimezone(ET).date())
    assert metrics["quote_event_count"] == 1
    assert metrics["live_quote_event_count"] == 1


def test_ibkr_monitor_records_live_vs_iex_comparison_without_changing_execution_authority(tmp_path):
    monitor, plane, store = _monitor(tmp_path)
    iex = ExecutionObservation(
        instrument_id=INSTRUMENT,
        binding_id="alpaca_iex:rest:" + INSTRUMENT,
        provider="alpaca_iex",
        bid=Decimal("100.00"),
        ask=Decimal("100.04"),
        last=Decimal("100.02"),
        source_time=NOW,
        received_at=NOW,
        session="regular",
        freshness_mode="live",
    )
    plane.record(assess_execution_observation(iex), recorded_at=NOW)

    monitor._record_quote(
        StreamingQuoteUpdate(
            binding_id="ibkr:socket:" + INSTRUMENT,
            instrument_id=INSTRUMENT,
            provider="ibkr",
            source_time=NOW,
            received_at=NOW,
            bid=Decimal("100.01"),
            ask=Decimal("100.03"),
            last=Decimal("100.02"),
            market_data_type="LIVE",
            live_entitled=True,
            contract_id="265598",
            primary_exchange="NASDAQ",
            local_symbol="AAPL",
            provider_sequence=8,
        )
    )

    metrics = store.session_diagnostics(NOW.astimezone(ET).date())
    assert metrics["iex_comparison_count"] == 1
    assert Decimal(metrics["last_price_diff_bps_mean"]) == Decimal("0")
    # Existing paper-fill observation remains EXECUTION; IBKR remains LIVE_DATA.
    assert any(
        row.observation.binding_purpose == "EXECUTION"
        for row in plane.observations(INSTRUMENT)
    )
    assert any(
        row.observation.binding_purpose == "LIVE_DATA"
        for row in plane.observations(INSTRUMENT)
    )


def test_ibkr_monitor_records_missing_last_as_durable_soak_failure(tmp_path):
    monitor, _, store = _monitor(tmp_path)
    monitor._record_quote(
        StreamingQuoteUpdate(
            binding_id="ibkr:socket:" + INSTRUMENT,
            instrument_id=INSTRUMENT,
            provider="ibkr",
            source_time=NOW,
            received_at=NOW,
            bid=Decimal("100"),
            ask=Decimal("100.01"),
            last=None,
            market_data_type="LIVE",
            live_entitled=True,
            contract_id="265598",
        )
    )

    metrics = store.session_diagnostics(NOW.astimezone(ET).date())
    assert metrics["missing_quote_count"] == 1
    assert metrics["reason_counts"]["IBKR_LAST_MISSING"] == 1


def test_execution_observation_monitor_does_not_own_ibkr_subscriptions():
    source = Path("src/app/apps/trading/execution_observation_monitor.py").read_text(
        encoding="utf-8"
    ).lower()

    assert "subscribe_quote" not in source
    assert "ibkr" not in source


def test_ibkr_monitor_market_data_line_budget_preserves_existing_lines(tmp_path):
    monitor, _, _ = _monitor(tmp_path)
    monitor.market_data_line_budget = 2
    monitor._keys = {
        "equity:NASDAQ:BBB": "key-bbb",
        "equity:NASDAQ:CCC": "key-ccc",
    }

    admitted = monitor._admitted_demand(
        {
            "equity:NASDAQ:AAA",
            "equity:NASDAQ:BBB",
            "equity:NASDAQ:CCC",
        }
    )

    assert admitted == {"equity:NASDAQ:BBB", "equity:NASDAQ:CCC"}
    assert monitor.budget_denied_instrument_count == 1


@pytest.mark.anyio
async def test_ibkr_monitor_reconciliation_runs_off_the_event_loop(monkeypatch, tmp_path):
    event_loop_thread_id = threading.get_ident()
    reconciliation_thread_ids: list[int] = []

    class FakeRuntime:
        enabled = True

        def diagnostics(self):
            reconciliation_thread_ids.append(threading.get_ident())
            return {
                "connected": False,
                "reconnect_count": 0,
                "active_quote_subscriptions": 0,
            }

    class FakeProvider:
        runtime = FakeRuntime()

    class FakeRegistry:
        def provider(self, provider_id):
            assert provider_id == "ibkr"
            return FakeProvider()

    class FakeMarketService:
        registry = FakeRegistry()

    monkeypatch.setattr(monitor_module, "IbkrEquityProvider", FakeProvider)
    monitor, _, _ = _monitor(tmp_path)
    monitor.strategy_repository_factory = object
    monitor.market_service_factory = FakeMarketService
    monkeypatch.setattr(
        monitor,
        "_active_demand",
        lambda _repository, *, now: {INSTRUMENT},
    )
    monkeypatch.setattr(
        monitor,
        "_ensure_subscription",
        lambda _service, _provider, _instrument: reconciliation_thread_ids.append(
            threading.get_ident()
        ),
    )

    result = await monitor.run_once()

    assert result == 0
    assert reconciliation_thread_ids
    assert all(thread_id != event_loop_thread_id for thread_id in reconciliation_thread_ids)
