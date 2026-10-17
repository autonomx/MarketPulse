from __future__ import annotations

"""Stateful AI-driven SHADOW policies for the Finviz Top-5 experiment.

This module contains no repository/order authority. The model may propose a
normalized research action; deterministic code owns position-state transitions,
risk/execution vetoes, and paper-execution-v2 fill simulation.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from .gapper_dataset import GapperCandidate
from .models import MarketBar
from .paper import (
    PaperExecutionPolicy,
    PaperMarketObservation,
    PaperOrder,
    paper_fill_decision,
)
from .strategy_intraday_learning import IntradayLearningSnapshot
from .indicator_signals import MultiTimeframeIndicatorContext


AIShadowPolicy = Literal["minute", "event"]
AIShadowAction = Literal["enter", "hold", "add", "reduce", "exit", "skip"]
AIShadowRegime = Literal[
    "unresolved",
    "trend_continuation",
    "gap_hold",
    "opening_fade_recovery",
    "failed_selloff",
    "squeeze_momentum",
    "distribution_fade",
    "high_variance",
]
AI_SHADOW_POLICY_VERSION = "ai-shadow-policy-v1"
MAX_NORMALIZED_UNITS = Decimal("1.5")
ADD_UNITS = Decimal("0.5")
REDUCE_UNITS = Decimal("0.5")


class AIShadowDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    action: AIShadowAction
    confidence: int = Field(ge=0, le=100)
    market_regime: AIShadowRegime
    expected_horizon_minutes: int = Field(ge=1, le=390)
    thesis: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    invalidation_price: Decimal | None = Field(default=None, gt=0)
    execution_authority: Literal[False] = False


class AIShadowBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decisions: tuple[AIShadowDecision, ...]


class AIShadowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: AIShadowPolicy
    decisions: tuple[AIShadowDecision, ...]
    provider: str
    model: str | None = None
    input_characters: int = Field(default=0, ge=0)
    output_characters: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    usage_source: Literal["provider", "estimated"] = "estimated"


class AIShadowPositionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: AIShadowPolicy
    instrument_id: str
    normalized_units: Decimal = Decimal("0")
    average_cost: Decimal | None = None
    trade_id: str | None = None
    entry_time: datetime | None = None
    first_entry_price: Decimal | None = None
    total_buy_notional: Decimal = Decimal("0")
    total_reference_buy_notional: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    execution_drag_dollars: Decimal = Decimal("0")
    fill_count: int = 0
    degraded_fill_count: int = Field(default=0, ge=0)

    @property
    def is_long(self) -> bool:
        return self.normalized_units > 0


class AIShadowFillSimulation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["ai-shadow-execution-v1"] = "ai-shadow-execution-v1"
    paper_execution_policy_version: str
    side: Literal["buy", "sell"]
    decision_at: datetime
    source_time: datetime | None = None
    requested_units: Decimal
    filled_units: Decimal = Decimal("0")
    reference_price: Decimal | None = None
    fill_price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    spread_bps: Decimal | None = None
    slippage_bps: Decimal
    should_fill: bool = False
    fill_reason: str
    execution_eligible: bool = False
    halted: bool = False
    observation_quality: Literal["book", "price_only", "bar_close_fallback"] = "book"
    hypothetical: bool = False
    assumed_friction_bps: Decimal | None = Field(default=None, ge=0)
    execution_authority: Literal[False] = False


def _usage_int(usage: Any, *keys: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    for key in keys:
        try:
            value = int(cast(Any, usage.get(key)))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _normalized_usage(
    usage: Any,
    *,
    input_characters: int,
    output_characters: int,
) -> tuple[int, int, int, Literal["provider", "estimated"]]:
    input_tokens = _usage_int(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_int(usage, "completion_tokens", "output_tokens")
    total_tokens = _usage_int(usage, "total_tokens")
    if input_tokens is not None or output_tokens is not None or total_tokens is not None:
        normalized_input = input_tokens or 0
        normalized_output = output_tokens or 0
        return (
            normalized_input,
            normalized_output,
            total_tokens if total_tokens is not None else normalized_input + normalized_output,
            "provider",
        )
    estimated_input = (input_characters + 3) // 4
    estimated_output = (output_characters + 3) // 4
    return (
        estimated_input,
        estimated_output,
        estimated_input + estimated_output,
        "estimated",
    )


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _bar_row(bar: MarketBar) -> list[str]:
    return [
        bar.end_time.isoformat(),
        str(bar.open),
        str(bar.high),
        str(bar.low),
        str(bar.close),
        str(bar.volume),
    ]


def feature_snapshot(
    *,
    candidate: GapperCandidate,
    deterministic: Any,
    learning: IntradayLearningSnapshot,
    indicators: MultiTimeframeIndicatorContext,
    bars: list[MarketBar],
    execution: dict[str, Any],
    cohort_rank: int,
    position: AIShadowPositionState,
) -> dict[str, Any]:
    regular = [bar for bar in bars if bar.is_final and bar.session == "regular"]
    recent = regular[-20:]
    current_volume = recent[-1].volume if recent else Decimal("0")
    prior_volumes = [bar.volume for bar in recent[-11:-1]]
    average_prior_volume = (
        sum(prior_volumes, Decimal("0")) / Decimal(len(prior_volumes))
        if prior_volumes
        else None
    )
    volume_ratio = (
        current_volume / average_prior_volume
        if average_prior_volume is not None and average_prior_volume > 0
        else None
    )
    current = learning.current_price
    unrealized_pct = (
        (current / position.average_cost - Decimal("1")) * Decimal("100")
        if position.average_cost is not None and position.average_cost > 0
        else None
    )
    return {
        "observed_at": recent[-1].end_time.isoformat() if recent else None,
        "cohort_rank": cohort_rank,
        "morning_rank": candidate.discovery_rank,
        "position": {
            "normalized_units": str(position.normalized_units),
            "average_cost": str(position.average_cost) if position.average_cost is not None else None,
            "unrealized_pct": str(unrealized_pct) if unrealized_pct is not None else None,
        },
        "morning": {
            "previous_close": str(candidate.previous_close),
            "premarket_price": str(candidate.premarket_price),
            "gap_pct": str(candidate.gap_pct),
            "premarket_volume": str(candidate.premarket_volume),
            "premarket_dollar_volume": str(candidate.premarket_dollar_volume),
            "tod_rvol": str(candidate.tod_rvol) if candidate.tod_rvol is not None else None,
            "market_cap": str(candidate.market_cap) if candidate.market_cap is not None else None,
            "float_shares": str(candidate.float_shares) if candidate.float_shares is not None else None,
            "frozen_spread_bps": str(candidate.spread_bps) if candidate.spread_bps is not None else None,
            "catalyst_evidence_count": len(candidate.catalyst_evidence_ids),
            "dilution_flags": list(candidate.dilution_flags),
            "discovery_rank": candidate.discovery_rank,
        },
        "market": {
            "current_price": str(current),
            "session_open": str(learning.session_open),
            "session_high": str(learning.session_high),
            "session_low": str(learning.session_low),
            "session_vwap": str(learning.session_vwap) if learning.session_vwap is not None else None,
            "session_return_pct": str(learning.session_return_pct),
            "current_vs_premarket_pct": str(learning.current_vs_premarket_pct),
            "turnover_to_float": str(learning.turnover_to_float) if learning.turnover_to_float is not None else None,
            "current_volume_ratio_to_prior10": str(volume_ratio) if volume_ratio is not None else None,
        },
        "deterministic": {
            "state": deterministic.state,
            "reason_code": deterministic.reason_code,
            "transitions": list(deterministic.transitions),
        },
        "learning": learning.model_dump(mode="json"),
        "indicators": indicators.model_dump(mode="json"),
        "execution": {
            "bid": str(execution.get("bid")) if execution.get("bid") is not None else None,
            "ask": str(execution.get("ask")) if execution.get("ask") is not None else None,
            "last": str(execution.get("last")) if execution.get("last") is not None else None,
            "spread_bps": (
                str(execution.get("spread_bps"))
                if execution.get("spread_bps") is not None
                else None
            ),
            "execution_eligible": execution.get("execution_eligible"),
            "halted": execution.get("halted"),
            "freshness_mode": execution.get("freshness_mode"),
            "rejection_reasons": list(execution.get("rejection_reasons") or ()),
            "observation_quality": execution.get("observation_quality"),
            "price_only": (
                execution.get("last") is not None
                and (execution.get("bid") is None or execution.get("ask") is None)
            ),
        },
        "recent_1m_bar_columns": [
            "end_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
        "recent_1m_bars": [_bar_row(bar) for bar in recent],
    }


def policy_feature_snapshot(
    snapshot: dict[str, Any],
    *,
    policy: AIShadowPolicy,
) -> dict[str, Any]:
    """Project the shared causal snapshot onto one experimental arm.

    The every-minute arm is intentionally "pure AI": it may use generic market,
    indicator, cohort and execution evidence, but it must not see the canonical
    V2 state machine or the deterministic intraday-learning interpretation.
    The event-driven arm is explicitly hybrid and receives those fields.
    """

    projected = dict(snapshot)
    if policy == "minute":
        projected.pop("deterministic", None)
        projected.pop("learning", None)
    return projected


def event_trigger_reasons(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    prior_decision: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Deterministic material-change triggers for the event-driven AI arm."""

    if previous is None:
        return ("initial",)

    reasons: list[str] = []
    current_det = current.get("deterministic")
    previous_det = previous.get("deterministic")
    if isinstance(current_det, dict) and isinstance(previous_det, dict):
        if current_det.get("state") != previous_det.get("state"):
            reasons.append("deterministic_state_changed")

    current_learning = current.get("learning")
    previous_learning = previous.get("learning")
    if isinstance(current_learning, dict) and isinstance(previous_learning, dict):
        if current_learning.get("pattern") != previous_learning.get("pattern"):
            reasons.append("learning_pattern_changed")

    def nested(snapshot: dict[str, Any], *path: str):
        value: object = snapshot
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    current_price = _decimal(nested(current, "market", "current_price"))
    current_vwap = _decimal(nested(current, "market", "session_vwap"))
    previous_price = _decimal(nested(previous, "market", "current_price"))
    previous_vwap = _decimal(nested(previous, "market", "session_vwap"))
    if None not in {current_price, current_vwap, previous_price, previous_vwap}:
        assert current_price is not None and current_vwap is not None
        assert previous_price is not None and previous_vwap is not None
        if (current_price >= current_vwap) != (previous_price >= previous_vwap):
            reasons.append("vwap_side_changed")

    current_high = _decimal(nested(current, "market", "session_high"))
    previous_high = _decimal(nested(previous, "market", "session_high"))
    if current_high is not None and previous_high is not None and current_high > previous_high:
        reasons.append("new_session_high")

    current_volume_ratio = _decimal(nested(current, "market", "current_volume_ratio_to_prior10"))
    previous_volume_ratio = _decimal(nested(previous, "market", "current_volume_ratio_to_prior10"))
    if current_volume_ratio is not None and current_volume_ratio >= Decimal("1.75"):
        if previous_volume_ratio is None or previous_volume_ratio < Decimal("1.75"):
            reasons.append("volume_spike")

    current_spread = _decimal(nested(current, "execution", "spread_bps"))
    previous_spread = _decimal(nested(previous, "execution", "spread_bps"))
    spread_thresholds = (Decimal("50"), Decimal("100"), Decimal("150"))
    if current_spread is not None and previous_spread is not None:
        if any(
            (previous_spread <= threshold < current_spread)
            or (previous_spread > threshold >= current_spread)
            for threshold in spread_thresholds
        ):
            reasons.append("spread_tier_changed")

    current_eligible = nested(current, "execution", "execution_eligible")
    previous_eligible = nested(previous, "execution", "execution_eligible")
    if current_eligible != previous_eligible:
        reasons.append("execution_eligibility_changed")
    if nested(current, "execution", "halted") != nested(previous, "execution", "halted"):
        reasons.append("halt_state_changed")

    current_one = nested(current, "indicators", "one_minute")
    previous_one = nested(previous, "indicators", "one_minute")
    if isinstance(current_one, dict) and isinstance(previous_one, dict):
        ck = _decimal(current_one.get("stochastic_rsi_k"))
        cd = _decimal(current_one.get("stochastic_rsi_d"))
        pk = _decimal(previous_one.get("stochastic_rsi_k"))
        pd = _decimal(previous_one.get("stochastic_rsi_d"))
        if None not in {ck, cd, pk, pd}:
            assert ck is not None and cd is not None and pk is not None and pd is not None
            if pk >= 20 and pd >= 20 and ck < 20 and cd < 20:
                reasons.append("stoch_entered_oversold")
            if pk <= 80 and pd <= 80 and ck > 80 and cd > 80:
                reasons.append("stoch_entered_overbought")
        if current_one.get("ema9_rising") != previous_one.get("ema9_rising"):
            reasons.append("ema9_direction_changed")

    current_units = _decimal(nested(current, "position", "normalized_units"))
    previous_units = _decimal(nested(previous, "position", "normalized_units"))
    if current_units is not None and previous_units is not None and current_units != previous_units:
        reasons.append("position_state_changed")

    current_unrealized = _decimal(nested(current, "position", "unrealized_pct"))
    previous_unrealized = _decimal(nested(previous, "position", "unrealized_pct"))
    for threshold in (Decimal("-1"), Decimal("1"), Decimal("2"), Decimal("5")):
        if current_unrealized is not None and previous_unrealized is not None:
            if (previous_unrealized < threshold <= current_unrealized) or (
                previous_unrealized >= threshold > current_unrealized
            ):
                reasons.append(f"position_pnl_crossed_{str(threshold).replace('-', 'minus_').replace('.', '_')}pct")

    if prior_decision:
        invalidation = _decimal(prior_decision.get("invalidation_price"))
        if (
            invalidation is not None
            and current_price is not None
            and current_price <= invalidation
        ):
            reasons.append("thesis_invalidation_reached")

    unique_reasons = tuple(dict.fromkeys(reasons))
    if not unique_reasons:
        return unique_reasons
    hard = {
        "execution_eligibility_changed",
        "halt_state_changed",
        "position_state_changed",
        "thesis_invalidation_reached",
    }
    if any(reason in hard for reason in unique_reasons):
        return unique_reasons
    try:
        current_at = datetime.fromisoformat(
            str(current.get("observed_at")).replace("Z", "+00:00")
        )
        previous_at = datetime.fromisoformat(
            str(previous.get("observed_at")).replace("Z", "+00:00")
        )
    except Exception:
        return unique_reasons
    if current_at.tzinfo is None or previous_at.tzinfo is None:
        return unique_reasons
    if current_at - previous_at < timedelta(minutes=2):
        return ()
    return unique_reasons


