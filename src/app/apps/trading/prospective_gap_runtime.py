from __future__ import annotations

import logging

from app.config.env import environment

logger = logging.getLogger(__name__)

"""End-to-end authority runtime for the prospective Top-10 gap experiment.

The runtime owns machine-readable persistence and phase transitions. External
schedulers may still discover/research catalysts, but they submit typed evidence
to this boundary instead of independently reconstructing forecast, confirmation,
outcome, or portfolio state in Markdown.
"""

import concurrent.futures
import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Final, Literal, Sequence, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .gapper_dataset import GapperCandidate
from .prospective_gap_repository import (
    ProspectiveGapRepository,
    ProspectiveGapSessionLedger,
    default_prospective_gap_repository,
)
from .prospective_prediction_evidence import (
    BinaryForecastMetrics,
    BinaryForecastObservation,
    ConfidenceRiskFactors,
    FrozenForecast,
    FrozenPortfolio,
    PortfolioScore,
    PremarketEvidenceSnapshot,
    evaluate_binary_forecasts,
    freeze_research_portfolios,
    score_frozen_portfolio,
)
from .prospective_prediction_operational import (
    CashPreservingShadowPortfolio,
    FormalOutcomeBundle,
    OperationalConfirmationEvaluation,
    OperationalPremarketState,
    build_cash_preserving_shadow_portfolio,
    evaluate_operational_confirmation,
    load_operational_formal_outcome,
    load_operational_premarket_state,
)
from .prospective_prediction_v41 import DEFAULT_V41_SPEC
from .prospective_prediction_v42 import (
    DEFAULT_V42_SPEC,
    V42Forecast,
    V42ForecastAttempt,
    V42ReturnMetrics,
    V42ReturnObservation,
    evaluate_v42_return_metrics,
    freeze_v42_forecast,
    session_eligible_for_v42_forward_validation,
)
from .prospective_prediction_v42_action import (
    DEFAULT_V42_ACTION_POLICY,
    PortfolioF,
    V42ActionPolicy,
    V42ActionSnapshot,
    V42AuthorizationReceipt,
    V42WatchDecision,
    authorize_v42_action,
    build_portfolio_f,
    classify_v42_watch,
    evaluate_v42_post_open_action,
)
from .prospective_prediction_v43 import (
    DEFAULT_V43_SPEC,
    V43CohortRegime,
    V43ExtensionExhaustionOverlay,
    V43Forecast,
    V43ForecastAttempt,
    derive_v43_cohort_regime,
    derive_v43_extension_overlay,
    freeze_v43_forecast,
    session_eligible_for_v43_forward_validation,
)
from .prospective_prediction_v43_action import (
    DEFAULT_V43_ACTION_POLICY,
    PortfolioG,
    V43ActionPolicy,
    V43ActionSnapshot,
    V43AuthorizationReceipt,
    V43WatchDecision,
    authorize_v43_action,
    build_portfolio_g,
    classify_v43_watch,
    evaluate_v43_post_open_action,
)
from .prospective_prediction_v4 import (
    ActionabilityDecision,
    CalibratorArtifact,
    CatalystDecomposition,
    ConfirmationState,
    ConfirmationTransitionReceipt,
    DEFAULT_V4_MODEL_SPEC,
    ExecutionCostInput,
    ExtensionExhaustionRisk,
    FinvizFrozenCohort,
    FrozenForecastV4,
    GrossReturnDistribution,
    MechanismRiskScores,
    PairedForecastMetrics,
    PairedForecastObservation,
    PredictionEvidenceQuality,
    RegimeTag,
    TradeAuthorizationReceipt,
    V4ForecastAttempt,
    authorize_trade,
    derive_extension_exhaustion_risk,
    evaluate_paired_v3_v4,
    extension_components_from_market_state,
    freeze_v4_forecast,
)
from .service import TradingMarketDataService, default_market_data_service
from .strategies.models import GapPullbackConfig
from app.apps.trading.us_equity_calendar import EASTERN as _ET


RUNTIME_VERSION: Final = "prospective-gap-runtime-v1"
PORTFOLIO_E_POLICY_VERSION: Final = "prospective-gap-portfolio-e-v1"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("prospective runtime timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class PortfolioEPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-portfolio-e-v1"] = PORTFOLIO_E_POLICY_VERSION
    starting_equity: Decimal = Field(default=Decimal("1000"), gt=0)
    max_positions: int = Field(default=3, ge=1, le=10)
    max_position_fraction: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    minimum_bullish_probability: Decimal = Field(default=Decimal("0.50"), ge=0, le=1)
    minimum_net_expected_return: Decimal = Decimal("0")
    minimum_net_q10: Decimal = Decimal("-0.10")
    estimated_entry_slippage_bps: Decimal = Field(default=Decimal("25"), ge=0)
    estimated_entry_impact_bps: Decimal = Field(default=Decimal("10"), ge=0)
    estimated_exit_slippage_bps: Decimal = Field(default=Decimal("25"), ge=0)
    estimated_exit_impact_bps: Decimal = Field(default=Decimal("10"), ge=0)
    estimated_round_trip_commission_bps: Decimal = Field(default=Decimal("0"), ge=0)


class ProspectiveClimatologyState(BaseModel):
    """Machine-readable confirmed baseline carried across scheduled sessions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-climatology-state-v1"] = "prospective-gap-climatology-state-v1"
    through_session: date
    observation_count: int = Field(ge=0)
    positive_count: int = Field(ge=0)
    probability: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def counts_match_probability(self):
        if self.positive_count > self.observation_count:
            raise ValueError("climatology_positive_count_exceeds_observations")
        expected = (
            Decimal(self.positive_count) / Decimal(self.observation_count)
            if self.observation_count
            else Decimal("0")
        )
        if abs(expected - self.probability) > Decimal("0.0001"):
            raise ValueError("climatology_probability_does_not_match_counts")
        return self


class SchedulerPremarketInstrumentInput(BaseModel):
    """Research-owned fields that the cloud scheduler can safely freeze."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1, max_length=16)
    discovery_rank: int = Field(ge=1)
    v3_p_close_above_open: Decimal = Field(ge=0, le=1)
    v3_p_persistent_uptrend: Decimal = Field(ge=0, le=1)
    v4_raw_p_close_above_open: Decimal = Field(ge=0, le=1)
    v4_calibrated_p_close_above_open: Decimal = Field(ge=0, le=1)
    v4_extension_risk_score: Decimal = Field(ge=0, le=1)
    v4_evidence_quality: Literal["COMPLETE", "DEGRADED"] = "DEGRADED"
    catalyst: CatalystDecomposition
    mechanisms: MechanismRiskScores
    float_shares: Decimal | None = Field(default=None, gt=0)
    market_cap: Decimal | None = Field(default=None, ge=0)
    tod_rvol: Decimal | None = Field(default=None, ge=0)
    dilution_flags: tuple[str, ...] = ()
    first_catalyst_at: datetime | None = None
    regime_tags: tuple[RegimeTag, ...] = ()
    regime_primary: RegimeTag | None = None
    regime_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    uncertainty: Literal["low", "moderate", "high"] = "high"

    @field_validator("first_catalyst_at")
    @classmethod
    def catalyst_time_aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def regime_alignment(self):
        if self.regime_primary is not None and self.regime_primary not in self.regime_tags:
            raise ValueError("scheduler_regime_primary_must_be_in_tags")
        return self


