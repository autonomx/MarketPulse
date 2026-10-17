from __future__ import annotations

"""Formal entry points for the scheduled prospective prediction experiment.

The lower-level evidence module contains reusable contracts.  This module is the
stricter scheduled-run boundary: causal snapshots must be internally ordered,
portfolio freezes must precede the prediction cutoff, and formal trend outcomes
must use one canonical consolidated-SIP RAW 5-minute series.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from .models import AdjustmentMode, MarketBar
from .prospective_prediction_v4 import (
    FrozenForecastV4,
    PairedForecastObservation,
    bind_v3_v4_pair,
)
from .prospective_prediction_evidence import (
    AnalysisSessionPrices,
    ConfidenceRiskFactors,
    FrozenForecast,
    FrozenPortfolio,
    OutcomeMeasurementsV1,
    OutcomeScorability,
    PremarketEvidenceSnapshot,
    VersionedOutcomeLabels,
    assess_outcome_scorability,
    build_outcome_measurements,
    derive_outcome_labels,
    freeze_research_portfolios,
)

FORMAL_OUTCOME_BAR_INTERVAL = "5m"
FORMAL_OUTCOME_BAR_DURATION = timedelta(minutes=5)


def validate_formal_premarket_snapshot(snapshot: PremarketEvidenceSnapshot) -> None:
    """Fail closed on internally impossible or post-cutoff evidence ordering."""

    if snapshot.frozen_at > snapshot.prediction_cutoff_at:
        raise ValueError("formal_snapshot_frozen_after_prediction_cutoff")
    for item in snapshot.evidence:
        timestamps = item.timestamps
        if timestamps.ingested_at < timestamps.observed_at:
            raise ValueError(f"formal_evidence_ingested_before_observed:{item.evidence_id}")
        if timestamps.frozen_at < timestamps.ingested_at:
            raise ValueError(f"formal_evidence_frozen_before_ingested:{item.evidence_id}")
        if timestamps.frozen_at > snapshot.frozen_at:
            raise ValueError(f"formal_evidence_frozen_after_snapshot:{item.evidence_id}")
        if timestamps.observed_at > snapshot.prediction_cutoff_at:
            raise ValueError(f"formal_post_cutoff_evidence:{item.evidence_id}")
        if timestamps.ingested_at > snapshot.prediction_cutoff_at:
            raise ValueError(f"formal_post_cutoff_ingestion:{item.evidence_id}")
        if timestamps.frozen_at > snapshot.prediction_cutoff_at:
            raise ValueError(f"formal_post_cutoff_freeze:{item.evidence_id}")


def freeze_formal_research_portfolios(
    snapshot: PremarketEvidenceSnapshot,
    forecasts: Sequence[FrozenForecast],
    *,
    frozen_at,
    risk_factors: dict[str, ConfidenceRiskFactors] | None = None,
    starting_equity: Decimal = Decimal("1000"),
) -> tuple[FrozenPortfolio, FrozenPortfolio, FrozenPortfolio, FrozenPortfolio]:
    """Freeze A/B/C/D only from forecasts causally bound to one snapshot."""

    validate_formal_premarket_snapshot(snapshot)
    if frozen_at.tzinfo is None:
        raise ValueError("formal_portfolio_freeze_timestamp_must_be_timezone_aware")
    frozen_utc = frozen_at.astimezone(snapshot.prediction_cutoff_at.tzinfo)
    if frozen_utc > snapshot.prediction_cutoff_at:
        raise ValueError("formal_portfolios_frozen_after_prediction_cutoff")
    for forecast in forecasts:
        if forecast.evidence_snapshot_id != snapshot.snapshot_id:
            raise ValueError(f"forecast_snapshot_mismatch:{forecast.instrument_id}")
        if forecast.frozen_at > snapshot.prediction_cutoff_at:
            raise ValueError(f"forecast_frozen_after_prediction_cutoff:{forecast.instrument_id}")
    return freeze_research_portfolios(
        forecasts,
        frozen_at=frozen_at,
        risk_factors=risk_factors,
        starting_equity=starting_equity,
    )


def canonical_formal_5m_bars(bars: Sequence[MarketBar]) -> tuple[MarketBar, ...]:
    """Return one revision-resolved RAW regular-session 5m series.

    Multiple providers are rejected rather than blended. Within one provider,
    duplicate/revised timestamps resolve to the highest ingestion revision, then
    latest received timestamp/sequence. Missing timestamps are retained as gaps;
    no synthetic halt bars are created.
    """

    candidates = [
        bar
        for bar in bars
        if bar.interval == FORMAL_OUTCOME_BAR_INTERVAL
        and bar.is_final
        and bar.session == "regular"
        and bar.adjustment_mode == AdjustmentMode.RAW
    ]
    if not candidates:
        raise ValueError("formal_prediction_scoring_requires_raw_final_5m_bars")
    providers = {bar.provider for bar in candidates}
    if len(providers) != 1:
        raise ValueError("formal_prediction_scoring_requires_single_provider")
    for bar in candidates:
        if bar.end_time - bar.start_time != FORMAL_OUTCOME_BAR_DURATION:
            raise ValueError("formal_prediction_scoring_requires_exact_5m_bar_duration")

    by_window: dict[tuple[object, object], MarketBar] = {}
    for bar in candidates:
        key = (bar.start_time, bar.end_time)
        prior = by_window.get(key)
        if prior is None:
            by_window[key] = bar
            continue
        prior_rank = (
            prior.ingestion_revision,
            prior.received_at,
            prior.provider_sequence if prior.provider_sequence is not None else -1,
        )
        current_rank = (
            bar.ingestion_revision,
            bar.received_at,
            bar.provider_sequence if bar.provider_sequence is not None else -1,
        )
        if current_rank > prior_rank:
            by_window[key] = bar
    return tuple(sorted(by_window.values(), key=lambda bar: (bar.start_time, bar.end_time)))


def build_formal_outcome_measurements(
    *,
    prices: AnalysisSessionPrices,
    bars: Sequence[MarketBar],
) -> OutcomeMeasurementsV1:
    """Build formal v1 outcomes from the canonical RAW regular-session 5m series."""

    canonical = canonical_formal_5m_bars(bars)
    return build_outcome_measurements(prices=prices, bars=canonical)


class FormalOutcomeEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    measurements: OutcomeMeasurementsV1
    scorability: OutcomeScorability
    labels: VersionedOutcomeLabels | None = None


def build_formal_outcome_evaluation(
    *,
    prices: AnalysisSessionPrices,
    bars: Sequence[MarketBar],
    confirmed_nontrading_minutes: Decimal = Decimal("0"),
) -> FormalOutcomeEvaluation:
    measurements = build_formal_outcome_measurements(prices=prices, bars=bars)
    scorability = assess_outcome_scorability(
        measurements,
        confirmed_nontrading_minutes=confirmed_nontrading_minutes,
    )
    labels = derive_outcome_labels(measurements) if scorability.status == "SCORABLE" else None
    return FormalOutcomeEvaluation(
        measurements=measurements,
        scorability=scorability,
        labels=labels,
    )


def build_formal_outcome_labels(
    *,
    prices: AnalysisSessionPrices,
    bars: Sequence[MarketBar],
    confirmed_nontrading_minutes: Decimal = Decimal("0"),
) -> tuple[OutcomeMeasurementsV1, VersionedOutcomeLabels]:
    evaluation = build_formal_outcome_evaluation(
        prices=prices,
        bars=bars,
        confirmed_nontrading_minutes=confirmed_nontrading_minutes,
    )
    if evaluation.labels is None:
        raise ValueError(
            "formal_outcome_unscorable:" + ",".join(evaluation.scorability.reasons)
        )
    return evaluation.measurements, evaluation.labels


def bind_formal_v3_v4_pair(
    snapshot: PremarketEvidenceSnapshot,
    *,
    v3: FrozenForecast,
    v4: FrozenForecastV4,
    outcome: bool,
) -> PairedForecastObservation:
    """Bind one matched champion/challenger observation under the formal causal gate."""

    validate_formal_premarket_snapshot(snapshot)
    if v3.evidence_snapshot_id != snapshot.snapshot_id:
        raise ValueError("formal_v3_snapshot_mismatch")
    if v4.evidence_snapshot_id != snapshot.snapshot_id:
        raise ValueError("formal_v4_snapshot_mismatch")
    if v3.frozen_at > snapshot.prediction_cutoff_at:
        raise ValueError("formal_v3_forecast_frozen_after_cutoff")
    if v4.frozen_at > snapshot.prediction_cutoff_at:
        raise ValueError("formal_v4_forecast_frozen_after_cutoff")
    if v4.session_date != snapshot.session_date:
        raise ValueError("formal_v4_session_mismatch")
    return bind_v3_v4_pair(v3=v3, v4=v4, outcome=outcome)
