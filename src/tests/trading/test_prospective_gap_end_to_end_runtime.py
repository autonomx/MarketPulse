from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.apps.trading.prospective_gap_runtime as runtime_module
from app.apps.trading.execution import ExecutionObservation
from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.market_data_window import (
    MarketDataWindow,
    detect_window_gaps,
    finalized_window_bars,
)
from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.prospective_gap_repository import ProspectiveGapRepository
from app.apps.trading.prospective_gap_runtime import (
    PortfolioEPolicy,
    PremarketFreezeRequest,
    PremarketInstrumentInput,
    ProspectiveClimatologyState,
    ProspectiveGapRuntime,
    SchedulerPremarketHandoff,
    SchedulerPremarketInstrumentInput,
)
from app.apps.trading.prospective_prediction_evidence import FrozenForecast
from app.apps.trading.prospective_prediction_operational import (
    OperationalConfirmationEvaluation,
    build_operational_formal_outcome,
)
from app.apps.trading.prospective_prediction_v41 import (
    DEFAULT_V41_SPEC,
    V41MechanismHeads,
    V41ScoreInputs,
    score_v41_raw_probability,
    session_eligible_for_v41_forward_validation,
)
from app.apps.trading.prospective_prediction_v4 import (
    CalibratorArtifact,
    CatalystDecomposition,
    ConfirmationTransitionReceipt,
    FinvizFrozenCohort,
    GrossReturnDistribution,
    MechanismRiskScores,
)
from app.apps.trading.strategy_repository import StrategyEvent


SESSION = date(2026, 9, 22)
PREMARKET_FREEZE = datetime(2026, 9, 22, 13, 22, tzinfo=timezone.utc)
FORMAL_CUTOFF = datetime(2026, 9, 22, 13, 29, tzinfo=timezone.utc)
OPEN = datetime(2026, 9, 22, 13, 30, tzinfo=timezone.utc)


def _bar(
    *,
    start: datetime,
    interval: str,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "1000",
    session: str,
    provider: str = "yahoo",
    received_at: datetime | None = None,
) -> MarketBar:
    minutes = 1 if interval == "1m" else 5
    return MarketBar(
        instrument_id="equity:US:AAA",
        interval=interval,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        provider=provider,
        provider_event_id=f"{provider}:{interval}:{start.isoformat()}",
        provider_sequence=int(start.timestamp()),
        received_at=received_at or start + timedelta(minutes=minutes),
        session=session,
        adjustment_mode=AdjustmentMode.RAW,
    )


def test_sparse_premarket_window_does_not_invent_missing_trade_bars() -> None:
    window = MarketDataWindow(
        start=datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 23, 8, 4, tzinfo=timezone.utc),
        interval="1m",
        session="extended_pre",
        include_extended_hours=True,
        continuity="sparse_event",
    )
    bars = [
        _bar(
            start=window.start,
            interval="1m",
            open_="10",
            high="10.1",
            low="9.9",
            close="10.05",
            session="extended_pre",
        ),
        _bar(
            start=window.start + timedelta(minutes=3),
            interval="1m",
            open_="10.05",
            high="10.2",
            low="10",
            close="10.15",
            session="extended_pre",
        ),
    ]
    assert detect_window_gaps(
        bars,
        window=window,
        knowledge_mode="live",
        knowledge_cutoff=window.end,
    ) == ()


def test_window_recovery_is_bounded_and_causal() -> None:
    window = MarketDataWindow(
        start=datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 22, 8, 4, tzinfo=timezone.utc),
        interval="1m",
        session="extended_pre",
        include_extended_hours=True,
    )
    bars = [
        _bar(
            start=window.start,
            interval="1m",
            open_="10",
            high="10.1",
            low="9.9",
            close="10.05",
            session="extended_pre",
        ),
        _bar(
            start=window.start + timedelta(minutes=1),
            interval="1m",
            open_="10.05",
            high="10.2",
            low="10",
            close="10.1",
            session="extended_pre",
        ),
        _bar(
            start=window.start + timedelta(minutes=2),
            interval="1m",
            open_="10.1",
            high="10.3",
            low="10.05",
            close="10.2",
            session="extended_pre",
            received_at=window.end + timedelta(minutes=1),
        ),
        _bar(
            start=window.start + timedelta(minutes=3),
            interval="1m",
            open_="10.2",
            high="10.4",
            low="10.1",
            close="10.3",
            session="extended_pre",
        ),
    ]
    canonical = finalized_window_bars(
        bars,
        window=window,
        knowledge_mode="live",
        knowledge_cutoff=window.end,
    )
    assert [bar.start_time for bar in canonical] == [
        window.start,
        window.start + timedelta(minutes=1),
        window.start + timedelta(minutes=3),
    ]
    gaps = detect_window_gaps(
        bars,
        window=window,
        knowledge_mode="live",
        knowledge_cutoff=window.end,
    )
    assert len(gaps) == 1
    assert gaps[0].start == window.start + timedelta(minutes=2)
    assert gaps[0].missing_bar_count == 1


