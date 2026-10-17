from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.gapper_dataset import GapperCandidate, GapperUniverseSnapshot
from app.apps.trading.models import MarketBar
from app.apps.trading.prospective_prediction_evidence import (
    EvidenceTimestamps,
    FrozenForecast,
    PremarketEvidenceItem,
    PremarketEvidenceSnapshot,
)
from app.apps.trading.prospective_prediction_scoring import bind_formal_v3_v4_pair
from app.apps.trading.prospective_prediction_v4 import (
    CalibratorArtifact,
    CashPreservingAuthorizedPortfolio,
    CatalystDecomposition,
    ExecutionCostInput,
    ExtensionComponents,
    FinvizFrozenCohort,
    GrossReturnDistribution,
    MechanismRiskScores,
    PairedForecastObservation,
    PremarketFeature,
    PremarketMarketStateSnapshot,
    SelectiveForecastObservation,
    V4ForecastAttempt,
    actionability_from_confirmation,
    apply_calibrator,
    apply_execution_costs,
    authorize_trade,
    bind_v3_v4_pair,
    build_premarket_market_state,
    derive_extension_exhaustion_risk,
    evaluate_paired_v3_v4,
    evaluate_post_open_confirmation,
    evaluate_selective_forecasts,
    finviz_cohort_from_universe,
    freeze_cash_preserving_authorized_portfolio,
    freeze_v4_forecast,
    market_state_from_candidate,
    summarize_evidence_quality,
    transition_confirmation,
)


SESSION = date(2026, 9, 18)
CUTOFF = datetime(2026, 9, 18, 13, 29, tzinfo=timezone.utc)
OPEN = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)


def _cohort() -> FinvizFrozenCohort:
    return FinvizFrozenCohort(
        cohort_id="finviz-2026-09-18",
        session_date=SESSION,
        discovery_cutoff_at=CUTOFF,
        frozen_at=CUTOFF - timedelta(minutes=8),
        symbols=("AAA", "BBB"),
    )


def _feature(
    name: str,
    value: str | None,
    *,
    observed_at: datetime | None = None,
    ingested_at: datetime | None = None,
    recovered_at: datetime | None = None,
    quality: str = "GOOD",
    available: bool | None = None,
    live: bool = True,
) -> PremarketFeature:
    available = value is not None if available is None else available
    return PremarketFeature(
        name=name,
        value=None if value is None else Decimal(value),
        event_at=(observed_at or CUTOFF - timedelta(minutes=2)),
        observed_at=observed_at,
        ingested_at=ingested_at,
        recovered_at=recovered_at,
        source="sip",
        freshness_seconds=15 if value is not None else None,
        quality=quality,
        available=available,
        available_to_live_forecaster=live,
    )


def _state(*features: PremarketFeature) -> PremarketMarketStateSnapshot:
    cohort = _cohort()
    return PremarketMarketStateSnapshot(
        snapshot_id="state-aaa",
        cohort_id=cohort.cohort_id,
        cohort_fingerprint=cohort.cohort_fingerprint,
        instrument_id="AAA",
        prediction_cutoff_at=CUTOFF,
        frozen_at=CUTOFF - timedelta(seconds=5),
        features=features,
    )


def _complete_state() -> PremarketMarketStateSnapshot:
    observed = CUTOFF - timedelta(minutes=1)
    ingested = CUTOFF - timedelta(seconds=30)
    return _state(
        _feature("gap_pct", "45", observed_at=observed, ingested_at=ingested),
        _feature("premarket_vwap_distance_pct", "4", observed_at=observed, ingested_at=ingested),
    )


def _calibrator(method: str = "identity") -> CalibratorArtifact:
    kwargs = {}
    if method == "prior_blend_v1":
        kwargs = {"prior_probability": Decimal("0.35"), "prior_weight": Decimal("0.25")}
    return CalibratorArtifact(
        calibrator_id=f"cal-{method}",
        method=method,
        training_cutoff_at=datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc),
        training_population_fingerprint="population",
        training_dataset_fingerprint="dataset",
        sample_count=100,
        population_definition="Finviz Top Gainers frozen before open",
        created_at=datetime(2026, 9, 17, 21, 0, tzinfo=timezone.utc),
        code_version="abc123",
        **kwargs,
    )


