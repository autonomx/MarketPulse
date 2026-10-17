from __future__ import annotations

"""Forward-only post-open action layer for prospective-gap v4.2.

This module never changes the frozen premarket probability. It converts a
PRODUCED v4.2 forecast into a watch classification, then evaluates finalized
regular-session one-minute structure between 09:30 and 10:00 ET.

Scientific boundary:
- premarket forecast quality and action quality remain separate;
- 09:30-09:35 is observe-only;
- 09:35-09:40 permits only exceptional early confirmation;
- 09:40-09:45 is the primary confirmation window;
- 09:45-10:00 is a higher-bar secondary window;
- the original premarket thesis expires at 10:00 ET;
- execution economics are required before capital is authorized.
"""

from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any, Final, Literal, Sequence, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import MarketBar
from .prospective_prediction_v4 import (
    ConfirmationState,
    ExecutionCostInput,
    GrossReturnDistribution,
    NetReturnDistribution,
    apply_execution_costs,
)
from .prospective_prediction_v42 import V42Forecast
from app.apps.trading.us_equity_calendar import EASTERN as _ET


V42_ACTION_VERSION: Final = "prospective-gap-v4.2-action-v1"
PORTFOLIO_F_VERSION: Final = "prospective-gap-portfolio-f-v1"

V42WatchClass = Literal["REJECT", "WATCH", "HIGH_PRIORITY_WATCH"]
V42ActionState = Literal[
    "OBSERVE_ONLY",
    "WATCH",
    "STRUCTURE_CONFIRMED",
    "INVALIDATED",
    "EXPIRED",
    "SUSPENDED_DATA_QUALITY",
]
V42DecisionWindow = Literal["OBSERVE", "EARLY", "PRIMARY", "SECONDARY", "EXPIRED"]
V42TradeDecision = Literal["LONG", "NO_TRADE", "WATCH"]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("v42_action_timestamp_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def _clamp01(value: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), value))


def _bar_id(bar: MarketBar) -> str:
    return str(
        bar.provider_event_id
        or f"{bar.instrument_id}:{bar.interval}:{bar.start_time.astimezone(timezone.utc).isoformat()}"
    )


def _session_time(value: datetime) -> time:
    return _utc(value).astimezone(_ET).time().replace(tzinfo=None)


class V42ActionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-v4.2-action-v1"] = V42_ACTION_VERSION

    observe_only_until_et: time = time(9, 35)
    early_window_end_et: time = time(9, 40)
    primary_window_end_et: time = time(9, 45)
    hard_expiry_et: time = time(10, 0)

    early_confirmation_threshold: Decimal = Field(default=Decimal("0.85"), ge=0, le=1)
    primary_confirmation_threshold: Decimal = Field(default=Decimal("0.72"), ge=0, le=1)
    secondary_confirmation_threshold: Decimal = Field(default=Decimal("0.78"), ge=0, le=1)

    minimum_volume_ratio: Decimal = Field(default=Decimal("0.80"), ge=0)
    minimum_trade_quality: Decimal = Field(default=Decimal("0.25"), ge=0, le=1)
    minimum_net_expected_return: Decimal = Decimal("0.005")
    minimum_net_q10: Decimal = Decimal("-0.08")
    maximum_p_return_lt_minus_5pct: Decimal = Field(default=Decimal("0.55"), ge=0, le=1)
    maximum_total_cost_bps_for_quality: Decimal = Field(default=Decimal("300"), gt=0)

    reject_probability_below: Decimal = Field(default=Decimal("0.30"), ge=0, le=1)
    reject_expected_return_below: Decimal = Decimal("-0.05")
    reject_tail_probability_at_or_above: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    high_priority_probability_at_or_above: Decimal = Field(default=Decimal("0.55"), ge=0, le=1)
    high_priority_expected_return_above: Decimal = Decimal("0.01")
    high_priority_tail_probability_below: Decimal = Field(default=Decimal("0.35"), ge=0, le=1)

    starting_equity: Decimal = Field(default=Decimal("1000"), gt=0)
    max_positions: int = Field(default=3, ge=1, le=10)
    max_position_fraction: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)

    estimated_entry_slippage_bps: Decimal = Field(default=Decimal("25"), ge=0)
    estimated_entry_impact_bps: Decimal = Field(default=Decimal("10"), ge=0)
    estimated_exit_slippage_bps: Decimal = Field(default=Decimal("25"), ge=0)
    estimated_exit_impact_bps: Decimal = Field(default=Decimal("10"), ge=0)
    estimated_round_trip_commission_bps: Decimal = Field(default=Decimal("0"), ge=0)

    @model_validator(mode="after")
    def ordered_windows(self):
        if not (
            self.observe_only_until_et
            < self.early_window_end_et
            < self.primary_window_end_et
            < self.hard_expiry_et
        ):
            raise ValueError("v42_action_windows_must_be_strictly_ordered")
        return self


