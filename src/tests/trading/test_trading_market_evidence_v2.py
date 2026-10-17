from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.market_evidence import (
    MARKET_EVIDENCE_POLICY_VERSION,
    PremarketLiquidityEvidence,
    SourceMemberDisposition,
    classify_provider_exception,
)
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_ai_shadow_monitor import TradingAIShadowMonitor
from app.apps.trading.strategy_data_integrity import finviz_atomic_source_locator
from app.apps.trading.strategy_evaluability import (
    assess_bar_coverage,
    assess_session_evaluability,
    build_trade_authorization,
    candidate_morning_evidence_eligible,
)
from app.apps.trading.strategy_managed_finviz_shadow import MANAGED_FINVIZ_SHADOW_STRATEGY_ID
from app.apps.trading.strategy_repository import StrategyEvent, TradingStrategyConfigDocument
from app.apps.trading.strategy_v2_qualification import (
    V2_QUALIFICATION_VERSION,
    V2_REPLAY_VERSION,
    evaluate_v2_prospective_qualification,
    managed_finviz_v2_config,
    v2_profile_fingerprint,
)
from app.apps.trading.strategies.failed_selloff_v2 import evaluate_gap_pullback_v2
from app.apps.trading.strategies.models import StrategyRiskProfile


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "2026-09-08-market-evidence-failures.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _liquidity(
    *,
    observed_at: datetime,
    volume: Decimal = Decimal("600000"),
    dollar_volume: Decimal = Decimal("3120000"),
    tod_rvol: Decimal = Decimal("6"),
) -> PremarketLiquidityEvidence:
    return PremarketLiquidityEvidence(
        policy_version=MARKET_EVIDENCE_POLICY_VERSION,
        provider="alpaca_iex",
        feed="iex",
        observed_at=observed_at,
        current_premarket_volume=volume,
        current_premarket_dollar_volume=dollar_volume,
        tod_rvol=tod_rvol,
        tod_rvol_numerator=volume,
        tod_rvol_denominator_mean=volume / tod_rvol,
        baseline_session_count=5,
        premarket_bar_count=315,
        nonzero_volume_bar_count=210,
        coverage_ratio=Decimal("1"),
        ready=True,
        reason_codes=(),
    )


def _candidate(
    symbol: str,
    *,
    observed_at: datetime,
    rank: int,
) -> GapperCandidate:
    liquidity = _liquidity(observed_at=observed_at)
    return GapperCandidate(
        instrument_id=f"equity:{symbol}",
        binding_id=f"replay:{symbol}",
        observed_at=observed_at,
        evidence_observed_at={"premarket_liquidity:alpaca_iex:iex": observed_at},
        previous_close=Decimal("4.00"),
        premarket_price=Decimal("5.20"),
        gap_pct=Decimal("30"),
        premarket_volume=liquidity.current_premarket_volume,
        premarket_dollar_volume=liquidity.current_premarket_dollar_volume,
        premarket_bar_count=liquidity.premarket_bar_count,
        tod_rvol=liquidity.tod_rvol,
        premarket_liquidity=liquidity,
        market_evidence_policy_version=MARKET_EVIDENCE_POLICY_VERSION,
        market_data_complete=True,
        spread_bps=None,
        discovery_rank=rank,
    )


def _universe(
    *,
    session_date: date,
    evaluation_time: datetime,
    candidates: list[GapperCandidate],
    dispositions: list[SourceMemberDisposition],
):
    return freeze_gapper_universe(
        universe_id=f"market-evidence-v2-{session_date.isoformat()}",
        session_date=session_date,
        evaluation_time=evaluation_time,
        discovery_source="finviz",
        source_locator=finviz_atomic_source_locator(
            "https://finviz.com/screener?v=340&s=ta_topgainers"
        ),
        source_candidate_symbols=[item.symbol for item in dispositions],
        source_member_dispositions=dispositions,
        candidates=candidates,
        allow_empty=not candidates,
    )


