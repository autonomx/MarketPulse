from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""Execution-cost accounting for the Stoch trend-capture SHADOW strategy.

This module deliberately reuses the canonical paper-execution-v2 fill kernel.
It never places an order. Point-in-time bid/ask evidence is converted into a
one-position-unit simulated market fill so the strategy can compare its
idealized chart return with a spread/slippage-adjusted executable return.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from .paper import (
    PaperExecutionPolicy,
    PaperMarketObservation,
    PaperOrder,
    paper_fill_decision,
)
from .strategy_stoch_trend_capture import StochTrendCaptureSnapshot


StochExecutionAction = Literal[
    "entry",
    "range_exit",
    "partial_exit",
    "runner_exit",
    "force_flat",
]
StochSpreadTier = Literal["tight", "acceptable", "expensive", "extreme", "unknown"]


class StochExecutionSimulation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["stoch-execution-cost-v1"] = "stoch-execution-cost-v1"
    paper_execution_policy_version: str
    action: StochExecutionAction
    side: Literal["buy", "sell"]
    decision_at: datetime
    source_time: datetime | None = None
    capture_lag_seconds: Decimal | None = None
    requested_fraction: Decimal
    filled_fraction: Decimal = Decimal("0")
    fill_complete: bool = False
    reference_price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    spread_bps: Decimal | None = None
    spread_tier: StochSpreadTier = "unknown"
    slippage_bps: Decimal
    estimated_round_trip_cost_bps: Decimal | None = None
    should_fill: bool = False
    fill_price: Decimal | None = None
    fill_reason: str
    execution_eligible: bool = False
    halted: bool = False
    execution_authority: Literal[False] = False


class StochExecutionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["stoch-execution-cost-v1"] = "stoch-execution-cost-v1"
    complete: bool
    reason_code: str
    gross_reference_return_pct: Decimal | None = None
    net_execution_return_pct: Decimal | None = None
    execution_drag_pct: Decimal | None = None
    entry_fill_price: Decimal | None = None
    weighted_exit_fill_price: Decimal | None = None
    entry_spread_bps: Decimal | None = None
    exit_spread_bps: Decimal | None = None
    maximum_observed_spread_bps: Decimal | None = None
    execution_authority: Literal[False] = False


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def spread_tier(spread_bps: Decimal | None) -> StochSpreadTier:
    if spread_bps is None:
        return "unknown"
    if spread_bps <= Decimal("50"):
        return "tight"
    if spread_bps <= Decimal("100"):
        return "acceptable"
    if spread_bps <= Decimal("150"):
        return "expensive"
    return "extreme"


def _spread_from_book(
    bid: Decimal | None,
    ask: Decimal | None,
    payload_spread: Decimal | None,
) -> Decimal | None:
    if payload_spread is not None:
        return payload_spread
    if bid is None or ask is None:
        return None
    midpoint = (bid + ask) / Decimal("2")
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint * Decimal("10000")


