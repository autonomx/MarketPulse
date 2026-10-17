"""Research-only 3-minute Stoch-RSI trend-capture strategy.

The policy is intentionally separate from AUTO PAPER authority. It uses only
finalized causal bars and produces a replay/snapshot that the strategy monitor
can persist as SHADOW evidence.

Policy:
- a regular-session 3m Stoch RSI K or D <= 20 arms or refreshes a bounded setup;
- momentum must recover (K crosses above D and reclaims 20), then price must
  close above a rising EMA9 and either reclaim session VWAP or break the prior
  3m high within five 3m bars;
- enter at the next 3m bar open after that confirmation;
- if price never proves an uptrend, exit the whole position at the first later
  K/D >= 80 reading (next 3m bar open);
- if price proves trend mode first, overbought is strength rather than an exit:
  take 25% at the first later overbought reading and keep 75% as a runner;
- tolerate one internal missing 1m bar by omitting only its incomplete 3m
  bucket; opening gaps and larger discontinuities remain fail-closed;
- exit the runner only after a buffered structural stop, two consecutive 3m
  closes below EMA9 and VWAP, or the 15:55 ET force-flat;
- live execution eligibility/halt/spread checks are a separate fail-closed veto.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .indicator_signals import _ema_aligned, _stochastic_rsi_aligned
from .indicators.engine import average_true_range
from .models import MarketBar
from .strategies.gap_pullback import session_vwap
from .strategy_timeframes import resample_final_bars
from app.apps.trading.us_equity_calendar import EASTERN as _ET


OVERSOLD = Decimal("20")
OVERBOUGHT = Decimal("80")
PARTIAL_FRACTION = Decimal("0.25")
TREND_ATR_PERIOD = 14
TREND_PIVOT_BUFFER_ATR = Decimal("0.75")
TREND_BREAK_CONFIRMATION_BARS = 2
TREND_MAX_RECOVERABLE_SOURCE_GAPS = 1
TREND_MAX_RECOVERABLE_SOURCE_GAP = timedelta(minutes=1)
ENTRY_SETUP_TTL = timedelta(minutes=15)
ENTRY_SETUP_BREAK_BUFFER_ATR = Decimal("0.50")

TrendCaptureState = Literal[
    "waiting_oversold",
    "data_gap",
    "setup_armed",
    "entry_armed",
    "range_active",
    "range_exit_armed",
    "range_exited",
    "trend_active",
    "trend_partial_armed",
    "trend_runner",
    "trend_exit_armed",
    "trend_exited",
    "force_flat",
]


class StochTrendCaptureSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["stoch-trend-capture-v3"] = "stoch-trend-capture-v3"
    state: TrendCaptureState
    reason_code: str
    three_minute_bar_count: int
    as_of: datetime | None = None
    setup_armed_time: datetime | None = None
    entry_signal_time: datetime | None = None
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    trend_confirmed_time: datetime | None = None
    first_overbought_time: datetime | None = None
    trend_break_time: datetime | None = None
    partial_exit_time: datetime | None = None
    partial_exit_price: Decimal | None = None
    partial_fraction: Decimal = PARTIAL_FRACTION
    runner_exit_time: datetime | None = None
    runner_exit_price: Decimal | None = None
    trailing_higher_low: Decimal | None = None
    trailing_stop_price: Decimal | None = None
    combined_exit_price: Decimal | None = None
    return_pct: Decimal | None = None
    stochastic_rsi_k: Decimal | None = None
    stochastic_rsi_d: Decimal | None = None
    data_gap_start_time: datetime | None = None
    data_gap_resume_time: datetime | None = None
    recovered_data_gap_count: int = 0
    recovered_data_gap_start_time: datetime | None = None
    recovered_data_gap_resume_time: datetime | None = None
    execution_authority: Literal[False] = False


class StochTrendRiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason_codes: tuple[str, ...] = ()


def stoch_trend_capture_risk_decision(
    execution: dict[str, Any],
    *,
    max_spread_bps: Decimal,
) -> StochTrendRiskDecision:
    """Fail closed on authoritative live execution defects at the entry signal."""

    reasons: list[str] = []
    if execution.get("halted") is True:
        reasons.append("STOCH_TREND_HALTED")
    if execution.get("execution_eligible") is not True:
        reasons.append("STOCH_TREND_EXECUTION_INELIGIBLE")
    spread_raw = execution.get("spread_bps")
    if spread_raw is not None:
        try:
            spread = Decimal(str(spread_raw))
        except Exception:
            reasons.append("STOCH_TREND_SPREAD_INVALID")
        else:
            if spread > max_spread_bps:
                reasons.append("STOCH_TREND_SPREAD_TOO_WIDE")
    return StochTrendRiskDecision(
        allowed=not reasons, reason_codes=tuple(dict.fromkeys(reasons))
    )


def _finalized_bars(
    bars: list[MarketBar] | tuple[MarketBar, ...],
) -> list[MarketBar]:
    return sorted((bar for bar in bars if bar.is_final), key=lambda bar: bar.start_time)


def _session_regular_positions(
    bars: list[MarketBar],
    session_date,
) -> list[int]:
    return [
        index
        for index, bar in enumerate(bars)
        if bar.session == "regular"
        and bar.start_time.astimezone(_ET).date() == session_date
    ]


def _first_regular_data_gap(
    bars: list[MarketBar],
    *,
    session_date,
    require_opening_bucket: bool,
) -> tuple[datetime, datetime] | None:
    """Return the first missing 3m interval inside the active regular session.

    Raw 1m monitor history is deep enough to prove whether the 09:30 bucket is
    missing. Direct 3m inputs may intentionally be bounded replay slices, so
    they can prove only internal discontinuities.
    """

    regular = [
        bar
        for bar in bars
        if bar.session == "regular"
        and bar.start_time.astimezone(_ET).date() == session_date
    ]
    if not regular:
        return None

    gaps: list[tuple[datetime, datetime]] = []
    if require_opening_bucket:
        expected_open = datetime.combine(session_date, time(9, 30), tzinfo=_ET)
        if regular[0].start_time != expected_open:
            gaps.append((expected_open, regular[0].start_time))

    for previous, current in zip(regular, regular[1:]):
        if current.start_time != previous.end_time:
            gaps.append((previous.end_time, current.start_time))
    return gaps[0] if gaps else None


def _regular_data_gaps(
    bars: list[MarketBar],
    *,
    session_date,
    require_opening_bucket: bool,
) -> list[tuple[datetime, datetime]]:
    """Return every same-session regular-tape discontinuity in causal order."""

    regular = [
        bar
        for bar in bars
        if bar.session == "regular"
        and bar.start_time.astimezone(_ET).date() == session_date
    ]
    if not regular:
        return []

    gaps: list[tuple[datetime, datetime]] = []
    if require_opening_bucket:
        expected_open = datetime.combine(session_date, time(9, 30), tzinfo=_ET)
        if regular[0].start_time != expected_open:
            gaps.append((expected_open, regular[0].start_time))
    for previous, current in zip(regular, regular[1:]):
        if current.start_time != previous.end_time:
            gaps.append((previous.end_time, current.start_time))
    return gaps


def _resolve_data_recovery(
    source_bars: list[MarketBar],
    sampled: list[MarketBar],
    *,
    session_date,
    require_opening_bucket: bool,
) -> tuple[list[tuple[datetime, datetime]], tuple[datetime, datetime] | None]:
    """Allow only one internal raw-minute gap without fabricating a candle."""

    if not require_opening_bucket:
        return [], _first_regular_data_gap(
            sampled,
            session_date=session_date,
            require_opening_bucket=False,
        )

    source_gaps = _regular_data_gaps(
        source_bars,
        session_date=session_date,
        require_opening_bucket=True,
    )
    if not source_gaps:
        return [], _first_regular_data_gap(
            sampled,
            session_date=session_date,
            require_opening_bucket=True,
        )
    if len(source_gaps) != TREND_MAX_RECOVERABLE_SOURCE_GAPS:
        return [], source_gaps[0]

    source_gap = source_gaps[0]
    expected_open = datetime.combine(session_date, time(9, 30), tzinfo=_ET)
    if source_gap[0] == expected_open:
        return [], source_gap
    if source_gap[1] - source_gap[0] != TREND_MAX_RECOVERABLE_SOURCE_GAP:
        return [], source_gap

    sampled_gaps = _regular_data_gaps(
        sampled,
        session_date=session_date,
        require_opening_bucket=True,
    )
    if len(sampled_gaps) != 1:
        return [], sampled_gaps[0] if sampled_gaps else source_gap
    sampled_gap = sampled_gaps[0]
    if (
        sampled_gap[1] - sampled_gap[0] != timedelta(minutes=3)
        or not sampled_gap[0] <= source_gap[0] < sampled_gap[1]
    ):
        return [], sampled_gap
    return source_gaps, None


def _next_regular_index(bars: list[MarketBar], after_index: int) -> int | None:
    for index in range(after_index + 1, len(bars)):
        if bars[index].session == "regular":
            return index
    return None


def _entry_price_confirmed(
    bars: list[MarketBar],
    ema9: list[Decimal | None],
    *,
    index: int,
) -> bool:
    """Require causal price/trend confirmation after oscillator recovery."""

    if index <= 0 or bars[index].session != "regular":
        return False
    current_ema = ema9[index]
    prior_ema = ema9[index - 1]
    if current_ema is None or prior_ema is None or current_ema <= prior_ema:
        return False
    vwap = session_vwap(_regular_prefix(bars, index))
    prior = bars[index - 1]
    structure_confirmed = (
        vwap is not None and bars[index].close >= vwap
    ) or (
        prior.session == "regular" and bars[index].close > prior.high
    )
    return bars[index].close > current_ema and structure_confirmed


def _setup_crosses_recovered_gap(
    recovered_gaps: list[tuple[datetime, datetime]],
    *,
    setup_time: datetime,
    through_time: datetime,
) -> bool:
    """Do not confirm an entry across an omitted/incomplete source bucket."""

    return any(
        setup_time <= gap_start < through_time
        for gap_start, _gap_resume in recovered_gaps
    )


def _entry_setup_broken(
    bars: list[MarketBar],
    *,
    setup_index: int,
    index: int,
) -> bool:
    """Invalidate only a close materially below the setup low."""

    prefix = bars[: index + 1]
    atr_values = average_true_range(
        [bar.high for bar in prefix],
        [bar.low for bar in prefix],
        [bar.close for bar in prefix],
        TREND_ATR_PERIOD,
    )
    current_atr = atr_values[-1] if atr_values else None
    if current_atr is None:
        return False
    invalidation_price = (
        bars[setup_index].low - current_atr * ENTRY_SETUP_BREAK_BUFFER_ATR
    )
    return bars[index].close < invalidation_price


def _regular_prefix(bars: list[MarketBar], through_index: int) -> list[MarketBar]:
    return [
        bar
        for index, bar in enumerate(bars)
        if index <= through_index and bar.session == "regular"
    ]


def _trend_confirmed(
    bars: list[MarketBar],
    ema9: list[Decimal | None],
    *,
    entry_index: int,
    index: int,
) -> bool:
    if index <= entry_index:
        return False
    current_ema = ema9[index]
    prior_ema = ema9[index - 1] if index > 0 else None
    if current_ema is None or prior_ema is None:
        return False
    current = bars[index]
    if current.session != "regular":
        return False

    recent = [bar for bar in bars[entry_index : index + 1] if bar.session == "regular"][
        -4:
    ]
    if len(recent) < 3:
        return False
    rising_low_pairs = sum(
        1 for left, right in zip(recent, recent[1:]) if right.low > left.low
    )
    if rising_low_pairs < 2:
        return False

    vwap = session_vwap(_regular_prefix(bars, index))
    return (
        current.close > current_ema
        and current_ema > prior_ema
        and vwap is not None
        and current.close >= vwap
    )


def _latest_confirmed_pivot_low(
    bars: list[MarketBar],
    *,
    entry_index: int,
    through_index: int,
) -> Decimal | None:
    latest: Decimal | None = None
    start = max(entry_index + 1, 1)
    for index in range(start, min(through_index, len(bars) - 1)):
        left, current, right = bars[index - 1], bars[index], bars[index + 1]
        if (
            current.session != "regular"
            or left.session != "regular"
            or right.session != "regular"
        ):
            continue
        if current.low <= left.low and current.low < right.low:
            latest = current.low
    return latest


def _trend_break(
    bars: list[MarketBar],
    ema9: list[Decimal | None],
    *,
    entry_index: int,
    index: int,
    prior_trailing_stop: Decimal | None = None,
) -> tuple[bool, Decimal | None, Decimal | None]:
    if index <= entry_index or bars[index].session != "regular":
        return False, None, None
    current_ema = ema9[index]
    prior_ema = ema9[index - 1] if index > 0 else None
    if current_ema is None or prior_ema is None:
        return False, None, None
    ema_falling = current_ema < prior_ema
    current = bars[index]
    trailing_low = _latest_confirmed_pivot_low(
        bars,
        entry_index=entry_index,
        through_index=index,
    )

    prefix = bars[: index + 1]
    atr_values = average_true_range(
        [bar.high for bar in prefix],
        [bar.low for bar in prefix],
        [bar.close for bar in prefix],
        TREND_ATR_PERIOD,
    )
    current_atr = atr_values[-1] if atr_values else None
    candidate_stop = (
        max(
            Decimal("0"),
            trailing_low - current_atr * TREND_PIVOT_BUFFER_ATR,
        )
        if trailing_low is not None and current_atr is not None
        else None
    )
    trailing_stop = (
        max(stop for stop in (prior_trailing_stop, candidate_stop) if stop is not None)
        if prior_trailing_stop is not None or candidate_stop is not None
        else None
    )
    pivot_break = trailing_stop is not None and current.close < trailing_stop

    confirmation_indexes = [
        candidate
        for candidate in range(entry_index, index + 1)
        if bars[candidate].session == "regular"
    ][-TREND_BREAK_CONFIRMATION_BARS:]
    sustained_ema_vwap_break = (
        len(confirmation_indexes) == TREND_BREAK_CONFIRMATION_BARS and ema_falling
    )
    if sustained_ema_vwap_break:
        for candidate_index in confirmation_indexes:
            candidate_ema = ema9[candidate_index]
            candidate_vwap = session_vwap(_regular_prefix(bars, candidate_index))
            if (
                candidate_ema is None
                or candidate_vwap is None
                or bars[candidate_index].close >= candidate_ema
                or bars[candidate_index].close >= candidate_vwap
            ):
                sustained_ema_vwap_break = False
                break
    return pivot_break or sustained_ema_vwap_break, trailing_low, trailing_stop


def _first_force_flat_index(
    bars: list[MarketBar],
    *,
    start_index: int,
    force_flat_et: time,
) -> int | None:
    """Return the first finalized 3m bar whose end crosses the cutoff.

    Using the containing bar's *open* would reference a price from before the
    configured cutoff (for example 15:54 for a 15:55 force-flat). The bar close
    is the first causal finalized 3m price available after the cutoff.
    """

    return next(
        (
            index
            for index in range(start_index, len(bars))
            if bars[index].session == "regular"
            and bars[index].end_time.astimezone(_ET).time() >= force_flat_et
        ),
        None,
    )


def _weighted_return(
    entry: Decimal,
    *,
    partial_price: Decimal | None,
    runner_price: Decimal,
) -> tuple[Decimal, Decimal]:
    combined = (
        runner_price
        if partial_price is None
        else partial_price * PARTIAL_FRACTION
        + runner_price * (Decimal("1") - PARTIAL_FRACTION)
    )
    return combined, (combined / entry - Decimal("1")) * Decimal("100")


def evaluate_stoch_trend_capture(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    entry_start_et: time = time(9, 35),
    last_entry_et: time = time(11, 30),
    force_flat_et: time = time(15, 55),
) -> StochTrendCaptureSnapshot:
    """Replay the single-trade policy causally over the available same-day tape."""

    finalized = _finalized_bars(bars)
    source_intervals = {bar.interval for bar in finalized}
    require_opening_bucket = source_intervals == {"1m"}
    regular_finalized = [bar for bar in finalized if bar.session == "regular"]
    sampled = (
        list(resample_final_bars(regular_finalized, "3m")) if regular_finalized else []
    )
    if not sampled:
        return StochTrendCaptureSnapshot(
            state="waiting_oversold",
            reason_code="STOCH_TREND_WAITING_FOR_3M_BARS",
            three_minute_bar_count=0,
        )

    # Carry prior regular-session bars into EMA/Stoch-RSI warmup just like a
    # chart does. The active session date comes from the latest finalized source
    # bar so an incomplete opening 3m bucket never falls back to yesterday.
    session_date = finalized[-1].start_time.astimezone(_ET).date()
    session_positions = _session_regular_positions(sampled, session_date)
    if not session_positions:
        return StochTrendCaptureSnapshot(
            state="waiting_oversold",
            reason_code="STOCH_TREND_WAITING_FOR_REGULAR_SESSION",
            three_minute_bar_count=len(sampled),
        )
    as_of = sampled[session_positions[-1]].end_time

    recovered_gaps, data_gap = _resolve_data_recovery(
        regular_finalized,
        sampled,
        session_date=session_date,
        require_opening_bucket=require_opening_bucket,
    )
    if data_gap is not None:
        gap_start, gap_resume = data_gap
        return StochTrendCaptureSnapshot(
            state="data_gap",
            reason_code="STOCH_TREND_REGULAR_SESSION_DATA_GAP",
            three_minute_bar_count=len(sampled),
            as_of=as_of,
            data_gap_start_time=gap_start,
            data_gap_resume_time=gap_resume,
        )

    recovery_fields: dict[str, Any] = dict(
        recovered_data_gap_count=len(recovered_gaps),
        recovered_data_gap_start_time=(
            recovered_gaps[0][0] if recovered_gaps else None
        ),
        recovered_data_gap_resume_time=(
            recovered_gaps[0][1] if recovered_gaps else None
        ),
    )

    closes = [bar.close for bar in sampled]
    ema9 = _ema_aligned(closes, 9)
    stoch_k, stoch_d = _stochastic_rsi_aligned(closes)

    setup_index: int | None = None
    bullish_cross_seen = False
    momentum_recovered = False
    signal_index: int | None = None
    for index in session_positions:
        signal_time = sampled[index].end_time.astimezone(_ET).time()
        if signal_time < entry_start_et or signal_time > last_entry_et:
            continue
        k = stoch_k[index]
        d = stoch_d[index]
        if k is None or d is None:
            continue

        if setup_index is not None:
            setup_bar = sampled[setup_index]
            setup_expired = (
                sampled[index].end_time - setup_bar.end_time > ENTRY_SETUP_TTL
            )
            setup_broken = _entry_setup_broken(
                sampled,
                setup_index=setup_index,
                index=index,
            )
            setup_crosses_gap = _setup_crosses_recovered_gap(
                recovered_gaps,
                setup_time=setup_bar.end_time,
                through_time=sampled[index].end_time,
            )
            if setup_expired or setup_broken or setup_crosses_gap:
                setup_index = None
                bullish_cross_seen = False
                momentum_recovered = False
            else:
                prior_k = stoch_k[index - 1] if index > 0 else None
                prior_d = stoch_d[index - 1] if index > 0 else None
                bullish_cross = (
                    prior_k is not None
                    and prior_d is not None
                    and prior_k <= prior_d
                    and k > d
                )
                bullish_cross_seen = bullish_cross_seen or bullish_cross
                momentum_recovered = bullish_cross_seen and k > OVERSOLD
                if momentum_recovered and _entry_price_confirmed(
                    sampled,
                    ema9,
                    index=index,
                ):
                    signal_index = index
                    break

        # Until a bullish crossover occurs, a fresh oversold observation is
        # the best causal anchor for both the setup low and its five-bar TTL.
        if (k <= OVERSOLD or d <= OVERSOLD) and not momentum_recovered:
            setup_index = index
            # A crossover can occur while the oscillator is still oversold;
            # retain it while refreshing the setup anchor until K reclaims 20.

    last_index = session_positions[-1]
    last_k = stoch_k[last_index]
    last_d = stoch_d[last_index]
    if signal_index is None:
        if setup_index is not None:
            return StochTrendCaptureSnapshot(
                state="setup_armed",
                reason_code="STOCH_TREND_OVERSOLD_SETUP_ARMED",
                three_minute_bar_count=len(sampled),
                as_of=as_of,
                setup_armed_time=sampled[setup_index].end_time,
                stochastic_rsi_k=last_k,
                stochastic_rsi_d=last_d,
                **recovery_fields,
            )
        return StochTrendCaptureSnapshot(
            state="waiting_oversold",
            reason_code="STOCH_TREND_NO_CONFIRMED_ENTRY_SETUP",
            three_minute_bar_count=len(sampled),
            as_of=as_of,
            stochastic_rsi_k=last_k,
            stochastic_rsi_d=last_d,
            **recovery_fields,
        )

    assert setup_index is not None
    setup_bar = sampled[setup_index]
    signal_bar = sampled[signal_index]
    entry_index = _next_regular_index(sampled, signal_index)
    if entry_index is None:
        return StochTrendCaptureSnapshot(
            state="entry_armed",
            reason_code="STOCH_TREND_RECOVERY_ENTRY_ARMED",
            three_minute_bar_count=len(sampled),
            as_of=as_of,
            setup_armed_time=setup_bar.end_time,
            entry_signal_time=signal_bar.end_time,
            stochastic_rsi_k=stoch_k[signal_index],
            stochastic_rsi_d=stoch_d[signal_index],
            **recovery_fields,
        )

    entry_bar = sampled[entry_index]
    entry_price = entry_bar.open
    trend_index: int | None = None
    overbought_index: int | None = None
    for index in range(entry_index, len(sampled)):
        if sampled[index].session != "regular":
            continue
        if trend_index is None and _trend_confirmed(
            sampled,
            ema9,
            entry_index=entry_index,
            index=index,
        ):
            trend_index = index
        if overbought_index is None:
            k = stoch_k[index]
            d = stoch_d[index]
            if k is not None and d is not None and k >= OVERBOUGHT and d >= OVERBOUGHT:
                overbought_index = index

    base: dict[str, Any] = dict(
        three_minute_bar_count=len(sampled),
        as_of=as_of,
        setup_armed_time=setup_bar.end_time,
        entry_signal_time=signal_bar.end_time,
        entry_time=entry_bar.start_time,
        entry_price=entry_price,
        **recovery_fields,
        trend_confirmed_time=(
            sampled[trend_index].end_time if trend_index is not None else None
        ),
        first_overbought_time=(
            sampled[overbought_index].end_time if overbought_index is not None else None
        ),
        stochastic_rsi_k=last_k,
        stochastic_rsi_d=last_d,
    )

    # Range/rebound mode: overbought is the exit because trend mode did not
    # prove itself before the oscillator reached the first extreme.
    if overbought_index is not None and (
        trend_index is None or overbought_index < trend_index
    ):
        exit_index = _next_regular_index(sampled, overbought_index)
        if exit_index is None:
            return StochTrendCaptureSnapshot(
                state="range_exit_armed",
                reason_code="STOCH_TREND_RANGE_OVERBOUGHT_EXIT_ARMED",
                **base,
            )
        exit_bar = sampled[exit_index]
        return_pct = (exit_bar.open / entry_price - Decimal("1")) * Decimal("100")
        return StochTrendCaptureSnapshot(
            state="range_exited",
            reason_code="STOCH_TREND_RANGE_OVERBOUGHT_EXIT",
            runner_exit_time=exit_bar.start_time,
            runner_exit_price=exit_bar.open,
            combined_exit_price=exit_bar.open,
            return_pct=return_pct,
            **base,
        )

    if trend_index is None:
        force_index = _first_force_flat_index(
            sampled,
            start_index=entry_index,
            force_flat_et=force_flat_et,
        )
        if force_index is not None:
            exit_bar = sampled[force_index]
            return_pct = (exit_bar.close / entry_price - Decimal("1")) * Decimal("100")
            return StochTrendCaptureSnapshot(
                state="force_flat",
                reason_code="STOCH_TREND_RANGE_FORCE_FLAT",
                runner_exit_time=exit_bar.end_time,
                runner_exit_price=exit_bar.close,
                combined_exit_price=exit_bar.close,
                return_pct=return_pct,
                **base,
            )
        return StochTrendCaptureSnapshot(
            state="range_active",
            reason_code="STOCH_TREND_RANGE_WAITING_FOR_TREND_OR_OVERBOUGHT",
            **base,
        )

    partial_time: datetime | None = None
    partial_price: Decimal | None = None
    runner_start = trend_index
    if overbought_index is not None and overbought_index >= trend_index:
        partial_index = _next_regular_index(sampled, overbought_index)
        if partial_index is None:
            return StochTrendCaptureSnapshot(
                state="trend_partial_armed",
                reason_code="STOCH_TREND_OVERBOUGHT_PARTIAL_ARMED",
                **base,
            )
        partial_time = sampled[partial_index].start_time
        partial_price = sampled[partial_index].open
        runner_start = partial_index

    trailing_low: Decimal | None = None
    trailing_stop: Decimal | None = None
    for index in range(max(runner_start, entry_index + 1), len(sampled)):
        if sampled[index].session != "regular":
            continue
        et_end = sampled[index].end_time.astimezone(_ET).time()
        if et_end >= force_flat_et:
            combined, return_pct = _weighted_return(
                entry_price,
                partial_price=partial_price,
                runner_price=sampled[index].close,
            )
            return StochTrendCaptureSnapshot(
                state="force_flat",
                reason_code="STOCH_TREND_FORCE_FLAT",
                partial_exit_time=partial_time,
                partial_exit_price=partial_price,
                runner_exit_time=sampled[index].end_time,
                runner_exit_price=sampled[index].close,
                trailing_higher_low=trailing_low,
                trailing_stop_price=trailing_stop,
                combined_exit_price=combined,
                return_pct=return_pct,
                **base,
            )

        broken, latest_low, latest_stop = _trend_break(
            sampled,
            ema9,
            entry_index=entry_index,
            index=index,
            prior_trailing_stop=trailing_stop,
        )
        if latest_low is not None:
            trailing_low = latest_low
        if latest_stop is not None:
            trailing_stop = latest_stop
        if not broken:
            continue
        exit_index = _next_regular_index(sampled, index)
        if exit_index is None:
            return StochTrendCaptureSnapshot(
                state="trend_exit_armed",
                reason_code="STOCH_TREND_BREAK_EXIT_ARMED",
                trend_break_time=sampled[index].end_time,
                partial_exit_time=partial_time,
                partial_exit_price=partial_price,
                trailing_higher_low=trailing_low,
                trailing_stop_price=trailing_stop,
                **base,
            )
        exit_bar = sampled[exit_index]
        combined, return_pct = _weighted_return(
            entry_price,
            partial_price=partial_price,
            runner_price=exit_bar.open,
        )
        return StochTrendCaptureSnapshot(
            state="trend_exited",
            reason_code="STOCH_TREND_BREAK_EXIT",
            trend_break_time=sampled[index].end_time,
            partial_exit_time=partial_time,
            partial_exit_price=partial_price,
            runner_exit_time=exit_bar.start_time,
            runner_exit_price=exit_bar.open,
            trailing_higher_low=trailing_low,
            trailing_stop_price=trailing_stop,
            combined_exit_price=combined,
            return_pct=return_pct,
            **base,
        )

    return StochTrendCaptureSnapshot(
        state="trend_runner" if partial_price is not None else "trend_active",
        reason_code=(
            "STOCH_TREND_RUNNER_ACTIVE"
            if partial_price is not None
            else "STOCH_TREND_CONFIRMED"
        ),
        partial_exit_time=partial_time,
        partial_exit_price=partial_price,
        trailing_higher_low=trailing_low,
        trailing_stop_price=trailing_stop,
        **base,
    )


__all__ = [
    "OVERBOUGHT",
    "OVERSOLD",
    "PARTIAL_FRACTION",
    "TREND_ATR_PERIOD",
    "TREND_BREAK_CONFIRMATION_BARS",
    "TREND_MAX_RECOVERABLE_SOURCE_GAP",
    "TREND_MAX_RECOVERABLE_SOURCE_GAPS",
    "TREND_PIVOT_BUFFER_ATR",
    "StochTrendCaptureSnapshot",
    "StochTrendRiskDecision",
    "evaluate_stoch_trend_capture",
    "stoch_trend_capture_risk_decision",
]
