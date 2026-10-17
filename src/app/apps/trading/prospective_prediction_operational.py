from __future__ import annotations

"""Operational hardening around frozen prospective-gap forecasts.

This module deliberately does not change prospective-gap-v3 or the frozen
prospective-gap-v4-shadow score. It connects existing market-data, deterministic
failed-selloff, outcome, actionability, and execution contracts so research
forecasts are not converted directly into fully-invested exposure.

Authority boundaries remain explicit:
- premarket forecast evidence is causal and immutable;
- post-open confirmation is a separate deterministic authority;
- execution economics are a separate authority;
- post-close recovered truth never rewrites prior live knowledge.
"""

import hashlib
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Final, Literal, Sequence, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .gapper_dataset import GapperCandidate
from .models import AdjustmentMode, MarketBar
from .prospective_prediction_evidence import (
    AnalysisSessionPrices,
    OutcomeMeasurementsV1,
    SelectedPriceEvent,
    VersionedOutcomeLabels,
)
from .prospective_prediction_scoring import (
    build_formal_outcome_labels,
    canonical_formal_5m_bars,
)
from .prospective_prediction_v4 import (
    Actionability,
    ConfirmationState,
    ConfirmationTransitionReceipt,
    FinvizFrozenCohort,
    PredictionEvidenceQuality,
    PremarketMarketStateSnapshot,
    TradeAuthorizationReceipt,
    actionability_from_confirmation,
    build_premarket_market_state,
    market_state_from_candidate,
    summarize_evidence_quality,
    transition_confirmation,
)
from .service import TradingMarketDataService
from .strategies import evaluate_gap_pullback
from .strategies.models import GapPullbackConfig, GapPullbackResult
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


PROSPECTIVE_OPERATIONAL_VERSION: Final = "prospective-gap-operational-v1"
RAW_5M_FALLBACK_PRICE_VERSION = "sip-analysis-prices-raw-5m-fallback-v1"
CONFIRMATION_NET_ALPHA_PORTFOLIO_VERSION: Final = "confirmation-net-alpha-capped-v1"

CORE_PREMARKET_FEATURES = ("gap_from_prior_close_pct",)
IMPORTANT_PREMARKET_FEATURES = (
    "premarket_move_since_first_catalyst_pct",
    "distance_from_premarket_vwap_pct",
    "distance_from_premarket_low_pct",
    "position_in_premarket_range",
    "prior_1d_return_pct",
    "prior_3d_return_pct",
    "float_turnover",
    "late_premarket_acceleration",
    "late_premarket_volume_share",
)


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


class OperationalPremarketState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-operational-v1"] = PROSPECTIVE_OPERATIONAL_VERSION
    instrument_id: str
    source_mode: Literal["CANONICAL_RAW_1M", "CANDIDATE_FALLBACK"]
    binding_id: str | None = None
    raw_bar_count: int = Field(ge=0)
    market_state: PremarketMarketStateSnapshot
    evidence_quality: PredictionEvidenceQuality
    coverage_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    unresolved_gap_count: int = Field(default=0, ge=0)
    latest_bar_lag_seconds: int | None = Field(default=None, ge=0)
    late_window_bar_count: int = Field(default=0, ge=0)
    dataset_fingerprint: str | None = None
    warnings: tuple[str, ...] = ()


