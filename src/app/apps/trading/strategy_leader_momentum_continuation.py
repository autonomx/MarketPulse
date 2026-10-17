from __future__ import annotations

"""Deterministic SHADOW-only leader momentum continuation strategy.

This arm is intentionally independent of Stoch-RSI and V2 failed-selloff
geometry. It looks for stocks that have already proven persistent intraday
leadership and then enters either:

1. a controlled pullback breakout, or
2. a momentum-compression breakout when the leader refuses to sell off.

The evaluator consumes finalized causal bars only, never places orders, and
never carries execution authority. Version 1.2 broadens continuation geometry
after leadership has been proven: deeper pullbacks, local compression breaks,
and a longer leadership latch are accepted while the current VWAP/EMA trend,
breakout, volume, and bounded-risk checks remain causal and deterministic.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .indicators.engine import average_true_range, exponential_moving_average
from .models import MarketBar
from .strategies.gap_pullback import session_vwap
from .strategy_timeframes import resample_final_bars
from app.apps.trading.us_equity_calendar import EASTERN as _ET


POLICY_VERSION: Final = "leader-momentum-continuation-v1.2"
MIN_PRICE = Decimal("0.75")
MAX_PRICE = Decimal("20")
MIN_LEADER_SCORE = Decimal("70")
MIN_SESSION_RETURN_PCT = Decimal("5")
MIN_IMPULSE_PCT = Decimal("4")
MIN_RUNAWAY_IMPULSE_PCT = Decimal("5")
MIN_BREAKOUT_VOLUME_RATIO = Decimal("1.25")
MIN_COMPRESSION_VOLUME_RATIO = Decimal("1")
MAX_PULLBACK_RETRACE = Decimal("0.80")
MIN_PULLBACK_RETRACE = Decimal("0.10")
MAX_PULLBACK_VOLUME_RATIO = Decimal("0.70")
MAX_ENTRY_RISK_PCT = Decimal("12")
MAX_EMA9_EXTENSION_PCT = Decimal("12")
MAX_ATR_EXTENSION = Decimal("3")
MIN_BREAKOUT_CLOSE_LOCATION = Decimal("0.40")
MAX_COMPRESSION_WIDTH_RATIO = Decimal("0.75")
REQUIRE_PULLBACK_NO_NEW_HIGH = True
REQUIRE_COMPRESSION_ABOVE_EMA20 = False
REQUIRE_COMPRESSION_HOD_BREAK = False
PARTIAL_TRIGGER_R = Decimal("3")
PARTIAL_FRACTION = Decimal("0.20")
STRUCTURAL_BUFFER_ATR = Decimal("1")
INITIAL_STOP_BUFFER_ATR = Decimal("0.25")
BELOW_TREND_EXIT_BARS = 2
DISTRIBUTION_RANGE_ATR = Decimal("1.5")
DISTRIBUTION_VOLUME_RATIO = Decimal("1.5")
ENABLE_STRUCTURAL_EXIT = True
ENABLE_TREND_EXIT = True
ENABLE_DISTRIBUTION_EXIT = True
LEADER_LATCH_TTL = timedelta(minutes=120)
REENTRY_COOLDOWN = timedelta(minutes=15)
MAX_TRADES = 2

LeaderMode = Literal["controlled_pullback", "momentum_compression"]
LeaderState = Literal[
    "waiting_session",
    "data_gap",
    "waiting_leader",
    "waiting_setup",
    "breakout_armed",
    "long_active",
    "trend_runner",
    "completed",
    "force_flat",
]


class LeaderMomentumContext(BaseModel):
    """Optional causal market context used to strengthen leader qualification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tod_rvol: Decimal | None = Field(default=None, ge=0)
    relative_strength_pct: Decimal | None = None
    spread_bps: Decimal | None = Field(default=None, ge=0)
    dollar_volume: Decimal | None = Field(default=None, ge=0)
    volume_acceleration: Decimal | None = Field(default=None, ge=0)
    hod_frequency_15m: int | None = Field(default=None, ge=0)


class LeaderMomentumTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: LeaderMode
    signal_time: datetime
    entry_time: datetime
    entry_price: Decimal
    initial_stop_price: Decimal
    exit_time: datetime
    exit_price: Decimal
    exit_reason_code: str
    partial_exit_time: datetime | None = None
    partial_exit_price: Decimal | None = None
    partial_fraction: Decimal = PARTIAL_FRACTION
    return_pct: Decimal
    mfe_pct: Decimal
    mae_pct: Decimal


class LeaderMomentumSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["leader-momentum-continuation-v1.2"] = POLICY_VERSION
    state: LeaderState
    reason_code: str
    session_date: str | None = None
    as_of: datetime | None = None
    leader_score: Decimal | None = None
    leader_confirmed_at: datetime | None = None
    leader_confirmed_until: datetime | None = None
    session_return_pct: Decimal | None = None
    session_vwap: Decimal | None = None
    ema9_1m: Decimal | None = None
    ema20_1m: Decimal | None = None
    ema9_3m: Decimal | None = None
    atr14_1m: Decimal | None = None
    atr14_3m: Decimal | None = None
    setup_mode: LeaderMode | None = None
    signal_time: datetime | None = None
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    initial_stop_price: Decimal | None = None
    risk_pct: Decimal | None = None
    trades: tuple[LeaderMomentumTrade, ...] = ()
    data_gap_start: datetime | None = None
    data_gap_resume: datetime | None = None
    recovered_gap_count: int = 0
    execution_authority: Literal[False] = False


def _regular_final_bars(
    bars: list[MarketBar] | tuple[MarketBar, ...],
) -> list[MarketBar]:
    return sorted(
        (bar for bar in bars if bar.is_final and bar.session == "regular"),
        key=lambda bar: bar.start_time,
    )


def _first_internal_gap(bars: list[MarketBar]) -> tuple[datetime, datetime] | None:
    """Return the first discontinuity in a raw 1m tape."""

    if not bars:
        return None
    if bars[0].interval != "1m":
        return None
    expected = timedelta(minutes=1)
    for previous, current in zip(bars, bars[1:]):
        if current.start_time - previous.start_time != expected:
            return previous.end_time, current.start_time
    return None


def _is_contiguous(sampled: list[MarketBar], start: int, end: int) -> bool:
    """Require a concrete setup window to contain consecutive 3m bars only."""

    if start < 0 or end >= len(sampled) or start >= end:
        return False
    return all(
        right.start_time - left.start_time == timedelta(minutes=3)
        for left, right in zip(sampled[start:end], sampled[start + 1 : end + 1])
    )


def _ema(values: list[Decimal], period: int) -> list[Decimal | None]:
    if not values:
        return []
    raw = exponential_moving_average(values, period)
    padding = max(0, len(values) - len(raw))
    return [None] * padding + list(raw)


def _atr(bars: list[MarketBar], period: int = 14) -> list[Decimal | None]:
    if not bars:
        return []
    raw = average_true_range(
        [bar.high for bar in bars],
        [bar.low for bar in bars],
        [bar.close for bar in bars],
        period,
    )
    padding = max(0, len(bars) - len(raw))
    return [None] * padding + list(raw)


def _pct_change(left: Decimal, right: Decimal) -> Decimal:
    if left <= 0:
        return Decimal("0")
    return (right / left - Decimal("1")) * Decimal("100")


