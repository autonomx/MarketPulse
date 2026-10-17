from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from app.apps.trading.market_evidence import MARKET_EVIDENCE_POLICY_VERSION
from app.apps.trading.strategies.models import GapPullbackConfig, StrategyRiskProfile
from app.apps.trading.strategy_repository import StrategyEvent, TradingStrategyConfigDocument
from app.apps.trading.strategy_v2_qualification import (
    FROZEN_V2_PROFILE_FINGERPRINT,
    MANAGED_FINVIZ_V2_PROFILE_FINGERPRINT,
    V2_PROSPECTIVE_START,
    V2_QUALIFICATION_VERSION,
    V2_REPLAY_VERSION,
    evaluate_v2_prospective_qualification,
    frozen_v2_config,
    managed_finviz_v2_config,
    v2_profile_fingerprint,
    v2_qualification_profile_fingerprint,
)


def _strategy(config: GapPullbackConfig | None = None) -> TradingStrategyConfigDocument:
    active = config or frozen_v2_config()
    return TradingStrategyConfigDocument(
        strategy_id="v2-prospective",
        account_id="paper-1",
        strategy_kind="gap_pullback_v1",
        strategy_version=active.strategy_version,
        mode="shadow",
        active_universe_id=None,
        config=active,
        risk=StrategyRiskProfile(),
        enabled=True,
    )


def _event(
    *,
    event_type: str,
    instrument_id: str,
    observed_at: datetime,
    reason_code: str | None,
    payload: dict[str, object],
    suffix: str,
) -> StrategyEvent:
    idem = hashlib.sha256(f"{event_type}|{instrument_id}|{observed_at.isoformat()}|{suffix}".encode()).hexdigest()
    return StrategyEvent(
        strategy_id="v2-prospective",
        event_id=idem[:32],
        instrument_id=instrument_id,
        event_type=event_type,
        state="entry_ready",
        reason_code=reason_code,
        observed_at=observed_at,
        idempotency_key=idem,
        payload=payload,
    )


def _clean_session_event(*, session: date, observed_at: datetime, profile: str, suffix: str) -> StrategyEvent:
    return _event(
        event_type="v2_shadow_replay_session",
        instrument_id="strategy:v2-prospective",
        observed_at=observed_at,
        reason_code="V2_SHADOW_REPLAY_COMPLETED",
        suffix=suffix,
        payload={
            "qualification_version": V2_QUALIFICATION_VERSION,
            "replay_version": V2_REPLAY_VERSION,
            "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
            "session_date": session.isoformat(),
            "status": "completed",
            "qualification_eligible": True,
            "profile_fingerprint": profile,
            "execution_authority": False,
        },
    )


def _qualified_evidence() -> list[StrategyEvent]:
    events: list[StrategyEvent] = []
    for index in range(20):
        session = V2_PROSPECTIVE_START + timedelta(days=index)
        instrument = f"equity:SYM{index % 10}"
        signal_at = datetime.combine(session, time(14, 0), tzinfo=timezone.utc)
        entry_at = signal_at + timedelta(minutes=1)
        universe_id = f"auto-archive-{session.isoformat()}-0920-test"
        events.append(
            _event(
                event_type="shadow_execution",
                instrument_id=instrument,
                observed_at=signal_at,
                reason_code="SHADOW_EXECUTION_OBSERVED",
                suffix=f"live-{index}",
                payload={
                    "strategy_version": "2.0.0",
                    "mode": "shadow",
                    "universe_id": universe_id,
                    "universe_source": "auto_archive_shadow",
                    "profile_fingerprint": FROZEN_V2_PROFILE_FINGERPRINT,
                    "execution_authority": False,
                    "execution": {"execution_eligible": True},
                },
            )
        )
        events.append(
            _event(
                event_type="v2_shadow_replay_trade",
                instrument_id=instrument,
                observed_at=entry_at + timedelta(hours=1),
                reason_code="V2_SHADOW_REPLAY_TRADE",
                suffix=f"replay-{index}",
                payload={
                    "qualification_version": V2_QUALIFICATION_VERSION,
                    "replay_version": V2_REPLAY_VERSION,
                    "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
                    "session_date": session.isoformat(),
                    "universe_id": universe_id,
                    "universe_source": "auto_archive_shadow",
                    "profile_fingerprint": FROZEN_V2_PROFILE_FINGERPRINT,
                    "entry_time": entry_at.isoformat(),
                    "exit_time": (entry_at + timedelta(minutes=20)).isoformat(),
                    "r_result": "0.50",
                    "execution_authority": False,
                },
            )
        )
        events.append(
            _clean_session_event(
                session=session,
                observed_at=entry_at + timedelta(hours=1, minutes=1),
                profile=FROZEN_V2_PROFILE_FINGERPRINT,
                suffix=f"session-{index}",
            )
        )
    return events