def _config(*, mode: str = "shadow") -> TradingStrategyConfigDocument:
    value = managed_finviz_v2_config()
    return TradingStrategyConfigDocument(
        strategy_id=MANAGED_FINVIZ_SHADOW_STRATEGY_ID,
        account_id="market-evidence-v2-paper",
        strategy_kind="gap_pullback_v1",
        strategy_version="2.0.0",
        mode=mode,
        active_universe_id=None,
        config=value,
        risk=StrategyRiskProfile(),
        enabled=True,
    )


def _event(
    *,
    strategy_id: str,
    event_type: str,
    instrument_id: str,
    observed_at: datetime,
    payload: dict[str, object],
    reason_code: str,
) -> StrategyEvent:
    raw = f"{strategy_id}|{event_type}|{instrument_id}|{observed_at.isoformat()}|{payload}"
    idem = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return StrategyEvent(
        strategy_id=strategy_id,
        event_id=idem[:32],
        instrument_id=instrument_id,
        event_type=event_type,
        state="replayed",
        reason_code=reason_code,
        observed_at=observed_at,
        idempotency_key=idem,
        payload=payload,
    )


def test_sep8_partial_source_failure_is_not_qualification_or_trade_authority() -> None:
    fixture = _fixture()
    evaluation = datetime.fromisoformat(str(fixture["evaluation_time_utc"]))
    session_date = date.fromisoformat(str(fixture["session_date"]))
    good = _candidate("GOOD", observed_at=evaluation, rank=1)
    failed = fixture["failed_source_member"]
    assert isinstance(failed, dict)
    universe = _universe(
        session_date=session_date,
        evaluation_time=evaluation,
        candidates=[good],
        dispositions=[
            SourceMemberDisposition(
                symbol="GOOD",
                source_rank=1,
                status="materialized",
                instrument_id=good.instrument_id,
            ),
            SourceMemberDisposition(
                symbol=str(failed["symbol"]),
                source_rank=int(failed["source_rank"]),
                status="enrichment_failed",
                reason_codes=tuple(str(item) for item in failed["reason_codes"]),
            ),
        ],
    )

    evaluability = assess_session_evaluability(universe, managed_finviz_v2_config())
    expected = fixture["expected"]
    assert isinstance(expected, dict)
    assert evaluability.status == expected["session_status"]
    assert evaluability.qualification_eligible is expected["qualification_eligible"]
    assert evaluability.evaluable_candidate_count == 1
    assert evaluability.source_failure_count == 1

    authorization = build_trade_authorization(
        strategy_id="sep8",
        instrument_id=good.instrument_id,
        trade_attempt_id="attempt-sep8",
        universe_id=universe.universe_id,
        strategy_profile_fingerprint="profile",
        source_member_valid_value=True,
        morning_evidence_eligible=True,
        session_evaluability_complete=False,
        bar_coverage_ready=True,
        strategy_entry_ready=True,
        execution_observation_present=True,
        execution_eligible=True,
        risk_sizing_valid=True,
        session_open=True,
        provider_ready=True,
        provider_circuit_clear=True,
        strategy_kill_switch_clear=True,
        qualification_authorized=True,
        profile_matches=True,
        evidence_policy_matches=True,
    )
    assert authorization.authorized is False
    assert expected["authorization_reason"] in authorization.reason_codes


