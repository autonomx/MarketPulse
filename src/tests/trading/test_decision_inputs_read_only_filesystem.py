"""No trading decision input is read from (or written to) local files (WP-8.3 acceptance)."""
from __future__ import annotations

import builtins
import importlib
import io
import pathlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.evidence_storage import MemoryEvidenceBackend, MemoryIbkrSessionEvidence
from app.apps.trading.ibkr_evidence import IbkrEvidenceStore
from app.apps.trading.models import MarketBar
from app.apps.trading.yahoo_evidence import YahooEvidenceStore


@pytest.fixture()
def read_only_filesystem(monkeypatch):
    """Every file open, read or write fails; the attempts are recorded.

    The PostgreSQL driver loads first: importing psycopg reads its installed package metadata (its version), which is
    no decision input, and a failed read would leave the module without a version for the rest of the process.
    """
    importlib.import_module("psycopg_pool")
    attempts: list[str] = []

    def refuse(target, *args, **kwargs):
        attempts.append(str(target))
        raise PermissionError(f"read-only filesystem: {target}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(io, "open", refuse)
    for name in ("open", "read_text", "read_bytes", "write_text", "write_bytes", "mkdir", "touch", "unlink"):
        monkeypatch.setattr(pathlib.Path, name, lambda self, *args, **kwargs: refuse(self))
    yield attempts
    assert attempts == []


def _bars(session: date) -> list[MarketBar]:
    open_ = datetime.combine(session, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=9)
    return [
        MarketBar(
            instrument_id="equity:NASDAQ:TEST",
            interval="1m",
            start_time=open_ + timedelta(minutes=index),
            end_time=open_ + timedelta(minutes=index + 1),
            open=Decimal("10"), high=Decimal("10.5"), low=Decimal("9.5"), close=Decimal("10"),
            volume=Decimal("1000"),
            session="extended_pre",
            provider="yahoo",
            provider_event_id=str(index),
            received_at=open_ + timedelta(minutes=index + 1, seconds=5),
        )
        for index in range(30)
    ]


def test_yahoo_evidence_works_without_the_filesystem(read_only_filesystem) -> None:
    store = YahooEvidenceStore(MemoryEvidenceBackend())
    session = date(2026, 9, 17)
    for offset in range(6):
        store.persist_market_bars(_bars(session - timedelta(days=offset)))

    bars = store.load_market_bars(
        "equity:NASDAQ:TEST",
        start=datetime(2026, 9, 17, 9, tzinfo=timezone.utc),
        end=datetime(2026, 9, 17, 10, tzinfo=timezone.utc),
        knowledge_mode="causal_replay",
        known_by=datetime(2026, 9, 17, 10, tzinfo=timezone.utc),
    )
    assert len(bars) == 30
    store.premarket_relative_volume("TEST", datetime(2026, 9, 17, 9, 40, tzinfo=timezone.utc))
    store.record_repair(attempted=True, recovered_bar_count=1, unresolved=False)
    store.record_evaluation_outcome(repaired=True, unresolved=False, session_date=session)
    assert store.diagnostics()["persisted_bar_count"] == 180
    assert store.session_diagnostics(session)["passed_evaluation_count"] == 1


def test_ibkr_evidence_and_the_market_data_cache_work_without_the_filesystem(read_only_filesystem) -> None:
    ibkr = IbkrEvidenceStore(MemoryIbkrSessionEvidence(), flush_events=1)
    ibkr.record_quote(
        date(2026, 9, 17), market_data_type="LIVE", live_entitled=True,
        quote_age_seconds=Decimal("0.5"), spread_bps=Decimal("4"),
    )
    assert ibkr.session_diagnostics(date(2026, 9, 17))["quote_event_count"] == 1

    cache = TradingMarketDataCache(max_entries=4)
    value, _, cached = cache.get_or_load("bars", lambda: {"bars": []}, ttl_seconds=60, source="test")
    assert value == {"bars": []} and cached is False
    assert cache.get_or_load("bars", lambda: {"bars": [1]}, ttl_seconds=60, source="test")[2] is True


def test_the_default_stores_do_not_touch_the_filesystem(read_only_filesystem) -> None:
    YahooEvidenceStore().diagnostics()
    IbkrEvidenceStore().session_diagnostics(date(2026, 9, 17))