def _economic_review(events: list[StrategyEvent], *, suffix: str = "economic-review") -> StrategyEvent:
    reviewed_at = max(event.observed_at for event in events) + timedelta(minutes=1)
    return _event(
        event_type="prospective_economic_auto_paper_review",
        instrument_id="strategy:v2-prospective",
        observed_at=reviewed_at,
        reason_code="PROSPECTIVE_ECONOMIC_AUTO_PAPER_REVIEW_APPROVED",
        suffix=suffix,
        payload={
            "policy_version": "prospective-economic-shadow-v1",
            "profile_fingerprint": "prospective-economic-profile-test",
            "v2_profile_fingerprint": FROZEN_V2_PROFILE_FINGERPRINT,
            "pipeline_evidence_fingerprint": "prospective-economic-pipeline-test",
            "approved": True,
            "review_note": "Prospective sample, sealed holdout and fresh SHADOW soak reviewed.",
            "execution_authority": False,
        },
    )


def test_frozen_v2_profile_fingerprint_is_stable_and_exact() -> None:
    canonical = frozen_v2_config()
    assert v2_profile_fingerprint(canonical) == FROZEN_V2_PROFILE_FINGERPRINT

    learning_only = canonical.model_copy(update={
        "intraday_learning_enabled": True,
        "stoch_trend_capture_enabled": True,
        "intraday_llm_enabled": True,
        "intraday_llm_top_n": 12,
        "intraday_llm_interval_minutes": 7,
    })
    assert v2_profile_fingerprint(learning_only) == FROZEN_V2_PROFILE_FINGERPRINT

    finviz_experiment = canonical.model_copy(update={"universe_discovery_source": "finviz"})
    assert v2_profile_fingerprint(finviz_experiment) != FROZEN_V2_PROFILE_FINGERPRINT

    changed = canonical.model_copy(update={"v2_maximum_l2_to_signal_minutes": 9})
    assert v2_profile_fingerprint(changed) != FROZEN_V2_PROFILE_FINGERPRINT


