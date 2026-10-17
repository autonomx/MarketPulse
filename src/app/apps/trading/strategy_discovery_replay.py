from __future__ import annotations

"""Replay the discovery layer itself, before strategy replay.

Every observation is processed only at its causal timestamp. The replay never
starts from a hindsight list of winners; callers provide the complete captured
observable population for the session.
"""

from datetime import date, datetime, timezone
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .strategy_dynamic_discovery import (
    DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
    DiscoveryEvent,
    DynamicCandidate,
    DynamicDiscoveryConfig,
    MarketAnomalyFeatures,
)


class DiscoveryReplayObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    observed_at: datetime
    source: str
    source_locator: str | None = None
    market: MarketAnomalyFeatures | None = None
    catalyst_payload: dict[str, Any] | None = None
    catalyst_known: bool = False

    @field_validator("observed_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("replay_observation_requires_timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.market is None and self.catalyst_payload is None:
            raise ValueError("replay_observation_requires_market_or_catalyst_evidence")
        if self.market is not None and self.market.observed_at > self.observed_at:
            raise ValueError("market_evidence_cannot_be_from_future")
        return self


class DiscoveryOpportunityLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    opportunity: bool
    first_actionable_at: datetime | None = None
    close_rank: int | None = Field(default=None, ge=1)

    @field_validator("first_actionable_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("opportunity_timestamp_requires_timezone")
        return value.astimezone(timezone.utc)


class DiscoveryReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    observation_count: int
    observable_symbol_count: int
    discovered_symbol_count: int
    first_discovered_at: dict[str, datetime]
    false_positive_symbols: tuple[str, ...] = ()
    missed_opportunity_symbols: tuple[str, ...] = ()
    discovery_recall: float | None = None
    discovery_precision: float | None = None
    median_discovery_latency_minutes: float | None = None
    fingerprint: str
    final_candidates: tuple[DynamicCandidate, ...]
    events: tuple[DiscoveryEvent, ...]


def replay_dynamic_discovery(
    *,
    session_date: date,
    observations: Sequence[DiscoveryReplayObservation],
    labels: Sequence[DiscoveryOpportunityLabel] = (),
    config: DynamicDiscoveryConfig = DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> DiscoveryReplayResult:
    """Replay discovery through the same causal scan kernel used by live discovery."""

    from .strategy_dynamic_discovery_quality import _replay_dynamic_discovery_refined

    return _replay_dynamic_discovery_refined(
        session_date=session_date,
        observations=observations,
        labels=labels,
        config=config,
    )


__all__ = [
    "DiscoveryOpportunityLabel",
    "DiscoveryReplayObservation",
    "DiscoveryReplayResult",
    "replay_dynamic_discovery",
]
