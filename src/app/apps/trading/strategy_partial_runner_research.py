from __future__ import annotations

"""Research-only partial-profit + adaptive runner replay on fixed historical entries.

No strategy/order authority is exposed here. The caller supplies an already-selected
baseline trade; this module changes only post-entry management.
"""

from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .gapper_dataset import GapperCandidate
from .indicator_signals import multi_timeframe_indicator_context
from .models import MarketBar
from .paper import PaperExecutionPolicy, PaperOrder, paper_fill_decision, paper_protection_trigger
from .strategies.models import GapPullbackConfig
from .strategy_adaptive_exit_research import (
    _market_fill,
    _observation,
    _stop_fill,
    adaptive_exit_deterioration,
)
from .strategy_backtest import GapPullbackBacktestTrade, _adverse_sell_slippage_bps
from .strategy_timeframes import resample_final_bars
from .strategy_v2_management import v2_active_stop_for_prior_high
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_FORCE_FLAT_ET = time(15, 55)
_PARTIAL_FRACTION = Decimal("0.50")
_PARTIAL_TARGET_R = Decimal("1.0")
_NO_TARGET_MULTIPLE = Decimal("1000000")
ExitKind = Literal["stop", "indicator", "force_flat"]


class PartialRunnerReplayTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    entry_time: datetime
    baseline_exit_time: datetime
    entry_price: Decimal
    stop_price: Decimal
    entry_fill_quantity: Decimal
    partial_target_price: Decimal
    partial_target_quantity: Decimal
    partial_filled_quantity: Decimal
    partial_fill_vwap: Decimal | None = None
    partial_fill_count: int = 0
    runner_exit_reason: ExitKind
    runner_indicator_reason_codes: tuple[str, ...] = ()
    final_fill_time: datetime
    combined_exit_price: Decimal
    exit_slippage_bps: Decimal
    exit_fill_count: int
    exit_partially_filled: bool
    pnl_per_share: Decimal
    r_multiple: Decimal
    mfe_r: Decimal
    mae_r: Decimal
    weighted_hold_minutes: Decimal
    final_hold_minutes: Decimal


def partial_target_quantity(quantity: Decimal) -> Decimal:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return quantity * _PARTIAL_FRACTION


def _limit_fill(
    *,
    candidate: GapperCandidate,
    bar: MarketBar,
    target_quantity: Decimal,
    already_filled: Decimal,
    target_price: Decimal,
    policy: PaperExecutionPolicy,
    assumed_spread_bps: Decimal,
):
    order = PaperOrder(
        account_id="backtest",
        order_id=f"partial-1r:{candidate.instrument_id}",
        instrument_id=candidate.instrument_id,
        binding_id=candidate.binding_id,
        side="sell",
        order_type="limit",
        quantity=target_quantity,
        filled_quantity=already_filled,
        limit_price=target_price,
        idempotency_key=f"partial-1r:{candidate.instrument_id}",
    )
    return paper_fill_decision(
        order,
        _observation(
            candidate=candidate,
            bar=bar,
            assumed_spread_bps=assumed_spread_bps,
            price=bar.open,
            open_only=False,
        ),
        policy,
    )


