from __future__ import annotations

"""Cross-checks for prospective prediction outcome prices.

The consolidated SIP trade-event contract remains authoritative. Bars and daily
aggregates are validators only; they can raise DATA_CONFLICT but never overwrite
selected analysis prices.
"""

from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .models import AdjustmentMode, MarketBar
from .prospective_prediction_evidence import AnalysisSessionPrices
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time

DATA_QUALITY_POLICY_VERSION = "prospective-price-crosscheck-v1"


class PriceCrossCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = DATA_QUALITY_POLICY_VERSION
    instrument_id: str
    authoritative_source: str = "consolidated_sip_trade_events"
    tolerance_bps: Decimal
    first_raw_bar_open: Decimal | None = None
    last_raw_bar_close: Decimal | None = None
    daily_bar_open: Decimal | None = None
    daily_bar_close: Decimal | None = None
    daily_bar_adjustment_mode: AdjustmentMode | None = None
    session_boundary_complete: bool = False
    data_conflict: bool = False
    flags: tuple[str, ...] = ()


def _bps(reference: Decimal, observed: Decimal) -> Decimal:
    if reference <= 0:
        return Decimal("999999")
    return abs(observed - reference) / reference * Decimal("10000")


def cross_check_analysis_prices(
    prices: AnalysisSessionPrices,
    *,
    intraday_bars: Sequence[MarketBar],
    daily_bar: MarketBar | None = None,
    tolerance_bps: Decimal = Decimal("25"),
) -> PriceCrossCheckResult:
    """Validate SIP-selected prices without allowing validators to replace them."""

    session_start = datetime.combine(prices.session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    session_end = datetime.combine(prices.session_date, regular_close_time(prices.session_date), tzinfo=_ET).astimezone(timezone.utc)
    raw = sorted(
        [
            bar
            for bar in intraday_bars
            if bar.instrument_id == prices.instrument_id
            and bar.is_final
            and bar.session == "regular"
            and bar.adjustment_mode == AdjustmentMode.RAW
            and session_start <= bar.start_time < session_end
        ],
        key=lambda bar: (bar.start_time, bar.end_time, bar.provider_sequence or -1),
    )

    flags: list[str] = []
    first_open = raw[0].open if raw else None
    last_close = raw[-1].close if raw else None
    boundary_complete = bool(
        raw
        and raw[0].start_time <= session_start
        and raw[-1].end_time >= session_end
    )
    if not raw:
        flags.append("RAW_INTRADAY_VALIDATION_UNAVAILABLE")
    else:
        if _bps(prices.open_price, raw[0].open) > tolerance_bps:
            flags.append("INTRADAY_OPEN_MISMATCH")
        if _bps(prices.close_price, raw[-1].close) > tolerance_bps:
            flags.append("INTRADAY_CLOSE_MISMATCH")
        session_high = max(bar.high for bar in raw)
        session_low = min(bar.low for bar in raw)
        if not session_low <= prices.open_price <= session_high:
            flags.append("ANALYSIS_OPEN_OUTSIDE_INTRADAY_RANGE")
        if not session_low <= prices.close_price <= session_high:
            flags.append("ANALYSIS_CLOSE_OUTSIDE_INTRADAY_RANGE")
        if not boundary_complete:
            flags.append("RAW_INTRADAY_SESSION_BOUNDARY_INCOMPLETE")

    daily_open = daily_bar.open if daily_bar is not None else None
    daily_close = daily_bar.close if daily_bar is not None else None
    daily_adjustment = daily_bar.adjustment_mode if daily_bar is not None else None
    if daily_bar is not None:
        if daily_bar.instrument_id != prices.instrument_id:
            flags.append("DAILY_BAR_INSTRUMENT_MISMATCH")
        if daily_bar.adjustment_mode != AdjustmentMode.RAW:
            flags.append("DAILY_BAR_ADJUSTED_NON_AUTHORITATIVE")
        if _bps(prices.open_price, daily_bar.open) > tolerance_bps:
            flags.append("DAILY_OPEN_MISMATCH")
        if _bps(prices.close_price, daily_bar.close) > tolerance_bps:
            flags.append("DAILY_CLOSE_MISMATCH")

    conflict_flags = {
        "INTRADAY_OPEN_MISMATCH",
        "INTRADAY_CLOSE_MISMATCH",
        "ANALYSIS_OPEN_OUTSIDE_INTRADAY_RANGE",
        "ANALYSIS_CLOSE_OUTSIDE_INTRADAY_RANGE",
        "DAILY_OPEN_MISMATCH",
        "DAILY_CLOSE_MISMATCH",
        "DAILY_BAR_INSTRUMENT_MISMATCH",
    }
    return PriceCrossCheckResult(
        instrument_id=prices.instrument_id,
        tolerance_bps=tolerance_bps,
        first_raw_bar_open=first_open,
        last_raw_bar_close=last_close,
        daily_bar_open=daily_open,
        daily_bar_close=daily_close,
        daily_bar_adjustment_mode=daily_adjustment,
        session_boundary_complete=boundary_complete,
        data_conflict=any(flag in conflict_flags for flag in flags),
        flags=tuple(flags),
    )