def simulate_stoch_execution(
    execution: dict[str, Any],
    *,
    action: StochExecutionAction,
    instrument_id: str,
    binding_id: str | None,
    decision_at: datetime,
    requested_fraction: Decimal,
    reference_price: Decimal | None = None,
    policy: PaperExecutionPolicy | None = None,
    max_capture_lag_seconds: Decimal = Decimal("60"),
) -> StochExecutionSimulation:
    """Apply paper-execution-v2 to one captured Stoch action without trading."""

    active = policy or PaperExecutionPolicy()
    side: Literal["buy", "sell"] = "buy" if action == "entry" else "sell"
    bid = _decimal(execution.get("bid"))
    ask = _decimal(execution.get("ask"))
    last = _decimal(execution.get("last"))
    source_time = _datetime(execution.get("source_time"))
    spread = _spread_from_book(bid, ask, _decimal(execution.get("spread_bps")))
    halted = execution.get("halted") is True
    execution_eligible = execution.get("execution_eligible") is True
    capture_lag = (
        Decimal(str((source_time - decision_at).total_seconds()))
        if source_time is not None
        else None
    )

    if last is None or source_time is None or requested_fraction <= 0:
        return StochExecutionSimulation(
            paper_execution_policy_version=active.policy_version,
            action=action,
            side=side,
            decision_at=decision_at,
            source_time=source_time,
            capture_lag_seconds=capture_lag,
            requested_fraction=requested_fraction,
            reference_price=reference_price,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            spread_tier=spread_tier(spread),
            slippage_bps=active.slippage_bps,
            estimated_round_trip_cost_bps=(
                spread + active.slippage_bps * Decimal("2")
                if action == "entry" and spread is not None
                else None
            ),
            should_fill=False,
            fill_reason="execution_observation_incomplete",
            execution_eligible=execution_eligible,
            halted=halted,
        )

    if capture_lag is not None and capture_lag > max_capture_lag_seconds:
        return StochExecutionSimulation(
            paper_execution_policy_version=active.policy_version,
            action=action,
            side=side,
            decision_at=decision_at,
            source_time=source_time,
            capture_lag_seconds=capture_lag,
            requested_fraction=requested_fraction,
            reference_price=reference_price,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            spread_tier=spread_tier(spread),
            slippage_bps=active.slippage_bps,
            estimated_round_trip_cost_bps=(
                spread + active.slippage_bps * Decimal("2")
                if action == "entry" and spread is not None
                else None
            ),
            should_fill=False,
            fill_reason="execution_capture_too_late",
            execution_eligible=execution_eligible,
            halted=halted,
        )

    observation = PaperMarketObservation(
        instrument_id=instrument_id,
        binding_id=binding_id,
        provider=str(execution.get("provider") or "stoch-shadow"),
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
        execution_eligible=execution_eligible,
        freshness_mode=str(execution.get("freshness_mode") or "unknown"),
        provider_sequence=(
            int(execution["provider_sequence"])
            if execution.get("provider_sequence") is not None
            else None
        ),
        rejection_reasons=tuple(
            str(item)
            for item in (execution.get("rejection_reasons") or ())
        ),
        halted=halted,
    )
    order = PaperOrder(
        account_id="stoch-shadow",
        order_id=f"{action}:{instrument_id}:{decision_at.isoformat()}",
        instrument_id=instrument_id,
        binding_id=binding_id,
        side=side,
        order_type="market",
        quantity=requested_fraction,
        idempotency_key=f"{action}:{instrument_id}:{decision_at.isoformat()}",
        created_at=decision_at,
    )
    decision = paper_fill_decision(order, observation, active)
    filled = decision.fill_quantity or Decimal("0")
    return StochExecutionSimulation(
        paper_execution_policy_version=active.policy_version,
        action=action,
        side=side,
        decision_at=decision_at,
        source_time=source_time,
        capture_lag_seconds=capture_lag,
        requested_fraction=requested_fraction,
        filled_fraction=filled,
        fill_complete=bool(decision.should_fill and filled >= requested_fraction),
        reference_price=reference_price,
        bid=bid,
        ask=ask,
        spread_bps=spread,
        spread_tier=spread_tier(spread),
        slippage_bps=active.slippage_bps,
        estimated_round_trip_cost_bps=(
            spread + active.slippage_bps * Decimal("2")
            if action == "entry" and spread is not None
            else None
        ),
        should_fill=decision.should_fill,
        fill_price=decision.fill_price,
        fill_reason=decision.reason,
        execution_eligible=execution_eligible,
        halted=halted,
    )


def requested_fraction_for_action(
    action: StochExecutionAction,
    snapshot: StochTrendCaptureSnapshot,
) -> Decimal:
    if action in {"entry", "range_exit"}:
        return Decimal("1")
    if action == "partial_exit":
        return snapshot.partial_fraction
    if action == "runner_exit":
        return Decimal("1") - snapshot.partial_fraction
    if action == "force_flat":
        return (
            Decimal("1") - snapshot.partial_fraction
            if snapshot.partial_exit_time is not None
            else Decimal("1")
        )
    raise ValueError(f"unsupported stoch execution action: {action}")


def action_for_snapshot(
    snapshot: StochTrendCaptureSnapshot,
) -> tuple[StochExecutionAction, datetime] | None:
    """Return the action whose point-in-time quote should be captured now."""

    if snapshot.state == "entry_armed" and snapshot.entry_signal_time is not None:
        return "entry", snapshot.entry_signal_time
    if snapshot.state == "range_exit_armed" and snapshot.first_overbought_time is not None:
        return "range_exit", snapshot.first_overbought_time
    if snapshot.state == "trend_partial_armed" and snapshot.first_overbought_time is not None:
        return "partial_exit", snapshot.first_overbought_time
    if snapshot.state == "trend_exit_armed" and snapshot.trend_break_time is not None:
        return "runner_exit", snapshot.trend_break_time
    if snapshot.state == "force_flat" and snapshot.runner_exit_time is not None:
        return "force_flat", snapshot.runner_exit_time
    return None