def test_v41_is_preregistered_future_only_and_opening_exhaustion_is_real_input() -> None:
    assert DEFAULT_V41_SPEC.activation_state == "PRE_REGISTERED_NOT_ACTIVE"
    assert not session_eligible_for_v41_forward_validation(date(2026, 9, 21))
    assert session_eligible_for_v41_forward_validation(date(2026, 9, 22))

    base = V41MechanismHeads(
        fundamental_reprice_score=Decimal("0.6"),
        theme_squeeze_score=Decimal("0.5"),
        low_information_technical_score=Decimal("0.7"),
        continuation_demand_score=Decimal("0.8"),
        opening_exhaustion_score=Decimal("0.1"),
        supply_fade_score=Decimal("0.2"),
    )
    exhausted = base.model_copy(update={"opening_exhaustion_score": Decimal("0.9")})
    common = {
        "catalyst_strength": Decimal("0.7"),
        "catalyst_finality": Decimal("0.6"),
        "catalyst_freshness": Decimal("0.8"),
        "catalyst_materiality": Decimal("0.7"),
        "extension_exhaustion_score": Decimal("0.5"),
    }
    low_exhaustion = score_v41_raw_probability(
        V41ScoreInputs(**common, mechanisms=base)
    )
    high_exhaustion = score_v41_raw_probability(
        V41ScoreInputs(**common, mechanisms=exhausted)
    )
    assert high_exhaustion < low_exhaustion


class _MemoryStrategyRepository:
    def __init__(self) -> None:
        self.events: list[StrategyEvent] = []
        self.keys: set[tuple[str, str]] = set()

    def append_event(self, event: StrategyEvent) -> bool:
        key = (event.strategy_id, event.idempotency_key)
        if key in self.keys:
            return False
        self.keys.add(key)
        self.events.append(event)
        return True

    def events_between(self, strategy_id, *, start_time, end_time, limit=50_000):
        return [
            event
            for event in self.events
            if event.strategy_id == strategy_id
            and start_time <= event.observed_at < end_time
        ][:limit]


class _MarketService:
    def __init__(self) -> None:
        self.window_end: datetime | None = None
        self.window_knowledge_mode: str | None = None
        self.regular_5m = self._regular_5m()

    @staticmethod
    def _premarket_1m() -> tuple[MarketBar, ...]:
        start = PREMARKET_FREEZE - timedelta(minutes=3)
        return tuple(
            _bar(
                start=start + timedelta(minutes=index),
                interval="1m",
                open_=str(Decimal("14.5") + Decimal(index) / Decimal("10")),
                high=str(Decimal("14.7") + Decimal(index) / Decimal("10")),
                low=str(Decimal("14.4") + Decimal(index) / Decimal("10")),
                close=str(Decimal("14.6") + Decimal(index) / Decimal("10")),
                volume="100000",
                session="extended_pre",
                received_at=start + timedelta(minutes=index + 1),
            )
            for index in range(3)
        )

    @staticmethod
    def _regular_5m() -> tuple[MarketBar, ...]:
        rows = []
        price = Decimal("10")
        for index in range(78):
            start = OPEN + timedelta(minutes=index * 5)
            next_price = price + Decimal("0.03")
            rows.append(
                _bar(
                    start=start,
                    interval="5m",
                    open_=str(price),
                    high=str(next_price + Decimal("0.02")),
                    low=str(price - Decimal("0.01")),
                    close=str(next_price),
                    volume="10000",
                    session="regular",
                    provider="alpaca_sip",
                )
            )
            price = next_price
        return tuple(rows)

    def recovered_window_bars(self, instrument_id, **kwargs):
        self.window_end = kwargs["end"]
        self.window_knowledge_mode = kwargs.get("knowledge_mode")
        bars = self._premarket_1m()
        return SimpleNamespace(
            bars=bars,
            report=SimpleNamespace(
                coverage_ratio=Decimal("0.02"),
                unresolved_gaps=(),
                provider_error=None,
                dataset_fingerprint="premarket-dataset",
            ),
        )

    def recovered_bars(self, instrument_id, interval, limit, binding_id, **kwargs):
        bars = self.regular_5m if interval == "5m" else ()
        return SimpleNamespace(
            bars=bars,
            report=SimpleNamespace(unresolved_gaps=()),
        )

    def bars(self, instrument_id, interval, limit, binding_id):
        return SimpleNamespace(bars=list(self.regular_5m if interval == "5m" else ()))

    def execution_observation(self, instrument_id):
        return ExecutionObservation(
            instrument_id=instrument_id,
            binding_id="alpaca_sip:test",
            provider="alpaca_sip",
            bid=Decimal("10.00"),
            ask=Decimal("10.02"),
            last=Decimal("10.01"),
            source_time=OPEN + timedelta(minutes=10),
            received_at=OPEN + timedelta(minutes=10, seconds=1),
            session="regular",
            freshness_mode="live",
            market_data_eligible=True,
            paper_fill_eligible=True,
            execution_eligible=True,
        )