def _catalyst() -> CatalystDecomposition:
    return CatalystDecomposition(
        strength=Decimal("0.8"),
        finality=Decimal("0.8"),
        freshness=Decimal("0.9"),
        surprise=Decimal("0.7"),
        economic_materiality=Decimal("0.8"),
        source_evidence_ids=("news-1",),
    )


def _mechanisms() -> MechanismRiskScores:
    return MechanismRiskScores(
        continuation_score=Decimal("0.65"),
        opening_exhaustion_score=Decimal("0.6"),
        squeeze_tail_score=Decimal("0.3"),
        fade_risk_score=Decimal("0.45"),
    )


def _extension():
    return derive_extension_exhaustion_risk(
        ExtensionComponents(
            gap_from_prior_close_pct=Decimal("45"),
            premarket_move_since_first_catalyst_pct=Decimal("38"),
            distance_from_premarket_vwap_pct=Decimal("4"),
            distance_from_premarket_low_pct=Decimal("28"),
            position_in_premarket_range=Decimal("0.75"),
            prior_1d_return_pct=Decimal("3"),
            prior_3d_return_pct=Decimal("8"),
            float_turnover=Decimal("0.4"),
            late_premarket_acceleration=Decimal("0.1"),
            late_premarket_volume_share=Decimal("0.3"),
        )
    )


def _v4_forecast():
    state = _complete_state()
    quality = summarize_evidence_quality(
        state,
        critical_features=("gap_pct", "premarket_vwap_distance_pct"),
    )
    return freeze_v4_forecast(
        instrument_id="AAA",
        session_date=SESSION,
        cohort=_cohort(),
        evidence_snapshot_id="evidence-1",
        market_state=state,
        evidence_quality=quality,
        catalyst=_catalyst(),
        extension_risk=_extension(),
        mechanisms=_mechanisms(),
        calibrator=_calibrator(),
        feature_vector_fingerprint=state.live_feature_fingerprint,
        frozen_at=CUTOFF - timedelta(seconds=1),
        regime_tags=("FUNDAMENTAL_REPRICE",),
        regime_primary="FUNDAMENTAL_REPRICE",
        regime_confidence=Decimal("0.8"),
        return_q10=Decimal("-0.05"),
        return_q50=Decimal("0.03"),
        return_q90=Decimal("0.12"),
        p_return_gt_2pct=Decimal("0.55"),
        p_return_lt_minus_5pct=Decimal("0.15"),
    )


def test_recovered_after_cutoff_can_exist_but_cannot_be_live_forecast_evidence() -> None:
    recovered = _feature(
        "premarket_vwap_distance_pct",
        "3.5",
        observed_at=CUTOFF + timedelta(minutes=8),
        ingested_at=CUTOFF + timedelta(minutes=8),
        recovered_at=CUTOFF + timedelta(minutes=9),
        quality="RECOVERED",
        live=False,
    )
    state = _state(recovered)
    assert state.live_value("premarket_vwap_distance_pct") is None

    with pytest.raises(ValueError, match="live_feature_observed_after_cutoff"):
        _state(
            _feature(
                "premarket_vwap_distance_pct",
                "3.5",
                observed_at=CUTOFF + timedelta(minutes=8),
                ingested_at=CUTOFF + timedelta(minutes=8),
                recovered_at=CUTOFF + timedelta(minutes=9),
                quality="RECOVERED",
                live=True,
            )
        )


def test_evidence_quality_distinguishes_degraded_and_insufficient() -> None:
    observed = CUTOFF - timedelta(minutes=1)
    ingested = CUTOFF - timedelta(seconds=30)
    degraded_state = _state(
        _feature("gap_pct", "50", observed_at=observed, ingested_at=ingested),
        _feature(
            "premarket_vwap_distance_pct",
            "8",
            observed_at=observed,
            ingested_at=ingested,
            quality="STALE",
        ),
    )
    degraded = summarize_evidence_quality(
        degraded_state,
        critical_features=("gap_pct", "premarket_vwap_distance_pct"),
    )
    assert degraded.quality == "DEGRADED"

    insufficient = summarize_evidence_quality(
        _state(_feature("gap_pct", "50", observed_at=observed, ingested_at=ingested)),
        critical_features=("gap_pct", "premarket_vwap_distance_pct"),
    )
    assert insufficient.quality == "INSUFFICIENT"
    assert insufficient.missing_critical_features == ("premarket_vwap_distance_pct",)