def _build_payload(
    rows: list[dict[str, Any]],
    *,
    policy: AIShadowPolicy,
) -> dict[str, Any]:
    return {
        "policy": policy,
        "task": (
            "Manage a normalized long-only SHADOW position from causal market evidence. "
            "Choose exactly one action: enter, hold, add, reduce, exit, or skip. "
            "Use skip only while flat and hold only while long. Adds are allowed only "
            "for a confirmed improving trend. Avoid reacting to one noisy bar unless "
            "the thesis materially changed. Overbought momentum can be strength in a "
            "trend. Always provide a causal invalidation price when entering/adding "
            "if the supplied evidence supports one."
        ),
        "normalization": {
            "enter_units": "1.0",
            "add_units": "0.5",
            "maximum_units": "1.5",
            "reduce_units": "0.5",
            "real_money_authority": False,
        },
        "candidates": rows,
    }


class AIShadowPolicyAnalyzer:
    def __init__(self, provider_factory=None) -> None:
        if provider_factory is None:
            from .strategy_ai_shadow_provider import get_trading_research_provider

            provider_factory = get_trading_research_provider
        self.provider_factory = provider_factory

    def assess(
        self,
        *,
        policy: AIShadowPolicy,
        rows: list[dict[str, Any]],
    ) -> AIShadowResult:
        from .strategy_ai_shadow_provider import _reliable_assess

        return _reliable_assess(self, policy=policy, rows=rows)


