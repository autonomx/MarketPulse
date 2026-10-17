from __future__ import annotations

"""Bar-coverage guard used by trading runtime owners.

Strategy entry authorization moved to ``order_gateway`` (WP-8.3).
"""

from datetime import datetime, timedelta, timezone

from .strategy_evaluability import assess_bar_coverage
from app.apps.trading.us_equity_calendar import EASTERN as _ET


class _CoverageMarketService:
    def __init__(self, delegate, *, session_date, observed_at) -> None:
        self._delegate = delegate
        self._session_date = session_date
        self._observed_at = observed_at

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def bars(self, instrument_id, interval, limit=500, binding_id=None, cancellation=None):
        response = self._delegate.bars(
            instrument_id,
            interval,
            limit,
            binding_id,
        )
        if interval == "1m" and self._observed_at.astimezone(_ET).time() >= datetime.strptime("09:30", "%H:%M").time():
            values = list(response.bars)
            assessment = assess_bar_coverage(
                values,
                session_date=self._session_date,
                observed_at=self._observed_at,
                provider="configured_history",
            )
            finalized = [bar for bar in values if getattr(bar, "is_final", False)]
            if not assessment.ready and finalized:
                latest_end = max(bar.end_time for bar in finalized)
                lag_seconds = (
                    self._observed_at.astimezone(timezone.utc)
                    - latest_end.astimezone(timezone.utc)
                ).total_seconds()
                if 0 <= lag_seconds <= 90:
                    effective_clock = min(
                        self._observed_at.astimezone(timezone.utc),
                        latest_end.astimezone(timezone.utc) + timedelta(seconds=30),
                    )
                    assessment = assess_bar_coverage(
                        values,
                        session_date=self._session_date,
                        observed_at=effective_clock,
                        provider="configured_history",
                    )
            if not assessment.ready:
                raise ValueError(
                    "bar_coverage_not_ready:"
                    + ",".join(assessment.reason_codes)
                )
        return response