def _candidate() -> GapperCandidate:
    return GapperCandidate(
        instrument_id="equity:US:AAA",
        binding_id="alpaca_sip:test",
        observed_at=PREMARKET_FREEZE - timedelta(minutes=1),
        evidence_observed_at={
            "finviz_top_gainers": PREMARKET_FREEZE - timedelta(minutes=1)
        },
        previous_close=Decimal("10"),
        premarket_price=Decimal("15"),
        gap_pct=Decimal("50"),
        premarket_volume=Decimal("300000"),
        premarket_dollar_volume=Decimal("4500000"),
        tod_rvol=Decimal("6"),
        float_shares=Decimal("2000000"),
        spread_bps=Decimal("40"),
        catalyst_evidence_ids=("news-1",),
    )


def _premarket_request() -> PremarketFreezeRequest:
    candidate = _candidate()
    cohort = FinvizFrozenCohort(
        cohort_id="finviz-2026-09-22",
        session_date=SESSION,
        discovery_cutoff_at=FORMAL_CUTOFF,
        frozen_at=PREMARKET_FREEZE,
        symbols=("AAA",),
    )
    v3 = FrozenForecast(
        instrument_id=candidate.instrument_id,
        evidence_snapshot_id="v3-evidence",
        feature_vector_fingerprint="v3-features",
        frozen_at=PREMARKET_FREEZE,
        p_close_above_open=Decimal("0.61"),
        p_persistent_uptrend=Decimal("0.55"),
    )
    calibrator = CalibratorArtifact(
        calibrator_id="identity-pre-2026-09-22",
        method="identity",
        training_cutoff_at=datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc),
        training_population_fingerprint="population",
        training_dataset_fingerprint="dataset",
        sample_count=20,
        population_definition="prior confirmed prospective sessions",
        created_at=datetime(2026, 9, 21, 20, 5, tzinfo=timezone.utc),
        code_version="test",
    )
    distribution = GrossReturnDistribution(
        q10=Decimal("0.01"),
        q50=Decimal("0.06"),
        q90=Decimal("0.15"),
        expected_return=Decimal("0.07"),
        expected_shortfall_10pct=Decimal("0"),
        p_return_gt_2pct=Decimal("0.70"),
        p_return_lt_minus_5pct=Decimal("0.05"),
    )
    return PremarketFreezeRequest(
        cohort=cohort,
        frozen_at=PREMARKET_FREEZE,
        frozen_climatology_probability=Decimal("0.43"),
        instruments=(
            PremarketInstrumentInput(
                candidate=candidate,
                v3_forecast=v3,
                catalyst=CatalystDecomposition(
                    strength=Decimal("0.8"),
                    finality=Decimal("0.8"),
                    freshness=Decimal("0.9"),
                    surprise=Decimal("0.6"),
                    economic_materiality=Decimal("0.7"),
                    source_evidence_ids=("news-1",),
                ),
                mechanisms=MechanismRiskScores(
                    continuation_score=Decimal("0.75"),
                    opening_exhaustion_score=Decimal("0.2"),
                    squeeze_tail_score=Decimal("0.4"),
                    fade_risk_score=Decimal("0.2"),
                ),
                calibrator=calibrator,
                evidence_snapshot_id="evidence-v4",
                economic_distribution=distribution,
                uncertainty="moderate",
            ),
        ),
        portfolio_e_policy=PortfolioEPolicy(),
        run_id="run-2026-09-22",
    )


def test_runtime_freezes_machine_readable_authority_at_actual_knowledge_time(monkeypatch) -> None:
    strategy_repo = _MemoryStrategyRepository()
    repo = ProspectiveGapRepository(strategy_repo)
    service = _MarketService()
    runtime = ProspectiveGapRuntime(repository=repo, market_service=service)

    result = runtime.freeze_premarket(_premarket_request())

    assert service.window_end == PREMARKET_FREEZE
    assert service.window_knowledge_mode == "causal_replay"
    assert result.results[0].v4_forecast is not None
    assert result.results[0].market_state.evidence_quality.quality == "DEGRADED"
    ledger = runtime.session_ledger(SESSION)
    assert ledger.latest(kind="session_manifest", instrument_id="__session__") is not None
    assert ledger.latest(kind="premarket_input", instrument_id="equity:US:AAA") is not None
    attempt = ledger.latest(kind="v4_attempt", instrument_id="equity:US:AAA")
    assert attempt is not None
    assert attempt.payload["model_state"] == "PRODUCED"
    assert ledger.latest(kind="v41_shadow_spec", instrument_id="__research_spec__") is not None
    assert ledger.latest(kind="legacy_portfolios", instrument_id="__portfolio__") is not None

    receipt = ConfirmationTransitionReceipt(
        instrument_id="equity:US:AAA",
        transition_at=OPEN + timedelta(minutes=10),
        previous_state="OBSERVE_PULLBACK",
        new_state="CONFIRMED_LONG",
        trigger="test deterministic confirmation",
        bar_ids=("bar-1",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=10),
        reasons=("HIGHER_LOW_CONFIRMED",),
    )

    monkeypatch.setattr(
        runtime_module,
        "evaluate_operational_confirmation",
        lambda **kwargs: OperationalConfirmationEvaluation(
            instrument_id="equity:US:AAA",
            observed_at=kwargs["observed_at"],
            deterministic_state="entry_ready",
            deterministic_reason_code="FAILED_SELL_OFF_CONFIRMED",
            final_confirmation_state="CONFIRMED_LONG",
            actionability="ACT",
            receipts=(receipt,),
            evaluated_bar_count=10,
            signal_entry_price=Decimal("10.01"),
            signal_stop_price=Decimal("9.80"),
            signal_target_price=Decimal("10.50"),
            signal_quality_score=8,
        ),
    )

    confirmation = runtime.run_confirmation(
        session_date=SESSION,
        evaluated_at=OPEN + timedelta(minutes=10),
    )
    assert confirmation.new_authorization_count == 1
    assert len(confirmation.portfolio_e.positions) == 1
    assert confirmation.portfolio_e.cash == Decimal("800")

    postclose = runtime.finalize_postclose(
        session_date=SESSION,
        evaluated_at=datetime(2026, 9, 22, 20, 30, tzinfo=timezone.utc),
    )
    assert postclose.outcomes[0].labels.close_above_open is True
    assert postclose.outcomes[0].labels.persistent_uptrend is True
    assert postclose.scorecard.v3_metrics.n == 1
    assert postclose.scorecard.v4_metrics.n == 1
    assert postclose.scorecard.confirmed_long_count == 1
    assert postclose.scorecard.authorization_long_count == 1
    assert postclose.scorecard.legacy_portfolio_scores is not None
    assert len(postclose.scorecard.legacy_portfolio_scores.scores) == 4
    assert runtime.session_ledger(SESSION).latest(
        kind="legacy_portfolio_scores",
        instrument_id="__portfolio__",
    ) is not None
    assert postclose.scorecard.portfolio_e_performance is not None
    assert postclose.scorecard.portfolio_e_performance.position_outcomes
    projection = runtime.render_markdown(SESSION)
    assert "Portfolio A (" in projection
    assert "Portfolio D (" in projection
    assert "Portfolio E return:" in projection



