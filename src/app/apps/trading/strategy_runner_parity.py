"""Parity evidence while the strategy runner shadows a configuration (strategy runner WP).

With ``execution_owner="runner_shadow"`` the monitor stays the owner and
records each proposal its pass hands to shadow observation or the entry path
as a ``monitor_parity_proposal`` event; the strategy runner runs the same pass
without any writes and records its proposals as ``runner_parity_proposal``
events. Both identify a proposal by its trade attempt (strategy, instrument and
signal bar), so ``runner_parity_report`` can tell whether the runner would have
proposed exactly the entries the monitor did. The events are evidence only.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from .strategy_monitor import _trade_attempt_id

if TYPE_CHECKING:
    from .strategy_monitor import StrategyRunHost, _EntryProposal
    from .strategy_repository import StrategyEvent, TradingStrategyConfigDocument, TradingStrategyRepository

ParitySource = Literal["monitor", "runner"]

MONITOR_PARITY_EVENT = "monitor_parity_proposal"
RUNNER_PARITY_EVENT = "runner_parity_proposal"
PARITY_EVENT_TYPES = (MONITOR_PARITY_EVENT, RUNNER_PARITY_EVENT)


def runner_shadowed(config: TradingStrategyConfigDocument) -> bool:
    return getattr(config.config, "execution_owner", "monitor") == "runner_shadow"


def runner_owned(config: TradingStrategyConfigDocument) -> bool:
    return getattr(config.config, "execution_owner", "monitor") == "runner"


async def record_parity_proposals(
    host: StrategyRunHost,
    config: TradingStrategyConfigDocument,
    strategy_repository: TradingStrategyRepository,
    proposals: Iterable[_EntryProposal],
    *,
    source: ParitySource,
) -> int:
    """Record one parity event per proposal; return how many were new."""
    recorded = 0
    for proposal in proposals:
        signal = proposal.result.signal
        recorded += await host._event(
            strategy_repository,
            config,
            instrument_id=proposal.candidate.instrument_id,
            event_type=MONITOR_PARITY_EVENT if source == "monitor" else RUNNER_PARITY_EVENT,
            state=proposal.result.state,
            reason_code="PARITY_PROPOSAL",
            observed_at=proposal.observed_at,
            payload={
                "source": source,
                "trade_attempt_id": _trade_attempt_id(
                    config.strategy_id, proposal.candidate.instrument_id, proposal.observed_at
                ),
                "mode": config.mode,
                "strategy_version": config.config.strategy_version,
                "signal": signal.model_dump(mode="json") if signal is not None else None,
                "execution_authority": False,
            },
        )
    return recorded


def parity_report(events: Iterable[StrategyEvent]) -> dict[str, Any]:
    """Compare the monitor's and the runner's proposals by trade attempt."""
    sides: dict[str, dict[str, Any]] = {MONITOR_PARITY_EVENT: {}, RUNNER_PARITY_EVENT: {}}
    for event in events:
        if event.event_type in sides:
            sides[event.event_type][str(event.payload.get("trade_attempt_id"))] = event.payload.get("signal")
    monitor, runner = sides[MONITOR_PARITY_EVENT], sides[RUNNER_PARITY_EVENT]
    matched = sorted(set(monitor) & set(runner))
    signal_mismatch = [attempt for attempt in matched if monitor[attempt] != runner[attempt]]
    monitor_only = sorted(set(monitor) - set(runner))
    runner_only = sorted(set(runner) - set(monitor))
    return {
        "monitor_proposals": len(monitor),
        "runner_proposals": len(runner),
        "matched": len(matched) - len(signal_mismatch),
        "signal_mismatch": signal_mismatch,
        "monitor_only": monitor_only,
        "runner_only": runner_only,
        "parity": not (signal_mismatch or monitor_only or runner_only),
    }


async def runner_parity_report(
    strategy_repository: TradingStrategyRepository, strategy_id: str, *, start: datetime, end: datetime
) -> dict[str, Any]:
    events = await asyncio.to_thread(
        strategy_repository.events_by_types_between,
        strategy_id,
        event_types=PARITY_EVENT_TYPES,
        start_time=start,
        end_time=end,
    )
    return {"strategy_id": strategy_id, **parity_report(events)}
