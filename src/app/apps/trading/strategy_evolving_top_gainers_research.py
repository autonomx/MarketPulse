from __future__ import annotations

"""Research-only integrity and trajectory helpers for evolving Top-Gainers replay.

This module deliberately sits beside ``strategy_evolving_top_gainers`` so the
frozen Leader Momentum v1.2 evaluator and the production discovery path remain
unchanged.  It adds two things the prospective replay needs:

1. a strict point-in-time population contract, so a historical leaderboard
   cannot contain symbols outside its declared session population; and
2. causal leaderboard trajectory features measured only through the signal
   timestamp.

End-of-day winner labels are intentionally absent from these contracts.
"""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .strategy_evolving_top_gainers import (
    EvolvingTopGainersConfig,
    EvolvingTopGainersReplay,
    TopGainerMember,
    TopGainerObservation,
    replay_evolving_top_gainers,
)


class HistoricalPopulationManifest(BaseModel):
    """Immutable authority for symbols eligible to participate in one session.

    ``point_in_time`` must be true for strict inference.  A present-day active
    asset catalog may still be useful for exploratory reconstruction, but it is
    not accepted as point-in-time historical evidence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    authority: str = Field(min_length=1, max_length=200)
    instrument_ids: tuple[str, ...] = ()
    point_in_time: bool
    outcome_conditioned: bool = False
    captured_as_of: datetime | None = None
    source_locator: str | None = Field(default=None, max_length=2_000)
    source_fingerprint: str | None = Field(default=None, max_length=128)
    execution_authority: Literal[False] = False

    @field_validator("captured_as_of")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("historical_population_timestamp_requires_timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_manifest(self):
        if not self.instrument_ids:
            raise ValueError("historical_population_requires_symbols")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("historical_population_contains_duplicates")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"source_fingerprint"})
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class PopulationIntegrityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    population_size: int = Field(ge=0)
    observation_symbol_count: int = Field(ge=0)
    leaderboard_symbol_count: int = Field(ge=0)
    observation_symbols_outside_population: tuple[str, ...] = ()
    leaderboard_symbols_outside_population: tuple[str, ...] = ()
    point_in_time: bool
    outcome_conditioned: bool
    valid_for_inference: bool
    reason_codes: tuple[str, ...] = ()
    population_fingerprint: str


def assess_population_integrity(
    *,
    manifest: HistoricalPopulationManifest,
    observations: Sequence[TopGainerObservation],
    replay: EvolvingTopGainersReplay | None = None,
) -> PopulationIntegrityReport:
    population = set(manifest.instrument_ids)
    observation_symbols = {row.instrument_id for row in observations}
    leaderboard_symbols = set(replay.union_instrument_ids) if replay is not None else set()

    observation_outside = tuple(sorted(observation_symbols - population))
    leaderboard_outside = tuple(sorted(leaderboard_symbols - population))
    reasons: list[str] = []
    if not manifest.point_in_time:
        reasons.append("population_not_point_in_time")
    if manifest.outcome_conditioned:
        reasons.append("population_outcome_conditioned")
    if observation_outside:
        reasons.append("observations_outside_population")
    if leaderboard_outside:
        reasons.append("leaderboard_members_outside_population")
    if replay is not None and replay.session_date != manifest.session_date:
        reasons.append("population_replay_session_mismatch")

    return PopulationIntegrityReport(
        session_date=manifest.session_date,
        population_size=len(population),
        observation_symbol_count=len(observation_symbols),
        leaderboard_symbol_count=len(leaderboard_symbols),
        observation_symbols_outside_population=observation_outside,
        leaderboard_symbols_outside_population=leaderboard_outside,
        point_in_time=manifest.point_in_time,
        outcome_conditioned=manifest.outcome_conditioned,
        valid_for_inference=not reasons,
        reason_codes=tuple(reasons),
        population_fingerprint=manifest.fingerprint,
    )


def replay_evolving_top_gainers_strict(
    *,
    manifest: HistoricalPopulationManifest,
    observations: Sequence[TopGainerObservation],
    config: EvolvingTopGainersConfig = EvolvingTopGainersConfig(),
) -> tuple[EvolvingTopGainersReplay, PopulationIntegrityReport]:
    """Replay only after the session population passes strict causal checks."""

    preflight = assess_population_integrity(
        manifest=manifest,
        observations=observations,
    )
    if not preflight.valid_for_inference:
        raise ValueError(
            "evolving_top_gainers_population_invalid:"
            + ",".join(preflight.reason_codes)
        )

    replay = replay_evolving_top_gainers(
        session_date=manifest.session_date,
        observations=observations,
        config=config,
    )
    report = assess_population_integrity(
        manifest=manifest,
        observations=observations,
        replay=replay,
    )
    if not report.valid_for_inference:
        raise RuntimeError(
            "evolving_top_gainers_population_invariant_failed:"
            + ",".join(report.reason_codes)
        )
    return replay, report


class LeaderboardTrajectoryFeatures(BaseModel):
    """Causal leaderboard state as known at one signal/evaluation timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    observed_at: datetime
    currently_top_n: bool
    current_rank: int | None = Field(default=None, ge=1)
    current_gain_pct: Decimal | None = None
    best_rank_so_far: int | None = Field(default=None, ge=1)
    best_gain_pct_so_far: Decimal | None = None
    rank_improvement_5m: int | None = None
    rank_improvement_15m: int | None = None
    gain_change_5m_pct_points: Decimal | None = None
    gain_change_15m_pct_points: Decimal | None = None
    minutes_since_first_top_n: Decimal | None = Field(default=None, ge=0)
    snapshots_in_top_10: int = Field(default=0, ge=0)
    consecutive_top_10_snapshots: int = Field(default=0, ge=0)
    top_10_minutes_observed: Decimal = Field(default=Decimal("0"), ge=0)
    ever_top_5_before_observation: bool = False

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("leaderboard_feature_timestamp_requires_timezone")
        return value.astimezone(timezone.utc)