def normalize_action_for_position(
    action: AIShadowAction,
    position: AIShadowPositionState,
) -> tuple[AIShadowAction, str | None]:
    """Normalize impossible model actions into a deterministic position state.

    This does not choose a trade. It prevents malformed state transitions from
    polluting churn/stability metrics (for example, HOLD while flat or ENTER
    while already long).
    """

    if position.is_long:
        if action in {"enter", "skip"}:
            return "hold", "AI_SHADOW_ACTION_NORMALIZED_LONG"
        if action == "add" and position.normalized_units >= MAX_NORMALIZED_UNITS:
            return "hold", "AI_SHADOW_MAX_UNITS_REACHED"
        return action, None

    if action == "enter":
        return "enter", None
    if action == "skip":
        return "skip", None
    return "skip", "AI_SHADOW_ACTION_NORMALIZED_FLAT"


def desired_fill(
    decision: AIShadowDecision,
    position: AIShadowPositionState,
) -> tuple[Literal["buy", "sell"] | None, Decimal]:
    if not position.is_long:
        if decision.action == "enter":
            return "buy", Decimal("1")
        return None, Decimal("0")
    if decision.action == "add" and position.normalized_units < MAX_NORMALIZED_UNITS:
        return "buy", min(ADD_UNITS, MAX_NORMALIZED_UNITS - position.normalized_units)
    if decision.action == "reduce":
        return "sell", min(REDUCE_UNITS, position.normalized_units)
    if decision.action == "exit":
        return "sell", position.normalized_units
    return None, Decimal("0")


