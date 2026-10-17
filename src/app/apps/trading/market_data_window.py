from __future__ import annotations

"""Session-window recovery for causal market-data consumers.

The existing shared recovery service is regular-session oriented. This module
adds an explicit bounded-window contract so premarket research can recover
04:00-09:29 ET evidence without pretending that regular-session gap semantics
apply to extended hours.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .market_data_recovery import KnowledgeMode, deduplicate_bars
from .models import MarketBar
from .providers.bar_semantics import interval_duration


WindowSession = Literal["extended_pre", "regular", "extended_post", "custom"]
WindowContinuity = Literal["bucket_complete", "sparse_event"]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("window timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class MarketDataWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime
    interval: str = "1m"
    session: WindowSession = "custom"
    include_extended_hours: bool = False
    continuity: WindowContinuity = "bucket_complete"

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("market_data_window_end_must_follow_start")
        duration = interval_duration(self.interval)
        if duration <= timedelta(0) or duration >= timedelta(days=1):
            raise ValueError("market_data_window_requires_intraday_interval")
        return self


class WindowGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime
    missing_bar_count: int = Field(ge=1)

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


class WindowRecoveryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    instrument_id: str
    window: MarketDataWindow
    knowledge_mode: KnowledgeMode
    knowledge_cutoff: datetime
    durable_bar_count: int = Field(ge=0)
    fetched_bar_count: int = Field(ge=0)
    canonical_bar_count: int = Field(ge=0)
    expected_bar_count: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    latest_bar_lag_seconds: int | None = Field(default=None, ge=0)
    late_window_bar_count: int = Field(default=0, ge=0)
    repair_attempted: bool = False
    persisted_repair_bar_count: int = Field(default=0, ge=0)
    unresolved_gaps: tuple[WindowGap, ...] = ()
    provider_error: str | None = None
    no_synthetic_prices: Literal[True] = True
    dataset_fingerprint: str

    @field_validator("knowledge_cutoff")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


@dataclass(frozen=True)
class RecoveredWindowBars:
    bars: tuple[MarketBar, ...]
    report: WindowRecoveryReport
    primary_response: Any | None = None


def expected_window_starts(window: MarketDataWindow) -> tuple[datetime, ...]:
    duration = interval_duration(window.interval)
    cursor = window.start
    output: list[datetime] = []
    while cursor + duration <= window.end:
        output.append(cursor)
        cursor += duration
    return tuple(output)


def finalized_window_bars(
    bars: Sequence[MarketBar],
    *,
    window: MarketDataWindow,
    knowledge_mode: KnowledgeMode,
    knowledge_cutoff: datetime,
) -> list[MarketBar]:
    known_by = _utc(knowledge_cutoff)
    selected: list[MarketBar] = []
    for bar in bars:
        if not bar.is_final or bar.interval != window.interval:
            continue
        if not window.start <= bar.start_time < window.end:
            continue
        if bar.end_time > window.end:
            continue
        if window.session != "custom" and bar.session != window.session:
            continue
        if knowledge_mode in {"live", "causal_replay"} and bar.received_at > known_by:
            continue
        selected.append(bar)
    return deduplicate_bars(selected)


def detect_window_gaps(
    bars: Sequence[MarketBar],
    *,
    window: MarketDataWindow,
    knowledge_mode: KnowledgeMode,
    knowledge_cutoff: datetime,
) -> tuple[WindowGap, ...]:
    # Extended-hours equity bars are sparse trade/event buckets. A missing minute
    # is not evidence of provider loss, so continuity cannot be inferred by
    # manufacturing an expected bar for every wall-clock minute.
    if window.continuity == "sparse_event":
        return ()
    expected = expected_window_starts(window)
    if not expected:
        return ()
    duration = interval_duration(window.interval)
    actual = {
        bar.start_time
        for bar in finalized_window_bars(
            bars,
            window=window,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=knowledge_cutoff,
        )
    }
    missing = [start for start in expected if start not in actual]
    if not missing:
        return ()
    gaps: list[WindowGap] = []
    gap_start = previous = missing[0]
    count = 1
    for current in missing[1:]:
        if current == previous + duration:
            previous = current
            count += 1
            continue
        gaps.append(
            WindowGap(
                start=gap_start,
                end=previous + duration,
                missing_bar_count=count,
            )
        )
        gap_start = previous = current
        count = 1
    gaps.append(
        WindowGap(
            start=gap_start,
            end=previous + duration,
            missing_bar_count=count,
        )
    )
    return tuple(gaps)


def window_dataset_fingerprint(
    bars: Sequence[MarketBar],
    *,
    window: MarketDataWindow,
) -> str:
    payload = {
        "window": window.model_dump(mode="json"),
        "bars": [
            (
                bar.provider,
                bar.provider_event_id,
                bar.provider_sequence,
                bar.ingestion_revision,
                bar.start_time.isoformat(),
                bar.end_time.isoformat(),
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
                bar.received_at.isoformat(),
            )
            for bar in bars
        ],
    }
    return _hash(payload)


__all__ = [
    "MarketDataWindow",
    "RecoveredWindowBars",
    "WindowContinuity",
    "WindowGap",
    "WindowRecoveryReport",
    "WindowSession",
    "detect_window_gaps",
    "expected_window_starts",
    "finalized_window_bars",
    "window_dataset_fingerprint",
]
