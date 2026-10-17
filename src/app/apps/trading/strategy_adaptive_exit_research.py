from __future__ import annotations

"""Research-only adaptive exit replay for fixed historical entries.

This module has no strategy/order authority. It replays an already-selected
backtest entry with a predeclared trend-following exit so entry selection and
exit management can be compared independently.
"""

from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .gapper_dataset import GapperCandidate
from .indicator_signals import MultiTimeframeIndicatorContext, multi_timeframe_indicator_context
from .models import MarketBar
from .paper import PaperExecutionPolicy, PaperOrder, paper_fill_decision, paper_protection_trigger
from .strategies.models import GapPullbackConfig
from .strategy_backtest import GapPullbackBacktestTrade, _adverse_sell_slippage_bps, _bar_observation
from .strategy_timeframes import resample_final_bars
from .strategy_v2_management import v2_active_stop_for_prior_high
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_FORCE_FLAT_ET = time(15, 55)
_NO_TARGET_MULTIPLE = Decimal("1000000")
ExitKind = Literal["stop", "indicator", "force_flat"]


class AdaptiveExitDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exit: bool = False
    reason_codes: tuple[str, ...] = ()
    one_minute_warning_count: int = 0
    five_minute_trend_break: bool = False
    five_minute_strong_break: bool = False


class AdaptiveExitReplayTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    entry_time: datetime
    baseline_exit_time: datetime
    entry_price: Decimal
    stop_price: Decimal
    entry_fill_quantity: Decimal
    exit_time: datetime
    exit_price: Decimal
    exit_reason: ExitKind
    indicator_reason_codes: tuple[str, ...] = ()
    exit_slippage_bps: Decimal
    exit_fill_count: int = 1
    exit_partially_filled: bool = False
    pnl_per_share: Decimal
    r_multiple: Decimal
    mfe_r: Decimal
    mae_r: Decimal
    hold_minutes: Decimal


def adaptive_exit_deterioration(
    current: MultiTimeframeIndicatorContext,
    previous: MultiTimeframeIndicatorContext | None,
) -> AdaptiveExitDecision:
    """Return the frozen adaptive-exit decision from finalized indicator state.

    A one-minute warning never exits by itself. The five-minute trend must first
    be below a falling EMA9, then either two tactical 1m warnings or one stronger
    5m confirmation must agree. Stoch RSI contributes only on a bearish cross
    after an overbought prior reading; merely being overbought is not bearish.
    """

    one = current.one_minute
    five = current.five_minute
    warnings: list[str] = []
    strong: list[str] = []

    if one.price_above_ema9 is False and one.ema9_rising is False:
        warnings.append("ADAPTIVE_EXIT_1M_EMA_WEAKNESS")
    if one.macd_bullish is False and one.macd_histogram is not None and one.macd_histogram < 0:
        warnings.append("ADAPTIVE_EXIT_1M_MACD_BEARISH")

    if previous is not None:
        prior = previous.one_minute
        if (
            prior.stochastic_rsi_k is not None
            and prior.stochastic_rsi_d is not None
            and one.stochastic_rsi_k is not None
            and one.stochastic_rsi_d is not None
            and prior.stochastic_rsi_k >= Decimal("80")
            and prior.stochastic_rsi_k >= prior.stochastic_rsi_d
            and one.stochastic_rsi_k < one.stochastic_rsi_d
        ):
            warnings.append("ADAPTIVE_EXIT_1M_STOCH_OVERBOUGHT_CROSS_DOWN")

    five_break = five.price_above_ema9 is False and five.ema9_rising is False
    if five.close is not None and five.ema20 is not None and five.close < five.ema20:
        strong.append("ADAPTIVE_EXIT_5M_PRICE_BELOW_EMA20")
    if five.macd_bullish is False and five.stochastic_rsi_bullish is False:
        strong.append("ADAPTIVE_EXIT_5M_MACD_STOCH_BEARISH")

    should_exit = five_break and (len(warnings) >= 2 or bool(strong))
    reasons: list[str] = []
    if should_exit:
        reasons.append("ADAPTIVE_EXIT_5M_TREND_BREAK")
        reasons.extend(warnings)
        reasons.extend(strong)
    return AdaptiveExitDecision(
        exit=should_exit,
        reason_codes=tuple(reasons),
        one_minute_warning_count=len(warnings),
        five_minute_trend_break=five_break,
        five_minute_strong_break=bool(strong),
    )