def test_extension_risk_retains_raw_component_missingness() -> None:
    risk = derive_extension_exhaustion_risk(
        ExtensionComponents(
            gap_from_prior_close_pct=Decimal("150"),
            position_in_premarket_range=Decimal("0.95"),
        )
    )
    assert risk.score > Decimal("0.5")
    assert "gap_from_prior_close_pct" in risk.used_components
    assert "distance_from_premarket_vwap_pct" in risk.missing_components


def test_calibrator_cannot_train_on_or_after_forecast_session() -> None:
    same_session = CalibratorArtifact(
        calibrator_id="bad",
        method="identity",
        training_cutoff_at=OPEN,
        training_population_fingerprint="population",
        training_dataset_fingerprint="dataset",
        sample_count=10,
        population_definition="same population",
        created_at=OPEN,
        code_version="abc",
    )
    with pytest.raises(ValueError, match="calibrator_training_cutoff_must_precede_forecast_session"):
        apply_calibrator(Decimal("0.6"), same_session, forecast_session_date=SESSION)


def test_prior_blend_calibration_is_separate_from_raw_probability() -> None:
    calibrated = apply_calibrator(
        Decimal("0.80"),
        _calibrator("prior_blend_v1"),
        forecast_session_date=SESSION,
    )
    assert calibrated == Decimal("0.6875")


def test_v4_forecast_is_schema_isolated_and_rejects_insufficient_evidence() -> None:
    forecast = _v4_forecast()
    assert forecast.predictor_version == "prospective-gap-v4-shadow"
    assert forecast.raw_p_close_above_open != Decimal("0")
    assert forecast.calibrated_p_close_above_open == forecast.raw_p_close_above_open
    assert forecast.regime_primary == "FUNDAMENTAL_REPRICE"

    state = _complete_state()
    insufficient = summarize_evidence_quality(state, critical_features=("missing_feature",))
    with pytest.raises(ValueError, match="insufficient_evidence_cannot_produce_v4_forecast"):
        freeze_v4_forecast(
            instrument_id="AAA",
            session_date=SESSION,
            cohort=_cohort(),
            evidence_snapshot_id="evidence-1",
            market_state=state,
            evidence_quality=insufficient,
            catalyst=_catalyst(),
            extension_risk=_extension(),
            mechanisms=_mechanisms(),
            calibrator=_calibrator(),
            feature_vector_fingerprint=state.live_feature_fingerprint,
            frozen_at=CUTOFF - timedelta(seconds=1),
        )


def test_confirmation_requires_finalized_bar_and_supports_data_suspension() -> None:
    initial = transition_confirmation(
        instrument_id="AAA",
        previous_state="WAIT_OPEN",
        new_state="OBSERVE_INITIAL_STRUCTURE",
        transition_at=OPEN + timedelta(minutes=5),
        trigger="first finalized 5m bar",
        bar_ids=("bar-0930",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=5),
    )
    suspended = transition_confirmation(
        instrument_id="AAA",
        previous_state=initial.new_state,
        new_state="SUSPENDED_DATA_QUALITY",
        transition_at=OPEN + timedelta(minutes=7),
        trigger="market data coverage degraded",
        reasons=("DATA_GAP_ACTIVE",),
    )
    assert actionability_from_confirmation(suspended).actionability == "WATCH"

    with pytest.raises(ValueError, match="confirmed_long_requires_finalized_bar_evidence"):
        transition_confirmation(
            instrument_id="AAA",
            previous_state="OBSERVE_PULLBACK",
            new_state="CONFIRMED_LONG",
            transition_at=OPEN + timedelta(minutes=10),
            trigger="higher low and VWAP reclaim",
        )

    resumed = transition_confirmation(
        instrument_id="AAA",
        previous_state="SUSPENDED_DATA_QUALITY",
        new_state="OBSERVE_PULLBACK",
        transition_at=OPEN + timedelta(minutes=20),
        trigger="current tape recovered and qualified",
        bar_ids=("bar-0945",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=20),
    )
    confirmed = transition_confirmation(
        instrument_id="AAA",
        previous_state=resumed.new_state,
        new_state="CONFIRMED_LONG",
        transition_at=OPEN + timedelta(minutes=25),
        trigger="finalized higher low plus VWAP reclaim",
        bar_ids=("bar-0950",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=25),
    )
    assert actionability_from_confirmation(confirmed).actionability == "ACT"