def test_confirmation_is_measured_even_when_v4_forecast_is_unavailable(monkeypatch) -> None:
    strategy_repo = _MemoryStrategyRepository()
    repo = ProspectiveGapRepository(strategy_repo)
    runtime = ProspectiveGapRuntime(repository=repo, market_service=_MarketService())
    runtime.freeze_premarket(_premarket_request())

    strategy_repo.events = [
        event
        for event in strategy_repo.events
        if event.event_type != "prospective_gap_v4_forecast"
    ]

    receipt = ConfirmationTransitionReceipt(
        instrument_id="equity:US:AAA",
        transition_at=OPEN + timedelta(minutes=9),
        previous_state="OBSERVE_PULLBACK",
        new_state="CONFIRMED_LONG",
        trigger="confirmation independent from model availability",
        bar_ids=("bar-no-v4",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=9),
    )
    monkeypatch.setattr(
        runtime_module,
        "evaluate_operational_confirmation",
        lambda **kwargs: OperationalConfirmationEvaluation(
            instrument_id="equity:US:AAA",
            observed_at=kwargs["observed_at"],
            deterministic_state="entry_ready",
            deterministic_reason_code="FAILED_SELL_OFF_CONFIRMED",
            final_confirmation_state="CONFIRMED_LONG",
            actionability="ACT",
            receipts=(receipt,),
            evaluated_bar_count=9,
        ),
    )

    result = runtime.run_confirmation(
        session_date=SESSION,
        evaluated_at=OPEN + timedelta(minutes=9),
    )

    assert result.new_confirmation_receipt_count == 1
    assert result.new_authorization_count == 0
    assert runtime.session_ledger(SESSION).latest(
        kind="confirmation",
        instrument_id="equity:US:AAA",
    ) is not None
    assert runtime.session_ledger(SESSION).latest(
        kind="authorization",
        instrument_id="equity:US:AAA",
    ) is None



def test_confirmed_long_half_state_recovers_as_no_trade_without_late_quote() -> None:
    strategy_repo = _MemoryStrategyRepository()
    repo = ProspectiveGapRepository(strategy_repo)
    service = _MarketService()
    runtime = ProspectiveGapRuntime(repository=repo, market_service=service)
    request = _premarket_request()
    runtime.freeze_premarket(request)

    confirmation = ConfirmationTransitionReceipt(
        instrument_id="equity:US:AAA",
        transition_at=OPEN + timedelta(minutes=7),
        previous_state="OBSERVE_PULLBACK",
        new_state="CONFIRMED_LONG",
        trigger="persisted before simulated crash",
        bar_ids=("bar-crash",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=7),
        reasons=("HIGHER_LOW_CONFIRMED",),
    )
    assert repo.append(
        session_date=SESSION,
        cohort_id=request.cohort.cohort_id,
        instrument_id="equity:US:AAA",
        kind="confirmation",
        observed_at=confirmation.transition_at,
        payload=confirmation,
        state="CONFIRMED_LONG",
        reason_code=confirmation.trigger,
        run_id=request.run_id,
    )

    def late_quote_must_not_be_used(_instrument_id):
        raise AssertionError("late execution evidence must not repair authorization")

    service.execution_observation = late_quote_must_not_be_used  # type: ignore[method-assign]

    result = runtime.run_confirmation(
        session_date=SESSION,
        evaluated_at=OPEN + timedelta(minutes=20),
    )

    assert result.new_authorization_count == 1
    assert result.portfolio_e.positions == ()
    assert result.portfolio_e.cash == Decimal("1000")
    authorization_record = runtime.session_ledger(SESSION).latest(
        kind="authorization",
        instrument_id="equity:US:AAA",
    )
    assert authorization_record is not None
    authorization = authorization_record.payload
    assert authorization["decision"] == "NO_TRADE"
    assert datetime.fromisoformat(
        str(authorization["decision_at"]).replace("Z", "+00:00")
    ) == confirmation.transition_at
    assert authorization["reasons"] == ["AUTHORIZATION_WINDOW_MISSED_AFTER_CONFIRMATION"]



