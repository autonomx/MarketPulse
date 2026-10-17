from __future__ import annotations

"""Shared causal market-data recovery and dependency-aware evaluability.

Recovery is deliberately strategy-agnostic. Providers produce the best factual
bar tape they can; strategies declare which portion of that tape their current
decision depends on. An unresolved historical gap therefore remains visible in
provenance without poisoning later rolling calculations that have independently
warmed up after the gap.

No function in this module fabricates OHLCV values. Missing windows are either
filled by real provider bars / deterministic lower-timeframe aggregation or stay
explicitly unresolved.
"""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from collections.abc import Callable
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import MarketBar
from .providers.bar_semantics import interval_duration
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


_REGULAR_OPEN = time(9, 30)

ContinuityMode = Literal["session", "rolling"]
KnowledgeMode = Literal["live", "causal_replay", "retroactive_research"]
DataField = Literal["ohlc", "volume"]
PriceScope = Literal["PROVIDER_TRADES", "US_EQUITY_TRADES", "PARTIAL_MARKET", "UNKNOWN"]
VolumeScope = Literal["PROVIDER_RELATIVE", "CONSOLIDATED", "PARTIAL_MARKET", "UNKNOWN"]
RecoveryMethod = Literal["primary", "primary_repair", "fallback", "unknown"]
EvaluabilityStatus = Literal[
    "full_session_complete",
    "recovered_complete",
    "rolling_window_complete",
    "post_gap_warming",
    "unresolved_dependency",
    "waiting_data",
]


class BarGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interval: str
    start: datetime
    end: datetime
    missing_bar_count: int = Field(ge=1)


class CoverageSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interval: str
    start: datetime
    end: datetime
    bar_count: int = Field(ge=1)


class RecoveryBucketEvidence(BaseModel):
    """Field-level source semantics for one recovered canonical bucket."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_time: datetime
    end_time: datetime
    price_provider: str
    volume_provider: str
    price_scope: PriceScope = "UNKNOWN"
    volume_scope: VolumeScope = "UNKNOWN"
    partial_market_price: bool = False
    partial_market_volume: bool = False
    recovery_method: RecoveryMethod = "unknown"
    contract_id: str | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _aware_bucket_time(cls, value: datetime) -> datetime:
        return _utc(value)


class StrategyDataRequirement(BaseModel):
    """Data dependency contract for one strategy decision.

    ``session`` means every finalized bucket from the regular-session open is an
    input to the calculation. ``rolling`` means state may reset at an unresolved
    gap and become independently evaluable after ``minimum_clean_bars`` new bars.

    Alpaca IEX is a partial-market feed. Price-only SHADOW calculations may opt
    into it explicitly; volume-sensitive calculations remain blocked unless a
    strategy separately proves that partial-market volume is acceptable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    interval: str
    continuity: ContinuityMode = "session"
    minimum_clean_bars: int = Field(default=1, ge=1)
    required_fields: tuple[DataField, ...] = ("ohlc",)
    reset_on_gap: bool = True
    allow_partial_market_price: bool = False
    allow_partial_market_volume: bool = False
    allow_mixed_provider_price: bool = False
    allow_mixed_provider_volume: bool = False


class DataEvaluability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: EvaluabilityStatus
    evaluable: bool
    reset_required: bool = False
    clean_bar_count: int = 0
    clean_start: datetime | None = None
    clean_end: datetime | None = None
    dependency_start: datetime | None = None
    dependency_end: datetime | None = None
    reason_codes: tuple[str, ...] = ()


class RecoveryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    interval: str
    session_date: date
    as_of: datetime
    knowledge_mode: KnowledgeMode = "live"
    knowledge_cutoff: datetime | None = None
    primary_provider: str | None = None
    fallback_provider: str | None = None
    requested_binding: str | None = None
    resolved_binding: str | None = None
    primary_attempt_count: int = Field(default=0, ge=0)
    fallback_attempted: bool = False
    primary_error: str | None = None
    fallback_error: str | None = None
    expected_bar_count: int = Field(default=0, ge=0)
    primary_bar_count: int = Field(default=0, ge=0)
    canonical_bar_count: int = Field(default=0, ge=0)
    recovered_bar_count: int = Field(default=0, ge=0)
    recovered_starts: tuple[datetime, ...] = ()
    unresolved_gaps: tuple[BarGap, ...] = ()
    confirmed_nontrading_starts: tuple[datetime, ...] = ()
    unresolved_market_state_starts: tuple[datetime, ...] = ()
    source_providers: tuple[str, ...] = ()
    partial_market_fallback: bool = False
    ibkr_shadow_repair_attempted: bool = False
    ibkr_shadow_recoverable_count: int = Field(default=0, ge=0)
    ibkr_repair_applied_count: int = Field(default=0, ge=0)
    recovery_source_order: tuple[str, ...] = ()
    no_synthetic_prices: Literal[True] = True
    dataset_fingerprint: str