def test_execution_cost_transform_can_veto_positive_gross_alpha() -> None:
    gross = GrossReturnDistribution(
        q10=Decimal("-0.03"),
        q50=Decimal("0.01"),
        q90=Decimal("0.08"),
    )
    cost = ExecutionCostInput(
        symbol="AAA",
        decision_at=OPEN + timedelta(minutes=10),
        notional=Decimal("1000"),
        reference_price=Decimal("10"),
        observed_bid=Decimal("9.90"),
        observed_ask=Decimal("10.10"),
        estimated_slippage_bps=Decimal("40"),
        estimated_impact_bps=Decimal("20"),
    )
    net = apply_execution_costs(gross, cost)
    assert net.q50 < 0


def test_authorization_is_distinct_from_forecast_and_requires_positive_net_alpha() -> None:
    forecast = _v4_forecast()
    confirmation = transition_confirmation(
        instrument_id="AAA",
        previous_state="OBSERVE_PULLBACK",
        new_state="CONFIRMED_LONG",
        transition_at=OPEN + timedelta(minutes=10),
        trigger="finalized setup",
        bar_ids=("bar-0935",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=10),
    )
    actionability = actionability_from_confirmation(confirmation)
    gross = GrossReturnDistribution(
        q10=Decimal("-0.03"),
        q50=Decimal("0.01"),
        q90=Decimal("0.08"),
        expected_return=Decimal("0.01"),
    )
    expensive = ExecutionCostInput(
        symbol="AAA",
        decision_at=confirmation.transition_at,
        notional=Decimal("1000"),
        reference_price=Decimal("10"),
        observed_bid=Decimal("9.90"),
        observed_ask=Decimal("10.10"),
        estimated_slippage_bps=Decimal("40"),
        estimated_impact_bps=Decimal("20"),
    )
    receipt = authorize_trade(
        forecast=forecast,
        confirmation=confirmation,
        actionability=actionability,
        gross=gross,
        cost=expensive,
        evidence_fingerprint="exec-evidence",
    )
    assert receipt.decision == "NO_TRADE"
    assert receipt.notional == 0
    assert receipt.net_expected_return is not None and receipt.net_expected_return < 0


def test_paired_v3_v4_metrics_use_same_observation() -> None:
    observations = [
        PairedForecastObservation(
            session_date=SESSION,
            cohort_fingerprint="cohort",
            instrument_id="AAA",
            v3_probability=Decimal("0.70"),
            v4_probability=Decimal("0.55"),
            outcome=False,
        ),
        PairedForecastObservation(
            session_date=SESSION,
            cohort_fingerprint="cohort",
            instrument_id="BBB",
            v3_probability=Decimal("0.60"),
            v4_probability=Decimal("0.75"),
            outcome=True,
        ),
    ]
    metrics = evaluate_paired_v3_v4(observations)
    assert metrics.n == 2
    assert metrics.mean_delta_brier_v4_minus_v3 is not None
    assert metrics.mean_delta_brier_v4_minus_v3 < 0


def test_bind_v3_v4_pair_preserves_v4_cohort_identity() -> None:
    v3 = FrozenForecast(
        instrument_id="AAA",
        evidence_snapshot_id="evidence-1",
        feature_vector_fingerprint="v3-features",
        frozen_at=CUTOFF - timedelta(seconds=2),
        p_close_above_open=Decimal("0.55"),
        p_persistent_uptrend=Decimal("0.45"),
    )
    v4 = _v4_forecast()
    pair = bind_v3_v4_pair(v3=v3, v4=v4, outcome=True)
    assert pair.cohort_fingerprint == v4.cohort_fingerprint
    assert pair.v4_probability == v4.calibrated_p_close_above_open


def test_selective_metrics_report_coverage_separately_from_accuracy() -> None:
    metrics = evaluate_selective_forecasts(
        [
            SelectiveForecastObservation(
                instrument_id="A",
                actionability="ACT",
                probability=Decimal("0.70"),
                outcome=True,
            ),
            SelectiveForecastObservation(
                instrument_id="B",
                actionability="ABSTAIN",
                probability=Decimal("0.55"),
                outcome=False,
            ),
            SelectiveForecastObservation(
                instrument_id="C",
                actionability="WATCH",
                probability=Decimal("0.40"),
                outcome=False,
            ),
        ]
    )
    assert metrics.coverage == Decimal("1") / Decimal("3")
    assert metrics.selective_accuracy == Decimal("1")
    assert metrics.bullish_precision == Decimal("1")