def test_formal_outcome_accepts_standard_us_equity_early_close() -> None:
    session_date = date(2026, 11, 27)  # Friday after Thanksgiving, 13:00 ET close.
    start = datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc)
    bars: list[MarketBar] = []
    price = Decimal("10")
    for index in range(42):
        bar_start = start + timedelta(minutes=index * 5)
        next_price = price + Decimal("0.02")
        bars.append(
            _bar(
                start=bar_start,
                interval="5m",
                open_=str(price),
                high=str(next_price + Decimal("0.01")),
                low=str(price - Decimal("0.01")),
                close=str(next_price),
                volume="10000",
                session="regular",
                provider="alpaca_sip",
            )
        )
        price = next_price

    outcome = build_operational_formal_outcome(
        session_date=session_date,
        bars=bars,
    )

    assert outcome.session_boundary_complete is True
    assert outcome.measurements.observed_session_coverage == Decimal("1")
    assert outcome.measurements.halt_or_gap_minutes == Decimal("0")
    assert outcome.canonical_bar_count == 42


def test_an_imported_handoff_with_malformed_content_is_refused_before_storage() -> None:
    from app.apps.trading.prospective_gap_inputs import ProspectiveGapInputs

    def no_database():
        raise AssertionError("a malformed handoff reached the database")

    with pytest.raises(Exception):
        ProspectiveGapInputs(uow_factory=no_database).import_handoff(
            '{"session_date":"2026-09-22","not_a_freeze_request":true}',
            source="test",
        )


class _SchedulerMarketService(_MarketService):
    def __init__(self) -> None:
        super().__init__()
        start = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)
        self.scheduler_premarket = tuple(
            MarketBar(
                instrument_id="equity:US:AAA",
                interval="1m",
                start_time=start + timedelta(minutes=index),
                end_time=start + timedelta(minutes=index + 1),
                open=Decimal("14") + Decimal(index) / Decimal("100"),
                high=Decimal("14.1") + Decimal(index) / Decimal("100"),
                low=Decimal("13.9") + Decimal(index) / Decimal("100"),
                close=Decimal("14.05") + Decimal(index) / Decimal("100"),
                volume=Decimal("10000"),
                provider="yahoo",
                provider_event_id=f"sched-pm-{index}",
                provider_sequence=index,
                received_at=start + timedelta(minutes=index + 1),
                session="extended_pre",
                adjustment_mode=AdjustmentMode.RAW,
            )
            for index in range(20)
        )
        daily_start = datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)
        self.scheduler_daily = tuple(
            MarketBar(
                instrument_id="equity:US:AAA",
                interval="1d",
                start_time=daily_start + timedelta(days=index),
                end_time=daily_start + timedelta(days=index + 1),
                open=Decimal("9") + Decimal(index),
                high=Decimal("10") + Decimal(index),
                low=Decimal("8") + Decimal(index),
                close=Decimal("9.5") + Decimal(index),
                volume=Decimal("100000"),
                provider="yahoo",
                provider_event_id=f"sched-d-{index}",
                provider_sequence=index,
                received_at=daily_start + timedelta(days=index + 1),
                session="regular",
                adjustment_mode=AdjustmentMode.RAW,
            )
            for index in range(4)
        )

    def recovered_window_bars(self, instrument_id, **kwargs):
        bars = tuple(
            bar
            for bar in self.scheduler_premarket
            if bar.end_time <= kwargs["end"]
        )
        return SimpleNamespace(
            bars=bars,
            report=SimpleNamespace(
                coverage_ratio=Decimal("1"),
                unresolved_gaps=(),
                provider_error=None,
                dataset_fingerprint="scheduler-premarket-dataset",
                latest_bar_lag_seconds=0,
                late_window_bar_count=min(15, len(bars)),
            ),
        )

    def bars(self, instrument_id, interval, limit, binding_id):
        if interval == "1d":
            return SimpleNamespace(bars=list(self.scheduler_daily))
        return super().bars(instrument_id, interval, limit, binding_id)


