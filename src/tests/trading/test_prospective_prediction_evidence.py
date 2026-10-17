from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.prospective_prediction_evidence import (
    AnalysisSessionPrices,
    BinaryForecastObservation,
    ConfidenceRiskFactors,
    EvidenceTimestamps,
    FrozenForecast,
    HypothesisDefinition,
    HypothesisRecord,
    PremarketEvidenceItem,
    PremarketEvidenceSnapshot,
    SIPTradeEvent,
    build_outcome_measurements,
    derive_outcome_labels,
    evaluate_binary_forecasts,
    freeze_research_portfolios,
    score_frozen_portfolio,
    select_analysis_session_prices,
    transition_hypothesis,
)

UTC = timezone.utc
SESSION = date(2026, 9, 16)
OPEN_UTC = datetime(2026, 9, 16, 13, 30, tzinfo=UTC)
CLOSE_UTC = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
INSTRUMENT = "equity:NASDAQ:TEST"


def _event(
    price: str,
    when: datetime,
    *,
    event_id: str,
    sequence: int,
    **flags,
) -> SIPTradeEvent:
    return SIPTradeEvent(
        instrument_id=INSTRUMENT,
        price=Decimal(price),
        event_timestamp=when,
        received_timestamp=when + timedelta(milliseconds=20),
        exchange="Q",
        provider_event_id=event_id,
        sequence=sequence,
        **flags,
    )


def _prices(open_price: str = "10", close_price: str = "12") -> AnalysisSessionPrices:
    return select_analysis_session_prices(
        [
            _event(open_price, OPEN_UTC, event_id="open", sequence=1),
            _event(close_price, CLOSE_UTC - timedelta(milliseconds=1), event_id="close", sequence=2),
        ],
        session_date=SESSION,
        outcome_state="FINAL",
    )


def _bar(index: int, close: Decimal) -> MarketBar:
    start = OPEN_UTC + timedelta(minutes=5 * index)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=max(Decimal("0.01"), close - Decimal("0.02")),
        high=close + Decimal("0.05"),
        low=max(Decimal("0.01"), close - Decimal("0.08")),
        close=close,
        volume=Decimal("1000") + Decimal(index),
        is_final=True,
        adjustment_mode=AdjustmentMode.RAW,
        session="regular",
        provider="alpaca_sip",
        provider_sequence=index,
        received_at=start + timedelta(minutes=5, seconds=1),
    )


def _forecast(symbol: str, probability: str) -> FrozenForecast:
    return FrozenForecast(
        instrument_id=symbol,
        evidence_snapshot_id="snap-1",
        feature_vector_fingerprint=f"features-{symbol}",
        frozen_at=OPEN_UTC - timedelta(minutes=1),
        p_close_above_open=Decimal(probability),
        p_persistent_uptrend=Decimal("0.50"),
    )


def test_premarket_snapshot_rejects_post_cutoff_evidence() -> None:
    cutoff = OPEN_UTC
    item = PremarketEvidenceItem(
        evidence_id="news-1",
        instrument_id=INSTRUMENT,
        source_type="news",
        source_locator="https://example.test/news",
        timestamps=EvidenceTimestamps(
            published_at=cutoff - timedelta(minutes=10),
            observed_at=cutoff + timedelta(seconds=1),
            ingested_at=cutoff + timedelta(seconds=2),
            frozen_at=cutoff + timedelta(seconds=3),
        ),
    )
    with pytest.raises(ValueError, match="post_cutoff_evidence"):
        PremarketEvidenceSnapshot(
            snapshot_id="snapshot-1",
            session_date=SESSION,
            prediction_cutoff_at=cutoff,
            frozen_at=cutoff - timedelta(seconds=1),
            evidence=(item,),
        )


def test_sip_contract_ignores_ineligible_and_post_session_prints() -> None:
    events = [
        _event("1", OPEN_UTC, event_id="auction", sequence=1, auction=True),
        _event("10", OPEN_UTC + timedelta(milliseconds=1), event_id="open", sequence=2),
        _event("10.5", OPEN_UTC + timedelta(hours=2), event_id="mid", sequence=3),
        _event("99", CLOSE_UTC - timedelta(seconds=1), event_id="late", sequence=4, late_report=True),
        _event("12", CLOSE_UTC - timedelta(milliseconds=1), event_id="close", sequence=5),
        _event("500", CLOSE_UTC, event_id="after", sequence=6),
    ]
    prices = select_analysis_session_prices(events, session_date=SESSION, outcome_state="FINAL")

    assert prices.open_price == Decimal("10")
    assert prices.close_price == Decimal("12")
    assert prices.open_event.provider_event_id == "open"
    assert prices.close_event.provider_event_id == "close"
    assert prices.eligible_trade_count == 3
    assert prices.discarded_trade_count == 2
    assert prices.outcome_state == "FINAL"


def test_continuous_outcomes_are_authority_and_labels_are_views() -> None:
    prices = _prices()
    bars = [_bar(i, Decimal("10") + Decimal(i + 1) * Decimal("0.025")) for i in range(78)]

    measurements = build_outcome_measurements(prices=prices, bars=bars)
    labels = derive_outcome_labels(measurements)

    assert measurements.open_to_close_return == Decimal("0.2")
    assert measurements.normalized_slope > 0
    assert measurements.vwap_occupancy >= Decimal("0.60")
    assert measurements.observed_session_coverage == Decimal("1")
    assert measurements.halt_or_gap_minutes == 0
    assert labels.close_above_open is True
    assert labels.persistent_uptrend is True
    assert labels.session_regime == "PERSISTENT_UP"
    assert labels.session_regime_evaluation_role == "diagnostic"


