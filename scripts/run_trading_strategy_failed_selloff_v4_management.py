from __future__ import annotations

"""Cache-only V4 research for causal profit protection.

V2 produced a small positive full-development expectancy, but two losing trades
first reached >+1R MFE and later fell through the original selloff-low stop. This
script keeps entry selection fixed and studies a conservative management rule:
only after a *previous finalized execution bar* has reached a configured R
threshold may the stop for the next bar be raised to breakeven or +0.25R.

Using only prior finalized bars avoids pretending that OHLC data reveals the
intrabar order of a profit excursion and later reversal. Pessimistic stop-before-
target and gap-through fill semantics remain unchanged.
"""

import itertools
from dataclasses import dataclass
from datetime import time, timedelta
from decimal import Decimal

import app.apps.trading.strategy_backtest as _bt
from app.apps.trading.paper import PaperOrder, paper_fill_decision, paper_protection_trigger
from app.apps.trading.strategy_timeframes import resample_final_bars
import scripts.run_trading_strategy_failed_selloff_v2_sweep as _v2


_ORIGINAL_FIND_TRADE = _bt._find_trade
_BASE_RUN_VARIANT = _v2._run_variant
_ACTIVE_MANAGEMENT = None


@dataclass(frozen=True)
class ManagementVariant:
    minimum_premarket_dollar_volume: Decimal
    minimum_tod_rvol: Decimal
    selloff_min_pct: Decimal
    selloff_max_pct: Decimal
    recovery_min_pct: Decimal
    breakout_lookback_bars: int
    bars_after_low: int
    breakout_volume_ratio: Decimal
    last_entry_et: time
    reward_multiple: Decimal
    breakeven_trigger_r: Decimal | None
    protected_stop_r: Decimal
    max_hold_minutes: int

    @property
    def variant_id(self) -> str:
        trigger = "none" if self.breakeven_trigger_r is None else str(self.breakeven_trigger_r)
        return f"v4-be{trigger}-lock{self.protected_stop_r}-hold{self.max_hold_minutes}"


def _grid():
    # Baselines preserve the original stop while varying only maximum hold.
    for hold in (45, 60, 90):
        yield _variant(None, Decimal("0"), hold)
    for trigger, lock_r, hold in itertools.product(
        (Decimal("0.75"), Decimal("1.0"), Decimal("1.25")),
        (Decimal("0"), Decimal("0.25")),
        (45, 60, 90),
    ):
        if lock_r >= trigger:
            continue
        yield _variant(trigger, lock_r, hold)


def _variant(trigger, lock_r, hold):
    return ManagementVariant(
        minimum_premarket_dollar_volume=Decimal("100000"),
        minimum_tod_rvol=Decimal("3"),
        selloff_min_pct=Decimal("8"),
        selloff_max_pct=Decimal("25"),
        recovery_min_pct=Decimal("3"),
        breakout_lookback_bars=1,
        bars_after_low=1,
        breakout_volume_ratio=Decimal("0"),
        last_entry_et=time(11, 30),
        reward_multiple=Decimal("1.5"),
        breakeven_trigger_r=trigger,
        protected_stop_r=lock_r,
        max_hold_minutes=int(hold),
    )