def test_market_state_builder_uses_only_pre_cutoff_received_bars_for_live_features() -> None:
    cohort = _cohort()

    def bar(minute: int, close: str, volume: str, *, received_delay_minutes: int = 0) -> MarketBar:
        start = datetime(2026, 9, 18, 13, minute, tzinfo=timezone.utc)
        end = start + timedelta(minutes=1)
        price = Decimal(close)
        return MarketBar(
            instrument_id="AAA",
            interval="1m",
            start_time=start,
            end_time=end,
            open=price,
            high=price + Decimal("0.05"),
            low=price - Decimal("0.05"),
            close=price,
            volume=Decimal(volume),
            provider="alpaca_sip",
            provider_event_id=f"bar-{minute}",
            provider_sequence=minute,
            received_at=end + timedelta(minutes=received_delay_minutes),
        )

    bars = [
        bar(10, "10.00", "1000"),
        bar(11, "10.20", "1200"),
        bar(12, "10.50", "1500"),
        # Event happened before cutoff but did not arrive until after the cutoff.
        bar(28, "12.00", "5000", received_delay_minutes=3),
    ]
    state = build_premarket_market_state(
        cohort=cohort,
        instrument_id="AAA",
        bars=bars,
        snapshot_id="derived-state",
        prediction_cutoff_at=CUTOFF,
        frozen_at=CUTOFF - timedelta(seconds=1),
        prior_close=Decimal("9.50"),
        float_shares=Decimal("1000000"),
        prior_1d_return_pct=Decimal("5"),
        prior_3d_return_pct=Decimal("8"),
    )

    assert state.live_value("gap_from_prior_close_pct") == (
        (Decimal("10.50") - Decimal("9.50")) / Decimal("9.50") * Decimal("100")
    )
    assert state.live_value("float_turnover") == Decimal("3700") / Decimal("1000000")
    assert state.live_value("recovered_after_cutoff_bar_count") is None
    diagnostic = next(feature for feature in state.features if feature.name == "recovered_after_cutoff_bar_count")
    assert diagnostic.value == Decimal("1")
    assert diagnostic.available_to_live_forecaster is False



def test_model_state_is_independent_from_evidence_quality() -> None:
    forecast = _v4_forecast()
    produced = V4ForecastAttempt(
        instrument_id="AAA",
        session_date=SESSION,
        attempted_at=CUTOFF - timedelta(seconds=1),
        model_state="PRODUCED",
        evidence_quality="DEGRADED",
        forecast_fingerprint=forecast.immutable_fingerprint,
    )
    assert produced.model_state == "PRODUCED"
    assert produced.evidence_quality == "DEGRADED"

    with pytest.raises(ValueError, match="not_applicable_requires_insufficient_evidence"):
        V4ForecastAttempt(
            instrument_id="AAA",
            session_date=SESSION,
            attempted_at=CUTOFF - timedelta(seconds=1),
            model_state="NOT_APPLICABLE",
            evidence_quality="DEGRADED",
        )


def test_formal_v3_v4_pair_requires_same_frozen_evidence_snapshot() -> None:
    timestamps = EvidenceTimestamps(
        observed_at=CUTOFF - timedelta(minutes=5),
        ingested_at=CUTOFF - timedelta(minutes=4),
        frozen_at=CUTOFF - timedelta(minutes=3),
    )
    snapshot = PremarketEvidenceSnapshot(
        snapshot_id="evidence-1",
        session_date=SESSION,
        prediction_cutoff_at=CUTOFF,
        frozen_at=CUTOFF - timedelta(minutes=2),
        evidence=(
            PremarketEvidenceItem(
                evidence_id="news-1",
                instrument_id="AAA",
                source_type="news",
                source_locator="example",
                values={"headline": "test"},
                timestamps=timestamps,
            ),
        ),
    )
    v3 = FrozenForecast(
        instrument_id="AAA",
        evidence_snapshot_id="evidence-1",
        feature_vector_fingerprint="v3-features",
        frozen_at=CUTOFF - timedelta(seconds=2),
        p_close_above_open=Decimal("0.55"),
        p_persistent_uptrend=Decimal("0.45"),
    )
    v4 = _v4_forecast()
    pair = bind_formal_v3_v4_pair(snapshot, v3=v3, v4=v4, outcome=True)
    assert pair.instrument_id == "AAA"

    bad_v3 = FrozenForecast(
        instrument_id="AAA",
        evidence_snapshot_id="other-evidence",
        feature_vector_fingerprint="v3-features",
        frozen_at=CUTOFF - timedelta(seconds=2),
        p_close_above_open=Decimal("0.55"),
        p_persistent_uptrend=Decimal("0.45"),
    )
    with pytest.raises(ValueError, match="formal_v3_snapshot_mismatch"):
        bind_formal_v3_v4_pair(snapshot, v3=bad_v3, v4=v4, outcome=True)



