"""Single-trade early-session variant of the five-minute Stoch RSI strategy.

This research variant preserves the canonical evaluator and its normal entry
window, but stops accounting after the first completed trade for a symbol in a
session. It has no broker or execution authority.

Historical-gap recovery can legitimately keep prior-session trades in the
canonical evaluator's evidence tuple.  Early-single is session scoped, so it
must select the first trade whose entry belongs to the evaluator's current
session rather than blindly taking ``snapshot.trades[0]``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from .models import MarketBar
from .strategies.models import StochRsi5mConfig
from .strategy_stoch_rsi_5m import (
    StochRsi5mSnapshot,
    StochRsi5mState,
    evaluate_stoch_rsi_5m,
)
from .strategy_timeframes import resample_final_bars
from app.apps.trading.us_equity_calendar import EASTERN as _ET


PRE_ENTRY_RANGE_CAP_PCT = Decimal("150")


def _completed_pre_entry_bars(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    entry_time: datetime,
) -> list[MarketBar]:
    session_date = entry_time.astimezone(_ET).date()
    return sorted(
        (
            bar
            for bar in bars
            if bar.is_final
            and bar.session == "regular"
            and bar.start_time.astimezone(_ET).date() == session_date
            and bar.end_time <= entry_time
        ),
        key=lambda bar: bar.start_time,
    )


def _reject_pre_entry_range(
    snapshot: StochRsi5mSnapshot,
    *,
    entry_time: datetime,
    completed_bar_count: int,
) -> StochRsi5mSnapshot:
    return snapshot.model_copy(
        update={
            "state": "waiting_oversold",
            "reason_code": "STOCH_RSI_5M_EARLY_SINGLE_PRE_ENTRY_RANGE_ABOVE_150",
            "as_of": entry_time,
            "five_minute_bar_count": completed_bar_count,
            "ema_50_5m": None,
            "stochastic_rsi_k": None,
            "stochastic_rsi_d": None,
            "previous_stochastic_rsi_k": None,
            "previous_stochastic_rsi_d": None,
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


def _pre_entry_range_rejection(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    snapshot: StochRsi5mSnapshot,
    *,
    entry_time: datetime,
) -> StochRsi5mSnapshot | None:
    pre_entry_bars = _completed_pre_entry_bars(bars, entry_time=entry_time)
    if not pre_entry_bars:
        return None
    pre_entry_high = max(bar.high for bar in pre_entry_bars)
    pre_entry_low = min(bar.low for bar in pre_entry_bars)
    pre_entry_range_pct = (
        (pre_entry_high / pre_entry_low - Decimal("1")) * Decimal("100")
        if pre_entry_low > 0
        else Decimal("Infinity")
    )
    if pre_entry_range_pct <= PRE_ENTRY_RANGE_CAP_PCT:
        return None
    source_interval = pre_entry_bars[0].interval
    completed_bar_count = (
        len(pre_entry_bars)
        if source_interval == "5m"
        else len(resample_final_bars(pre_entry_bars, "5m"))
    )
    return _reject_pre_entry_range(
        snapshot,
        entry_time=entry_time,
        completed_bar_count=completed_bar_count,
    )


def _current_session_date(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    snapshot: StochRsi5mSnapshot,
) -> date | None:
    if snapshot.session_date:
        try:
            return date.fromisoformat(snapshot.session_date)
        except ValueError:
            pass
    regular = [
        bar
        for bar in bars
        if bar.is_final and bar.session == "regular"
    ]
    if not regular:
        return None
    return max(regular, key=lambda bar: bar.start_time).start_time.astimezone(_ET).date()


def _clear_prior_session_trade_metadata(
    snapshot: StochRsi5mSnapshot,
) -> StochRsi5mSnapshot:
    state: StochRsi5mState = snapshot.state
    reason_code = snapshot.reason_code
    if state in {"exited", "force_flat"}:
        state = "waiting_oversold"
        reason_code = "STOCH_RSI_5M_EARLY_SINGLE_NO_CURRENT_SESSION_TRADE"
    return snapshot.model_copy(
        update={
            "state": state,
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


def _clear_unadmitted_open_trade(
    snapshot: StochRsi5mSnapshot,
) -> StochRsi5mSnapshot:
    return snapshot.model_copy(
        update={
            "state": "waiting_oversold",
            "reason_code": "STOCH_RSI_5M_EARLY_SINGLE_ENTRY_NOT_ADMITTED",
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


def evaluate_stoch_rsi_5m_early_single(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    config: StochRsi5mConfig | None = None,
    *,
    entry_allowed: Callable[[datetime], bool] | None = None,
) -> StochRsi5mSnapshot:
    """Evaluate canonical Stoch RSI while retaining the first current-session trade.

    ``entry_allowed`` restricts which entry times count, e.g. only entries at or
    after the symbol joined an evolving intraday universe. The first allowed
    trade is the session's single trade; earlier bars still warm up indicators.
    """

    snapshot = evaluate_stoch_rsi_5m(bars, config)
    session_date = _current_session_date(bars, snapshot)
    if not snapshot.trades:
        if (
            entry_allowed is not None
            and snapshot.entry_time is not None
            and not entry_allowed(snapshot.entry_time)
        ):
            return _clear_unadmitted_open_trade(snapshot)
        # A live SHADOW cycle sees the first trade while it is still open. The
        # range cap depends only on pre-entry bars, so veto it at entry rather
        # than reporting a position that is retracted once it exits.
        if (
            snapshot.state in {"long_active", "exit_armed"}
            and snapshot.entry_time is not None
            and session_date is not None
            and snapshot.entry_time.astimezone(_ET).date() == session_date
        ):
            rejected = _pre_entry_range_rejection(
                bars,
                snapshot,
                entry_time=snapshot.entry_time,
            )
            if rejected is not None:
                return rejected
        return snapshot

    current_session_trades = tuple(
        trade
        for trade in snapshot.trades
        if session_date is not None
        and trade.entry_time.astimezone(_ET).date() == session_date
        and (entry_allowed is None or entry_allowed(trade.entry_time))
    )
    if not current_session_trades:
        return _clear_prior_session_trade_metadata(snapshot)

    first_trade = current_session_trades[0]
    rejected = _pre_entry_range_rejection(
        bars,
        snapshot,
        entry_time=first_trade.entry_time,
    )
    if rejected is not None:
        return rejected
    state: StochRsi5mState = (
        "force_flat"
        if first_trade.exit_reason_code == "STOCH_RSI_5M_FORCE_FLAT"
        else "exited"
    )
    return snapshot.model_copy(
        update={
            "state": state,
            "reason_code": first_trade.exit_reason_code,
            "oversold_arm_time": first_trade.oversold_arm_time,
            "momentum_cross_time": first_trade.momentum_cross_time,
            "entry_signal_time": first_trade.entry_signal_time,
            "entry_time": first_trade.entry_time,
            "entry_price": first_trade.entry_price,
            "exit_signal_time": first_trade.exit_signal_time,
            "exit_time": first_trade.exit_time,
            "exit_price": first_trade.exit_price,
            "return_pct": first_trade.return_pct,
            "trades": (first_trade,),
        }
    )


__all__ = [
    "PRE_ENTRY_RANGE_CAP_PCT",
    "evaluate_stoch_rsi_5m_early_single",
]
