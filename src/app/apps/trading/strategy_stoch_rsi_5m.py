"""Causal five-minute Stoch RSI strategy evaluation.

The evaluator consumes finalized regular-session bars only. A %K observation
below the oversold threshold arms a setup. A later %K cross above %D arms
momentum confirmation; %K must then cross above the recovery threshold while
still rising and above %D before price confirmation can authorize entry.
Non-bullish confirmation candles require a later close above their high;
bullish confirmation candles use the next five-minute bar's open. The actual
entry open must be above the 5-period EMA calculated from finalized
five-minute closes. Open positions exit on the next five-minute open after a
close below that EMA, or after a finalized %K/%D cross down while %K is below
80. After an exit, the evaluator starts a fresh setup search, allowing
multiple sequential trades in the same session. This module is deterministic
research evidence; it has no broker or order side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from .indicator_signals import _stochastic_rsi_aligned
from .indicators.engine import exponential_moving_average
from .market_data_recovery import detect_session_gaps, latest_clean_bars
from .models import MarketBar
from .strategies.models import StochRsi5mConfig
from .strategy_timeframes import resample_final_bars
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


_REGULAR_OPEN = time(9, 30)
_SOURCE_INTERVAL_MINUTES = {"1m": 1, "5m": 5}
_EMA_PERIOD = 5
_STOCH_RSI_MIDLINE_EXIT_THRESHOLD = Decimal("80")

StochRsi5mState = Literal[
    "waiting_data",
    "data_gap",
    "waiting_oversold",
    "setup_armed",
    "entry_armed",
    "long_active",
    "exit_armed",
    "exited",
    "force_flat",
]


@dataclass(frozen=True)
class _EntrySearch:
    found: bool
    state: StochRsi5mState
    reason_code: str
    entry_index: int | None = None
    entry_signal_index: int | None = None
    entry_arm_index: int | None = None
    entry_momentum_cross_index: int | None = None


@dataclass(frozen=True)
class _ExitSearch:
    state: Literal["exited", "force_flat", "exit_armed", "long_active"]
    reason_code: str
    exit_signal_index: int | None = None
    exit_index: int | None = None
    force_flat_index: int | None = None


@dataclass(frozen=True)
class _CarriedPosition:
    """An open position whose exit must resume after an unresolved gap."""

    oversold_arm_time: datetime
    momentum_cross_time: datetime
    entry_signal_time: datetime
    entry_time: datetime
    entry_price: Decimal
    trades: tuple[StochRsi5mTrade, ...]


class StochRsi5mTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    oversold_arm_time: datetime
    momentum_cross_time: datetime
    entry_signal_time: datetime
    entry_time: datetime
    entry_price: Decimal
    exit_signal_time: datetime | None = None
    exit_time: datetime
    exit_price: Decimal
    exit_reason_code: str
    return_pct: Decimal


class StochRsi5mSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["stoch-rsi-5min-v16"] = "stoch-rsi-5min-v16"
    state: StochRsi5mState
    reason_code: str
    session_date: str | None = None
    as_of: datetime | None = None
    five_minute_bar_count: int = 0
    ema_50_5m: Decimal | None = None
    stochastic_rsi_k: Decimal | None = None
    stochastic_rsi_d: Decimal | None = None
    previous_stochastic_rsi_k: Decimal | None = None
    previous_stochastic_rsi_d: Decimal | None = None
    oversold_arm_time: datetime | None = None
    momentum_cross_time: datetime | None = None
    entry_signal_time: datetime | None = None
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    exit_signal_time: datetime | None = None
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    return_pct: Decimal | None = None
    trades: tuple[StochRsi5mTrade, ...] = ()
    data_gap_start: datetime | None = None
    data_gap_resume: datetime | None = None
    execution_authority: Literal[False] = False


def _regular_bars(bars: list[MarketBar] | tuple[MarketBar, ...]) -> list[MarketBar]:
    return sorted(
        (
            bar
            for bar in bars
            if bar.is_final
            and bar.session == "regular"
            and _REGULAR_OPEN
            <= bar.start_time.astimezone(_ET).time()
            < regular_close_time(bar.start_time.astimezone(_ET).date())
        ),
        key=lambda bar: bar.start_time,
    )


def _snapshot(
    *,
    state: StochRsi5mState,
    reason_code: str,
    session_date: str | None = None,
    as_of: datetime | None = None,
    five_minute_bar_count: int = 0,
    ema_50_5m: Decimal | None = None,
    stochastic_rsi_k: Decimal | None = None,
    stochastic_rsi_d: Decimal | None = None,
    previous_stochastic_rsi_k: Decimal | None = None,
    previous_stochastic_rsi_d: Decimal | None = None,
    oversold_arm_time: datetime | None = None,
    momentum_cross_time: datetime | None = None,
    entry_signal_time: datetime | None = None,
    entry_time: datetime | None = None,
    entry_price: Decimal | None = None,
    exit_signal_time: datetime | None = None,
    exit_time: datetime | None = None,
    exit_price: Decimal | None = None,
    return_pct: Decimal | None = None,
    trades: tuple[StochRsi5mTrade, ...] = (),
    data_gap_start: datetime | None = None,
    data_gap_resume: datetime | None = None,
) -> StochRsi5mSnapshot:
    return StochRsi5mSnapshot(
        state=state,
        reason_code=reason_code,
        session_date=session_date,
        as_of=as_of,
        five_minute_bar_count=five_minute_bar_count,
        ema_50_5m=ema_50_5m,
        stochastic_rsi_k=stochastic_rsi_k,
        stochastic_rsi_d=stochastic_rsi_d,
        previous_stochastic_rsi_k=previous_stochastic_rsi_k,
        previous_stochastic_rsi_d=previous_stochastic_rsi_d,
        oversold_arm_time=oversold_arm_time,
        momentum_cross_time=momentum_cross_time,
        entry_signal_time=entry_signal_time,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_signal_time=exit_signal_time,
        exit_time=exit_time,
        exit_price=exit_price,
        return_pct=return_pct,
        trades=trades,
        data_gap_start=data_gap_start,
        data_gap_resume=data_gap_resume,
    )


def _first_current_gap(
    bars: list[MarketBar],
    *,
    session_date,
    source_interval: str,
) -> tuple[datetime, datetime | None] | None:
    current = [
        bar
        for bar in bars
        if bar.start_time.astimezone(_ET).date() == session_date
    ]
    if not current:
        return None
    current.sort(key=lambda bar: bar.start_time)
    opening = current[0].start_time.astimezone(_ET)
    expected_open = datetime.combine(session_date, _REGULAR_OPEN, tzinfo=_ET)
    if opening > expected_open:
        return expected_open.astimezone(timezone.utc), current[0].start_time

    delta_minutes = _SOURCE_INTERVAL_MINUTES[source_interval]
    starts = {bar.start_time.astimezone(timezone.utc) for bar in current}
    cursor = expected_open.astimezone(timezone.utc)
    latest = current[-1].start_time.astimezone(timezone.utc)
    while cursor <= latest:
        if cursor not in starts:
            resume = next((value for value in sorted(starts) if value > cursor), None)
            return cursor, resume
        cursor += timedelta(minutes=delta_minutes)
    return None


def _bridgeable_span(gap: tuple[datetime, datetime | None], source_interval: str) -> bool:
    start, resume = gap
    return (
        source_interval == "5m"
        and resume is not None
        and resume - start <= timedelta(minutes=_SOURCE_INTERVAL_MINUTES["5m"])
    )


def _bridgeable_gap(gap, source_interval: str) -> bool:
    """A single missing 5m bucket; 1m sources and longer gaps still reset."""
    return source_interval == "5m" and gap.missing_bar_count == 1


def _entry_in_window(bar: MarketBar, config: StochRsi5mConfig) -> bool:
    local_time = bar.start_time.astimezone(_ET).time()
    return config.entry_start_et <= local_time <= config.last_entry_et


def _force_flat_reached(bar: MarketBar, config: StochRsi5mConfig) -> bool:
    return bar.end_time.astimezone(_ET).time() >= config.force_flat_et


def _ema_at(ema_values: list[Decimal], index: int) -> Decimal | None:
    ema_index = index - (_EMA_PERIOD - 1)
    if not 0 <= ema_index < len(ema_values):
        return None
    return ema_values[ema_index]


def _minimum_stochastic_rsi_bars(config: StochRsi5mConfig) -> int:
    """Return the bars needed for one configured %K/%D observation."""

    return (
        config.rsi_period
        + config.stochastic_period
        + config.k_smoothing_period
        + config.d_smoothing_period
        - 2
    )


def _signal_requires_breakout(bar: MarketBar) -> bool:
    """Return whether a non-bullish signal needs a confirmed high breakout."""

    return bar.close <= bar.open


def evaluate_stoch_rsi_5m(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    config: StochRsi5mConfig | None = None,
) -> StochRsi5mSnapshot:
    """Evaluate Stoch RSI entries and 5m EMA exits on finalized bars.

    An unresolved historical gap resets indicator/setup state rather than
    invalidating the symbol forever. Evaluation resumes only after a fresh,
    contiguous post-gap segment must contain enough bars to calculate the
    configured Stoch RSI. The 5-period EMA is allowed to initialize from that
    segment without imposing an additional post-gap wait.
    If the pre-gap state had an open position (or armed exit), the position is
    carried across the gap. Exit signals are ignored inside the missing
    interval and resume on the first usable post-gap bar; no exit is inferred
    from data that was not observed.
    """

    active = config or StochRsi5mConfig()
    regular = _regular_bars(bars)
    if not regular:
        return _snapshot(
            state="waiting_data",
            reason_code="STOCH_RSI_5M_DATA_UNAVAILABLE",
        )

    session_date = regular[-1].start_time.astimezone(_ET).date()
    source_intervals = {bar.interval for bar in regular}
    if len(source_intervals) != 1:
        raise ValueError("stoch-rsi-5min requires one source interval")
    source_interval = next(iter(source_intervals))
    if source_interval not in _SOURCE_INTERVAL_MINUTES:
        raise ValueError("stoch-rsi-5min accepts only 1m or 5m source bars")

    gap = _first_current_gap(
        regular,
        session_date=session_date,
        source_interval=source_interval,
    )
    prior_trades: tuple[StochRsi5mTrade, ...] = ()
    carried_position: _CarriedPosition | None = None
    if gap is not None:
        detected = detect_session_gaps(
            regular,
            session_date=session_date,
            interval=source_interval,
            as_of=regular[-1].end_time,
        )
        # One missing 5m bucket (a short halt, a quiet bucket) is stepped over:
        # the indicators continue on the bars that exist. Longer gaps reset.
        gaps = [item for item in detected if not _bridgeable_gap(item, source_interval)]
        if not gaps and (detected or _bridgeable_span(gap, source_interval)):
            gap = None
        latest_gap = gaps[-1] if gaps else None
        if latest_gap is not None:
            gap = (latest_gap.start, latest_gap.end)
            prefix = [bar for bar in regular if bar.end_time <= latest_gap.start]
            if prefix:
                pre_gap = evaluate_stoch_rsi_5m(prefix, active)
                if pre_gap.state in {"long_active", "exit_armed"}:
                    position_values = (
                        pre_gap.oversold_arm_time,
                        pre_gap.momentum_cross_time,
                        pre_gap.entry_signal_time,
                        pre_gap.entry_time,
                        pre_gap.entry_price,
                    )
                    arm_time, cross_time, signal_time, entry_time, entry_price = position_values
                    if (
                        arm_time is not None
                        and cross_time is not None
                        and signal_time is not None
                        and entry_time is not None
                        and entry_price is not None
                    ):
                        carried_position = _CarriedPosition(
                            oversold_arm_time=arm_time,
                            momentum_cross_time=cross_time,
                            entry_signal_time=signal_time,
                            entry_time=entry_time,
                            entry_price=entry_price,
                            trades=pre_gap.trades,
                        )
                    else:
                        return _snapshot(
                            state="data_gap",
                            reason_code="STOCH_RSI_5M_OPEN_POSITION_METADATA_UNAVAILABLE",
                            session_date=session_date.isoformat(),
                            as_of=regular[-1].end_time,
                            trades=pre_gap.trades,
                            data_gap_start=gap[0],
                            data_gap_resume=gap[1],
                        )
                else:
                    prior_trades = pre_gap.trades
            if carried_position is None:
                regular = latest_clean_bars(
                    regular,
                    session_date=session_date,
                    interval=source_interval,
                    as_of=regular[-1].end_time,
                    bridge_single_missing=source_interval == "5m",
                )
            if not regular:
                return _snapshot(
                    state="data_gap",
                    reason_code="STOCH_RSI_5M_DATA_GAP",
                    session_date=session_date.isoformat(),
                    as_of=gap[1],
                    trades=prior_trades,
                    data_gap_start=gap[0],
                    data_gap_resume=gap[1],
                )

    sampled = regular if source_interval == "5m" else resample_final_bars(regular, "5m")
    if not sampled:
        return _snapshot(
            state="waiting_data",
            reason_code="STOCH_RSI_5M_WAITING_FOR_COMPLETED_BAR",
            session_date=session_date.isoformat(),
            as_of=regular[-1].end_time,
            trades=prior_trades,
            data_gap_start=gap[0] if gap is not None else None,
            data_gap_resume=gap[1] if gap is not None else None,
        )

    sampled = sorted(sampled, key=lambda bar: bar.start_time)
    if (
        gap is not None
        and carried_position is None
        and len(sampled) < _minimum_stochastic_rsi_bars(active)
    ):
        return _snapshot(
            state="data_gap",
            reason_code="STOCH_RSI_5M_POST_GAP_STOCH_RSI_WARMUP",
            session_date=session_date.isoformat(),
            as_of=sampled[-1].end_time,
            five_minute_bar_count=len(sampled),
            trades=prior_trades,
            data_gap_start=gap[0],
            data_gap_resume=gap[1],
        )

    ema_values = exponential_moving_average(
        (bar.close for bar in sampled),
        _EMA_PERIOD,
    )
    ema_start_index = _EMA_PERIOD - 1
    if not ema_values:
        return _snapshot(
            state="waiting_data",
            reason_code="STOCH_RSI_5M_5_5M_EMA_WARMUP",
            session_date=session_date.isoformat(),
            as_of=sampled[-1].end_time,
            five_minute_bar_count=len(sampled),
            ema_50_5m=None,
            trades=prior_trades,
            data_gap_start=gap[0] if gap is not None else None,
            data_gap_resume=gap[1] if gap is not None else None,
        )
    closes = [bar.close for bar in sampled]
    k_values, d_values = _stochastic_rsi_aligned(
        closes,
        rsi_period=active.rsi_period,
        stochastic_period=active.stochastic_period,
        smoothing_period=active.k_smoothing_period,
        signal_period=active.d_smoothing_period,
    )
    last = sampled[-1]
    last_k = k_values[-1] if k_values else None
    last_d = d_values[-1] if d_values else None
    previous_k = k_values[-2] if len(k_values) > 1 else None
    previous_d = d_values[-2] if len(d_values) > 1 else None
    common: dict[str, Any] = {
        "session_date": session_date.isoformat(),
        "as_of": last.end_time,
        "five_minute_bar_count": len(sampled),
        "ema_50_5m": ema_values[-1],
        "stochastic_rsi_k": last_k,
        "stochastic_rsi_d": last_d,
        "previous_stochastic_rsi_k": previous_k,
        "previous_stochastic_rsi_d": previous_d,
        "data_gap_start": gap[0] if gap is not None else None,
        "data_gap_resume": gap[1] if gap is not None else None,
    }
    if last_k is None or last_d is None:
        return _snapshot(
            state="waiting_data",
            reason_code="STOCH_RSI_5M_WARMUP",
            trades=prior_trades,
            **common,
        )

    current_session_indexes = [
        index
        for index, bar in enumerate(sampled)
        if bar.start_time.astimezone(_ET).date() == session_date
    ]
    if not current_session_indexes:
        return _snapshot(
            state="waiting_data",
            reason_code="STOCH_RSI_5M_CURRENT_SESSION_UNAVAILABLE",
            trades=prior_trades,
            **common,
        )

    def crossed_up(index: int) -> bool:
        if index <= 0 or k_values[index] is None or d_values[index] is None:
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
            and prior_k <= prior_d
            and current_k > current_d
        )

    def recovery_confirmed(index: int) -> bool:
        if index <= 0 or k_values[index] is None or d_values[index] is None:
            return False
        prior_k = k_values[index - 1]
        current_k = k_values[index]
        current_d = d_values[index]
        return (
            prior_k is not None
            and current_k is not None
            and current_d is not None
            and current_k >= active.recovery_threshold
            and current_k > prior_k
            and current_k > current_d
        )

    def stochastic_crossed_down(index: int) -> bool:
        if index <= 0 or k_values[index] is None or d_values[index] is None:
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

    def crossed_down(index: int) -> bool:
        return (
            stochastic_crossed_down(index)
            and (k_value := k_values[index]) is not None
            and k_value > active.overbought_threshold
        )

    def crossed_down_below_midline(index: int) -> bool:
        return (
            stochastic_crossed_down(index)
            and (k_value := k_values[index]) is not None
            and k_value < _STOCH_RSI_MIDLINE_EXIT_THRESHOLD
        )

    def _entry_search(start_index: int) -> _EntrySearch:
        active_arm_index: int | None = None
        active_momentum_cross_index: int | None = None
        latest_arm_index: int | None = None
        latest_momentum_cross_index: int | None = None
        pending_entry_index: int | None = None
        pending_confirmation_index: int | None = None
        rejected_price_confirmation = False

        for index in current_session_indexes:
            if index < start_index:
                continue
            current_k = k_values[index]
            current_d = d_values[index]
            if current_k is None or current_d is None:
                continue

            if current_k < active.oversold_threshold and active_momentum_cross_index is None:
                if (
                    active_arm_index is None
                    or (arm_k := k_values[active_arm_index]) is None
                    or current_k <= arm_k
                ):
                    active_arm_index = index
                    latest_arm_index = index

            if active_arm_index is None:
                continue

            if active_momentum_cross_index is None:
                if not crossed_up(index):
                    continue
                active_momentum_cross_index = index
                latest_momentum_cross_index = index

            if index > active_momentum_cross_index and current_k <= current_d:
                active_momentum_cross_index = None
                active_arm_index = index if current_k < active.oversold_threshold else None
                if active_arm_index is not None:
                    latest_arm_index = active_arm_index
                continue

            if not recovery_confirmed(index):
                continue

            signal_bar = sampled[index]
            if not _entry_in_window(signal_bar, active):
                continue

            if not _signal_requires_breakout(signal_bar):
                next_index = index + 1
                if next_index >= len(sampled):
                    pending_entry_index = index
                    continue
                next_bar = sampled[next_index]
                if (
                    next_bar.start_time.astimezone(_ET).date() == session_date
                    and _entry_in_window(next_bar, active)
                ):
                    signal_ema = _ema_at(ema_values, index)
                    if signal_ema is None or next_bar.open <= signal_ema:
                        rejected_price_confirmation = True
                        active_arm_index = None
                        active_momentum_cross_index = None
                        continue
                    return _EntrySearch(
                        found=True,
                        state="long_active",
                        reason_code="STOCH_RSI_5M_ENTRY_CONFIRMED",
                        entry_index=next_index,
                        entry_signal_index=index,
                        entry_arm_index=active_arm_index,
                        entry_momentum_cross_index=active_momentum_cross_index,
                    )
                continue

            breakout_index: int | None = None
            breakout_invalidated = False
            for candidate_index in range(index + 1, len(sampled)):
                candidate = sampled[candidate_index]
                if candidate.start_time.astimezone(_ET).date() != session_date:
                    break
                candidate_k = k_values[candidate_index]
                candidate_d = d_values[candidate_index]
                if (
                    candidate_k is None
                    or candidate_d is None
                    or candidate_k < active.recovery_threshold
                    or candidate_k <= candidate_d
                ):
                    rejected_price_confirmation = True
                    breakout_invalidated = True
                    break
                if candidate.close <= signal_bar.high:
                    continue
                breakout_index = candidate_index
                break
            if breakout_invalidated:
                active_arm_index = None
                active_momentum_cross_index = None
                break
            if breakout_index is None:
                pending_confirmation_index = index
                break

            next_index = breakout_index + 1
            if next_index >= len(sampled):
                pending_confirmation_index = index
                break
            next_bar = sampled[next_index]
            if (
                next_bar.start_time.astimezone(_ET).date() == session_date
                and _entry_in_window(next_bar, active)
            ):
                signal_ema = _ema_at(ema_values, breakout_index)
                if signal_ema is None or next_bar.open <= signal_ema:
                    rejected_price_confirmation = True
                    active_arm_index = None
                    active_momentum_cross_index = None
                    continue
                return _EntrySearch(
                    found=True,
                    state="long_active",
                    reason_code="STOCH_RSI_5M_ENTRY_CONFIRMED",
                    entry_index=next_index,
                    entry_signal_index=index,
                    entry_arm_index=active_arm_index,
                    entry_momentum_cross_index=active_momentum_cross_index,
                )

        if pending_entry_index is not None:
            return _EntrySearch(
                found=False,
                state="entry_armed",
                reason_code="STOCH_RSI_5M_RECOVERY_20_CONFIRMED",
                entry_signal_index=pending_entry_index,
                entry_arm_index=active_arm_index,
                entry_momentum_cross_index=active_momentum_cross_index,
            )
        if pending_confirmation_index is not None:
            return _EntrySearch(
                found=False,
                state="entry_armed",
                reason_code="STOCH_RSI_5M_WAITING_PRICE_CONFIRMATION",
                entry_signal_index=pending_confirmation_index,
                entry_arm_index=active_arm_index,
                entry_momentum_cross_index=active_momentum_cross_index,
            )
        if rejected_price_confirmation:
            return _EntrySearch(
                found=False,
                state="waiting_oversold",
                reason_code="STOCH_RSI_5M_PRICE_CONFIRMATION_REJECTED",
                entry_arm_index=latest_arm_index,
                entry_momentum_cross_index=latest_momentum_cross_index,
            )
        if active_momentum_cross_index is not None:
            return _EntrySearch(
                found=False,
                state="setup_armed",
                reason_code="STOCH_RSI_5M_WAITING_RECOVERY_20",
                entry_arm_index=active_arm_index,
                entry_momentum_cross_index=active_momentum_cross_index,
            )
        if active_arm_index is not None:
            return _EntrySearch(
                found=False,
                state="setup_armed",
                reason_code="STOCH_RSI_5M_WAITING_MOMENTUM_CROSS",
                entry_arm_index=active_arm_index,
            )
        return _EntrySearch(
            found=False,
            state="waiting_oversold",
            reason_code="STOCH_RSI_5M_WAITING_OVERSOLD_ARM",
        )

    def _next_contiguous_index(index: int) -> int | None:
        next_index = index + 1
        if next_index >= len(sampled):
            return None
        if sampled[next_index].start_time != sampled[index].end_time:
            return None
        return next_index

    def _exit_search(
        entry_index: int,
        *,
        signal_start_index: int | None = None,
    ) -> _ExitSearch:
        search_start_index = max(
            entry_index,
            signal_start_index if signal_start_index is not None else entry_index,
        )
        for index in range(search_start_index, len(sampled)):
            ema_index = index - ema_start_index
            ema_value = ema_values[ema_index] if 0 <= ema_index < len(ema_values) else None
            if ema_value is not None and sampled[index].close < ema_value:
                next_index = _next_contiguous_index(index)
                if next_index is not None:
                    next_bar = sampled[next_index]
                    if (
                        next_bar.start_time.astimezone(_ET).date() == session_date
                        and next_bar.start_time.astimezone(_ET).time() <= active.force_flat_et
                    ):
                        return _ExitSearch(
                            state="exited",
                            reason_code="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA",
                            exit_signal_index=index,
                            exit_index=next_index,
                        )

            if crossed_down_below_midline(index):
                next_index = _next_contiguous_index(index)
                if next_index is not None:
                    next_bar = sampled[next_index]
                    if (
                        next_bar.start_time.astimezone(_ET).date() == session_date
                        and next_bar.start_time.astimezone(_ET).time() <= active.force_flat_et
                    ):
                        return _ExitSearch(
                            state="exited",
                            reason_code="STOCH_RSI_5M_CROSS_DOWN_BELOW_80",
                            exit_signal_index=index,
                            exit_index=next_index,
                        )
                if _force_flat_reached(sampled[index], active):
                    return _ExitSearch(
                        state="force_flat",
                        reason_code="STOCH_RSI_5M_FORCE_FLAT",
                        force_flat_index=index,
                    )

            if crossed_down(index):
                next_index = _next_contiguous_index(index)
                if next_index is not None:
                    next_bar = sampled[next_index]
                    if (
                        next_bar.start_time.astimezone(_ET).date() == session_date
                        and next_bar.start_time.astimezone(_ET).time() <= active.force_flat_et
                    ):
                        return _ExitSearch(
                            state="exited",
                            reason_code="STOCH_RSI_5M_OVERBOUGHT_CROSS_DOWN",
                            exit_signal_index=index,
                            exit_index=next_index,
                        )
                if _force_flat_reached(sampled[index], active):
                    return _ExitSearch(
                        state="force_flat",
                        reason_code="STOCH_RSI_5M_FORCE_FLAT",
                        force_flat_index=index,
                    )

        force_flat_index = next(
            (
                index
                for index in range(search_start_index, len(sampled))
                if _force_flat_reached(sampled[index], active)
            ),
            None,
        )
        if force_flat_index is not None:
            return _ExitSearch(
                state="force_flat",
                reason_code="STOCH_RSI_5M_FORCE_FLAT",
                force_flat_index=force_flat_index,
            )

        for index in range(search_start_index, len(sampled)):
            if crossed_down_below_midline(index):
                return _ExitSearch(
                    state="exit_armed",
                    reason_code="STOCH_RSI_5M_CROSS_DOWN_BELOW_80",
                )
            if crossed_down(index):
                return _ExitSearch(
                    state="exit_armed",
                    reason_code="STOCH_RSI_5M_OVERBOUGHT_CROSS_DOWN",
                )
        return _ExitSearch(
            state="long_active",
            reason_code="STOCH_RSI_5M_LONG_ACTIVE",
        )

    def _trade_from(entry: _EntrySearch, exit: _ExitSearch) -> StochRsi5mTrade:
        assert entry.entry_index is not None
        assert entry.entry_signal_index is not None
        assert entry.entry_arm_index is not None
        assert entry.entry_momentum_cross_index is not None
        entry_bar = sampled[entry.entry_index]
        if exit.state == "exited":
            assert exit.exit_signal_index is not None
            assert exit.exit_index is not None
            exit_signal_time = sampled[exit.exit_signal_index].end_time
            exit_bar = sampled[exit.exit_index]
            exit_time = exit_bar.start_time
            exit_price = exit_bar.open
        else:
            assert exit.force_flat_index is not None
            exit_signal_time = None
            exit_bar = sampled[exit.force_flat_index]
            exit_time = exit_bar.end_time
            exit_price = exit_bar.close
        return StochRsi5mTrade(
            oversold_arm_time=sampled[entry.entry_arm_index].end_time,
            momentum_cross_time=sampled[entry.entry_momentum_cross_index].end_time,
            entry_signal_time=sampled[entry.entry_signal_index].end_time,
            entry_time=entry_bar.start_time,
            entry_price=entry_bar.open,
            exit_signal_time=exit_signal_time,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason_code=exit.reason_code,
            return_pct=(exit_price - entry_bar.open) / entry_bar.open * Decimal("100"),
        )

    def _trade_from_carried(exit: _ExitSearch) -> StochRsi5mTrade:
        assert carried_position is not None
        if exit.state == "exited":
            assert exit.exit_signal_index is not None
            assert exit.exit_index is not None
            exit_signal_time = sampled[exit.exit_signal_index].end_time
            exit_bar = sampled[exit.exit_index]
            exit_time = exit_bar.start_time
            exit_price = exit_bar.open
        else:
            assert exit.force_flat_index is not None
            exit_signal_time = None
            exit_bar = sampled[exit.force_flat_index]
            exit_time = exit_bar.end_time
            exit_price = exit_bar.close
        return StochRsi5mTrade(
            oversold_arm_time=carried_position.oversold_arm_time,
            momentum_cross_time=carried_position.momentum_cross_time,
            entry_signal_time=carried_position.entry_signal_time,
            entry_time=carried_position.entry_time,
            entry_price=carried_position.entry_price,
            exit_signal_time=exit_signal_time,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason_code=exit.reason_code,
            return_pct=(exit_price - carried_position.entry_price)
            / carried_position.entry_price
            * Decimal("100"),
        )

    def _snapshot_for_trade(
        trade: StochRsi5mTrade,
        *,
        state: Literal["exited", "force_flat"],
        trades: tuple[StochRsi5mTrade, ...],
    ) -> StochRsi5mSnapshot:
        return _snapshot(
            state=state,
            reason_code=trade.exit_reason_code,
            oversold_arm_time=trade.oversold_arm_time,
            momentum_cross_time=trade.momentum_cross_time,
            entry_signal_time=trade.entry_signal_time,
            entry_time=trade.entry_time,
            entry_price=trade.entry_price,
            exit_signal_time=trade.exit_signal_time,
            exit_time=trade.exit_time,
            exit_price=trade.exit_price,
            return_pct=trade.return_pct,
            trades=trades,
            **common,
        )

    def _snapshot_for_entry_wait(
        search: _EntrySearch,
        trades: tuple[StochRsi5mTrade, ...],
    ) -> StochRsi5mSnapshot:
        return _snapshot(
            state=search.state,
            reason_code=search.reason_code,
            oversold_arm_time=(
                sampled[search.entry_arm_index].end_time
                if search.entry_arm_index is not None
                else None
            ),
            momentum_cross_time=(
                sampled[search.entry_momentum_cross_index].end_time
                if search.entry_momentum_cross_index is not None
                else None
            ),
            entry_signal_time=(
                sampled[search.entry_signal_index].end_time
                if search.entry_signal_index is not None
                else None
            ),
            trades=trades,
            **common,
        )

    if carried_position is not None:
        entry_index = next(
            (
                index
                for index, bar in enumerate(sampled)
                if bar.start_time == carried_position.entry_time
            ),
            None,
        )
        if entry_index is None:
            return _snapshot(
                state="data_gap",
                reason_code="STOCH_RSI_5M_OPEN_POSITION_METADATA_UNAVAILABLE",
                trades=carried_position.trades,
                **common,
            )
        assert gap is not None
        post_gap_start_index = next(
            (
                index
                for index, bar in enumerate(sampled)
                if bar.start_time >= cast(datetime, gap[1])
            ),
            len(sampled),
        )
        exit = _exit_search(
            entry_index,
            signal_start_index=post_gap_start_index,
        )
        if exit.state in {"exited", "force_flat"}:
            trade = _trade_from_carried(exit)
            return _snapshot_for_trade(
                trade,
                state=exit.state,
                trades=(*carried_position.trades, trade),
            )
        return _snapshot(
            state=exit.state,
            reason_code=exit.reason_code,
            oversold_arm_time=carried_position.oversold_arm_time,
            momentum_cross_time=carried_position.momentum_cross_time,
            entry_signal_time=carried_position.entry_signal_time,
            entry_time=carried_position.entry_time,
            entry_price=carried_position.entry_price,
            trades=carried_position.trades,
            **common,
        )

    completed_trades: list[StochRsi5mTrade] = list(prior_trades)
    search_start_index = current_session_indexes[0]
    while True:
        entry = _entry_search(search_start_index)
        completed = tuple(completed_trades)
        if not entry.found:
            if completed_trades and entry.state == "waiting_oversold":
                return _snapshot_for_trade(
                    completed_trades[-1],
                    state="exited",
                    trades=completed,
                )
            return _snapshot_for_entry_wait(entry, completed)

        assert entry.entry_index is not None
        assert entry.entry_signal_index is not None
        assert entry.entry_arm_index is not None
        assert entry.entry_momentum_cross_index is not None
        exit = _exit_search(entry.entry_index)
        entry_bar = sampled[entry.entry_index]
        entry_signal_bar = sampled[entry.entry_signal_index]
        entry_evidence: dict[str, Any] = {
            "oversold_arm_time": sampled[entry.entry_arm_index].end_time,
            "momentum_cross_time": sampled[entry.entry_momentum_cross_index].end_time,
        }
        if exit.state in {"exited", "force_flat"}:
            trade = _trade_from(entry, exit)
            completed_trades.append(trade)
            if exit.state == "force_flat":
                return _snapshot_for_trade(
                    trade,
                    state="force_flat",
                    trades=tuple(completed_trades),
                )
            assert exit.exit_index is not None
            search_start_index = exit.exit_index + 1
            if not any(index >= search_start_index for index in current_session_indexes):
                return _snapshot_for_trade(
                    trade,
                    state="exited",
                    trades=tuple(completed_trades),
                )
            continue

        if exit.state == "exit_armed":
            return _snapshot(
                state="exit_armed",
                reason_code=exit.reason_code,
                entry_signal_time=entry_signal_bar.end_time,
                entry_time=entry_bar.start_time,
                entry_price=entry_bar.open,
                trades=tuple(completed_trades),
                **entry_evidence,
                **common,
            )
        return _snapshot(
            state="long_active",
            reason_code="STOCH_RSI_5M_LONG_ACTIVE",
            entry_signal_time=entry_signal_bar.end_time,
            entry_time=entry_bar.start_time,
            entry_price=entry_bar.open,
            trades=tuple(completed_trades),
            **entry_evidence,
            **common,
        )


__all__ = [
    "StochRsi5mSnapshot",
    "StochRsi5mState",
    "StochRsi5mTrade",
    "evaluate_stoch_rsi_5m",
]
