from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""Causal intraday evidence recovery and deterministic V2 replay.

The strategy monitor persists overlapping finalized one-minute bar windows on
state events. This module reconstructs that tape without using daily OHLC or
post-hoc chart interpretation, reports any gaps, and can replay the existing
versioned evaluator over prefixes to locate the first causal entry signal.
"""

from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from .gapper_dataset import GapperCandidate
from .models import MarketBar
from .strategies import evaluate_gap_pullback
from .strategies.models import GapPullbackConfig, GapPullbackResult
from .strategy_repository import StrategyEvent


class CausalBarArchive(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    bars: tuple[MarketBar, ...] = ()
    missing_intervals: tuple[str, ...] = ()
    complete: bool = False


class CausalV2Replay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    archive_complete: bool
    bar_count: int = Field(ge=0)
    missing_intervals: tuple[str, ...] = ()
    first_entry_bar_end: str | None = None
    result: GapPullbackResult | None = None


def recover_causal_1m_bars(
    events: Iterable[StrategyEvent],
    *,
    instrument_id: str,
) -> CausalBarArchive:
    by_start: dict[str, MarketBar] = {}
    for event in sorted(events, key=lambda item: (item.observed_at, item.event_id)):
        if event.instrument_id != instrument_id or event.event_type != "state":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        rows = payload.get("causal_1m_bar_window")
        if not isinstance(rows, list):
            fallback = payload.get("latest_execution_bar")
            rows = [fallback] if isinstance(fallback, dict) else []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            try:
                bar = MarketBar.model_validate(raw)
            except Exception:
                logger.debug("suppressed error in %s", "recover_causal_1m_bars", exc_info=True)
                continue
            if bar.interval != "1m" or not bar.is_final or bar.session != "regular":
                continue
            by_start[bar.start_time.isoformat()] = bar

    bars = tuple(sorted(by_start.values(), key=lambda item: item.start_time))
    missing: list[str] = []
    for left, right in zip(bars, bars[1:]):
        expected = left.end_time
        if right.start_time != expected:
            missing.append(f"{expected.isoformat()}..{right.start_time.isoformat()}")

    return CausalBarArchive(
        instrument_id=instrument_id,
        bars=bars,
        missing_intervals=tuple(missing),
        complete=bool(bars) and not missing,
    )


def replay_v2_from_causal_archive(
    candidate: GapperCandidate,
    config: GapPullbackConfig,
    events: Iterable[StrategyEvent],
) -> CausalV2Replay:
    archive = recover_causal_1m_bars(events, instrument_id=candidate.instrument_id)
    if not archive.bars:
        return CausalV2Replay(
            instrument_id=candidate.instrument_id,
            archive_complete=False,
            bar_count=0,
            missing_intervals=archive.missing_intervals,
        )

    last_result: GapPullbackResult | None = None
    first_entry_bar_end: str | None = None
    for index in range(1, len(archive.bars) + 1):
        last_result = evaluate_gap_pullback(candidate, list(archive.bars[:index]), config)
        if last_result.state == "entry_ready" and last_result.signal is not None:
            first_entry_bar_end = archive.bars[index - 1].end_time.isoformat()
            break

    return CausalV2Replay(
        instrument_id=candidate.instrument_id,
        archive_complete=archive.complete,
        bar_count=len(archive.bars),
        missing_intervals=archive.missing_intervals,
        first_entry_bar_end=first_entry_bar_end,
        result=last_result,
    )


__all__ = [
    "CausalBarArchive",
    "CausalV2Replay",
    "recover_causal_1m_bars",
    "replay_v2_from_causal_archive",
]
