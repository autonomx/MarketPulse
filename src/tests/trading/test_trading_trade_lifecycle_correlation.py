from pathlib import Path

from app.apps.trading.paper_analytics import lifecycle_funnel
from app.apps.trading.strategy_repository import StrategyEvent, _event
from datetime import datetime, timezone


def test_lifecycle_correlation_migration_uses_existing_canonical_trade_record() -> None:
    migration = Path(
        "src/app/apps/trading/migrations/0045_trading_trade_lifecycle_correlation.sql"
    ).read_text()
    for token in (
        "omnix_trading_paper_trade_records",
        "correlation_version",
        "strategy_revision",
        "strategy_run_id",
        "session_id",
        "setup_id",
        "trade_intent_id",
        "risk_decision_id",
        "protection_id",
        "entry_fill_ids",
        "exit_fill_ids",
        "lifecycle_state",
        "review_state",
        "trg_omnix_trading_strategy_event_correlation",
        "trg_omnix_trading_paper_trade_correlation",
    ):
        assert token in migration
    assert "CREATE TABLE" not in migration


def test_historical_strategy_revision_is_not_fabricated_during_backfill() -> None:
    migration = Path(
        "src/app/apps/trading/migrations/0045_trading_trade_lifecycle_correlation.sql"
    ).read_text()
    assert "the historical\n-- config revision cannot be inferred safely" in migration
    # The current config revision is read only by the BEFORE INSERT trigger for
    # future events; the bulk UPDATE intentionally does not overwrite the
    # strategy_revision column.
    bulk_backfill = migration.split("-- Existing event rows predate revision stamping.", 1)[1]
    bulk_backfill = bulk_backfill.split("ALTER TABLE omnix_trading_paper_trade_records", 1)[0]
    assert "strategy_revision =" not in bulk_backfill


def test_payload_boundary_migration_keeps_correlation_out_of_domain_payload() -> None:
    migration = Path(
        "src/app/persistence/migrations/0082_trading_strategy_event_payload_boundary.sql"
    ).read_text()

    assert "CREATE OR REPLACE FUNCTION omnix_trading_stamp_strategy_event_correlation()" in migration
    for token in (
        "correlation_version",
        "strategy_revision",
        "session_id",
        "setup_id",
        "trade_attempt_id",
        "trade_intent_id",
        "risk_decision_id",
    ):
        assert f"NEW.{token}" in migration

    # Preserve causal compatibility with writers that already persisted these
    # domain inputs, but never mirror the derived envelope back into JSONB.
    assert "payload_attempt := NULLIF(NEW.payload ->> 'trade_attempt_id', '');" in migration
    assert "risk_payload := NEW.payload -> 'risk_decision';" in migration
    assert "NEW.payload :=" not in migration
    assert "JSONB_BUILD_OBJECT" not in migration


def test_strategy_event_correlation_metadata_is_separate_from_domain_payload() -> None:
    observed = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
    event = _event(
        (
            "gap-v2",
            "event-1",
            "run-1",
            "equity:NASDAQ:TEST",
            "signal",
            "ready",
            None,
            observed,
            "idem-1",
            "trade-lifecycle-v2",
            12,
            "session-envelope",
            "setup-envelope",
            "attempt-envelope",
            "intent-envelope",
            "risk-envelope",
            {
                "setup_id": "domain-setup",
                "trade_attempt_id": "domain-attempt",
                "signal_quality": 0.9,
            },
        )
    )

    assert event.correlation_version == "trade-lifecycle-v2"
    assert event.strategy_revision == 12
    assert event.session_id == "session-envelope"
    assert event.setup_id == "setup-envelope"
    assert event.trade_attempt_id == "attempt-envelope"
    assert event.trade_intent_id == "intent-envelope"
    assert event.risk_decision_id == "risk-envelope"
    assert event.payload == {
        "setup_id": "domain-setup",
        "trade_attempt_id": "domain-attempt",
        "signal_quality": 0.9,
    }


def test_risk_decision_is_a_first_class_funnel_stage() -> None:
    observed = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    event = StrategyEvent(
        strategy_id="gap-v2",
        event_id="risk-1",
        run_id="run-1",
        instrument_id="equity:NASDAQ:TEST",
        event_type="risk_decision",
        state="approved",
        reason_code="RISK_ACCEPTED",
        observed_at=observed,
        idempotency_key="risk-1",
        payload={
            "session_id": "session-1",
            "setup_id": "setup-1",
            "trade_intent_id": "intent-1",
        },
    )
    funnel = {stage.stage: stage.count for stage in lifecycle_funnel([event])}
    assert funnel["RISK ELIGIBLE"] == 1