class SchedulerPremarketHandoff(BaseModel):
    """Lightweight GitHub transport contract; runtime owns market reconstruction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_version: Literal["prospective-gap-scheduler-handoff-v1"] = "prospective-gap-scheduler-handoff-v1"
    session_date: date
    cohort_id: str
    discovered_at: datetime
    research_frozen_at: datetime
    prediction_cutoff_at: datetime
    instruments: tuple[SchedulerPremarketInstrumentInput, ...]
    baseline_through_session: date
    baseline_observation_count: int = Field(ge=0)
    baseline_positive_count: int = Field(ge=0)
    run_id: str | None = None

    @field_validator("discovered_at", "research_frozen_at", "prediction_cutoff_at")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def causal_and_cohort_alignment(self):
        if self.discovered_at > self.research_frozen_at:
            raise ValueError("scheduler_handoff_research_frozen_before_discovery")
        if self.research_frozen_at > self.prediction_cutoff_at:
            raise ValueError("scheduler_handoff_research_frozen_after_cutoff")
        if not self.instruments:
            raise ValueError("scheduler_handoff_requires_instruments")
        symbols = [row.symbol.upper() for row in self.instruments]
        if len(symbols) != len(set(symbols)):
            raise ValueError("scheduler_handoff_duplicate_symbol")
        ranks = [row.discovery_rank for row in self.instruments]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("scheduler_handoff_ranks_must_be_contiguous")
        for row in self.instruments:
            if (
                row.first_catalyst_at is not None
                and row.first_catalyst_at > self.research_frozen_at
            ):
                raise ValueError(f"scheduler_catalyst_after_research_freeze:{row.symbol}")
        if self.baseline_through_session >= self.session_date:
            raise ValueError("scheduler_baseline_must_precede_session")
        if self.baseline_positive_count > self.baseline_observation_count:
            raise ValueError("scheduler_baseline_positive_count_exceeds_observations")
        return self

    @property
    def baseline_probability(self) -> Decimal | None:
        if self.baseline_observation_count == 0:
            return None
        return Decimal(self.baseline_positive_count) / Decimal(self.baseline_observation_count)


class PremarketInstrumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: GapperCandidate
    v3_forecast: FrozenForecast
    catalyst: CatalystDecomposition
    mechanisms: MechanismRiskScores
    calibrator: CalibratorArtifact
    evidence_snapshot_id: str
    evidence_snapshot: PremarketEvidenceSnapshot | None = None
    first_catalyst_at: datetime | None = None
    prior_1d_return_pct: Decimal | None = None
    prior_3d_return_pct: Decimal | None = None
    regime_tags: tuple[RegimeTag, ...] = ()
    regime_primary: RegimeTag | None = None
    regime_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    uncertainty: Literal["low", "moderate", "high"] = "high"
    economic_distribution: GrossReturnDistribution | None = None

    @field_validator("first_catalyst_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def instrument_alignment(self):
        if self.v3_forecast.instrument_id != self.candidate.instrument_id:
            raise ValueError("v3_forecast_candidate_instrument_mismatch")
        if self.regime_primary is not None and self.regime_primary not in self.regime_tags:
            raise ValueError("regime_primary_must_be_in_regime_tags")
        if self.evidence_snapshot is not None:
            if self.evidence_snapshot.snapshot_id != self.evidence_snapshot_id:
                raise ValueError("evidence_snapshot_id_mismatch")
            if self.evidence_snapshot.session_date != self.v3_forecast.frozen_at.astimezone(_ET).date():
                raise ValueError("evidence_snapshot_session_mismatch")
        return self


class PremarketFreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cohort: FinvizFrozenCohort
    frozen_at: datetime
    instruments: tuple[PremarketInstrumentInput, ...]
    risk_factors: dict[str, ConfidenceRiskFactors] = Field(default_factory=dict)
    frozen_climatology_probability: Decimal | None = Field(default=None, ge=0, le=1)
    confirmation_strategy_config: GapPullbackConfig = Field(default_factory=GapPullbackConfig)
    portfolio_e_policy: PortfolioEPolicy = Field(default_factory=PortfolioEPolicy)
    run_id: str | None = None

    @field_validator("frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def causal_and_cohort_alignment(self):
        if self.frozen_at > self.cohort.discovery_cutoff_at:
            raise ValueError("premarket_runtime_freeze_after_prediction_cutoff")
        symbols = set(self.cohort.symbols)
        if len(self.instruments) != len(self.cohort.symbols):
            raise ValueError("premarket_runtime_requires_one_input_per_cohort_symbol")
        seen: set[str] = set()
        for row in self.instruments:
            instrument_id = row.candidate.instrument_id
            symbol = instrument_id.rsplit(":", 1)[-1]
            if instrument_id in seen:
                raise ValueError("duplicate_premarket_runtime_instrument")
            seen.add(instrument_id)
            if symbol not in symbols and instrument_id not in symbols:
                raise ValueError(f"candidate_not_in_frozen_cohort:{instrument_id}")
            if row.v3_forecast.frozen_at > self.cohort.discovery_cutoff_at:
                raise ValueError(f"v3_forecast_after_cutoff:{instrument_id}")
        return self


class ProspectiveSessionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_version: Literal["prospective-gap-runtime-v1"] = RUNTIME_VERSION
    session_date: date
    cohort: FinvizFrozenCohort
    frozen_at: datetime
    candidates: tuple[GapperCandidate, ...]
    confirmation_strategy_config: GapPullbackConfig
    portfolio_e_policy: PortfolioEPolicy
    frozen_climatology_probability: Decimal | None = None
    run_id: str | None = None


class V42ForecastRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    forecast: V42Forecast


class V43ForecastRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    forecast: V43Forecast
    extension_overlay: V43ExtensionExhaustionOverlay


class V42ComparisonMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    n: int
    brier_delta_v42_minus_v3: Decimal | None = None
    log_loss_delta_v42_minus_v3: Decimal | None = None
    accuracy_delta_v42_minus_v3: Decimal | None = None


class V43ComparisonMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    n: int
    brier_delta_v43_minus_v3: Decimal | None = None
    log_loss_delta_v43_minus_v3: Decimal | None = None
    accuracy_delta_v43_minus_v3: Decimal | None = None


class V4ForecastRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    forecast: FrozenForecastV4
    catalyst: CatalystDecomposition
    extension_risk: ExtensionExhaustionRisk
    calibrator: CalibratorArtifact
    economic_distribution: GrossReturnDistribution | None = None


class LegacyPortfolioBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    portfolios: tuple[FrozenPortfolio, FrozenPortfolio, FrozenPortfolio, FrozenPortfolio]


class LegacyPortfolioScoreBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scores: tuple[PortfolioScore, PortfolioScore, PortfolioScore, PortfolioScore]


class PremarketInstrumentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    market_state: OperationalPremarketState
    v3_forecast: FrozenForecast
    v4_forecast: FrozenForecastV4 | None = None
    v4_failure_reason: str | None = None
    v42_forecast: V42Forecast | None = None
    v42_failure_reason: str | None = None
    v43_forecast: V43Forecast | None = None
    v43_failure_reason: str | None = None


class PremarketFreezeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    cohort_id: str
    results: tuple[PremarketInstrumentResult, ...]
    legacy_portfolios: LegacyPortfolioBundle


class ConfirmationRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    evaluated_at: datetime
    evaluated_count: int
    new_confirmation_receipt_count: int
    new_authorization_count: int
    terminal_count: int
    portfolio_e: CashPreservingShadowPortfolio


class PortfolioEPositionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    allocation: Decimal
    reference_entry_price: Decimal
    close_price: Decimal
    raw_return: Decimal
    cost_adjusted_return: Decimal
    pnl: Decimal


class PortfolioEPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_version: str
    starting_equity: Decimal
    ending_equity: Decimal
    pnl: Decimal
    return_pct: Decimal
    cash: Decimal
    position_outcomes: tuple[PortfolioEPositionOutcome, ...] = ()


class DailyProspectiveScorecard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_version: Literal["prospective-gap-runtime-v1"] = RUNTIME_VERSION
    session_date: date
    cohort_id: str
    complete_evidence_count: int = Field(ge=0)
    degraded_evidence_count: int = Field(ge=0)
    insufficient_evidence_count: int = Field(ge=0)
    unresolved_premarket_bar_count: int = Field(ge=0)
    v3_metrics: BinaryForecastMetrics
    v4_metrics: BinaryForecastMetrics
    paired_metrics: PairedForecastMetrics
    v42_metrics: BinaryForecastMetrics = Field(
        default_factory=lambda: BinaryForecastMetrics(n=0)
    )
    v42_comparison: V42ComparisonMetrics = Field(
        default_factory=lambda: V42ComparisonMetrics(n=0)
    )
    v42_return_metrics: V42ReturnMetrics = Field(
        default_factory=lambda: V42ReturnMetrics(n=0)
    )
    v43_metrics: BinaryForecastMetrics = Field(
        default_factory=lambda: BinaryForecastMetrics(n=0)
    )
    v43_comparison: V43ComparisonMetrics = Field(
        default_factory=lambda: V43ComparisonMetrics(n=0)
    )
    legacy_portfolio_scores: LegacyPortfolioScoreBundle | None = None
    confirmation_receipt_count: int = Field(ge=0)
    confirmed_long_count: int = Field(ge=0)
    authorization_long_count: int = Field(ge=0)
    authorization_no_trade_count: int = Field(ge=0)
    portfolio_e_performance: PortfolioEPerformance | None = None
    portfolio_f_performance: PortfolioEPerformance | None = None
    portfolio_g_performance: PortfolioEPerformance | None = None


class PostcloseRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    outcomes: tuple[FormalOutcomeBundle, ...]
    scorecard: DailyProspectiveScorecard


class ProspectiveGapRuntime:
    def __init__(
        self,
        *,
        repository: ProspectiveGapRepository | None = None,
        market_service: TradingMarketDataService | None = None,
        now_factory: Callable[[], datetime] | None = None,
        scheduler_handoff_fetcher: Callable[[date], SchedulerPremarketHandoff | None] | None = None,
        climatology_loader: Callable[[date], ProspectiveClimatologyState | None] | None = None,
    ) -> None:
        self.repository = repository or default_prospective_gap_repository()
        self.market_service = market_service or default_market_data_service()
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        # Decision inputs come from PostgreSQL, imported with provenance by
        # app.apps.trading.prospective_gap_inputs (WP-8.3).
        if scheduler_handoff_fetcher is None or climatology_loader is None:
            from .prospective_gap_inputs import ProspectiveGapInputs

            inputs = ProspectiveGapInputs()
            scheduler_handoff_fetcher = scheduler_handoff_fetcher or inputs.handoff
            climatology_loader = climatology_loader or inputs.climatology_before
        self.scheduler_handoff_fetcher = scheduler_handoff_fetcher
        self.climatology_loader = climatology_loader

    def _scheduler_candidate(
        self,
        *,
        handoff: SchedulerPremarketHandoff,
        row: SchedulerPremarketInstrumentInput,
        knowledge_cutoff: datetime,
    ) -> tuple[GapperCandidate, Decimal | None, Decimal | None]:
        instrument_id = f"equity:US:{row.symbol.upper()}"
        premarket_start = datetime.combine(
            handoff.session_date,
            time(4, 0),
            tzinfo=_ET,
        ).astimezone(timezone.utc)
        recovered = self.market_service.recovered_window_bars(
            instrument_id,
            start=premarket_start,
            end=knowledge_cutoff,
            interval="1m",
            session="extended_pre",
            provider="yahoo",
            include_extended_hours=True,
            knowledge_mode="live",
            knowledge_cutoff=knowledge_cutoff,
        )
        bars = tuple(recovered.bars)
        if not bars:
            raise ValueError(f"scheduler_runtime_premarket_tape_unavailable:{row.symbol}")

        daily_response = self.market_service.bars(instrument_id, "1d", 10, None)
        daily = sorted(
            (
                bar
                for bar in tuple(getattr(daily_response, "bars", ()) or ())
                if bar.start_time.astimezone(_ET).date() < handoff.session_date
            ),
            key=lambda bar: bar.start_time,
        )
        if not daily:
            raise ValueError(f"scheduler_runtime_prior_close_unavailable:{row.symbol}")

        previous_close = daily[-1].close
        latest = max(bars, key=lambda bar: bar.end_time)
        premarket_price = latest.close
        volume = sum((max(Decimal("0"), bar.volume) for bar in bars), Decimal("0"))
        dollar_volume = sum(
            (
                ((bar.high + bar.low + bar.close) / Decimal("3"))
                * max(Decimal("0"), bar.volume)
                for bar in bars
            ),
            Decimal("0"),
        )
        gap_pct = (
            premarket_price / previous_close - Decimal("1")
        ) * Decimal("100")
        prior_1d = (
            (daily[-1].close / daily[-2].close - Decimal("1")) * Decimal("100")
            if len(daily) >= 2 and daily[-2].close > 0
            else None
        )
        prior_3d = (
            (daily[-1].close / daily[-4].close - Decimal("1")) * Decimal("100")
            if len(daily) >= 4 and daily[-4].close > 0
            else None
        )
        daily_received = getattr(
            getattr(daily_response, "provenance", None),
            "received_at",
            None,
        )
        known_times = [
            handoff.research_frozen_at,
            *(bar.received_at for bar in bars),
        ]
        if isinstance(daily_received, datetime):
            known_times.append(_utc(daily_received))
        candidate_observed_at = max(_utc(value) for value in known_times)
        if candidate_observed_at > knowledge_cutoff:
            raise ValueError(
                f"scheduler_runtime_evidence_after_prediction_cutoff:{row.symbol}"
            )
        candidate = GapperCandidate(
            instrument_id=instrument_id,
            observed_at=candidate_observed_at,
            evidence_observed_at={
                "finviz_top_gainers": handoff.discovered_at,
                "scheduler_research": handoff.research_frozen_at,
                "runtime_premarket_tape": max(bar.received_at for bar in bars),
            },
            previous_close=previous_close,
            premarket_price=premarket_price,
            gap_pct=gap_pct,
            premarket_volume=volume,
            premarket_dollar_volume=dollar_volume,
            premarket_bar_count=len(bars),
            tod_rvol=row.tod_rvol,
            market_cap=row.market_cap,
            float_shares=row.float_shares,
            catalyst_evidence_ids=row.catalyst.source_evidence_ids,
            dilution_flags=row.dilution_flags,
            discovery_rank=row.discovery_rank,
        )
        return candidate, prior_1d, prior_3d

    def freeze_scheduler_handoff(
        self,
        handoff: SchedulerPremarketHandoff,
        *,
        observed_at: datetime,
        climatology_state: ProspectiveClimatologyState | None = None,
    ) -> PremarketFreezeResult:
        ingestion_started_at = _utc(observed_at)
        if ingestion_started_at > handoff.prediction_cutoff_at:
            raise ValueError("scheduler_handoff_ingested_after_prediction_cutoff")

        cohort = FinvizFrozenCohort(
            cohort_id=handoff.cohort_id,
            session_date=handoff.session_date,
            discovery_cutoff_at=handoff.prediction_cutoff_at,
            frozen_at=handoff.discovered_at,
            symbols=tuple(row.symbol.upper() for row in handoff.instruments),
        )
        baseline_n = handoff.baseline_observation_count
        baseline_pos = handoff.baseline_positive_count
        if climatology_state is not None:
            if climatology_state.through_session >= handoff.session_date:
                raise ValueError("climatology_state_must_precede_handoff_session")
            if climatology_state.through_session > handoff.baseline_through_session:
                baseline_n = climatology_state.observation_count
                baseline_pos = climatology_state.positive_count
            elif climatology_state.through_session == handoff.baseline_through_session:
                if (
                    climatology_state.observation_count != baseline_n
                    or climatology_state.positive_count != baseline_pos
                ):
                    raise ValueError("scheduler_climatology_conflicts_with_state")
            # If the local state is older than the handoff checkpoint, the
            # handoff wins. This is required when the local checkout has not
            # yet synced the scheduler's newer GitHub state file.
        baseline_probability = (
            Decimal(baseline_pos) / Decimal(baseline_n)
            if baseline_n
            else None
        )

        calibrator = CalibratorArtifact(
            calibrator_id="prospective-gap-v4-identity-runtime-v1",
            method="identity",
            training_cutoff_at=datetime.combine(
                handoff.session_date - timedelta(days=1),
                time(23, 59, 59),
                tzinfo=_ET,
            ).astimezone(timezone.utc),
            training_population_fingerprint=_hash("prospective-gap-v4-identity-population"),
            training_dataset_fingerprint=_hash("prospective-gap-v4-identity-dataset"),
            sample_count=0,
            population_definition="identity calibrator; no fitted population",
            created_at=handoff.research_frozen_at,
            code_version="prospective-gap-runtime-v1",
        )

        inputs: list[PremarketInstrumentInput] = []
        v4_overrides: dict[str, V4ForecastRecord] = {}
        workers = min(4, len(handoff.instruments))
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, workers),
            thread_name_prefix="prospective-gap-premarket",
        ) as pool:
            futures = {
                row.discovery_rank: pool.submit(
                    self._scheduler_candidate,
                    handoff=handoff,
                    row=row,
                    knowledge_cutoff=handoff.prediction_cutoff_at,
                )
                for row in handoff.instruments
            }
            recovered_by_rank = {
                rank: future.result()
                for rank, future in futures.items()
            }

        for row in handoff.instruments:
            candidate, prior_1d, prior_3d = recovered_by_rank[row.discovery_rank]
            snapshot_id = (
                f"{handoff.cohort_id}:{candidate.instrument_id}:scheduler-research"
            )
            v3 = FrozenForecast(
                instrument_id=candidate.instrument_id,
                evidence_snapshot_id=snapshot_id,
                feature_vector_fingerprint=_hash(
                    {
                        "v3_p_close_above_open": row.v3_p_close_above_open,
                        "v3_p_persistent_uptrend": row.v3_p_persistent_uptrend,
                        "catalyst": row.catalyst.model_dump(mode="json"),
                        "mechanisms": row.mechanisms.model_dump(mode="json"),
                        "regime_tags": row.regime_tags,
                    }
                ),
                frozen_at=handoff.research_frozen_at,
                p_close_above_open=row.v3_p_close_above_open,
                p_persistent_uptrend=row.v3_p_persistent_uptrend,
                uncertainty=row.uncertainty,
            )
            inputs.append(
                PremarketInstrumentInput(
                    candidate=candidate,
                    v3_forecast=v3,
                    catalyst=row.catalyst,
                    mechanisms=row.mechanisms,
                    calibrator=calibrator,
                    evidence_snapshot_id=snapshot_id,
                    first_catalyst_at=row.first_catalyst_at,
                    prior_1d_return_pct=prior_1d,
                    prior_3d_return_pct=prior_3d,
                    regime_tags=row.regime_tags,
                    regime_primary=row.regime_primary,
                    regime_confidence=row.regime_confidence,
                    uncertainty=row.uncertainty,
                )
            )
            scheduler_extension = ExtensionExhaustionRisk(
                score=row.v4_extension_risk_score,
                used_components=("scheduler_frozen_v4_extension_risk",),
                missing_components=(),
            )
            scheduler_quality = PredictionEvidenceQuality(
                quality=row.v4_evidence_quality,
                critical_features=(),
                reasons=("SCHEDULER_FROZEN_V4_EVIDENCE",),
            )
            scheduler_v4 = FrozenForecastV4(
                instrument_id=candidate.instrument_id,
                session_date=handoff.session_date,
                cohort_id=cohort.cohort_id,
                cohort_fingerprint=cohort.cohort_fingerprint,
                evidence_snapshot_id=snapshot_id,
                market_state_snapshot_id=f"{snapshot_id}:v4",
                model_spec_fingerprint=DEFAULT_V4_MODEL_SPEC.implementation_fingerprint,
                feature_vector_fingerprint=_hash(
                    {
                        "v4_raw_p_close_above_open": row.v4_raw_p_close_above_open,
                        "v4_calibrated_p_close_above_open": row.v4_calibrated_p_close_above_open,
                        "v4_extension_risk_score": row.v4_extension_risk_score,
                        "v4_evidence_quality": row.v4_evidence_quality,
                        "catalyst": row.catalyst.model_dump(mode="json"),
                        "mechanisms": row.mechanisms.model_dump(mode="json"),
                        "regime_tags": row.regime_tags,
                    }
                ),
                frozen_at=handoff.research_frozen_at,
                raw_p_close_above_open=row.v4_raw_p_close_above_open,
                calibrated_p_close_above_open=row.v4_calibrated_p_close_above_open,
                uncertainty=row.uncertainty,
                evidence_quality=scheduler_quality,
                mechanism_scores=row.mechanisms,
                regime_tags=row.regime_tags,
                regime_primary=row.regime_primary,
                regime_confidence=row.regime_confidence,
                calibrator_id=calibrator.calibrator_id,
                calibrator_fingerprint=calibrator.calibrator_fingerprint,
            )
            v4_overrides[candidate.instrument_id] = V4ForecastRecord(
                forecast=scheduler_v4,
                catalyst=row.catalyst,
                extension_risk=scheduler_extension,
                calibrator=calibrator,
            )

        completed_at = _utc(self.now_factory())
        if completed_at < ingestion_started_at:
            raise ValueError("scheduler_handoff_completion_precedes_ingestion")
        if completed_at > handoff.prediction_cutoff_at:
            raise ValueError("scheduler_handoff_completed_after_prediction_cutoff")
        latest_input_observed_at = max(
            row.candidate.observed_at or handoff.research_frozen_at
            for row in inputs
        )
        if latest_input_observed_at > completed_at:
            raise ValueError("scheduler_runtime_evidence_after_freeze")
        request = PremarketFreezeRequest(
            cohort=cohort,
            frozen_at=completed_at,
            instruments=tuple(inputs),
            frozen_climatology_probability=baseline_probability,
            run_id=handoff.run_id,
        )
        return self.freeze_premarket(request, v4_overrides=v4_overrides)

    def freeze_premarket_file(
        self,
        path: str | Path,
        *,
        observed_at: datetime | None = None,
    ) -> PremarketFreezeResult:
        """Ingest either the full runtime request or the safe scheduler handoff (an operator action)."""

        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload.get("handoff_version") == "prospective-gap-scheduler-handoff-v1":
            handoff = SchedulerPremarketHandoff.model_validate(payload)
            state = self.climatology_loader(handoff.session_date)
            return self.freeze_scheduler_handoff(
                handoff,
                observed_at=observed_at or self.now_factory(),
                climatology_state=state,
            )
        request = PremarketFreezeRequest.model_validate(payload)
        return self.freeze_premarket(request)

    def try_freeze_scheduler_inbox(
        self,
        session_date: date,
        *,
        observed_at: datetime | None = None,
    ) -> PremarketFreezeResult | None:
        """Freeze today's imported scheduler handoff exactly once."""

        ledger = self.repository.session(session_date)
        if ledger.latest(kind="session_manifest", instrument_id="__session__") is not None:
            return None
        handoff = self.scheduler_handoff_fetcher(session_date)
        if handoff is None:
            return None
        if handoff.session_date != session_date:
            raise ValueError("scheduler_handoff_session_date_mismatch")
        result = self.freeze_scheduler_handoff(
            handoff,
            observed_at=observed_at or self.now_factory(),
            climatology_state=self.climatology_loader(session_date),
        )
        if result.session_date != session_date:
            raise ValueError("scheduler_handoff_session_date_mismatch")
        return result

    def freeze_premarket(
        self,
        request: PremarketFreezeRequest,
        *,
        v4_overrides: dict[str, V4ForecastRecord] | None = None,
    ) -> PremarketFreezeResult:
        session_date = request.cohort.session_date
        manifest = ProspectiveSessionManifest(
            session_date=session_date,
            cohort=request.cohort,
            frozen_at=request.frozen_at,
            candidates=tuple(row.candidate for row in request.instruments),
            confirmation_strategy_config=request.confirmation_strategy_config,
            portfolio_e_policy=request.portfolio_e_policy,
            frozen_climatology_probability=request.frozen_climatology_probability,
            run_id=request.run_id,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=request.cohort.cohort_id,
            instrument_id="__session__",
            kind="session_manifest",
            observed_at=request.frozen_at,
            payload=manifest,
            run_id=request.run_id,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=request.cohort.cohort_id,
            instrument_id="__research_spec__",
            kind="v41_shadow_spec",
            observed_at=request.frozen_at,
            payload=DEFAULT_V41_SPEC,
            state=DEFAULT_V41_SPEC.activation_state,
            run_id=request.run_id,
            idempotency_suffix=DEFAULT_V41_SPEC.implementation_fingerprint,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=request.cohort.cohort_id,
            instrument_id="__research_spec_v42__",
            kind="v42_shadow_spec",
            observed_at=request.frozen_at,
            payload=DEFAULT_V42_SPEC,
            state=DEFAULT_V42_SPEC.activation_state,
            run_id=request.run_id,
            idempotency_suffix=DEFAULT_V42_SPEC.implementation_fingerprint,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=request.cohort.cohort_id,
            instrument_id="__action_spec_v42__",
            kind="v42_action_spec",
            observed_at=request.frozen_at,
            payload=DEFAULT_V42_ACTION_POLICY,
            state="FORWARD_SHADOW_ACTIVE",
            run_id=request.run_id,
            idempotency_suffix=_hash(
                DEFAULT_V42_ACTION_POLICY.model_dump(mode="json")
            ),
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=request.cohort.cohort_id,
            instrument_id="__research_spec_v43__",
            kind="v43_shadow_spec",
            observed_at=request.frozen_at,
            payload=DEFAULT_V43_SPEC,
            state=DEFAULT_V43_SPEC.activation_state,
            run_id=request.run_id,
            idempotency_suffix=DEFAULT_V43_SPEC.implementation_fingerprint,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=request.cohort.cohort_id,
            instrument_id="__action_spec_v43__",
            kind="v43_action_spec",
            observed_at=request.frozen_at,
            payload=DEFAULT_V43_ACTION_POLICY,
            state="FORWARD_SHADOW_ACTIVE",
            run_id=request.run_id,
            idempotency_suffix=_hash(
                DEFAULT_V43_ACTION_POLICY.model_dump(mode="json")
            ),
        )

        results: list[PremarketInstrumentResult] = []
        v3_forecasts: list[FrozenForecast] = []
        v43_inputs: list[
            tuple[int, GapperCandidate, OperationalPremarketState, V42Forecast]
        ] = []
        for row in request.instruments:
            candidate = row.candidate
            self.repository.append(
                session_date=session_date,
                cohort_id=request.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="premarket_input",
                observed_at=request.frozen_at,
                payload=row,
                run_id=request.run_id,
            )
            if row.evidence_snapshot is not None:
                self.repository.append(
                    session_date=session_date,
                    cohort_id=request.cohort.cohort_id,
                    instrument_id=candidate.instrument_id,
                    kind="premarket_evidence",
                    observed_at=row.evidence_snapshot.frozen_at,
                    payload=row.evidence_snapshot,
                    run_id=request.run_id,
                )

            state = load_operational_premarket_state(
                market_service=self.market_service,
                cohort=request.cohort,
                candidate=candidate,
                snapshot_id=f"{request.cohort.cohort_id}:{candidate.instrument_id}:market-state",
                prediction_cutoff_at=request.cohort.discovery_cutoff_at,
                frozen_at=request.frozen_at,
                first_catalyst_at=row.first_catalyst_at,
                prior_1d_return_pct=row.prior_1d_return_pct,
                prior_3d_return_pct=row.prior_3d_return_pct,
            )
            self.repository.append(
                session_date=session_date,
                cohort_id=request.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="premarket_state",
                observed_at=request.frozen_at,
                payload=state,
                run_id=request.run_id,
            )
            self.repository.append(
                session_date=session_date,
                cohort_id=request.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="v3_forecast",
                observed_at=row.v3_forecast.frozen_at,
                payload=row.v3_forecast,
                run_id=request.run_id,
            )
            v3_forecasts.append(row.v3_forecast)

            v4: FrozenForecastV4 | None = None
            failure: str | None = None
            extension: ExtensionExhaustionRisk | None = None
            override = (v4_overrides or {}).get(candidate.instrument_id)
            try:
                if override is not None:
                    v4 = override.forecast
                    extension = override.extension_risk
                    distribution = override.economic_distribution
                    self.repository.append(
                        session_date=session_date,
                        cohort_id=request.cohort.cohort_id,
                        instrument_id=candidate.instrument_id,
                        kind="v4_forecast",
                        observed_at=v4.frozen_at,
                        payload=override,
                        run_id=request.run_id,
                    )
                    attempt = V4ForecastAttempt(
                        instrument_id=candidate.instrument_id,
                        session_date=session_date,
                        attempted_at=v4.frozen_at,
                        model_state="PRODUCED",
                        evidence_quality=v4.evidence_quality.quality,
                        forecast_fingerprint=v4.immutable_fingerprint,
                    )
                else:
                    extension = derive_extension_exhaustion_risk(
                        extension_components_from_market_state(state.market_state)
                    )
                    feature_fingerprint = _hash(
                        {
                            "market_state": state.market_state.live_feature_fingerprint,
                            "catalyst": row.catalyst.model_dump(mode="json"),
                            "mechanisms": row.mechanisms.model_dump(mode="json"),
                            "extension": extension.model_dump(mode="json"),
                        }
                    )
                    distribution = row.economic_distribution
                    v4 = freeze_v4_forecast(
                        instrument_id=candidate.instrument_id,
                        session_date=session_date,
                        cohort=request.cohort,
                        evidence_snapshot_id=row.evidence_snapshot_id,
                        market_state=state.market_state,
                        evidence_quality=state.evidence_quality,
                        catalyst=row.catalyst,
                        extension_risk=extension,
                        mechanisms=row.mechanisms,
                        calibrator=row.calibrator,
                        feature_vector_fingerprint=feature_fingerprint,
                        frozen_at=request.frozen_at,
                        regime_tags=row.regime_tags,
                        regime_primary=row.regime_primary,
                        regime_confidence=row.regime_confidence,
                        uncertainty=row.uncertainty,
                        return_q10=distribution.q10 if distribution is not None else None,
                        return_q50=distribution.q50 if distribution is not None else None,
                        return_q90=distribution.q90 if distribution is not None else None,
                        p_return_gt_2pct=distribution.p_return_gt_2pct if distribution is not None else None,
                        p_return_lt_minus_5pct=distribution.p_return_lt_minus_5pct if distribution is not None else None,
                    )
                    self.repository.append(
                        session_date=session_date,
                        cohort_id=request.cohort.cohort_id,
                        instrument_id=candidate.instrument_id,
                        kind="v4_forecast",
                        observed_at=request.frozen_at,
                        payload=V4ForecastRecord(
                            forecast=v4,
                            catalyst=row.catalyst,
                            extension_risk=extension,
                            calibrator=row.calibrator,
                            economic_distribution=distribution,
                        ),
                        run_id=request.run_id,
                    )
                    attempt = V4ForecastAttempt(
                        instrument_id=candidate.instrument_id,
                        session_date=session_date,
                        attempted_at=request.frozen_at,
                        model_state="PRODUCED",
                        evidence_quality=state.evidence_quality.quality,
                        forecast_fingerprint=v4.immutable_fingerprint,
                    )
            except Exception as exc:
                failure = f"{type(exc).__name__}:{exc}"
                if state.evidence_quality.quality == "INSUFFICIENT":
                    attempt = V4ForecastAttempt(
                        instrument_id=candidate.instrument_id,
                        session_date=session_date,
                        attempted_at=request.frozen_at,
                        model_state="NOT_APPLICABLE",
                        evidence_quality="INSUFFICIENT",
                    )
                else:
                    attempt = V4ForecastAttempt(
                        instrument_id=candidate.instrument_id,
                        session_date=session_date,
                        attempted_at=request.frozen_at,
                        model_state="FAILED",
                        evidence_quality=state.evidence_quality.quality,
                        failure_reason=failure,
                    )

            self.repository.append(
                session_date=session_date,
                cohort_id=request.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="v4_attempt",
                observed_at=attempt.attempted_at,
                payload=attempt,
                state=attempt.model_state,
                reason_code=attempt.failure_reason,
                run_id=request.run_id,
            )

            v42: V42Forecast | None = None
            v42_failure: str | None = None
            v42_extension: ExtensionExhaustionRisk | None = None
            try:
                v42_extension = derive_extension_exhaustion_risk(
                    extension_components_from_market_state(state.market_state)
                )
            except Exception:
                v42_extension = None
            if not session_eligible_for_v42_forward_validation(session_date):
                v42_attempt = V42ForecastAttempt(
                    instrument_id=candidate.instrument_id,
                    session_date=session_date,
                    attempted_at=request.frozen_at,
                    model_state="NOT_APPLICABLE",
                    failure_reason="V42_SESSION_NOT_FORWARD_ELIGIBLE",
                )
            elif (
                state.source_mode != "CANONICAL_RAW_1M"
                or state.coverage_ratio is None
                or state.coverage_ratio < DEFAULT_V42_SPEC.minimum_premarket_coverage
                or state.unresolved_gap_count > 0
                or state.raw_bar_count < DEFAULT_V42_SPEC.minimum_total_premarket_bars
                or state.late_window_bar_count < DEFAULT_V42_SPEC.minimum_late_window_bars
                or state.latest_bar_lag_seconds is None
                or state.latest_bar_lag_seconds > DEFAULT_V42_SPEC.maximum_latest_bar_lag_seconds
            ):
                v42_failure = "V42_COMPLETE_PREMARKET_TAPE_REQUIRED"
                v42_attempt = V42ForecastAttempt(
                    instrument_id=candidate.instrument_id,
                    session_date=session_date,
                    attempted_at=request.frozen_at,
                    model_state="NOT_APPLICABLE",
                    failure_reason=v42_failure,
                )
            elif v42_extension is None:
                v42_failure = "V42_EXTENSION_RISK_UNAVAILABLE"
                v42_attempt = V42ForecastAttempt(
                    instrument_id=candidate.instrument_id,
                    session_date=session_date,
                    attempted_at=request.frozen_at,
                    model_state="FAILED",
                    failure_reason=v42_failure,
                )
            else:
                try:
                    v42 = freeze_v42_forecast(
                        candidate=candidate,
                        session_date=session_date,
                        cohort_id=request.cohort.cohort_id,
                        cohort_fingerprint=request.cohort.cohort_fingerprint,
                        market_state=state.market_state,
                        catalyst=row.catalyst,
                        v4_mechanisms=row.mechanisms,
                        extension_risk=v42_extension,
                        regime_tags=row.regime_tags,
                        frozen_at=request.frozen_at,
                    )
                    self.repository.append(
                        session_date=session_date,
                        cohort_id=request.cohort.cohort_id,
                        instrument_id=candidate.instrument_id,
                        kind="v42_forecast",
                        observed_at=request.frozen_at,
                        payload=V42ForecastRecord(forecast=v42),
                        state="PRODUCED",
                        run_id=request.run_id,
                    )
                    watch = classify_v42_watch(
                        v42,
                        policy=DEFAULT_V42_ACTION_POLICY,
                    )
                    self.repository.append(
                        session_date=session_date,
                        cohort_id=request.cohort.cohort_id,
                        instrument_id=candidate.instrument_id,
                        kind="v42_watch",
                        observed_at=request.frozen_at,
                        payload=watch,
                        state=watch.classification,
                        reason_code=watch.reasons[0] if watch.reasons else None,
                        run_id=request.run_id,
                        idempotency_suffix=watch.forecast_fingerprint,
                    )
                    v42_attempt = V42ForecastAttempt(
                        instrument_id=candidate.instrument_id,
                        session_date=session_date,
                        attempted_at=request.frozen_at,
                        model_state="PRODUCED",
                        forecast_fingerprint=v42.immutable_fingerprint,
                    )
                except Exception as exc:
                    v42_failure = f"{type(exc).__name__}:{exc}"
                    state_name: Literal["FAILED", "NOT_APPLICABLE"] = (
                        "NOT_APPLICABLE"
                        if "missing_complete_demand_evidence" in str(exc)
                        else "FAILED"
                    )
                    v42_attempt = V42ForecastAttempt(
                        instrument_id=candidate.instrument_id,
                        session_date=session_date,
                        attempted_at=request.frozen_at,
                        model_state=state_name,
                        failure_reason=v42_failure,
                    )
            self.repository.append(
                session_date=session_date,
                cohort_id=request.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="v42_attempt",
                observed_at=request.frozen_at,
                payload=v42_attempt,
                state=v42_attempt.model_state,
                reason_code=v42_attempt.failure_reason,
                run_id=request.run_id,
            )
            results.append(
                PremarketInstrumentResult(
                    instrument_id=candidate.instrument_id,
                    market_state=state,
                    v3_forecast=row.v3_forecast,
                    v4_forecast=v4,
                    v4_failure_reason=failure,
                    v42_forecast=v42,
                    v42_failure_reason=v42_failure,
                )
            )
            if v42 is not None:
                v43_inputs.append((len(results) - 1, candidate, state, v42))

        if session_eligible_for_v43_forward_validation(session_date):
            v43_overlay_rows: list[
                tuple[
                    int,
                    GapperCandidate,
                    OperationalPremarketState,
                    V42Forecast,
                    V43ExtensionExhaustionOverlay,
                ]
            ] = []
            v43_overlay_failures: dict[str, str] = {}
            for result_index, candidate, state, base_v42 in v43_inputs:
                try:
                    overlay = derive_v43_extension_overlay(
                        candidate=candidate,
                        market_state=state.market_state,
                        base_v42=base_v42,
                    )
                    v43_overlay_rows.append(
                        (result_index, candidate, state, base_v42, overlay)
                    )
                except Exception as exc:
                    v43_overlay_failures[candidate.instrument_id] = (
                        f"{type(exc).__name__}:{exc}"
                    )

            cohort_regime = derive_v43_cohort_regime(
                tuple(
                    (candidate, base_v42, overlay)
                    for _, candidate, _, base_v42, overlay in v43_overlay_rows
                ),
                spec=DEFAULT_V43_SPEC,
            )
            self.repository.append(
                session_date=session_date,
                cohort_id=request.cohort.cohort_id,
                instrument_id="__cohort_v43__",
                kind="v43_cohort_regime",
                observed_at=request.frozen_at,
                payload=cohort_regime,
                state=cohort_regime.classification,
                run_id=request.run_id,
                idempotency_suffix=cohort_regime.cohort_fingerprint,
            )

            prepared = {
                candidate.instrument_id: (
                    result_index,
                    candidate,
                    state,
                    base_v42,
                    overlay,
                )
                for result_index, candidate, state, base_v42, overlay in v43_overlay_rows
            }
            for result_index, result in enumerate(tuple(results)):
                instrument_id = result.instrument_id
                v43: V43Forecast | None = None
                v43_failure: str | None = None
                prepared_row = prepared.get(instrument_id)
                if prepared_row is None:
                    overlay_failure = v43_overlay_failures.get(instrument_id)
                    v43_failure = (
                        overlay_failure
                        or "V43_REQUIRES_PRODUCED_V42_FORECAST"
                    )
                    v43_attempt = V43ForecastAttempt(
                        instrument_id=instrument_id,
                        session_date=session_date,
                        attempted_at=request.frozen_at,
                        model_state=(
                            "FAILED"
                            if overlay_failure is not None
                            else "NOT_APPLICABLE"
                        ),
                        failure_reason=v43_failure,
                    )
                elif cohort_regime.classification == "INSUFFICIENT":
                    v43_failure = "V43_REQUIRES_SUFFICIENT_COHORT_REGIME"
                    v43_attempt = V43ForecastAttempt(
                        instrument_id=instrument_id,
                        session_date=session_date,
                        attempted_at=request.frozen_at,
                        model_state="NOT_APPLICABLE",
                        failure_reason=v43_failure,
                    )
                else:
                    _, candidate, _, base_v42, overlay = prepared_row
                    try:
                        v43 = freeze_v43_forecast(
                            candidate=candidate,
                            base_v42=base_v42,
                            extension_overlay=overlay,
                            cohort_regime=cohort_regime,
                            frozen_climatology_probability=(
                                request.frozen_climatology_probability
                            ),
                            frozen_at=request.frozen_at,
                            spec=DEFAULT_V43_SPEC,
                        )
                        self.repository.append(
                            session_date=session_date,
                            cohort_id=request.cohort.cohort_id,
                            instrument_id=instrument_id,
                            kind="v43_forecast",
                            observed_at=request.frozen_at,
                            payload=V43ForecastRecord(
                                forecast=v43,
                                extension_overlay=overlay,
                            ),
                            state="PRODUCED",
                            run_id=request.run_id,
                            idempotency_suffix=v43.immutable_fingerprint,
                        )
                        v43_watch = classify_v43_watch(
                            v43,
                            policy=DEFAULT_V43_ACTION_POLICY,
                        )
                        self.repository.append(
                            session_date=session_date,
                            cohort_id=request.cohort.cohort_id,
                            instrument_id=instrument_id,
                            kind="v43_watch",
                            observed_at=request.frozen_at,
                            payload=v43_watch,
                            state=v43_watch.classification,
                            reason_code=v43_watch.reasons[0] if v43_watch.reasons else None,
                            run_id=request.run_id,
                            idempotency_suffix=v43_watch.forecast_fingerprint,
                        )
                        v43_attempt = V43ForecastAttempt(
                            instrument_id=instrument_id,
                            session_date=session_date,
                            attempted_at=request.frozen_at,
                            model_state="PRODUCED",
                            forecast_fingerprint=v43.immutable_fingerprint,
                        )
                    except Exception as exc:
                        v43_failure = f"{type(exc).__name__}:{exc}"
                        v43_attempt = V43ForecastAttempt(
                            instrument_id=instrument_id,
                            session_date=session_date,
                            attempted_at=request.frozen_at,
                            model_state="FAILED",
                            failure_reason=v43_failure,
                        )
                self.repository.append(
                    session_date=session_date,
                    cohort_id=request.cohort.cohort_id,
                    instrument_id=instrument_id,
                    kind="v43_attempt",
                    observed_at=request.frozen_at,
                    payload=v43_attempt,
                    state=v43_attempt.model_state,
                    reason_code=v43_attempt.failure_reason,
                    run_id=request.run_id,
                )
                results[result_index] = result.model_copy(
                    update={
                        "v43_forecast": v43,
                        "v43_failure_reason": v43_failure,
                    }
                )
        else:
            for result_index, result in enumerate(tuple(results)):
                v43_failure = "V43_SESSION_NOT_FORWARD_ELIGIBLE"
                v43_attempt = V43ForecastAttempt(
                    instrument_id=result.instrument_id,
                    session_date=session_date,
                    attempted_at=request.frozen_at,
                    model_state="NOT_APPLICABLE",
                    failure_reason=v43_failure,
                )
                self.repository.append(
                    session_date=session_date,
                    cohort_id=request.cohort.cohort_id,
                    instrument_id=result.instrument_id,
                    kind="v43_attempt",
                    observed_at=request.frozen_at,
                    payload=v43_attempt,
                    state=v43_attempt.model_state,
                    reason_code=v43_failure,
                    run_id=request.run_id,
                )
                results[result_index] = result.model_copy(
                    update={"v43_failure_reason": v43_failure}
                )

        legacy = LegacyPortfolioBundle(
            portfolios=freeze_research_portfolios(
                v3_forecasts,
                frozen_at=request.frozen_at,
                risk_factors=request.risk_factors,
                starting_equity=request.portfolio_e_policy.starting_equity,
            )
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=request.cohort.cohort_id,
            instrument_id="__portfolio__",
            kind="legacy_portfolios",
            observed_at=request.frozen_at,
            payload=legacy,
            run_id=request.run_id,
        )
        return PremarketFreezeResult(
            session_date=session_date,
            cohort_id=request.cohort.cohort_id,
            results=tuple(results),
            legacy_portfolios=legacy,
        )

    def _manifest(self, session_date: date) -> ProspectiveSessionManifest:
        record = self.repository.session(session_date).latest(
            kind="session_manifest",
            instrument_id="__session__",
        )
        if record is None:
            raise ValueError("prospective_session_not_frozen")
        return ProspectiveSessionManifest.model_validate(record.payload)

    def _v4_record(
        self,
        ledger: ProspectiveGapSessionLedger,
        instrument_id: str,
    ) -> V4ForecastRecord | None:
        record = ledger.latest(kind="v4_forecast", instrument_id=instrument_id)
        return V4ForecastRecord.model_validate(record.payload) if record is not None else None

    def _v42_record(
        self,
        ledger: ProspectiveGapSessionLedger,
        instrument_id: str,
    ) -> V42ForecastRecord | None:
        record = ledger.latest(kind="v42_forecast", instrument_id=instrument_id)
        return V42ForecastRecord.model_validate(record.payload) if record is not None else None

    def _v42_watch(
        self,
        ledger: ProspectiveGapSessionLedger,
        instrument_id: str,
    ) -> V42WatchDecision | None:
        record = ledger.latest(kind="v42_watch", instrument_id=instrument_id)
        return V42WatchDecision.model_validate(record.payload) if record is not None else None

    def _v43_record(
        self,
        ledger: ProspectiveGapSessionLedger,
        instrument_id: str,
    ) -> V43ForecastRecord | None:
        record = ledger.latest(kind="v43_forecast", instrument_id=instrument_id)
        return V43ForecastRecord.model_validate(record.payload) if record is not None else None

    def _v43_watch(
        self,
        ledger: ProspectiveGapSessionLedger,
        instrument_id: str,
    ) -> V43WatchDecision | None:
        record = ledger.latest(kind="v43_watch", instrument_id=instrument_id)
        return V43WatchDecision.model_validate(record.payload) if record is not None else None

    def _latest_confirmation_state(
        self,
        ledger: ProspectiveGapSessionLedger,
        instrument_id: str,
    ) -> ConfirmationState:
        record = ledger.latest(kind="confirmation", instrument_id=instrument_id)
        if record is None:
            return "WAIT_OPEN"
        return ConfirmationTransitionReceipt.model_validate(record.payload).new_state

    def _latest_authorization(
        self,
        ledger: ProspectiveGapSessionLedger,
        instrument_id: str,
    ) -> TradeAuthorizationReceipt | None:
        record = ledger.latest(kind="authorization", instrument_id=instrument_id)
        return (
            TradeAuthorizationReceipt.model_validate(record.payload)
            if record is not None
            else None
        )

    def _latest_confirmation(
        self,
        ledger: ProspectiveGapSessionLedger,
        instrument_id: str,
    ) -> ConfirmationTransitionReceipt | None:
        record = ledger.latest(kind="confirmation", instrument_id=instrument_id)
        return (
            ConfirmationTransitionReceipt.model_validate(record.payload)
            if record is not None
            else None
        )

    def _execution_cost(
        self,
        *,
        candidate: GapperCandidate,
        policy: PortfolioEPolicy | V42ActionPolicy,
        decision_at: datetime,
    ) -> ExecutionCostInput | None:
        try:
            observation = self.market_service.execution_observation(candidate.instrument_id)
        except Exception:
            logger.debug("suppressed error in %s", "ProspectiveGapRuntime._execution_cost", exc_info=True)
            return None
        if (
            not observation.paper_fill_eligible
            or observation.bid is None
            or observation.ask is None
            or observation.midpoint is None
        ):
            return None
        return ExecutionCostInput(
            symbol=candidate.instrument_id.rsplit(":", 1)[-1],
            decision_at=decision_at,
            notional=policy.starting_equity * policy.max_position_fraction,
            reference_price=observation.midpoint,
            observed_bid=observation.bid,
            observed_ask=observation.ask,
            estimated_slippage_bps=policy.estimated_entry_slippage_bps,
            estimated_impact_bps=policy.estimated_entry_impact_bps,
            expected_exit_slippage_bps=policy.estimated_exit_slippage_bps,
            expected_exit_impact_bps=policy.estimated_exit_impact_bps,
            estimated_round_trip_commission_bps=policy.estimated_round_trip_commission_bps,
        )

    def run_confirmation(
        self,
        *,
        session_date: date,
        evaluated_at: datetime,
    ) -> ConfirmationRunResult:
        evaluated_at = _utc(evaluated_at)
        manifest = self._manifest(session_date)
        ledger = self.repository.session(session_date)
        new_receipts = 0
        new_authorizations = 0
        terminal = 0

        for candidate in manifest.candidates:
            v4_record = self._v4_record(ledger, candidate.instrument_id)
            previous = self._latest_confirmation_state(ledger, candidate.instrument_id)
            if previous == "CONFIRMED_LONG":
                terminal += 1
                if (
                    v4_record is not None
                    and self._latest_authorization(ledger, candidate.instrument_id) is None
                ):
                    confirmation = self._latest_confirmation(ledger, candidate.instrument_id)
                    if confirmation is not None:
                        actionability = ActionabilityDecision(
                            instrument_id=candidate.instrument_id,
                            decision_at=confirmation.transition_at,
                            actionability="ABSTAIN",
                            reasons=("AUTHORIZATION_WINDOW_MISSED_AFTER_CONFIRMATION",),
                        )
                        evidence_fingerprint = _hash(
                            {
                                "confirmation": confirmation.model_dump(mode="json"),
                                "recovery": "fail_closed_no_late_market_evidence",
                            }
                        )
                        authorization = authorize_trade(
                            forecast=v4_record.forecast,
                            confirmation=confirmation,
                            actionability=actionability,
                            gross=None,
                            cost=None,
                            evidence_fingerprint=evidence_fingerprint,
                            max_positive_alpha_notional=Decimal("0"),
                            minimum_net_expected_return=manifest.portfolio_e_policy.minimum_net_expected_return,
                            minimum_net_q10=manifest.portfolio_e_policy.minimum_net_q10,
                        )
                        inserted = self.repository.append(
                            session_date=session_date,
                            cohort_id=manifest.cohort.cohort_id,
                            instrument_id=candidate.instrument_id,
                            kind="authorization",
                            observed_at=authorization.decision_at,
                            payload=authorization,
                            state=authorization.decision,
                            reason_code=authorization.reasons[0] if authorization.reasons else None,
                            run_id=manifest.run_id,
                            idempotency_suffix=authorization.confirmation_receipt_fingerprint,
                        )
                        new_authorizations += int(inserted)
                continue
            if previous in {"INVALIDATED", "EXPIRED"}:
                terminal += 1
                continue

            bars: Sequence[Any] = ()
            data_quality_ok = False
            data_quality_reason: str | None = None
            recovered = getattr(self.market_service, "recovered_bars", None)
            try:
                if callable(recovered):
                    recovered_result = recovered(
                        candidate.instrument_id,
                        "1m",
                        500,
                        candidate.binding_id,
                        session_date=session_date,
                        as_of=evaluated_at,
                        knowledge_mode="live",
                        knowledge_cutoff=evaluated_at,
                    )
                    bars = recovered_result.bars
                    data_quality_ok = not recovered_result.report.unresolved_gaps
                    if not data_quality_ok:
                        data_quality_reason = "CURRENT_SESSION_DEPENDENCY_GAP"
                else:
                    response = self.market_service.bars(
                        candidate.instrument_id,
                        "1m",
                        500,
                        candidate.binding_id,
                    )
                    bars = tuple(getattr(response, "bars", ()) or ())
                    data_quality_ok = bool(bars)
                    if not data_quality_ok:
                        data_quality_reason = "CURRENT_TAPE_UNAVAILABLE"
            except Exception as exc:
                data_quality_reason = f"CURRENT_TAPE_FETCH_FAILED:{type(exc).__name__}"

            evaluation = evaluate_operational_confirmation(
                candidate=candidate,
                bars=bars,
                observed_at=evaluated_at,
                previous_state=previous,
                config=manifest.confirmation_strategy_config,
                data_quality_ok=data_quality_ok,
                data_quality_reason=data_quality_reason,
            )
            for transition_receipt in evaluation.receipts:
                inserted = self.repository.append(
                    session_date=session_date,
                    cohort_id=manifest.cohort.cohort_id,
                    instrument_id=candidate.instrument_id,
                    kind="confirmation",
                    observed_at=transition_receipt.transition_at,
                    payload=transition_receipt,
                    state=transition_receipt.new_state,
                    reason_code=transition_receipt.trigger,
                    run_id=manifest.run_id,
                    idempotency_suffix=_hash(transition_receipt.model_dump(mode="json")),
                )
                new_receipts += int(inserted)

            if evaluation.final_confirmation_state != "CONFIRMED_LONG" or not evaluation.receipts:
                continue
            confirmation = evaluation.receipts[-1]
            if confirmation.new_state != "CONFIRMED_LONG":
                continue
            if v4_record is None:
                continue

            actionability = ActionabilityDecision(
                instrument_id=candidate.instrument_id,
                decision_at=evaluated_at,
                actionability=(
                    "ACT"
                    if v4_record.forecast.calibrated_p_close_above_open
                    > manifest.portfolio_e_policy.minimum_bullish_probability
                    else "ABSTAIN"
                ),
                reasons=(
                    ()
                    if v4_record.forecast.calibrated_p_close_above_open
                    > manifest.portfolio_e_policy.minimum_bullish_probability
                    else ("PREMARKET_FORECAST_NOT_BULLISH",)
                ),
            )
            cost = self._execution_cost(
                candidate=candidate,
                policy=manifest.portfolio_e_policy,
                decision_at=evaluated_at,
            )
            evidence_fingerprint = _hash(
                {
                    "confirmation": confirmation.model_dump(mode="json"),
                    "cost": cost.model_dump(mode="json") if cost is not None else None,
                }
            )
            authorization = authorize_trade(
                forecast=v4_record.forecast,
                confirmation=confirmation,
                actionability=actionability,
                gross=v4_record.economic_distribution,
                cost=cost,
                evidence_fingerprint=evidence_fingerprint,
                max_positive_alpha_notional=(
                    manifest.portfolio_e_policy.starting_equity
                    * manifest.portfolio_e_policy.max_position_fraction
                ),
                minimum_net_expected_return=manifest.portfolio_e_policy.minimum_net_expected_return,
                minimum_net_q10=manifest.portfolio_e_policy.minimum_net_q10,
            )
            inserted = self.repository.append(
                session_date=session_date,
                cohort_id=manifest.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="authorization",
                observed_at=authorization.decision_at,
                payload=authorization,
                state=authorization.decision,
                reason_code=authorization.reasons[0] if authorization.reasons else None,
                run_id=manifest.run_id,
                idempotency_suffix=authorization.confirmation_receipt_fingerprint,
            )
            new_authorizations += int(inserted)

        refreshed = self.repository.session(session_date)
        authorization_by_instrument: dict[str, TradeAuthorizationReceipt] = {}
        for record in refreshed.records_of_kind("authorization"):
            receipt = TradeAuthorizationReceipt.model_validate(record.payload)
            prior = authorization_by_instrument.get(receipt.instrument_id)
            if prior is None or receipt.decision_at > prior.decision_at:
                authorization_by_instrument[receipt.instrument_id] = receipt
        portfolio_e = build_cash_preserving_shadow_portfolio(
            tuple(authorization_by_instrument.values()),
            starting_equity=manifest.portfolio_e_policy.starting_equity,
            max_positions=manifest.portfolio_e_policy.max_positions,
            max_position_fraction=manifest.portfolio_e_policy.max_position_fraction,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=manifest.cohort.cohort_id,
            instrument_id="__portfolio_e__",
            kind="portfolio_e",
            observed_at=evaluated_at,
            payload=portfolio_e,
            state="frozen",
            run_id=manifest.run_id,
            idempotency_suffix=_hash(portfolio_e.model_dump(mode="json")),
        )
        terminal_count = sum(
            self._latest_confirmation_state(refreshed, candidate.instrument_id)
            in {"CONFIRMED_LONG", "INVALIDATED", "EXPIRED"}
            for candidate in manifest.candidates
        )

        # v4.2 remains an independent shadow action experiment. It does not
        # alter Portfolio E or the legacy v4 confirmation authority.
        v42_ledger = self.repository.session(session_date)
        for candidate in manifest.candidates:
            v42_record = self._v42_record(v42_ledger, candidate.instrument_id)
            watch = self._v42_watch(v42_ledger, candidate.instrument_id)
            if v42_record is None or watch is None:
                continue

            existing_v42_authorizations = [
                V42AuthorizationReceipt.model_validate(row.payload)
                for row in v42_ledger.records_of_kind("v42_authorization")
                if row.instrument_id == candidate.instrument_id
            ]
            if any(row.decision == "LONG" for row in existing_v42_authorizations):
                continue
            prior_action_record = v42_ledger.latest(
                kind="v42_action",
                instrument_id=candidate.instrument_id,
            )
            if prior_action_record is not None:
                prior_action = V42ActionSnapshot.model_validate(prior_action_record.payload)
                if prior_action.state in {"INVALIDATED", "EXPIRED"}:
                        continue

            v42_bars: Sequence[Any] = ()
            v42_data_quality_ok = False
            v42_data_quality_reasons: list[str] = []
            recovered = getattr(self.market_service, "recovered_bars", None)
            try:
                if callable(recovered):
                    recovered_result = recovered(
                        candidate.instrument_id,
                        "1m",
                        500,
                        candidate.binding_id,
                        session_date=session_date,
                        as_of=evaluated_at,
                        knowledge_mode="live",
                        knowledge_cutoff=evaluated_at,
                    )
                    v42_bars = recovered_result.bars
                    v42_data_quality_ok = not recovered_result.report.unresolved_gaps
                    if not v42_data_quality_ok:
                        v42_data_quality_reasons.append("CURRENT_SESSION_DEPENDENCY_GAP")
                else:
                    response = self.market_service.bars(
                        candidate.instrument_id,
                        "1m",
                        500,
                        candidate.binding_id,
                    )
                    v42_bars = tuple(getattr(response, "bars", ()) or ())
                    v42_data_quality_ok = bool(v42_bars)
                    if not v42_data_quality_ok:
                        v42_data_quality_reasons.append("CURRENT_TAPE_UNAVAILABLE")
            except Exception as exc:
                v42_data_quality_reasons.append(
                    f"CURRENT_TAPE_FETCH_FAILED:{type(exc).__name__}"
                )

            v42_cost = self._execution_cost(
                candidate=candidate,
                policy=DEFAULT_V42_ACTION_POLICY,
                decision_at=evaluated_at,
            )
            snapshot = evaluate_v42_post_open_action(
                forecast=v42_record.forecast,
                watch=watch,
                bars=v42_bars,
                evaluated_at=evaluated_at,
                data_quality_ok=v42_data_quality_ok,
                execution_cost=v42_cost,
                shared_confirmation_state=self._latest_confirmation_state(
                    v42_ledger,
                    candidate.instrument_id,
                ),
                policy=DEFAULT_V42_ACTION_POLICY,
                data_quality_reasons=tuple(v42_data_quality_reasons),
            )
            self.repository.append(
                session_date=session_date,
                cohort_id=manifest.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="v42_action",
                observed_at=evaluated_at,
                payload=snapshot,
                state=snapshot.state,
                reason_code=snapshot.reasons[0] if snapshot.reasons else None,
                run_id=manifest.run_id,
                idempotency_suffix=_hash(snapshot.model_dump(mode="json")),
            )

            if snapshot.state in {"STRUCTURE_CONFIRMED", "INVALIDATED", "EXPIRED"}:
                v42_authorization = authorize_v42_action(
                    forecast=v42_record.forecast,
                    watch=watch,
                    snapshot=snapshot,
                    execution_cost=v42_cost,
                    shared_confirmation_state=self._latest_confirmation_state(
                        v42_ledger,
                        candidate.instrument_id,
                    ),
                    policy=DEFAULT_V42_ACTION_POLICY,
                )
                self.repository.append(
                    session_date=session_date,
                    cohort_id=manifest.cohort.cohort_id,
                    instrument_id=candidate.instrument_id,
                    kind="v42_authorization",
                    observed_at=v42_authorization.decision_at,
                    payload=v42_authorization,
                    state=v42_authorization.decision,
                    reason_code=v42_authorization.reasons[0] if v42_authorization.reasons else None,
                    run_id=manifest.run_id,
                    idempotency_suffix=_hash(
                        {
                            "snapshot": snapshot.model_dump(mode="json"),
                            "authorization": v42_authorization.model_dump(mode="json"),
                        }
                    ),
                )
        refreshed_v42 = self.repository.session(session_date)
        portfolio_f = build_portfolio_f(
            tuple(
                V42AuthorizationReceipt.model_validate(row.payload)
                for row in refreshed_v42.records_of_kind("v42_authorization")
            ),
            policy=DEFAULT_V42_ACTION_POLICY,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=manifest.cohort.cohort_id,
            instrument_id="__portfolio_f__",
            kind="portfolio_f",
            observed_at=evaluated_at,
            payload=portfolio_f,
            state="frozen",
            run_id=manifest.run_id,
            idempotency_suffix=_hash(portfolio_f.model_dump(mode="json")),
        )

        # v4.3 consumes the frozen v4.3 premarket forecast and the causal v4.2
        # action snapshot. It never recomputes the base forecast or market tape.
        v43_ledger = self.repository.session(session_date)
        for candidate in manifest.candidates:
            v43_record = self._v43_record(v43_ledger, candidate.instrument_id)
            v43_watch = self._v43_watch(v43_ledger, candidate.instrument_id)
            base_v42_record = self._v42_record(v43_ledger, candidate.instrument_id)
            base_action_record = v43_ledger.latest(
                kind="v42_action",
                instrument_id=candidate.instrument_id,
            )
            if (
                v43_record is None
                or v43_watch is None
                or base_v42_record is None
                or base_action_record is None
            ):
                continue

            existing_v43_authorizations = [
                V43AuthorizationReceipt.model_validate(row.payload)
                for row in v43_ledger.records_of_kind("v43_authorization")
                if row.instrument_id == candidate.instrument_id
            ]
            if any(row.decision == "LONG" for row in existing_v43_authorizations):
                continue

            prior_v43_action_record = v43_ledger.latest(
                kind="v43_action",
                instrument_id=candidate.instrument_id,
            )
            if prior_v43_action_record is not None:
                prior_v43_action = V43ActionSnapshot.model_validate(
                    prior_v43_action_record.payload
                )
                if prior_v43_action.state in {"INVALIDATED", "EXPIRED"}:
                    continue

            base_action = V42ActionSnapshot.model_validate(base_action_record.payload)
            v43_action = evaluate_v43_post_open_action(
                forecast=v43_record.forecast,
                base_v42=base_v42_record.forecast,
                watch=v43_watch,
                base_snapshot=base_action,
                policy=DEFAULT_V43_ACTION_POLICY,
            )
            self.repository.append(
                session_date=session_date,
                cohort_id=manifest.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="v43_action",
                observed_at=evaluated_at,
                payload=v43_action,
                state=v43_action.state,
                reason_code=v43_action.reasons[0] if v43_action.reasons else None,
                run_id=manifest.run_id,
                idempotency_suffix=_hash(v43_action.model_dump(mode="json")),
            )

            if v43_action.state in {
                "STRUCTURE_CONFIRMED",
                "INVALIDATED",
                "EXPIRED",
            }:
                base_authorization_record = v43_ledger.latest(
                    kind="v42_authorization",
                    instrument_id=candidate.instrument_id,
                )
                base_authorization = (
                    V42AuthorizationReceipt.model_validate(
                        base_authorization_record.payload
                    )
                    if base_authorization_record is not None
                    else None
                )
                v43_authorization = authorize_v43_action(
                    forecast=v43_record.forecast,
                    watch=v43_watch,
                    snapshot=v43_action,
                    base_authorization=base_authorization,
                    policy=DEFAULT_V43_ACTION_POLICY,
                )
                self.repository.append(
                    session_date=session_date,
                    cohort_id=manifest.cohort.cohort_id,
                    instrument_id=candidate.instrument_id,
                    kind="v43_authorization",
                    observed_at=v43_authorization.decision_at,
                    payload=v43_authorization,
                    state=v43_authorization.decision,
                    reason_code=(
                        v43_authorization.reasons[0]
                        if v43_authorization.reasons
                        else None
                    ),
                    run_id=manifest.run_id,
                    idempotency_suffix=_hash(
                        {
                            "action": v43_action.model_dump(mode="json"),
                            "authorization": v43_authorization.model_dump(
                                mode="json"
                            ),
                        }
                    ),
                )

        refreshed_v43 = self.repository.session(session_date)
        portfolio_g = build_portfolio_g(
            tuple(
                V43AuthorizationReceipt.model_validate(row.payload)
                for row in refreshed_v43.records_of_kind("v43_authorization")
            ),
            policy=DEFAULT_V43_ACTION_POLICY,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=manifest.cohort.cohort_id,
            instrument_id="__portfolio_g__",
            kind="portfolio_g",
            observed_at=evaluated_at,
            payload=portfolio_g,
            state="frozen",
            run_id=manifest.run_id,
            idempotency_suffix=_hash(portfolio_g.model_dump(mode="json")),
        )
        return ConfirmationRunResult(
            session_date=session_date,
            evaluated_at=evaluated_at,
            evaluated_count=len(manifest.candidates),
            new_confirmation_receipt_count=new_receipts,
            new_authorization_count=new_authorizations,
            terminal_count=terminal_count,
            portfolio_e=portfolio_e,
        )

    def _portfolio_e_performance(
        self,
        *,
        ledger: ProspectiveGapSessionLedger,
        outcomes: dict[str, FormalOutcomeBundle],
    ) -> PortfolioEPerformance | None:
        record = ledger.latest(kind="portfolio_e", instrument_id="__portfolio_e__")
        if record is None:
            return None
        portfolio = CashPreservingShadowPortfolio.model_validate(record.payload)
        authorizations: dict[str, TradeAuthorizationReceipt] = {}
        for row in ledger.records_of_kind("authorization"):
            receipt = TradeAuthorizationReceipt.model_validate(row.payload)
            prior = authorizations.get(receipt.instrument_id)
            if prior is None or receipt.decision_at > prior.decision_at:
                authorizations[receipt.instrument_id] = receipt

        ending = portfolio.cash
        rows: list[PortfolioEPositionOutcome] = []
        for position in portfolio.positions:
            outcome = outcomes.get(position.instrument_id)
            position_receipt = authorizations.get(position.instrument_id)
            if outcome is None or position_receipt is None or position_receipt.reference_price is None:
                continue
            entry = position_receipt.reference_price
            close = outcome.prices.close_price
            raw_return = close / entry - Decimal("1")
            cost_return = (position_receipt.total_cost_bps or Decimal("0")) / Decimal("10000")
            net_return = raw_return - cost_return
            value = position.allocation * (Decimal("1") + net_return)
            pnl = value - position.allocation
            ending += value
            rows.append(
                PortfolioEPositionOutcome(
                    instrument_id=position.instrument_id,
                    allocation=position.allocation,
                    reference_entry_price=entry,
                    close_price=close,
                    raw_return=raw_return,
                    cost_adjusted_return=net_return,
                    pnl=pnl,
                )
            )
        pnl = ending - portfolio.starting_equity
        return PortfolioEPerformance(
            rule_version=portfolio.rule_version,
            starting_equity=portfolio.starting_equity,
            ending_equity=ending,
            pnl=pnl,
            return_pct=pnl / portfolio.starting_equity,
            cash=portfolio.cash,
            position_outcomes=tuple(rows),
        )

    def _portfolio_f_performance(
        self,
        *,
        ledger: ProspectiveGapSessionLedger,
        outcomes: dict[str, FormalOutcomeBundle],
    ) -> PortfolioEPerformance | None:
        record = ledger.latest(kind="portfolio_f", instrument_id="__portfolio_f__")
        if record is None:
            return None
        portfolio = PortfolioF.model_validate(record.payload)
        ending = portfolio.cash
        rows: list[PortfolioEPositionOutcome] = []
        for position in portfolio.positions:
            outcome = outcomes.get(position.instrument_id)
            if outcome is None:
                continue
            entry = position.reference_price
            close = outcome.prices.close_price
            raw_return = close / entry - Decimal("1")
            cost_return = position.total_cost_bps / Decimal("10000")
            net_return = raw_return - cost_return
            value = position.allocation * (Decimal("1") + net_return)
            pnl = value - position.allocation
            ending += value
            rows.append(
                PortfolioEPositionOutcome(
                    instrument_id=position.instrument_id,
                    allocation=position.allocation,
                    reference_entry_price=entry,
                    close_price=close,
                    raw_return=raw_return,
                    cost_adjusted_return=net_return,
                    pnl=pnl,
                )
            )
        pnl = ending - portfolio.starting_equity
        return PortfolioEPerformance(
            rule_version=portfolio.version,
            starting_equity=portfolio.starting_equity,
            ending_equity=ending,
            pnl=pnl,
            return_pct=pnl / portfolio.starting_equity,
            cash=portfolio.cash,
            position_outcomes=tuple(rows),
        )

    def _portfolio_g_performance(
        self,
        *,
        ledger: ProspectiveGapSessionLedger,
        outcomes: dict[str, FormalOutcomeBundle],
    ) -> PortfolioEPerformance | None:
        record = ledger.latest(kind="portfolio_g", instrument_id="__portfolio_g__")
        if record is None:
            return None
        portfolio = PortfolioG.model_validate(record.payload)
        ending = portfolio.cash
        rows: list[PortfolioEPositionOutcome] = []
        for position in portfolio.positions:
            outcome = outcomes.get(position.instrument_id)
            if outcome is None:
                continue
            entry = position.reference_price
            close = outcome.prices.close_price
            raw_return = close / entry - Decimal("1")
            cost_return = position.total_cost_bps / Decimal("10000")
            net_return = raw_return - cost_return
            value = position.allocation * (Decimal("1") + net_return)
            pnl = value - position.allocation
            ending += value
            rows.append(
                PortfolioEPositionOutcome(
                    instrument_id=position.instrument_id,
                    allocation=position.allocation,
                    reference_entry_price=entry,
                    close_price=close,
                    raw_return=raw_return,
                    cost_adjusted_return=net_return,
                    pnl=pnl,
                )
            )
        pnl = ending - portfolio.starting_equity
        return PortfolioEPerformance(
            rule_version=portfolio.version,
            starting_equity=portfolio.starting_equity,
            ending_equity=ending,
            pnl=pnl,
            return_pct=pnl / portfolio.starting_equity,
            cash=portfolio.cash,
            position_outcomes=tuple(rows),
        )

    def finalize_postclose(
        self,
        *,
        session_date: date,
        evaluated_at: datetime,
    ) -> PostcloseRunResult:
        evaluated_at = _utc(evaluated_at)
        manifest = self._manifest(session_date)
        ledger = self.repository.session(session_date)
        outcomes: list[FormalOutcomeBundle] = []
        outcome_by_instrument: dict[str, FormalOutcomeBundle] = {}
        for candidate in manifest.candidates:
            bundle = load_operational_formal_outcome(
                market_service=self.market_service,
                candidate=candidate,
                session_date=session_date,
            )
            outcomes.append(bundle)
            outcome_by_instrument[candidate.instrument_id] = bundle
            self.repository.append(
                session_date=session_date,
                cohort_id=manifest.cohort.cohort_id,
                instrument_id=candidate.instrument_id,
                kind="formal_outcome",
                observed_at=evaluated_at,
                payload=bundle,
                state="FINAL",
                run_id=manifest.run_id,
            )

        refreshed = self.repository.session(session_date)
        v3_obs: list[BinaryForecastObservation] = []
        v4_obs: list[BinaryForecastObservation] = []
        v42_obs: list[BinaryForecastObservation] = []
        v42_matched_v3_obs: list[BinaryForecastObservation] = []
        v42_matched_obs: list[BinaryForecastObservation] = []
        v42_return_obs: list[V42ReturnObservation] = []
        v43_obs: list[BinaryForecastObservation] = []
        v43_matched_v3_obs: list[BinaryForecastObservation] = []
        v43_matched_obs: list[BinaryForecastObservation] = []
        paired: list[PairedForecastObservation] = []
        complete = degraded = insufficient = unresolved = 0

        for candidate in manifest.candidates:
            outcome = outcome_by_instrument[candidate.instrument_id]
            outcome_value = outcome.labels.close_above_open

            state_record = refreshed.latest(kind="premarket_state", instrument_id=candidate.instrument_id)
            if state_record is not None:
                state = OperationalPremarketState.model_validate(state_record.payload)
                unresolved += state.unresolved_gap_count
                if state.evidence_quality.quality == "COMPLETE":
                    complete += 1
                elif state.evidence_quality.quality == "DEGRADED":
                    degraded += 1
                else:
                    insufficient += 1

            v3_record = refreshed.latest(kind="v3_forecast", instrument_id=candidate.instrument_id)
            v4_record = refreshed.latest(kind="v4_forecast", instrument_id=candidate.instrument_id)
            v3 = FrozenForecast.model_validate(v3_record.payload) if v3_record is not None else None
            v4 = (
                V4ForecastRecord.model_validate(v4_record.payload).forecast
                if v4_record is not None
                else None
            )
            v42_record = refreshed.latest(kind="v42_forecast", instrument_id=candidate.instrument_id)
            v42 = (
                V42ForecastRecord.model_validate(v42_record.payload).forecast
                if v42_record is not None
                else None
            )
            v43_record = refreshed.latest(kind="v43_forecast", instrument_id=candidate.instrument_id)
            v43 = (
                V43ForecastRecord.model_validate(v43_record.payload).forecast
                if v43_record is not None
                else None
            )
            if v3 is not None:
                v3_obs.append(
                    BinaryForecastObservation(
                        instrument_id=candidate.instrument_id,
                        probability=v3.p_close_above_open,
                        outcome=outcome_value,
                    )
                )
            if v4 is not None:
                v4_obs.append(
                    BinaryForecastObservation(
                        instrument_id=candidate.instrument_id,
                        probability=v4.calibrated_p_close_above_open,
                        outcome=outcome_value,
                    )
                )
            if v42 is not None:
                v42_observation = BinaryForecastObservation(
                    instrument_id=candidate.instrument_id,
                    probability=v42.p_close_above_open,
                    outcome=outcome_value,
                )
                v42_obs.append(v42_observation)
                if v3 is not None:
                    v42_matched_v3_obs.append(
                        BinaryForecastObservation(
                            instrument_id=candidate.instrument_id,
                            probability=v3.p_close_above_open,
                            outcome=outcome_value,
                        )
                    )
                    v42_matched_obs.append(v42_observation)
                v42_return_obs.append(
                    V42ReturnObservation(
                        instrument_id=candidate.instrument_id,
                        forecast=v42,
                        realized_return=outcome.measurements.open_to_close_return,
                    )
                )
            if v43 is not None:
                v43_observation = BinaryForecastObservation(
                    instrument_id=candidate.instrument_id,
                    probability=v43.p_close_above_open,
                    outcome=outcome_value,
                )
                v43_obs.append(v43_observation)
                if v3 is not None:
                    v43_matched_v3_obs.append(
                        BinaryForecastObservation(
                            instrument_id=candidate.instrument_id,
                            probability=v3.p_close_above_open,
                            outcome=outcome_value,
                        )
                    )
                    v43_matched_obs.append(v43_observation)
            if v3 is not None and v4 is not None:
                paired.append(
                    PairedForecastObservation(
                        session_date=session_date,
                        cohort_fingerprint=manifest.cohort.cohort_fingerprint,
                        instrument_id=candidate.instrument_id,
                        v3_probability=v3.p_close_above_open,
                        v4_probability=v4.calibrated_p_close_above_open,
                        outcome=outcome_value,
                    )
                )

        legacy_score_bundle: LegacyPortfolioScoreBundle | None = None
        legacy_record = refreshed.latest(
            kind="legacy_portfolios",
            instrument_id="__portfolio__",
        )
        if legacy_record is not None:
            legacy = LegacyPortfolioBundle.model_validate(legacy_record.payload)
            prices_by_instrument = {
                instrument_id: outcome.prices
                for instrument_id, outcome in outcome_by_instrument.items()
            }
            legacy_score_bundle = LegacyPortfolioScoreBundle(
                scores=cast(Any, tuple(
                    score_frozen_portfolio(portfolio, prices_by_instrument)
                    for portfolio in legacy.portfolios
                ))
            )
            self.repository.append(
                session_date=session_date,
                cohort_id=manifest.cohort.cohort_id,
                instrument_id="__portfolio__",
                kind="legacy_portfolio_scores",
                observed_at=evaluated_at,
                payload=legacy_score_bundle,
                state="FINAL",
                run_id=manifest.run_id,
            )

        confirmation_rows = refreshed.records_of_kind("confirmation")
        authorization_rows = refreshed.records_of_kind("authorization")
        confirmations = [
            ConfirmationTransitionReceipt.model_validate(row.payload)
            for row in confirmation_rows
        ]
        authorizations = [
            TradeAuthorizationReceipt.model_validate(row.payload)
            for row in authorization_rows
        ]
        portfolio_e_performance = self._portfolio_e_performance(
            ledger=refreshed,
            outcomes=outcome_by_instrument,
        )
        portfolio_f_performance = self._portfolio_f_performance(
            ledger=refreshed,
            outcomes=outcome_by_instrument,
        )
        if portfolio_f_performance is not None:
            self.repository.append(
                session_date=session_date,
                cohort_id=manifest.cohort.cohort_id,
                instrument_id="__portfolio_f__",
                kind="portfolio_f_score",
                observed_at=evaluated_at,
                payload=portfolio_f_performance,
                state="FINAL",
                run_id=manifest.run_id,
                idempotency_suffix=_hash(
                    portfolio_f_performance.model_dump(mode="json")
                ),
            )
        portfolio_g_performance = self._portfolio_g_performance(
            ledger=refreshed,
            outcomes=outcome_by_instrument,
        )
        if portfolio_g_performance is not None:
            self.repository.append(
                session_date=session_date,
                cohort_id=manifest.cohort.cohort_id,
                instrument_id="__portfolio_g__",
                kind="portfolio_g_score",
                observed_at=evaluated_at,
                payload=portfolio_g_performance,
                state="FINAL",
                run_id=manifest.run_id,
                idempotency_suffix=_hash(
                    portfolio_g_performance.model_dump(mode="json")
                ),
            )
        v3_metrics = evaluate_binary_forecasts(
            v3_obs,
            frozen_climatology_probability=manifest.frozen_climatology_probability,
        )
        v42_metrics = evaluate_binary_forecasts(
            v42_obs,
            frozen_climatology_probability=manifest.frozen_climatology_probability,
        )
        matched_v3_metrics = evaluate_binary_forecasts(v42_matched_v3_obs)
        matched_v42_metrics = evaluate_binary_forecasts(v42_matched_obs)
        v43_metrics = evaluate_binary_forecasts(
            v43_obs,
            frozen_climatology_probability=manifest.frozen_climatology_probability,
        )
        matched_v43_v3_metrics = evaluate_binary_forecasts(v43_matched_v3_obs)
        matched_v43_metrics = evaluate_binary_forecasts(v43_matched_obs)
        v42_comparison = V42ComparisonMetrics(
            n=matched_v42_metrics.n,
            brier_delta_v42_minus_v3=(
                matched_v42_metrics.brier_score - matched_v3_metrics.brier_score
                if (
                    matched_v42_metrics.brier_score is not None
                    and matched_v3_metrics.brier_score is not None
                )
                else None
            ),
            log_loss_delta_v42_minus_v3=(
                matched_v42_metrics.log_loss - matched_v3_metrics.log_loss
                if (
                    matched_v42_metrics.log_loss is not None
                    and matched_v3_metrics.log_loss is not None
                )
                else None
            ),
            accuracy_delta_v42_minus_v3=(
                matched_v42_metrics.accuracy - matched_v3_metrics.accuracy
                if (
                    matched_v42_metrics.accuracy is not None
                    and matched_v3_metrics.accuracy is not None
                )
                else None
            ),
        )
        v43_comparison = V43ComparisonMetrics(
            n=matched_v43_metrics.n,
            brier_delta_v43_minus_v3=(
                matched_v43_metrics.brier_score
                - matched_v43_v3_metrics.brier_score
                if (
                    matched_v43_metrics.brier_score is not None
                    and matched_v43_v3_metrics.brier_score is not None
                )
                else None
            ),
            log_loss_delta_v43_minus_v3=(
                matched_v43_metrics.log_loss
                - matched_v43_v3_metrics.log_loss
                if (
                    matched_v43_metrics.log_loss is not None
                    and matched_v43_v3_metrics.log_loss is not None
                )
                else None
            ),
            accuracy_delta_v43_minus_v3=(
                matched_v43_metrics.accuracy
                - matched_v43_v3_metrics.accuracy
                if (
                    matched_v43_metrics.accuracy is not None
                    and matched_v43_v3_metrics.accuracy is not None
                )
                else None
            ),
        )
        scorecard = DailyProspectiveScorecard(
            session_date=session_date,
            cohort_id=manifest.cohort.cohort_id,
            complete_evidence_count=complete,
            degraded_evidence_count=degraded,
            insufficient_evidence_count=insufficient,
            unresolved_premarket_bar_count=unresolved,
            v3_metrics=v3_metrics,
            v4_metrics=evaluate_binary_forecasts(
                v4_obs,
                frozen_climatology_probability=manifest.frozen_climatology_probability,
            ),
            paired_metrics=evaluate_paired_v3_v4(paired),
            v42_metrics=v42_metrics,
            v42_comparison=v42_comparison,
            v42_return_metrics=evaluate_v42_return_metrics(v42_return_obs),
            v43_metrics=v43_metrics,
            v43_comparison=v43_comparison,
            legacy_portfolio_scores=legacy_score_bundle,
            confirmation_receipt_count=len(confirmations),
            confirmed_long_count=sum(row.new_state == "CONFIRMED_LONG" for row in confirmations),
            authorization_long_count=sum(row.decision == "LONG" for row in authorizations),
            authorization_no_trade_count=sum(row.decision == "NO_TRADE" for row in authorizations),
            portfolio_e_performance=portfolio_e_performance,
            portfolio_f_performance=portfolio_f_performance,
            portfolio_g_performance=portfolio_g_performance,
        )
        self.repository.append(
            session_date=session_date,
            cohort_id=manifest.cohort.cohort_id,
            instrument_id="__scorecard__",
            kind="daily_scorecard",
            observed_at=evaluated_at,
            payload=scorecard,
            state="FINAL",
            run_id=manifest.run_id,
        )
        return PostcloseRunResult(
            session_date=session_date,
            outcomes=tuple(outcomes),
            scorecard=scorecard,
        )

    def session_ledger(self, session_date: date) -> ProspectiveGapSessionLedger:
        return self.repository.session(session_date)

    def render_markdown(self, session_date: date) -> str:
        manifest = self._manifest(session_date)
        ledger = self.repository.session(session_date)
        score_record = ledger.latest(kind="daily_scorecard", instrument_id="__scorecard__")
        lines = [
            f"# Omnix Prospective Gap Runtime — {session_date.isoformat()}",
            "",
            f"- Runtime: `{RUNTIME_VERSION}`",
            f"- Cohort: `{manifest.cohort.cohort_id}`",
            f"- Cohort fingerprint: `{manifest.cohort.cohort_fingerprint}`",
            f"- Prediction cutoff: {manifest.cohort.discovery_cutoff_at.isoformat()}",
            f"- Frozen at: {manifest.frozen_at.isoformat()}",
            "",
            "## Machine-readable authority",
            "",
            "This document is a projection of the prospective-gap StrategyEvent ledger. "
            "The ledger, not this Markdown, is forecast/action/outcome authority.",
            "",
        ]
        if score_record is None:
            lines.extend([
                "Post-close scorecard is not final yet.",
                "",
            ])
            return "\n".join(lines)
        score = DailyProspectiveScorecard.model_validate(score_record.payload)
        lines.extend([
            "## Daily scorecard",
            "",
            f"- Evidence: {score.complete_evidence_count} COMPLETE / "
            f"{score.degraded_evidence_count} DEGRADED / "
            f"{score.insufficient_evidence_count} INSUFFICIENT",
            f"- Unresolved premarket one-minute bars: {score.unresolved_premarket_bar_count}",
            f"- V3 Brier: {score.v3_metrics.brier_score}",
            f"- V4 Brier: {score.v4_metrics.brier_score}",
            f"- Paired ΔBrier (v4-v3): {score.paired_metrics.mean_delta_brier_v4_minus_v3}",
            f"- V4.2 Brier: {score.v42_metrics.brier_score}",
            f"- V4.2 ΔBrier vs v3: {score.v42_comparison.brier_delta_v42_minus_v3}",
            f"- V4.2 expected-return MAE: {score.v42_return_metrics.expected_return_mae}",
            f"- V4.2 downside-tail Brier P(return<-5%): {score.v42_return_metrics.p_lt_minus_5_brier}",
            f"- V4.2 q10 breach rate: {score.v42_return_metrics.q10_breach_rate}",
            f"- V4.3 Brier: {score.v43_metrics.brier_score}",
            f"- V4.3 ΔBrier vs v3: {score.v43_comparison.brier_delta_v43_minus_v3}",
            f"- Confirmation receipts: {score.confirmation_receipt_count}",
            f"- Confirmed longs: {score.confirmed_long_count}",
            f"- Authorized longs: {score.authorization_long_count}",
            f"- NO_TRADE authorizations: {score.authorization_no_trade_count}",
        ])
        if score.legacy_portfolio_scores is not None:
            for index, portfolio_score in enumerate(
                score.legacy_portfolio_scores.scores,
                start=1,
            ):
                label = ("A", "B", "C", "D")[index - 1]
                lines.append(
                    f"- Portfolio {label} ({portfolio_score.rule_version}) return: "
                    f"{portfolio_score.return_pct * Decimal('100')}%"
                )
        if score.portfolio_e_performance is not None:
            lines.append(
                f"- Portfolio E return: {score.portfolio_e_performance.return_pct * Decimal('100')}%"
            )
        v42_action_rows = ledger.records_of_kind("v42_action")
        v42_authorization_rows = ledger.records_of_kind("v42_authorization")
        if v42_action_rows or v42_authorization_rows:
            v42_actions = [
                V42ActionSnapshot.model_validate(row.payload)
                for row in v42_action_rows
            ]
            v42_authorizations = [
                V42AuthorizationReceipt.model_validate(row.payload)
                for row in v42_authorization_rows
            ]
            lines.extend([
                f"- V4.2 action snapshots: {len(v42_actions)}",
                f"- V4.2 structure confirmations: "
                f"{sum(row.state == 'STRUCTURE_CONFIRMED' for row in v42_actions)}",
                f"- V4.2 authorized longs: "
                f"{sum(row.decision == 'LONG' for row in v42_authorizations)}",
                f"- V4.2 NO_TRADE authorizations: "
                f"{sum(row.decision == 'NO_TRADE' for row in v42_authorizations)}",
            ])
        portfolio_f_score_record = ledger.latest(
            kind="portfolio_f_score",
            instrument_id="__portfolio_f__",
        )
        if portfolio_f_score_record is not None:
            portfolio_f_score = PortfolioEPerformance.model_validate(
                portfolio_f_score_record.payload
            )
            lines.append(
                f"- Portfolio F (v4.2 timed confirmation) return: "
                f"{portfolio_f_score.return_pct * Decimal('100')}%"
            )
        v43_action_rows = ledger.records_of_kind("v43_action")
        v43_authorization_rows = ledger.records_of_kind("v43_authorization")
        if v43_action_rows or v43_authorization_rows:
            v43_actions = [
                V43ActionSnapshot.model_validate(row.payload)
                for row in v43_action_rows
            ]
            v43_authorizations = [
                V43AuthorizationReceipt.model_validate(row.payload)
                for row in v43_authorization_rows
            ]
            lines.extend([
                f"- V4.3 action snapshots: {len(v43_actions)}",
                f"- V4.3 structure confirmations: "
                f"{sum(row.state == 'STRUCTURE_CONFIRMED' for row in v43_actions)}",
                f"- V4.3 authorized longs: "
                f"{sum(row.decision == 'LONG' for row in v43_authorizations)}",
                f"- V4.3 NO_TRADE authorizations: "
                f"{sum(row.decision == 'NO_TRADE' for row in v43_authorizations)}",
            ])
        portfolio_g_score_record = ledger.latest(
            kind="portfolio_g_score",
            instrument_id="__portfolio_g__",
        )
        if portfolio_g_score_record is not None:
            portfolio_g_score = PortfolioEPerformance.model_validate(
                portfolio_g_score_record.payload
            )
            lines.append(
                f"- Portfolio G (v4.3 exhaustion/regime action) return: "
                f"{portfolio_g_score.return_pct * Decimal('100')}%"
            )
        lines.append("")
        return "\n".join(lines)


_default_runtime: ProspectiveGapRuntime | None = None


def default_prospective_gap_runtime() -> ProspectiveGapRuntime:
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = ProspectiveGapRuntime()
    return _default_runtime


__all__ = [
    "DailyProspectiveScorecard",
    "LegacyPortfolioBundle",
    "LegacyPortfolioScoreBundle",
    "PORTFOLIO_E_POLICY_VERSION",
    "PortfolioEPerformance",
    "PortfolioEPolicy",
    "PostcloseRunResult",
    "PremarketFreezeRequest",
    "ProspectiveClimatologyState",
    "SchedulerPremarketHandoff",
    "SchedulerPremarketInstrumentInput",
    "PremarketFreezeResult",
    "PremarketInstrumentInput",
    "ProspectiveGapRuntime",
    "ProspectiveSessionManifest",
    "RUNTIME_VERSION",
    "V4ForecastRecord",
    "V42ComparisonMetrics",
    "V42ForecastRecord",
    "V43ComparisonMetrics",
    "V43ForecastRecord",
    "default_prospective_gap_runtime",
]
