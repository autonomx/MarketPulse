from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .market_evidence import PremarketLiquidityEvidence, SourceMemberDisposition
from collections.abc import Sequence


class GapperCandidate(BaseModel):
    """Point-in-time candidate evidence used by research/backtests and paper runs.

    `previous_close` is always the split/corporate-action-normalized close in the
    same share basis as `premarket_price`. When raw evidence is available it is
    preserved separately with the adjustment factor so the gap can be audited.

    `observed_at` records when the candidate snapshot itself was observable.
    `evidence_observed_at` can additionally timestamp fields sourced from separate
    requests. Freeze validation rejects any timestamp later than the universe
    evaluation time so historical universes cannot silently absorb future facts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str = Field(min_length=3, max_length=200)
    binding_id: str | None = Field(default=None, max_length=240)
    observed_at: datetime | None = None
    evidence_observed_at: dict[str, datetime] = Field(default_factory=dict)
    previous_close: Decimal = Field(gt=0)
    raw_previous_close: Decimal | None = Field(default=None, gt=0)
    split_adjustment_factor: Decimal = Field(default=Decimal("1"), gt=0)
    corporate_action_evidence_ids: tuple[str, ...] = ()
    premarket_price: Decimal = Field(gt=0)
    gap_pct: Decimal
    premarket_volume: Decimal = Field(default=Decimal("0"), ge=0)
    premarket_dollar_volume: Decimal = Field(default=Decimal("0"), ge=0)
    premarket_bar_count: int | None = Field(default=None, ge=0)
    tod_rvol: Decimal | None = Field(default=None, ge=0)
    premarket_liquidity: PremarketLiquidityEvidence | None = None
    market_evidence_policy_version: str | None = Field(default=None, max_length=120)
    market_data_complete: bool = True
    data_quality_flags: tuple[str, ...] = ()
    research_quality_flags: tuple[str, ...] = ()
    market_cap: Decimal | None = Field(default=None, ge=0)
    float_shares: Decimal | None = Field(default=None, gt=0)
    spread_bps: Decimal | None = Field(default=None, ge=0)
    catalyst_evidence_ids: tuple[str, ...] = ()
    dilution_flags: tuple[str, ...] = ()
    discovery_rank: int | None = Field(default=None, ge=1)

    @field_validator("observed_at")
    @classmethod
    def observed_at_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("candidate observed_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("evidence_observed_at")
    @classmethod
    def evidence_times_aware(cls, value: dict[str, datetime]) -> dict[str, datetime]:
        normalized: dict[str, datetime] = {}
        for key, observed_at in value.items():
            clean = str(key).strip()
            if not clean:
                raise ValueError("evidence_observed_at keys cannot be empty")
            if observed_at.tzinfo is None:
                raise ValueError("candidate evidence timestamps must be timezone-aware")
            normalized[clean] = observed_at.astimezone(timezone.utc)
        return normalized

    @model_validator(mode="after")
    def validate_gap_and_adjustment(self):
        if self.raw_previous_close is not None:
            adjusted = self.raw_previous_close * self.split_adjustment_factor
            tolerance = max(Decimal("0.0001"), self.previous_close * Decimal("0.0001"))
            if abs(adjusted - self.previous_close) > tolerance:
                raise ValueError(
                    "previous_close must equal raw_previous_close * split_adjustment_factor"
                )
            if self.split_adjustment_factor != Decimal("1") and not self.corporate_action_evidence_ids:
                raise ValueError("non-unit split adjustment requires corporate_action_evidence_ids")
        elif self.split_adjustment_factor != Decimal("1"):
            raise ValueError("non-unit split adjustment requires raw_previous_close")
        implied = (self.premarket_price / self.previous_close - Decimal("1")) * Decimal("100")
        if abs(implied - self.gap_pct) > Decimal("0.25"):
            raise ValueError("gap_pct does not match normalized previous_close/premarket_price")
        if self.market_data_complete and self.data_quality_flags:
            raise ValueError("market_data_complete cannot be true when data_quality_flags are present")
        if self.premarket_liquidity is not None:
            if self.premarket_volume != self.premarket_liquidity.current_premarket_volume:
                raise ValueError("premarket_volume must match premarket_liquidity evidence")
            if self.premarket_dollar_volume != self.premarket_liquidity.current_premarket_dollar_volume:
                raise ValueError("premarket_dollar_volume must match premarket_liquidity evidence")
            if self.premarket_bar_count != self.premarket_liquidity.premarket_bar_count:
                raise ValueError("premarket_bar_count must match premarket_liquidity evidence")
            if self.tod_rvol != self.premarket_liquidity.tod_rvol:
                raise ValueError("tod_rvol must match premarket_liquidity evidence")
            if self.market_evidence_policy_version != self.premarket_liquidity.policy_version:
                raise ValueError("market evidence policy must match premarket liquidity policy")
        return self


class GapperUniverseSnapshot(BaseModel):
    """Immutable daily candidate universe, including eventual failures/fades.

    An empty candidate tuple is valid only when produced by a trusted archival
    path that explicitly records a completed morning scan with zero qualifying
    names. Interactive/manual freeze endpoints keep requiring at least one name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    universe_id: str = Field(min_length=1, max_length=200)
    session_date: date
    evaluation_time: datetime
    discovery_source: Literal["manual", "import", "scanner", "provider", "finviz"]
    source_locator: str | None = Field(default=None, max_length=2000)
    source_candidate_symbols: tuple[str, ...] = ()
    source_member_dispositions: tuple[SourceMemberDisposition, ...] = ()
    candidates: tuple[GapperCandidate, ...]
    source_fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("evaluation_time")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def source_dispositions_match_source(self):
        if not self.source_member_dispositions:
            return self
        symbols = [item.symbol for item in self.source_member_dispositions]
        ranks = [item.source_rank for item in self.source_member_dispositions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("source member dispositions must have unique symbols")
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("source member disposition ranks must be contiguous from 1")
        if self.source_candidate_symbols and tuple(symbols) != self.source_candidate_symbols:
            raise ValueError("source member dispositions must match source_candidate_symbols order")
        materialized = {
            item.instrument_id
            for item in self.source_member_dispositions
            if item.status == "materialized"
        }
        candidate_ids = {candidate.instrument_id for candidate in self.candidates}
        if materialized != candidate_ids:
            raise ValueError("materialized source members must match frozen candidates")
        return self


def time_of_day_relative_volume(
    current_cumulative_volume: Decimal | int | str,
    historical_cumulative_volumes: Sequence[Decimal | int | str],
    *,
    minimum_baseline_sessions: int = 1,
) -> Decimal | None:
    """Current cumulative volume / historical mean at the same clock minute."""
    current = Decimal(str(current_cumulative_volume))
    samples = [Decimal(str(value)) for value in historical_cumulative_volumes]
    samples = [value for value in samples if value >= 0]
    if current < 0:
        raise ValueError("current cumulative volume cannot be negative")
    if minimum_baseline_sessions < 1:
        raise ValueError("minimum_baseline_sessions must be positive")
    if len(samples) < minimum_baseline_sessions:
        return None
    baseline = sum(samples, Decimal("0")) / Decimal(len(samples))
    if baseline <= 0:
        return None
    return current / baseline


def _validate_point_in_time_candidate(
    candidate: GapperCandidate,
    *,
    evaluation_time: datetime,
    discovery_source: str,
) -> None:
    evaluation = evaluation_time.astimezone(timezone.utc)
    if candidate.observed_at is not None and candidate.observed_at > evaluation:
        raise ValueError(f"candidate observation occurs after universe freeze: {candidate.instrument_id}")
    if discovery_source in {"scanner", "provider", "finviz"} and candidate.observed_at is None:
        raise ValueError(
            f"provider/scanner candidate requires observed_at: {candidate.instrument_id}"
        )
    for field, observed_at in candidate.evidence_observed_at.items():
        if observed_at > evaluation:
            raise ValueError(
                f"candidate evidence occurs after universe freeze: {candidate.instrument_id}:{field}"
            )
    if (
        candidate.premarket_liquidity is not None
        and candidate.premarket_liquidity.observed_at.astimezone(timezone.utc) > evaluation
    ):
        raise ValueError(
            f"candidate premarket liquidity occurs after universe freeze: {candidate.instrument_id}"
        )


def _candidate_fingerprint_payload(candidate: GapperCandidate) -> dict[str, Any]:
    """Preserve legacy fingerprints while binding newly observed integrity evidence."""

    payload = candidate.model_dump(
        mode="json",
        exclude={
            "premarket_bar_count",
            "market_data_complete",
            "data_quality_flags",
            "research_quality_flags",
            "premarket_liquidity",
            "market_evidence_policy_version",
        },
    )
    if (
        candidate.premarket_bar_count is not None
        or not candidate.market_data_complete
        or candidate.data_quality_flags
    ):
        payload["premarket_bar_count"] = candidate.premarket_bar_count
        payload["market_data_complete"] = candidate.market_data_complete
        payload["data_quality_flags"] = list(candidate.data_quality_flags)
    if candidate.research_quality_flags:
        payload["research_quality_flags"] = list(candidate.research_quality_flags)
    if candidate.premarket_liquidity is not None:
        payload["premarket_liquidity"] = candidate.premarket_liquidity.model_dump(mode="json")
    if candidate.market_evidence_policy_version is not None:
        payload["market_evidence_policy_version"] = candidate.market_evidence_policy_version
    return payload


def gapper_universe_fingerprint(
    *,
    universe_id: str,
    session_date: date,
    evaluation_time: datetime,
    discovery_source: str,
    candidates: tuple[GapperCandidate, ...] | list[GapperCandidate],
    source_locator: str | None = None,
    source_candidate_symbols: tuple[str, ...] | list[str] = (),
    source_member_dispositions: tuple[SourceMemberDisposition, ...] | list[SourceMemberDisposition] = (),
) -> str:
    ordered = sorted(candidates, key=lambda item: (item.discovery_rank or 10**9, item.instrument_id))
    payload: dict[str, Any] = {
        "universe_id": universe_id,
        "session_date": session_date.isoformat(),
        "evaluation_time": evaluation_time.astimezone(timezone.utc).isoformat(),
        "discovery_source": discovery_source,
        "candidates": [_candidate_fingerprint_payload(candidate) for candidate in ordered],
    }
    # Keep fingerprints for pre-0049 universes stable. Discovery provenance only
    # participates in the fingerprint when it was actually captured.
    if source_locator is not None or source_candidate_symbols:
        payload["source_locator"] = source_locator
        payload["source_candidate_symbols"] = list(source_candidate_symbols)
    if source_member_dispositions:
        payload["source_member_dispositions"] = [
            item.model_dump(mode="json") for item in source_member_dispositions
        ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def freeze_gapper_universe(
    *,
    universe_id: str,
    session_date: date,
    evaluation_time: datetime,
    discovery_source: Literal["manual", "import", "scanner", "provider", "finviz"],
    candidates: list[GapperCandidate] | tuple[GapperCandidate, ...],
    source_locator: str | None = None,
    source_candidate_symbols: list[str] | tuple[str, ...] = (),
    source_member_dispositions: list[SourceMemberDisposition] | tuple[SourceMemberDisposition, ...] = (),
    allow_empty: bool = False,
) -> GapperUniverseSnapshot:
    if not candidates and not allow_empty:
        raise ValueError("gapper universe requires at least one candidate")
    if evaluation_time.tzinfo is None:
        raise ValueError("evaluation_time must be timezone-aware")
    for candidate in candidates:
        _validate_point_in_time_candidate(
            candidate,
            evaluation_time=evaluation_time,
            discovery_source=discovery_source,
        )
    ordered = tuple(sorted(candidates, key=lambda item: (item.discovery_rank or 10**9, item.instrument_id)))
    dispositions = tuple(source_member_dispositions)
    fingerprint = gapper_universe_fingerprint(
        universe_id=universe_id,
        session_date=session_date,
        evaluation_time=evaluation_time,
        discovery_source=discovery_source,
        candidates=ordered,
        source_locator=source_locator,
        source_candidate_symbols=source_candidate_symbols,
        source_member_dispositions=dispositions,
    )
    return GapperUniverseSnapshot(
        universe_id=universe_id,
        session_date=session_date,
        evaluation_time=evaluation_time,
        discovery_source=discovery_source,
        source_locator=source_locator,
        source_candidate_symbols=tuple(source_candidate_symbols),
        source_member_dispositions=dispositions,
        candidates=ordered,
        source_fingerprint=fingerprint,
    )