def _observation(
    *,
    candidate: GapperCandidate,
    bar: MarketBar,
    assumed_spread_bps: Decimal,
    price: Decimal,
    open_only: bool = False,
):
    observation = _bar_observation(
        bar,
        instrument_id=candidate.instrument_id,
        binding_id=candidate.binding_id,
        spread_bps=assumed_spread_bps,
        price=price,
    )
    if open_only:
        observation = observation.model_copy(update={"high": price, "low": price, "price": price})
    return observation


def _market_fill(
    *,
    candidate: GapperCandidate,
    bar: MarketBar,
    total_quantity: Decimal,
    already_filled: Decimal,
    policy: PaperExecutionPolicy,
    assumed_spread_bps: Decimal,
    price: Decimal,
    order_suffix: str,
):
    order = PaperOrder(
        account_id="backtest",
        order_id=f"{order_suffix}:{candidate.instrument_id}",
        instrument_id=candidate.instrument_id,
        binding_id=candidate.binding_id,
        side="sell",
        order_type="market",
        quantity=total_quantity,
        filled_quantity=already_filled,
        idempotency_key=f"{order_suffix}:{candidate.instrument_id}",
    )
    return paper_fill_decision(
        order,
        _observation(
            candidate=candidate,
            bar=bar,
            assumed_spread_bps=assumed_spread_bps,
            price=price,
            open_only=True,
        ),
        policy,
    )


def _stop_fill(
    *,
    candidate: GapperCandidate,
    bar: MarketBar,
    remaining_quantity: Decimal,
    active_stop: Decimal,
    policy: PaperExecutionPolicy,
    assumed_spread_bps: Decimal,
    open_only: bool,
):
    observation = _observation(
        candidate=candidate,
        bar=bar,
        assumed_spread_bps=assumed_spread_bps,
        price=bar.open,
        open_only=open_only,
    )
    order = PaperOrder(
        account_id="backtest",
        order_id=f"adaptive-stop:{candidate.instrument_id}:{bar.start_time.isoformat()}",
        instrument_id=candidate.instrument_id,
        binding_id=candidate.binding_id,
        side="sell",
        order_type="stop",
        quantity=remaining_quantity,
        stop_price=active_stop,
        idempotency_key=f"adaptive-stop:{candidate.instrument_id}:{bar.start_time.isoformat()}",
    )
    return paper_fill_decision(order, observation, policy)


