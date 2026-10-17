"""Research-only loss-control ablations for STOCH_RSI_5M_EARLY_SINGLE.

Every arm starts from the canonical early-single evaluator and the same 150%
pre-entry regular-session range cap.  Each non-baseline arm changes exactly one
causal rule so replay results remain attributable.  The module has no broker or
order side effects.

The entry filters intentionally veto the first canonical early-single trade
rather than searching for a later setup.  That preserves the parent strategy's
"first trade only" semantics.  The structural-stop and early-failure arms keep
the canonical entry and may only move the exit earlier.  The recovery-high arm
may delay the canonical entry until a causal close above the recovery candle's
high, then enters on the following five-minute open.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Literal

from .indicator_signals import _stochastic_rsi_aligned
from .indicators.engine import exponential_moving_average
from .models import MarketBar
from .strategies.gap_pullback import session_vwap
from .strategies.models import StochRsi5mConfig
from .strategy_stoch_rsi_5m import StochRsi5mSnapshot, StochRsi5mTrade
from .strategy_stoch_rsi_5m_early_single import evaluate_stoch_rsi_5m_early_single
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_PRE_ENTRY_RANGE_CAP_PCT = Decimal("150")
_GUARDED_EMA_PERIOD = 50
_GUARDED_EMA_SLOPE_LOOKBACK_BARS = 3
_EARLY_FAILURE_CHECK_BARS = 2  # two completed 5m bars = 10 minutes
_EARLY_FAILURE_MAX_MFE_PCT = Decimal("1")

StochRsiEarlySingleLossControlArm = Literal[
    "baseline_cap150",
    "structural_stop",
    "early_failure_exit",
    "ema_slope_positive",
    "above_vwap",
    "recovery_volume_1x",
    "recovery_volume_1_25x",
    "recovery_high_break",
]


def _regular_5m_bars(
    bars: list[MarketBar] | tuple[MarketBar, ...],
) -> list[MarketBar]:
    return sorted(
        (
            bar
            for bar in bars
            if bar.is_final and bar.session == "regular" and bar.interval == "5m"
        ),
        key=lambda bar: bar.start_time,
    )


def _trade_indexes(
    sampled: list[MarketBar],
    trade: StochRsi5mTrade,
) -> tuple[int, int, int] | None:
    arm_index = next(
        (index for index, bar in enumerate(sampled) if bar.end_time == trade.oversold_arm_time),
        None,
    )
    signal_index = next(
        (index for index, bar in enumerate(sampled) if bar.end_time == trade.entry_signal_time),
        None,
    )
    entry_index = next(
        (index for index, bar in enumerate(sampled) if bar.start_time == trade.entry_time),
        None,
    )
    if arm_index is None or signal_index is None or entry_index is None:
        return None
    return arm_index, signal_index, entry_index


def _session_start_index(sampled: list[MarketBar], entry_index: int) -> int:
    session_date = sampled[entry_index].start_time.astimezone(_ET).date()
    return next(
        index
        for index, bar in enumerate(sampled)
        if bar.start_time.astimezone(_ET).date() == session_date
    )


def _pre_entry_range_pct(
    sampled: list[MarketBar],
    *,
    session_start_index: int,
    signal_index: int,
) -> Decimal | None:
    prefix = sampled[session_start_index : signal_index + 1]
    if not prefix:
        return None
    low = min(bar.low for bar in prefix)
    if low <= 0:
        return None
    high = max(bar.high for bar in prefix)
    return (high - low) / low * Decimal("100")


def _ema_at(values: list[Decimal], *, index: int, period: int) -> Decimal | None:
    ema_index = index - (period - 1)
    if not 0 <= ema_index < len(values):
        return None
    return values[ema_index]


def _guarded_ema_slope_pct(
    sampled: list[MarketBar],
    *,
    signal_index: int,
) -> Decimal | None:
    values = exponential_moving_average(
        (bar.close for bar in sampled),
        _GUARDED_EMA_PERIOD,
    )
    current = _ema_at(values, index=signal_index, period=_GUARDED_EMA_PERIOD)
    prior = _ema_at(
        values,
        index=signal_index - _GUARDED_EMA_SLOPE_LOOKBACK_BARS,
        period=_GUARDED_EMA_PERIOD,
    )
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / prior * Decimal("100")


def _recovery_volume_ratio(
    sampled: list[MarketBar],
    *,
    session_start_index: int,
    arm_index: int,
    signal_index: int,
) -> Decimal | None:
    reference = sampled[arm_index:signal_index]
    if not reference:
        reference = sampled[max(session_start_index, signal_index - 3) : signal_index]
    if not reference:
        return None
    average_volume = sum((bar.volume for bar in reference), Decimal("0")) / Decimal(
        len(reference)
    )
    if average_volume <= 0:
        return None
    return sampled[signal_index].volume / average_volume


def _clear_trade_snapshot(
    snapshot: StochRsi5mSnapshot,
    *,
    reason_code: str,
) -> StochRsi5mSnapshot:
    return snapshot.model_copy(
        update={
            "state": "waiting_oversold",
            "reason_code": reason_code,
            "oversold_arm_time": None,
            "momentum_cross_time": None,
            "entry_signal_time": None,
            "entry_time": None,
            "entry_price": None,
            "exit_signal_time": None,
            "exit_time": None,
            "exit_price": None,
            "return_pct": None,
            "trades": (),
        }
    )


def _snapshot_with_trade(
    snapshot: StochRsi5mSnapshot,
    trade: StochRsi5mTrade,
) -> StochRsi5mSnapshot:
    state = "force_flat" if trade.exit_reason_code == "STOCH_RSI_5M_FORCE_FLAT" else "exited"
    return snapshot.model_copy(
        update={
            "state": state,
            "reason_code": trade.exit_reason_code,
            "oversold_arm_time": trade.oversold_arm_time,
            "momentum_cross_time": trade.momentum_cross_time,
            "entry_signal_time": trade.entry_signal_time,
            "entry_time": trade.entry_time,
            "entry_price": trade.entry_price,
            "exit_signal_time": trade.exit_signal_time,
            "exit_time": trade.exit_time,
            "exit_price": trade.exit_price,
            "return_pct": trade.return_pct,
            "trades": (trade,),
        }
    )


def _modified_trade(
    trade: StochRsi5mTrade,
    *,
    entry_time=None,
    entry_price: Decimal | None = None,
    exit_signal_time=None,
    exit_time=None,
    exit_price: Decimal | None = None,
    exit_reason_code: str | None = None,
) -> StochRsi5mTrade:
    new_entry_time = entry_time if entry_time is not None else trade.entry_time
    new_entry_price = entry_price if entry_price is not None else trade.entry_price
    new_exit_signal_time = (
        exit_signal_time if exit_signal_time is not None else trade.exit_signal_time
    )
    new_exit_time = exit_time if exit_time is not None else trade.exit_time
    new_exit_price = exit_price if exit_price is not None else trade.exit_price
    return trade.model_copy(
        update={
            "entry_time": new_entry_time,
            "entry_price": new_entry_price,
            "exit_signal_time": new_exit_signal_time,
            "exit_time": new_exit_time,
            "exit_price": new_exit_price,
            "exit_reason_code": exit_reason_code or trade.exit_reason_code,
            "return_pct": (new_exit_price - new_entry_price)
            / new_entry_price
            * Decimal("100"),
        }
    )


def _structural_stop_trade(
    sampled: list[MarketBar],
    trade: StochRsi5mTrade,
    *,
    arm_index: int,
    signal_index: int,
    entry_index: int,
    force_flat_et: time,
) -> StochRsi5mTrade:
    stop_price = min(bar.low for bar in sampled[arm_index : signal_index + 1])
    session_date = sampled[entry_index].start_time.astimezone(_ET).date()
    for index in range(entry_index, len(sampled)):
        bar = sampled[index]
        if bar.start_time.astimezone(_ET).date() != session_date:
            break
        if bar.start_time >= trade.exit_time:
            break
        if bar.close >= stop_price:
            continue
        next_index = index + 1
        if next_index >= len(sampled):
            break
        next_bar = sampled[next_index]
        if next_bar.start_time.astimezone(_ET).date() != session_date:
            break
        if next_bar.start_time.astimezone(_ET).time() > force_flat_et:
            break
        if next_bar.start_time >= trade.exit_time:
            break
        return _modified_trade(
            trade,
            exit_signal_time=bar.end_time,
            exit_time=next_bar.start_time,
            exit_price=next_bar.open,
            exit_reason_code="STOCH_RSI_5M_EARLY_SINGLE_STRUCTURAL_STOP",
        )
    return trade


def _early_failure_trade(
    sampled: list[MarketBar],
    trade: StochRsi5mTrade,
    *,
    session_start_index: int,
    entry_index: int,
) -> StochRsi5mTrade:
    check_index = entry_index + (_EARLY_FAILURE_CHECK_BARS - 1)
    next_index = check_index + 1
    if next_index >= len(sampled):
        return trade
    check_bar = sampled[check_index]
    next_bar = sampled[next_index]
    session_date = sampled[entry_index].start_time.astimezone(_ET).date()
    if (
        check_bar.start_time.astimezone(_ET).date() != session_date
        or next_bar.start_time.astimezone(_ET).date() != session_date
        or next_bar.start_time >= trade.exit_time
    ):
        return trade
    observed = sampled[entry_index : check_index + 1]
    mfe_pct = (
        max(bar.high for bar in observed) - trade.entry_price
    ) / trade.entry_price * Decimal("100")
    current_vwap = session_vwap(sampled[session_start_index : check_index + 1])
    if (
        current_vwap is not None
        and mfe_pct <= _EARLY_FAILURE_MAX_MFE_PCT
        and check_bar.close < trade.entry_price
        and check_bar.close < current_vwap
    ):
        return _modified_trade(
            trade,
            exit_signal_time=check_bar.end_time,
            exit_time=next_bar.start_time,
            exit_price=next_bar.open,
            exit_reason_code="STOCH_RSI_5M_EARLY_SINGLE_EARLY_FAILURE_10M",
        )
    return trade


def _recovery_high_break_trade(
    sampled: list[MarketBar],
    trade: StochRsi5mTrade,
    *,
    signal_index: int,
    active: StochRsi5mConfig,
) -> StochRsi5mTrade | None:
    closes = [bar.close for bar in sampled]
    k_values, d_values = _stochastic_rsi_aligned(
        closes,
        rsi_period=active.rsi_period,
        stochastic_period=active.stochastic_period,
        smoothing_period=active.k_smoothing_period,
        signal_period=active.d_smoothing_period,
    )
    signal_bar = sampled[signal_index]
    session_date = signal_bar.start_time.astimezone(_ET).date()
    breakout_index: int | None = None
    for candidate_index in range(signal_index + 1, len(sampled)):
        candidate = sampled[candidate_index]
        if candidate.start_time.astimezone(_ET).date() != session_date:
            break
        if candidate.start_time >= trade.exit_time:
            break
        candidate_k = k_values[candidate_index] if candidate_index < len(k_values) else None
        candidate_d = d_values[candidate_index] if candidate_index < len(d_values) else None
        if (
            candidate_k is None
            or candidate_d is None
            or candidate_k < active.recovery_threshold
            or candidate_k <= candidate_d
        ):
            return None
        if candidate.close > signal_bar.high:
            breakout_index = candidate_index
            break
    if breakout_index is None:
        return None

    next_index = breakout_index + 1
    if next_index >= len(sampled):
        return None
    next_bar = sampled[next_index]
    if (
        next_bar.start_time.astimezone(_ET).date() != session_date
        or not (active.entry_start_et <= next_bar.start_time.astimezone(_ET).time() <= active.last_entry_et)
        or next_bar.start_time >= trade.exit_time
    ):
        return None

    # Preserve the target branch's v15 price-confirmation semantics at the
    # delayed entry: its canonical evaluator uses a 5-period five-minute EMA.
    ema_values = exponential_moving_average((bar.close for bar in sampled), 5)
    signal_ema = _ema_at(ema_values, index=breakout_index, period=5)
    if signal_ema is None or next_bar.open <= signal_ema:
        return None

    return _modified_trade(
        trade,
        entry_time=next_bar.start_time,
        entry_price=next_bar.open,
    )


def evaluate_stoch_rsi_5m_early_single_loss_control(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    arm: StochRsiEarlySingleLossControlArm,
    config: StochRsi5mConfig | None = None,
) -> StochRsi5mSnapshot:
    """Evaluate one isolated loss-control arm on the early-single parent."""

    active = config or StochRsi5mConfig()
    snapshot = evaluate_stoch_rsi_5m_early_single(bars, active)
    if not snapshot.trades:
        return snapshot

    trade = snapshot.trades[0]
    sampled = _regular_5m_bars(bars)
    indexes = _trade_indexes(sampled, trade)
    if indexes is None:
        return _clear_trade_snapshot(
            snapshot,
            reason_code="STOCH_RSI_5M_EARLY_SINGLE_LOSS_CONTROL_METADATA_UNAVAILABLE",
        )
    arm_index, signal_index, entry_index = indexes
    session_start_index = _session_start_index(sampled, entry_index)

    pre_entry_range = _pre_entry_range_pct(
        sampled,
        session_start_index=session_start_index,
        signal_index=signal_index,
    )
    if pre_entry_range is None or pre_entry_range > _PRE_ENTRY_RANGE_CAP_PCT:
        return _clear_trade_snapshot(
            snapshot,
            reason_code="STOCH_RSI_5M_EARLY_SINGLE_PRE_ENTRY_RANGE_ABOVE_150",
        )

    if arm == "baseline_cap150":
        return _snapshot_with_trade(snapshot, trade)

    if arm == "structural_stop":
        trade = _structural_stop_trade(
            sampled,
            trade,
            arm_index=arm_index,
            signal_index=signal_index,
            entry_index=entry_index,
            force_flat_et=active.force_flat_et,
        )
        return _snapshot_with_trade(snapshot, trade)

    if arm == "early_failure_exit":
        trade = _early_failure_trade(
            sampled,
            trade,
            session_start_index=session_start_index,
            entry_index=entry_index,
        )
        return _snapshot_with_trade(snapshot, trade)

    if arm == "ema_slope_positive":
        slope = _guarded_ema_slope_pct(sampled, signal_index=signal_index)
        if slope is None or slope <= 0:
            return _clear_trade_snapshot(
                snapshot,
                reason_code="STOCH_RSI_5M_EARLY_SINGLE_EMA_SLOPE_NOT_POSITIVE",
            )
        return _snapshot_with_trade(snapshot, trade)

    if arm == "above_vwap":
        current_vwap = session_vwap(sampled[session_start_index : signal_index + 1])
        if (
            current_vwap is None
            or sampled[signal_index].close <= current_vwap
            or sampled[entry_index].open <= current_vwap
        ):
            return _clear_trade_snapshot(
                snapshot,
                reason_code="STOCH_RSI_5M_EARLY_SINGLE_NOT_ABOVE_VWAP",
            )
        return _snapshot_with_trade(snapshot, trade)

    if arm in {"recovery_volume_1x", "recovery_volume_1_25x"}:
        minimum = Decimal("1") if arm == "recovery_volume_1x" else Decimal("1.25")
        ratio = _recovery_volume_ratio(
            sampled,
            session_start_index=session_start_index,
            arm_index=arm_index,
            signal_index=signal_index,
        )
        if ratio is None or ratio < minimum:
            suffix = "1_00" if minimum == Decimal("1") else "1_25"
            return _clear_trade_snapshot(
                snapshot,
                reason_code=f"STOCH_RSI_5M_EARLY_SINGLE_RECOVERY_VOLUME_BELOW_{suffix}",
            )
        return _snapshot_with_trade(snapshot, trade)

    if arm == "recovery_high_break":
        delayed = _recovery_high_break_trade(
            sampled,
            trade,
            signal_index=signal_index,
            active=active,
        )
        if delayed is None:
            return _clear_trade_snapshot(
                snapshot,
                reason_code="STOCH_RSI_5M_EARLY_SINGLE_RECOVERY_HIGH_BREAK_NOT_CONFIRMED",
            )
        return _snapshot_with_trade(snapshot, delayed)

    raise ValueError(f"unsupported early-single loss-control arm: {arm}")


__all__ = [
    "StochRsiEarlySingleLossControlArm",
    "evaluate_stoch_rsi_5m_early_single_loss_control",
]
