from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""Point-in-time premarket liquidity evidence for prospective Finviz cohorts."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from .historical_gapper_reconstruction import _alpaca_bars
from .market_evidence import (
    DEFAULT_MARKET_EVIDENCE_POLICY,
    MarketEvidencePolicy,
    PremarketLiquidityEvidence,
)
from .providers.alpaca_iex import alpaca_iex_auth_headers
from .providers.http_runtime import ProviderHttpRuntime
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_PREMARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_BASELINE_LOOKBACK_DAYS = 21


def _decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _latest_completed_start_clock(evaluation_et: datetime) -> time | None:
    opening = datetime.combine(evaluation_et.date(), _PREMARKET_OPEN, tzinfo=_ET)
    if evaluation_et < opening + timedelta(minutes=1):
        return None
    latest_start = (evaluation_et - timedelta(minutes=1)).replace(second=0, microsecond=0)
    if latest_start.time() >= _REGULAR_OPEN:
        latest_start = datetime.combine(evaluation_et.date(), _REGULAR_OPEN, tzinfo=_ET) - timedelta(minutes=1)
    return latest_start.time()


def _expected_elapsed_minutes(evaluation_et: datetime) -> int:
    cutoff = _latest_completed_start_clock(evaluation_et)
    if cutoff is None:
        return 0
    start_minutes = _PREMARKET_OPEN.hour * 60 + _PREMARKET_OPEN.minute
    cutoff_minutes = cutoff.hour * 60 + cutoff.minute
    return max(0, cutoff_minutes - start_minutes + 1)


def alpaca_premarket_liquidity_evidence(
    symbol: str,
    evaluation_time: datetime,
    *,
    runtime: ProviderHttpRuntime | None = None,
    policy: MarketEvidencePolicy = DEFAULT_MARKET_EVIDENCE_POLICY,
) -> PremarketLiquidityEvidence:
    """Build same-feed IEX numerator/denominator evidence through ``evaluation_time``.

    Absolute volume and TOD-RVOL are both IEX-scoped. This is intentionally not
    represented as SIP/NBBO coverage. Only bars whose one-minute interval had
    fully completed by the scan time participate in either the current numerator
    or historical same-clock denominator.
    """

    if evaluation_time.tzinfo is None:
        raise ValueError("evaluation_time must be timezone-aware")
    evaluation = evaluation_time.astimezone(timezone.utc)
    evaluation_et = evaluation.astimezone(_ET)
    if evaluation_et.time() >= _REGULAR_OPEN:
        raise ValueError("premarket liquidity evidence must be frozen before 09:30 ET")

    active_runtime = runtime or ProviderHttpRuntime(
        "alpaca_premarket_liquidity",
        max_concurrency=2,
    )
    headers = alpaca_iex_auth_headers()
    start_local = datetime.combine(
        evaluation_et.date() - timedelta(days=_BASELINE_LOOKBACK_DAYS),
        _PREMARKET_OPEN,
        tzinfo=_ET,
    )
    raw = _alpaca_bars(
        active_runtime,
        headers,
        [symbol.upper()],
        timeframe="1Min",
        start=start_local.astimezone(timezone.utc),
        end=evaluation,
        chunk_size=1,
    ).get(symbol.upper(), [])

    cutoff_clock = _latest_completed_start_clock(evaluation_et)
    volume_by_date: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    dollar_volume_by_date: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    bar_count_by_date: dict[date, int] = defaultdict(int)
    nonzero_count_by_date: dict[date, int] = defaultdict(int)

    for item in raw:
        observed = _timestamp(item.get("t"))
        volume = _decimal(item.get("v"))
        close = _decimal(item.get("c"))
        if observed is None or volume is None or volume < 0 or close is None or close <= 0:
            continue
        local = observed.astimezone(_ET)
        clock = local.timetz().replace(tzinfo=None)
        if not (_PREMARKET_OPEN <= clock < _REGULAR_OPEN):
            continue
        if cutoff_clock is None or clock > cutoff_clock:
            continue
        # This is redundant for historical sessions but essential for current-day
        # safety if an upstream returns the still-forming bar despite ``end``.
        if local.date() == evaluation_et.date() and observed + timedelta(minutes=1) > evaluation:
            continue
        bar_count_by_date[local.date()] += 1
        volume_by_date[local.date()] += volume
        dollar_volume_by_date[local.date()] += volume * close
        if volume > 0:
            nonzero_count_by_date[local.date()] += 1

    current_date = evaluation_et.date()
    current_volume = volume_by_date.get(current_date, Decimal("0"))
    current_dollar_volume = dollar_volume_by_date.get(current_date, Decimal("0"))
    current_bar_count = bar_count_by_date.get(current_date, 0)
    nonzero_count = nonzero_count_by_date.get(current_date, 0)
    historical = [
        volume
        for session_date, volume in sorted(volume_by_date.items(), key=lambda item: item[0])
        if session_date < current_date and volume > 0 and bar_count_by_date.get(session_date, 0) > 0
    ]
    baseline_count = len(historical)
    denominator = (
        sum(historical, Decimal("0")) / Decimal(baseline_count)
        if baseline_count
        else None
    )
    tod_rvol = (
        current_volume / denominator
        if denominator is not None
        and denominator > 0
        and baseline_count >= policy.minimum_tod_rvol_baseline_sessions
        else None
    )

    expected_minutes = _expected_elapsed_minutes(evaluation_et)
    coverage_ratio = (
        Decimal(current_bar_count) / Decimal(expected_minutes)
        if expected_minutes > 0
        else None
    )
    reasons: list[str] = []
    if current_bar_count == 0:
        reasons.append("PREMARKET_BARS_MISSING")
    if current_bar_count > 0 and nonzero_count == 0:
        reasons.append("PREMARKET_VOLUME_SUSPICIOUS_ZERO")
    if current_volume <= 0:
        reasons.append("PREMARKET_VOLUME_UNAVAILABLE")
    if baseline_count < policy.minimum_tod_rvol_baseline_sessions:
        reasons.append("TOD_RVOL_BASELINE_INSUFFICIENT")
    if tod_rvol is None:
        reasons.append("TOD_RVOL_MISSING")

    observed_at = datetime.now(timezone.utc)
    return PremarketLiquidityEvidence(
        policy_version=policy.version,
        provider=policy.premarket_provider,
        feed=policy.premarket_feed,
        observed_at=observed_at,
        current_premarket_volume=current_volume,
        current_premarket_dollar_volume=current_dollar_volume,
        tod_rvol=tod_rvol,
        tod_rvol_numerator=current_volume,
        tod_rvol_denominator_mean=denominator,
        baseline_session_count=baseline_count,
        premarket_bar_count=current_bar_count,
        nonzero_volume_bar_count=nonzero_count,
        coverage_ratio=coverage_ratio,
        ready=not reasons,
        reason_codes=tuple(dict.fromkeys(reasons)),
        volume_authority="provider_relative",
        volume_basis="alpaca_iex",
        consolidated_volume_authority=False,
    )


__all__ = ["alpaca_premarket_liquidity_evidence"]
