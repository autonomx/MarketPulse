from __future__ import annotations

from datetime import timedelta

from .models import MarketBar
from app.apps.trading.us_equity_calendar import EASTERN as _ET


def session_aware_gap_indices(bars: list[MarketBar] | tuple[MarketBar, ...]) -> list[int]:
    """Return actual missing intraday-bar boundaries, ignoring market closures.

    Overnight, weekend and regular-to-extended session boundaries are not data
    gaps. Within a continuous same-session sequence, a jump larger than the
    inferred interval is reported.
    """
    if len(bars) < 2:
        return []
    ordered = sorted(bars, key=lambda bar: bar.start_time)
    output: list[int] = []
    for index in range(1, len(ordered)):
        previous = ordered[index - 1]
        current = ordered[index]
        previous_local = previous.start_time.astimezone(_ET)
        current_local = current.start_time.astimezone(_ET)
        if previous_local.date() != current_local.date():
            continue
        if previous.session != current.session:
            continue
        expected = previous.end_time - previous.start_time
        if expected <= timedelta(0):
            continue
        if current.start_time - previous.start_time > expected * 1.5:
            output.append(index)
    return output