def _simulation_from_payload(
    payload: dict[str, Any] | None,
) -> StochExecutionSimulation | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("execution_simulation")
    if not isinstance(raw, dict):
        return None
    try:
        return StochExecutionSimulation.model_validate(raw)
    except Exception:
        logger.debug("suppressed error in %s", "_simulation_from_payload", exc_info=True)
        return None


def build_execution_summary(
    snapshot: StochTrendCaptureSnapshot,
    *,
    entry_payload: dict[str, Any] | None,
    action_payloads: dict[StochExecutionAction, dict[str, Any]],
) -> StochExecutionSummary:
    """Combine captured entry/exit fills into the strategy's executable return."""

    entry = _simulation_from_payload(entry_payload)
    if entry is None or not entry.fill_complete or entry.fill_price is None:
        return StochExecutionSummary(
            complete=False,
            reason_code="STOCH_EXECUTION_ENTRY_UNAVAILABLE",
            gross_reference_return_pct=snapshot.return_pct,
        )

    exits: list[tuple[Decimal, StochExecutionSimulation]] = []
    if snapshot.partial_exit_time is not None:
        partial = _simulation_from_payload(action_payloads.get("partial_exit"))
        if partial is None or not partial.fill_complete or partial.fill_price is None:
            return StochExecutionSummary(
                complete=False,
                reason_code="STOCH_EXECUTION_PARTIAL_EXIT_UNAVAILABLE",
                gross_reference_return_pct=snapshot.return_pct,
                entry_fill_price=entry.fill_price,
                entry_spread_bps=entry.spread_bps,
            )
        exits.append((snapshot.partial_fraction, partial))
        runner = (
            _simulation_from_payload(action_payloads.get("runner_exit"))
            or _simulation_from_payload(action_payloads.get("force_flat"))
        )
        if runner is None or not runner.fill_complete or runner.fill_price is None:
            return StochExecutionSummary(
                complete=False,
                reason_code="STOCH_EXECUTION_RUNNER_EXIT_UNAVAILABLE",
                gross_reference_return_pct=snapshot.return_pct,
                entry_fill_price=entry.fill_price,
                entry_spread_bps=entry.spread_bps,
            )
        exits.append((Decimal("1") - snapshot.partial_fraction, runner))
    else:
        full = (
            _simulation_from_payload(action_payloads.get("range_exit"))
            or _simulation_from_payload(action_payloads.get("force_flat"))
            or _simulation_from_payload(action_payloads.get("runner_exit"))
        )
        if full is None or not full.fill_complete or full.fill_price is None:
            return StochExecutionSummary(
                complete=False,
                reason_code="STOCH_EXECUTION_EXIT_UNAVAILABLE",
                gross_reference_return_pct=snapshot.return_pct,
                entry_fill_price=entry.fill_price,
                entry_spread_bps=entry.spread_bps,
            )
        exits.append((Decimal("1"), full))

    weighted_exit = sum(
        (weight * cast(Decimal, simulation.fill_price) for weight, simulation in exits),
        Decimal("0"),
    )
    net = (weighted_exit / entry.fill_price - Decimal("1")) * Decimal("100")
    gross = snapshot.return_pct
    spreads = [
        spread
        for spread in [
            entry.spread_bps,
            *(simulation.spread_bps for _, simulation in exits),
        ]
        if spread is not None
    ]
    exit_spread = (
        sum(
            (
                weight * simulation.spread_bps
                for weight, simulation in exits
                if simulation.spread_bps is not None
            ),
            Decimal("0"),
        )
        if all(simulation.spread_bps is not None for _, simulation in exits)
        else None
    )

    return StochExecutionSummary(
        complete=True,
        reason_code="STOCH_EXECUTION_NET_RETURN_READY",
        gross_reference_return_pct=gross,
        net_execution_return_pct=net,
        execution_drag_pct=(gross - net if gross is not None else None),
        entry_fill_price=entry.fill_price,
        weighted_exit_fill_price=weighted_exit,
        entry_spread_bps=entry.spread_bps,
        exit_spread_bps=exit_spread,
        maximum_observed_spread_bps=max(spreads) if spreads else None,
    )


__all__ = [
    "StochExecutionAction",
    "StochExecutionSimulation",
    "StochExecutionSummary",
    "action_for_snapshot",
    "build_execution_summary",
    "requested_fraction_for_action",
    "simulate_stoch_execution",
    "spread_tier",
]