DEFAULT_V42_ACTION_POLICY = V42ActionPolicy()


class V42WatchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    forecast_fingerprint: str
    classification: V42WatchClass
    reasons: tuple[str, ...] = ()


def classify_v42_watch(
    forecast: V42Forecast,
    *,
    policy: V42ActionPolicy = DEFAULT_V42_ACTION_POLICY,
) -> V42WatchDecision:
    distribution = forecast.return_distribution
    reasons: list[str] = []
    if forecast.p_close_above_open < policy.reject_probability_below:
        reasons.append("PREMARKET_DIRECTION_PROBABILITY_TOO_LOW")
    if distribution.expected_return <= policy.reject_expected_return_below:
        reasons.append("PREMARKET_EXPECTED_RETURN_TOO_LOW")
    if distribution.p_return_lt_minus_5pct >= policy.reject_tail_probability_at_or_above:
        reasons.append("PREMARKET_DOWNSIDE_TAIL_TOO_HIGH")
    if reasons:
        classification: V42WatchClass = "REJECT"
    elif (
        forecast.p_close_above_open >= policy.high_priority_probability_at_or_above
        and distribution.expected_return > policy.high_priority_expected_return_above
        and distribution.p_return_lt_minus_5pct < policy.high_priority_tail_probability_below
    ):
        classification = "HIGH_PRIORITY_WATCH"
        reasons.append("HIGH_PRIORITY_PREMARKET_EDGE")
    else:
        classification = "WATCH"
        reasons.append("PREMARKET_EDGE_REQUIRES_POST_OPEN_CONFIRMATION")
    return V42WatchDecision(
        instrument_id=forecast.instrument_id,
        forecast_fingerprint=forecast.immutable_fingerprint,
        classification=classification,
        reasons=tuple(reasons),
    )


