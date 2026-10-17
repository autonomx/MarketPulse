"""Late-stage research variant of the five-minute Stoch RSI strategy.

This variant preserves the canonical Stoch RSI entry and exit rules, but does
not authorize new entries before 10:00 AM Pacific.  The evaluator's schedule
is expressed in New York time, so the Pacific cutoff is represented as 13:00
ET for the North American market session.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

from .models import MarketBar
from .strategies.models import StochRsi5mConfig
from .strategy_stoch_rsi_5m import (
    StochRsi5mSnapshot,
    StochRsi5mState,
    evaluate_stoch_rsi_5m,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


LATE_STAGE_ENTRY_START_ET = time(13, 0)
LATE_STAGE_MINIMUM_OPEN_GAIN_PCT = Decimal("50")


def _current_regular_bars(
    bars: list[MarketBar] | tuple[MarketBar, ...],
) -> list[MarketBar]:
    regular = sorted(
        (
            bar
            for bar in bars
            if bar.is_final
            and bar.session == "regular"
            and time(9, 30)
            <= bar.start_time.astimezone(_ET).time()
            < regular_close_time(bar.start_time.astimezone(_ET).date())
        ),
        key=lambda bar: bar.start_time,
    )
    if not regular:
        return []
    session_date = regular[-1].start_time.astimezone(_ET).date()
    return [
        bar
        for bar in regular
        if bar.start_time.astimezone(_ET).date() == session_date
    ]


def _gate_snapshot(
    current_bars: list[MarketBar],
    *,
    reason_code: str,
    state: StochRsi5mState = "waiting_data",
) -> StochRsi5mSnapshot:
    last = current_bars[-1] if current_bars else None
    return StochRsi5mSnapshot(
        state=state,
        reason_code=reason_code,
        session_date=(
            last.start_time.astimezone(_ET).date().isoformat() if last else None
        ),
        as_of=last.end_time if last else None,
        five_minute_bar_count=len(current_bars),
    )


def evaluate_stoch_rsi_5m_late_stage(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    config: StochRsi5mConfig | None = None,
) -> StochRsi5mSnapshot:
    """Evaluate the late-stage Stoch RSI experiment.

    A completed bar ending at 10:00 AM Pacific must be at least 20% above the
    regular-session opening price. If it passes, entries remain restricted to
    the existing 10:00 AM Pacific onward window.
    """

    active = config or StochRsi5mConfig()
    current_bars = _current_regular_bars(bars)
    session_date = current_bars[-1].start_time.astimezone(_ET).date() if current_bars else None
    cutoff_utc = (
        datetime.combine(
            session_date,
            LATE_STAGE_ENTRY_START_ET,
            tzinfo=_ET,
        ).astimezone(timezone.utc)
        if session_date is not None
        else None
    )
    cutoff_bars = [
        bar
        for bar in current_bars
        if cutoff_utc is not None
        and bar.end_time.astimezone(timezone.utc) <= cutoff_utc
    ]
    if not current_bars or not cutoff_bars:
        return _gate_snapshot(
            current_bars,
            reason_code="STOCH_RSI_5M_LATE_STAGE_OPEN_GAIN_UNAVAILABLE",
        )

    opening_price = current_bars[0].open
    cutoff_close = cutoff_bars[-1].close
    minimum_cutoff_close = opening_price * (
        Decimal("1") + LATE_STAGE_MINIMUM_OPEN_GAIN_PCT / Decimal("100")
    )
    if cutoff_close < minimum_cutoff_close:
        return _gate_snapshot(
            current_bars,
            reason_code="STOCH_RSI_5M_LATE_STAGE_OPEN_GAIN_BELOW_THRESHOLD",
            state="waiting_oversold",
        )

    if active.entry_start_et < LATE_STAGE_ENTRY_START_ET:
        active = active.model_copy(update={"entry_start_et": LATE_STAGE_ENTRY_START_ET})
    return evaluate_stoch_rsi_5m(bars, active)


__all__ = [
    "LATE_STAGE_ENTRY_START_ET",
    "evaluate_stoch_rsi_5m_late_stage",
]
