from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AuthorityTier = Literal[1, 2, 3, 4]
CoverageState = Literal["unchecked", "complete", "failed", "unresolved"]
ResearchOperation = Literal[
    "sec_find_filings",
    "sec_extract_filing",
    "company_find_releases",
    "company_extract_release",
    "web_search",
    "web_extract",
    "evaluate",
    "stop",
]


def utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("research timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def fingerprint(payload: Any) -> str:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json", exclude={"immutable_fingerprint", "omnix_known_at"})
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IssuerIdentity(FrozenModel):
    identity_id: str
    instrument_id: str
    symbol: str
    exchange: str | None = None
    legal_name: str | None = None
    cik: str | None = None
    source: str
    source_available_at: datetime | None = None
    captured_at: datetime
    omnix_known_at: datetime | None = None
    confidence: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    immutable_fingerprint: str

    _timestamps = field_validator("source_available_at", "captured_at", "omnix_known_at")(utc)


class TradingEvidence(FrozenModel):
    evidence_id: str
    instrument_id: str
    issuer_identity_id: str | None = None
    evidence_type: str
    source_type: Literal["sec", "company_ir", "news", "web", "manual"]
    source_locator: str
    source_authority_tier: AuthorityTier
    source_published_at: datetime | None = None
    source_available_at: datetime | None = None
    captured_at: datetime
    omnix_known_at: datetime | None = None
    title: str | None = None
    content: str = Field(min_length=1, max_length=250_000)
    content_hash: str = Field(min_length=64, max_length=64)
    extraction_status: Literal["metadata", "snippet", "completed", "failed"] = "snippet"
    metadata: dict[str, Any] = Field(default_factory=dict)
    immutable_fingerprint: str

    _timestamps = field_validator(
        "source_published_at", "source_available_at", "captured_at", "omnix_known_at"
    )(utc)


class TradingMarketBriefItem(FrozenModel):
    text: str = Field(min_length=1, max_length=500)
    source_evidence_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=4)


class TradingMarketBrief(FrozenModel):
    instrument_id: str
    generated_at: datetime
    provider: str
    model: str
    headline: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=1_800)
    key_points: tuple[TradingMarketBriefItem, ...] = Field(default_factory=tuple, max_length=5)
    risks: tuple[TradingMarketBriefItem, ...] = Field(default_factory=tuple, max_length=5)
    watch_items: tuple[TradingMarketBriefItem, ...] = Field(default_factory=tuple, max_length=4)
    confidence: Literal["low", "medium", "high", "uncertain"] = "uncertain"
    source_evidence_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    read_only: Literal[True] = True
    disclaimer: Literal[
        "AI-generated research summary only. Not financial advice. No order was created or executed."
    ] = "AI-generated research summary only. Not financial advice. No order was created or executed."

    _timestamps = field_validator("generated_at")(utc)


class SupplyFact(FrozenModel):
    fact_id: str
    schema_version: str = "supply-facts-1"
    extractor_version: str = "supply-parser-1"
    instrument_id: str
    supply_type: Literal[
        "atm", "warrant", "registered_offering", "resale_registration",
        "convertible", "shelf_registration", "equity_line",
    ]
    status: Literal[
        "active", "terminated", "exhausted", "expired", "redeemed",
        "exercisable", "locked", "withdrawn", "unknown",
    ] = "unknown"
    shares: Decimal | None = Field(default=None, ge=0)
    remaining_capacity_usd: Decimal | None = Field(default=None, ge=0)
    strike_price: Decimal | None = Field(default=None, ge=0)
    exercise_status: str | None = None
    registration_status: str | None = None
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    source_evidence_ids: tuple[str, ...]
    resolution_status: Literal["resolved", "partial", "unresolved"] = "unresolved"
    confidence: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    generated_at: datetime
    omnix_known_at: datetime | None = None
    immutable_fingerprint: str

    _timestamps = field_validator("effective_at", "expires_at", "generated_at", "omnix_known_at")(utc)


