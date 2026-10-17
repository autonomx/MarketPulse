from __future__ import annotations

"""Versioned evidence contracts for prospective equity trading.

These models deliberately separate source membership, frozen morning evidence,
provider availability and execution authorization. Missing inputs are represented
as typed gaps; they are never converted into synthetic executable observations.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MARKET_EVIDENCE_POLICY_VERSION = "market-evidence-v3-finviz-membership"
YAHOO_HARDENED_EVIDENCE_POLICY_VERSION = "market-evidence-yahoo-relative-v1"
YAHOO_RELATIVE_VOLUME = "YAHOO_RELATIVE_VOLUME"
MIN_TOD_RVOL_BASELINE_SESSIONS = 5


class MarketEvidencePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = MARKET_EVIDENCE_POLICY_VERSION
    discovery_source: Literal["finviz"] = "finviz"
    premarket_liquidity_provider: str = "alpaca_iex"
    premarket_feed: str = "iex"
    historical_canonical_provider: str = "yahoo"
    live_quote_primary_provider: str = "ibkr"
    live_quote_fallback_provider: str = "alpaca_iex"
    gap_repair_fallback_provider: str = "alpaca_iex"
    paper_fill_observation_provider: str = "alpaca_iex"
    order_execution_provider: None = None
    minimum_tod_rvol_baseline_sessions: int = Field(
        default=MIN_TOD_RVOL_BASELINE_SESSIONS,
        ge=2,
    )
    frozen_spread_is_authoritative: Literal[False] = False
    live_entry_spread_is_authoritative: Literal[True] = True

    # Compatibility aliases for older strategy code. These names are no longer
    # serialized as policy authority because they collapsed independent roles.
    @property
    def premarket_provider(self) -> str:
        return self.premarket_liquidity_provider

    @property
    def regular_bar_primary_provider(self) -> str:
        return self.historical_canonical_provider

    @property
    def shadow_bar_fallback_provider(self) -> str:
        return self.gap_repair_fallback_provider

    @property
    def execution_provider(self) -> str:
        return self.paper_fill_observation_provider


DEFAULT_MARKET_EVIDENCE_POLICY = MarketEvidencePolicy()


SourceMemberStatus = Literal[
    "materialized",
    "filtered_gap",
    "filtered_price",
    "unsupported_instrument",
    "enrichment_failed",
    "provider_unavailable",
]


class SourceMemberDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1, max_length=32)
    source_rank: int = Field(ge=1)
    status: SourceMemberStatus
    reason_codes: tuple[str, ...] = ()
    instrument_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def materialized_has_instrument(self):
        if self.status == "materialized" and not self.instrument_id:
            raise ValueError("materialized source member requires instrument_id")
        return self


class PremarketLiquidityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = MARKET_EVIDENCE_POLICY_VERSION
    provider: str
    feed: str
    observed_at: datetime
    current_premarket_volume: Decimal = Field(ge=0)
    current_premarket_dollar_volume: Decimal = Field(ge=0)
    tod_rvol: Decimal | None = Field(default=None, ge=0)
    tod_rvol_numerator: Decimal = Field(ge=0)
    tod_rvol_denominator_mean: Decimal | None = Field(default=None, ge=0)
    baseline_session_count: int = Field(ge=0)
    premarket_bar_count: int = Field(ge=0)
    nonzero_volume_bar_count: int = Field(ge=0)
    coverage_ratio: Decimal | None = Field(default=None, ge=0)
    ready: bool
    reason_codes: tuple[str, ...] = ()
    volume_authority: Literal["provider_relative", "consolidated", "unknown"] = "provider_relative"
    volume_basis: str | None = Field(default=None, max_length=120)
    consolidated_volume_authority: Literal[False] = False

    @model_validator(mode="after")
    def readiness_consistent(self):
        if self.ready and self.reason_codes:
            raise ValueError("ready premarket evidence cannot have reason_codes")
        if self.ready and self.tod_rvol is None:
            raise ValueError("ready premarket evidence requires tod_rvol")
        return self


def premarket_evidence_feature_compatible(
    evidence: PremarketLiquidityEvidence | None,
) -> bool:
    """Whether evidence may authorize its own provider-relative features.

    This deliberately does *not* grant consolidated-volume, live-quote, or
    brokerage execution authority. The existing Alpaca/IEX policy and the hardened Yahoo policy
    are both same-feed relative-volume contracts; callers that need SIP must
    request a separate consolidated-volume requirement.
    """

    if evidence is None or not evidence.ready:
        return False
    if evidence.policy_version == MARKET_EVIDENCE_POLICY_VERSION:
        return True
    return (
        evidence.policy_version == YAHOO_HARDENED_EVIDENCE_POLICY_VERSION
        and evidence.provider == "yahoo"
        and evidence.feed == "extended_hours"
        and evidence.volume_authority == "provider_relative"
        and evidence.consolidated_volume_authority is False
    )


def evidence_authorizes_feature(
    evidence: PremarketLiquidityEvidence | None,
    feature: Literal[
        "price_ohlc",
        "provider_relative_volume",
        "YAHOO_RELATIVE_VOLUME",
        "consolidated_volume",
        "live_bid_ask",
        "execution_fill",
    ],
) -> bool:
    """Feature-local authority contract for premarket evidence."""

    if evidence is None:
        return False
    if feature == "price_ohlc":
        return evidence.provider in {"yahoo", "alpaca_iex"} and evidence.premarket_bar_count > 0
    if feature == "provider_relative_volume":
        return premarket_evidence_feature_compatible(evidence)
    if feature == YAHOO_RELATIVE_VOLUME:
        return (
            evidence.provider == "yahoo"
            and evidence.volume_basis == YAHOO_RELATIVE_VOLUME
            and premarket_evidence_feature_compatible(evidence)
        )
    # Neither Yahoo nor IEX premarket evidence is consolidated SIP, generic
    # live-quote authority, or brokerage execution authority.
    return False


ProviderReadinessState = Literal[
    "READY",
    "NOT_CONFIGURED",
    "AUTH_FAILED",
    "RATE_LIMITED",
    "QUOTE_MISSING",
    "CONTRACT_ERROR",
    "STALE",
    "PROVIDER_UNAVAILABLE",
    "UNKNOWN_ERROR",
]


class ProviderReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    state: ProviderReadinessState
    ready: bool
    observed_at: datetime
    error_type: str | None = None
    detail: str | None = None


class ExecutionInputGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    binding_id: str | None = None
    provider: str
    readiness: ProviderReadiness
    reason_code: str
    observed_at: datetime
    execution_authority: Literal[False] = False


class BarCoverageAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str | None = None
    provider: str | None = None
    fallback_provider: str | None = None
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    expected_latest_bar: datetime | None = None
    actual_latest_bar: datetime | None = None
    missing_minutes: tuple[datetime, ...] = ()
    latency_seconds: Decimal | None = Field(default=None, ge=0)
    opening_bar_present: bool = False
    latest_bar_present: bool = False
    contiguous: bool = False
    ready: bool = False
    reason_codes: tuple[str, ...] = ()


SessionEvaluabilityStatus = Literal[
    "complete",
    "completed_no_trigger",
    "zero_candidate_scan",
    "partial_data",
    "not_evaluable_data",
    "provider_unavailable",
]


class SessionEvaluabilityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SessionEvaluabilityStatus
    source_member_count: int = Field(ge=0)
    accounted_source_member_count: int = Field(ge=0)
    materialized_candidate_count: int = Field(ge=0)
    evaluable_candidate_count: int = Field(ge=0)
    unevaluable_candidate_count: int = Field(ge=0)
    source_failure_count: int = Field(ge=0)
    reason_codes: tuple[str, ...] = ()
    qualification_eligible: bool = False


class TradeAuthorizationAssessment(BaseModel):
    """Final fail-closed gate immediately in front of a strategy entry order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["trade-authorization-v1"] = "trade-authorization-v1"
    strategy_id: str
    instrument_id: str
    trade_attempt_id: str
    universe_id: str
    strategy_profile_fingerprint: str
    market_evidence_policy_version: str
    source_member_valid: bool
    morning_evidence_eligible: bool
    session_evaluability_complete: bool
    bar_coverage_ready: bool
    strategy_entry_ready: bool
    execution_observation_present: bool
    execution_eligible: bool
    risk_sizing_valid: bool
    session_open: bool
    provider_ready: bool
    provider_circuit_clear: bool
    strategy_kill_switch_clear: bool
    qualification_authorized: bool
    profile_matches: bool
    evidence_policy_matches: bool
    reason_codes: tuple[str, ...] = ()
    authorized: bool

    @model_validator(mode="after")
    def authorization_is_conjunction(self):
        required = (
            self.source_member_valid,
            self.morning_evidence_eligible,
            self.session_evaluability_complete,
            self.bar_coverage_ready,
            self.strategy_entry_ready,
            self.execution_observation_present,
            self.execution_eligible,
            self.risk_sizing_valid,
            self.session_open,
            self.provider_ready,
            self.provider_circuit_clear,
            self.strategy_kill_switch_clear,
            self.qualification_authorized,
            self.profile_matches,
            self.evidence_policy_matches,
        )
        if self.authorized != all(required):
            raise ValueError("authorized must equal conjunction of authority predicates")
        if self.authorized and self.reason_codes:
            raise ValueError("authorized assessment cannot contain reason_codes")
        return self