def test_qualification_requires_economic_pipeline_then_exact_v2_review() -> None:
    strategy = _strategy()
    events = _qualified_evidence()

    before_economic_review = evaluate_v2_prospective_qualification(strategy, events)

    assert before_economic_review.profile_match is True
    assert before_economic_review.replay_trade_count == 20
    assert before_economic_review.matched_eligible_trade_count == 20
    assert before_economic_review.distinct_sessions == 20
    assert before_economic_review.distinct_symbols == 10
    assert before_economic_review.execution_match_rate == Decimal("1")
    assert before_economic_review.expectancy_r == Decimal("0.50")
    assert before_economic_review.one_sided_90_lcb_r == Decimal("0.50")
    assert before_economic_review.max_drawdown_r == Decimal("0")
    assert before_economic_review.prospective_economic_reviewed is False
    assert before_economic_review.qualified is False
    assert before_economic_review.auto_paper_authorized is False
    assert "V2_PROSPECTIVE_ECONOMIC_PIPELINE_REVIEW_REQUIRED" in before_economic_review.reason_codes

    events.append(_economic_review(events))
    before_v2_review = evaluate_v2_prospective_qualification(strategy, events)
    assert before_v2_review.prospective_economic_reviewed is True
    assert before_v2_review.qualified is True
    assert before_v2_review.reviewed is False
    assert before_v2_review.auto_paper_authorized is False
    assert "V2_OPERATOR_REVIEW_REQUIRED" in before_v2_review.reason_codes

    review_at = max(event.observed_at for event in events) + timedelta(minutes=1)
    events.append(
        _event(
            event_type="v2_promotion_review",
            instrument_id="strategy:v2-prospective",
            observed_at=review_at,
            reason_code="V2_PROMOTION_REVIEW_APPROVED",
            suffix="v2-review",
            payload={
                "qualification_version": V2_QUALIFICATION_VERSION,
                "profile_fingerprint": before_v2_review.current_profile_fingerprint,
                "evidence_fingerprint": before_v2_review.evidence_fingerprint,
                "approved": True,
                "review_note": "Current V2 prospective execution evidence reviewed for AUTO PAPER.",
                "execution_authority": False,
            },
        )
    )

    after_review = evaluate_v2_prospective_qualification(strategy, events)
    assert after_review.qualified is True
    assert after_review.reviewed is True
    assert after_review.auto_paper_authorized is True
    assert "V2_OPERATOR_REVIEW_REQUIRED" not in after_review.reason_codes


def test_new_trade_invalidates_exact_v2_review_even_after_economic_pipeline_passes() -> None:
    strategy = _strategy()
    events = _qualified_evidence()
    events.append(_economic_review(events))
    qualified = evaluate_v2_prospective_qualification(strategy, events)
    review_at = max(event.observed_at for event in events) + timedelta(minutes=1)
    review = _event(
        event_type="v2_promotion_review",
        instrument_id="strategy:v2-prospective",
        observed_at=review_at,
        reason_code="V2_PROMOTION_REVIEW_APPROVED",
        suffix="review",
        payload={
            "qualification_version": V2_QUALIFICATION_VERSION,
            "profile_fingerprint": qualified.current_profile_fingerprint,
            "evidence_fingerprint": qualified.evidence_fingerprint,
            "approved": True,
            "review_note": "Initial V2 prospective execution evidence explicitly reviewed.",
            "execution_authority": False,
        },
    )
    events.append(review)
    assert evaluate_v2_prospective_qualification(strategy, events).auto_paper_authorized is True

    session = date(2026, 9, 20)
    signal_at = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)
    entry_at = signal_at + timedelta(minutes=1)
    instrument = "equity:NEW"
    universe_id = f"auto-archive-{session.isoformat()}-0920-test"
    events.extend([
        _event(
            event_type="shadow_execution",
            instrument_id=instrument,
            observed_at=signal_at,
            reason_code="SHADOW_EXECUTION_OBSERVED",
            suffix="new-live",
            payload={
                "universe_source": "auto_archive_shadow",
                "profile_fingerprint": FROZEN_V2_PROFILE_FINGERPRINT,
                "execution_authority": False,
                "execution": {"execution_eligible": True},
            },
        ),
        _event(
            event_type="v2_shadow_replay_trade",
            instrument_id=instrument,
            observed_at=entry_at + timedelta(hours=1),
            reason_code="V2_SHADOW_REPLAY_TRADE",
            suffix="new-replay",
            payload={
                "qualification_version": V2_QUALIFICATION_VERSION,
                "replay_version": V2_REPLAY_VERSION,
                "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
                "session_date": session.isoformat(),
                "universe_id": universe_id,
                "universe_source": "auto_archive_shadow",
                "profile_fingerprint": FROZEN_V2_PROFILE_FINGERPRINT,
                "entry_time": entry_at.isoformat(),
                "r_result": "0.50",
                "execution_authority": False,
            },
        ),
        _clean_session_event(
            session=session,
            observed_at=entry_at + timedelta(hours=1, minutes=1),
            profile=FROZEN_V2_PROFILE_FINGERPRINT,
            suffix="new-session",
        ),
    ])

    changed = evaluate_v2_prospective_qualification(strategy, events)
    assert changed.prospective_economic_reviewed is True
    assert changed.qualified is True
    assert changed.reviewed is False
    assert changed.auto_paper_authorized is False
    assert "V2_OPERATOR_REVIEW_REQUIRED" in changed.reason_codes