def _member(snapshot_members: Sequence[TopGainerMember], instrument_id: str) -> TopGainerMember | None:
    return next((row for row in snapshot_members if row.instrument_id == instrument_id), None)


def _member_at_or_before(
    snapshots,
    *,
    instrument_id: str,
    observed_at: datetime,
    lookback_minutes: int,
) -> TopGainerMember | None:
    target = observed_at.timestamp() - lookback_minutes * 60
    eligible = [row for row in snapshots if row.observed_at.timestamp() <= target]
    if not eligible:
        return None
    return _member(eligible[-1].members, instrument_id)


def leaderboard_trajectory_at(
    replay: EvolvingTopGainersReplay,
    *,
    instrument_id: str,
    observed_at: datetime,
) -> LeaderboardTrajectoryFeatures:
    """Return ranking trajectory using snapshots no later than ``observed_at``."""

    if observed_at.tzinfo is None:
        raise ValueError("leaderboard_feature_timestamp_requires_timezone")
    observed = observed_at.astimezone(timezone.utc)
    snapshots = [row for row in replay.snapshots if row.observed_at <= observed]
    if not snapshots:
        return LeaderboardTrajectoryFeatures(
            instrument_id=instrument_id,
            observed_at=observed,
            currently_top_n=False,
        )

    current = _member(snapshots[-1].members, instrument_id)
    history = [
        (snapshot, _member(snapshot.members, instrument_id))
        for snapshot in snapshots
    ]
    present = [(snapshot, member) for snapshot, member in history if member is not None]
    membership = replay.membership_for(instrument_id)

    best_rank = min((member.rank for _, member in present), default=None)
    best_gain = max((member.gain_pct for _, member in present), default=None)
    prior_5 = _member_at_or_before(
        snapshots,
        instrument_id=instrument_id,
        observed_at=observed,
        lookback_minutes=5,
    )
    prior_15 = _member_at_or_before(
        snapshots,
        instrument_id=instrument_id,
        observed_at=observed,
        lookback_minutes=15,
    )

    def rank_improvement(prior: TopGainerMember | None) -> int | None:
        if current is None or prior is None:
            return None
        return prior.rank - current.rank

    def gain_change(prior: TopGainerMember | None) -> Decimal | None:
        if current is None or prior is None:
            return None
        return current.gain_pct - prior.gain_pct

    top10 = [member for _, member in history if member is not None and member.rank <= 10]
    consecutive_top10 = 0
    for _, member in reversed(history):
        if member is None or member.rank > 10:
            break
        consecutive_top10 += 1

    minutes_since_discovery = None
    if membership is not None and membership.first_top_n_at <= observed:
        minutes_since_discovery = Decimal(
            str((observed - membership.first_top_n_at).total_seconds() / 60.0)
        )

    return LeaderboardTrajectoryFeatures(
        instrument_id=instrument_id,
        observed_at=observed,
        currently_top_n=current is not None,
        current_rank=current.rank if current is not None else None,
        current_gain_pct=current.gain_pct if current is not None else None,
        best_rank_so_far=best_rank,
        best_gain_pct_so_far=best_gain,
        rank_improvement_5m=rank_improvement(prior_5),
        rank_improvement_15m=rank_improvement(prior_15),
        gain_change_5m_pct_points=gain_change(prior_5),
        gain_change_15m_pct_points=gain_change(prior_15),
        minutes_since_first_top_n=minutes_since_discovery,
        snapshots_in_top_10=len(top10),
        consecutive_top_10_snapshots=consecutive_top10,
        top_10_minutes_observed=Decimal(len(top10) * replay.config.cadence_minutes),
        ever_top_5_before_observation=any(member.rank <= 5 for _, member in present),
    )


__all__ = [
    "HistoricalPopulationManifest",
    "LeaderboardTrajectoryFeatures",
    "PopulationIntegrityReport",
    "assess_population_integrity",
    "leaderboard_trajectory_at",
    "replay_evolving_top_gainers_strict",
]