class V42ActionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-v4.2-action-v1"] = V42_ACTION_VERSION
    instrument_id: str
    forecast_fingerprint: str
    evaluated_at: datetime
    decision_window: V42DecisionWindow
    state: V42ActionState
    watch_classification: V42WatchClass
    finalized_bar_count: int = Field(ge=0)

    open_price: Decimal | None = Field(default=None, gt=0)
    current_price: Decimal | None = Field(default=None, gt=0)
    session_vwap: Decimal | None = Field(default=None, gt=0)
    opening_range_low: Decimal | None = Field(default=None, gt=0)
    opening_range_high: Decimal | None = Field(default=None, gt=0)

    higher_low: bool = False
    vwap_held_or_reclaimed: bool = False
    pullback_high_broken: bool = False
    opening_range_support: bool = False
    volume_ratio: Decimal | None = Field(default=None, ge=0)
    five_minute_return: Decimal | None = None
    ten_minute_return: Decimal | None = None

    confirmation_strength: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    timing_quality: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    remaining_upside_quality: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    execution_quality: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    trade_quality: Decimal = Field(default=Decimal("0"), ge=0, le=1)

    gross_remaining_distribution: GrossReturnDistribution | None = None
    net_remaining_distribution: NetReturnDistribution | None = None
    evidence_bar_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @field_validator("evaluated_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


def _decision_window(
    evaluated_at: datetime,
    policy: V42ActionPolicy,
) -> V42DecisionWindow:
    local = _session_time(evaluated_at)
    if local < policy.observe_only_until_et:
        return "OBSERVE"
    if local < policy.early_window_end_et:
        return "EARLY"
    if local < policy.primary_window_end_et:
        return "PRIMARY"
    if local < policy.hard_expiry_et:
        return "SECONDARY"
    return "EXPIRED"


def _window_threshold(window: V42DecisionWindow, policy: V42ActionPolicy) -> Decimal:
    if window == "EARLY":
        return policy.early_confirmation_threshold
    if window == "PRIMARY":
        return policy.primary_confirmation_threshold
    if window == "SECONDARY":
        return policy.secondary_confirmation_threshold
    return Decimal("1")


def _timing_quality(
    evaluated_at: datetime,
    policy: V42ActionPolicy,
) -> Decimal:
    local = _session_time(evaluated_at)
    if local < policy.observe_only_until_et:
        return Decimal("0")
    if local < policy.early_window_end_et:
        return Decimal("1")
    if local < policy.primary_window_end_et:
        return Decimal("0.95")
    if local >= policy.hard_expiry_et:
        return Decimal("0")
    start = (
        policy.primary_window_end_et.hour * 60
        + policy.primary_window_end_et.minute
    )
    end = policy.hard_expiry_et.hour * 60 + policy.hard_expiry_et.minute
    current = local.hour * 60 + local.minute + Decimal(local.second) / Decimal("60")
    progress = Decimal(str((current - start) / (end - start)))
    return _clamp01(Decimal("0.90") - progress * Decimal("0.30"))


def _session_vwap(bars: Sequence[MarketBar]) -> Decimal | None:
    volume = sum((max(Decimal("0"), bar.volume) for bar in bars), Decimal("0"))
    if volume <= 0:
        return None
    notional = sum(
        (
            ((bar.high + bar.low + bar.close) / Decimal("3"))
            * max(Decimal("0"), bar.volume)
            for bar in bars
        ),
        Decimal("0"),
    )
    return notional / volume


def _return_from_current(
    open_price: Decimal,
    current_price: Decimal,
    open_to_close_return: Decimal,
) -> Decimal:
    target_close = open_price * (Decimal("1") + open_to_close_return)
    return target_close / current_price - Decimal("1")


def remaining_distribution_from_current(
    *,
    forecast: V42Forecast,
    open_price: Decimal,
    current_price: Decimal,
) -> GrossReturnDistribution:
    source = forecast.return_distribution
    return GrossReturnDistribution(
        q10=_return_from_current(open_price, current_price, source.q10),
        q50=_return_from_current(open_price, current_price, source.q50),
        q90=_return_from_current(open_price, current_price, source.q90),
        expected_return=_return_from_current(
            open_price,
            current_price,
            source.expected_return,
        ),
        expected_shortfall_10pct=_return_from_current(
            open_price,
            current_price,
            source.expected_shortfall_10pct,
        ),
        p_return_gt_2pct=source.p_return_gt_2pct,
        p_return_lt_minus_5pct=source.p_return_lt_minus_5pct,
    )


def _trajectory_return(bars: Sequence[MarketBar], lookback: int) -> Decimal | None:
    if len(bars) <= lookback:
        return None
    base = bars[-lookback - 1].close
    if base <= 0:
        return None
    return bars[-1].close / base - Decimal("1")


def evaluate_v42_post_open_action(
    *,
    forecast: V42Forecast,
    watch: V42WatchDecision,
    bars: Sequence[MarketBar],
    evaluated_at: datetime,
    data_quality_ok: bool,
    execution_cost: ExecutionCostInput | None,
    shared_confirmation_state: ConfirmationState,
    policy: V42ActionPolicy = DEFAULT_V42_ACTION_POLICY,
    data_quality_reasons: Sequence[str] = (),
) -> V42ActionSnapshot:
    evaluated_at = _utc(evaluated_at)
    if watch.instrument_id != forecast.instrument_id:
        raise ValueError("v42_action_watch_instrument_mismatch")
    if watch.forecast_fingerprint != forecast.immutable_fingerprint:
        raise ValueError("v42_action_watch_forecast_mismatch")
    local_session_date = evaluated_at.astimezone(_ET).date()
    if local_session_date != forecast.session_date:
        raise ValueError("v42_action_session_date_mismatch")
    if execution_cost is not None and _utc(execution_cost.decision_at) != evaluated_at:
        raise ValueError("v42_action_execution_cost_time_mismatch")
    window = _decision_window(evaluated_at, policy)
    if shared_confirmation_state == "INVALIDATED":
        return V42ActionSnapshot(
            instrument_id=forecast.instrument_id,
            forecast_fingerprint=forecast.immutable_fingerprint,
            evaluated_at=evaluated_at,
            decision_window=window,
            state="INVALIDATED",
            watch_classification=watch.classification,
            finalized_bar_count=0,
            reasons=("SHARED_FAILED_SELLOFF_CONFIRMATION_INVALIDATED",),
        )
    if shared_confirmation_state == "EXPIRED":
        return V42ActionSnapshot(
            instrument_id=forecast.instrument_id,
            forecast_fingerprint=forecast.immutable_fingerprint,
            evaluated_at=evaluated_at,
            decision_window=window,
            state="EXPIRED",
            watch_classification=watch.classification,
            finalized_bar_count=0,
            reasons=("SHARED_FAILED_SELLOFF_CONFIRMATION_EXPIRED",),
        )

    if watch.classification == "REJECT":
        return V42ActionSnapshot(
            instrument_id=forecast.instrument_id,
            forecast_fingerprint=forecast.immutable_fingerprint,
            evaluated_at=evaluated_at,
            decision_window=window,
            state="INVALIDATED",
            watch_classification=watch.classification,
            finalized_bar_count=0,
            reasons=watch.reasons + ("PREMARKET_WATCH_REJECTED",),
        )

    if window == "EXPIRED":
        return V42ActionSnapshot(
            instrument_id=forecast.instrument_id,
            forecast_fingerprint=forecast.immutable_fingerprint,
            evaluated_at=evaluated_at,
            decision_window=window,
            state="EXPIRED",
            watch_classification=watch.classification,
            finalized_bar_count=0,
            reasons=("PREMARKET_THESIS_EXPIRED_AT_10_ET",),
        )

    if not data_quality_ok:
        return V42ActionSnapshot(
            instrument_id=forecast.instrument_id,
            forecast_fingerprint=forecast.immutable_fingerprint,
            evaluated_at=evaluated_at,
            decision_window=window,
            state="SUSPENDED_DATA_QUALITY",
            watch_classification=watch.classification,
            finalized_bar_count=0,
            reasons=tuple(data_quality_reasons) or ("CURRENT_CONFIRMATION_DATA_INVALID",),
        )

    session_open = datetime.combine(local_session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    finalized = tuple(
        sorted(
            (
                bar
                for bar in bars
                if bar.instrument_id == forecast.instrument_id
                and bar.is_final
                and bar.session == "regular"
                and session_open <= bar.start_time
                and bar.end_time <= evaluated_at
            ),
            key=lambda bar: (bar.start_time, bar.end_time),
        )
    )
    if not finalized:
        return V42ActionSnapshot(
            instrument_id=forecast.instrument_id,
            forecast_fingerprint=forecast.immutable_fingerprint,
            evaluated_at=evaluated_at,
            decision_window=window,
            state="OBSERVE_ONLY" if window == "OBSERVE" else "WATCH",
            watch_classification=watch.classification,
            finalized_bar_count=0,
            reasons=("WAITING_FOR_FINALIZED_REGULAR_BARS",),
        )

    open_price = finalized[0].open
    latest = finalized[-1]
    current_price = latest.close
    vwap = _session_vwap(finalized)

    first_five_end = session_open.replace(minute=35)
    opening_bars = tuple(bar for bar in finalized if bar.end_time <= first_five_end)
    if not opening_bars:
        opening_bars = finalized[: min(5, len(finalized))]
    opening_low = min(bar.low for bar in opening_bars)
    opening_high = max(bar.high for bar in opening_bars)
    opening_mid = (opening_low + opening_high) / Decimal("2")

    recent_pullback = finalized[-3:-1]
    older = finalized[:-3]
    higher_low = bool(recent_pullback and older) and (
        min(bar.low for bar in recent_pullback)
        > min(bar.low for bar in older)
    )
    vwap_ok = vwap is not None and current_price >= vwap
    pullback_high = max((bar.high for bar in recent_pullback), default=opening_high)
    pullback_high_broken = current_price > pullback_high
    opening_support = current_price >= opening_mid

    prior_volume = finalized[:-1][-5:]
    avg_prior_volume = (
        sum((max(Decimal("0"), bar.volume) for bar in prior_volume), Decimal("0"))
        / Decimal(len(prior_volume))
        if prior_volume
        else Decimal("0")
    )
    volume_ratio = (
        max(Decimal("0"), latest.volume) / avg_prior_volume
        if avg_prior_volume > 0
        else Decimal("0")
    )

    five_return = _trajectory_return(finalized, 5)
    ten_return = _trajectory_return(finalized, 10)
    trajectory_positive = (
        Decimal("1")
        if (
            (five_return is not None and five_return > 0)
            and (ten_return is None or ten_return >= Decimal("-0.01"))
        )
        else Decimal("0")
    )
    volume_score = _clamp01(volume_ratio / max(policy.minimum_volume_ratio, Decimal("0.01")))

    confirmation_strength = _clamp01(
        (Decimal("0.20") if higher_low else Decimal("0"))
        + (Decimal("0.20") if vwap_ok else Decimal("0"))
        + (Decimal("0.20") if pullback_high_broken else Decimal("0"))
        + (Decimal("0.10") if opening_support else Decimal("0"))
        + volume_score * Decimal("0.15")
        + trajectory_positive * Decimal("0.15")
    )

    gross_remaining = remaining_distribution_from_current(
        forecast=forecast,
        open_price=open_price,
        current_price=current_price,
    )
    original_positive_q90 = max(
        Decimal("0.02"),
        forecast.return_distribution.q90,
    )
    remaining_upside_quality = _clamp01(
        max(Decimal("0"), gross_remaining.q90) / original_positive_q90
    )
    timing_quality = _timing_quality(evaluated_at, policy)

    net_remaining: NetReturnDistribution | None = None
    execution_quality = Decimal("0")
    if execution_cost is not None:
        net_remaining = apply_execution_costs(gross_remaining, execution_cost)
        execution_quality = _clamp01(
            Decimal("1")
            - net_remaining.total_cost_bps / policy.maximum_total_cost_bps_for_quality
        )

    trade_quality = _clamp01(
        confirmation_strength
        * remaining_upside_quality
        * execution_quality
        * timing_quality
    )

    common: dict[str, Any] = dict(
        instrument_id=forecast.instrument_id,
        forecast_fingerprint=forecast.immutable_fingerprint,
        evaluated_at=evaluated_at,
        decision_window=window,
        watch_classification=watch.classification,
        finalized_bar_count=len(finalized),
        open_price=open_price,
        current_price=current_price,
        session_vwap=vwap,
        opening_range_low=opening_low,
        opening_range_high=opening_high,
        higher_low=higher_low,
        vwap_held_or_reclaimed=vwap_ok,
        pullback_high_broken=pullback_high_broken,
        opening_range_support=opening_support,
        volume_ratio=volume_ratio,
        five_minute_return=five_return,
        ten_minute_return=ten_return,
        confirmation_strength=confirmation_strength,
        timing_quality=timing_quality,
        remaining_upside_quality=remaining_upside_quality,
        execution_quality=execution_quality,
        trade_quality=trade_quality,
        gross_remaining_distribution=gross_remaining,
        net_remaining_distribution=net_remaining,
        evidence_bar_ids=tuple(_bar_id(bar) for bar in finalized[-10:]),
    )

    if window == "OBSERVE":
        return V42ActionSnapshot(
            **common,
            state="OBSERVE_ONLY",
            reasons=("OPENING_AUCTION_OBSERVE_ONLY_UNTIL_09_35_ET",),
        )

    if current_price < opening_low and (vwap is None or current_price < vwap):
        return V42ActionSnapshot(
            **common,
            state="INVALIDATED",
            reasons=("OPENING_RANGE_AND_VWAP_STRUCTURE_FAILED",),
        )

    threshold = _window_threshold(window, policy)
    required_structure = (
        shared_confirmation_state == "CONFIRMED_LONG"
        and confirmation_strength >= threshold
        and vwap_ok
        and pullback_high_broken
        and volume_ratio >= policy.minimum_volume_ratio
    )
    if required_structure:
        return V42ActionSnapshot(
            **common,
            state="STRUCTURE_CONFIRMED",
            reasons=(
                "SHARED_FAILED_SELLOFF_CONFIRMATION_CONFIRMED",
                "HIGHER_LOW_DIAGNOSTIC_CONFIRMED" if higher_low else "HIGHER_LOW_DIAGNOSTIC_NOT_CURRENT",
                "VWAP_HOLD_OR_RECLAIM_CONFIRMED",
                "PULLBACK_HIGH_BROKEN",
                "VOLUME_EXPANSION_CONFIRMED",
                f"CONFIRMATION_STRENGTH={confirmation_strength}",
            ),
        )

    return V42ActionSnapshot(
        **common,
        state="WATCH",
        reasons=(
            f"CONFIRMATION_THRESHOLD_NOT_MET:{confirmation_strength}<{threshold}",
        ),
    )


class V42AuthorizationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-v4.2-action-v1"] = V42_ACTION_VERSION
    instrument_id: str
    forecast_fingerprint: str
    decision_at: datetime
    decision: V42TradeDecision
    watch_classification: V42WatchClass
    confirmation_strength: Decimal = Field(ge=0, le=1)
    timing_quality: Decimal = Field(ge=0, le=1)
    remaining_upside_quality: Decimal = Field(ge=0, le=1)
    execution_quality: Decimal = Field(ge=0, le=1)
    trade_quality: Decimal = Field(ge=0, le=1)
    notional: Decimal = Field(ge=0)
    reference_price: Decimal | None = Field(default=None, gt=0)
    observed_spread_bps: Decimal | None = Field(default=None, ge=0)
    total_cost_bps: Decimal | None = Field(default=None, ge=0)
    net_expected_return: Decimal | None = None
    net_q10: Decimal | None = None
    reasons: tuple[str, ...] = ()

    @field_validator("decision_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


def authorize_v42_action(
    *,
    forecast: V42Forecast,
    watch: V42WatchDecision,
    snapshot: V42ActionSnapshot,
    execution_cost: ExecutionCostInput | None,
    shared_confirmation_state: ConfirmationState,
    policy: V42ActionPolicy = DEFAULT_V42_ACTION_POLICY,
) -> V42AuthorizationReceipt:
    base: dict[str, Any] = dict(
        instrument_id=forecast.instrument_id,
        forecast_fingerprint=forecast.immutable_fingerprint,
        decision_at=snapshot.evaluated_at,
        watch_classification=watch.classification,
        confirmation_strength=snapshot.confirmation_strength,
        timing_quality=snapshot.timing_quality,
        remaining_upside_quality=snapshot.remaining_upside_quality,
        execution_quality=snapshot.execution_quality,
        trade_quality=snapshot.trade_quality,
    )
    if snapshot.state in {"OBSERVE_ONLY", "WATCH", "SUSPENDED_DATA_QUALITY"}:
        return V42AuthorizationReceipt(
            **base,
            decision="WATCH",
            notional=Decimal("0"),
            reasons=snapshot.reasons,
        )
    if snapshot.state in {"INVALIDATED", "EXPIRED"}:
        return V42AuthorizationReceipt(
            **base,
            decision="NO_TRADE",
            notional=Decimal("0"),
            reasons=snapshot.reasons,
        )
    if shared_confirmation_state != "CONFIRMED_LONG":
        return V42AuthorizationReceipt(
            **base,
            decision="NO_TRADE",
            notional=Decimal("0"),
            reasons=("SHARED_FAILED_SELLOFF_CONFIRMATION_REQUIRED",),
        )
    if execution_cost is None or snapshot.net_remaining_distribution is None:
        return V42AuthorizationReceipt(
            **base,
            decision="NO_TRADE",
            notional=Decimal("0"),
            reasons=("EXECUTION_ECONOMICS_UNAVAILABLE",),
        )

    net = snapshot.net_remaining_distribution
    reasons: list[str] = []
    if snapshot.trade_quality < policy.minimum_trade_quality:
        reasons.append("TRADE_QUALITY_BELOW_THRESHOLD")
    if net.expected_return is None or net.expected_return <= policy.minimum_net_expected_return:
        reasons.append("EXPECTED_NET_ALPHA_TOO_LOW")
    if net.q10 < policy.minimum_net_q10:
        reasons.append("DOWNSIDE_TAIL_EXCEEDS_LIMIT")
    if (
        forecast.return_distribution.p_return_lt_minus_5pct
        > policy.maximum_p_return_lt_minus_5pct
    ):
        reasons.append("PREMARKET_DOWNSIDE_TAIL_PROBABILITY_TOO_HIGH")

    if reasons:
        decision: V42TradeDecision = "NO_TRADE"
        notional = Decimal("0")
    else:
        decision = "LONG"
        notional = min(
            execution_cost.notional,
            policy.starting_equity * policy.max_position_fraction,
        )

    mid = (execution_cost.observed_bid + execution_cost.observed_ask) / Decimal("2")
    spread_bps = (
        (execution_cost.observed_ask - execution_cost.observed_bid)
        / mid
        * Decimal("10000")
    )
    return V42AuthorizationReceipt(
        **base,
        decision=decision,
        notional=notional,
        reference_price=execution_cost.reference_price,
        observed_spread_bps=spread_bps,
        total_cost_bps=net.total_cost_bps,
        net_expected_return=net.expected_return,
        net_q10=net.q10,
        reasons=tuple(reasons),
    )


class PortfolioFPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    allocation: Decimal = Field(gt=0)
    weight: Decimal = Field(gt=0, le=1)
    trade_quality: Decimal = Field(ge=0, le=1)
    net_expected_return: Decimal
    authorization_decision_at: datetime
    reference_price: Decimal = Field(gt=0)
    total_cost_bps: Decimal = Field(ge=0)

    @field_validator("authorization_decision_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


class PortfolioF(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["prospective-gap-portfolio-f-v1"] = PORTFOLIO_F_VERSION
    starting_equity: Decimal = Field(gt=0)
    positions: tuple[PortfolioFPosition, ...]
    cash: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def conserve(self):
        allocated = sum((row.allocation for row in self.positions), Decimal("0"))
        if abs(self.starting_equity - allocated - self.cash) > Decimal("0.01"):
            raise ValueError("portfolio_f_must_conserve_equity")
        return self


def build_portfolio_f(
    authorizations: Sequence[V42AuthorizationReceipt],
    *,
    policy: V42ActionPolicy = DEFAULT_V42_ACTION_POLICY,
) -> PortfolioF:
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
        key=lambda row: (row.trade_quality, row.net_expected_return or Decimal("-999")),
        reverse=True,
    )
    remaining = policy.starting_equity
    cap = policy.starting_equity * policy.max_position_fraction
    positions: list[PortfolioFPosition] = []
    for row in eligible[: policy.max_positions]:
        allocation = min(cap, remaining, row.notional)
        if allocation <= 0:
            continue
        positions.append(
            PortfolioFPosition(
                instrument_id=row.instrument_id,
                allocation=allocation,
                weight=allocation / policy.starting_equity,
                trade_quality=row.trade_quality,
                net_expected_return=row.net_expected_return or Decimal("0"),
                authorization_decision_at=row.decision_at,
                reference_price=cast(Decimal, row.reference_price),
                total_cost_bps=cast(Decimal, row.total_cost_bps),
            )
        )
        remaining -= allocation
    return PortfolioF(
        starting_equity=policy.starting_equity,
        positions=tuple(positions),
        cash=remaining,
    )


__all__ = [
    "DEFAULT_V42_ACTION_POLICY",
    "PORTFOLIO_F_VERSION",
    "PortfolioF",
    "PortfolioFPosition",
    "V42_ACTION_VERSION",
    "V42ActionPolicy",
    "V42ActionSnapshot",
    "V42AuthorizationReceipt",
    "V42WatchDecision",
    "authorize_v42_action",
    "build_portfolio_f",
    "classify_v42_watch",
    "evaluate_v42_post_open_action",
    "remaining_distribution_from_current",
]
