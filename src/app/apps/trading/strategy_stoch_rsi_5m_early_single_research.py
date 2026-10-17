"""Second-generation research arms for STOCH_RSI_5M_EARLY_SINGLE.

Every arm starts from the frozen ``baseline_cap150`` evaluator. Child arms
change exactly one research hypothesis: time-of-day admission, one-minute loss
control, VWAP reclaim timing, early-failure behavior, or deterministic reversal
structure. None has broker/order authority.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, cast

from .indicators.engine import average_true_range, exponential_moving_average
from .models import MarketBar
from .strategies.gap_pullback import session_vwap
from .strategies.models import StochRsi5mConfig
from .strategy_stoch_rsi_5m import StochRsi5mSnapshot, StochRsi5mTrade
from .strategy_stoch_rsi_5m_early_single_loss_controls import (
    evaluate_stoch_rsi_5m_early_single_loss_control,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_ATR_PERIOD_1M = 14
_ATR_MULTIPLE_1M = Decimal("2")
_PATTERN_BOUNCE_PCT = Decimal("2")
_HIGHER_LOW_BUFFER_PCT = Decimal("0.5")
_DOUBLE_BOTTOM_TOLERANCE_PCT = Decimal("3")
_FAILED_SELLOFF_TOLERANCE_PCT = Decimal("0.5")
_VOLUME_EXHAUSTION_RATIO = Decimal("0.80")

StochRsiEarlySingleResearchArm = Literal[
    "no_entry_1030_1100",
    "hard_stop_2pct",
    "hard_stop_3pct",
    "hard_stop_4pct",
    "hard_stop_5pct",
    "atr_stop_2x_14",
    "vwap_reclaim_1bar",
    "vwap_reclaim_2bar",
    "vwap_reclaim_3bar",
    "failed_selloff",
    "higher_low",
    "lower_high_break",
    "double_bottom",
    "volume_exhaustion",
    "reversal_structure_v1",
    "reversal_structure_aggressive_v1",
    "early_failure_5m_entry_any",
    "early_failure_5m_entry_mfe1",
    "early_failure_5m_entry_mfe2",
    "early_failure_5m_entry_mfe3",
    "early_failure_5m_vwap_any",
    "early_failure_5m_vwap_mfe1",
    "early_failure_5m_vwap_mfe2",
    "early_failure_5m_vwap_mfe3",
    "early_failure_10m_entry_any",
    "early_failure_10m_entry_mfe1",
    "early_failure_10m_entry_mfe2",
    "early_failure_10m_entry_mfe3",
    "early_failure_10m_vwap_any",
    "early_failure_10m_vwap_mfe1",
    "early_failure_10m_vwap_mfe2",
    "early_failure_10m_vwap_mfe3",
    "early_failure_15m_entry_any",
    "early_failure_15m_entry_mfe1",
    "early_failure_15m_entry_mfe2",
    "early_failure_15m_entry_mfe3",
    "early_failure_15m_vwap_any",
    "early_failure_15m_vwap_mfe1",
    "early_failure_15m_vwap_mfe2",
    "early_failure_15m_vwap_mfe3",
]

EARLY_FAILURE_ARM_SPECS = MappingProxyType(
    {
        f"early_failure_{minutes}m_{token}_{mfe_token}": (minutes, condition, mfe)
        for minutes in (5, 10, 15)
        for condition, token in (("entry", "entry"), ("vwap", "vwap"))
        for mfe_token, mfe in (
            ("any", None),
            ("mfe1", Decimal("1")),
            ("mfe2", Decimal("2")),
            ("mfe3", Decimal("3")),
        )
    }
)

ONE_MINUTE_STOP_ARMS = {
    "hard_stop_2pct": Decimal("2"),
    "hard_stop_3pct": Decimal("3"),
    "hard_stop_4pct": Decimal("4"),
    "hard_stop_5pct": Decimal("5"),
}
VWAP_RECLAIM_ARMS = {
    "vwap_reclaim_1bar": 1,
    "vwap_reclaim_2bar": 2,
    "vwap_reclaim_3bar": 3,
}
PATTERN_ARMS = {
    "failed_selloff",
    "higher_low",
    "lower_high_break",
    "double_bottom",
    "volume_exhaustion",
    "reversal_structure_v1",
    "reversal_structure_aggressive_v1",
}


def _regular_bars(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    interval: str,
) -> list[MarketBar]:
    return sorted(
        (
            bar
            for bar in bars
            if bar.is_final and bar.session == "regular" and bar.interval == interval
        ),
        key=lambda bar: bar.start_time,
    )


def _trade_indexes(
    sampled: list[MarketBar],
    trade: StochRsi5mTrade,
) -> tuple[int, int, int] | None:
    arm_index = next(
        (i for i, bar in enumerate(sampled) if bar.end_time == trade.oversold_arm_time),
        None,
    )
    signal_index = next(
        (i for i, bar in enumerate(sampled) if bar.end_time == trade.entry_signal_time),
        None,
    )
    entry_index = next(
        (i for i, bar in enumerate(sampled) if bar.start_time == trade.entry_time),
        None,
    )
    if arm_index is None or signal_index is None or entry_index is None:
        return None
    return arm_index, signal_index, entry_index


def _session_start_index(sampled: list[MarketBar], entry_index: int) -> int:
    session_date = sampled[entry_index].start_time.astimezone(_ET).date()
    return next(
        i
        for i, bar in enumerate(sampled)
        if bar.start_time.astimezone(_ET).date() == session_date
    )


def _clear_trade(
    snapshot: StochRsi5mSnapshot,
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


def _with_trade(
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


def _entry_after_confirmation(
    sampled: list[MarketBar],
    trade: StochRsi5mTrade,
    *,
    confirm_index: int,
    active: StochRsi5mConfig,
) -> StochRsi5mTrade | None:
    if sampled[confirm_index].end_time <= trade.entry_time:
        return trade
    next_index = confirm_index + 1
    if next_index >= len(sampled):
        return None
    next_bar = sampled[next_index]
    session_date = trade.entry_time.astimezone(_ET).date()
    local_time = next_bar.start_time.astimezone(_ET).time()
    if (
        next_bar.start_time.astimezone(_ET).date() != session_date
        or not (active.entry_start_et <= local_time <= active.last_entry_et)
        or next_bar.start_time >= trade.exit_time
    ):
        return None

    # Preserve the target branch's canonical 5-period 5m EMA price confirmation.
    ema_values = exponential_moving_average((bar.close for bar in sampled), 5)
    ema_index = confirm_index - 4
    if not 0 <= ema_index < len(ema_values):
        return None
    if next_bar.open <= ema_values[ema_index]:
        return None
    return _modified_trade(
        trade,
        entry_time=next_bar.start_time,
        entry_price=next_bar.open,
    )


def _one_minute_stop_trade(
    one_minute: list[MarketBar],
    trade: StochRsi5mTrade,
    *,
    stop_price: Decimal,
    reason_code: str,
) -> StochRsi5mTrade:
    session_date = trade.entry_time.astimezone(_ET).date()
    for bar in one_minute:
        if bar.start_time.astimezone(_ET).date() != session_date:
            continue
        if bar.start_time < trade.entry_time:
            continue
        if bar.start_time >= trade.exit_time:
            break
        if bar.open <= stop_price:
            fill = bar.open
        elif bar.low <= stop_price:
            fill = stop_price
        else:
            continue
        return _modified_trade(
            trade,
            exit_signal_time=bar.end_time,
            exit_time=bar.start_time,
            exit_price=fill,
            exit_reason_code=reason_code,
        )
    return trade


def _atr_stop_price(
    one_minute: list[MarketBar],
    trade: StochRsi5mTrade,
) -> Decimal | None:
    prefix = [
        bar
        for bar in one_minute
        if bar.end_time <= trade.entry_time
        and bar.start_time.astimezone(_ET).date()
        == trade.entry_time.astimezone(_ET).date()
    ]
    values = average_true_range(
        [bar.high for bar in prefix],
        [bar.low for bar in prefix],
        [bar.close for bar in prefix],
        _ATR_PERIOD_1M,
    )
    if not values:
        return None
    stop = trade.entry_price - values[-1] * _ATR_MULTIPLE_1M
    return stop if stop > 0 else None


def _vwap_reclaim_trade(
    sampled: list[MarketBar],
    trade: StochRsi5mTrade,
    *,
    entry_index: int,
    session_start_index: int,
    max_wait_bars: int,
    active: StochRsi5mConfig,
) -> StochRsi5mTrade | None:
    reference = sampled[session_start_index:entry_index]
    entry_vwap = session_vwap(reference)
    if entry_vwap is None:
        return None
    if trade.entry_price > entry_vwap:
        return trade

    session_date = trade.entry_time.astimezone(_ET).date()
    final_index = min(len(sampled), entry_index + max_wait_bars)
    for candidate_index in range(entry_index, final_index):
        candidate = sampled[candidate_index]
        if candidate.start_time.astimezone(_ET).date() != session_date:
            break
        if candidate.start_time >= trade.exit_time:
            break
        current_vwap = session_vwap(sampled[session_start_index : candidate_index + 1])
        if current_vwap is None or candidate.close <= current_vwap:
            continue
        delayed = _entry_after_confirmation(
            sampled,
            trade,
            confirm_index=candidate_index,
            active=active,
        )
        if delayed is None:
            continue
        next_index = candidate_index + 1
        next_vwap = session_vwap(sampled[session_start_index : candidate_index + 1])
        if next_vwap is not None and sampled[next_index].open > next_vwap:
            return delayed
    return None


def _early_failure_trade(
    sampled: list[MarketBar],
    trade: StochRsi5mTrade,
    *,
    entry_index: int,
    session_start_index: int,
    checkpoint_minutes: int,
    condition: str,
    max_mfe_pct: Decimal | None,
) -> StochRsi5mTrade:
    bars_to_check = checkpoint_minutes // 5
    check_index = entry_index + bars_to_check - 1
    next_index = check_index + 1
    if check_index >= len(sampled) or next_index >= len(sampled):
        return trade
    check_bar = sampled[check_index]
    next_bar = sampled[next_index]
    session_date = trade.entry_time.astimezone(_ET).date()
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
    if max_mfe_pct is not None and mfe_pct > max_mfe_pct:
        return trade

    if condition == "entry":
        failed = check_bar.close < trade.entry_price
    elif condition == "vwap":
        current_vwap = session_vwap(sampled[session_start_index : check_index + 1])
        failed = current_vwap is not None and check_bar.close < current_vwap
    else:
        raise ValueError(f"unsupported early-failure condition: {condition}")
    if not failed:
        return trade

    mfe_token = "ANY" if max_mfe_pct is None else str(max_mfe_pct).replace(".", "_")
    return _modified_trade(
        trade,
        exit_signal_time=check_bar.end_time,
        exit_time=next_bar.start_time,
        exit_price=next_bar.open,
        exit_reason_code=(
            f"STOCH_RSI_5M_EARLY_SINGLE_EARLY_FAILURE_{checkpoint_minutes}M_"
            f"{condition.upper()}_MFE_{mfe_token}"
        ),
    )


def _selloff_low(
    sampled: list[MarketBar],
    *,
    arm_index: int,
    signal_index: int,
) -> tuple[int, Decimal]:
    low_index = min(
        range(arm_index, signal_index + 1),
        key=lambda index: sampled[index].low,
    )
    return low_index, sampled[low_index].low


def _failed_selloff_confirmation(
    sampled: list[MarketBar],
    *,
    session_start_index: int,
    arm_index: int,
    signal_index: int,
    stop_index: int,
) -> int | None:
    prior_start = max(session_start_index, arm_index - 3)
    prior = sampled[prior_start:arm_index]
    if not prior:
        return None
    prior_low = min(bar.low for bar in prior)
    undercut_limit = prior_low * (
        Decimal("1") + _FAILED_SELLOFF_TOLERANCE_PCT / Decimal("100")
    )
    for index in range(arm_index, min(stop_index + 1, len(sampled))):
        bar = sampled[index]
        if bar.low <= undercut_limit and bar.close > prior_low:
            return index
    return None


def _higher_low_confirmation(
    sampled: list[MarketBar],
    *,
    arm_index: int,
    signal_index: int,
    stop_index: int,
) -> int | None:
    low1_index, low1 = _selloff_low(
        sampled,
        arm_index=arm_index,
        signal_index=signal_index,
    )
    bounce_level = low1 * (Decimal("1") + _PATTERN_BOUNCE_PCT / Decimal("100"))
    bounce_index = next(
        (
            index
            for index in range(low1_index + 1, min(stop_index + 1, len(sampled)))
            if sampled[index].high >= bounce_level
        ),
        None,
    )
    if bounce_index is None:
        return None

    higher_low_floor = low1 * (
        Decimal("1") + _HIGHER_LOW_BUFFER_PCT / Decimal("100")
    )
    for index in range(bounce_index + 1, min(stop_index, len(sampled) - 1)):
        bar = sampled[index]
        next_bar = sampled[index + 1]
        if bar.low > higher_low_floor and next_bar.close > bar.close:
            return index + 1
    return None


def _last_pre_low_pivot_high(
    sampled: list[MarketBar],
    *,
    session_start_index: int,
    low_index: int,
) -> Decimal | None:
    start = max(session_start_index + 1, low_index - 8)
    pivots = [
        sampled[index].high
        for index in range(start, low_index)
        if (
            sampled[index].high >= sampled[index - 1].high
            and index + 1 < len(sampled)
            and sampled[index].high > sampled[index + 1].high
        )
    ]
    if pivots:
        return pivots[-1]
    fallback = sampled[max(session_start_index, low_index - 3) : low_index]
    return max((bar.high for bar in fallback), default=None)


def _lower_high_break_confirmation(
    sampled: list[MarketBar],
    *,
    session_start_index: int,
    arm_index: int,
    signal_index: int,
    stop_index: int,
) -> int | None:
    low_index, _low = _selloff_low(
        sampled,
        arm_index=arm_index,
        signal_index=signal_index,
    )
    level = _last_pre_low_pivot_high(
        sampled,
        session_start_index=session_start_index,
        low_index=low_index,
    )
    if level is None:
        return None
    for index in range(max(signal_index, low_index + 1), min(stop_index + 1, len(sampled))):
        if sampled[index].close > level:
            return index
    return None


def _double_bottom_confirmation(
    sampled: list[MarketBar],
    *,
    arm_index: int,
    signal_index: int,
    stop_index: int,
) -> int | None:
    low1_index, low1 = _selloff_low(
        sampled,
        arm_index=arm_index,
        signal_index=signal_index,
    )
    tolerance = _DOUBLE_BOTTOM_TOLERANCE_PCT / Decimal("100")
    lower_bound = low1 * (Decimal("1") - tolerance)
    upper_bound = low1 * (Decimal("1") + tolerance)
    for low2_index in range(low1_index + 2, min(stop_index, len(sampled) - 1)):
        low2 = sampled[low2_index].low
        if not (lower_bound <= low2 <= upper_bound):
            continue
        between = sampled[low1_index + 1 : low2_index]
        if not between:
            continue
        neckline = max(bar.high for bar in between)
        for index in range(low2_index + 1, min(stop_index + 1, len(sampled))):
            if sampled[index].close > neckline:
                return index
    return None


def _volume_exhaustion_confirmed(
    sampled: list[MarketBar],
    *,
    session_start_index: int,
    signal_index: int,
) -> bool:
    start = max(session_start_index, signal_index - 6)
    prior = sampled[start:signal_index]
    if len(prior) < 5:
        return False
    split = max(2, len(prior) - 2)
    early = prior[:split]
    late = prior[split:]
    early_average = sum((bar.volume for bar in early), Decimal("0")) / Decimal(len(early))
    late_average = sum((bar.volume for bar in late), Decimal("0")) / Decimal(len(late))
    if early_average <= 0:
        return False
    return late_average <= early_average * _VOLUME_EXHAUSTION_RATIO


def _baseline_trade(
    bars_5m: list[MarketBar] | tuple[MarketBar, ...],
    active: StochRsi5mConfig,
) -> StochRsi5mSnapshot:
    return evaluate_stoch_rsi_5m_early_single_loss_control(
        bars_5m,
        "baseline_cap150",
        active,
    )


def evaluate_stoch_rsi_5m_early_single_research_arm(
    bars_5m: list[MarketBar] | tuple[MarketBar, ...],
    arm: StochRsiEarlySingleResearchArm,
    *,
    one_minute_bars: list[MarketBar] | tuple[MarketBar, ...] = (),
    config: StochRsi5mConfig | None = None,
) -> StochRsi5mSnapshot:
    """Evaluate one isolated research arm on the frozen cap150 parent."""

    active = config or StochRsi5mConfig()
    snapshot = _baseline_trade(bars_5m, active)
    if not snapshot.trades:
        return snapshot

    trade = snapshot.trades[0]
    sampled = _regular_bars(bars_5m, "5m")
    indexes = _trade_indexes(sampled, trade)
    if indexes is None:
        return _clear_trade(
            snapshot,
            "STOCH_RSI_5M_EARLY_SINGLE_RESEARCH_METADATA_UNAVAILABLE",
        )
    arm_index, signal_index, entry_index = indexes
    session_start_index = _session_start_index(sampled, entry_index)

    if arm == "no_entry_1030_1100":
        local_time = trade.entry_time.astimezone(_ET).time()
        if time(10, 30) <= local_time < time(11, 0):
            return _clear_trade(
                snapshot,
                "STOCH_RSI_5M_EARLY_SINGLE_NO_ENTRY_1030_1100",
            )
        return _with_trade(snapshot, trade)

    if arm in ONE_MINUTE_STOP_ARMS or arm == "atr_stop_2x_14":
        one_minute = _regular_bars(one_minute_bars, "1m")
        if not one_minute:
            return _clear_trade(
                snapshot,
                "STOCH_RSI_5M_EARLY_SINGLE_1M_STOP_DATA_UNAVAILABLE",
            )
        if arm in ONE_MINUTE_STOP_ARMS:
            stop_pct = ONE_MINUTE_STOP_ARMS[arm]
            stop_price: Decimal | None = trade.entry_price * (
                Decimal("1") - stop_pct / Decimal("100")
            )
            reason = (
                f"STOCH_RSI_5M_EARLY_SINGLE_1M_HARD_STOP_"
                f"{str(stop_pct).replace('.', '_')}PCT"
            )
        else:
            stop_price = _atr_stop_price(one_minute, trade)
            if stop_price is None:
                return _clear_trade(
                    snapshot,
                    "STOCH_RSI_5M_EARLY_SINGLE_1M_ATR_STOP_WARMUP",
                )
            reason = "STOCH_RSI_5M_EARLY_SINGLE_1M_ATR14_2X_STOP"
        return _with_trade(
            snapshot,
            _one_minute_stop_trade(
                one_minute,
                trade,
                stop_price=cast(Decimal, stop_price),
                reason_code=reason,
            ),
        )

    if arm in VWAP_RECLAIM_ARMS:
        delayed = _vwap_reclaim_trade(
            sampled,
            trade,
            entry_index=entry_index,
            session_start_index=session_start_index,
            max_wait_bars=VWAP_RECLAIM_ARMS[arm],
            active=active,
        )
        if delayed is None:
            return _clear_trade(
                snapshot,
                f"STOCH_RSI_5M_EARLY_SINGLE_{arm.upper()}_NOT_CONFIRMED",
            )
        return _with_trade(snapshot, delayed)

    if arm in EARLY_FAILURE_ARM_SPECS:
        checkpoint, condition, max_mfe = EARLY_FAILURE_ARM_SPECS[arm]
        return _with_trade(
            snapshot,
            _early_failure_trade(
                sampled,
                trade,
                entry_index=entry_index,
                session_start_index=session_start_index,
                checkpoint_minutes=checkpoint,
                condition=condition,
                max_mfe_pct=max_mfe,
            ),
        )

    if arm in PATTERN_ARMS:
        stop_index = next(
            (
                index
                for index, bar in enumerate(sampled)
                if bar.start_time >= trade.exit_time
            ),
            len(sampled) - 1,
        )
        failed = _failed_selloff_confirmation(
            sampled,
            session_start_index=session_start_index,
            arm_index=arm_index,
            signal_index=signal_index,
            stop_index=stop_index,
        )
        higher_low = _higher_low_confirmation(
            sampled,
            arm_index=arm_index,
            signal_index=signal_index,
            stop_index=stop_index,
        )
        lower_high_break = _lower_high_break_confirmation(
            sampled,
            session_start_index=session_start_index,
            arm_index=arm_index,
            signal_index=signal_index,
            stop_index=stop_index,
        )
        double_bottom = _double_bottom_confirmation(
            sampled,
            arm_index=arm_index,
            signal_index=signal_index,
            stop_index=stop_index,
        )
        volume_exhaustion = _volume_exhaustion_confirmed(
            sampled,
            session_start_index=session_start_index,
            signal_index=signal_index,
        )

        if arm == "failed_selloff":
            confirmation = failed
        elif arm == "higher_low":
            confirmation = higher_low
        elif arm == "lower_high_break":
            confirmation = lower_high_break
        elif arm == "double_bottom":
            confirmation = double_bottom
        elif arm == "volume_exhaustion":
            if not volume_exhaustion:
                confirmation = None
            else:
                return _with_trade(snapshot, trade)
        elif arm == "reversal_structure_v1":
            confirmation = (
                max(failed, higher_low, lower_high_break)
                if failed is not None
                and higher_low is not None
                and lower_high_break is not None
                else None
            )
        elif arm == "reversal_structure_aggressive_v1":
            confirmation = (
                max(failed, higher_low)
                if failed is not None and higher_low is not None
                else None
            )
        else:
            raise ValueError(f"unsupported pattern arm: {arm}")

        if confirmation is None:
            return _clear_trade(
                snapshot,
                f"STOCH_RSI_5M_EARLY_SINGLE_{arm.upper()}_NOT_CONFIRMED",
            )
        delayed = _entry_after_confirmation(
            sampled,
            trade,
            confirm_index=confirmation,
            active=active,
        )
        if delayed is None:
            return _clear_trade(
                snapshot,
                f"STOCH_RSI_5M_EARLY_SINGLE_{arm.upper()}_ENTRY_UNAVAILABLE",
            )
        return _with_trade(snapshot, delayed)

    raise ValueError(f"unsupported early-single research arm: {arm}")


__all__ = [
    "EARLY_FAILURE_ARM_SPECS",
    "ONE_MINUTE_STOP_ARMS",
    "PATTERN_ARMS",
    "StochRsiEarlySingleResearchArm",
    "VWAP_RECLAIM_ARMS",
    "evaluate_stoch_rsi_5m_early_single_research_arm",
]