def _average(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")


def _close_location(bar: MarketBar) -> Decimal:
    width = bar.high - bar.low
    if width <= 0:
        return Decimal("1") if bar.close >= bar.open else Decimal("0")
    return (bar.close - bar.low) / width


def _leader_score(
    regular: list[MarketBar],
    sampled: list[MarketBar],
    *,
    index: int,
    context: LeaderMomentumContext | None,
) -> Decimal:
    current = sampled[index]
    prefix = sampled[: index + 1]
    regular_through = [bar for bar in regular if bar.end_time <= current.end_time]
    vwap = session_vwap(regular_through)
    one_minute_closes = [bar.close for bar in regular_through]
    ema9 = _ema(one_minute_closes, 9)[-1]
    ema20 = _ema(one_minute_closes, 20)[-1]
    if vwap is None or ema9 is None or ema20 is None:
        return Decimal("0")

    session_open = regular_through[0].open
    session_return = max(Decimal("0"), _pct_change(session_open, current.close))
    price_strength = min(Decimal("25"), session_return / Decimal("20") * Decimal("25"))

    trend_points = Decimal("0")
    if current.close > vwap:
        trend_points += Decimal("7")
    if current.close > ema9 > ema20:
        trend_points += Decimal("8")
    if len(regular_through) >= 12:
        prior_ema9 = _ema(one_minute_closes[:-3], 9)[-1]
        if prior_ema9 is not None and ema9 > prior_ema9:
            trend_points += Decimal("5")

    recent = prefix[-6:]
    new_highs = 0
    running_high = max((bar.high for bar in prefix[:-len(recent)]), default=Decimal("0"))
    for bar in recent:
        if bar.high > running_high:
            new_highs += 1
            running_high = bar.high
    hod_points = min(Decimal("15"), Decimal(new_highs) * Decimal("5"))

    prior_volume = prefix[max(0, index - 5):index]
    baseline = _average([bar.volume for bar in prior_volume])
    volume_ratio = current.volume / baseline if baseline > 0 else Decimal("0")
    volume_points = min(Decimal("20"), volume_ratio / Decimal("2") * Decimal("20"))

    execution_points = Decimal("10")
    if context is not None:
        if context.tod_rvol is not None:
            execution_points += min(Decimal("5"), context.tod_rvol / Decimal("8") * Decimal("5"))
        if context.relative_strength_pct is not None and context.relative_strength_pct > 0:
            execution_points += min(
                Decimal("5"), context.relative_strength_pct / Decimal("20") * Decimal("5")
            )
        if context.spread_bps is not None and context.spread_bps > Decimal("150"):
            execution_points -= Decimal("10")
        if context.dollar_volume is not None and context.dollar_volume < Decimal("2000000"):
            execution_points -= Decimal("5")
        if context.volume_acceleration is not None and context.volume_acceleration >= Decimal("1.5"):
            execution_points += Decimal("5")
        if context.hod_frequency_15m is not None:
            execution_points += min(
                Decimal("5"), Decimal(context.hod_frequency_15m) * Decimal("2.5")
            )

    return max(
        Decimal("0"),
        min(
            Decimal("100"),
            price_strength + trend_points + hod_points + volume_points + execution_points,
        ),
    )


def _setup_at(
    regular: list[MarketBar],
    sampled: list[MarketBar],
    ema9_3m: list[Decimal | None],
    atr14: list[Decimal | None],
    *,
    index: int,
) -> tuple[LeaderMode, Decimal, Decimal, Decimal] | None:
    """Return (mode, stop_reference, volume_ratio, entry_atr) for a breakout."""

    if index < 7:
        return None
    current = sampled[index]
    prior = sampled[:index]
    current_ema9_3m = ema9_3m[index]
    current_atr_3m = atr14[index]
    if current_ema9_3m is None or current_atr_3m is None or current_atr_3m <= 0:
        return None

    regular_through = [bar for bar in regular if bar.end_time <= current.end_time]
    entry_atr = _atr(regular_through, 14)[-1]
    if entry_atr is None or entry_atr <= 0:
        return None
    one_minute_closes = [bar.close for bar in regular_through]
    ema9_1m = _ema(one_minute_closes, 9)[-1]
    ema20_1m = _ema(one_minute_closes, 20)[-1]
    vwap = session_vwap(regular_through)
    if (
        vwap is None
        or ema9_1m is None
        or ema20_1m is None
        or current.close <= vwap
        or current.close <= ema9_1m
        or ema9_1m <= ema20_1m
    ):
        return None

    # v1.1 correction: compare a 3m trend mean with a 3m volatility measure.
    # The initial stop buffer intentionally keeps using the shorter source-tape
    # ATR returned as entry_atr so this fix does not silently retune risk.
    extension_pct = _pct_change(current_ema9_3m, current.close)
    extension_atr = (current.close - current_ema9_3m) / current_atr_3m
    if extension_pct > MAX_EMA9_EXTENSION_PCT or extension_atr > MAX_ATR_EXTENSION:
        return None

    prior_volumes = [bar.volume for bar in sampled[max(0, index - 5):index]]
    baseline_volume = _average(prior_volumes)
    volume_ratio = current.volume / baseline_volume if baseline_volume > 0 else Decimal("0")

    # Mode A: impulse -> 2-6 bar controlled pullback -> breakout.
    for pullback_len in range(2, 7):
        impulse_end = index - pullback_len
        if impulse_end < 2:
            continue
        impulse_start = max(0, impulse_end - 4)
        # v1.1 correction: a historical halt outside this candidate setup no
        # longer invalidates the setup; the impulse/pullback/breakout itself
        # must still be fully contiguous.
        if not _is_contiguous(sampled, impulse_start, index):
            continue
        pullback = sampled[impulse_end:index]
        impulse_window = sampled[impulse_start : impulse_end + 1]
        impulse_low = min(bar.low for bar in impulse_window)
        impulse_high = max(bar.high for bar in impulse_window)
        if impulse_low <= 0 or impulse_high <= impulse_low:
            continue
        impulse_pct = _pct_change(impulse_low, impulse_high)
        if impulse_pct < MIN_IMPULSE_PCT:
            continue
        if (
            REQUIRE_PULLBACK_NO_NEW_HIGH
            and max(bar.high for bar in pullback) > impulse_high * Decimal("1.01")
        ):
            continue
        pullback_low = min(bar.low for bar in pullback)
        impulse_size = impulse_high - impulse_low
        retrace = (impulse_high - pullback_low) / impulse_size
        if not (MIN_PULLBACK_RETRACE <= retrace <= MAX_PULLBACK_RETRACE):
            continue
        impulse_volume = _average([bar.volume for bar in impulse_window])
        pullback_volume = _average([bar.volume for bar in pullback])
        if impulse_volume <= 0 or pullback_volume / impulse_volume > MAX_PULLBACK_VOLUME_RATIO:
            continue
        breakout_level = max(bar.high for bar in pullback)
        if (
            current.close > breakout_level
            and _close_location(current) >= MIN_BREAKOUT_CLOSE_LOCATION
            and volume_ratio >= MIN_BREAKOUT_VOLUME_RATIO
        ):
            return "controlled_pullback", pullback_low, volume_ratio, entry_atr

    # Mode B: runaway impulse -> tight compression -> HOD breakout.
    for compression_len in range(2, 7):
        start = index - compression_len
        if start < 3:
            continue
        impulse_start = max(0, start - 3)
        if not _is_contiguous(sampled, impulse_start, index):
            continue
        compression = sampled[start:index]
        impulse_window = sampled[impulse_start : start + 1]
        impulse_low = min(bar.low for bar in impulse_window)
        impulse_high = max(bar.high for bar in impulse_window)
        if (
            impulse_low <= 0
            or _pct_change(impulse_low, impulse_high) < MIN_RUNAWAY_IMPULSE_PCT
        ):
            continue
        impulse_size = impulse_high - impulse_low
        compression_high = max(bar.high for bar in compression)
        compression_low = min(bar.low for bar in compression)
        if (
            impulse_size <= 0
            or (compression_high - compression_low) / impulse_size
            > MAX_COMPRESSION_WIDTH_RATIO
        ):
            continue
        if REQUIRE_COMPRESSION_ABOVE_EMA20 and any(
            bar.close < ema20_1m for bar in compression
        ):
            continue
        breakout_level = max(bar.high for bar in compression)
        if (
            current.close > breakout_level
            and (
                not REQUIRE_COMPRESSION_HOD_BREAK
                or current.close >= max(bar.high for bar in prior[-8:])
            )
            and _close_location(current) >= MIN_BREAKOUT_CLOSE_LOCATION
            and volume_ratio >= MIN_COMPRESSION_VOLUME_RATIO
        ):
            return "momentum_compression", compression_low, volume_ratio, entry_atr

    return None


def _pivot_low(sampled: list[MarketBar], start: int, end: int) -> Decimal | None:
    latest: Decimal | None = None
    lo = max(start + 1, 1)
    hi = min(end, len(sampled) - 1)
    for index in range(lo, hi):
        left, current, right = sampled[index - 1], sampled[index], sampled[index + 1]
        if current.low <= left.low and current.low < right.low:
            latest = current.low
    return latest


def _trade_from_signal(
    sampled: list[MarketBar],
    ema9: list[Decimal | None],
    atr14: list[Decimal | None],
    *,
    signal_index: int,
    mode: LeaderMode,
    stop_reference: Decimal,
    entry_atr: Decimal,
    force_flat_et: time,
) -> LeaderMomentumTrade | None:
    entry_index = signal_index + 1
    if entry_index >= len(sampled):
        return None
    entry_bar = sampled[entry_index]
    entry_price = entry_bar.open
    if entry_atr <= 0 or entry_price <= 0:
        return None

    stop_price = max(
        Decimal("0.0001"),
        stop_reference - entry_atr * INITIAL_STOP_BUFFER_ATR,
    )
    risk = entry_price - stop_price
    risk_pct = risk / entry_price * Decimal("100")
    if risk <= 0 or risk_pct > MAX_ENTRY_RISK_PCT:
        return None

    partial_time: datetime | None = None
    partial_price: Decimal | None = None
    remaining_fraction = Decimal("1")
    realized_value = Decimal("0")
    high_water = entry_price
    low_water = entry_price
    exit_index = len(sampled) - 1
    exit_reason = "LEADER_MOMENTUM_FORCE_FLAT"

    below_trend_count = 0
    for index in range(entry_index, len(sampled)):
        bar = sampled[index]
        if index > entry_index:
            previous_bar = sampled[index - 1]
            if bar.start_time - previous_bar.start_time != timedelta(minutes=3):
                below_trend_count = 0
                if bar.open <= stop_price:
                    exit_price = bar.open
                    gross = realized_value + remaining_fraction * exit_price
                    return LeaderMomentumTrade(
                        mode=mode,
                        signal_time=sampled[signal_index].end_time,
                        entry_time=entry_bar.start_time,
                        entry_price=entry_price,
                        initial_stop_price=stop_price,
                        exit_time=bar.start_time,
                        exit_price=exit_price,
                        exit_reason_code="LEADER_MOMENTUM_GAP_THROUGH_STOP",
                        partial_exit_time=partial_time,
                        partial_exit_price=partial_price,
                        return_pct=(gross / entry_price - Decimal("1")) * Decimal("100"),
                        mfe_pct=_pct_change(entry_price, high_water),
                        mae_pct=_pct_change(entry_price, min(low_water, bar.open)),
                    )
        high_water = max(high_water, bar.high)
        low_water = min(low_water, bar.low)
        if bar.end_time.astimezone(_ET).time() >= force_flat_et:
            exit_index = index
            exit_reason = "LEADER_MOMENTUM_FORCE_FLAT"
            break

        # Hard initial invalidation uses intrabar low; research fill is stop price.
        if bar.low <= stop_price:
            exit_price = stop_price
            gross = realized_value + remaining_fraction * exit_price
            return LeaderMomentumTrade(
                mode=mode,
                signal_time=sampled[signal_index].end_time,
                entry_time=entry_bar.start_time,
                entry_price=entry_price,
                initial_stop_price=stop_price,
                exit_time=bar.end_time,
                exit_price=exit_price,
                exit_reason_code="LEADER_MOMENTUM_INITIAL_STOP",
                partial_exit_time=partial_time,
                partial_exit_price=partial_price,
                return_pct=(gross / entry_price - Decimal("1")) * Decimal("100"),
                mfe_pct=_pct_change(entry_price, high_water),
                mae_pct=_pct_change(entry_price, low_water),
            )

        if partial_time is None and bar.high >= entry_price + risk * PARTIAL_TRIGGER_R:
            partial_time = bar.end_time
            partial_price = entry_price + risk * PARTIAL_TRIGGER_R
            realized_value = PARTIAL_FRACTION * partial_price
            remaining_fraction = Decimal("1") - PARTIAL_FRACTION

        current_ema9 = ema9[index]
        regular_prefix = sampled[: index + 1]
        vwap = session_vwap(regular_prefix)
        if (
            current_ema9 is not None
            and vwap is not None
            and bar.close < current_ema9
            and bar.close < vwap
        ):
            below_trend_count += 1
        else:
            below_trend_count = 0

        trailing_low = _pivot_low(sampled, entry_index, index)
        current_atr = atr14[index]
        if ENABLE_STRUCTURAL_EXIT and trailing_low is not None and current_atr is not None:
            trailing_stop = trailing_low - current_atr * STRUCTURAL_BUFFER_ATR
            if bar.close < trailing_stop:
                exit_index = min(index + 1, len(sampled) - 1)
                exit_reason = "LEADER_MOMENTUM_STRUCTURE_BREAK"
                break

        if ENABLE_TREND_EXIT and below_trend_count >= BELOW_TREND_EXIT_BARS:
            exit_index = min(index + 1, len(sampled) - 1)
            exit_reason = "LEADER_MOMENTUM_EMA9_VWAP_BREAK"
            break

        # Distribution reversal: high-volume red bar below VWAP then failed reclaim.
        if index >= entry_index + 1 and current_atr is not None and vwap is not None:
            previous = sampled[index - 1]
            previous_range = previous.high - previous.low
            previous_red = previous.close < previous.open
            previous_volume_baseline = _average(
                [item.volume for item in sampled[max(entry_index, index - 6): index - 1]]
            )
            distribution = (
                previous_red
                and previous_range >= current_atr * DISTRIBUTION_RANGE_ATR
                and previous.close < vwap
                and previous_volume_baseline > 0
                and previous.volume >= previous_volume_baseline
                * DISTRIBUTION_VOLUME_RATIO
            )
            if (
                ENABLE_DISTRIBUTION_EXIT
                and distribution
                and bar.high < vwap
                and bar.close < vwap
            ):
                exit_index = min(index + 1, len(sampled) - 1)
                exit_reason = "LEADER_MOMENTUM_DISTRIBUTION_REVERSAL"
                break

    exit_bar = sampled[exit_index]
    exit_price = exit_bar.open if exit_index > entry_index else exit_bar.close
    gross = realized_value + remaining_fraction * exit_price
    return LeaderMomentumTrade(
        mode=mode,
        signal_time=sampled[signal_index].end_time,
        entry_time=entry_bar.start_time,
        entry_price=entry_price,
        initial_stop_price=stop_price,
        exit_time=exit_bar.start_time,
        exit_price=exit_price,
        exit_reason_code=exit_reason,
        partial_exit_time=partial_time,
        partial_exit_price=partial_price,
        return_pct=(gross / entry_price - Decimal("1")) * Decimal("100"),
        mfe_pct=_pct_change(entry_price, high_water),
        mae_pct=_pct_change(entry_price, low_water),
    )


def evaluate_leader_momentum_continuation(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    context: LeaderMomentumContext | None = None,
    entry_start_et: time = time(9, 35),
    last_entry_et: time = time(15, 30),
    force_flat_et: time = time(15, 55),
) -> LeaderMomentumSnapshot:
    """Evaluate the v1.2 leader-momentum policy on finalized causal bars."""

    regular = _regular_final_bars(bars)
    if not regular:
        return LeaderMomentumSnapshot(
            state="waiting_session",
            reason_code="LEADER_MOMENTUM_WAITING_SESSION",
        )

    session_date = regular[-1].start_time.astimezone(_ET).date()
    regular = [
        bar for bar in regular if bar.start_time.astimezone(_ET).date() == session_date
    ]
    as_of = regular[-1].end_time
    base: dict[str, Any] = {"session_date": session_date.isoformat(), "as_of": as_of}
    raw_gap = _first_internal_gap(regular)

    sampled = [
        bar
        for bar in resample_final_bars(regular, "3m")
        if bar.session == "regular"
    ]
    if len(sampled) < 10:
        return LeaderMomentumSnapshot(
            state="waiting_leader",
            reason_code="LEADER_MOMENTUM_INSUFFICIENT_HISTORY",
            **base,
        )

    closes = [bar.close for bar in sampled]
    ema9_3m = _ema(closes, 9)
    atr14 = _atr(sampled, 14)
    recovered_gap_count = sum(
        1
        for left, right in zip(sampled, sampled[1:])
        if right.start_time - left.start_time != timedelta(minutes=3)
    )

    trades: list[LeaderMomentumTrade] = []
    scan_start = 9
    next_allowed_time = datetime.combine(session_date, entry_start_et, tzinfo=_ET)
    latest_score = Decimal("0")
    latest_vwap: Decimal | None = None
    latest_session_return: Decimal | None = None
    latest_mode: LeaderMode | None = None
    latest_signal_index: int | None = None
    latest_stop: Decimal | None = None
    leader_confirmed_at: datetime | None = None
    leader_confirmed_until: datetime | None = None

    for index in range(scan_start, len(sampled) - 1):
        bar = sampled[index]
        bar_et = bar.end_time.astimezone(_ET)
        if bar_et < next_allowed_time:
            continue
        if bar_et.time() < entry_start_et:
            continue
        if bar_et.time() > last_entry_et:
            break
        if not (MIN_PRICE <= bar.close <= MAX_PRICE):
            continue

        score = _leader_score(regular, sampled, index=index, context=context)
        latest_score = score
        regular_through = [item for item in regular if item.end_time <= bar.end_time]
        latest_vwap = session_vwap(regular_through)
        latest_session_return = _pct_change(regular_through[0].open, bar.close)

        # v1.1 correction: leadership is a causal state, not a property that a
        # pullback must re-prove on every bar. A fresh qualifying observation
        # starts/refreshes a bounded latch. Setup qualification below still
        # requires the current VWAP/EMA trend to be intact, so a stale latch
        # cannot authorize a structurally broken stock.
        if score >= MIN_LEADER_SCORE and latest_session_return >= MIN_SESSION_RETURN_PCT:
            leader_confirmed_at = bar.end_time
            leader_confirmed_until = bar.end_time + LEADER_LATCH_TTL
        leader_latched = (
            leader_confirmed_until is not None and bar.end_time <= leader_confirmed_until
        )
        if not leader_latched:
            continue

        setup = _setup_at(regular, sampled, ema9_3m, atr14, index=index)
        if setup is None:
            continue
        mode, stop_reference, _volume_ratio, entry_atr = setup

        # Re-entry requires a genuinely new session high after the previous exit.
        if trades and bar.close <= max(item.high for item in sampled[:index]):
            continue

        trade = _trade_from_signal(
            sampled,
            ema9_3m,
            atr14,
            signal_index=index,
            mode=mode,
            stop_reference=stop_reference,
            entry_atr=entry_atr,
            force_flat_et=force_flat_et,
        )
        latest_mode = mode
        latest_signal_index = index
        if trade is None:
            continue
        latest_stop = trade.initial_stop_price
        trades.append(trade)
        if len(trades) >= MAX_TRADES:
            break
        next_allowed_time = trade.exit_time.astimezone(_ET) + REENTRY_COOLDOWN

    final_ema9_1m = _ema([bar.close for bar in regular], 9)[-1]
    final_ema20_1m = _ema([bar.close for bar in regular], 20)[-1]
    final_atr14_1m = _atr(regular, 14)[-1]
    leader_latched_at_end = (
        leader_confirmed_until is not None and as_of <= leader_confirmed_until
    )

    shared: dict[str, Any] = {
        "leader_score": latest_score,
        "leader_confirmed_at": leader_confirmed_at,
        "leader_confirmed_until": leader_confirmed_until,
        "session_return_pct": latest_session_return,
        "session_vwap": latest_vwap,
        "ema9_1m": final_ema9_1m,
        "ema20_1m": final_ema20_1m,
        "ema9_3m": ema9_3m[-1],
        "atr14_1m": final_atr14_1m,
        "atr14_3m": atr14[-1],
        "recovered_gap_count": recovered_gap_count,
        "data_gap_start": raw_gap[0] if raw_gap is not None else None,
        "data_gap_resume": raw_gap[1] if raw_gap is not None else None,
        **base,
    }

    if trades:
        last = trades[-1]
        state: LeaderState = (
            "force_flat"
            if last.exit_reason_code == "LEADER_MOMENTUM_FORCE_FLAT"
            else "completed"
        )
        return LeaderMomentumSnapshot(
            state=state,
            reason_code=last.exit_reason_code,
            setup_mode=last.mode,
            signal_time=last.signal_time,
            entry_time=last.entry_time,
            entry_price=last.entry_price,
            initial_stop_price=last.initial_stop_price,
            risk_pct=(last.entry_price - last.initial_stop_price)
            / last.entry_price
            * Decimal("100"),
            trades=tuple(trades),
            **shared,
        )

    if latest_signal_index is not None and latest_mode is not None:
        return LeaderMomentumSnapshot(
            state="breakout_armed",
            reason_code="LEADER_MOMENTUM_BREAKOUT_AWAITING_ENTRY",
            setup_mode=latest_mode,
            signal_time=sampled[latest_signal_index].end_time,
            initial_stop_price=latest_stop,
            **shared,
        )

    return LeaderMomentumSnapshot(
        state="waiting_setup" if leader_latched_at_end else "waiting_leader",
        reason_code=(
            "LEADER_MOMENTUM_WAITING_SETUP"
            if leader_latched_at_end
            else "LEADER_MOMENTUM_LEADER_NOT_CONFIRMED"
        ),
        **shared,
    )


__all__ = [
    "POLICY_VERSION",
    "LEADER_LATCH_TTL",
    "LeaderMomentumContext",
    "LeaderMomentumSnapshot",
    "LeaderMomentumTrade",
    "evaluate_leader_momentum_continuation",
]
