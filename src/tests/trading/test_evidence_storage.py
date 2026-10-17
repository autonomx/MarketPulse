"""Market evidence storage: content-identified revisions and batched IBKR writes (WP-8.3)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.evidence_storage import MemoryEvidenceBackend, MemoryIbkrSessionEvidence, content_hash, distinct_revisions
from app.apps.trading.ibkr_evidence import IbkrEvidenceStore
from app.apps.trading.models import MarketBar
from app.apps.trading.yahoo_evidence import YahooEvidenceStore

START = datetime(2026, 9, 17, 13, 31, tzinfo=timezone.utc)


def _bar(received: datetime, close: str = "10") -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="1m",
        start_time=START,
        end_time=START + timedelta(minutes=1),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("100"),
        provider="yahoo",
        provider_event_id="1789652460",
        received_at=received,
    )


def test_seeing_the_same_bar_again_adds_no_revision_and_keeps_the_first_receipt() -> None:
    backend = MemoryEvidenceBackend()
    store = YahooEvidenceStore(backend)
    first = START + timedelta(minutes=2)

    assert store.persist_market_bars([_bar(first + timedelta(minutes=5))]) == 1
    assert store.persist_market_bars([_bar(first)]) == 0  # same content, seen earlier
    assert store.persist_market_bars([_bar(first + timedelta(minutes=9))]) == 0
    assert store.persist_market_bars([_bar(first, close="11")]) == 1  # a real revision

    [original, revision] = sorted(backend.revisions("TEST", date(2026, 9, 17)), key=lambda row: row["close"])
    assert original["received_at"] == first.isoformat()
    assert revision["close"] == "11"
    assert store.diagnostics()["persisted_bar_count"] == 2


def test_revision_identity_ignores_only_the_receipt_time() -> None:
    record = {
        "start_time": START.isoformat(), "end_time": (START + timedelta(minutes=1)).isoformat(),
        "open": "10.0", "high": "10", "low": "10.00", "close": "10", "volume": "100",
        "session": "regular", "provider_event_id": "1", "received_at": START.isoformat(),
    }
    later = {**record, "received_at": (START + timedelta(hours=1)).isoformat(), "open": "10"}
    [(_, kept)] = distinct_revisions([later, record]).items()
    assert kept["received_at"] == START.isoformat()
    assert content_hash(kept) != content_hash({**kept, "close": "11"})


class _CountingSessions(MemoryIbkrSessionEvidence):
    def __init__(self) -> None:
        super().__init__()
        self.applies = 0

    def apply(self, session_date, empty, mutators):
        self.applies += 1
        super().apply(session_date, empty, mutators)


def test_ibkr_quote_evidence_is_written_in_batches_and_read_complete() -> None:
    sessions = _CountingSessions()
    store = IbkrEvidenceStore(sessions, flush_events=3, flush_seconds=3600)
    session = date(2026, 9, 17)

    store.record_missing_quote(session, "NO_DATA")
    store.record_missing_quote(session, "NO_DATA")
    assert sessions.applies == 0
    store.record_missing_quote(session, "NO_DATA")
    assert sessions.applies == 1

    store.record_subscription_error(session, "DENIED")
    diagnostics = store.session_diagnostics(session)  # a read applies what is queued
    assert sessions.applies == 2
    assert diagnostics["missing_quote_count"] == 3
    assert diagnostics["reason_counts"] == {"DENIED": 1, "NO_DATA": 3}


def test_a_failed_ibkr_write_is_dropped_without_raising_on_the_market_data_thread() -> None:
    class Broken(MemoryIbkrSessionEvidence):
        def apply(self, session_date, empty, mutators):
            raise OSError("database unavailable")

    store = IbkrEvidenceStore(Broken(), flush_events=1)
    store.record_missing_quote(date(2026, 9, 17), "NO_DATA")


def test_network_thread_quotes_only_queue_and_the_ring_drops_the_oldest() -> None:
    sessions = MemoryIbkrSessionEvidence()
    store = IbkrEvidenceStore(sessions, flush_events=1, flush_seconds=0, max_pending=3)
    session = date(2026, 10, 5)
    for _ in range(5):
        store.record_quote(
            session, market_data_type="LIVE", live_entitled=True,
            quote_age_seconds=Decimal("0.1"), spread_bps=Decimal("2"), defer=True,
        )
    # Deferred updates never write from the caller's thread.
    assert sessions.read(session) is None
    assert store.pending_count() == 3
    assert store.dropped_update_count == 2

    store.flush()
    assert sessions.read(session)["quote_event_count"] == 3
    assert store.pending_count() == 0
