from __future__ import annotations

"""Build causal non-winner control universes for Leader Momentum research.

The winner benchmark answers whether a strategy can capture known winners.  It
cannot estimate false positives.  This module derives a separate control plan
from the same historical scanner/discovery observations *before* outcome labels
are used, then removes known winner IDs only after the causal universe exists.

It intentionally does not fetch market data or run the strategy.  Replay drivers
can use the returned specs to request the same Alpaca SIP bars/context and then
feed the resulting traces into ``build_leader_momentum_cohort_report``.
"""

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .strategy_discovery_replay import DiscoveryReplayObservation, DiscoveryReplayResult
from .strategy_leader_momentum_diagnostics import (
    LeaderMomentumCohortObservation,
    LeaderMomentumDiagnosticTrace,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET

ControlUniverseScope = Literal["observable_scanner", "discovered_candidates"]


class LeaderMomentumControlSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    instrument_id: str
    first_observed_at: datetime
    last_observed_at: datetime
    observation_count: int = Field(ge=1)
    sources: tuple[str, ...] = ()
    discovered_at: datetime | None = None
    discovered: bool = False
    final_tier: str | None = None
    final_common_priority: float | None = None


class LeaderMomentumControlPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    scope: ControlUniverseScope
    observable_symbol_count: int = Field(ge=0)
    discovered_symbol_count: int = Field(ge=0)
    winner_exclusion_count: int = Field(ge=0)
    control_count: int = Field(ge=0)
    controls: tuple[LeaderMomentumControlSpec, ...] = ()
    fingerprint: str
    winner_exclusion_applied_after_causal_universe: Literal[True] = True
    execution_authority: Literal[False] = False


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("control_replay_timestamp_requires_timezone")
    return value.astimezone(timezone.utc)


def _fingerprint(
    *,
    session_date: date,
    scope: ControlUniverseScope,
    causal_symbols: Sequence[str],
    winners: Sequence[str],
    specs: Sequence[LeaderMomentumControlSpec],
) -> str:
    payload = {
        "session_date": session_date.isoformat(),
        "scope": scope,
        "causal_symbols": sorted(causal_symbols),
        "winner_exclusions": sorted(winners),
        "controls": [item.model_dump(mode="json") for item in specs],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_leader_momentum_control_plan(
    *,
    session_date: date,
    observations: Sequence[DiscoveryReplayObservation],
    winner_instrument_ids: Sequence[str],
    discovery_result: DiscoveryReplayResult | None = None,
    scope: ControlUniverseScope = "observable_scanner",
) -> LeaderMomentumControlPlan:
    """Freeze a causal control universe, then exclude outcome-labeled winners.

    ``observable_scanner`` uses every symbol present in the historical causal
    scanner observation stream, including names that never passed dynamic
    discovery. ``discovered_candidates`` restricts the universe to symbols that
    actually entered the discovery state and therefore requires the matching
    ``DiscoveryReplayResult``.
    """

    if scope == "discovered_candidates" and discovery_result is None:
        raise ValueError("discovered_control_scope_requires_discovery_result")
    if discovery_result is not None and discovery_result.session_date != session_date:
        raise ValueError("control_replay_discovery_session_mismatch")

    grouped: dict[str, list[DiscoveryReplayObservation]] = defaultdict(list)
    for row in observations:
        observed_at = _utc(row.observed_at)
        if observed_at.astimezone(_ET).date() != session_date:
            raise ValueError("control_replay_observation_outside_exchange_session")
        grouped[row.instrument_id].append(row)

    observable_symbols = set(grouped)
    discovered_at = (
        dict(discovery_result.first_discovered_at)
        if discovery_result is not None
        else {}
    )
    discovered_symbols = set(discovered_at)

    if scope == "observable_scanner":
        causal_symbols = set(observable_symbols)
    else:
        # Require every discovered control to be traceable to the supplied
        # scanner observation population rather than admitting an orphaned
        # symbol from a mismatched replay result.
        orphaned = discovered_symbols - observable_symbols
        if orphaned:
            raise ValueError("control_replay_discovered_symbol_missing_observation")
        causal_symbols = set(discovered_symbols)

    winners = {str(value) for value in winner_instrument_ids}
    control_symbols = causal_symbols - winners
    final_by_symbol = {
        item.instrument_id: item
        for item in (discovery_result.final_candidates if discovery_result else ())
    }

    specs: list[LeaderMomentumControlSpec] = []
    for instrument_id in sorted(control_symbols):
        rows = sorted(
            grouped[instrument_id],
            key=lambda item: (item.observed_at, item.source),
        )
        if not rows:
            continue
        final = final_by_symbol.get(instrument_id)
        specs.append(
            LeaderMomentumControlSpec(
                session_date=session_date,
                instrument_id=instrument_id,
                first_observed_at=_utc(rows[0].observed_at),
                last_observed_at=_utc(rows[-1].observed_at),
                observation_count=len(rows),
                sources=tuple(sorted({row.source for row in rows})),
                discovered_at=discovered_at.get(instrument_id),
                discovered=instrument_id in discovered_symbols,
                final_tier=(
                    str(final.tier.value if hasattr(final.tier, "value") else final.tier)
                    if final is not None
                    else None
                ),
                final_common_priority=(final.common_priority if final is not None else None),
            )
        )

    ordered_specs = tuple(
        sorted(specs, key=lambda item: (item.first_observed_at, item.instrument_id))
    )
    return LeaderMomentumControlPlan(
        session_date=session_date,
        scope=scope,
        observable_symbol_count=len(observable_symbols),
        discovered_symbol_count=len(discovered_symbols),
        winner_exclusion_count=len(causal_symbols & winners),
        control_count=len(ordered_specs),
        controls=ordered_specs,
        fingerprint=_fingerprint(
            session_date=session_date,
            scope=scope,
            causal_symbols=tuple(causal_symbols),
            winners=tuple(winners),
            specs=ordered_specs,
        ),
    )


def build_control_cohort_observations(
    plan: LeaderMomentumControlPlan,
    traces_by_instrument: Mapping[str, LeaderMomentumDiagnosticTrace],
    *,
    require_complete: bool = True,
) -> tuple[LeaderMomentumCohortObservation, ...]:
    """Attach already-computed traces to a frozen control plan.

    The outcome label is added here, after strategy diagnostics exist.  This
    function never changes or recomputes a strategy trace.
    """

    required = {item.instrument_id for item in plan.controls}
    available = set(traces_by_instrument)
    missing = required - available
    if missing and require_complete:
        raise ValueError(
            "control_replay_missing_traces:" + ",".join(sorted(missing))
        )

    return tuple(
        LeaderMomentumCohortObservation(
            cohort="control",
            instrument_id=item.instrument_id,
            trace=traces_by_instrument[item.instrument_id],
        )
        for item in plan.controls
        if item.instrument_id in traces_by_instrument
    )


__all__ = [
    "ControlUniverseScope",
    "LeaderMomentumControlPlan",
    "LeaderMomentumControlSpec",
    "build_control_cohort_observations",
    "build_leader_momentum_control_plan",
]