def test_existing_finviz_universe_binds_directly_to_v4_cohort() -> None:
    observed = CUTOFF - timedelta(minutes=7)
    candidate = GapperCandidate(
        instrument_id="equity:US:AAA",
        observed_at=observed,
        evidence_observed_at={"finviz_top_gainers": observed},
        previous_close=Decimal("10"),
        premarket_price=Decimal("15"),
        gap_pct=Decimal("50"),
        premarket_volume=Decimal("500000"),
        premarket_dollar_volume=Decimal("7500000"),
        tod_rvol=Decimal("8"),
        float_shares=Decimal("2000000"),
        spread_bps=Decimal("45"),
    )
    universe = GapperUniverseSnapshot(
        universe_id="finviz-universe-2026-09-18",
        session_date=SESSION,
        evaluation_time=observed,
        discovery_source="finviz",
        source_locator="https://finviz.com/screener?v=340&s=ta_topgainers",
        source_candidate_symbols=("AAA",),
        candidates=(candidate,),
        source_fingerprint="a" * 64,
    )
    cohort = finviz_cohort_from_universe(universe, prediction_cutoff_at=CUTOFF)
    assert cohort.symbols == ("AAA",)

    state = market_state_from_candidate(
        cohort=cohort,
        candidate=candidate,
        snapshot_id="candidate-state",
        prediction_cutoff_at=CUTOFF,
        frozen_at=CUTOFF - timedelta(seconds=1),
    )
    assert state.live_value("gap_from_prior_close_pct") == Decimal("50")
    assert state.live_value("float_turnover") == Decimal("0.25")

    quality = summarize_evidence_quality(
        state,
        critical_features=("gap_from_prior_close_pct",),
        important_features=(
            "distance_from_premarket_vwap_pct",
            "position_in_premarket_range",
            "late_premarket_acceleration",
        ),
    )
    assert quality.quality == "DEGRADED"
    assert "distance_from_premarket_vwap_pct" in quality.degraded_features


def test_missing_enrichment_does_not_make_core_candidate_forecast_insufficient() -> None:
    observed = CUTOFF - timedelta(minutes=5)
    state = _state(
        _feature("gap_pct", "40", observed_at=observed, ingested_at=observed + timedelta(seconds=10))
    )
    quality = summarize_evidence_quality(
        state,
        critical_features=("gap_pct",),
        important_features=("distance_from_premarket_vwap_pct",),
    )
    assert quality.quality == "DEGRADED"
    assert quality.missing_critical_features == ()


def _regular_bar(index: int, *, open_: str, high: str, low: str, close: str, volume: str) -> MarketBar:
    start = OPEN + timedelta(minutes=5 * index)
    return MarketBar(
        instrument_id="AAA",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        session="regular",
        provider="alpaca_sip",
        provider_event_id=f"confirm-{index}",
        received_at=start + timedelta(minutes=5),
    )


def test_deterministic_confirmation_uses_finalized_higher_low_vwap_break_and_volume():
    bars = [
        _regular_bar(0, open_="10.00", high="10.50", low="9.80", close="10.30", volume="1000"),
        _regular_bar(1, open_="10.30", high="10.35", low="9.95", close="10.10", volume="800"),
        _regular_bar(2, open_="10.10", high="10.60", low="10.05", close="10.55", volume="1200"),
    ]
    first = evaluate_post_open_confirmation(
        instrument_id="AAA",
        previous_state="WAIT_OPEN",
        bars=bars[:1],
        transition_at=bars[0].end_time,
        data_quality_ok=True,
    )
    assert first is not None and first.new_state == "OBSERVE_INITIAL_STRUCTURE"
    second = evaluate_post_open_confirmation(
        instrument_id="AAA",
        previous_state=first.new_state,
        bars=bars[:2],
        transition_at=bars[1].end_time,
        data_quality_ok=True,
    )
    assert second is not None and second.new_state == "OBSERVE_PULLBACK"
    confirmed = evaluate_post_open_confirmation(
        instrument_id="AAA",
        previous_state=second.new_state,
        bars=bars,
        transition_at=bars[-1].end_time,
        data_quality_ok=True,
    )
    assert confirmed is not None
    assert confirmed.new_state == "CONFIRMED_LONG"


