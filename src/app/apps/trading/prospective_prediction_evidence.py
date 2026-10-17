from __future__ import annotations

"""Prospective premarket prediction evidence, scoring, and learning contracts.

Research-only: this module freezes causal evidence/forecasts, defines a single
same-session SIP scoring contract, derives continuous outcomes/versioned labels,
freezes portfolio baselines, evaluates forecasts, and governs hypothesis promotion.
It never places orders or changes predictor coefficients.
"""

import hashlib
import json
import math
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Final, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AdjustmentMode, MarketBar
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


PREMARKET_EVIDENCE_SCHEMA_VERSION = "premarket-evidence-v1"
FEATURE_SCHEMA_VERSION = "prospective-gap-features-v1"
PREDICTOR_VERSION = "prospective-gap-v3"
PRICE_CONTRACT_VERSION = "sip-analysis-prices-v1"
OUTCOME_MEASUREMENTS_VERSION: Final = "outcome-measurements-v1"
CLOSE_ABOVE_OPEN_LABEL_VERSION = "close_above_open_v1"
PERSISTENT_UPTREND_LABEL_VERSION = "persistent_uptrend_v1"
SESSION_REGIME_VERSION = "session_regime_v1"
CLIMATOLOGY_BASELINE_VERSION = "historical-climatology-v1"
PORTFOLIO_EQUAL_VERSION = "equal-weight-bullish-v1"
PORTFOLIO_RISK_VERSION = "confidence-risk-weight-v1"
PORTFOLIO_PROBABILITY_VERSION = "excess-probability-weight-v1"
PORTFOLIO_CASH_VERSION = "cash-v1"
HYPOTHESIS_POLICY_VERSION = "hypothesis-forward-validation-v1"

SessionRegime = Literal["PERSISTENT_UP", "VOLATILE_UP", "FLAT", "FADE", "PERSISTENT_DOWN"]
HypothesisStatus = Literal[
    "PROPOSED",
    "OBSERVATIONAL",
    "FROZEN_CANDIDATE",
    "FORWARD_VALIDATION",
    "CANDIDATE_FOR_PROMOTION",
    "SHADOW_CHALLENGER",
    "PROMOTED",
]
OutcomeState = Literal["PROVISIONAL", "FINAL"]


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def _regular_session_end(session_date: date) -> datetime:
    close = regular_close_time(session_date)
    return datetime.combine(session_date, close, tzinfo=_ET).astimezone(timezone.utc)


def _regular_session_minutes(session_date: date) -> Decimal:
    start = datetime.combine(session_date, time(9, 30), tzinfo=_ET)
    close = datetime.combine(
        session_date,
        regular_close_time(session_date),
        tzinfo=_ET,
    )
    return Decimal(str((close - start).total_seconds() / 60))