def replay_partial_profit_runner(
    *,
    candidate: GapperCandidate,
    bars: list[MarketBar] | tuple[MarketBar, ...],
    baseline_trade: GapPullbackBacktestTrade,
    config: GapPullbackConfig,
    policy: PaperExecutionPolicy,
    assumed_spread_bps: Decimal,
    force_flat_et: time = _FORCE_FLAT_ET,
) -> PartialRunnerReplayTrade:
    """Replay frozen policy C from the exact baseline entry/fill/size.

    Policy C:
    - preserve the initial structural stop;
    - preserve causal +0.75R -> +0.25R protection;
    - offer 50% at +1.0R using pessimistic stop-before-target ordering;
    - allow the already-frozen adaptive deterioration rule to exit all remaining
      quantity, both before and after the partial target;
    - remove the 1.5R full target and 60-minute timeout;
    - make force-flat irrevocable at 15:55 ET.

    Market/stop/limit partial fills remain active under paper-execution-v2. Later
    bars may complete an already-issued exit, but they may not reverse its decision.
    """

    if candidate.instrument_id != baseline_trade.instrument_id:
        raise ValueError("candidate/trade instrument mismatch")

    execution_bars = tuple(resample_final_bars(bars, config.execution_interval))
    entry_index = next(
        (index for index, bar in enumerate(execution_bars) if bar.start_time == baseline_trade.entry_time),
        None,
    )
    if entry_index is None:
        raise ValueError("baseline entry is not present in replay bars")

    entry = baseline_trade.entry_price
    stop = baseline_trade.stop_price
    risk = entry - stop
    if risk <= 0:
        raise ValueError("baseline trade has non-positive risk")

    quantity = baseline_trade.entry_fill_quantity
    target_quantity = partial_target_quantity(quantity)
    target_price = entry + risk * _PARTIAL_TARGET_R
    unreachable_target = entry + risk * _NO_TARGET_MULTIPLE

    force_flat_index = max(
        (
            index
            for index, bar in enumerate(execution_bars)
            if index >= entry_index and bar.start_time.astimezone(_ET).time() < force_flat_et
        ),
        default=len(execution_bars) - 1,
    )

    max_high = entry
    min_low = entry
    previous_context = (
        multi_timeframe_indicator_context(execution_bars[:entry_index])
        if entry_index > 0
        else None
    )
    pending_indicator_reasons: tuple[str, ...] | None = None
    active_exit_kind: ExitKind | None = None
    active_indicator_reasons: tuple[str, ...] = ()

    sold_quantity = Decimal("0")
    exit_notional = Decimal("0")
    reference_notional = Decimal("0")
    hold_minute_quantity = Decimal("0")
    fill_count = 0
    final_fill_time: datetime | None = None
    last_exit_error: str | None = None

    partial_filled = Decimal("0")
    partial_notional = Decimal("0")
    partial_fill_count = 0

    def record_fill(
        fill_price: Decimal,
        fill_quantity: Decimal,
        reference: Decimal,
        fill_time: datetime,
        *,
        partial: bool = False,
    ) -> None:
        nonlocal sold_quantity, exit_notional, reference_notional, hold_minute_quantity
        nonlocal fill_count, final_fill_time, partial_filled, partial_notional, partial_fill_count

        accepted = min(fill_quantity, quantity - sold_quantity)
        if accepted <= 0:
            return
        sold_quantity += accepted
        exit_notional += fill_price * accepted
        reference_notional += reference * accepted
        minutes = Decimal(str((fill_time - baseline_trade.entry_time).total_seconds() / 60))
        hold_minute_quantity += minutes * accepted
        fill_count += 1
        final_fill_time = fill_time
        if partial:
            accepted_partial = min(accepted, target_quantity - partial_filled)
            if accepted_partial > 0:
                partial_filled += accepted_partial
                partial_notional += fill_price * accepted_partial
                partial_fill_count += 1

    for index in range(entry_index, len(execution_bars)):
        bar = execution_bars[index]
        remaining = quantity - sold_quantity
        if remaining <= 0:
            break

        active_stop = v2_active_stop_for_prior_high(
            config,
            entry_price=entry,
            initial_stop=stop,
            prior_finalized_high=max_high,
        )

        if active_exit_kind is None and pending_indicator_reasons is not None:
            active_exit_kind = "indicator"
            active_indicator_reasons = pending_indicator_reasons

        if active_exit_kind in {"indicator", "force_flat", "stop"}:
            max_high = max(max_high, bar.open)
            min_low = min(min_low, bar.open)

            if active_exit_kind != "stop" and bar.open <= active_stop:
                decision = _stop_fill(
                    candidate=candidate,
                    bar=bar,
                    remaining_quantity=remaining,
                    active_stop=active_stop,
                    policy=policy,
                    assumed_spread_bps=assumed_spread_bps,
                    open_only=True,
                )
                if (
                    decision.should_fill
                    and decision.fill_price is not None
                    and decision.fill_quantity is not None
                ):
                    record_fill(
                        decision.fill_price,
                        decision.fill_quantity,
                        active_stop,
                        bar.start_time,
                    )
                    active_exit_kind = "stop"
                    if sold_quantity >= quantity:
                        break
                    continue
                last_exit_error = f"exit_execution:{decision.reason}"

            decision = _market_fill(
                candidate=candidate,
                bar=bar,
                total_quantity=quantity,
                already_filled=sold_quantity,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
                price=bar.open,
                order_suffix=f"partial-runner-{active_exit_kind}",
            )
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
            ):
                record_fill(
                    decision.fill_price,
                    decision.fill_quantity,
                    bar.open,
                    bar.start_time,
                )
                if sold_quantity >= quantity:
                    break
                continue
            last_exit_error = f"exit_execution:{decision.reason}"
            continue

        max_high = max(max_high, bar.high)
        min_low = min(min_low, bar.low)

        observation = _observation(
            candidate=candidate,
            bar=bar,
            assumed_spread_bps=assumed_spread_bps,
            price=bar.open,
            open_only=False,
        )
        target_for_bar = target_price if partial_filled < target_quantity else unreachable_target
        trigger = paper_protection_trigger(
            is_long=True,
            stop_price=active_stop,
            target_price=target_for_bar,
            observation=observation,
            activated_at=baseline_trade.entry_time,
        )

        if trigger == "stop":
            decision = _stop_fill(
                candidate=candidate,
                bar=bar,
                remaining_quantity=remaining,
                active_stop=active_stop,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
                open_only=False,
            )
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
            ):
                record_fill(
                    decision.fill_price,
                    decision.fill_quantity,
                    active_stop,
                    bar.end_time,
                )
                active_exit_kind = "stop"
                if sold_quantity >= quantity:
                    break
                continue
            last_exit_error = f"exit_execution:{decision.reason}"

        elif trigger == "target":
            decision = _limit_fill(
                candidate=candidate,
                bar=bar,
                target_quantity=target_quantity,
                already_filled=partial_filled,
                target_price=target_price,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
            )
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
            ):
                record_fill(
                    decision.fill_price,
                    decision.fill_quantity,
                    target_price,
                    bar.end_time,
                    partial=True,
                )
            else:
                last_exit_error = f"partial_execution:{decision.reason}"

        context = multi_timeframe_indicator_context(execution_bars[: index + 1])
        deterioration = adaptive_exit_deterioration(context, previous_context)
        previous_context = context
        if deterioration.exit and pending_indicator_reasons is None:
            pending_indicator_reasons = deterioration.reason_codes

        if index == force_flat_index and active_exit_kind is None:
            active_exit_kind = "force_flat"
            remaining = quantity - sold_quantity
            if remaining <= 0:
                break
            decision = _market_fill(
                candidate=candidate,
                bar=bar,
                total_quantity=quantity,
                already_filled=sold_quantity,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
                price=bar.close,
                order_suffix="partial-runner-force-flat",
            )
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
            ):
                record_fill(
                    decision.fill_price,
                    decision.fill_quantity,
                    bar.close,
                    bar.end_time,
                )
                if sold_quantity >= quantity:
                    break
            else:
                last_exit_error = f"exit_execution:{decision.reason}"

    if sold_quantity < quantity or final_fill_time is None or active_exit_kind is None:
        raise RuntimeError(
            last_exit_error
            or "partial-runner exit remained partially filled after final historical observation"
        )

    combined_exit_price = exit_notional / sold_quantity
    exit_reference = reference_notional / sold_quantity
    pnl = combined_exit_price - entry
    weighted_hold = hold_minute_quantity / sold_quantity
    partial_vwap = partial_notional / partial_filled if partial_filled > 0 else None

    return PartialRunnerReplayTrade(
        instrument_id=candidate.instrument_id,
        entry_time=baseline_trade.entry_time,
        baseline_exit_time=baseline_trade.exit_time,
        entry_price=entry,
        stop_price=stop,
        entry_fill_quantity=quantity,
        partial_target_price=target_price,
        partial_target_quantity=target_quantity,
        partial_filled_quantity=partial_filled,
        partial_fill_vwap=partial_vwap,
        partial_fill_count=partial_fill_count,
        runner_exit_reason=active_exit_kind,
        runner_indicator_reason_codes=(
            active_indicator_reasons if active_exit_kind == "indicator" else ()
        ),
        final_fill_time=final_fill_time,
        combined_exit_price=combined_exit_price,
        exit_slippage_bps=_adverse_sell_slippage_bps(combined_exit_price, exit_reference),
        exit_fill_count=fill_count,
        exit_partially_filled=fill_count > 1,
        pnl_per_share=pnl,
        r_multiple=pnl / risk,
        mfe_r=(max_high - entry) / risk,
        mae_r=(min_low - entry) / risk,
        weighted_hold_minutes=weighted_hold,
        final_hold_minutes=Decimal(
            str((final_fill_time - baseline_trade.entry_time).total_seconds() / 60)
        ),
    )


__all__ = [
    "PartialRunnerReplayTrade",
    "partial_target_quantity",
    "replay_partial_profit_runner",
]