def test_lightweight_scheduler_handoff_uses_runtime_market_data_and_newer_climatology() -> None:
    session = date(2026, 9, 23)
    discovered_at = datetime(2026, 9, 23, 13, 17, tzinfo=timezone.utc)
    research_frozen_at = datetime(2026, 9, 23, 13, 20, tzinfo=timezone.utc)
    cutoff = datetime(2026, 9, 23, 13, 29, tzinfo=timezone.utc)
    observed_at = datetime(2026, 9, 23, 13, 25, tzinfo=timezone.utc)
    completed_at = datetime(2026, 9, 23, 13, 26, tzinfo=timezone.utc)
    repo = ProspectiveGapRepository(_MemoryStrategyRepository())
    runtime = ProspectiveGapRuntime(
        repository=repo,
        market_service=_SchedulerMarketService(),
        now_factory=lambda: completed_at,
    )
    handoff = SchedulerPremarketHandoff(
        session_date=session,
        cohort_id="finviz-2026-09-23",
        discovered_at=discovered_at,
        research_frozen_at=research_frozen_at,
        prediction_cutoff_at=cutoff,
        baseline_through_session=date(2026, 9, 21),
        baseline_observation_count=30,
        baseline_positive_count=13,
        instruments=(
            SchedulerPremarketInstrumentInput(
                symbol="AAA",
                discovery_rank=1,
                v3_p_close_above_open=Decimal("0.60"),
                v3_p_persistent_uptrend=Decimal("0.55"),
                v4_raw_p_close_above_open=Decimal("0.62"),
                v4_calibrated_p_close_above_open=Decimal("0.62"),
                v4_extension_risk_score=Decimal("0.40"),
                v4_evidence_quality="DEGRADED",
                catalyst=CatalystDecomposition(
                    strength=Decimal("0.8"),
                    finality=Decimal("0.8"),
                    freshness=Decimal("0.8"),
                    surprise=Decimal("0.7"),
                    economic_materiality=Decimal("0.8"),
                ),
                mechanisms=MechanismRiskScores(
                    continuation_score=Decimal("0.7"),
                    opening_exhaustion_score=Decimal("0.2"),
                    squeeze_tail_score=Decimal("0.2"),
                    fade_risk_score=Decimal("0.2"),
                ),
                float_shares=Decimal("1000000"),
            ),
        ),
    )
    state = ProspectiveClimatologyState(
        through_session=date(2026, 9, 22),
        observation_count=40,
        positive_count=17,
        probability=Decimal("0.425"),
    )

    result = runtime.freeze_scheduler_handoff(
        handoff,
        observed_at=observed_at,
        climatology_state=state,
    )

    assert result.session_date == session
    ledger = runtime.session_ledger(session)
    manifest = runtime_module.ProspectiveSessionManifest.model_validate(
        ledger.latest(kind="session_manifest", instrument_id="__session__").payload
    )
    assert manifest.frozen_climatology_probability == Decimal("0.425")
    assert manifest.candidates[0].previous_close > 0
    assert manifest.candidates[0].premarket_volume > 0
    assert manifest.candidates[0].premarket_bar_count == 20
    assert manifest.candidates[0].float_shares == Decimal("1000000")
    v4_record = runtime_module.V4ForecastRecord.model_validate(
        ledger.latest(
            kind="v4_forecast",
            instrument_id="equity:US:AAA",
        ).payload
    )
    assert v4_record.forecast.frozen_at == research_frozen_at
    assert v4_record.forecast.raw_p_close_above_open == Decimal("0.62")
    assert v4_record.forecast.calibrated_p_close_above_open == Decimal("0.62")
    assert v4_record.extension_risk.score == Decimal("0.40")
    assert manifest.frozen_at == completed_at


