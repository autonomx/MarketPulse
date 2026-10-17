from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.models import MarketBar

from .bar_semantics import interval_duration


def aggregation_plan(
    target_interval: str,
    supported_intervals: tuple[str, ...] | list[str],
) -> tuple[str, int] | None:
    """Return the closest supported base interval and its aggregation factor."""
    if target_interval in supported_intervals:
        return None
    try:
        target_seconds = interval_duration(target_interval).total_seconds()
    except ValueError:
        return None

    candidates: list[tuple[float, str, int]] = []
    for candidate in supported_intervals:
        try:
            base_seconds = interval_duration(candidate).total_seconds()
        except ValueError:
            continue
        if base_seconds <= 0 or target_seconds < base_seconds:
            continue
        ratio = target_seconds / base_seconds
        if ratio.is_integer():
            candidates.append((base_seconds, candidate, int(ratio)))
    if not candidates:
        return None
    _, base_interval, factor = max(candidates, key=lambda item: item[0])
    return base_interval, factor


def aggregate_market_bars(
    bars: list[MarketBar],
    *,
    target_interval: str,
    base_interval: str,
    factor: int,
) -> list[MarketBar]:
    """Combine sequential base bars into OHLCV bars for a derived interval."""
    if not bars:
        return []
    if factor <= 1:
        target_duration = interval_duration(target_interval)
        return [
            bar.model_copy(
                update={
                    "interval": target_interval,
                    "end_time": bar.start_time + target_duration,
                    "is_final": bar.start_time + target_duration <= bar.received_at,
                }
            )
            for bar in bars
        ]

    ordered = sorted(bars, key=lambda bar: bar.start_time)
    base_duration = interval_duration(base_interval)
    groups: list[list[MarketBar]] = []
    current: list[MarketBar] = []
    current_session_date = None

    for bar in ordered:
        session_date = bar.start_time.date()
        starts_new_equity_session = (
            current
            and bar.session != "24x7"
            and base_duration < timedelta(days=1)
            and session_date != current_session_date
        )
        if starts_new_equity_session:
            groups.append(current)
            current = []
        if not current:
            current_session_date = session_date
        current.append(bar)
        if len(current) == factor:
            groups.append(current)
            current = []
            current_session_date = None
    if current:
        groups.append(current)

    # A provider may return a partial first page. Do not expose that as a
    # misleading full-width candle; retain a partial final candle instead.
    if len(groups) > 1 and len(groups[0]) < factor:
        groups.pop(0)

    target_duration = interval_duration(target_interval)
    result: list[MarketBar] = []
    for group in groups:
        first = group[0]
        last = group[-1]
        end_time = first.start_time + target_duration
        received_at = max(bar.received_at for bar in group)
        result.append(
            MarketBar(
                instrument_id=first.instrument_id,
                interval=target_interval,
                start_time=first.start_time,
                end_time=end_time,
                open=first.open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=last.close,
                volume=sum((bar.volume for bar in group), Decimal("0")),
                is_final=end_time <= received_at,
                adjustment_mode=first.adjustment_mode,
                session=first.session,
                provider=first.provider,
                provider_event_id=(
                    f"aggregate:{base_interval}:{first.provider_event_id or first.start_time.isoformat()}"
                    f":{last.provider_event_id or last.start_time.isoformat()}"
                ),
                provider_sequence=last.provider_sequence,
                ingestion_revision=max(bar.ingestion_revision for bar in group),
                received_at=received_at,
            )
        )
    return result


def aggregated_dataset_fingerprint(
    base_fingerprint: str,
    *,
    target_interval: str,
    base_interval: str,
    factor: int,
) -> str:
    return TradingMarketDataCache.fingerprint(
        {
            "base_fingerprint": base_fingerprint,
            "base_interval": base_interval,
            "factor": factor,
            "target_interval": target_interval,
        }
    )