def test_bar_coverage_and_provider_failures_are_typed_not_synthetic() -> None:
    fixture = _fixture()
    failure = fixture["bar_failure"]
    execution_failure = fixture["execution_failure"]
    expected = fixture["expected"]
    assert isinstance(failure, dict)
    assert isinstance(execution_failure, dict)
    assert isinstance(expected, dict)

    observed_at = datetime.fromisoformat(str(failure["observed_at_utc"]))
    missing = datetime.fromisoformat(str(failure["missing_regular_minute_utc"]))
    session_date = observed_at.date()
    starts = [
        datetime(2026, 9, 8, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=index)
        for index in range(6)
    ]
    bars = [
        MarketBar(
            instrument_id="equity:GOOD",
            interval="1m",
            start_time=start,
            end_time=start + timedelta(minutes=1),
            open=Decimal("5"),
            high=Decimal("5.1"),
            low=Decimal("4.9"),
            close=Decimal("5"),
            volume=Decimal("1000"),
            provider="yahoo",
            session="regular",
            is_final=True,
            received_at=start + timedelta(minutes=1),
        )
        for start in starts
        if start != missing
    ]
    coverage = assess_bar_coverage(
        bars,
        session_date=session_date,
        observed_at=observed_at,
        provider="yahoo",
    )
    assert coverage.ready is False
    assert expected["bar_reason"] in coverage.reason_codes
    assert missing in coverage.missing_minutes

    readiness = classify_provider_exception(
        RuntimeError(str(execution_failure["exception_text"])),
        provider=str(execution_failure["provider"]),
        observed_at=observed_at,
    )
    assert readiness.ready is False
    assert readiness.state == expected["provider_readiness"]


def test_trade_authorization_is_exact_conjunction_of_all_predicates() -> None:
    base = {
        "source_member_valid_value": True,
        "morning_evidence_eligible": True,
        "session_evaluability_complete": True,
        "bar_coverage_ready": True,
        "strategy_entry_ready": True,
        "execution_observation_present": True,
        "execution_eligible": True,
        "risk_sizing_valid": True,
        "session_open": True,
        "provider_ready": True,
        "provider_circuit_clear": True,
        "strategy_kill_switch_clear": True,
        "qualification_authorized": True,
        "profile_matches": True,
        "evidence_policy_matches": True,
    }
    common = {
        "strategy_id": "strategy",
        "instrument_id": "equity:GOOD",
        "trade_attempt_id": "attempt",
        "universe_id": "universe",
        "strategy_profile_fingerprint": "profile",
    }
    healthy = build_trade_authorization(**common, **base)
    assert healthy.authorized is True
    assert healthy.reason_codes == ()

    for predicate in base:
        values = dict(base)
        values[predicate] = False
        denied = build_trade_authorization(**common, **values)
        assert denied.authorized is False, predicate
        assert denied.reason_codes, predicate


class _MemoryRepository:
    def __init__(self, universe) -> None:
        self.universe = universe
        self.events: list[StrategyEvent] = []
        self.keys: set[str] = set()

    def get_universe(self, universe_id):
        assert universe_id == self.universe.universe_id
        return self.universe

    def append_event(self, event: StrategyEvent):
        if event.idempotency_key in self.keys:
            return False
        self.keys.add(event.idempotency_key)
        self.events.append(event)
        return True


class _BombAnalyzer:
    calls = 0

    def assess(self, **kwargs):
        type(self).calls += 1
        raise AssertionError("LLM must not run before common cohort watermark")


def test_minute_ai_waits_for_complete_common_cohort_watermark() -> None:
    _BombAnalyzer.calls = 0
    evaluation = datetime(2026, 9, 8, 13, 15, tzinfo=timezone.utc)
    left = _candidate("LEFT", observed_at=evaluation, rank=1)
    right = _candidate("RIGHT", observed_at=evaluation, rank=2)
    universe = _universe(
        session_date=date(2026, 9, 8),
        evaluation_time=evaluation,
        candidates=[left, right],
        dispositions=[
            SourceMemberDisposition(
                symbol="LEFT",
                source_rank=1,
                status="materialized",
                instrument_id=left.instrument_id,
            ),
            SourceMemberDisposition(
                symbol="RIGHT",
                source_rank=2,
                status="materialized",
                instrument_id=right.instrument_id,
            ),
        ],
    )
    repository = _MemoryRepository(universe)
    monitor = TradingAIShadowMonitor(analyzer_factory=_BombAnalyzer, interval_seconds=5)
    config = _config(mode="shadow")
    row = {
        "candidate": left,
        "observed_at": datetime(2026, 9, 8, 13, 41, tzinfo=timezone.utc),
        "universe_id": universe.universe_id,
    }

    asyncio.run(
        monitor._run_policy(
            policy="minute",
            rows=[row],
            config=config,
            repository=repository,
            market_service=object(),
            events=[],
        )
    )

    assert _BombAnalyzer.calls == 0
    gap = next(event for event in repository.events if event.event_type == "ai_shadow_input_gap")
    assert gap.reason_code == "AI_SHADOW_COHORT_WATERMARK_PENDING"
    assert gap.payload["cohort_complete"] is False
    assert gap.payload["expected_instrument_ids"] == [left.instrument_id, right.instrument_id]