class SupplyMetrics(FrozenModel):
    potential_dilution_pct_float: Decimal | None = None
    remaining_atm_pct_market_cap: Decimal | None = None
    in_the_money_warrant_pct_float: Decimal | None = None
    registered_resale_pct_float: Decimal | None = None
    immediate_supply_risk: bool | None = None
    supply_resolution_status: Literal["clear", "risk_found", "unresolved"] = "unresolved"


class CatalystFactSet(FrozenModel):
    schema_version: str = "catalyst-facts-1"
    extractor_version: str = "catalyst-parser-1"
    primary_confirmed: bool = False
    same_day: bool = False
    source_count_primary: int = Field(default=0, ge=0)
    source_count_secondary: int = Field(default=0, ge=0)
    catalyst_type: str = "unknown"
    source_published_at: datetime | None = None
    official_filing_present: bool = False
    company_release_present: bool = False
    unresolved: bool = True
    source_evidence_ids: tuple[str, ...] = ()
    generated_at: datetime
    omnix_known_at: datetime | None = None

    _timestamps = field_validator("source_published_at", "generated_at", "omnix_known_at")(utc)


class ResearchCoverage(FrozenModel):
    sec: CoverageState = "unchecked"
    company_ir: CoverageState = "unchecked"
    recent_news: CoverageState = "unchecked"
    prior_news_novelty: CoverageState = "unchecked"
    atm: CoverageState = "unchecked"
    warrants: CoverageState = "unchecked"
    resale_registration: CoverageState = "unchecked"
    convertibles: CoverageState = "unchecked"


class TradingResearchRequest(FrozenModel):
    request_id: str
    contract_version: Literal["trading-research-1"] = "trading-research-1"
    strategy_id: str | None = None
    instrument_id: str
    issuer_identity_id: str | None = None
    requested_at: datetime
    decision_context_at: datetime
    evidence_cutoff_at: datetime
    known_headlines: tuple[str, ...] = ()
    known_filings: tuple[str, ...] = ()
    objectives: tuple[str, ...] = ()
    allowed_operations: tuple[ResearchOperation, ...] = (
        "sec_find_filings", "sec_extract_filing", "company_find_releases",
        "company_extract_release", "web_search", "web_extract", "evaluate", "stop",
    )
    deadline_at: datetime
    max_steps: int = Field(default=8, ge=1, le=20)
    max_queries: int = Field(default=5, ge=0, le=20)
    max_sources: int = Field(default=20, ge=1, le=100)
    max_extracts: int = Field(default=8, ge=0, le=30)

    _timestamps = field_validator(
        "requested_at", "decision_context_at", "evidence_cutoff_at", "deadline_at"
    )(utc)


class TradingResearchReport(FrozenModel):
    report_id: str
    report_version: int = Field(ge=1)
    contract_version: str = "trading-research-1"
    strategy_id: str | None = None
    instrument_id: str
    research_started_at: datetime
    research_completed_at: datetime | None = None
    evidence_cutoff_at: datetime
    omnix_known_at: datetime | None = None
    catalyst_status: Literal["confirmed", "probable", "unresolved", "absent"] = "unresolved"
    supply_status: Literal["clear", "risk_found", "unresolved"] = "unresolved"
    research_status: Literal["complete", "partial", "timed_out", "failed"] = "partial"
    coverage: ResearchCoverage = Field(default_factory=ResearchCoverage)
    unresolved_facts: tuple[str, ...] = ()
    source_evidence_ids: tuple[str, ...] = ()
    hermes_trace_id: str | None = None
    planner_backend: str = "local"
    stop_reason: str | None = None
    immutable_fingerprint: str

    _timestamps = field_validator(
        "research_started_at", "research_completed_at", "evidence_cutoff_at", "omnix_known_at"
    )(utc)


class TradingFactSet(FrozenModel):
    fact_set_id: str
    schema_version: str = "trading-facts-1"
    extractor_version: str = "trading-extractors-1"
    strategy_id: str | None = None
    instrument_id: str
    report_id: str | None = None
    generated_at: datetime
    omnix_known_at: datetime | None = None
    catalyst: CatalystFactSet
    supply: tuple[SupplyFact, ...] = ()
    supply_metrics: SupplyMetrics = Field(default_factory=SupplyMetrics)
    completeness: ResearchCoverage = Field(default_factory=ResearchCoverage)
    unresolved_facts: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    immutable_fingerprint: str

    _timestamps = field_validator("generated_at", "omnix_known_at")(utc)