def simulate_ai_shadow_fill(
    execution: dict[str, Any],
    *,
    side: Literal["buy", "sell"],
    instrument_id: str,
    binding_id: str | None,
    decision_at: datetime,
    requested_units: Decimal,
    reference_price: Decimal | None,
    policy: PaperExecutionPolicy | None = None,
    max_capture_lag_seconds: Decimal = Decimal("60"),
    allow_degraded_price_only: bool = False,
    degraded_spread_bps: Decimal = Decimal("150"),
) -> AIShadowFillSimulation:
    active = policy or PaperExecutionPolicy()
    bid = _decimal(execution.get("bid"))
    ask = _decimal(execution.get("ask"))
    last = _decimal(execution.get("last"))
    source_time = _datetime(execution.get("source_time"))
    spread = _decimal(execution.get("spread_bps"))
    halted = execution.get("halted") is True
    eligible = execution.get("execution_eligible") is True
    quality_value = str(execution.get("observation_quality") or "")
    if quality_value not in {"book", "price_only", "bar_close_fallback"}:
        quality_value = "book" if bid is not None and ask is not None else "price_only"
    degraded_price_only = bool(
        allow_degraded_price_only
        and last is not None
        and source_time is not None
        and abs((source_time - decision_at).total_seconds()) <= float(max_capture_lag_seconds)
        and (bid is None or ask is None)
        and not halted
    )
    if (
        last is None
        or source_time is None
        or requested_units <= 0
        or (source_time - decision_at).total_seconds() > float(max_capture_lag_seconds)
    ):
        return AIShadowFillSimulation(
            paper_execution_policy_version=active.policy_version,
            side=side,
            decision_at=decision_at,
            source_time=source_time,
            requested_units=requested_units,
            reference_price=reference_price,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            slippage_bps=active.slippage_bps,
            fill_reason=(
                "execution_capture_too_late"
                if source_time is not None
                and (source_time - decision_at).total_seconds() > float(max_capture_lag_seconds)
                else "execution_observation_incomplete"
            ),
            execution_eligible=eligible,
            halted=halted,
            observation_quality=cast(Any, quality_value),
        )

    if degraded_price_only:
        assert last is not None
        total_friction_bps = degraded_spread_bps + active.slippage_bps
        fraction = total_friction_bps / Decimal("10000")
        fill_price = last * (
            Decimal("1") + fraction if side == "buy" else Decimal("1") - fraction
        )
        return AIShadowFillSimulation(
            paper_execution_policy_version=active.policy_version,
            side=side,
            decision_at=decision_at,
            source_time=source_time,
            requested_units=requested_units,
            filled_units=requested_units,
            reference_price=reference_price,
            fill_price=fill_price,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            slippage_bps=active.slippage_bps,
            should_fill=True,
            fill_reason="degraded_price_only_hypothetical",
            execution_eligible=False,
            halted=False,
            observation_quality=cast(Any, quality_value),
            hypothetical=True,
            assumed_friction_bps=total_friction_bps,
        )

    observation = PaperMarketObservation(
        instrument_id=instrument_id,
        binding_id=binding_id,
        provider=str(execution.get("provider") or "ai-shadow"),
        price=last,
        bid=bid,
        ask=ask,
        bid_size=_decimal(execution.get("bid_size")),
        ask_size=_decimal(execution.get("ask_size")),
        high=_decimal(execution.get("high")),
        low=_decimal(execution.get("low")),
        volume=_decimal(execution.get("bar_volume")),
        bar_start_time=_datetime(execution.get("bar_start_time")),
        source_time=source_time,
        evaluated_at=source_time,
        execution_eligible=eligible,
        freshness_mode=str(execution.get("freshness_mode") or "unknown"),
        rejection_reasons=tuple(str(item) for item in (execution.get("rejection_reasons") or ())),
        halted=halted,
    )
    order = PaperOrder(
        account_id="ai-shadow",
        order_id=f"{side}:{instrument_id}:{decision_at.isoformat()}",
        instrument_id=instrument_id,
        binding_id=binding_id,
        side=side,
        order_type="market",
        quantity=requested_units,
        idempotency_key=f"{side}:{instrument_id}:{decision_at.isoformat()}",
        created_at=decision_at,
    )
    fill = paper_fill_decision(order, observation, active)
    return AIShadowFillSimulation(
        paper_execution_policy_version=active.policy_version,
        side=side,
        decision_at=decision_at,
        source_time=source_time,
        requested_units=requested_units,
        filled_units=fill.fill_quantity or Decimal("0"),
        reference_price=reference_price,
        fill_price=fill.fill_price,
        bid=bid,
        ask=ask,
        spread_bps=spread,
        slippage_bps=active.slippage_bps,
        should_fill=fill.should_fill,
        fill_reason=fill.reason,
        execution_eligible=eligible,
        halted=halted,
        observation_quality=cast(Any, quality_value),
    )


