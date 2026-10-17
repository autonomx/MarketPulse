from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.apps.trading.session_evidence import (
    begin_reconciliation,
    build_session_evidence_manifest,
    defer_reconciliation,
    file_evidence_input,
    finalize_reconciliation,
)
from app.apps.trading.strategy_repository import StrategyEvent


AT = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)


class _Universe:
    universe_id = "universe-2026-09-17"
    evaluation_time = AT
    candidates = (
        SimpleNamespace(instrument_id="equity:NASDAQ:AAA"),
        SimpleNamespace(instrument_id="equity:NASDAQ:BBB"),
    )

    def model_dump(self, mode="json"):
        return {
            "universe_id": self.universe_id,
            "evaluation_time": self.evaluation_time.isoformat(),
            "candidates": [item.instrument_id for item in self.candidates],
        }


def _event(event_id: str, minute: int) -> StrategyEvent:
    at = AT + timedelta(minutes=minute)
    return StrategyEvent(
        strategy_id="strategy",
        event_id=event_id,
        run_id="run-1",
        instrument_id="equity:NASDAQ:AAA",
        event_type="ai_v3_decision",
        state="watch",
        reason_code="TEST",
        observed_at=at,
        idempotency_key=f"idem-{event_id}",
        payload={
            "arm": "ai-shadow-v3-canonical-1",
            "universe_id": _Universe.universe_id,
        },
    )


def test_manifest_hash_and_id_are_order_independent_for_same_exact_events():
    first = _event("a", 0)
    second = _event("b", 1)
    kwargs = dict(
        strategy_id="strategy",
        session_date=date(2026, 9, 17),
        universe=_Universe(),
        aggregation_start=AT,
        aggregation_end=AT + timedelta(hours=7),
        frozen_at=AT + timedelta(hours=7),
    )
    a = build_session_evidence_manifest(events=[first, second], **kwargs)
    b = build_session_evidence_manifest(events=[second, first], **kwargs)
    assert a.manifest_id == b.manifest_id
    assert a.immutable_fingerprint == b.immutable_fingerprint
    assert a.frozen_scope.run_ids == ("run-1",)
    assert a.frozen_scope.arm_ids == ("ai-shadow-v3-canonical-1",)


def test_file_input_hash_is_explicitly_part_of_frozen_manifest_scope():
    file_input = file_evidence_input(
        source_id="auto_trading.jsonl",
        content=b'{"event":"one"}\n',
        record_count=1,
    )
    manifest = build_session_evidence_manifest(
        strategy_id="strategy",
        session_date=date(2026, 9, 17),
        events=[_event("a", 0)],
        universe=_Universe(),
        aggregation_start=AT,
        aggregation_end=AT + timedelta(hours=7),
        frozen_at=AT + timedelta(hours=7),
        extra_inputs=(file_input,),
    )
    assert any(
        item.source_type == "input_file"
        and item.source_id == "auto_trading.jsonl"
        and len(item.sha256) == 64
        for item in manifest.frozen_scope.evidence_inputs
    )


def test_reconciliation_transitions_never_mutate_frozen_scope():
    manifest = build_session_evidence_manifest(
        strategy_id="strategy",
        session_date=date(2026, 9, 17),
        events=[_event("a", 0)],
        universe=_Universe(),
        aggregation_start=AT,
        aggregation_end=AT + timedelta(hours=7),
        frozen_at=AT + timedelta(hours=7),
    )
    fingerprint = manifest.immutable_fingerprint
    retrying = begin_reconciliation(
        manifest,
        observed_at=AT + timedelta(hours=8),
        lease_until=AT + timedelta(hours=8, minutes=15),
    )
    assert retrying.attempt_count == 1
    deferred = defer_reconciliation(
        retrying,
        observed_at=AT + timedelta(hours=8),
        next_retry_at=AT + timedelta(hours=8, minutes=15),
        payload={"errors": {"AAA": "provider unavailable"}},
    )
    assert deferred.immutable_fingerprint == fingerprint

    retrying_again = begin_reconciliation(
        deferred,
        observed_at=AT + timedelta(hours=8, minutes=15),
        lease_until=AT + timedelta(hours=8, minutes=30),
    )
    final = finalize_reconciliation(
        retrying_again,
        observed_at=AT + timedelta(hours=8, minutes=16),
        payload={"outcomes": {"AAA": {"label": True}}},
    )
    assert final.reconciliation_state == "FINAL"
    assert final.immutable_fingerprint == fingerprint


def test_an_attempt_leases_the_manifest_so_an_interrupted_one_is_not_retried_at_once():
    manifest = build_session_evidence_manifest(
        strategy_id="strategy",
        session_date=date(2026, 9, 17),
        events=[_event("a", 0)],
        universe=_Universe(),
        aggregation_start=AT,
        aggregation_end=AT + timedelta(hours=7),
        frozen_at=AT + timedelta(hours=7),
    )
    observed = AT + timedelta(hours=8)
    lease = observed + timedelta(minutes=15)

    retrying = begin_reconciliation(manifest, observed_at=observed, lease_until=lease)

    # ``pending`` skips a RETRYING manifest until next_retry_at; NULL would make it due again immediately.
    assert retrying.reconciliation_state == "RETRYING"
    assert retrying.next_retry_at == lease
    with pytest.raises(ValueError, match="reconciliation_lease_must_follow_observation"):
        begin_reconciliation(manifest, observed_at=observed, lease_until=observed)