def test_scheduler_handoff_fails_closed_after_prediction_cutoff() -> None:
    session = date(2026, 9, 23)
    handoff = SchedulerPremarketHandoff(
        session_date=session,
        cohort_id="finviz-2026-09-23",
        discovered_at=datetime(2026, 9, 23, 13, 17, tzinfo=timezone.utc),
        research_frozen_at=datetime(2026, 9, 23, 13, 20, tzinfo=timezone.utc),
        prediction_cutoff_at=datetime(2026, 9, 23, 13, 29, tzinfo=timezone.utc),
        baseline_through_session=date(2026, 9, 22),
        baseline_observation_count=40,
        baseline_positive_count=17,
        instruments=(
            SchedulerPremarketInstrumentInput(
                symbol="AAA",
                discovery_rank=1,
                v3_p_close_above_open=Decimal("0.60"),
                v3_p_persistent_uptrend=Decimal("0.55"),
                v4_raw_p_close_above_open=Decimal("0.62"),
                v4_calibrated_p_close_above_open=Decimal("0.62"),
                v4_extension_risk_score=Decimal("0.40"),
                v4_evidence_quality="DEGRADED",
                catalyst=CatalystDecomposition(
                    strength=Decimal("0.8"),
                    finality=Decimal("0.8"),
                    freshness=Decimal("0.8"),
                    surprise=Decimal("0.7"),
                    economic_materiality=Decimal("0.8"),
                ),
                mechanisms=MechanismRiskScores(
                    continuation_score=Decimal("0.7"),
                    opening_exhaustion_score=Decimal("0.2"),
                    squeeze_tail_score=Decimal("0.2"),
                    fade_risk_score=Decimal("0.2"),
                ),
            ),
        ),
    )
    runtime = ProspectiveGapRuntime(
        repository=ProspectiveGapRepository(_MemoryStrategyRepository()),
        market_service=_SchedulerMarketService(),
        now_factory=lambda: datetime(2026, 9, 23, 13, 30, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="scheduler_handoff_ingested_after_prediction_cutoff"):
        runtime.freeze_scheduler_handoff(
            handoff,
            observed_at=datetime(2026, 9, 23, 13, 30, tzinfo=timezone.utc),
        )



def _scheduler_handoff_fixture() -> SchedulerPremarketHandoff:
    return SchedulerPremarketHandoff(
        session_date=date(2026, 9, 23),
        cohort_id="finviz-2026-09-23",
        discovered_at=datetime(2026, 9, 23, 13, 17, tzinfo=timezone.utc),
        research_frozen_at=datetime(2026, 9, 23, 13, 20, tzinfo=timezone.utc),
        prediction_cutoff_at=datetime(2026, 9, 23, 13, 29, tzinfo=timezone.utc),
        baseline_through_session=date(2026, 9, 22),
        baseline_observation_count=40,
        baseline_positive_count=17,
        instruments=(
            SchedulerPremarketInstrumentInput(
                symbol="AAA",
                discovery_rank=1,
                v3_p_close_above_open=Decimal("0.60"),
                v3_p_persistent_uptrend=Decimal("0.55"),
                v4_raw_p_close_above_open=Decimal("0.62"),
                v4_calibrated_p_close_above_open=Decimal("0.62"),
                v4_extension_risk_score=Decimal("0.40"),
                v4_evidence_quality="DEGRADED",
                catalyst=CatalystDecomposition(
                    strength=Decimal("0.8"),
                    finality=Decimal("0.8"),
                    freshness=Decimal("0.8"),
                    surprise=Decimal("0.7"),
                    economic_materiality=Decimal("0.8"),
                ),
                mechanisms=MechanismRiskScores(
                    continuation_score=Decimal("0.7"),
                    opening_exhaustion_score=Decimal("0.2"),
                    squeeze_tail_score=Decimal("0.2"),
                    fade_risk_score=Decimal("0.2"),
                ),
                float_shares=Decimal("1000000"),
            ),
        ),
    )


def test_scheduler_handoff_fails_closed_when_recovery_completes_after_cutoff() -> None:
    handoff = _scheduler_handoff_fixture()
    runtime = ProspectiveGapRuntime(
        repository=ProspectiveGapRepository(_MemoryStrategyRepository()),
        market_service=_SchedulerMarketService(),
        now_factory=lambda: datetime(2026, 9, 23, 13, 29, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(
        ValueError,
        match="scheduler_handoff_completed_after_prediction_cutoff",
    ):
        runtime.freeze_scheduler_handoff(
            handoff,
            observed_at=datetime(2026, 9, 23, 13, 25, tzinfo=timezone.utc),
        )

    assert runtime.session_ledger(handoff.session_date).latest(
        kind="session_manifest",
        instrument_id="__session__",
    ) is None


def test_the_imported_handoff_is_frozen_exactly_once() -> None:
    handoff = _scheduler_handoff_fixture()
    requested: list[object] = []
    runtime = ProspectiveGapRuntime(
        repository=ProspectiveGapRepository(_MemoryStrategyRepository()),
        market_service=_SchedulerMarketService(),
        now_factory=lambda: datetime(2026, 9, 23, 13, 26, tzinfo=timezone.utc),
        scheduler_handoff_fetcher=lambda session_date: (
            handoff if session_date == handoff.session_date else None
        ),
        climatology_loader=lambda session_date: requested.append(session_date),
    )

    result = runtime.try_freeze_scheduler_inbox(
        handoff.session_date,
        observed_at=datetime(2026, 9, 23, 13, 25, tzinfo=timezone.utc),
    )
    again = runtime.try_freeze_scheduler_inbox(handoff.session_date)

    assert result is not None
    assert result.session_date == handoff.session_date
    assert again is None
    # The climatology state is the latest one before the handoff's session.
    assert requested == [handoff.session_date]
    manifest = runtime.session_ledger(handoff.session_date).latest(
        kind="session_manifest",
        instrument_id="__session__",
    )
    assert manifest is not None


class _V43SchedulerMarketService(_SchedulerMarketService):
    def __init__(self) -> None:
        super().__init__()
        start = datetime(2026, 9, 28, 13, 5, tzinfo=timezone.utc)
        self.scheduler_premarket = tuple(
            MarketBar(
                instrument_id="equity:US:AAA",
                interval="1m",
                start_time=start + timedelta(minutes=index),
                end_time=start + timedelta(minutes=index + 1),
                open=Decimal("14") + Decimal(index) / Decimal("100"),
                high=Decimal("14.12") + Decimal(index) / Decimal("100"),
                low=Decimal("13.95") + Decimal(index) / Decimal("100"),
                close=Decimal("14.06") + Decimal(index) / Decimal("100"),
                volume=Decimal("25000") + Decimal(index * 1000),
                provider="yahoo",
                provider_event_id=f"v43-pm-{index}",
                provider_sequence=index,
                received_at=start + timedelta(minutes=index + 1),
                session="extended_pre",
                adjustment_mode=AdjustmentMode.RAW,
            )
            for index in range(20)
        )
        daily_start = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)
        self.scheduler_daily = tuple(
            MarketBar(
                instrument_id="equity:US:AAA",
                interval="1d",
                start_time=daily_start + timedelta(days=index),
                end_time=daily_start + timedelta(days=index + 1),
                open=Decimal("9.5") + Decimal(index) / Decimal("10"),
                high=Decimal("10.2") + Decimal(index) / Decimal("10"),
                low=Decimal("9.2") + Decimal(index) / Decimal("10"),
                close=Decimal("10") + Decimal(index) / Decimal("10"),
                volume=Decimal("500000"),
                provider="yahoo",
                provider_event_id=f"v43-d-{index}",
                provider_sequence=index,
                received_at=daily_start + timedelta(days=index + 1),
                session="regular",
                adjustment_mode=AdjustmentMode.RAW,
            )
            for index in range(4)
        )

    def recovered_window_bars(self, instrument_id, **kwargs):
        bars = tuple(
            bar.model_copy(
                update={
                    "instrument_id": instrument_id,
                    "provider_event_id": (
                        f"{instrument_id}:{bar.provider_event_id}"
                    ),
                }
            )
            for bar in self.scheduler_premarket
            if bar.end_time <= kwargs["end"]
        )
        return SimpleNamespace(
            bars=bars,
            report=SimpleNamespace(
                coverage_ratio=Decimal("1"),
                unresolved_gaps=(),
                provider_error=None,
                dataset_fingerprint=f"v43:{instrument_id}",
                latest_bar_lag_seconds=60,
                late_window_bar_count=min(15, len(bars)),
            ),
        )

    def bars(self, instrument_id, interval, limit, binding_id):
        if interval == "1d":
            return SimpleNamespace(
                bars=[
                    bar.model_copy(
                        update={
                            "instrument_id": instrument_id,
                            "provider_event_id": (
                                f"{instrument_id}:{bar.provider_event_id}"
                            ),
                        }
                    )
                    for bar in self.scheduler_daily
                ]
            )
        return super().bars(instrument_id, interval, limit, binding_id)


def test_scheduler_handoff_freezes_v43_for_three_name_forward_cohort() -> None:
    session = date(2026, 9, 28)
    discovered_at = datetime(2026, 9, 28, 13, 17, tzinfo=timezone.utc)
    research_frozen_at = datetime(2026, 9, 28, 13, 20, tzinfo=timezone.utc)
    cutoff = datetime(2026, 9, 28, 13, 29, tzinfo=timezone.utc)
    observed_at = datetime(2026, 9, 28, 13, 25, tzinfo=timezone.utc)
    completed_at = datetime(2026, 9, 28, 13, 26, tzinfo=timezone.utc)
    runtime = ProspectiveGapRuntime(
        repository=ProspectiveGapRepository(_MemoryStrategyRepository()),
        market_service=_V43SchedulerMarketService(),
        now_factory=lambda: completed_at,
    )

    instruments = tuple(
        SchedulerPremarketInstrumentInput(
            symbol=symbol,
            discovery_rank=index,
            v3_p_close_above_open=Decimal("0.60"),
            v3_p_persistent_uptrend=Decimal("0.55"),
            v4_raw_p_close_above_open=Decimal("0.62"),
            v4_calibrated_p_close_above_open=Decimal("0.62"),
            v4_extension_risk_score=Decimal("0.35"),
            v4_evidence_quality="DEGRADED",
            catalyst=CatalystDecomposition(
                strength=Decimal("0.8"),
                finality=Decimal("0.8"),
                freshness=Decimal("0.8"),
                surprise=Decimal("0.7"),
                economic_materiality=Decimal("0.8"),
            ),
            mechanisms=MechanismRiskScores(
                continuation_score=Decimal("0.75"),
                opening_exhaustion_score=Decimal("0.20"),
                squeeze_tail_score=Decimal("0.20"),
                fade_risk_score=Decimal("0.15"),
            ),
            float_shares=Decimal("1000000"),
        )
        for index, symbol in enumerate(("AAA", "BBB", "CCC"), start=1)
    )
    handoff = SchedulerPremarketHandoff(
        session_date=session,
        cohort_id="finviz-2026-09-28",
        discovered_at=discovered_at,
        research_frozen_at=research_frozen_at,
        prediction_cutoff_at=cutoff,
        baseline_through_session=date(2026, 9, 23),
        baseline_observation_count=50,
        baseline_positive_count=19,
        instruments=instruments,
    )
    state = ProspectiveClimatologyState(
        through_session=date(2026, 9, 23),
        observation_count=50,
        positive_count=19,
        probability=Decimal("0.38"),
    )

    result = runtime.freeze_scheduler_handoff(
        handoff,
        observed_at=observed_at,
        climatology_state=state,
    )

    assert len(result.results) == 3
    assert all(row.v42_forecast is not None for row in result.results)
    assert all(row.v43_forecast is not None for row in result.results)
    ledger = runtime.session_ledger(session)
    regime = ledger.latest(
        kind="v43_cohort_regime",
        instrument_id="__cohort_v43__",
    )
    assert regime is not None
    assert regime.payload["classification"] in {
        "NORMAL",
        "CAUTIOUS",
        "HIGH_EXHAUSTION",
    }
    attempts = ledger.records_of_kind("v43_attempt")
    assert len(attempts) == 3
    assert all(row.payload["model_state"] == "PRODUCED" for row in attempts)
    assert len(ledger.records_of_kind("v43_watch")) == 3