def apply_fill(
    state: AIShadowPositionState,
    fill: AIShadowFillSimulation,
    *,
    trade_id: str,
) -> AIShadowPositionState:
    if not fill.should_fill or fill.fill_price is None or fill.filled_units <= 0:
        return state
    units = fill.filled_units
    reference = fill.reference_price or fill.fill_price
    if fill.side == "buy":
        previous_notional = (
            state.average_cost * state.normalized_units
            if state.average_cost is not None
            else Decimal("0")
        )
        new_units = state.normalized_units + units
        average_cost = (previous_notional + fill.fill_price * units) / new_units
        drag = max(fill.fill_price - reference, Decimal("0")) * units
        return state.model_copy(
            update={
                "normalized_units": new_units,
                "average_cost": average_cost,
                "trade_id": state.trade_id or trade_id,
                "entry_time": state.entry_time or fill.source_time,
                "first_entry_price": state.first_entry_price or fill.fill_price,
                "total_buy_notional": state.total_buy_notional + fill.fill_price * units,
                "total_reference_buy_notional": state.total_reference_buy_notional + reference * units,
                "execution_drag_dollars": state.execution_drag_dollars + drag,
                "fill_count": state.fill_count + 1,
                "degraded_fill_count": state.degraded_fill_count + (1 if fill.hypothetical else 0),
            }
        )

    if state.average_cost is None or state.normalized_units <= 0:
        return state
    sold = min(units, state.normalized_units)
    realized = (fill.fill_price - state.average_cost) * sold
    drag = max(reference - fill.fill_price, Decimal("0")) * sold
    remaining = state.normalized_units - sold
    return state.model_copy(
        update={
            "normalized_units": remaining,
            "average_cost": state.average_cost if remaining > 0 else None,
            "realized_pnl": state.realized_pnl + realized,
            "execution_drag_dollars": state.execution_drag_dollars + drag,
            "fill_count": state.fill_count + 1,
            "degraded_fill_count": state.degraded_fill_count + (1 if fill.hypothetical else 0),
        }
    )


__all__ = [
    "ADD_UNITS",
    "AI_SHADOW_POLICY_VERSION",
    "AIShadowAction",
    "AIShadowDecision",
    "AIShadowFillSimulation",
    "AIShadowPolicy",
    "AIShadowPolicyAnalyzer",
    "AIShadowPositionState",
    "AIShadowResult",
    "MAX_NORMALIZED_UNITS",
    "REDUCE_UNITS",
    "apply_fill",
    "desired_fill",
    "event_trigger_reasons",
    "feature_snapshot",
    "normalize_action_for_position",
    "policy_feature_snapshot",
    "simulate_ai_shadow_fill",
]