@dataclass(frozen=True)
class RecoveredBars:
    bars: tuple[MarketBar, ...]
    report: RecoveryReport
    primary_response: Any | None = None
    bucket_evidence: tuple[RecoveryBucketEvidence, ...] = ()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("market-data recovery timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _session_bounds(session_date: date) -> tuple[datetime, datetime]:
    opening = datetime.combine(session_date, _REGULAR_OPEN, tzinfo=_ET)
    close = datetime.combine(session_date, regular_close_time(session_date), tzinfo=_ET)
    return opening.astimezone(timezone.utc), close.astimezone(timezone.utc)


def finalized_session_bars(
    bars: Sequence[MarketBar],
    *,
    session_date: date,
    interval: str | None = None,
    as_of: datetime | None = None,
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
) -> list[MarketBar]:
    cutoff = _utc(as_of) if as_of is not None else None
    known_by = (
        _utc(knowledge_cutoff)
        if knowledge_cutoff is not None
        else cutoff
    )
    rows = [
        bar
        for bar in bars
        if bar.is_final
        and bar.session == "regular"
        and bar.start_time.astimezone(_ET).date() == session_date
        and (interval is None or getattr(bar, "interval", interval) == interval)
        and (cutoff is None or bar.end_time <= cutoff)
        and (
            knowledge_mode != "causal_replay"
            or known_by is None
            or getattr(bar, "received_at", bar.end_time) <= known_by
        )
    ]
    return sorted(
        rows,
        key=lambda item: (
            item.start_time,
            getattr(item, "received_at", item.end_time),
        ),
    )


def deduplicate_bars(
    bars: Sequence[MarketBar],
    *,
    preferred_provider: str | None = None,
) -> list[MarketBar]:
    """Resolve revisions without blending values from the same time bucket."""

    selected: dict[datetime, MarketBar] = {}
    for bar in bars:
        key = _utc(bar.start_time)
        prior = selected.get(key)
        if prior is None:
            selected[key] = bar
            continue
        prior_preferred = int(
            preferred_provider is not None
            and getattr(prior, "provider", None) == preferred_provider
        )
        current_preferred = int(
            preferred_provider is not None
            and getattr(bar, "provider", None) == preferred_provider
        )
        prior_sequence = getattr(prior, "provider_sequence", None)
        current_sequence = getattr(bar, "provider_sequence", None)
        prior_rank = (
            prior_preferred,
            int(getattr(prior, "ingestion_revision", 0) or 0),
            getattr(prior, "received_at", prior.end_time),
            prior_sequence if prior_sequence is not None else -1,
        )
        current_rank = (
            current_preferred,
            int(getattr(bar, "ingestion_revision", 0) or 0),
            getattr(bar, "received_at", bar.end_time),
            current_sequence if current_sequence is not None else -1,
        )
        if current_rank > prior_rank:
            selected[key] = bar
    return [selected[key] for key in sorted(selected)]


def expected_bar_starts(
    *,
    session_date: date,
    interval: str,
    as_of: datetime,
) -> tuple[datetime, ...]:
    """Return finalized regular-session bucket starts knowable at ``as_of``.

    We intentionally do not invent a partial closing bucket for intervals that
    do not divide the 390-minute US regular session. Such provider-specific
    partial bars can still be consumed, but they are not treated as required for
    generic gap detection.
    """

    duration = interval_duration(interval)
    if duration <= timedelta(0) or duration >= timedelta(days=1):
        return ()
    opening, close = _session_bounds(session_date)
    cutoff = min(_utc(as_of), close)
    starts: list[datetime] = []
    cursor = opening
    while cursor + duration <= cutoff and cursor + duration <= close:
        starts.append(cursor)
        cursor += duration
    return tuple(starts)


def detect_session_gaps(
    bars: Sequence[MarketBar],
    *,
    session_date: date,
    interval: str,
    as_of: datetime,
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
) -> tuple[BarGap, ...]:
    expected = expected_bar_starts(
        session_date=session_date,
        interval=interval,
        as_of=as_of,
    )
    if not expected:
        return ()
    duration = interval_duration(interval)
    starts = {
        _utc(bar.start_time)
        for bar in finalized_session_bars(
            bars,
            session_date=session_date,
            interval=interval,
            as_of=as_of,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )
    }
    missing = [start for start in expected if start not in starts]
    if not missing:
        return ()

    gaps: list[BarGap] = []
    gap_start = missing[0]
    previous = missing[0]
    count = 1
    for current in missing[1:]:
        if current == previous + duration:
            count += 1
            previous = current
            continue
        gaps.append(
            BarGap(
                interval=interval,
                start=gap_start,
                end=previous + duration,
                missing_bar_count=count,
            )
        )
        gap_start = previous = current
        count = 1
    gaps.append(
        BarGap(
            interval=interval,
            start=gap_start,
            end=previous + duration,
            missing_bar_count=count,
        )
    )
    return tuple(gaps)


def _provider_bucket_semantics(
    bar: MarketBar,
    *,
    recovered_starts: set[datetime],
) -> RecoveryBucketEvidence:
    provider = str(bar.provider or "")
    if provider == "yahoo":
        price_scope: PriceScope = "PROVIDER_TRADES"
        volume_scope: VolumeScope = "PROVIDER_RELATIVE"
        partial_price = False
        partial_volume = False
    elif provider == "ibkr":
        # IBKR TRADES is useful price evidence, but volume authority remains
        # conservative until entitlement/feed semantics are explicitly proven.
        price_scope = "US_EQUITY_TRADES"
        volume_scope = "UNKNOWN"
        partial_price = False
        partial_volume = False
    elif provider == "alpaca_iex":
        price_scope = "PARTIAL_MARKET"
        volume_scope = "PARTIAL_MARKET"
        partial_price = True
        partial_volume = True
    else:
        price_scope = "UNKNOWN"
        volume_scope = "UNKNOWN"
        partial_price = False
        partial_volume = False

    contract_id = None
    event_id = str(bar.provider_event_id or "")
    if provider == "ibkr" and event_id.startswith("ibkr:"):
        parts = event_id.split(":", 2)
        if len(parts) >= 2 and parts[1]:
            contract_id = parts[1]
    start = _utc(bar.start_time)
    return RecoveryBucketEvidence(
        start_time=start,
        end_time=_utc(bar.end_time),
        price_provider=provider,
        volume_provider=provider,
        price_scope=price_scope,
        volume_scope=volume_scope,
        partial_market_price=partial_price,
        partial_market_volume=partial_volume,
        recovery_method="fallback" if start in recovered_starts else "primary",
        contract_id=contract_id,
    )


def _gaps_from_missing_starts(
    starts: Sequence[datetime],
    *,
    interval: str,
) -> tuple[BarGap, ...]:
    ordered = sorted({_utc(value) for value in starts})
    if not ordered:
        return ()
    duration = interval_duration(interval)
    gaps: list[BarGap] = []
    start = previous = ordered[0]
    count = 1
    for current in ordered[1:]:
        if current == previous + duration:
            previous = current
            count += 1
            continue
        gaps.append(
            BarGap(
                interval=interval,
                start=start,
                end=previous + duration,
                missing_bar_count=count,
            )
        )
        start = previous = current
        count = 1
    gaps.append(
        BarGap(
            interval=interval,
            start=start,
            end=previous + duration,
            missing_bar_count=count,
        )
    )
    return tuple(gaps)


def coverage_segments(
    bars: Sequence[MarketBar],
    *,
    session_date: date,
    interval: str,
    as_of: datetime | None = None,
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
) -> tuple[CoverageSegment, ...]:
    rows = deduplicate_bars(
        finalized_session_bars(
            bars,
            session_date=session_date,
            interval=interval,
            as_of=as_of,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )
    )
    if not rows:
        return ()
    duration = interval_duration(interval)
    segments: list[CoverageSegment] = []
    start_index = 0
    for index in range(1, len(rows)):
        if rows[index].start_time != rows[index - 1].start_time + duration:
            group = rows[start_index:index]
            segments.append(
                CoverageSegment(
                    interval=interval,
                    start=group[0].start_time,
                    end=group[-1].end_time,
                    bar_count=len(group),
                )
            )
            start_index = index
    group = rows[start_index:]
    segments.append(
        CoverageSegment(
            interval=interval,
            start=group[0].start_time,
            end=group[-1].end_time,
            bar_count=len(group),
        )
    )
    return tuple(segments)


def latest_clean_bars(
    bars: Sequence[MarketBar],
    *,
    session_date: date,
    interval: str,
    as_of: datetime | None = None,
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
    bridge_single_missing: bool = False,
) -> list[MarketBar]:
    """The latest unbroken run of bars; ``bridge_single_missing`` steps over one-bar holes."""
    rows = deduplicate_bars(
        finalized_session_bars(
            bars,
            session_date=session_date,
            interval=interval,
            as_of=as_of,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )
    )
    if not rows:
        return []
    duration = interval_duration(interval)
    steps = {duration, duration * 2} if bridge_single_missing else {duration}
    start = len(rows) - 1
    while start > 0 and rows[start].start_time - rows[start - 1].start_time in steps:
        start -= 1
    return rows[start:]


def aggregate_complete_bars(
    bars: Sequence[MarketBar],
    *,
    session_date: date,
    target_interval: str,
    as_of: datetime,
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
) -> list[MarketBar]:
    """Aggregate one provider's complete lower-timeframe buckets only."""

    rows = finalized_session_bars(
        bars,
        session_date=session_date,
        as_of=as_of,
        knowledge_mode=knowledge_mode,
        knowledge_cutoff=knowledge_cutoff,
    )
    if not rows:
        return []
    source_intervals = {bar.interval for bar in rows}
    providers = {bar.provider for bar in rows}
    if len(source_intervals) != 1:
        raise ValueError("recovery aggregation requires one source interval")
    if len(providers) != 1:
        raise ValueError("recovery aggregation cannot mix providers")
    source_interval = next(iter(source_intervals))
    source_duration = interval_duration(source_interval)
    target_duration = interval_duration(target_interval)
    if target_duration < source_duration:
        raise ValueError("recovery target interval cannot be finer than source")
    ratio = target_duration.total_seconds() / source_duration.total_seconds()
    if not ratio.is_integer():
        raise ValueError("recovery target interval must be an integer source multiple")
    factor = int(ratio)
    if factor == 1:
        return [bar.model_copy(update={"interval": target_interval}) for bar in rows]

    opening, _ = _session_bounds(session_date)
    by_start = {_utc(bar.start_time): bar for bar in deduplicate_bars(rows)}
    output: list[MarketBar] = []
    for bucket_start in expected_bar_starts(
        session_date=session_date,
        interval=target_interval,
        as_of=as_of,
    ):
        expected = [bucket_start + source_duration * index for index in range(factor)]
        if any(value not in by_start for value in expected):
            continue
        group = [by_start[value] for value in expected]
        if group[0].start_time < opening:
            continue
        first, last = group[0], group[-1]
        output.append(
            MarketBar(
                instrument_id=first.instrument_id,
                interval=target_interval,
                start_time=bucket_start,
                end_time=bucket_start + target_duration,
                open=first.open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=last.close,
                volume=sum((bar.volume for bar in group), Decimal("0")),
                is_final=True,
                adjustment_mode=first.adjustment_mode,
                session=first.session,
                provider=first.provider,
                provider_event_id=(
                    f"recovery-aggregate:{source_interval}:"
                    f"{first.provider_event_id or first.start_time.isoformat()}:"
                    f"{last.provider_event_id or last.start_time.isoformat()}"
                ),
                provider_sequence=last.provider_sequence,
                ingestion_revision=max(bar.ingestion_revision for bar in group),
                received_at=max(bar.received_at for bar in group),
            )
        )
    return output


def reconcile_recovery(
    *,
    instrument_id: str,
    interval: str,
    session_date: date,
    as_of: datetime,
    primary_bars: Sequence[MarketBar],
    fallback_bars: Sequence[MarketBar] = (),
    primary_provider: str | None = None,
    fallback_provider: str | None = None,
    requested_binding: str | None = None,
    resolved_binding: str | None = None,
    primary_attempt_count: int = 1,
    fallback_attempted: bool = False,
    primary_error: str | None = None,
    fallback_error: str | None = None,
    partial_market_fallback: bool = False,
    primary_response: Any | None = None,
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
    confirmed_nontrading_starts: Sequence[datetime] = (),
    unresolved_market_state_starts: Sequence[datetime] = (),
) -> RecoveredBars:
    """Fill only genuinely missing buckets; primary revisions win duplicates."""

    primary = deduplicate_bars(
        finalized_session_bars(
            primary_bars,
            session_date=session_date,
            interval=interval,
            as_of=as_of,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        ),
        preferred_provider=primary_provider,
    )
    initial_gaps = detect_session_gaps(
        primary,
        session_date=session_date,
        interval=interval,
        as_of=as_of,
        knowledge_mode=knowledge_mode,
        knowledge_cutoff=knowledge_cutoff,
    )
    missing_starts: set[datetime] = set()
    duration = interval_duration(interval)
    for gap in initial_gaps:
        cursor = gap.start
        while cursor < gap.end:
            missing_starts.add(cursor)
            cursor += duration

    fallback = deduplicate_bars(
        finalized_session_bars(
            fallback_bars,
            session_date=session_date,
            interval=interval,
            as_of=as_of,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        ),
        preferred_provider=fallback_provider,
    )
    recovered = [bar for bar in fallback if _utc(bar.start_time) in missing_starts]
    canonical = deduplicate_bars(
        [*primary, *recovered],
        preferred_provider=primary_provider,
    )
    unresolved_detected = detect_session_gaps(
        canonical,
        session_date=session_date,
        interval=interval,
        as_of=as_of,
        knowledge_mode=knowledge_mode,
        knowledge_cutoff=knowledge_cutoff,
    )
    confirmed_set = {_utc(value) for value in confirmed_nontrading_starts}
    unresolved_starts: list[datetime] = []
    for gap in unresolved_detected:
        cursor = gap.start
        while cursor < gap.end:
            if cursor not in confirmed_set:
                unresolved_starts.append(cursor)
            cursor += duration
    unresolved = _gaps_from_missing_starts(
        unresolved_starts,
        interval=interval,
    )
    recovered_starts = tuple(sorted(_utc(bar.start_time) for bar in recovered))
    source_providers = tuple(sorted({bar.provider for bar in canonical}))
    expected_count = len(
        expected_bar_starts(
            session_date=session_date,
            interval=interval,
            as_of=as_of,
        )
    )
    fingerprint_payload = "|".join(
        [
            instrument_id,
            interval,
            session_date.isoformat(),
            *(
                f"{bar.start_time.isoformat()}:{bar.provider}:{bar.ingestion_revision}:"
                f"{bar.open}:{bar.high}:{bar.low}:{bar.close}:{bar.volume}"
                for bar in canonical
            ),
        ]
    )
    bucket_evidence = tuple(
        _provider_bucket_semantics(
            bar,
            recovered_starts=set(recovered_starts),
        )
        for bar in canonical
    )
    report = RecoveryReport(
        instrument_id=instrument_id,
        interval=interval,
        session_date=session_date,
        as_of=_utc(as_of),
        primary_provider=primary_provider,
        fallback_provider=fallback_provider,
        requested_binding=requested_binding,
        resolved_binding=resolved_binding,
        primary_attempt_count=primary_attempt_count,
        fallback_attempted=fallback_attempted,
        primary_error=primary_error,
        fallback_error=fallback_error,
        knowledge_mode=knowledge_mode,
        knowledge_cutoff=(
            _utc(knowledge_cutoff)
            if knowledge_cutoff is not None
            else _utc(as_of)
        ),
        expected_bar_count=expected_count,
        primary_bar_count=len(primary),
        canonical_bar_count=len(canonical),
        recovered_bar_count=len(recovered_starts),
        recovered_starts=recovered_starts,
        unresolved_gaps=unresolved,
        confirmed_nontrading_starts=tuple(
            sorted(_utc(value) for value in confirmed_nontrading_starts)
        ),
        unresolved_market_state_starts=tuple(
            sorted(
                _utc(value)
                for value in unresolved_market_state_starts
                if _utc(value) in set(unresolved_starts)
            )
        ),
        source_providers=source_providers,
        partial_market_fallback=partial_market_fallback and bool(recovered_starts),
        dataset_fingerprint=hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest(),
    )
    return RecoveredBars(
        tuple(canonical),
        report,
        primary_response,
        bucket_evidence,
    )


def assess_data_requirement(
    bars: Sequence[MarketBar],
    *,
    session_date: date,
    as_of: datetime,
    requirement: StrategyDataRequirement,
    recovery_report: RecoveryReport | None = None,
    bucket_evidence: Sequence[RecoveryBucketEvidence] = (),
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
) -> DataEvaluability:
    """Prove whether the current decision actually depends on a known gap."""

    rows = deduplicate_bars(
        finalized_session_bars(
            bars,
            session_date=session_date,
            interval=requirement.interval,
            as_of=as_of,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )
    )
    if not rows:
        return DataEvaluability(
            status="waiting_data",
            evaluable=False,
            reason_codes=("DATA_UNAVAILABLE",),
        )

    gaps = detect_session_gaps(
        rows,
        session_date=session_date,
        interval=requirement.interval,
        as_of=as_of,
        knowledge_mode=knowledge_mode,
        knowledge_cutoff=knowledge_cutoff,
    )
    if recovery_report is not None and recovery_report.confirmed_nontrading_starts:
        confirmed = {
            _utc(value)
            for value in recovery_report.confirmed_nontrading_starts
        }
        duration = interval_duration(requirement.interval)
        unresolved_starts: list[datetime] = []
        for gap in gaps:
            cursor = gap.start
            while cursor < gap.end:
                if cursor not in confirmed:
                    unresolved_starts.append(cursor)
                cursor += duration
        gaps = _gaps_from_missing_starts(
            unresolved_starts,
            interval=requirement.interval,
        )
    expected = expected_bar_starts(
        session_date=session_date,
        interval=requirement.interval,
        as_of=as_of,
    )

    if requirement.continuity == "session":
        dependency = rows
        if gaps:
            return DataEvaluability(
                status="unresolved_dependency",
                evaluable=False,
                clean_bar_count=len(rows),
                clean_start=rows[0].start_time,
                clean_end=rows[-1].end_time,
                dependency_start=expected[0] if expected else rows[0].start_time,
                dependency_end=rows[-1].end_time,
                reason_codes=("SESSION_DEPENDS_ON_UNRESOLVED_GAP",),
            )
    else:
        dependency = latest_clean_bars(
            rows,
            session_date=session_date,
            interval=requirement.interval,
            as_of=as_of,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )
        reset_required = len(dependency) < len(rows) or bool(gaps)
        if len(dependency) < requirement.minimum_clean_bars:
            return DataEvaluability(
                status="post_gap_warming" if reset_required else "waiting_data",
                evaluable=False,
                reset_required=reset_required,
                clean_bar_count=len(dependency),
                clean_start=dependency[0].start_time if dependency else None,
                clean_end=dependency[-1].end_time if dependency else None,
                dependency_start=dependency[0].start_time if dependency else None,
                dependency_end=dependency[-1].end_time if dependency else None,
                reason_codes=(
                    "POST_GAP_WARMUP_INCOMPLETE"
                    if reset_required
                    else "STRATEGY_WARMUP_INCOMPLETE",
                ),
            )

    dependency_starts = {_utc(bar.start_time) for bar in dependency}
    semantic_rows = [
        row
        for row in bucket_evidence
        if _utc(row.start_time) in dependency_starts
    ]
    if bucket_evidence and len(semantic_rows) != len(dependency_starts):
        return DataEvaluability(
            status="unresolved_dependency",
            evaluable=False,
            reset_required=requirement.continuity == "rolling" and bool(gaps),
            clean_bar_count=len(dependency),
            clean_start=dependency[0].start_time,
            clean_end=dependency[-1].end_time,
            dependency_start=dependency[0].start_time,
            dependency_end=dependency[-1].end_time,
            reason_codes=("SOURCE_PROVENANCE_INCOMPLETE",),
        )

    if semantic_rows:
        if "ohlc" in requirement.required_fields:
            price_providers = {row.price_provider for row in semantic_rows}
            if len(price_providers) > 1 and not requirement.allow_mixed_provider_price:
                return DataEvaluability(
                    status="unresolved_dependency",
                    evaluable=False,
                    reset_required=requirement.continuity == "rolling" and bool(gaps),
                    clean_bar_count=len(dependency),
                    clean_start=dependency[0].start_time,
                    clean_end=dependency[-1].end_time,
                    dependency_start=dependency[0].start_time,
                    dependency_end=dependency[-1].end_time,
                    reason_codes=("MIXED_PROVIDER_PRICE_NOT_AUTHORIZED",),
                )
            if (
                any(row.partial_market_price for row in semantic_rows)
                and not requirement.allow_partial_market_price
            ):
                return DataEvaluability(
                    status="unresolved_dependency",
                    evaluable=False,
                    reset_required=requirement.continuity == "rolling" and bool(gaps),
                    clean_bar_count=len(dependency),
                    clean_start=dependency[0].start_time,
                    clean_end=dependency[-1].end_time,
                    dependency_start=dependency[0].start_time,
                    dependency_end=dependency[-1].end_time,
                    reason_codes=("PARTIAL_MARKET_PRICE_NOT_AUTHORIZED",),
                )
        if "volume" in requirement.required_fields:
            volume_providers = {row.volume_provider for row in semantic_rows}
            if len(volume_providers) > 1 and not requirement.allow_mixed_provider_volume:
                return DataEvaluability(
                    status="unresolved_dependency",
                    evaluable=False,
                    reset_required=requirement.continuity == "rolling" and bool(gaps),
                    clean_bar_count=len(dependency),
                    clean_start=dependency[0].start_time,
                    clean_end=dependency[-1].end_time,
                    dependency_start=dependency[0].start_time,
                    dependency_end=dependency[-1].end_time,
                    reason_codes=("MIXED_PROVIDER_VOLUME_NOT_AUTHORIZED",),
                )
            if any(row.volume_scope == "UNKNOWN" for row in semantic_rows):
                return DataEvaluability(
                    status="unresolved_dependency",
                    evaluable=False,
                    reset_required=requirement.continuity == "rolling" and bool(gaps),
                    clean_bar_count=len(dependency),
                    clean_start=dependency[0].start_time,
                    clean_end=dependency[-1].end_time,
                    dependency_start=dependency[0].start_time,
                    dependency_end=dependency[-1].end_time,
                    reason_codes=("UNKNOWN_VOLUME_SCOPE_NOT_AUTHORIZED",),
                )
            if (
                any(row.partial_market_volume for row in semantic_rows)
                and not requirement.allow_partial_market_volume
            ):
                return DataEvaluability(
                    status="unresolved_dependency",
                    evaluable=False,
                    reset_required=requirement.continuity == "rolling" and bool(gaps),
                    clean_bar_count=len(dependency),
                    clean_start=dependency[0].start_time,
                    clean_end=dependency[-1].end_time,
                    dependency_start=dependency[0].start_time,
                    dependency_end=dependency[-1].end_time,
                    reason_codes=("PARTIAL_MARKET_VOLUME_NOT_EQUIVALENT",),
                )
    else:
        # Compatibility for callers that have not yet supplied field-level
        # provenance. Partial-market recovery remains fail-closed exactly as
        # before; mixed-provider authority requires bucket evidence.
        fallback_dependency = False
        if recovery_report is not None and recovery_report.partial_market_fallback:
            recovered = set(recovery_report.recovered_starts)
            fallback_dependency = any(_utc(bar.start_time) in recovered for bar in dependency)
        if fallback_dependency:
            if "volume" in requirement.required_fields and not requirement.allow_partial_market_volume:
                return DataEvaluability(
                    status="unresolved_dependency",
                    evaluable=False,
                    reset_required=requirement.continuity == "rolling" and bool(gaps),
                    clean_bar_count=len(dependency),
                    clean_start=dependency[0].start_time,
                    clean_end=dependency[-1].end_time,
                    dependency_start=dependency[0].start_time,
                    dependency_end=dependency[-1].end_time,
                    reason_codes=("PARTIAL_MARKET_VOLUME_NOT_EQUIVALENT",),
                )
            if "ohlc" in requirement.required_fields and not requirement.allow_partial_market_price:
                return DataEvaluability(
                    status="unresolved_dependency",
                    evaluable=False,
                    reset_required=requirement.continuity == "rolling" and bool(gaps),
                    clean_bar_count=len(dependency),
                    clean_start=dependency[0].start_time,
                    clean_end=dependency[-1].end_time,
                    dependency_start=dependency[0].start_time,
                    dependency_end=dependency[-1].end_time,
                    reason_codes=("PARTIAL_MARKET_PRICE_NOT_AUTHORIZED",),
                )

    reset_required = requirement.continuity == "rolling" and (
        len(dependency) < len(rows) or bool(gaps)
    )
    if len(dependency) < requirement.minimum_clean_bars:
        return DataEvaluability(
            status="post_gap_warming" if reset_required else "waiting_data",
            evaluable=False,
            reset_required=reset_required,
            clean_bar_count=len(dependency),
            clean_start=dependency[0].start_time,
            clean_end=dependency[-1].end_time,
            dependency_start=dependency[0].start_time,
            dependency_end=dependency[-1].end_time,
            reason_codes=("STRATEGY_WARMUP_INCOMPLETE",),
        )

    if requirement.continuity == "rolling":
        status: EvaluabilityStatus = "rolling_window_complete"
    elif recovery_report is not None and recovery_report.recovered_bar_count:
        status = "recovered_complete"
    else:
        status = "full_session_complete"
    return DataEvaluability(
        status=status,
        evaluable=True,
        reset_required=reset_required,
        clean_bar_count=len(dependency),
        clean_start=dependency[0].start_time,
        clean_end=dependency[-1].end_time,
        dependency_start=dependency[0].start_time,
        dependency_end=dependency[-1].end_time,
        reason_codes=(),
    )


# ---------------------------------------------------------------------------
# v3 compatibility API
#
# The leader-momentum branch already contains the richer dependency-aware
# recovery/evaluability framework above.  These contracts provide the simpler
# orchestration surface consumed by AI Shadow v3 without replacing that richer
# implementation.
# ---------------------------------------------------------------------------

RecoveryStatus = Literal["COMPLETE", "PARTIAL", "UNAVAILABLE"]


class RecoveryAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal[
        "primary",
        "primary_retry",
        "fallback",
        "lower_resolution_rebuild",
        "nontrading_confirmation",
    ]
    source: str
    succeeded: bool
    bar_count: int = Field(default=0, ge=0)
    detail: str | None = None


class RecoveredBarSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    instrument_id: str
    interval: str
    session_date: date
    observed_at: datetime
    knowledge_mode: KnowledgeMode = "live"
    knowledge_cutoff: datetime | None = None
    bars: tuple[MarketBar, ...]
    status: RecoveryStatus
    unresolved_starts: tuple[datetime, ...] = ()
    confirmed_nontrading_starts: tuple[datetime, ...] = ()
    attempts: tuple[RecoveryAttempt, ...] = ()
    source_providers: tuple[str, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def _aware_observed_at(cls, value: datetime) -> datetime:
        return _utc(value)


def _recovery_gap_starts(
    bars: Sequence[MarketBar],
    *,
    interval: str,
    session_date: date,
    observed_at: datetime,
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
) -> tuple[datetime, ...]:
    duration = interval_duration(interval)
    values: list[datetime] = []
    for gap in detect_session_gaps(
        bars,
        session_date=session_date,
        interval=interval,
        as_of=observed_at,
        knowledge_mode=knowledge_mode,
        knowledge_cutoff=knowledge_cutoff,
    ):
        cursor = gap.start
        while cursor < gap.end:
            values.append(cursor)
            cursor += duration
    return tuple(values)


def _recovery_safe_fetch(
    fetcher: Callable[[], Sequence[MarketBar]],
    *,
    stage: Literal[
        "primary",
        "primary_retry",
        "fallback",
        "lower_resolution_rebuild",
        "nontrading_confirmation",
    ],
    source: str,
) -> tuple[list[MarketBar], RecoveryAttempt]:
    try:
        values = list(fetcher())
    except Exception as exc:
        return [], RecoveryAttempt(
            stage=stage,
            source=source,
            succeeded=False,
            detail=f"{type(exc).__name__}: {exc}",
        )
    return values, RecoveryAttempt(
        stage=stage,
        source=source,
        succeeded=True,
        bar_count=len(values),
    )


def recover_market_bars(
    *,
    instrument_id: str,
    interval: str,
    session_date: date,
    observed_at: datetime,
    primary_fetch: Callable[[], Sequence[MarketBar]],
    primary_source: str,
    primary_retry_fetch: Callable[[], Sequence[MarketBar]] | None = None,
    fallback_fetch: Callable[[], Sequence[MarketBar]] | None = None,
    fallback_source: str | None = None,
    lower_resolution_fetch: Callable[[], Sequence[MarketBar]] | None = None,
    lower_resolution_source: str | None = None,
    confirm_nontrading: Callable[[datetime, datetime], bool] | None = None,
    knowledge_mode: KnowledgeMode = "live",
    knowledge_cutoff: datetime | None = None,
) -> RecoveredBarSeries:
    """Ordered provider recovery with explicit unresolved/non-trading windows.

    This is intentionally an orchestration layer only.  It reuses the canonical
    finalized/deduplicated bar semantics above and never fabricates OHLCV values.
    """

    observed_at = _utc(observed_at)
    attempts: list[RecoveryAttempt] = []
    collected: list[MarketBar] = []

    def canonical() -> list[MarketBar]:
        return deduplicate_bars(
            finalized_session_bars(
                collected,
                session_date=session_date,
                interval=interval,
                as_of=observed_at,
                knowledge_mode=knowledge_mode,
                knowledge_cutoff=knowledge_cutoff,
            ),
            preferred_provider=primary_source,
        )

    values, attempt = _recovery_safe_fetch(
        primary_fetch,
        stage="primary",
        source=primary_source,
    )
    attempts.append(attempt)
    collected.extend(values)
    rows = canonical()
    missing = _recovery_gap_starts(
        rows,
        interval=interval,
        session_date=session_date,
        observed_at=observed_at,
        knowledge_mode=knowledge_mode,
        knowledge_cutoff=knowledge_cutoff,
    )

    if missing and primary_retry_fetch is not None:
        values, attempt = _recovery_safe_fetch(
            primary_retry_fetch,
            stage="primary_retry",
            source=primary_source,
        )
        attempts.append(attempt)
        collected.extend(values)
        rows = canonical()
        missing = _recovery_gap_starts(
            rows,
            interval=interval,
            session_date=session_date,
            observed_at=observed_at,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )

    if missing and fallback_fetch is not None:
        values, attempt = _recovery_safe_fetch(
            fallback_fetch,
            stage="fallback",
            source=fallback_source or "fallback",
        )
        attempts.append(attempt)
        collected.extend(values)
        rows = canonical()
        missing = _recovery_gap_starts(
            rows,
            interval=interval,
            session_date=session_date,
            observed_at=observed_at,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )

    if missing and interval != "1m" and lower_resolution_fetch is not None:
        lower, attempt = _recovery_safe_fetch(
            lower_resolution_fetch,
            stage="lower_resolution_rebuild",
            source=lower_resolution_source or "lower_resolution",
        )
        rebuilt: list[MarketBar] = []
        if attempt.succeeded:
            try:
                rebuilt = aggregate_complete_bars(
                    lower,
                    session_date=session_date,
                    target_interval=interval,
                    as_of=observed_at,
                    knowledge_mode=knowledge_mode,
                    knowledge_cutoff=knowledge_cutoff,
                )
                attempt = attempt.model_copy(update={"bar_count": len(rebuilt)})
            except Exception as exc:
                attempt = attempt.model_copy(
                    update={
                        "succeeded": False,
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
        attempts.append(attempt)
        collected.extend(rebuilt)
        rows = canonical()
        missing = _recovery_gap_starts(
            rows,
            interval=interval,
            session_date=session_date,
            observed_at=observed_at,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )

    confirmed: list[datetime] = []
    unresolved: list[datetime] = []
    duration = interval_duration(interval)
    if missing and confirm_nontrading is not None:
        for start in missing:
            try:
                no_trade = bool(confirm_nontrading(start, start + duration))
            except Exception:
                no_trade = False
            if no_trade:
                confirmed.append(start)
            else:
                unresolved.append(start)
        attempts.append(
            RecoveryAttempt(
                stage="nontrading_confirmation",
                source="halt_or_no_trade_authority",
                succeeded=bool(confirmed),
                bar_count=len(confirmed),
                detail=None if confirmed else "no_missing_interval_confirmed",
            )
        )
    else:
        unresolved.extend(missing)

    status: RecoveryStatus
    if not rows:
        status = "UNAVAILABLE"
    elif unresolved:
        status = "PARTIAL"
    else:
        status = "COMPLETE"

    return RecoveredBarSeries(
        instrument_id=instrument_id,
        interval=interval,
        session_date=session_date,
        observed_at=observed_at,
        knowledge_mode=knowledge_mode,
        knowledge_cutoff=(
            _utc(knowledge_cutoff)
            if knowledge_cutoff is not None
            else observed_at
        ),
        bars=tuple(rows),
        status=status,
        unresolved_starts=tuple(unresolved),
        confirmed_nontrading_starts=tuple(confirmed),
        attempts=tuple(attempts),
        source_providers=tuple(sorted({bar.provider for bar in rows if bar.provider})),
    )


__all__ = [
    "BarGap",
    "CoverageSegment",
    "DataEvaluability",
    "KnowledgeMode",
    "RecoveredBars",
    "RecoveredBarSeries",
    "RecoveryAttempt",
    "RecoveryStatus",
    "RecoveryReport",
    "StrategyDataRequirement",
    "aggregate_complete_bars",
    "assess_data_requirement",
    "coverage_segments",
    "deduplicate_bars",
    "detect_session_gaps",
    "expected_bar_starts",
    "finalized_session_bars",
    "latest_clean_bars",
    "reconcile_recovery",
    "recover_market_bars",
]
