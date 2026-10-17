from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import MarketBar
from .providers.bar_semantics import interval_duration


def interval_minutes(interval: str) -> int:
    """Return an integral minute duration for any intraday Trading interval."""

    duration = interval_duration(interval)
    seconds = duration.total_seconds()
    if seconds <= 0 or seconds >= timedelta(days=1).total_seconds() or seconds % 60 != 0:
        raise ValueError(f"unsupported strategy bar interval: {interval}")
    return int(seconds // 60)


def resample_final_bars(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    target_interval: str,
) -> list[MarketBar]:
    """Causally resample finalized bars and drop incomplete target buckets.

    Incomplete target buckets are omitted so a strategy can never see a partial
    future bar. Recovery may legitimately yield source bars from more than one
    provider. In that case the derived candle carries an explicit ``composite:``
    provider label instead of pretending the first source owned the whole bar.
    Consumers that require single-provider authority can therefore reject it
    deterministically without turning a recovered research tape into an error.
    """

    target_minutes = interval_minutes(target_interval)
    ordered = sorted((bar for bar in bars if bar.is_final), key=lambda bar: bar.start_time)
    if not ordered:
        return []

    source_intervals = {bar.interval for bar in ordered}
    if len(source_intervals) != 1:
        raise ValueError("strategy resampling requires one source interval")
    source_interval = next(iter(source_intervals))
    source_minutes = interval_minutes(source_interval)
    if target_minutes < source_minutes or target_minutes % source_minutes != 0:
        raise ValueError("target strategy interval must be an integer multiple of source interval")
    if target_minutes == source_minutes:
        return ordered

    ratio = target_minutes // source_minutes
    groups: dict[datetime, list[MarketBar]] = {}
    for bar in ordered:
        start = bar.start_time.astimezone(timezone.utc)
        bucket_minute = (start.minute // target_minutes) * target_minutes
        bucket_start = start.replace(minute=bucket_minute, second=0, microsecond=0)
        groups.setdefault(bucket_start, []).append(bar)

    output: list[MarketBar] = []
    source_delta = timedelta(minutes=source_minutes)
    target_delta = timedelta(minutes=target_minutes)
    for bucket_start in sorted(groups):
        group = sorted(groups[bucket_start], key=lambda bar: bar.start_time)
        if len(group) != ratio:
            continue
        expected_starts = [bucket_start + source_delta * index for index in range(ratio)]
        if [bar.start_time.astimezone(timezone.utc) for bar in group] != expected_starts:
            continue
        if any(
            bar.end_time.astimezone(timezone.utc) != expected + source_delta
            for bar, expected in zip(group, expected_starts)
        ):
            continue
        first = group[0]
        if any(bar.instrument_id != first.instrument_id for bar in group):
            raise ValueError("strategy resampling cannot mix instruments")
        if any(bar.adjustment_mode != first.adjustment_mode for bar in group):
            raise ValueError("strategy resampling cannot mix adjustment modes")
        if any(bar.session != first.session for bar in group):
            raise ValueError("strategy resampling cannot mix sessions")

        providers = tuple(sorted({bar.provider for bar in group}))
        derived_provider = (
            providers[0]
            if len(providers) == 1
            else "composite:" + "+".join(providers)
        )
        output.append(
            MarketBar(
                instrument_id=first.instrument_id,
                interval=target_interval,
                start_time=bucket_start,
                end_time=bucket_start + target_delta,
                open=first.open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=group[-1].close,
                volume=sum((bar.volume for bar in group), first.volume * 0),
                is_final=True,
                adjustment_mode=first.adjustment_mode,
                session=first.session,
                provider=derived_provider,
                provider_event_id=(
                    None
                    if len(providers) == 1
                    else "composite:" + ":".join(providers)
                ),
                provider_sequence=None,
                ingestion_revision=max(bar.ingestion_revision for bar in group),
                received_at=max(bar.received_at for bar in group),
            )
        )
    return output


def proposal_priority(
    *,
    observed_at: datetime,
    quality_score: int,
    discovery_rank: int | None,
    instrument_id: str,
) -> tuple[datetime, int, int, str]:
    """Causal portfolio ordering shared by AUTO PAPER and backtests.

    Chronology is always first. When candidates become actionable at the same
    timestamp, higher deterministic quality wins before discovery rank. This
    prevents a lower-quality simultaneous name from consuming scarce portfolio
    capacity merely because it appeared earlier in the morning scan.
    """

    return (
        observed_at.astimezone(timezone.utc),
        -quality_score,
        discovery_rank if discovery_rank is not None else 10**9,
        instrument_id,
    )