def load_operational_premarket_state(
    *,
    market_service: TradingMarketDataService,
    cohort: FinvizFrozenCohort,
    candidate: GapperCandidate,
    snapshot_id: str,
    prediction_cutoff_at: datetime,
    frozen_at: datetime,
    first_catalyst_at: datetime | None = None,
    prior_1d_return_pct: Decimal | None = None,
    prior_3d_return_pct: Decimal | None = None,
) -> OperationalPremarketState:
    """Prefer canonical RAW 1m premarket bars and fail down to candidate evidence.

    The fallback is intentionally DEGRADED when rich tape-derived features are
    absent. Missing enrichments are never replaced with neutral values.
    """

    warnings: list[str] = []
    bars: Sequence[MarketBar] = ()
    knowledge_cutoff = min(_utc(prediction_cutoff_at), _utc(frozen_at))
    coverage_ratio: Decimal | None = None
    unresolved_gap_count = 0
    latest_bar_lag_seconds: int | None = None
    late_window_bar_count = 0
    dataset_fingerprint: str | None = None
    recovered_window = getattr(market_service, "recovered_window_bars", None)
    if callable(recovered_window):
        premarket_start = datetime.combine(
            cohort.session_date,
            time(4, 0),
            tzinfo=_ET,
        ).astimezone(timezone.utc)
        try:
            recovered = recovered_window(
                candidate.instrument_id,
                start=premarket_start,
                end=knowledge_cutoff,
                interval="1m",
                session="extended_pre",
                provider="yahoo",
                include_extended_hours=True,
                knowledge_mode="causal_replay",
                knowledge_cutoff=knowledge_cutoff,
            )
            bars = tuple(recovered.bars)
            coverage_ratio = Decimal(str(recovered.report.coverage_ratio))
            unresolved_gap_count = sum(
                gap.missing_bar_count for gap in recovered.report.unresolved_gaps
            )
            dataset_fingerprint = recovered.report.dataset_fingerprint
            latest_bar_lag_seconds = getattr(
                recovered.report,
                "latest_bar_lag_seconds",
                None,
            )
            late_window_bar_count = int(
                getattr(recovered.report, "late_window_bar_count", 0) or 0
            )
            if recovered.report.provider_error:
                warnings.append("PREMARKET_WINDOW_PROVIDER_ERROR")
            if unresolved_gap_count:
                warnings.append(f"PREMARKET_WINDOW_MISSING_BARS:{unresolved_gap_count}")
        except Exception as exc:
            warnings.append(f"PREMARKET_WINDOW_RECOVERY_FAILED:{type(exc).__name__}")

    if not bars:
        try:
            response = market_service.bars(
                candidate.instrument_id,
                "1m",
                500,
                candidate.binding_id,
            )
            bars = tuple(getattr(response, "bars", ()) or ())
        except Exception as exc:  # provider failure must not erase the forecast
            warnings.append(f"RAW_1M_FETCH_FAILED:{type(exc).__name__}")

    if bars:
        try:
            state = build_premarket_market_state(
                cohort=cohort,
                instrument_id=candidate.instrument_id,
                bars=bars,
                snapshot_id=snapshot_id,
                prediction_cutoff_at=knowledge_cutoff,
                frozen_at=frozen_at,
                prior_close=candidate.previous_close,
                float_shares=candidate.float_shares,
                first_catalyst_at=first_catalyst_at,
                prior_1d_return_pct=prior_1d_return_pct,
                prior_3d_return_pct=prior_3d_return_pct,
            )
            quality = summarize_evidence_quality(
                state,
                critical_features=CORE_PREMARKET_FEATURES,
                important_features=IMPORTANT_PREMARKET_FEATURES,
            )
            tape_quality_reasons: list[str] = []
            if coverage_ratio is not None and coverage_ratio < Decimal("0.90"):
                tape_quality_reasons.append("PREMARKET_WINDOW_COVERAGE_BELOW_90PCT")
            if len(bars) < 12:
                tape_quality_reasons.append("PREMARKET_REAL_BAR_COUNT_BELOW_12")
            if late_window_bar_count < 3:
                tape_quality_reasons.append("PREMARKET_LATE_WINDOW_BAR_COUNT_BELOW_3")
            if latest_bar_lag_seconds is None or latest_bar_lag_seconds > 300:
                tape_quality_reasons.append("PREMARKET_LATEST_BAR_STALE_OR_UNKNOWN")
            if unresolved_gap_count:
                tape_quality_reasons.append("PREMARKET_UNRESOLVED_PROVIDER_GAPS")
            if "PREMARKET_WINDOW_PROVIDER_ERROR" in warnings:
                tape_quality_reasons.append("PREMARKET_WINDOW_PROVIDER_ERROR")
            if tape_quality_reasons and quality.quality != "INSUFFICIENT":
                quality = PredictionEvidenceQuality(
                    quality="DEGRADED",
                    critical_features=quality.critical_features,
                    missing_critical_features=quality.missing_critical_features,
                    degraded_features=quality.degraded_features,
                    reasons=quality.reasons + tuple(
                        reason
                        for reason in tape_quality_reasons
                        if reason not in quality.reasons
                    ),
                )
            return OperationalPremarketState(
                instrument_id=candidate.instrument_id,
                source_mode="CANONICAL_RAW_1M",
                binding_id=candidate.binding_id,
                raw_bar_count=len(bars),
                market_state=state,
                evidence_quality=quality,
                coverage_ratio=coverage_ratio,
                unresolved_gap_count=unresolved_gap_count,
                latest_bar_lag_seconds=latest_bar_lag_seconds,
                late_window_bar_count=late_window_bar_count,
                dataset_fingerprint=dataset_fingerprint,
                warnings=tuple(warnings),
            )
        except Exception as exc:
            warnings.append(f"RAW_1M_ENRICHMENT_UNAVAILABLE:{type(exc).__name__}")

    fallback = market_state_from_candidate(
        cohort=cohort,
        candidate=candidate,
        snapshot_id=snapshot_id,
        prediction_cutoff_at=knowledge_cutoff,
        frozen_at=frozen_at,
    )
    quality = summarize_evidence_quality(
        fallback,
        critical_features=CORE_PREMARKET_FEATURES,
        important_features=IMPORTANT_PREMARKET_FEATURES,
    )
    return OperationalPremarketState(
        instrument_id=candidate.instrument_id,
        source_mode="CANDIDATE_FALLBACK",
        binding_id=candidate.binding_id,
        raw_bar_count=len(bars),
        market_state=fallback,
        evidence_quality=quality,
        coverage_ratio=coverage_ratio,
        unresolved_gap_count=unresolved_gap_count,
        dataset_fingerprint=dataset_fingerprint,
        warnings=tuple(warnings),
    )


class FormalOutcomeBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    session_date: date
    price_authority: Literal["DIRECT_SIP_TRADES", "RAW_SIP_5M_FALLBACK"]
    price_contract_version: str
    prices: AnalysisSessionPrices
    measurements: OutcomeMeasurementsV1
    labels: VersionedOutcomeLabels
    canonical_bar_count: int = Field(gt=0)
    session_boundary_complete: bool
    internal_gap_minutes: Decimal = Field(ge=0)


def raw_5m_fallback_analysis_prices(
    *,
    session_date: date,
    bars: Sequence[MarketBar],
) -> AnalysisSessionPrices:
    """Documented fallback when direct condition-filtered SIP events are absent."""

    canonical = canonical_formal_5m_bars(bars)
    if not canonical:
        raise ValueError("raw_5m_fallback_requires_canonical_bars")
    instrument_ids = {bar.instrument_id for bar in canonical}
    if len(instrument_ids) != 1:
        raise ValueError("raw_5m_fallback_requires_single_instrument")

    session_start = datetime.combine(session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    session_end = datetime.combine(
        session_date,
        regular_close_time(session_date),
        tzinfo=_ET,
    ).astimezone(timezone.utc)
    regular = [
        bar
        for bar in canonical
        if bar.is_final
        and bar.adjustment_mode == AdjustmentMode.RAW
        and bar.session == "regular"
        and session_start <= bar.start_time < session_end
    ]
    if not regular:
        raise ValueError("raw_5m_fallback_regular_session_unavailable")
    regular.sort(key=lambda bar: (bar.start_time, bar.end_time, bar.provider_sequence or -1))
    if regular[0].start_time != session_start:
        raise ValueError("raw_5m_fallback_open_boundary_missing")
    if regular[-1].end_time < session_end:
        raise ValueError("raw_5m_fallback_close_boundary_missing")

    first = regular[0]
    last = regular[-1]
    policy_version = RAW_5M_FALLBACK_PRICE_VERSION
    reconciliation = _hash(
        [
            (
                bar.provider,
                bar.provider_event_id,
                bar.provider_sequence,
                bar.ingestion_revision,
                bar.start_time.isoformat(),
                bar.end_time.isoformat(),
                str(bar.open),
                str(bar.close),
            )
            for bar in regular
        ]
    )
    return AnalysisSessionPrices(
        instrument_id=first.instrument_id,
        session_date=session_date,
        price_contract_version=RAW_5M_FALLBACK_PRICE_VERSION,
        open_event=SelectedPriceEvent(
            price=first.open,
            event_timestamp=first.start_time,
            received_timestamp=first.received_at,
            exchange=None,
            sip_feed=first.provider,
            provider_event_id=first.provider_event_id,
            sequence=first.provider_sequence,
            eligibility_policy_version=policy_version,
        ),
        close_event=SelectedPriceEvent(
            price=last.close,
            event_timestamp=last.end_time,
            received_timestamp=last.received_at,
            exchange=None,
            sip_feed=last.provider,
            provider_event_id=last.provider_event_id,
            sequence=last.provider_sequence,
            eligibility_policy_version=policy_version,
        ),
        outcome_state="FINAL",
        eligible_trade_count=len(regular),
        discarded_trade_count=0,
        reconciliation_fingerprint=reconciliation,
    )


def build_operational_formal_outcome(
    *,
    session_date: date,
    bars: Sequence[MarketBar],
    prices: AnalysisSessionPrices | None = None,
) -> FormalOutcomeBundle:
    """Always run the deterministic outcome derivation when formal 5m bars exist."""

    canonical = canonical_formal_5m_bars(bars)
    if prices is None:
        prices = raw_5m_fallback_analysis_prices(session_date=session_date, bars=canonical)
        authority: Literal["DIRECT_SIP_TRADES", "RAW_SIP_5M_FALLBACK"] = "RAW_SIP_5M_FALLBACK"
    else:
        authority = "DIRECT_SIP_TRADES"

    measurements, labels = build_formal_outcome_labels(prices=prices, bars=canonical)
    start = datetime.combine(session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    end = datetime.combine(
        session_date,
        regular_close_time(session_date),
        tzinfo=_ET,
    ).astimezone(timezone.utc)
    boundary_complete = bool(
        canonical
        and canonical[0].start_time <= start
        and canonical[-1].end_time >= end
    )
    return FormalOutcomeBundle(
        instrument_id=prices.instrument_id,
        session_date=session_date,
        price_authority=authority,
        price_contract_version=prices.price_contract_version,
        prices=prices,
        measurements=measurements,
        labels=labels,
        canonical_bar_count=len(canonical),
        session_boundary_complete=boundary_complete,
        internal_gap_minutes=measurements.halt_or_gap_minutes,
    )


def load_operational_formal_outcome(
    *,
    market_service: TradingMarketDataService,
    candidate: GapperCandidate,
    session_date: date,
    prices: AnalysisSessionPrices | None = None,
) -> FormalOutcomeBundle:
    bars: tuple[MarketBar, ...] = ()
    recovered = getattr(market_service, "recovered_bars", None)
    if callable(recovered):
        close_at = datetime.combine(
            session_date,
            regular_close_time(session_date),
            tzinfo=_ET,
        ).astimezone(timezone.utc)
        try:
            result = recovered(
                candidate.instrument_id,
                "5m",
                500,
                candidate.binding_id,
                session_date=session_date,
                as_of=close_at,
                knowledge_mode="retroactive_research",
                knowledge_cutoff=close_at,
            )
            bars = tuple(result.bars)
        except Exception:
            bars = ()
    if not bars:
        response = market_service.bars(
            candidate.instrument_id,
            "5m",
            500,
            candidate.binding_id,
        )
        bars = tuple(getattr(response, "bars", ()) or ())
    return build_operational_formal_outcome(
        session_date=session_date,
        bars=bars,
        prices=prices,
    )


class OperationalConfirmationEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    observed_at: datetime
    deterministic_state: str
    deterministic_reason_code: str
    final_confirmation_state: ConfirmationState
    actionability: Actionability
    receipts: tuple[ConfirmationTransitionReceipt, ...] = ()
    evaluated_bar_count: int = Field(ge=0)
    signal_entry_price: Decimal | None = None
    signal_stop_price: Decimal | None = None
    signal_target_price: Decimal | None = None
    signal_quality_score: int | None = None

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


_STRUCTURE_STATES = {"discovered", "qualified_gap", "opening_impulse"}
_PULLBACK_STATES = {
    "first_pullback",
    "first_low_confirmed",
    "bounce_high_confirmed",
    "second_pullback",
    "higher_low_confirmed",
    "vwap_reclaim",
    "lower_high_break",
    "breakout_hold",
}


def _target_confirmation_state(result: GapPullbackResult) -> ConfirmationState:
    if result.state == "entry_ready":
        return "CONFIRMED_LONG"
    if result.state == "rejected":
        return "INVALIDATED"
    if result.state == "expired":
        return "EXPIRED"
    if result.state in _PULLBACK_STATES:
        return "OBSERVE_PULLBACK"
    return "OBSERVE_INITIAL_STRUCTURE"


def _bar_ids(bars: Sequence[MarketBar]) -> tuple[str, ...]:
    output: list[str] = []
    for bar in bars:
        if not bar.is_final:
            continue
        output.append(
            bar.provider_event_id
            or f"{bar.provider}:{bar.interval}:{bar.start_time.isoformat()}:{bar.end_time.isoformat()}"
        )
    return tuple(output)


def _action_for_state(state: ConfirmationState) -> Actionability:
    if state == "CONFIRMED_LONG":
        return "ACT"
    if state in {
        "OBSERVE_INITIAL_STRUCTURE",
        "OBSERVE_PULLBACK",
        "WATCH",
        "SUSPENDED_DATA_QUALITY",
    }:
        return "WATCH"
    return "ABSTAIN"


def evaluate_operational_confirmation(
    *,
    candidate: GapperCandidate,
    bars: Sequence[MarketBar],
    observed_at: datetime,
    previous_state: ConfirmationState = "WAIT_OPEN",
    config: GapPullbackConfig | None = None,
    data_quality_ok: bool = True,
    data_quality_reason: str | None = None,
) -> OperationalConfirmationEvaluation:
    """Map the existing deterministic failed-selloff evaluator into v4 actionability."""

    observed_at = _utc(observed_at)
    finalized = tuple(
        sorted(
            (bar for bar in bars if bar.is_final and bar.end_time <= observed_at),
            key=lambda bar: (bar.start_time, bar.end_time),
        )
    )
    ids = _bar_ids(finalized)
    latest_finalized = finalized[-1].end_time if finalized else None

    if not data_quality_ok:
        if previous_state in {"CONFIRMED_LONG", "INVALIDATED", "EXPIRED"}:
            raise ValueError("terminal_confirmation_state_cannot_suspend")
        if previous_state == "SUSPENDED_DATA_QUALITY":
            return OperationalConfirmationEvaluation(
                instrument_id=candidate.instrument_id,
                observed_at=observed_at,
                deterministic_state="data_unavailable",
                deterministic_reason_code=data_quality_reason or "DATA_QUALITY_UNCERTAIN",
                final_confirmation_state=previous_state,
                actionability="WATCH",
                evaluated_bar_count=len(finalized),
            )
        receipt = transition_confirmation(
            instrument_id=candidate.instrument_id,
            previous_state=previous_state,
            new_state="SUSPENDED_DATA_QUALITY",
            transition_at=observed_at,
            trigger=data_quality_reason or "DATA_QUALITY_UNCERTAIN",
            bar_ids=ids[-3:],
            latest_finalized_bar_at=latest_finalized,
            reasons=(data_quality_reason or "DATA_QUALITY_UNCERTAIN",),
        )
        return OperationalConfirmationEvaluation(
            instrument_id=candidate.instrument_id,
            observed_at=observed_at,
            deterministic_state="data_unavailable",
            deterministic_reason_code=data_quality_reason or "DATA_QUALITY_UNCERTAIN",
            final_confirmation_state="SUSPENDED_DATA_QUALITY",
            actionability="WATCH",
            receipts=(receipt,),
            evaluated_bar_count=len(finalized),
        )

    result = evaluate_gap_pullback(candidate, finalized, config)
    target = _target_confirmation_state(result)
    current = previous_state
    receipts: list[ConfirmationTransitionReceipt] = []

    def step(new_state: ConfirmationState) -> None:
        nonlocal current
        if current == new_state:
            return
        receipt = transition_confirmation(
            instrument_id=candidate.instrument_id,
            previous_state=current,
            new_state=new_state,
            transition_at=observed_at,
            trigger=result.reason_code,
            bar_ids=ids[-5:],
            latest_finalized_bar_at=latest_finalized,
            reasons=(result.reason_code,),
        )
        receipts.append(receipt)
        current = new_state

    if current == "SUSPENDED_DATA_QUALITY":
        resume_target: ConfirmationState = (
            "OBSERVE_PULLBACK"
            if target in {"OBSERVE_PULLBACK", "CONFIRMED_LONG", "INVALIDATED"}
            else "OBSERVE_INITIAL_STRUCTURE"
        )
        step(resume_target)

    if target == "OBSERVE_INITIAL_STRUCTURE":
        if current == "WAIT_OPEN":
            step("OBSERVE_INITIAL_STRUCTURE")
        elif current == "WATCH":
            step("OBSERVE_INITIAL_STRUCTURE")
    elif target == "OBSERVE_PULLBACK":
        if current == "WAIT_OPEN":
            step("OBSERVE_INITIAL_STRUCTURE")
        if current in {"OBSERVE_INITIAL_STRUCTURE", "WATCH"}:
            step("OBSERVE_PULLBACK")
    elif target == "CONFIRMED_LONG":
        if current == "WAIT_OPEN":
            step("OBSERVE_INITIAL_STRUCTURE")
        if current in {"OBSERVE_INITIAL_STRUCTURE", "WATCH"}:
            step("OBSERVE_PULLBACK")
        if current == "OBSERVE_PULLBACK":
            step("CONFIRMED_LONG")
    elif target == "INVALIDATED":
        if current == "WAIT_OPEN":
            step("OBSERVE_INITIAL_STRUCTURE")
        if current in {"OBSERVE_INITIAL_STRUCTURE", "OBSERVE_PULLBACK", "WATCH"}:
            step("INVALIDATED")
    elif target == "EXPIRED":
        if current == "WAIT_OPEN":
            step("EXPIRED")
        elif current in {
            "OBSERVE_INITIAL_STRUCTURE",
            "OBSERVE_PULLBACK",
            "WATCH",
            "SUSPENDED_DATA_QUALITY",
        }:
            step("EXPIRED")

    signal = result.signal
    actionability = (
        actionability_from_confirmation(receipts[-1]).actionability
        if receipts
        else _action_for_state(current)
    )
    return OperationalConfirmationEvaluation(
        instrument_id=candidate.instrument_id,
        observed_at=observed_at,
        deterministic_state=result.state,
        deterministic_reason_code=result.reason_code,
        final_confirmation_state=current,
        actionability=actionability,
        receipts=tuple(receipts),
        evaluated_bar_count=result.evaluated_bar_count,
        signal_entry_price=signal.entry_price if signal is not None else None,
        signal_stop_price=signal.stop_price if signal is not None else None,
        signal_target_price=signal.target_price if signal is not None else None,
        signal_quality_score=signal.quality_score if signal is not None else None,
    )


class ShadowPortfolioPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    forecast_fingerprint: str
    allocation: Decimal = Field(gt=0)
    weight: Decimal = Field(gt=0, le=1)
    expected_net_return: Decimal = Field(gt=0)
    authorization_decision_at: datetime

    @field_validator("authorization_decision_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


class CashPreservingShadowPortfolio(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_version: Literal[
        "confirmation-net-alpha-capped-v1"
    ] = CONFIRMATION_NET_ALPHA_PORTFOLIO_VERSION
    starting_equity: Decimal = Field(gt=0)
    max_positions: int = Field(ge=1)
    max_position_fraction: Decimal = Field(gt=0, le=1)
    positions: tuple[ShadowPortfolioPosition, ...]
    cash: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def conserve(self):
        allocated = sum((position.allocation for position in self.positions), Decimal("0"))
        if abs(self.starting_equity - allocated - self.cash) > Decimal("0.01"):
            raise ValueError("shadow_portfolio_must_conserve_equity")
        if len(self.positions) > self.max_positions:
            raise ValueError("shadow_portfolio_exceeds_max_positions")
        for position in self.positions:
            if position.weight > self.max_position_fraction:
                raise ValueError("shadow_portfolio_position_exceeds_cap")
        return self


def build_cash_preserving_shadow_portfolio(
    authorizations: Sequence[TradeAuthorizationReceipt],
    *,
    starting_equity: Decimal = Decimal("1000"),
    max_positions: int = 3,
    max_position_fraction: Decimal = Decimal("0.20"),
) -> CashPreservingShadowPortfolio:
    """Allocate only confirmed positive-net-alpha LONG receipts; never force full use."""

    if max_positions < 1:
        raise ValueError("max_positions_must_be_positive")
    if not Decimal("0") < max_position_fraction <= Decimal("1"):
        raise ValueError("max_position_fraction_out_of_range")

    eligible = [
        receipt
        for receipt in authorizations
        if receipt.decision == "LONG"
        and receipt.net_expected_return is not None
        and receipt.net_expected_return > 0
        and receipt.notional > 0
    ]
    eligible.sort(
        key=lambda receipt: (
            receipt.net_expected_return or Decimal("-999"),
            receipt.decision_at,
            receipt.forecast_fingerprint,
        ),
        reverse=True,
    )

    cap = starting_equity * max_position_fraction
    remaining = starting_equity
    positions: list[ShadowPortfolioPosition] = []
    for receipt in eligible[:max_positions]:
        available_cap = min(cap, remaining, receipt.notional)
        if receipt.max_positive_alpha_notional is not None:
            available_cap = min(available_cap, receipt.max_positive_alpha_notional)
        if available_cap <= 0:
            continue
        positions.append(
            ShadowPortfolioPosition(
                instrument_id=receipt.instrument_id,
                forecast_fingerprint=receipt.forecast_fingerprint,
                allocation=available_cap,
                weight=available_cap / starting_equity,
                expected_net_return=cast(Decimal, receipt.net_expected_return),
                authorization_decision_at=receipt.decision_at,
            )
        )
        remaining -= available_cap
        if remaining <= 0:
            break

    return CashPreservingShadowPortfolio(
        starting_equity=starting_equity,
        max_positions=max_positions,
        max_position_fraction=max_position_fraction,
        positions=tuple(positions),
        cash=remaining,
    )


__all__ = [
    "CONFIRMATION_NET_ALPHA_PORTFOLIO_VERSION",
    "CashPreservingShadowPortfolio",
    "FormalOutcomeBundle",
    "OperationalConfirmationEvaluation",
    "OperationalPremarketState",
    "PROSPECTIVE_OPERATIONAL_VERSION",
    "RAW_5M_FALLBACK_PRICE_VERSION",
    "ShadowPortfolioPosition",
    "build_cash_preserving_shadow_portfolio",
    "build_operational_formal_outcome",
    "evaluate_operational_confirmation",
    "load_operational_formal_outcome",
    "load_operational_premarket_state",
    "raw_5m_fallback_analysis_prices",
]
