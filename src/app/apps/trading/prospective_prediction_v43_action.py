from __future__ import annotations

"""Forward-only action overlay for prospective-gap v4.3.

The v4.2 post-open confirmation engine remains frozen. v4.3 consumes its
causal action snapshot and adds stricter higher-low confirmation, climatology
edge requirements, cohort-regime risk, and dynamic cash-preserving sizing.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Final, Literal, Sequence, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .prospective_prediction_v42 import V42Forecast
from .prospective_prediction_v42_action import (
    V42ActionSnapshot,
    V42AuthorizationReceipt,
)
from .prospective_prediction_v43 import V43Forecast


V43_ACTION_VERSION: Final = "prospective-gap-v4.3-action-v1"
PORTFOLIO_G_VERSION: Final = "prospective-gap-portfolio-g-v1"

V43WatchClass = Literal["REJECT", "WATCH", "HIGH_PRIORITY_WATCH"]
V43ActionState = Literal[
    "OBSERVE_ONLY",
    "WATCH",
    "STRUCTURE_CONFIRMED",
    "INVALIDATED",
    "EXPIRED",
    "SUSPENDED_DATA_QUALITY",
]
V43TradeDecision = Literal["LONG", "NO_TRADE", "WATCH"]


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _clamp01(value: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), value))


class V43ActionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-v4.3-action-v1"] = V43_ACTION_VERSION

    minimum_direction_probability: Decimal = Field(default=Decimal("0.50"), ge=0, le=1)
    minimum_probability_edge_over_climatology: Decimal = Decimal("0.08")
    minimum_remaining_upside_score: Decimal = Field(default=Decimal("0.30"), ge=0, le=1)
    maximum_repricing_complete_score: Decimal = Field(default=Decimal("0.82"), ge=0, le=1)
    maximum_exhaustion_score: Decimal = Field(default=Decimal("0.88"), ge=0, le=1)
    minimum_forecast_expected_return: Decimal = Decimal("0")

    high_priority_probability: Decimal = Field(default=Decimal("0.58"), ge=0, le=1)
    high_priority_edge: Decimal = Decimal("0.12")
    high_priority_remaining_upside: Decimal = Field(default=Decimal("0.50"), ge=0, le=1)
    high_priority_expected_return: Decimal = Decimal("0.015")

    normal_confirmation_threshold: Decimal = Field(default=Decimal("0.72"), ge=0, le=1)
    cautious_confirmation_threshold: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)
    high_exhaustion_confirmation_threshold: Decimal = Field(default=Decimal("0.88"), ge=0, le=1)

    normal_trade_quality_threshold: Decimal = Field(default=Decimal("0.25"), ge=0, le=1)
    cautious_trade_quality_threshold: Decimal = Field(default=Decimal("0.32"), ge=0, le=1)
    high_exhaustion_trade_quality_threshold: Decimal = Field(default=Decimal("0.40"), ge=0, le=1)
    minimum_remaining_upside_quality: Decimal = Field(default=Decimal("0.25"), ge=0, le=1)
    minimum_net_expected_return: Decimal = Decimal("0.0075")
    minimum_net_q10: Decimal = Decimal("-0.07")

    starting_equity: Decimal = Field(default=Decimal("1000"), gt=0)
    max_positions: int = Field(default=3, ge=1, le=10)
    max_position_fraction: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    normal_regime_size_multiplier: Decimal = Field(default=Decimal("1.00"), gt=0, le=1)
    cautious_regime_size_multiplier: Decimal = Field(default=Decimal("0.60"), gt=0, le=1)
    high_exhaustion_regime_size_multiplier: Decimal = Field(
        default=Decimal("0.35"), gt=0, le=1
    )
    minimum_quality_size_multiplier: Decimal = Field(default=Decimal("0.50"), gt=0, le=1)
    full_size_trade_quality: Decimal = Field(default=Decimal("0.50"), gt=0, le=1)

    @model_validator(mode="after")
    def ordered_thresholds(self):
        if not (
            self.normal_confirmation_threshold
            <= self.cautious_confirmation_threshold
            <= self.high_exhaustion_confirmation_threshold
        ):
            raise ValueError("v43_confirmation_thresholds_must_be_ordered")
        if not (
            self.normal_trade_quality_threshold
            <= self.cautious_trade_quality_threshold
            <= self.high_exhaustion_trade_quality_threshold
        ):
            raise ValueError("v43_trade_quality_thresholds_must_be_ordered")
        return self


DEFAULT_V43_ACTION_POLICY = V43ActionPolicy()


class V43WatchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    forecast_fingerprint: str
    base_v42_forecast_fingerprint: str
    classification: V43WatchClass
    reasons: tuple[str, ...] = ()


def classify_v43_watch(
    forecast: V43Forecast,
    *,
    policy: V43ActionPolicy = DEFAULT_V43_ACTION_POLICY,
) -> V43WatchDecision:
    reasons: list[str] = []
    edge = forecast.probability_edge_over_climatology

    if forecast.cohort_regime.classification == "INSUFFICIENT":
        reasons.append("COHORT_REGIME_INSUFFICIENT")
    if forecast.p_close_above_open < policy.minimum_direction_probability:
        reasons.append("DIRECTION_PROBABILITY_BELOW_ACTION_THRESHOLD")
    if edge is None:
        reasons.append("FROZEN_CLIMATOLOGY_REQUIRED_FOR_ACTION")
    elif edge < policy.minimum_probability_edge_over_climatology:
        reasons.append("EDGE_OVER_CLIMATOLOGY_TOO_SMALL")
    if forecast.remaining_upside_score < policy.minimum_remaining_upside_score:
        reasons.append("REMAINING_UPSIDE_TOO_LOW")
    if (
        forecast.premarket_repricing_complete_score
        > policy.maximum_repricing_complete_score
    ):
        reasons.append("PREMARKET_REPRICING_LARGELY_COMPLETE")
    if (
        forecast.extension_overlay.composite_exhaustion_score
        > policy.maximum_exhaustion_score
    ):
        reasons.append("PREMARKET_EXTENSION_EXHAUSTION_TOO_HIGH")
    if forecast.expected_return <= policy.minimum_forecast_expected_return:
        reasons.append("FORECAST_EXPECTED_RETURN_NOT_POSITIVE")

    if reasons:
        classification: V43WatchClass = "REJECT"
    elif (
        forecast.cohort_regime.classification != "HIGH_EXHAUSTION"
        and forecast.p_close_above_open >= policy.high_priority_probability
        and edge is not None
        and edge >= policy.high_priority_edge
        and forecast.remaining_upside_score >= policy.high_priority_remaining_upside
        and forecast.expected_return > policy.high_priority_expected_return
    ):
        classification = "HIGH_PRIORITY_WATCH"
        reasons.append("HIGH_PRIORITY_EDGE_WITH_REMAINING_UPSIDE")
    else:
        classification = "WATCH"
        reasons.append("EDGE_REQUIRES_POST_OPEN_STRUCTURE_CONFIRMATION")

    return V43WatchDecision(
        instrument_id=forecast.instrument_id,
        forecast_fingerprint=forecast.immutable_fingerprint,
        base_v42_forecast_fingerprint=forecast.base_v42_forecast_fingerprint,
        classification=classification,
        reasons=tuple(reasons),
    )


def _confirmation_threshold(
    forecast: V43Forecast,
    policy: V43ActionPolicy,
) -> Decimal:
    return {
        "NORMAL": policy.normal_confirmation_threshold,
        "CAUTIOUS": policy.cautious_confirmation_threshold,
        "HIGH_EXHAUSTION": policy.high_exhaustion_confirmation_threshold,
        "INSUFFICIENT": Decimal("1"),
    }[forecast.cohort_regime.classification]


def _trade_quality_threshold(
    forecast: V43Forecast,
    policy: V43ActionPolicy,
) -> Decimal:
    return {
        "NORMAL": policy.normal_trade_quality_threshold,
        "CAUTIOUS": policy.cautious_trade_quality_threshold,
        "HIGH_EXHAUSTION": policy.high_exhaustion_trade_quality_threshold,
        "INSUFFICIENT": Decimal("1"),
    }[forecast.cohort_regime.classification]


def _regime_size_multiplier(
    forecast: V43Forecast,
    policy: V43ActionPolicy,
) -> Decimal:
    return {
        "NORMAL": policy.normal_regime_size_multiplier,
        "CAUTIOUS": policy.cautious_regime_size_multiplier,
        "HIGH_EXHAUSTION": policy.high_exhaustion_regime_size_multiplier,
        "INSUFFICIENT": Decimal("0"),
    }[forecast.cohort_regime.classification]


class V43ActionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-v4.3-action-v1"] = V43_ACTION_VERSION
    instrument_id: str
    forecast_fingerprint: str
    base_v42_forecast_fingerprint: str
    base_action_fingerprint: str
    state: V43ActionState
    required_confirmation_strength: Decimal = Field(ge=0, le=1)
    confirmation_strength: Decimal = Field(ge=0, le=1)
    trade_quality: Decimal = Field(ge=0, le=1)
    higher_low_required: bool = True
    reasons: tuple[str, ...] = ()
    base_snapshot: V42ActionSnapshot


def evaluate_v43_post_open_action(
    *,
    forecast: V43Forecast,
    base_v42: V42Forecast,
    watch: V43WatchDecision,
    base_snapshot: V42ActionSnapshot,
    policy: V43ActionPolicy = DEFAULT_V43_ACTION_POLICY,
) -> V43ActionSnapshot:
    if forecast.instrument_id != base_v42.instrument_id:
        raise ValueError("v43_action_base_forecast_instrument_mismatch")
    if forecast.base_v42_forecast_fingerprint != base_v42.immutable_fingerprint:
        raise ValueError("v43_action_base_forecast_fingerprint_mismatch")
    if watch.forecast_fingerprint != forecast.immutable_fingerprint:
        raise ValueError("v43_action_watch_forecast_mismatch")
    if base_snapshot.forecast_fingerprint != base_v42.immutable_fingerprint:
        raise ValueError("v43_action_base_snapshot_forecast_mismatch")

    required = _confirmation_threshold(forecast, policy)
    common: dict[str, Any] = dict(
        instrument_id=forecast.instrument_id,
        forecast_fingerprint=forecast.immutable_fingerprint,
        base_v42_forecast_fingerprint=base_v42.immutable_fingerprint,
        base_action_fingerprint=_hash(base_snapshot.model_dump(mode="json")),
        required_confirmation_strength=required,
        confirmation_strength=base_snapshot.confirmation_strength,
        trade_quality=base_snapshot.trade_quality,
        base_snapshot=base_snapshot,
    )

    if watch.classification == "REJECT":
        return V43ActionSnapshot(
            **common,
            state="INVALIDATED",
            reasons=watch.reasons + ("V43_PREMARKET_ACTION_GATE_REJECTED",),
        )

    if base_snapshot.state in {
        "OBSERVE_ONLY",
        "SUSPENDED_DATA_QUALITY",
        "INVALIDATED",
        "EXPIRED",
    }:
        return V43ActionSnapshot(
            **common,
            state=base_snapshot.state,
            reasons=base_snapshot.reasons,
        )

    if base_snapshot.state != "STRUCTURE_CONFIRMED":
        return V43ActionSnapshot(
            **common,
            state="WATCH",
            reasons=base_snapshot.reasons + ("V43_WAITING_FOR_BASE_STRUCTURE_CONFIRMATION",),
        )

    reasons: list[str] = []
    if not base_snapshot.higher_low:
        reasons.append("HIGHER_LOW_REQUIRED_FOR_V43")
    if base_snapshot.confirmation_strength < required:
        reasons.append(
            f"REGIME_CONFIRMATION_STRENGTH_TOO_LOW:{base_snapshot.confirmation_strength}<{required}"
        )
    if reasons:
        return V43ActionSnapshot(
            **common,
            state="WATCH",
            reasons=tuple(reasons),
        )

    return V43ActionSnapshot(
        **common,
        state="STRUCTURE_CONFIRMED",
        reasons=(
            "FAILED_SELLOFF_STRUCTURE_CONFIRMED",
            "HIGHER_LOW_CONFIRMED",
            "VWAP_RECLAIM_OR_HOLD_CONFIRMED",
            "PULLBACK_HIGH_BREAK_CONFIRMED",
            f"COHORT_REGIME={forecast.cohort_regime.classification}",
        ),
    )


class V43AuthorizationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-v4.3-action-v1"] = V43_ACTION_VERSION
    instrument_id: str
    forecast_fingerprint: str
    decision: V43TradeDecision
    decision_at: datetime
    cohort_regime: str
    trade_quality: Decimal = Field(ge=0, le=1)
    probability_edge_over_climatology: Decimal | None
    notional: Decimal = Field(ge=0)
    reference_price: Decimal | None = Field(default=None, gt=0)
    total_cost_bps: Decimal | None = Field(default=None, ge=0)
    net_expected_return: Decimal | None = None
    net_q10: Decimal | None = None
    size_multiplier: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    reasons: tuple[str, ...] = ()

    @field_validator("decision_at")
    @classmethod
    def aware_decision_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("v43_authorization_decision_at_must_be_timezone_aware")
        return value.astimezone(timezone.utc)


def authorize_v43_action(
    *,
    forecast: V43Forecast,
    watch: V43WatchDecision,
    snapshot: V43ActionSnapshot,
    base_authorization: V42AuthorizationReceipt | None,
    policy: V43ActionPolicy = DEFAULT_V43_ACTION_POLICY,
) -> V43AuthorizationReceipt:
    base = snapshot.base_snapshot
    decision_at = base.evaluated_at
    common: dict[str, Any] = dict(
        instrument_id=forecast.instrument_id,
        forecast_fingerprint=forecast.immutable_fingerprint,
        decision_at=decision_at,
        cohort_regime=forecast.cohort_regime.classification,
        trade_quality=snapshot.trade_quality,
        probability_edge_over_climatology=forecast.probability_edge_over_climatology,
    )

    if snapshot.state in {"OBSERVE_ONLY", "WATCH", "SUSPENDED_DATA_QUALITY"}:
        return V43AuthorizationReceipt(
            **common,
            decision="WATCH",
            notional=Decimal("0"),
            reasons=snapshot.reasons,
        )
    if snapshot.state in {"INVALIDATED", "EXPIRED"}:
        return V43AuthorizationReceipt(
            **common,
            decision="NO_TRADE",
            notional=Decimal("0"),
            reasons=snapshot.reasons,
        )

    net = base.net_remaining_distribution
    reasons: list[str] = []
    if (
        base_authorization is None
        or base_authorization.instrument_id != forecast.instrument_id
        or base_authorization.forecast_fingerprint
        != forecast.base_v42_forecast_fingerprint
        or base_authorization.reference_price is None
        or base_authorization.total_cost_bps is None
        or net is None
    ):
        reasons.append("EXECUTION_ECONOMICS_UNAVAILABLE")
    edge = forecast.probability_edge_over_climatology
    if edge is None:
        reasons.append("FROZEN_CLIMATOLOGY_REQUIRED_FOR_ACTION")
    elif edge < policy.minimum_probability_edge_over_climatology:
        reasons.append("EDGE_OVER_CLIMATOLOGY_TOO_SMALL")
    if base.remaining_upside_quality < policy.minimum_remaining_upside_quality:
        reasons.append("DECISION_PRICE_REMAINING_UPSIDE_TOO_LOW")
    if snapshot.trade_quality < _trade_quality_threshold(forecast, policy):
        reasons.append("REGIME_ADJUSTED_TRADE_QUALITY_TOO_LOW")
    if net is not None:
        if net.expected_return is None or net.expected_return <= policy.minimum_net_expected_return:
            reasons.append("EXPECTED_NET_ALPHA_TOO_LOW")
        if net.q10 < policy.minimum_net_q10:
            reasons.append("DOWNSIDE_TAIL_EXCEEDS_LIMIT")

    if reasons:
        return V43AuthorizationReceipt(
            **common,
            decision="NO_TRADE",
            notional=Decimal("0"),
            reference_price=(
                base_authorization.reference_price
                if base_authorization is not None
                else None
            ),
            total_cost_bps=(
                base_authorization.total_cost_bps
                if base_authorization is not None
                else None
            ),
            net_expected_return=net.expected_return if net is not None else None,
            net_q10=net.q10 if net is not None else None,
            reasons=tuple(reasons),
        )

    regime_multiplier = _regime_size_multiplier(forecast, policy)
    quality_multiplier = max(
        policy.minimum_quality_size_multiplier,
        min(
            Decimal("1"),
            snapshot.trade_quality / policy.full_size_trade_quality,
        ),
    )
    size_multiplier = _clamp01(regime_multiplier * quality_multiplier)
    notional = (
        policy.starting_equity
        * policy.max_position_fraction
        * size_multiplier
    )
    assert net is not None
    assert base_authorization is not None
    assert base_authorization.reference_price is not None
    assert base_authorization.total_cost_bps is not None
    return V43AuthorizationReceipt(
        **common,
        decision="LONG",
        notional=notional,
        reference_price=base_authorization.reference_price,
        total_cost_bps=base_authorization.total_cost_bps,
        net_expected_return=net.expected_return,
        net_q10=net.q10,
        size_multiplier=size_multiplier,
        reasons=(
            f"REGIME_SIZE_MULTIPLIER={regime_multiplier}",
            f"QUALITY_SIZE_MULTIPLIER={quality_multiplier}",
        ),
    )


class PortfolioGPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    allocation: Decimal = Field(gt=0)
    weight: Decimal = Field(gt=0, le=1)
    trade_quality: Decimal = Field(ge=0, le=1)
    net_expected_return: Decimal
    reference_price: Decimal = Field(gt=0)
    total_cost_bps: Decimal = Field(ge=0)
    cohort_regime: str


class PortfolioG(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-portfolio-g-v1"] = PORTFOLIO_G_VERSION
    starting_equity: Decimal = Field(gt=0)
    positions: tuple[PortfolioGPosition, ...]
    cash: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def conserve(self):
        allocated = sum((row.allocation for row in self.positions), Decimal("0"))
        if abs(self.starting_equity - allocated - self.cash) > Decimal("0.01"):
            raise ValueError("portfolio_g_must_conserve_equity")
        return self


def build_portfolio_g(
    authorizations: Sequence[V43AuthorizationReceipt],
    *,
    policy: V43ActionPolicy = DEFAULT_V43_ACTION_POLICY,
) -> PortfolioG:
    eligible = [
        row
        for row in authorizations
        if row.decision == "LONG"
        and row.notional > 0
        and row.reference_price is not None
        and row.total_cost_bps is not None
        and row.net_expected_return is not None
    ]
    eligible.sort(
        key=lambda row: (
            row.trade_quality,
            row.probability_edge_over_climatology or Decimal("-999"),
            row.net_expected_return or Decimal("-999"),
        ),
        reverse=True,
    )
    remaining = policy.starting_equity
    positions: list[PortfolioGPosition] = []
    for row in eligible[: policy.max_positions]:
        allocation = min(row.notional, remaining)
        if allocation <= 0:
            continue
        positions.append(
            PortfolioGPosition(
                instrument_id=row.instrument_id,
                allocation=allocation,
                weight=allocation / policy.starting_equity,
                trade_quality=row.trade_quality,
                net_expected_return=row.net_expected_return or Decimal("0"),
                reference_price=cast(Decimal, row.reference_price),
                total_cost_bps=cast(Decimal, row.total_cost_bps),
                cohort_regime=row.cohort_regime,
            )
        )
        remaining -= allocation
    return PortfolioG(
        starting_equity=policy.starting_equity,
        positions=tuple(positions),
        cash=remaining,
    )


__all__ = [
    "DEFAULT_V43_ACTION_POLICY",
    "PORTFOLIO_G_VERSION",
    "PortfolioG",
    "PortfolioGPosition",
    "V43_ACTION_VERSION",
    "V43ActionPolicy",
    "V43ActionSnapshot",
    "V43AuthorizationReceipt",
    "V43WatchDecision",
    "authorize_v43_action",
    "build_portfolio_g",
    "classify_v43_watch",
    "evaluate_v43_post_open_action",
]