def _managed_find_trade(
    candidate,
    bars,
    config,
    policy,
    *,
    assumed_spread_bps,
    max_hold_minutes,
    quantity=Decimal("1"),
):
    baseline = _ORIGINAL_FIND_TRADE(
        candidate,
        bars,
        config,
        policy,
        assumed_spread_bps=assumed_spread_bps,
        max_hold_minutes=max_hold_minutes,
        quantity=quantity,
    )
    management = _ACTIVE_MANAGEMENT
    if management is None or management.breakeven_trigger_r is None or baseline.trade is None:
        return baseline

    trade = baseline.trade
    execution_bars = tuple(resample_final_bars(bars, config.execution_interval))
    entry_index = trade.entry_bar_index
    if entry_index < 0 or entry_index >= len(execution_bars):
        return baseline
    entry_bar = execution_bars[entry_index]
    entry = trade.entry_price
    initial_stop = trade.stop_price
    target = trade.target_price
    initial_risk = entry - initial_stop
    if initial_risk <= 0:
        return baseline

    trigger_price = entry + initial_risk * management.breakeven_trigger_r
    protected_stop = max(initial_stop, entry + initial_risk * management.protected_stop_r)
    horizon = entry_bar.start_time + timedelta(minutes=max_hold_minutes)
    max_high = entry
    min_low = entry
    exit_price = None
    exit_time = None
    exit_reference = None
    exit_reason = None
    last_exit_rejection = None

    for index in range(entry_index, len(execution_bars)):
        bar = execution_bars[index]
        # The stop used for this bar may only depend on *prior finalized bars*.
        active_stop = protected_stop if max_high >= trigger_price else initial_stop
        max_high = max(max_high, bar.high)
        min_low = min(min_low, bar.low)
        observation = _bt._bar_observation(
            bar,
            instrument_id=candidate.instrument_id,
            binding_id=candidate.binding_id,
            spread_bps=assumed_spread_bps,
            price=bar.open,
        )
        trigger = paper_protection_trigger(
            is_long=True,
            stop_price=active_stop,
            target_price=target,
            observation=observation,
            activated_at=entry_bar.start_time,
        )
        if trigger == "stop":
            order = PaperOrder(
                account_id="backtest",
                order_id=f"managed-stop:{candidate.instrument_id}:{index}",
                instrument_id=candidate.instrument_id,
                binding_id=candidate.binding_id,
                side="sell",
                order_type="stop",
                quantity=trade.entry_fill_quantity,
                stop_price=active_stop,
                idempotency_key=f"managed-stop:{candidate.instrument_id}:{index}",
            )
            decision = paper_fill_decision(order, observation, policy)
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
                and decision.fill_quantity >= trade.entry_fill_quantity
            ):
                exit_price = decision.fill_price
                exit_time = bar.end_time
                exit_reference = active_stop
                exit_reason = "stop"
                break
            last_exit_rejection = f"exit_execution:{decision.reason}"
        elif trigger == "target":
            order = PaperOrder(
                account_id="backtest",
                order_id=f"managed-target:{candidate.instrument_id}:{index}",
                instrument_id=candidate.instrument_id,
                binding_id=candidate.binding_id,
                side="sell",
                order_type="limit",
                quantity=trade.entry_fill_quantity,
                limit_price=target,
                idempotency_key=f"managed-target:{candidate.instrument_id}:{index}",
            )
            decision = paper_fill_decision(
                order,
                observation.model_copy(update={"price": target}),
                policy,
            )
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
                and decision.fill_quantity >= trade.entry_fill_quantity
            ):
                exit_price = decision.fill_price
                exit_time = bar.end_time
                exit_reference = target
                exit_reason = "target"
                break
            last_exit_rejection = f"exit_execution:{decision.reason}"

        if bar.end_time >= horizon:
            decision = _bt._exit_market_decision(
                candidate=candidate,
                bar=bar,
                quantity=trade.entry_fill_quantity,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
                price=bar.close,
                order_suffix="managed-time",
            )
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
                and decision.fill_quantity >= trade.entry_fill_quantity
            ):
                exit_price = decision.fill_price
                exit_time = bar.end_time
                exit_reference = bar.close
                exit_reason = "time"
                break
            last_exit_rejection = f"exit_execution:{decision.reason}"

    if exit_price is None:
        last_bar = execution_bars[-1]
        decision = _bt._exit_market_decision(
            candidate=candidate,
            bar=last_bar,
            quantity=trade.entry_fill_quantity,
            policy=policy,
            assumed_spread_bps=assumed_spread_bps,
            price=last_bar.close,
            order_suffix="managed-eod",
        )
        if (
            decision.should_fill
            and decision.fill_price is not None
            and decision.fill_quantity is not None
            and decision.fill_quantity >= trade.entry_fill_quantity
        ):
            exit_price = decision.fill_price
            exit_time = last_bar.end_time
            exit_reference = last_bar.close
            exit_reason = "eod"
        else:
            last_exit_rejection = f"exit_execution:{decision.reason}"

    if exit_price is None or exit_time is None or exit_reference is None or exit_reason is None:
        return _bt._TradeAttempt(
            trigger_bar_index=baseline.trigger_bar_index,
            rejection_reason=last_exit_rejection or "exit_execution:unfilled",
        )

    pnl = exit_price - entry
    return _bt._TradeAttempt(
        trigger_bar_index=baseline.trigger_bar_index,
        trade=trade.model_copy(
            update={
                "exit_time": exit_time,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl_per_share": pnl,
                "r_multiple": pnl / initial_risk,
                "mfe_r": (max_high - entry) / initial_risk,
                "mae_r": (min_low - entry) / initial_risk,
                "hold_minutes": Decimal(str((exit_time - entry_bar.start_time).total_seconds() / 60)),
                "exit_slippage_bps": _bt._adverse_sell_slippage_bps(exit_price, exit_reference),
            }
        ),
    )


def _run_variant(variant, datasets, *, initial_cash, spread, max_hold_minutes):
    global _ACTIVE_MANAGEMENT
    _ACTIVE_MANAGEMENT = variant
    return _BASE_RUN_VARIANT(
        variant,
        datasets,
        initial_cash=initial_cash,
        spread=spread,
        max_hold_minutes=variant.max_hold_minutes,
    )


def _rank_key(row):
    trades = row.get("trades") or []
    n = int(row["trade_count"])
    expectancy = Decimal(str(row["expectancy_r"])) if row.get("expectancy_r") is not None else Decimal("-999")
    worst = min((Decimal(str(t["r_multiple"])) for t in trades), default=Decimal("-999"))
    pnl = Decimal(str(row["pnl"]))
    dd = Decimal(str(row["max_drawdown_pct"]))
    return (
        1 if n >= 8 and expectancy > 0 else 0,
        1 if n >= 5 and expectancy > 0 else 0,
        min(n, 20),
        expectancy,
        worst,
        pnl,
        -dd,
    )


def main() -> int:
    _bt._find_trade = _managed_find_trade
    _v2._grid = _grid
    _v2._run_variant = _run_variant
    _v2._rank_key = _rank_key
    return _v2.main()


if __name__ == "__main__":
    raise SystemExit(main())
