from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.apps.trading import session_reconciliation_monitor as reconciliation
from app.apps.trading.session_evidence import (
    build_session_evidence_manifest,
    provider_evidence_input,
)
from app.apps.trading.session_reconciliation_monitor import TradingSessionReconciliationMonitor
from app.apps.trading.strategy_repository import StrategyEvent


SESSION = date(2026, 9, 17)
AT = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)
SETTLED = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
SESSION_EVENING = datetime(2026, 9, 17, 21, 0, tzinfo=timezone.utc)
AAA = "equity:NASDAQ:AAA"
BBB = "equity:NASDAQ:BBB"
GAPS = ValueError("formal_sip_5m_session_incomplete:FEATURE_WINDOW_HAS_GAPS")


class _Universe:
    universe_id = "universe-2026-09-17"
    evaluation_time = AT
    candidates = (
        SimpleNamespace(instrument_id=AAA),
        SimpleNamespace(instrument_id=BBB),
    )

    def model_dump(self, mode="json"):
        return {
            "universe_id": self.universe_id,
            "evaluation_time": self.evaluation_time.isoformat(),
            "candidates": [item.instrument_id for item in self.candidates],
        }


EVENTS = [
    StrategyEvent(
        strategy_id="strategy",
        event_id="a",
        run_id="run-1",
        instrument_id=AAA,
        event_type="ai_v3_decision",
        state="watch",
        reason_code="TEST",
        observed_at=AT,
        idempotency_key="idem-a",
        payload={"universe_id": _Universe.universe_id},
    )
]


class _Repository:
    def get_config(self, strategy_id):
        return SimpleNamespace(strategy_id=strategy_id)

    def events_between(self, strategy_id, *, start_time, end_time, limit):
        return list(EVENTS)


class _Manifests:
    def __init__(self) -> None:
        self.saved = []

    def save_transition(self, previous, updated):
        self.saved.append(updated)
        return updated


def _manifest():
    return build_session_evidence_manifest(
        strategy_id="strategy",
        session_date=SESSION,
        events=EVENTS,
        universe=_Universe(),
        aggregation_start=AT,
        aggregation_end=AT + timedelta(hours=7),
        frozen_at=AT + timedelta(hours=7),
    )


def _monitor(monkeypatch, behaviour: dict[str, BaseException | None]):
    monkeypatch.setattr(
        reconciliation,
        "resolve_v2_evidence_archive_for_session",
        lambda config, repository, *, session_date: _Universe(),
    )
    monitor = TradingSessionReconciliationMonitor(sip_provider_factory=lambda: object())
    fetched: list[str] = []

    async def reconcile_symbol(provider, *, instrument_id, session_date, events):
        fetched.append(instrument_id)
        failure = behaviour.get(instrument_id)
        if failure is not None:
            raise failure
        inputs = tuple(
            provider_evidence_input(source_id=source_id, payload=[instrument_id], record_count=1)
            for source_id in reconciliation._sip_source_ids(instrument_id, session_date)
        )
        return {"labels": {"instrument": instrument_id}}, inputs

    monitor._reconcile_symbol = reconcile_symbol
    return monitor, fetched


def _attempt(monitor, manifests, manifest, now):
    asyncio.run(monitor._attempt_manifest(_Repository(), manifests, manifest, now=now))
    return manifests.saved[-1]


def test_a_retry_reuses_scored_symbols_and_fetches_only_the_failed_ones(monkeypatch):
    behaviour: dict[str, Exception | None] = {BBB: RuntimeError("provider down")}
    monitor, fetched = _monitor(monkeypatch, behaviour)
    manifests = _Manifests()

    deferred = _attempt(monitor, manifests, _manifest(), SETTLED)
    assert deferred.reconciliation_state == "RETRYING"
    assert fetched == [AAA, BBB]

    behaviour[BBB] = None
    fetched.clear()
    final = _attempt(monitor, manifests, deferred, SETTLED + timedelta(minutes=15))

    assert fetched == [BBB]
    assert final.reconciliation_state == "FINAL"
    payload = final.reconciliation_payload
    assert payload["reused_candidate_outcomes"] == [AAA]
    assert set(payload["outcomes"]) == {AAA, BBB}
    assert {item["source_id"] for item in payload["reconciliation_evidence"]} == {
        *reconciliation._sip_source_ids(AAA, SESSION),
        *reconciliation._sip_source_ids(BBB, SESSION),
    }


def test_deterministic_failures_on_a_settled_session_close_it_without_waiting_for_max_age(monkeypatch):
    monitor, _ = _monitor(monkeypatch, {BBB: GAPS})
    manifests = _Manifests()

    closed = _attempt(monitor, manifests, _manifest(), SETTLED)

    assert closed.reconciliation_state == "PERMANENTLY_UNSCORABLE"
    assert closed.next_retry_at is None
    assert closed.reconciliation_payload["close_reason"] == "deterministic_symbol_failures"
    assert closed.reconciliation_payload["deterministic_error_candidates"] == [BBB]
    # The symbol that did score stays on record; the session still scores whole or not at all.
    assert set(closed.reconciliation_payload["outcomes"]) == {AAA}


def test_deterministic_failures_on_the_session_day_wait_for_the_tape_to_settle(monkeypatch):
    monitor, _ = _monitor(monkeypatch, {BBB: GAPS})
    manifests = _Manifests()

    deferred = _attempt(monitor, manifests, _manifest(), SESSION_EVENING)

    assert deferred.reconciliation_state == "RETRYING"
    assert deferred.next_retry_at == SESSION_EVENING + timedelta(minutes=15)


def test_a_transient_failure_keeps_the_session_retrying(monkeypatch):
    monitor, _ = _monitor(
        monkeypatch,
        {AAA: GAPS, BBB: RuntimeError("alpaca request budget exhausted")},
    )
    manifests = _Manifests()

    deferred = _attempt(monitor, manifests, _manifest(), SETTLED)

    assert deferred.reconciliation_state == "RETRYING"
    assert deferred.reconciliation_payload["deterministic_error_candidates"] == [AAA]


def test_an_attempt_cut_off_by_the_cycle_timeout_keeps_the_symbols_it_scored(monkeypatch):
    import pytest

    behaviour: dict[str, BaseException | None] = {BBB: asyncio.CancelledError()}
    monitor, fetched = _monitor(monkeypatch, behaviour)
    manifests = _Manifests()

    with pytest.raises(asyncio.CancelledError):
        _attempt(monitor, manifests, _manifest(), SETTLED)
    checkpointed = manifests.saved[-1]
    assert checkpointed.reconciliation_state == "RETRYING"
    assert set(checkpointed.reconciliation_payload["outcomes"]) == {AAA}
    # The lease outlasts the cycle, so the cut-off attempt is not re-picked at once.
    assert checkpointed.next_retry_at >= SETTLED + timedelta(seconds=monitor.run_timeout_seconds())

    behaviour[BBB] = None
    fetched.clear()
    final = _attempt(monitor, manifests, checkpointed, SETTLED + timedelta(hours=1))

    assert fetched == [BBB]
    assert final.reconciliation_state == "FINAL"
    assert final.reconciliation_payload["reused_candidate_outcomes"] == [AAA]