def replay_adaptive_indicator_exit(
    *,
    candidate: GapperCandidate,
    bars: list[MarketBar] | tuple[MarketBar, ...],
    baseline_trade: GapPullbackBacktestTrade,
    config: GapPullbackConfig,
    policy: PaperExecutionPolicy,
    assumed_spread_bps: Decimal,
    force_flat_et: time = _FORCE_FLAT_ET,
) -> AdaptiveExitReplayTrade:
    """Replay policy B from the exact baseline entry/fill/size.

    The initial structural stop and V2 +0.75R -> +0.25R protection are retained.
    There is no fixed profit target and no 60-minute time exit. Indicator exits
    are decided only on finalized bars and submitted at the next 1m bar open.

    A force-flat order becomes irrevocably active at the last finalized bar known
    by 15:55 ET. If historical bar liquidity permits only a partial fill, the
    already-issued market exit may consume later observations until flat; no
    later indicator/discretionary decision is made.
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
    previous_context = multi_timeframe_indicator_context(execution_bars[:entry_index]) if entry_index > 0 else None
    pending_indicator_reasons: tuple[str, ...] | None = None
    active_exit_kind: ExitKind | None = None
    active_indicator_reasons: tuple[str, ...] = ()

    filled_quantity = Decimal("0")
    exit_notional = Decimal("0")
    reference_notional = Decimal("0")
    fill_count = 0
    final_fill_time: datetime | None = None
    last_exit_error: str | None = None

    def record_fill(fill_price: Decimal, fill_quantity: Decimal, reference: Decimal, fill_time: datetime) -> None:
        nonlocal filled_quantity, exit_notional, reference_notional, fill_count, final_fill_time
        accepted = min(fill_quantity, quantity - filled_quantity)
        if accepted <= 0:
            return
        filled_quantity += accepted
        exit_notional += fill_price * accepted
        reference_notional += reference * accepted
        fill_count += 1
        final_fill_time = fill_time

    for index in range(entry_index, len(execution_bars)):
        bar = execution_bars[index]
        remaining = quantity - filled_quantity
        if remaining <= 0:
            break

        active_stop = v2_active_stop_for_prior_high(
            config,
            entry_price=entry,
            initial_stop=stop,
            prior_finalized_high=max_high,
        )

        # A causal indicator decision is submitted at the next bar open.
        if active_exit_kind is None and pending_indicator_reasons is not None:
            active_exit_kind = "indicator"
            active_indicator_reasons = pending_indicator_reasons

        # Once any market exit is active, the decision never changes back. A gap
        # through the already-active protection is priced pessimistically as stop
        # execution for that bar and turns the residual into a stop liquidation.
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
                if decision.should_fill and decision.fill_price is not None and decision.fill_quantity is not None:
                    record_fill(decision.fill_price, decision.fill_quantity, active_stop, bar.start_time)
                    active_exit_kind = "stop"
                    if filled_quantity >= quantity:
                        break
                    continue
                last_exit_error = f"exit_execution:{decision.reason}"

            decision = _market_fill(
                candidate=candidate,
                bar=bar,
                total_quantity=quantity,
                already_filled=filled_quantity,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
                price=bar.open,
                order_suffix=f"adaptive-{active_exit_kind}",
            )
            if decision.should_fill and decision.fill_price is not None and decision.fill_quantity is not None:
                record_fill(decision.fill_price, decision.fill_quantity, bar.open, bar.start_time)
                if filled_quantity >= quantity:
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
        )
        trigger = paper_protection_trigger(
            is_long=True,
            stop_price=active_stop,
            target_price=unreachable_target,
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
            if decision.should_fill and decision.fill_price is not None and decision.fill_quantity is not None:
                record_fill(decision.fill_price, decision.fill_quantity, active_stop, bar.end_time)
                active_exit_kind = "stop"
                if filled_quantity >= quantity:
                    break
                continue
            last_exit_error = f"exit_execution:{decision.reason}"

        context = multi_timeframe_indicator_context(execution_bars[: index + 1])
        deterioration = adaptive_exit_deterioration(context, previous_context)
        previous_context = context
        if deterioration.exit and pending_indicator_reasons is None:
            pending_indicator_reasons = deterioration.reason_codes

        # Force-flat is an execution decision, not an indicator observation. Use
        # the last finalized bar known by 15:55 for the first market fill. Any
        # partial residual stays under that already-active order on later bars.
        if index == force_flat_index and active_exit_kind is None:
            active_exit_kind = "force_flat"
            decision = _market_fill(
                candidate=candidate,
                bar=bar,
                total_quantity=quantity,
                already_filled=filled_quantity,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
                price=bar.close,
                order_suffix="adaptive-force-flat",
            )
            if decision.should_fill and decision.fill_price is not None and decision.fill_quantity is not None:
                record_fill(decision.fill_price, decision.fill_quantity, bar.close, bar.end_time)
                if filled_quantity >= quantity:
                    break
            else:
                last_exit_error = f"exit_execution:{decision.reason}"

    if filled_quantity < quantity or final_fill_time is None or active_exit_kind is None:
        raise RuntimeError(last_exit_error or "adaptive exit remained partially filled after final historical observation")

    exit_price = exit_notional / filled_quantity
    exit_reference = reference_notional / filled_quantity
    pnl = exit_price - entry
    return AdaptiveExitReplayTrade(
        instrument_id=candidate.instrument_id,
        entry_time=baseline_trade.entry_time,
        baseline_exit_time=baseline_trade.exit_time,
        entry_price=entry,
        stop_price=stop,
        entry_fill_quantity=quantity,
        exit_time=final_fill_time,
        exit_price=exit_price,
        exit_reason=active_exit_kind,
        indicator_reason_codes=active_indicator_reasons if active_exit_kind == "indicator" else (),
        exit_slippage_bps=_adverse_sell_slippage_bps(exit_price, exit_reference),
        exit_fill_count=fill_count,
        exit_partially_filled=fill_count > 1,
        pnl_per_share=pnl,
        r_multiple=pnl / risk,
        mfe_r=(max_high - entry) / risk,
        mae_r=(min_low - entry) / risk,
        hold_minutes=Decimal(str((final_fill_time - baseline_trade.entry_time).total_seconds() / 60)),
    )


__all__ = [
    "AdaptiveExitDecision",
    "AdaptiveExitReplayTrade",
    "adaptive_exit_deterioration",
    "replay_adaptive_indicator_exit",
]