def test_profile_mismatch_and_missing_execution_match_fail_closed() -> None:
    changed_config = frozen_v2_config().model_copy(update={"minimum_tod_rvol": Decimal("4")})
    strategy = _strategy(changed_config)
    events = _qualified_evidence()
    first_live = next(event for event in events if event.event_type == "shadow_execution")
    events.remove(first_live)

    result = evaluate_v2_prospective_qualification(strategy, events)

    assert result.profile_match is False
    assert result.matched_eligible_trade_count == 19
    assert result.execution_match_rate == Decimal("0.95")
    assert result.qualified is False
    assert "V2_PROFILE_MISMATCH" in result.reason_codes
    assert "V2_MATCHED_TRADES_LOW" in result.reason_codes


def test_managed_finviz_profile_qualifies_only_from_exact_profile_evidence() -> None:
    managed = managed_finviz_v2_config()
    strategy = _strategy(managed)
    managed_profile = v2_profile_fingerprint(managed)

    assert managed_profile == MANAGED_FINVIZ_V2_PROFILE_FINGERPRINT
    assert managed_profile != FROZEN_V2_PROFILE_FINGERPRINT
    assert v2_qualification_profile_fingerprint(managed) == managed_profile

    canonical_events = _qualified_evidence()
    canonical_events.append(_economic_review(canonical_events))
    canonical_result = evaluate_v2_prospective_qualification(strategy, canonical_events)
    assert canonical_result.matched_eligible_trade_count == 0
    assert canonical_result.auto_paper_authorized is False

    events = []
    for event in _qualified_evidence():
        payload = dict(event.payload)
        if "profile_fingerprint" in payload:
            payload["profile_fingerprint"] = managed_profile
        events.append(event.model_copy(update={"payload": payload}))

    economic_base = _economic_review(events)
    economic = economic_base.model_copy(
        update={
            "payload": {
                **economic_base.payload,
                "v2_profile_fingerprint": managed_profile,
            }
        }
    )
    events.append(economic)

    before_review = evaluate_v2_prospective_qualification(strategy, events)
    assert before_review.profile_match is True
    assert before_review.expected_profile_fingerprint == managed_profile
    assert before_review.matched_eligible_trade_count == 20
    assert before_review.qualified is True
    assert before_review.reviewed is False
    assert before_review.auto_paper_authorized is False
    assert "V2_OPERATOR_REVIEW_REQUIRED" in before_review.reason_codes

    review_at = max(event.observed_at for event in events) + timedelta(minutes=1)
    events.append(
        _event(
            event_type="v2_promotion_review",
            instrument_id="strategy:v2-prospective",
            observed_at=review_at,
            reason_code="V2_PROMOTION_REVIEW_APPROVED",
            suffix="managed-finviz-review",
            payload={
                "qualification_version": V2_QUALIFICATION_VERSION,
                "profile_fingerprint": managed_profile,
                "evidence_fingerprint": before_review.evidence_fingerprint,
                "approved": True,
                "review_note": "Exact managed Finviz V2 evidence explicitly reviewed.",
                "execution_authority": False,
            },
        )
    )

    after_review = evaluate_v2_prospective_qualification(strategy, events)
    assert after_review.auto_paper_authorized is True

    changed = managed.model_copy(update={"v2_maximum_l2_to_signal_minutes": 9})
    assert v2_qualification_profile_fingerprint(changed) is None
    changed_result = evaluate_v2_prospective_qualification(_strategy(changed), events)
    assert changed_result.auto_paper_authorized is False
    assert "V2_PROFILE_MISMATCH" in changed_result.reason_codes
