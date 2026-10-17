"""Canonical Stoch-RSI 5m exit replay for research arms with delayed entry.

Some research controls keep the canonical setup but wait for additional causal
confirmation before entering.  Reusing the parent trade's exit after moving its
entry would mix two different paths.  This helper preserves the production
five-minute exit semantics while starting the exit search at the research
arm's actual entry.
"""

from __future__ import annotations

from decimal import Decimal

from .indicator_signals import _stochastic_rsi_aligned
from .indicators.engine import exponential_moving_average
from .models import MarketBar
from .strategies.models import StochRsi5mConfig
from .strategy_stoch_rsi_5m import StochRsi5mTrade
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_EMA_PERIOD = 5
_STOCH_RSI_MIDLINE_EXIT_THRESHOLD = Decimal("80")


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


def _ema_at(values: list[Decimal], index: int) -> Decimal | None:
    ema_index = index - (_EMA_PERIOD - 1)
    if not 0 <= ema_index < len(values):
        return None
    return values[ema_index]


def _next_contiguous_index(sampled: list[MarketBar], index: int) -> int | None:
    next_index = index + 1
    if next_index >= len(sampled):
        return None
    if sampled[next_index].start_time != sampled[index].end_time:
        return None
    return next_index


def _modified_exit(
    trade: StochRsi5mTrade,
    *,
    exit_signal_time,
    exit_time,
    exit_price: Decimal,
    exit_reason_code: str,
) -> StochRsi5mTrade:
    return trade.model_copy(
        update={
            "exit_signal_time": exit_signal_time,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_reason_code": exit_reason_code,
            "return_pct": (exit_price - trade.entry_price)
            / trade.entry_price
            * Decimal("100"),
        }
    )


def recompute_stoch_rsi_5m_exit_from_entry(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    trade: StochRsi5mTrade,
    config: StochRsi5mConfig | None = None,
) -> StochRsi5mTrade:
    """Re-run canonical v15 exit semantics from ``trade.entry_time``.

    The entry/setup evidence is left untouched.  Only exit evidence and return
    are recomputed.  If the requested entry is not present on the supplied 5m
    tape, the input trade is returned unchanged so callers can surface their
    own metadata/data-quality contract.
    """

    active = config or StochRsi5mConfig()
    sampled = _regular_5m_bars(bars)
    if not sampled:
        return trade

    entry_index = next(
        (index for index, bar in enumerate(sampled) if bar.start_time == trade.entry_time),
        None,
    )
    if entry_index is None:
        return trade

    session_date = trade.entry_time.astimezone(_ET).date()
    closes = [bar.close for bar in sampled]
    ema_values = exponential_moving_average(closes, _EMA_PERIOD)
    k_values, d_values = _stochastic_rsi_aligned(
        closes,
        rsi_period=active.rsi_period,
        stochastic_period=active.stochastic_period,
        smoothing_period=active.k_smoothing_period,
        signal_period=active.d_smoothing_period,
    )

    def stochastic_crossed_down(index: int) -> bool:
        if index <= 0 or index >= len(k_values) or index >= len(d_values):
            return False
        prior_k = k_values[index - 1]
        prior_d = d_values[index - 1]
        current_k = k_values[index]
        current_d = d_values[index]
        return (
            prior_k is not None
            and prior_d is not None
            and current_k is not None
            and current_d is not None
            and prior_k >= prior_d
            and current_k < current_d
        )

    def exit_on_next_open(index: int, reason: str) -> StochRsi5mTrade | None:
        next_index = _next_contiguous_index(sampled, index)
        if next_index is None:
            return None
        next_bar = sampled[next_index]
        if (
            next_bar.start_time.astimezone(_ET).date() != session_date
            or next_bar.start_time.astimezone(_ET).time() > active.force_flat_et
        ):
            return None
        return _modified_exit(
            trade,
            exit_signal_time=sampled[index].end_time,
            exit_time=next_bar.start_time,
            exit_price=next_bar.open,
            exit_reason_code=reason,
        )

    for index in range(entry_index, len(sampled)):
        bar = sampled[index]
        if bar.start_time.astimezone(_ET).date() != session_date:
            break

        ema_value = _ema_at(ema_values, index)
        if ema_value is not None and bar.close < ema_value:
            exited = exit_on_next_open(index, "STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA")
            if exited is not None:
                return exited

        current_k = k_values[index] if index < len(k_values) else None
        if (
            stochastic_crossed_down(index)
            and current_k is not None
            and current_k < _STOCH_RSI_MIDLINE_EXIT_THRESHOLD
        ):
            exited = exit_on_next_open(index, "STOCH_RSI_5M_CROSS_DOWN_BELOW_80")
            if exited is not None:
                return exited

        if (
            stochastic_crossed_down(index)
            and current_k is not None
            and current_k > active.overbought_threshold
        ):
            exited = exit_on_next_open(index, "STOCH_RSI_5M_OVERBOUGHT_CROSS_DOWN")
            if exited is not None:
                return exited

        if bar.end_time.astimezone(_ET).time() >= active.force_flat_et:
            return _modified_exit(
                trade,
                exit_signal_time=None,
                exit_time=bar.end_time,
                exit_price=bar.close,
                exit_reason_code="STOCH_RSI_5M_FORCE_FLAT",
            )

    return trade


__all__ = ["recompute_stoch_rsi_5m_exit_from_entry"]