def test_authorization_requires_true_expected_return_not_positive_median():
    forecast = _v4_forecast()
    confirmation = transition_confirmation(
        instrument_id="AAA",
        previous_state="OBSERVE_PULLBACK",
        new_state="CONFIRMED_LONG",
        transition_at=OPEN + timedelta(minutes=15),
        trigger="deterministic confirmation",
        bar_ids=("bar",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=15),
    )
    actionability = actionability_from_confirmation(confirmation)
    gross = GrossReturnDistribution(
        q10=Decimal("-0.02"),
        q50=Decimal("0.04"),
        q90=Decimal("0.30"),
        expected_return=Decimal("-0.01"),
    )
    cost = ExecutionCostInput(
        symbol="AAA",
        decision_at=confirmation.transition_at,
        notional=Decimal("500"),
        reference_price=Decimal("10"),
        observed_bid=Decimal("9.99"),
        observed_ask=Decimal("10.01"),
        estimated_slippage_bps=Decimal("2"),
        estimated_impact_bps=Decimal("1"),
    )
    receipt = authorize_trade(
        forecast=forecast,
        confirmation=confirmation,
        actionability=actionability,
        gross=gross,
        cost=cost,
        evidence_fingerprint="exec",
    )
    assert receipt.decision == "NO_TRADE"
    assert receipt.reasons == ("EXPECTED_NET_ALPHA_NONPOSITIVE",)
    assert receipt.gross_median_return == Decimal("0.04")
    assert receipt.gross_expected_return == Decimal("-0.01")


def test_round_trip_costs_are_explicit_and_cash_allocator_does_not_renormalize():
    forecast = _v4_forecast()
    confirmation = transition_confirmation(
        instrument_id="AAA",
        previous_state="OBSERVE_PULLBACK",
        new_state="CONFIRMED_LONG",
        transition_at=OPEN + timedelta(minutes=15),
        trigger="deterministic confirmation",
        bar_ids=("bar",),
        latest_finalized_bar_at=OPEN + timedelta(minutes=15),
    )
    actionability = actionability_from_confirmation(confirmation)
    gross = GrossReturnDistribution(
        q10=Decimal("-0.02"),
        q50=Decimal("0.08"),
        q90=Decimal("0.20"),
        expected_return=Decimal("0.10"),
    )
    cost = ExecutionCostInput(
        symbol="AAA",
        decision_at=confirmation.transition_at,
        notional=Decimal("500"),
        reference_price=Decimal("10"),
        observed_bid=Decimal("9.95"),
        observed_ask=Decimal("10.05"),
        estimated_slippage_bps=Decimal("5"),
        estimated_impact_bps=Decimal("2"),
        expected_exit_spread_bps=Decimal("80"),
        expected_exit_slippage_bps=Decimal("5"),
        expected_exit_impact_bps=Decimal("2"),
        estimated_round_trip_commission_bps=Decimal("1"),
    )
    net = apply_execution_costs(gross, cost)
    assert net.entry_spread_cost_bps > 0
    assert net.expected_exit_spread_cost_bps == Decimal("40")
    receipt = authorize_trade(
        forecast=forecast,
        confirmation=confirmation,
        actionability=actionability,
        gross=gross,
        cost=cost,
        evidence_fingerprint="exec",
    )
    assert receipt.decision == "LONG"
    portfolio = freeze_cash_preserving_authorized_portfolio(
        [receipt],
        starting_equity=Decimal("1000"),
        max_position_fraction=Decimal("0.20"),
    )
    assert isinstance(portfolio, CashPreservingAuthorizedPortfolio)
    assert portfolio.positions[0].allocation == Decimal("200")
    assert portfolio.cash == Decimal("800")
