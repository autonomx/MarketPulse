from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.strategy_prospective_dataset import (
    matched_prospective_signal_outcomes,
    prospective_dataset_readiness,
)
from app.apps.trading.strategy_repository import StrategyEvent
from app.apps.trading.strategy_v2_qualification import (
    FROZEN_V2_PROFILE_FINGERPRINT,
    V2_QUALIFICATION_VERSION,
    V2_REPLAY_VERSION,
)


STRATEGY = "strategy-1"
INSTRUMENT = "equity:NASDAQ:TEST"
SESSION = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


def _live(event_id: str, observed_at: datetime, *, fingerprint: str = "a" * 64) -> StrategyEvent:
    return StrategyEvent(
        strategy_id=STRATEGY,
        event_id=event_id,
        instrument_id=INSTRUMENT,
        event_type="shadow_execution",
        state="entry_ready",
        reason_code="SHADOW_EXECUTION_OBSERVED",
        observed_at=observed_at,
        idempotency_key=f"idem-{event_id}",
        payload={
            "execution_authority": False,
            "universe_source": "auto_archive_shadow",
            "profile_fingerprint": FROZEN_V2_PROFILE_FINGERPRINT,
            "execution": {
                "execution_eligible": True,
                "prospective_signal_features": {
                    "schema_version": "v2-prospective-signal-features-1",
                    "immutable_fingerprint": fingerprint,
                    "completeness": {
                        "premarket_available": True,
                        "research_available": True,
                        "halt_history_complete": True,
                        "momentum_full_warmup": True,
                        "all_core_available": True,
                    },
                },
            },
        },
    )


def _replay(event_id: str, entry_time: datetime, r_result: str) -> StrategyEvent:
    return StrategyEvent(
        strategy_id=STRATEGY,
        event_id=event_id,
        instrument_id=INSTRUMENT,
        event_type="v2_shadow_replay_trade",
        state="replayed",
        reason_code="V2_SHADOW_REPLAY_TRADE",
        observed_at=entry_time + timedelta(hours=6),
        idempotency_key=f"idem-{event_id}",
        payload={
            "qualification_version": V2_QUALIFICATION_VERSION,
            "replay_version": V2_REPLAY_VERSION,
            "session_date": entry_time.date().isoformat(),
            "universe_source": "auto_archive_shadow",
            "profile_fingerprint": FROZEN_V2_PROFILE_FINGERPRINT,
            "entry_time": entry_time.isoformat(),
            "r_result": r_result,
            "mfe_r": "1.20",
            "mae_r": "-0.30",
        },
    )


def test_prospective_dataset_joins_closest_live_feature_row() -> None:
    events = [
        _live("live-far", SESSION - timedelta(minutes=8), fingerprint="b" * 64),
        _live("live-close", SESSION - timedelta(minutes=1), fingerprint="c" * 64),
        _replay("replay-1", SESSION, "1.5"),
    ]

    rows = matched_prospective_signal_outcomes(events)

    assert len(rows) == 1
    row = rows[0]
    assert row.live_event_id == "live-close"
    assert row.replay_event_id == "replay-1"
    assert row.r_result == Decimal("1.5")
    assert row.mfe_r == Decimal("1.20")
    assert row.mae_r == Decimal("-0.30")
    assert row.feature_fingerprint == "c" * 64
    assert row.won is True


def test_prospective_dataset_does_not_match_outside_frozen_window() -> None:
    rows = matched_prospective_signal_outcomes(
        [
            _live("live-1", SESSION - timedelta(minutes=11)),
            _replay("replay-1", SESSION, "-1"),
        ]
    )
    assert rows == ()


def test_prospective_dataset_readiness_reports_feature_coverage() -> None:
    next_session = SESSION + timedelta(days=1)
    events = [
        _live("live-win", SESSION - timedelta(minutes=1), fingerprint="d" * 64),
        _replay("replay-win", SESSION, "1.5"),
        _live("live-loss", next_session - timedelta(minutes=1), fingerprint="e" * 64),
        _replay("replay-loss", next_session, "-1"),
    ]

    rows = matched_prospective_signal_outcomes(events)
    assert len(rows) == 2
    readiness = prospective_dataset_readiness(rows)
    assert readiness["matched_trade_count"] == 2
    assert readiness["winner_count"] == 1
    assert readiness["loser_count"] == 1
    assert readiness["win_rate"] == "0.5"
    assert readiness["premarket_available_count"] == 2
    assert readiness["research_available_count"] == 2
    assert readiness["halt_history_complete_count"] == 2
    assert readiness["momentum_full_warmup_count"] == 2
    assert readiness["all_core_available_count"] == 2
    assert readiness["distinct_sessions"] == 2
    assert readiness["distinct_symbols"] == 1