def test_halted_or_missing_periods_are_not_interpolated() -> None:
    prices = _prices("10", "10.5")
    bars = [
        _bar(0, Decimal("10.1")),
        MarketBar(
            **_bar(77, Decimal("10.45")).model_dump(),
        ),
    ]

    measurements = build_outcome_measurements(prices=prices, bars=bars)

    assert measurements.observed_bar_count == 2
    assert measurements.observed_session_coverage == Decimal("10") / Decimal("390")
    assert measurements.halt_or_gap_minutes == Decimal("380")
    assert measurements.interpolated_halt_minutes == 0


def test_four_portfolios_freeze_distinct_semantics() -> None:
    forecasts = [_forecast("A", "0.60"), _forecast("B", "0.55"), _forecast("C", "0.45")]
    risk = {
        "A": ConfidenceRiskFactors(
            catalyst="stale_theme_technical_unexplained",
            liquidity="weak",
            opening_extension="HIGH",
            supply_risk="HIGH",
            squeeze="chaotic",
        ),
        "B": ConfidenceRiskFactors(
            catalyst="strong_definitive",
            liquidity="strong",
            opening_extension="LOW",
            supply_risk="LOW",
            squeeze="neutral",
        ),
    }

    equal, weighted, probability, cash = freeze_research_portfolios(
        forecasts,
        frozen_at=OPEN_UTC - timedelta(seconds=30),
        risk_factors=risk,
    )

    assert [p.instrument_id for p in equal.positions] == ["A", "B"]
    assert equal.positions[0].allocation == Decimal("500")
    assert equal.positions[1].allocation == Decimal("500")
    assert weighted.positions[1].weight > weighted.positions[0].weight
    assert probability.positions[0].weight == Decimal("2") / Decimal("3")
    assert probability.positions[1].weight == Decimal("1") / Decimal("3")
    assert cash.positions == ()
    assert cash.cash == Decimal("1000")


def test_portfolio_scoring_uses_frozen_allocations_and_analysis_prices() -> None:
    forecasts = [_forecast("A", "0.60"), _forecast("B", "0.55")]
    equal, _, _, _ = freeze_research_portfolios(forecasts, frozen_at=OPEN_UTC - timedelta(seconds=30))

    def symbol_prices(symbol: str, close: str) -> AnalysisSessionPrices:
        events = [
            SIPTradeEvent(
                instrument_id=symbol,
                price=Decimal("10"),
                event_timestamp=OPEN_UTC,
                received_timestamp=OPEN_UTC + timedelta(milliseconds=1),
                provider_event_id=f"{symbol}-open",
                sequence=1,
            ),
            SIPTradeEvent(
                instrument_id=symbol,
                price=Decimal(close),
                event_timestamp=CLOSE_UTC - timedelta(milliseconds=1),
                received_timestamp=CLOSE_UTC + timedelta(milliseconds=1),
                provider_event_id=f"{symbol}-close",
                sequence=2,
            ),
        ]
        return select_analysis_session_prices(events, session_date=SESSION, outcome_state="FINAL")

    score = score_frozen_portfolio(equal, {"A": symbol_prices("A", "11"), "B": symbol_prices("B", "9")})
    assert score.starting_equity == Decimal("1000")
    assert score.ending_equity == Decimal("1000")
    assert score.pnl == 0
    assert score.return_pct == 0


def test_forecast_metrics_include_climatology_skill_logloss_precision_recall() -> None:
    metrics = evaluate_binary_forecasts(
        [
            BinaryForecastObservation(instrument_id="A", probability=Decimal("0.80"), outcome=True),
            BinaryForecastObservation(instrument_id="B", probability=Decimal("0.60"), outcome=False),
        ],
        frozen_climatology_probability=Decimal("0.50"),
    )

    assert metrics.n == 2
    assert metrics.accuracy == Decimal("0.5")
    assert metrics.brier_score == Decimal("0.20")
    assert metrics.climatology_brier == Decimal("0.25")
    assert metrics.brier_skill == Decimal("0.20")
    assert metrics.bullish_precision == Decimal("0.5")
    assert metrics.bullish_recall == Decimal("1")
    assert metrics.log_loss is not None and metrics.log_loss > 0


def test_hypothesis_definition_freezes_before_forward_validation() -> None:
    definition = HypothesisDefinition(
        hypothesis_id="H17",
        statement="Extreme extension plus weak catalyst plus supply risk reduces persistent-uptrend probability.",
        feature_definition="extension=HIGH AND catalyst=weak AND supply=HIGH",
        expected_direction="negative",
        evaluation_criterion="forward Brier delta < 0",
        proposed_at=OPEN_UTC,
    )
    record = HypothesisRecord(definition=definition, last_transition_at=OPEN_UTC)
    observational = transition_hypothesis(record, new_status="OBSERVATIONAL", transitioned_at=OPEN_UTC + timedelta(days=1))
    frozen = transition_hypothesis(observational, new_status="FROZEN_CANDIDATE", transitioned_at=OPEN_UTC + timedelta(days=2))

    assert frozen.definition_frozen_at == OPEN_UTC + timedelta(days=2)
    assert frozen.definition.definition_fingerprint == definition.definition_fingerprint
    with pytest.raises(ValueError, match="invalid_hypothesis_transition"):
        transition_hypothesis(observational, new_status="FORWARD_VALIDATION", transitioned_at=OPEN_UTC + timedelta(days=3))