class StrategyResearchFeatures(FrozenModel):
    feature_id: str
    projection_version: str = "research-features-1"
    research_policy_version: str = "trading-research-1-shadow"
    strategy_id: str | None = None
    instrument_id: str
    fact_set_id: str
    decision_at: datetime
    omnix_known_at: datetime | None = None
    primary_catalyst_confirmed: bool = False
    catalyst_same_day: bool = False
    catalyst_fresh: bool = False
    catalyst_age_minutes: int | None = None
    immediate_supply_risk: bool | None = None
    supply_resolution_status: Literal["clear", "risk_found", "unresolved"] = "unresolved"
    research_status: Literal["complete", "partial", "timed_out", "failed", "unavailable"] = "unavailable"
    unresolved_supply: bool = True
    source_authority_sufficient: bool = False
    immutable_fingerprint: str

    _timestamps = field_validator("decision_at", "omnix_known_at")(utc)


class ResearchActionProposal(FrozenModel):
    operation: ResearchOperation
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ResearchActionRecord(FrozenModel):
    action_id: str
    trace_id: str
    strategy_id: str | None = None
    instrument_id: str
    step: int = Field(ge=0)
    operation: ResearchOperation
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    status: Literal["proposed", "completed", "failed", "blocked", "timed_out"]
    result_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    requested_at: datetime
    completed_at: datetime | None = None
    omnix_known_at: datetime | None = None
    error_code: str | None = None
    immutable_fingerprint: str

    _timestamps = field_validator("requested_at", "completed_at", "omnix_known_at")(utc)


class NoveltyShadowAnnotation(FrozenModel):
    annotation_id: str
    instrument_id: str
    observed_at: datetime
    novelty: Literal["new", "incremental", "recycled", "uncertain"]
    relevance: Literal["high", "medium", "low", "uncertain"]
    catalyst_class: str
    conflict_summary: str = ""
    confidence: Decimal = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...]
    rationale: str = ""
    shadow_only: Literal[True] = True

    _timestamps = field_validator("observed_at")(utc)


class ResearchOutcome(FrozenModel):
    outcome_id: str
    session_date: date
    strategy_id: str | None = None
    instrument_id: str
    strategy_version: str
    research_policy_version: str
    feature_projection_version: str
    market_fidelity: str
    research_fidelity: str
    research_status: str
    features: dict[str, Any]
    strategy_state: str | None = None
    rejection_reason: str | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None
    r_result: Decimal | None = None
    two_r_before_minus_one_r: bool | None = None
    time_to_mfe_minutes: Decimal | None = None
    time_to_stop_minutes: Decimal | None = None
    data_quality_flags: tuple[str, ...] = ()
    immutable_fingerprint: str

    _timestamps = field_validator("entry_time", "exit_time")(utc)


class ValidationFeatureResult(FrozenModel):
    feature: str
    sample_size: int = Field(ge=0)
    exact_sample_size: int = Field(ge=0)
    in_sample_effect_r: Decimal | None = None
    out_of_sample_effect_r: Decimal | None = None
    win_probability_delta: Decimal | None = None
    confidence_interval_low: Decimal | None = None
    confidence_interval_high: Decimal | None = None
    recommendation: Literal["observe_only", "score_only", "soft_gate", "hard_gate"] = "observe_only"
    reason: str = ""


class ResearchValidationReport(FrozenModel):
    validation_id: str
    policy_version: str
    generated_at: datetime
    sample_size: int = Field(ge=0)
    exact_sample_size: int = Field(ge=0)
    feature_results: tuple[ValidationFeatureResult, ...] = ()
    promotion_allowed: bool = False
    notes: tuple[str, ...] = ()
    immutable_fingerprint: str

    _timestamps = field_validator("generated_at")(utc)