class EvidenceTimestamps(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    published_at: datetime | None = None
    effective_at: datetime | None = None
    observed_at: datetime
    ingested_at: datetime
    frozen_at: datetime

    @field_validator("published_at", "effective_at", "observed_at", "ingested_at", "frozen_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def ordering(self):
        if self.ingested_at < self.observed_at:
            raise ValueError("evidence_ingested_before_observed")
        if self.frozen_at < self.observed_at:
            raise ValueError("evidence_frozen_before_observed")
        return self


class PremarketEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    instrument_id: str | None = None
    source_type: str
    source_locator: str
    values: dict[str, Any] = Field(default_factory=dict)
    timestamps: EvidenceTimestamps
    source_fingerprint: str | None = None

    @property
    def immutable_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


class PremarketEvidenceSnapshot(BaseModel):
    """Complete information set the predictor was allowed to consume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    session_date: date
    prediction_cutoff_at: datetime
    frozen_at: datetime
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    evidence_schema_version: str = PREMARKET_EVIDENCE_SCHEMA_VERSION
    evidence: tuple[PremarketEvidenceItem, ...] = ()

    @field_validator("prediction_cutoff_at", "frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def causal_gate(self):
        if self.frozen_at > self.prediction_cutoff_at:
            raise ValueError("snapshot_frozen_after_prediction_cutoff")
        for item in self.evidence:
            if item.timestamps.observed_at > self.prediction_cutoff_at:
                raise ValueError(f"post_cutoff_evidence:{item.evidence_id}")
            if item.timestamps.ingested_at > self.prediction_cutoff_at:
                raise ValueError(f"post_cutoff_ingestion:{item.evidence_id}")
            if item.timestamps.frozen_at > self.prediction_cutoff_at:
                raise ValueError(f"post_cutoff_freeze:{item.evidence_id}")
        return self

    @property
    def snapshot_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


class FrozenForecast(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    evidence_snapshot_id: str
    predictor_version: str = PREDICTOR_VERSION
    feature_vector_fingerprint: str
    frozen_at: datetime
    p_close_above_open: Decimal = Field(ge=0, le=1)
    p_persistent_uptrend: Decimal = Field(ge=0, le=1)
    return_q10: Decimal | None = None
    return_q50: Decimal | None = None
    return_q90: Decimal | None = None
    mae_bucket: Literal["low", "moderate", "high"] | None = None
    mfe_bucket: Literal["low", "moderate", "high"] | None = None
    uncertainty: Literal["low", "moderate", "high"] = "high"

    @field_validator("frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def quantile_order(self):
        if self.return_q10 is not None and self.return_q50 is not None and self.return_q90 is not None:
            if not self.return_q10 <= self.return_q50 <= self.return_q90:
                raise ValueError("return_quantiles_must_be_monotonic")
        return self


class PredictionRunIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    session_date: date
    evidence_snapshot_fingerprint: str
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    predictor_version: str = PREDICTOR_VERSION
    portfolio_rule_versions: tuple[str, ...] = (
        PORTFOLIO_EQUAL_VERSION,
        PORTFOLIO_RISK_VERSION,
        PORTFOLIO_PROBABILITY_VERSION,
        PORTFOLIO_CASH_VERSION,
    )
    price_contract_version: str = PRICE_CONTRACT_VERSION
    label_versions: tuple[str, ...] = (
        CLOSE_ABOVE_OPEN_LABEL_VERSION,
        PERSISTENT_UPTREND_LABEL_VERSION,
        SESSION_REGIME_VERSION,
    )

    @property
    def immutable_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


class SIPTradeEligibilityPolicyV1(BaseModel):
    """Provider adapters decode native condition codes into these semantic flags."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["sip-trade-eligibility-v1"] = "sip-trade-eligibility-v1"
    allow_odd_lot: bool = False
    allow_auction: bool = False
    allow_special_condition: bool = False
    allow_out_of_sequence: bool = False
    allow_late_report: bool = False
    allow_luld_related: bool = True


class SIPTradeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    price: Decimal = Field(gt=0)
    event_timestamp: datetime
    received_timestamp: datetime
    exchange: str | None = None
    sip_feed: str = "sip"
    trade_condition_codes: tuple[str, ...] = ()
    provider_event_id: str | None = None
    sequence: int | None = None
    cancelled: bool = False
    correction: bool = False
    duplicate: bool = False
    out_of_sequence: bool = False
    late_report: bool = False
    auction: bool = False
    special_condition: bool = False
    odd_lot: bool = False
    luld_related: bool = False

    @field_validator("event_timestamp", "received_timestamp")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


class SelectedPriceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    price: Decimal
    event_timestamp: datetime
    received_timestamp: datetime
    exchange: str | None = None
    sip_feed: str
    trade_condition_codes: tuple[str, ...] = ()
    provider_event_id: str | None = None
    sequence: int | None = None
    eligibility_policy_version: str


class AnalysisSessionPrices(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    session_date: date
    price_contract_version: str = PRICE_CONTRACT_VERSION
    open_event: SelectedPriceEvent
    close_event: SelectedPriceEvent
    outcome_state: OutcomeState = "PROVISIONAL"
    eligible_trade_count: int = Field(ge=2)
    discarded_trade_count: int = Field(ge=0)
    reconciliation_fingerprint: str

    @property
    def open_price(self) -> Decimal:
        return self.open_event.price

    @property
    def close_price(self) -> Decimal:
        return self.close_event.price


def sip_trade_eligible(event: SIPTradeEvent, policy: SIPTradeEligibilityPolicyV1 | None = None) -> bool:
    policy = policy or SIPTradeEligibilityPolicyV1()
    if event.cancelled or event.correction or event.duplicate:
        return False
    if event.out_of_sequence and not policy.allow_out_of_sequence:
        return False
    if event.late_report and not policy.allow_late_report:
        return False
    if event.auction and not policy.allow_auction:
        return False
    if event.special_condition and not policy.allow_special_condition:
        return False
    if event.odd_lot and not policy.allow_odd_lot:
        return False
    if event.luld_related and not policy.allow_luld_related:
        return False
    return True


def _selected(event: SIPTradeEvent, policy: SIPTradeEligibilityPolicyV1) -> SelectedPriceEvent:
    return SelectedPriceEvent(
        price=event.price,
        event_timestamp=event.event_timestamp,
        received_timestamp=event.received_timestamp,
        exchange=event.exchange,
        sip_feed=event.sip_feed,
        trade_condition_codes=event.trade_condition_codes,
        provider_event_id=event.provider_event_id,
        sequence=event.sequence,
        eligibility_policy_version=policy.version,
    )


def select_analysis_session_prices(
    events: Sequence[SIPTradeEvent],
    *,
    session_date: date,
    policy: SIPTradeEligibilityPolicyV1 | None = None,
    outcome_state: OutcomeState = "PROVISIONAL",
) -> AnalysisSessionPrices:
    """Select first/last eligible consolidated SIP trades in [09:30,16:00) ET."""

    if not events:
        raise ValueError("sip_trade_events_required")
    policy = policy or SIPTradeEligibilityPolicyV1()
    instruments = {event.instrument_id for event in events}
    if len(instruments) != 1:
        raise ValueError("session_price_events_must_share_instrument")
    start = datetime.combine(session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    end = _regular_session_end(session_date)
    in_session = [event for event in events if start <= event.event_timestamp < end]
    eligible = [event for event in in_session if sip_trade_eligible(event, policy)]
    if len(eligible) < 2:
        raise ValueError("insufficient_eligible_regular_session_sip_trades")
    eligible.sort(key=lambda e: (e.event_timestamp, e.sequence if e.sequence is not None else -1, e.provider_event_id or ""))
    payload = {
        "policy": policy.model_dump(mode="json"),
        "session_date": session_date.isoformat(),
        "instrument_id": eligible[0].instrument_id,
        "eligible": [
            (e.provider_event_id, e.sequence, e.event_timestamp.isoformat(), str(e.price)) for e in eligible
        ],
    }
    return AnalysisSessionPrices(
        instrument_id=eligible[0].instrument_id,
        session_date=session_date,
        open_event=_selected(eligible[0], policy),
        close_event=_selected(eligible[-1], policy),
        outcome_state=outcome_state,
        eligible_trade_count=len(eligible),
        discarded_trade_count=len(in_session) - len(eligible),
        reconciliation_fingerprint=_hash(payload),
    )


class OutcomeMeasurementsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["outcome-measurements-v1"] = OUTCOME_MEASUREMENTS_VERSION
    instrument_id: str
    session_date: date
    analysis_open_price: Decimal
    analysis_close_price: Decimal
    open_to_close_return: Decimal
    normalized_slope: Decimal
    vwap_occupancy: Decimal = Field(ge=0, le=1)
    observed_bar_occupancy_above_open: Decimal = Field(ge=0, le=1)
    wall_clock_observed_occupancy_above_open: Decimal = Field(ge=0, le=1)
    observed_session_coverage: Decimal = Field(ge=0, le=1)
    directional_efficiency: Decimal = Field(ge=0, le=1)
    mae_from_open: Decimal
    mfe_from_open: Decimal
    closing_range_position: Decimal = Field(ge=0, le=1)
    session_high: Decimal
    session_low: Decimal
    observed_bar_count: int = Field(gt=0)
    halt_or_gap_minutes: Decimal = Field(ge=0)
    interpolated_halt_minutes: Literal[0] = 0
    source_price_contract_version: str = PRICE_CONTRACT_VERSION


def _regular_bars(bars: Sequence[MarketBar], prices: AnalysisSessionPrices) -> list[MarketBar]:
    start = datetime.combine(prices.session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    end = _regular_session_end(prices.session_date)
    selected = [
        bar for bar in bars
        if bar.instrument_id == prices.instrument_id
        and bar.is_final
        and bar.session == "regular"
        and bar.adjustment_mode == AdjustmentMode.RAW
        and start <= bar.start_time < end
    ]
    selected.sort(key=lambda b: (b.start_time, b.end_time, b.provider_sequence or -1))
    return selected


def build_outcome_measurements(*, prices: AnalysisSessionPrices, bars: Sequence[MarketBar]) -> OutcomeMeasurementsV1:
    """Compute continuous outcomes from RAW finalized bars without halt interpolation."""

    selected = _regular_bars(bars, prices)
    if not selected:
        raise ValueError("final_raw_regular_session_bars_required")
    open_price = prices.open_price
    close_price = prices.close_price
    count = len(selected)
    session_high = max(bar.high for bar in selected)
    session_low = min(bar.low for bar in selected)

    xs = [Decimal("0")] if count == 1 else [Decimal(i) / Decimal(count - 1) for i in range(count)]
    ys = [bar.close / open_price for bar in selected]
    x_mean = sum(xs, Decimal("0")) / Decimal(count)
    y_mean = sum(ys, Decimal("0")) / Decimal(count)
    denom = sum(((x - x_mean) ** 2 for x in xs), Decimal("0"))
    slope = (
        sum(((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)), Decimal("0")) / denom
        if denom > 0 else Decimal("0")
    )

    cumulative_pv = Decimal("0")
    cumulative_volume = Decimal("0")
    above_vwap = 0
    above_open = 0
    observed_minutes = Decimal("0")
    above_open_minutes = Decimal("0")
    previous_close = open_price
    path_distance = Decimal("0")
    for bar in selected:
        duration = max(Decimal("0"), Decimal(str((bar.end_time - bar.start_time).total_seconds())) / Decimal("60"))
        observed_minutes += duration
        if bar.close > open_price:
            above_open += 1
            above_open_minutes += duration
        volume = max(Decimal("0"), bar.volume)
        typical = (bar.high + bar.low + bar.close) / Decimal("3")
        cumulative_pv += typical * volume
        cumulative_volume += volume
        if cumulative_volume > 0 and bar.close > cumulative_pv / cumulative_volume:
            above_vwap += 1
        path_distance += abs(bar.close - previous_close)
        previous_close = bar.close

    directional_efficiency = (
        min(Decimal("1"), abs(close_price - open_price) / path_distance) if path_distance > 0 else Decimal("0")
    )
    range_size = session_high - session_low
    closing_range_position = (
        min(Decimal("1"), max(Decimal("0"), (close_price - session_low) / range_size))
        if range_size > 0 else Decimal("0.5")
    )
    regular_minutes = _regular_session_minutes(prices.session_date)
    return OutcomeMeasurementsV1(
        instrument_id=prices.instrument_id,
        session_date=prices.session_date,
        analysis_open_price=open_price,
        analysis_close_price=close_price,
        open_to_close_return=(close_price - open_price) / open_price,
        normalized_slope=slope,
        vwap_occupancy=Decimal(above_vwap) / Decimal(count),
        observed_bar_occupancy_above_open=Decimal(above_open) / Decimal(count),
        wall_clock_observed_occupancy_above_open=min(Decimal("1"), above_open_minutes / regular_minutes),
        observed_session_coverage=min(Decimal("1"), observed_minutes / regular_minutes),
        directional_efficiency=directional_efficiency,
        mae_from_open=(session_low - open_price) / open_price,
        mfe_from_open=(session_high - open_price) / open_price,
        closing_range_position=closing_range_position,
        session_high=session_high,
        session_low=session_low,
        observed_bar_count=count,
        halt_or_gap_minutes=max(Decimal("0"), regular_minutes - observed_minutes),
    )


class VersionedOutcomeLabels(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    close_above_open_version: str = CLOSE_ABOVE_OPEN_LABEL_VERSION
    close_above_open: bool
    persistent_uptrend_version: str = PERSISTENT_UPTREND_LABEL_VERSION
    persistent_uptrend: bool
    session_regime_version: str = SESSION_REGIME_VERSION
    session_regime: SessionRegime
    session_regime_evaluation_role: Literal["diagnostic"] = "diagnostic"


OutcomeScorabilityStatus = Literal["SCORABLE", "UNSCORABLE"]


class OutcomeScorability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: OutcomeScorabilityStatus
    observed_session_coverage: Decimal = Field(ge=0, le=1)
    confirmed_nontrading_minutes: Decimal = Field(ge=0)
    unresolved_minutes: Decimal = Field(ge=0)
    reasons: tuple[str, ...] = ()


def assess_outcome_scorability(
    measurements: OutcomeMeasurementsV1,
    *,
    confirmed_nontrading_minutes: Decimal = Decimal("0"),
) -> OutcomeScorability:
    """Decide whether whole-session labels are reproducible from observed data.

    Confirmed exchange halts/no-trade intervals may explain missing wall-clock
    minutes. Unresolved provider/data loss may not be silently treated as a halt.
    """

    confirmed = max(Decimal("0"), confirmed_nontrading_minutes)
    missing = max(Decimal("0"), measurements.halt_or_gap_minutes)
    explained = min(confirmed, missing)
    unresolved = max(Decimal("0"), missing - explained)
    if unresolved > Decimal("0.000001"):
        return OutcomeScorability(
            status="UNSCORABLE",
            observed_session_coverage=measurements.observed_session_coverage,
            confirmed_nontrading_minutes=explained,
            unresolved_minutes=unresolved,
            reasons=("UNRESOLVED_REGULAR_SESSION_COVERAGE",),
        )
    reason = (
        "CONFIRMED_NONTRADING_INTERVALS_ACCEPTED"
        if explained > 0
        else "COMPLETE_REGULAR_SESSION"
    )
    return OutcomeScorability(
        status="SCORABLE",
        observed_session_coverage=measurements.observed_session_coverage,
        confirmed_nontrading_minutes=explained,
        unresolved_minutes=Decimal("0"),
        reasons=(reason,),
    )


def derive_outcome_labels(measurements: OutcomeMeasurementsV1) -> VersionedOutcomeLabels:
    close_above = measurements.analysis_close_price > measurements.analysis_open_price
    persistent = (
        close_above
        and measurements.normalized_slope > 0
        and measurements.vwap_occupancy >= Decimal("0.60")
        and measurements.closing_range_position >= Decimal("0.60")
    )
    if abs(measurements.open_to_close_return) <= Decimal("0.01"):
        regime: SessionRegime = "FLAT"
    elif persistent:
        regime = "PERSISTENT_UP"
    elif close_above:
        regime = "VOLATILE_UP"
    elif measurements.mfe_from_open >= Decimal("0.05"):
        regime = "FADE"
    else:
        regime = "PERSISTENT_DOWN"
    return VersionedOutcomeLabels(close_above_open=close_above, persistent_uptrend=persistent, session_regime=regime)


class ConfidenceRiskFactors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalyst: Literal["strong_definitive", "solid_direct", "material_uncertain", "stale_theme_technical_unexplained"]
    liquidity: Literal["strong", "adequate", "weak"]
    opening_extension: Literal["LOW", "MEDIUM", "HIGH"]
    supply_risk: Literal["LOW", "MEDIUM", "HIGH"]
    squeeze: Literal["orderly_persistent", "neutral", "chaotic"]

    @property
    def multiplier_product(self) -> Decimal:
        return (
            {"strong_definitive": Decimal("1.15"), "solid_direct": Decimal("1.05"), "material_uncertain": Decimal("0.90"), "stale_theme_technical_unexplained": Decimal("0.80")}[self.catalyst]
            * {"strong": Decimal("1.10"), "adequate": Decimal("1.00"), "weak": Decimal("0.80")}[self.liquidity]
            * {"LOW": Decimal("1.10"), "MEDIUM": Decimal("1.00"), "HIGH": Decimal("0.75")}[self.opening_extension]
            * {"LOW": Decimal("1.10"), "MEDIUM": Decimal("0.90"), "HIGH": Decimal("0.65")}[self.supply_risk]
            * {"orderly_persistent": Decimal("1.05"), "neutral": Decimal("1.00"), "chaotic": Decimal("0.80")}[self.squeeze]
        )


class FrozenPortfolioPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    weight: Decimal = Field(ge=0, le=1)
    allocation: Decimal = Field(ge=0)
    p_close_above_open: Decimal = Field(ge=0, le=1)
    score: Decimal | None = None
    factors: ConfidenceRiskFactors | None = None


class FrozenPortfolio(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    portfolio_id: str
    rule_version: str
    starting_equity: Decimal = Field(gt=0)
    frozen_at: datetime
    positions: tuple[FrozenPortfolioPosition, ...] = ()
    cash: Decimal = Field(ge=0)

    @field_validator("frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def conserve(self):
        total = self.cash + sum((position.allocation for position in self.positions), Decimal("0"))
        if abs(total - self.starting_equity) > Decimal("0.01"):
            raise ValueError("portfolio_allocations_must_conserve_equity")
        return self


def _normalize_positions(rows, starting_equity: Decimal) -> tuple[FrozenPortfolioPosition, ...]:
    total = sum((row[2] for row in rows), Decimal("0"))
    if total <= 0:
        return ()
    output: list[FrozenPortfolioPosition] = []
    allocated = Decimal("0")
    for index, (instrument_id, probability, score, factors) in enumerate(rows):
        allocation = starting_equity - allocated if index == len(rows) - 1 else starting_equity * score / total
        allocated += allocation
        output.append(FrozenPortfolioPosition(
            instrument_id=instrument_id,
            weight=allocation / starting_equity,
            allocation=allocation,
            p_close_above_open=probability,
            score=score,
            factors=factors,
        ))
    return tuple(output)


def freeze_research_portfolios(
    forecasts: Sequence[FrozenForecast],
    *,
    frozen_at: datetime,
    risk_factors: dict[str, ConfidenceRiskFactors] | None = None,
    starting_equity: Decimal = Decimal("1000"),
) -> tuple[FrozenPortfolio, FrozenPortfolio, FrozenPortfolio, FrozenPortfolio]:
    """Freeze A equal, B confidence/risk, C excess-probability, D cash portfolios."""

    frozen_at = _utc(frozen_at)
    factors_by_symbol = risk_factors or {}
    eligible = sorted(
        [forecast for forecast in forecasts if forecast.p_close_above_open > Decimal("0.50")],
        key=lambda forecast: forecast.instrument_id,
    )
    equal_rows = [(f.instrument_id, f.p_close_above_open, Decimal("1"), None) for f in eligible]
    risk_rows = []
    probability_rows = []
    for forecast in eligible:
        factors = factors_by_symbol.get(forecast.instrument_id)
        multiplier = factors.multiplier_product if factors else Decimal("1")
        risk_rows.append((forecast.instrument_id, forecast.p_close_above_open, forecast.p_close_above_open * multiplier, factors))
        probability_rows.append((forecast.instrument_id, forecast.p_close_above_open, forecast.p_close_above_open - Decimal("0.50"), None))

    def build(rule: str, rows) -> FrozenPortfolio:
        positions = _normalize_positions(rows, starting_equity)
        allocated = sum((position.allocation for position in positions), Decimal("0"))
        return FrozenPortfolio(
            portfolio_id=f"{rule}:{frozen_at.isoformat()}",
            rule_version=rule,
            starting_equity=starting_equity,
            frozen_at=frozen_at,
            positions=positions,
            cash=starting_equity - allocated,
        )

    return (
        build(PORTFOLIO_EQUAL_VERSION, equal_rows),
        build(PORTFOLIO_RISK_VERSION, risk_rows),
        build(PORTFOLIO_PROBABILITY_VERSION, probability_rows),
        build(PORTFOLIO_CASH_VERSION, []),
    )


class PortfolioPositionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    allocation: Decimal
    open_price: Decimal
    close_price: Decimal
    shares: Decimal
    ending_value: Decimal
    pnl: Decimal
    return_pct: Decimal


class PortfolioScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_version: str
    starting_equity: Decimal
    ending_equity: Decimal
    pnl: Decimal
    return_pct: Decimal
    position_results: tuple[PortfolioPositionResult, ...] = ()
    cash: Decimal


def score_frozen_portfolio(portfolio: FrozenPortfolio, prices: dict[str, AnalysisSessionPrices]) -> PortfolioScore:
    ending = portfolio.cash
    results: list[PortfolioPositionResult] = []
    for position in portfolio.positions:
        selected = prices.get(position.instrument_id)
        if selected is None:
            raise ValueError(f"missing_analysis_prices:{position.instrument_id}")
        shares = position.allocation / selected.open_price
        ending_value = shares * selected.close_price
        pnl = ending_value - position.allocation
        ending += ending_value
        results.append(PortfolioPositionResult(
            instrument_id=position.instrument_id,
            allocation=position.allocation,
            open_price=selected.open_price,
            close_price=selected.close_price,
            shares=shares,
            ending_value=ending_value,
            pnl=pnl,
            return_pct=(selected.close_price - selected.open_price) / selected.open_price,
        ))
    pnl = ending - portfolio.starting_equity
    return PortfolioScore(
        rule_version=portfolio.rule_version,
        starting_equity=portfolio.starting_equity,
        ending_equity=ending,
        pnl=pnl,
        return_pct=pnl / portfolio.starting_equity,
        position_results=tuple(results),
        cash=portfolio.cash,
    )


class BinaryForecastObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    probability: Decimal = Field(ge=0, le=1)
    outcome: bool


class BinaryForecastMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    n: int
    base_rate: Decimal | None = None
    accuracy: Decimal | None = None
    brier_score: Decimal | None = None
    log_loss: Decimal | None = None
    bullish_precision: Decimal | None = None
    bullish_recall: Decimal | None = None
    climatology_probability: Decimal | None = None
    climatology_brier: Decimal | None = None
    brier_skill: Decimal | None = None


def climatology_probability(prior_outcomes: Sequence[bool]) -> Decimal | None:
    if not prior_outcomes:
        return None
    return Decimal(sum(1 for value in prior_outcomes if value)) / Decimal(len(prior_outcomes))


def evaluate_binary_forecasts(
    observations: Sequence[BinaryForecastObservation],
    *,
    frozen_climatology_probability: Decimal | None = None,
) -> BinaryForecastMetrics:
    if not observations:
        return BinaryForecastMetrics(n=0)
    n = Decimal(len(observations))
    briers: list[Decimal] = []
    losses: list[Decimal] = []
    correct = positives = tp = fp = fn = 0
    for row in observations:
        y = Decimal("1") if row.outcome else Decimal("0")
        positives += int(row.outcome)
        briers.append((row.probability - y) ** 2)
        p = min(1 - 1e-12, max(1e-12, float(row.probability)))
        losses.append(Decimal(str(-math.log(p if row.outcome else 1 - p))))
        predicted = row.probability > Decimal("0.50")
        correct += int(predicted == row.outcome)
        tp += int(predicted and row.outcome)
        fp += int(predicted and not row.outcome)
        fn += int((not predicted) and row.outcome)
    model_brier = sum(briers, Decimal("0")) / n
    baseline_brier = None
    skill = None
    if frozen_climatology_probability is not None:
        q = frozen_climatology_probability
        baseline_brier = sum(
            ((q - (Decimal("1") if row.outcome else Decimal("0"))) ** 2 for row in observations),
            Decimal("0"),
        ) / n
        if baseline_brier > 0:
            skill = Decimal("1") - model_brier / baseline_brier
    return BinaryForecastMetrics(
        n=len(observations),
        base_rate=Decimal(positives) / n,
        accuracy=Decimal(correct) / n,
        brier_score=model_brier,
        log_loss=sum(losses, Decimal("0")) / n,
        bullish_precision=Decimal(tp) / Decimal(tp + fp) if tp + fp else None,
        bullish_recall=Decimal(tp) / Decimal(tp + fn) if tp + fn else None,
        climatology_probability=frozen_climatology_probability,
        climatology_brier=baseline_brier,
        brier_skill=skill,
    )


class HypothesisDefinition(BaseModel):
    """LLMs may propose this semantic definition; they do not own effect estimates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str
    statement: str
    feature_definition: str
    expected_direction: Literal["positive", "negative"]
    evaluation_criterion: str
    proposed_at: datetime
    proposed_by: Literal["llm", "human", "deterministic"] = "llm"

    @field_validator("proposed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @property
    def definition_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


class HypothesisEvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    discovery_sessions: int = Field(default=0, ge=0)
    discovery_observations: int = Field(default=0, ge=0)
    forward_sessions: int = Field(default=0, ge=0)
    forward_observations: int = Field(default=0, ge=0)
    effect_estimate_pp: Decimal | None = None
    clustered_ci_low_pp: Decimal | None = None
    clustered_ci_high_pp: Decimal | None = None
    forward_brier_delta: Decimal | None = None
    direction_stability: Decimal | None = Field(default=None, ge=0, le=1)
    category_coverage_acceptable: bool = False
    statistical_method: str | None = None


class HypothesisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = HYPOTHESIS_POLICY_VERSION
    definition: HypothesisDefinition
    status: HypothesisStatus = "PROPOSED"
    definition_frozen_at: datetime | None = None
    evidence: HypothesisEvidenceSummary = Field(default_factory=HypothesisEvidenceSummary)
    last_transition_at: datetime

    @field_validator("definition_frozen_at", "last_transition_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)


_HYPOTHESIS_TRANSITIONS: dict[HypothesisStatus, HypothesisStatus] = {
    "PROPOSED": "OBSERVATIONAL",
    "OBSERVATIONAL": "FROZEN_CANDIDATE",
    "FROZEN_CANDIDATE": "FORWARD_VALIDATION",
    "FORWARD_VALIDATION": "CANDIDATE_FOR_PROMOTION",
    "CANDIDATE_FOR_PROMOTION": "SHADOW_CHALLENGER",
    "SHADOW_CHALLENGER": "PROMOTED",
}


def transition_hypothesis(
    record: HypothesisRecord,
    *,
    new_status: HypothesisStatus,
    transitioned_at: datetime,
    evidence: HypothesisEvidenceSummary | None = None,
) -> HypothesisRecord:
    if new_status != _HYPOTHESIS_TRANSITIONS.get(record.status):
        raise ValueError(f"invalid_hypothesis_transition:{record.status}->{new_status}")
    frozen_at = record.definition_frozen_at
    if new_status == "FROZEN_CANDIDATE" and frozen_at is None:
        frozen_at = _utc(transitioned_at)
    return record.model_copy(update={
        "status": new_status,
        "definition_frozen_at": frozen_at,
        "evidence": evidence or record.evidence,
        "last_transition_at": _utc(transitioned_at),
    })


class ChallengerComparison(BaseModel):
    """Deterministic/statistical output used by promotion policy, never LLM opinion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    champion_version: str
    challenger_version: str
    forward_sessions: int = Field(ge=0)
    forward_observations: int = Field(ge=0)
    brier_delta: Decimal | None = None
    log_loss_delta: Decimal | None = None
    calibration_error_delta: Decimal | None = None
    persistent_uptrend_metric_delta: Decimal | None = None
    portfolio_return_delta: Decimal | None = None
    max_drawdown_delta: Decimal | None = None
    execution_adjusted_return_delta: Decimal | None = None
    statistical_evidence_fingerprint: str


def frozen_climatology_baseline(*, prior_valid_outcomes: Sequence[bool], frozen_at: datetime) -> dict[str, Any]:
    return {
        "version": CLIMATOLOGY_BASELINE_VERSION,
        "frozen_at": _utc(frozen_at).isoformat(),
        "valid_prior_observation_count": len(prior_valid_outcomes),
        "p_close_above_open": str(climatology_probability(prior_valid_outcomes)) if prior_valid_outcomes else None,
    }