def classify_provider_exception(
    exc: BaseException,
    *,
    provider: str,
    observed_at: datetime,
) -> ProviderReadiness:
    """Classify an unavailable provider input without manufacturing an observation."""

    text = str(exc or "")
    lower = text.casefold()
    error_type = type(exc).__name__
    if "not configured" in lower or "credentials are not configured" in lower:
        state: ProviderReadinessState = "NOT_CONFIGURED"
    elif "401" in lower or "403" in lower or "unauthorized" in lower or "forbidden" in lower:
        state = "AUTH_FAILED"
    elif "429" in lower or "rate limit" in lower or "too many requests" in lower:
        state = "RATE_LIMITED"
    elif "no latest quote" in lower or "no latest trade" in lower or "quote" in lower and "missing" in lower:
        state = "QUOTE_MISSING"
    elif "contract" in error_type.casefold() or "malformed" in lower or "invalid" in lower:
        state = "CONTRACT_ERROR"
    elif "stale" in lower:
        state = "STALE"
    elif isinstance(exc, (ConnectionError, TimeoutError, OSError)) or "unavailable" in lower or "timeout" in lower:
        state = "PROVIDER_UNAVAILABLE"
    else:
        state = "UNKNOWN_ERROR"
    return ProviderReadiness(
        provider=provider,
        state=state,
        ready=False,
        observed_at=observed_at,
        error_type=error_type,
        detail=text,
    )


__all__ = [
    "BarCoverageAssessment",
    "DEFAULT_MARKET_EVIDENCE_POLICY",
    "ExecutionInputGap",
    "MARKET_EVIDENCE_POLICY_VERSION",
    "YAHOO_HARDENED_EVIDENCE_POLICY_VERSION",
    "YAHOO_RELATIVE_VOLUME",
    "MIN_TOD_RVOL_BASELINE_SESSIONS",
    "MarketEvidencePolicy",
    "PremarketLiquidityEvidence",
    "premarket_evidence_feature_compatible",
    "evidence_authorizes_feature",
    "ProviderReadiness",
    "SessionEvaluabilityAssessment",
    "SourceMemberDisposition",
    "TradeAuthorizationAssessment",
    "classify_provider_exception",
]