def test_v2_replay_trade_requires_clean_session_assessment_to_count() -> None:
    config = _config(mode="shadow")
    profile = v2_profile_fingerprint(config.config)
    session = date(2026, 9, 1)
    live_at = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    entry_at = live_at + timedelta(minutes=1)
    trade = _event(
        strategy_id=config.strategy_id,
        event_type="v2_shadow_replay_trade",
        instrument_id="equity:GOOD",
        observed_at=entry_at + timedelta(hours=2),
        reason_code="V2_SHADOW_REPLAY_TRADE",
        payload={
            "qualification_version": V2_QUALIFICATION_VERSION,
            "replay_version": V2_REPLAY_VERSION,
            "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
            "session_date": session.isoformat(),
            "universe_source": "auto_archive_shadow",
            "profile_fingerprint": profile,
            "entry_time": entry_at.isoformat(),
            "r_result": "0.5",
            "execution_authority": False,
        },
    )
    live = _event(
        strategy_id=config.strategy_id,
        event_type="shadow_execution",
        instrument_id="equity:GOOD",
        observed_at=live_at,
        reason_code="SHADOW_EXECUTION_OBSERVED",
        payload={
            "universe_source": "auto_archive_shadow",
            "profile_fingerprint": profile,
            "execution_authority": False,
            "execution": {"execution_eligible": True},
        },
    )
    without_session = evaluate_v2_prospective_qualification(config, [live, trade])
    assert without_session.replay_trade_count == 0
    assert without_session.matched_eligible_trade_count == 0

    clean_session = _event(
        strategy_id=config.strategy_id,
        event_type="v2_shadow_replay_session",
        instrument_id=f"strategy:{config.strategy_id}",
        observed_at=entry_at + timedelta(hours=3),
        reason_code="V2_SHADOW_REPLAY_COMPLETED",
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
    with_session = evaluate_v2_prospective_qualification(
        config,
        [live, trade, clean_session],
    )
    assert with_session.replay_trade_count == 1
    assert with_session.matched_eligible_trade_count == 1


def test_managed_finviz_membership_is_ready_despite_premarket_research_gaps() -> None:
    observed = datetime(2026, 9, 8, 13, 15, tzinfo=timezone.utc)
    candidate = GapperCandidate(
        instrument_id="equity:US:RECOVER",
        binding_id="yahoo:historical_polling:equity:US:RECOVER",
        observed_at=observed,
        evidence_observed_at={"finviz_top_gainers": observed},
        previous_close=Decimal("1"),
        premarket_price=Decimal("1"),
        gap_pct=Decimal("0"),
        premarket_volume=Decimal("0"),
        premarket_dollar_volume=Decimal("0"),
        tod_rvol=None,
        market_data_complete=False,
        data_quality_flags=("YAHOO_CHART_PROVIDERDATAUNAVAILABLEERROR",),
        discovery_rank=1,
    )
    config = managed_finviz_v2_config()
    eligible, reasons = candidate_morning_evidence_eligible(candidate, config)
    assert eligible is True
    assert reasons == ()
    result = evaluate_gap_pullback_v2(candidate, (), config)
    assert result.state == "qualified_gap"
    assert result.reason_code == "WAITING_FOR_REGULAR_SESSION"

