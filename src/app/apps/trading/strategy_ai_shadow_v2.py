from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.providers import ChatMessage
from app.providers.structured.contracts import StructuredMode
from app.providers.structured.schema_projection import project_provider_schema

from .models import MarketBar
from .structured_llm import trading_model_call, sends_json_schema
from .research import _call_provider, _json_payload, _provider_identity, default_research_provider
from .research.contracts import TradingEvidence, TradingResearchReport

AI_SHADOW_V2_VERSION = "ai-shadow-v2"
AI_SHADOW_V2_POLICY_VERSION = "ai-shadow-v2-roadmap-2"
AIShadowV2Arm = Literal[
    "morning_control",
    "morning_catalyst",
    "full_session_control",
    "full_session_catalyst",
]
AIShadowV2State = Literal["avoid", "watch", "armed", "enter", "manage", "exit"]
SetupFamily = Literal[
    "trend_continuation",
    "failed_selloff_reclaim",
    "first_pullback",
    "squeeze_continuation",
    "gap_hold",
    "distribution",
    "unresolved",
]
PersistenceClass = Literal["low", "medium", "high", "uncertain"]
AlphaBias = Literal["favorable", "neutral", "cautious"]
TriggerType = Literal[
    "bar_close_above",
    "bar_close_below",
    "vwap_reclaim",
    "new_session_high",
    "volume_expansion",
]
EvidenceQuality = Literal["primary_verified", "mixed", "secondary_only", "unresolved"]


class StructuredAlphaTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_type: TriggerType
    price: Decimal | None = Field(default=None, gt=0)
    min_volume_ratio: Decimal | None = Field(default=None, ge=0)
    expiry_minutes: int = Field(default=30, ge=1, le=180)


class CatalystInfluence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    persistence_class: PersistenceClass = "uncertain"
    alpha_bias: AlphaBias = "neutral"
    conviction_modifier: Literal["negative", "neutral", "positive"] = "neutral"
    preferred_behavior: Literal[
        "require_strong_confirmation",
        "standard_confirmation",
        "seek_early_confirmation",
    ] = "standard_confirmation"
    confirmation_hurdle: int = Field(default=60, ge=40, le=80)
    empirical_persistence_rate: Decimal | None = Field(default=None, ge=0, le=1)
    empirical_sample_size: int = Field(default=0, ge=0)


class CatalystIntelligenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    instrument_id: str
    as_of: datetime
    provider: str
    model: str | None = None
    evidence_fingerprint: str = Field(min_length=64, max_length=64)
    evidence_ids: tuple[str, ...]
    evidence_quality: EvidenceQuality = "unresolved"
    primary_source_verified: bool = False
    catalyst_type: str = "unknown"
    catalyst_summary: str = Field(default="", max_length=1200)
    fundamental_materiality: int = Field(default=50, ge=0, le=100)
    materiality_to_company_size: int = Field(default=50, ge=0, le=100)
    revenue_or_cash_impact: Literal["none", "small", "moderate", "large", "transformative", "unknown"] = "unknown"
    catalyst_novelty: Literal["new", "incremental", "recycled", "uncertain"] = "uncertain"
    attention_strength: int = Field(default=50, ge=0, le=100)
    expected_attention_duration: Literal["minutes", "hours", "session", "multi_day", "uncertain"] = "uncertain"
    event_certainty: int = Field(default=50, ge=0, le=100)
    supply_pressure: Literal["low", "medium", "high", "unresolved"] = "unresolved"
    promotional_risk: Literal["low", "medium", "high", "unresolved"] = "unresolved"
    gap_already_prices_in_news: Literal["low", "medium", "high", "uncertain"] = "uncertain"
    intraday_persistence_class: PersistenceClass = "uncertain"
    source_quality_score: int = Field(default=0, ge=0, le=100)
    catalyst_strength: int = Field(default=50, ge=0, le=100)
    ambiguity: Literal["low", "medium", "high"] = "medium"
    influence: CatalystInfluence = Field(default_factory=CatalystInfluence)
    reasoning: str = Field(default="", max_length=2000)
    research_only: Literal[True] = True
    execution_authority: Literal[False] = False

    @field_validator("as_of")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("catalyst intelligence timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class CatalystSemanticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalyst_type: str = "unknown"
    catalyst_summary: str = Field(default="", max_length=1200)
    fundamental_materiality: int = Field(default=50, ge=0, le=100)
    materiality_to_company_size: int = Field(default=50, ge=0, le=100)
    revenue_or_cash_impact: Literal["none", "small", "moderate", "large", "transformative", "unknown"] = "unknown"
    catalyst_novelty: Literal["new", "incremental", "recycled", "uncertain"] = "uncertain"
    attention_strength: int = Field(default=50, ge=0, le=100)
    expected_attention_duration: Literal["minutes", "hours", "session", "multi_day", "uncertain"] = "uncertain"
    event_certainty: int = Field(default=50, ge=0, le=100)
    supply_pressure: Literal["low", "medium", "high", "unresolved"] = "unresolved"
    promotional_risk: Literal["low", "medium", "high", "unresolved"] = "unresolved"
    gap_already_prices_in_news: Literal["low", "medium", "high", "uncertain"] = "uncertain"
    intraday_persistence_class: PersistenceClass = "uncertain"
    catalyst_strength: int = Field(default=50, ge=0, le=100)
    ambiguity: Literal["low", "medium", "high"] = "medium"
    reasoning: str = Field(default="", max_length=2000)


class MarketStructureSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    current_price: Decimal = Field(gt=0)
    session_open: Decimal = Field(gt=0)
    session_high: Decimal = Field(gt=0)
    session_low: Decimal = Field(gt=0)
    session_vwap: Decimal | None = Field(default=None, gt=0)
    vwap_distance_pct: Decimal | None = None
    hod_distance_pct: Decimal = Field(ge=0)
    session_return_pct: Decimal
    ema9_1m: Decimal | None = None
    ema20_1m: Decimal | None = None
    ema9_slope_pct_5bars: Decimal | None = None
    ema20_slope_pct_5bars: Decimal | None = None
    three_minute_return_pct: Decimal | None = None
    five_minute_return_pct: Decimal | None = None
    pullback_from_hod_pct: Decimal = Field(ge=0)
    current_volume_ratio_to_prior10: Decimal | None = None
    higher_low_count_5: int = Field(default=0, ge=0, le=4)
    higher_high_count_5: int = Field(default=0, ge=0, le=4)
    range_location: Decimal = Field(ge=0, le=1)
    confirmation_score: int = Field(default=0, ge=0, le=100)

    @field_validator("observed_at")
    @classmethod
    def observed_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("market structure timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)


class AIShadowV2AlphaDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    setup_family: SetupFamily
    state: AIShadowV2State
    quality_score: int = Field(ge=0, le=100)
    entry_zone_low: Decimal | None = Field(default=None, gt=0)
    entry_zone_high: Decimal | None = Field(default=None, gt=0)
    invalidation_price: Decimal | None = Field(default=None, gt=0)
    target_1: Decimal | None = Field(default=None, gt=0)
    target_2: Decimal | None = Field(default=None, gt=0)
    trigger: StructuredAlphaTrigger | None = None
    extension_risk: Literal["low", "medium", "high"] = "medium"
    evidence_for: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    evidence_against: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    thesis_changed: bool = False
    thesis: str = Field(min_length=1, max_length=1000)
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_geometry(self):
        if self.entry_zone_low is not None and self.entry_zone_high is not None and self.entry_zone_high < self.entry_zone_low:
            raise ValueError("entry_zone_high must be >= entry_zone_low")
        if self.state == "armed" and self.trigger is None:
            raise ValueError("armed decisions require a structured trigger")
        if self.state == "enter" and (self.invalidation_price is None or self.target_1 is None):
            raise ValueError("enter decisions require invalidation_price and target_1")
        return self


class AIShadowV2BatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decisions: tuple[AIShadowV2AlphaDecision, ...]


class DeterministicRiskGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_reference: Decimal
    invalidation_price: Decimal | None = None
    target_1: Decimal | None = None
    risk_per_share: Decimal | None = None
    reward_per_share: Decimal | None = None
    gross_r: Decimal | None = None
    net_r: Decimal | None = None
    estimated_cost_bps: Decimal = Decimal("0")
    valid: bool = False
    reason: str


class OpportunityEpisodeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm: AIShadowV2Arm
    instrument_id: str
    episode_id: str
    started_at: datetime
    ended_at: datetime
    setup_family: SetupFamily
    catalyst_persistence_class: PersistenceClass | None = None
    entered: bool = False
    entry_price: Decimal
    invalidation_price: Decimal | None = None
    target_1: Decimal | None = None
    return_1m_pct: Decimal | None = None
    return_3m_pct: Decimal | None = None
    return_5m_pct: Decimal | None = None
    return_10m_pct: Decimal | None = None
    return_20m_pct: Decimal | None = None
    return_60m_pct: Decimal | None = None
    mfe_pct: Decimal | None = None
    mae_pct: Decimal | None = None
    peak_r: Decimal | None = None
    plus_one_r_before_minus_one_r: bool | None = None
    plus_two_r_before_minus_one_r: bool | None = None
    positive_opportunity: bool | None = None
    research_only: Literal[True] = True
    execution_authority: Literal[False] = False


def _pct(new: Decimal, old: Decimal) -> Decimal | None:
    if old <= 0:
        return None
    return (new / old - Decimal("1")) * Decimal("100")


def _ema(values: list[Decimal], period: int) -> list[Decimal]:
    if not values:
        return []
    alpha = Decimal("2") / Decimal(period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(alpha * value + (Decimal("1") - alpha) * output[-1])
    return output


def _return_minutes(regular: list[MarketBar], minutes: int) -> Decimal | None:
    if len(regular) <= minutes:
        return None
    return _pct(regular[-1].close, regular[-1 - minutes].close)


def build_market_structure_snapshot(bars: list[MarketBar]) -> MarketStructureSnapshot:
    regular = sorted([bar for bar in bars if bar.is_final and bar.session == "regular"], key=lambda item: item.end_time)
    if not regular:
        raise ValueError("market_structure_requires_regular_bars")
    current = regular[-1]
    session_open = regular[0].open
    session_high = max(bar.high for bar in regular)
    session_low = min(bar.low for bar in regular)
    total_volume = sum((bar.volume for bar in regular), Decimal("0"))
    typical_notional = sum((((bar.high + bar.low + bar.close) / Decimal("3")) * bar.volume for bar in regular), Decimal("0"))
    vwap = typical_notional / total_volume if total_volume > 0 else None
    closes = [bar.close for bar in regular]
    ema9 = _ema(closes, 9)
    ema20 = _ema(closes, 20)
    ema9_slope = _pct(ema9[-1], ema9[-6]) if len(ema9) >= 6 else None
    ema20_slope = _pct(ema20[-1], ema20[-6]) if len(ema20) >= 6 else None
    prior_volumes = [bar.volume for bar in regular[-11:-1]]
    average_prior = sum(prior_volumes, Decimal("0")) / Decimal(len(prior_volumes)) if prior_volumes else None
    volume_ratio = current.volume / average_prior if average_prior is not None and average_prior > 0 else None
    recent = regular[-5:]
    higher_lows = sum(1 for left, right in zip(recent, recent[1:]) if right.low > left.low)
    higher_highs = sum(1 for left, right in zip(recent, recent[1:]) if right.high > left.high)
    session_range = session_high - session_low
    range_location = (current.close - session_low) / session_range if session_range > 0 else Decimal("0.5")
    vwap_distance = _pct(current.close, vwap) if vwap is not None else None
    hod_distance = max(Decimal("0"), (session_high - current.close) / session_high * Decimal("100"))
    confirmation = 0
    if vwap is not None and current.close >= vwap:
        confirmation += 20
    if ema9 and ema20 and current.close >= ema9[-1] >= ema20[-1]:
        confirmation += 20
    if ema9_slope is not None and ema9_slope > 0:
        confirmation += 10
    if ema20_slope is not None and ema20_slope > 0:
        confirmation += 10
    confirmation += min(20, higher_lows * 5)
    if volume_ratio is not None and volume_ratio >= Decimal("1.25"):
        confirmation += 10
    if hod_distance <= Decimal("5"):
        confirmation += 10
    return MarketStructureSnapshot(
        observed_at=current.end_time,
        current_price=current.close,
        session_open=session_open,
        session_high=session_high,
        session_low=session_low,
        session_vwap=vwap,
        vwap_distance_pct=vwap_distance,
        hod_distance_pct=hod_distance,
        session_return_pct=_pct(current.close, session_open) or Decimal("0"),
        ema9_1m=ema9[-1] if ema9 else None,
        ema20_1m=ema20[-1] if ema20 else None,
        ema9_slope_pct_5bars=ema9_slope,
        ema20_slope_pct_5bars=ema20_slope,
        three_minute_return_pct=_return_minutes(regular, 3),
        five_minute_return_pct=_return_minutes(regular, 5),
        pullback_from_hod_pct=hod_distance,
        current_volume_ratio_to_prior10=volume_ratio,
        higher_low_count_5=higher_lows,
        higher_high_count_5=higher_highs,
        range_location=max(Decimal("0"), min(Decimal("1"), range_location)),
        confirmation_score=min(100, confirmation),
    )


def evidence_fingerprint(evidence: list[TradingEvidence] | tuple[TradingEvidence, ...]) -> str:
    payload = [
        {
            "id": item.evidence_id,
            "fingerprint": item.immutable_fingerprint,
            "authority": item.source_authority_tier,
            "known": item.omnix_known_at.isoformat() if item.omnix_known_at else None,
        }
        for item in sorted(evidence, key=lambda item: item.evidence_id)
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def deterministic_evidence_quality(evidence: list[TradingEvidence] | tuple[TradingEvidence, ...]) -> tuple[EvidenceQuality, bool, int]:
    if not evidence:
        return "unresolved", False, 0
    reference = max(
        item.omnix_known_at or item.captured_at
        for item in evidence
    )
    lower = reference - timedelta(hours=72)
    relevant = []
    supply_only_forms = {
        "S-1", "S-1/A", "S-3", "S-3/A", "424B3", "424B5", "RW", "EFFECT"
    }
    for item in evidence:
        if item.source_type in {"news", "web", "manual"}:
            relevant.append(item)
            continue
        published = (
            item.source_published_at
            or item.source_available_at
            or item.captured_at
        )
        if published < lower:
            continue
        if item.source_type == "company_ir":
            relevant.append(item)
            continue
        if item.source_type == "sec":
            form = str(item.metadata.get("form") or "").upper()
            if form not in supply_only_forms:
                relevant.append(item)
    if not relevant:
        return "unresolved", False, 0
    primary = [
        item
        for item in relevant
        if item.source_authority_tier == 1
        and item.source_type in {"sec", "company_ir"}
    ]
    verified = bool(primary)
    if verified and all(item.source_authority_tier <= 2 for item in relevant):
        quality: EvidenceQuality = "primary_verified"
    elif verified:
        quality = "mixed"
    else:
        quality = "secondary_only"
    score = round(
        sum(max(0, 5 - int(item.source_authority_tier)) for item in relevant)
        / (len(relevant) * 4)
        * 100
    )
    return quality, verified, min(100, score)


def derive_catalyst_influence(
    *,
    persistence_class: PersistenceClass,
    primary_source_verified: bool,
    supply_pressure: str,
    promotional_risk: str,
    gap_already_prices_in_news: str,
    empirical_persistence_rate: Decimal | None = None,
    empirical_sample_size: int = 0,
) -> CatalystInfluence:
    score = 60
    if persistence_class == "high":
        score -= 8
    elif persistence_class == "low":
        score += 8
    if primary_source_verified:
        score -= 3
    if supply_pressure == "high":
        score += 8
    elif supply_pressure == "low":
        score -= 2
    if promotional_risk == "high":
        score += 7
    if gap_already_prices_in_news == "high":
        score += 7
    elif gap_already_prices_in_news == "low":
        score -= 2
    if empirical_persistence_rate is not None and empirical_sample_size >= 30:
        strength = 10 if empirical_sample_size >= 100 else 5
        if empirical_persistence_rate >= Decimal("0.60"):
            score -= strength
        elif empirical_persistence_rate <= Decimal("0.35"):
            score += strength
    hurdle = max(40, min(80, score))
    if hurdle <= 52:
        bias: AlphaBias = "favorable"
        conviction = "positive"
        behavior = "seek_early_confirmation"
    elif hurdle >= 68:
        bias = "cautious"
        conviction = "negative"
        behavior = "require_strong_confirmation"
    else:
        bias = "neutral"
        conviction = "neutral"
        behavior = "standard_confirmation"
    return CatalystInfluence(
        persistence_class=persistence_class,
        alpha_bias=bias,
        conviction_modifier=cast(Any, conviction),
        preferred_behavior=cast(Any, behavior),
        confirmation_hurdle=hurdle,
        empirical_persistence_rate=empirical_persistence_rate,
        empirical_sample_size=empirical_sample_size,
    )


def normalize_catalyst_provenance(
    snapshot: CatalystIntelligenceSnapshot,
) -> CatalystIntelligenceSnapshot:
    if snapshot.primary_source_verified:
        return snapshot
    if not snapshot.evidence_ids:
        quality: EvidenceQuality = "unresolved"
    elif snapshot.evidence_quality == "primary_verified":
        quality = "mixed"
    else:
        quality = snapshot.evidence_quality
    if quality == snapshot.evidence_quality:
        return snapshot
    return snapshot.model_copy(update={"evidence_quality": quality})


class CatalystIntelligenceAnalyzer:
    def __init__(self, provider_factory=default_research_provider) -> None:
        self.provider_factory = provider_factory

    def _assess_core(
        self,
        *,
        instrument_id: str,
        as_of: datetime,
        report: TradingResearchReport | None,
        evidence: list[TradingEvidence],
        morning_context: dict[str, Any] | None = None,
        empirical_persistence_rate: Decimal | None = None,
        empirical_sample_size: int = 0,
    ) -> CatalystIntelligenceSnapshot:
        if as_of.tzinfo is None:
            raise ValueError("catalyst_intelligence_as_of_must_be_timezone_aware")
        causal = [item for item in evidence if item.omnix_known_at is None or item.omnix_known_at <= as_of]
        quality, primary_verified, quality_score = deterministic_evidence_quality(causal)
        fingerprint_value = evidence_fingerprint(causal)
        if not causal:
            influence = derive_catalyst_influence(
                persistence_class="uncertain",
                primary_source_verified=False,
                supply_pressure="unresolved",
                promotional_risk="unresolved",
                gap_already_prices_in_news="uncertain",
                empirical_persistence_rate=empirical_persistence_rate,
                empirical_sample_size=empirical_sample_size,
            )
            return CatalystIntelligenceSnapshot(
                snapshot_id=f"catalyst-v2-{fingerprint_value[:24]}",
                instrument_id=instrument_id,
                as_of=as_of,
                provider="none",
                evidence_fingerprint=fingerprint_value,
                evidence_ids=(),
                evidence_quality=quality,
                primary_source_verified=False,
                source_quality_score=0,
                influence=influence,
                reasoning="No causal catalyst evidence was available.",
            )
        provider = self.provider_factory()
        provider_name, model = _provider_identity(provider, None)
        payload = {
            "instrument_id": instrument_id,
            "as_of": as_of.astimezone(timezone.utc).isoformat(),
            "research_report": report.model_dump(mode="json") if report is not None else None,
            "morning_context": morning_context or {},
            "deterministic_provenance": {
                "evidence_quality": quality,
                "primary_source_verified": primary_verified,
                "source_quality_score": quality_score,
            },
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "source_type": item.source_type,
                    "source_authority_tier": item.source_authority_tier,
                    "source_locator": item.source_locator,
                    "source_published_at": item.source_published_at.isoformat() if item.source_published_at is not None else None,
                    "title": item.title,
                    "content": item.content[:12000],
                    "metadata": item.metadata,
                }
                for item in causal
            ],
        }
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are the read-only Omnix Catalyst Intelligence model. Use only supplied causal evidence. "
                    "Do not recommend trades, choose size, or discuss execution. Distinguish fundamental materiality "
                    "from likely intraday persistence. A strong catalyst can still be fully priced into a large gap. "
                    "Assess supply pressure and promotional risk separately. Return JSON only with exactly: "
                    "catalyst_type, catalyst_summary, fundamental_materiality, materiality_to_company_size, "
                    "revenue_or_cash_impact, catalyst_novelty, attention_strength, expected_attention_duration, "
                    "event_certainty, supply_pressure, promotional_risk, gap_already_prices_in_news, "
                    "intraday_persistence_class, catalyst_strength, ambiguity, reasoning. "
                    "Scores are integers 0..100. catalyst_novelty=new|incremental|recycled|uncertain. "
                    "expected_attention_duration=minutes|hours|session|multi_day|uncertain. "
                    "supply_pressure/promotional_risk=low|medium|high|unresolved. "
                    "gap_already_prices_in_news=low|medium|high|uncertain. "
                    "intraday_persistence_class=low|medium|high|uncertain. ambiguity=low|medium|high."
                ),
            ),
            ChatMessage(role="user", content=json.dumps(payload, sort_keys=True, default=str)),
        ]
        if hasattr(provider, "chat_completion") and callable(provider.chat_completion):
            if sends_json_schema(provider_name):
                schema = project_provider_schema(
                    CatalystSemanticResponse.model_json_schema(),
                    mode=StructuredMode.JSON_SCHEMA,
                    provider_name=provider_name,
                )
                response_format: dict[str, Any] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "catalyst_intelligence_semantics",
                        "strict": True,
                        "schema": schema,
                    },
                }
            else:
                response_format = {"type": "json_object"}
            semantics = trading_model_call(
                provider,
                messages,
                output_model=CatalystSemanticResponse,
                contract_id="trading.ai_shadow_v2.catalyst_semantics",
                schema_name="catalyst_intelligence_semantics",
                model=model,
                max_tokens=1_400,
                response_format=response_format,
            ).value
        else:
            semantics = CatalystSemanticResponse.model_validate(
                _json_payload(_call_provider(provider, messages, model))
            )
        influence = derive_catalyst_influence(
            persistence_class=semantics.intraday_persistence_class,
            primary_source_verified=primary_verified,
            supply_pressure=semantics.supply_pressure,
            promotional_risk=semantics.promotional_risk,
            gap_already_prices_in_news=semantics.gap_already_prices_in_news,
            empirical_persistence_rate=empirical_persistence_rate,
            empirical_sample_size=empirical_sample_size,
        )
        return CatalystIntelligenceSnapshot.model_validate({
            "snapshot_id": f"catalyst-v2-{fingerprint_value[:24]}",
            "instrument_id": instrument_id,
            "as_of": as_of,
            "provider": provider_name,
            "model": model,
            "evidence_fingerprint": fingerprint_value,
            "evidence_ids": [item.evidence_id for item in causal],
            "evidence_quality": quality,
            "primary_source_verified": primary_verified,
            "source_quality_score": quality_score,
            **semantics.model_dump(mode="json"),
            "influence": influence.model_dump(mode="json"),
        })

    def assess(
        self,
        *,
        instrument_id: str,
        as_of: datetime,
        report: TradingResearchReport | None,
        evidence: list[TradingEvidence],
        morning_context: dict[str, Any] | None = None,
        empirical_persistence_rate: Decimal | None = None,
        empirical_sample_size: int = 0,
    ) -> CatalystIntelligenceSnapshot:
        from .strategy_ai_shadow_v2_roadmap_policy import _catalyst_assess_policy

        snapshot = _catalyst_assess_policy(
            self,
            instrument_id=instrument_id,
            as_of=as_of,
            report=report,
            evidence=evidence,
            morning_context=morning_context,
            empirical_persistence_rate=empirical_persistence_rate,
            empirical_sample_size=empirical_sample_size,
            original=self._assess_core,
        )
        return normalize_catalyst_provenance(snapshot)


def alpha_prompt_snapshot(
    *,
    instrument_id: str,
    structure: MarketStructureSnapshot,
    morning: dict[str, Any],
    catalyst: CatalystIntelligenceSnapshot | None,
    include_catalyst: bool,
    trusted_microstructure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "instrument_id": instrument_id,
        "market_structure": structure.model_dump(mode="json"),
        "morning_context": morning,
        "market_microstructure": trusted_microstructure,
        "missing_data_rule": "unknown_not_bearish",
        "execution_status_visible": False,
        "catalyst_mode": "aware" if include_catalyst else "blind_control",
    }
    if include_catalyst:
        payload["catalyst_intelligence"] = catalyst.model_dump(mode="json") if catalyst is not None else None
        payload["alpha_confirmation_hurdle"] = catalyst.influence.confirmation_hurdle if catalyst is not None else 60
    else:
        payload["alpha_confirmation_hurdle"] = 60
    return payload


class AIShadowV2Analyzer:
    def __init__(self, provider_factory=default_research_provider) -> None:
        self.provider_factory = provider_factory

    def assess(self, *, arm: AIShadowV2Arm, rows: list[dict[str, Any]]) -> tuple[AIShadowV2AlphaDecision, ...]:
        from .strategy_ai_shadow_provider import assess_ai_shadow_v2_with_shared_circuit

        return assess_ai_shadow_v2_with_shared_circuit(
            self,
            arm=arm,
            rows=rows,
            original=type(self)._assess_core,
        )

    def _assess_core(self, *, arm: AIShadowV2Arm, rows: list[dict[str, Any]]) -> tuple[AIShadowV2AlphaDecision, ...]:
        if not rows:
            return ()
        provider = self.provider_factory()
        provider_name, model = _provider_identity(provider, None)
        catalyst_aware = arm.endswith("_catalyst")
        system = (
            "You are Omnix AI Shadow v2, a research-only long alpha model. Your job is alpha, not execution. "
            "Never use provider availability, execution eligibility, fallback state, quote freshness, halt status, "
            "rejection reasons, or connectivity as bearish evidence; those fields are hidden. Missing data means "
            "unknown, not bearish. Choose one state per symbol: avoid, watch, armed, enter, manage, exit. Previous "
            "skip/watch decisions are context, not evidence. Armed requires a machine-readable trigger. Enter "
            "requires explicit invalidation and target_1 geometry. "
        )
        if catalyst_aware:
            system += (
                "Catalyst intelligence is a prior on intraday persistence. A high-quality durable catalyst should "
                "increase willingness to enter when market structure provides reasonable confirmation and reduce "
                "the tendency to wait for an unnecessarily perfect setup. Weak, recycled, promotional, ambiguous, "
                "supply-heavy, or already-fully-priced catalysts require stronger confirmation. Catalyst strength "
                "never overrides deteriorating price action or invalid geometry. Use alpha_confirmation_hurdle as "
                "auditable guidance: favorable durable catalysts may lower it and weak catalysts may raise it. "
            )
        else:
            system += (
                "This is the catalyst-blind control arm. Do not infer or invent news/catalyst information. Use only "
                "market structure and supplied morning measurements. "
            )
        system += (
            "Return JSON only: {\"decisions\":[...]}. Every decision must contain exactly instrument_id, "
            "setup_family, state, quality_score, entry_zone_low, entry_zone_high, invalidation_price, target_1, "
            "target_2, trigger, extension_risk, evidence_for, evidence_against, thesis_changed, thesis, "
            "execution_authority. execution_authority must be false."
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=json.dumps({"arm": arm, "candidates": rows}, sort_keys=True, default=str)),
        ]
        if sends_json_schema(provider_name):
            schema = project_provider_schema(
                AIShadowV2BatchResponse.model_json_schema(),
                mode=StructuredMode.JSON_SCHEMA,
                provider_name=provider_name,
            )
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": "ai_shadow_v2_batch_response", "strict": True, "schema": schema},
            }
        else:
            response_format = {"type": "json_object"}
        parsed = trading_model_call(
            provider,
            messages,
            output_model=AIShadowV2BatchResponse,
            contract_id="trading.ai_shadow_v2.batch",
            schema_name="ai_shadow_v2_batch_response",
            model=model,
            max_tokens=max(1200, 500 * len(rows)),
            response_format=response_format,
        ).value
        requested = {str(row["instrument_id"]) for row in rows}
        seen: set[str] = set()
        output: list[AIShadowV2AlphaDecision] = []
        for decision in parsed.decisions:
            if decision.instrument_id not in requested or decision.instrument_id in seen:
                continue
            seen.add(decision.instrument_id)
            output.append(decision)
        if seen != requested:
            raise RuntimeError("ai_shadow_v2_missing_decisions:" + ",".join(sorted(requested - seen)))
        return tuple(output)


def trigger_satisfied(
    trigger: StructuredAlphaTrigger,
    *,
    structure: MarketStructureSnapshot,
    previous_structure: MarketStructureSnapshot | None = None,
) -> bool:
    if trigger.trigger_type == "bar_close_above":
        return trigger.price is not None and structure.current_price >= trigger.price
    if trigger.trigger_type == "bar_close_below":
        return trigger.price is not None and structure.current_price <= trigger.price
    if trigger.trigger_type == "vwap_reclaim":
        if structure.session_vwap is None:
            return False
        previous_below = previous_structure is None or previous_structure.session_vwap is None or previous_structure.current_price < previous_structure.session_vwap
        return previous_below and structure.current_price >= structure.session_vwap
    if trigger.trigger_type == "new_session_high":
        return previous_structure is not None and structure.session_high > previous_structure.session_high
    if trigger.trigger_type == "volume_expansion":
        return (
            trigger.min_volume_ratio is not None
            and structure.current_volume_ratio_to_prior10 is not None
            and structure.current_volume_ratio_to_prior10 >= trigger.min_volume_ratio
        )
    return False


def deterministic_risk_geometry(
    decision: AIShadowV2AlphaDecision,
    *,
    entry_reference: Decimal,
    estimated_cost_bps: Decimal,
    minimum_net_r: Decimal,
) -> DeterministicRiskGeometry:
    invalidation = decision.invalidation_price
    target = decision.target_1
    if invalidation is None or target is None:
        return DeterministicRiskGeometry(entry_reference=entry_reference, invalidation_price=invalidation, target_1=target, estimated_cost_bps=estimated_cost_bps, valid=False, reason="geometry_incomplete")
    risk = entry_reference - invalidation
    reward = target - entry_reference
    if risk <= 0:
        return DeterministicRiskGeometry(entry_reference=entry_reference, invalidation_price=invalidation, target_1=target, estimated_cost_bps=estimated_cost_bps, valid=False, reason="invalidation_not_below_entry")
    if reward <= 0:
        return DeterministicRiskGeometry(entry_reference=entry_reference, invalidation_price=invalidation, target_1=target, risk_per_share=risk, reward_per_share=reward, estimated_cost_bps=estimated_cost_bps, valid=False, reason="target_not_above_entry")
    gross_r = reward / risk
    cost = entry_reference * estimated_cost_bps / Decimal("10000")
    net_r = (reward - cost) / risk
    return DeterministicRiskGeometry(
        entry_reference=entry_reference,
        invalidation_price=invalidation,
        target_1=target,
        risk_per_share=risk,
        reward_per_share=reward,
        gross_r=gross_r,
        net_r=net_r,
        estimated_cost_bps=estimated_cost_bps,
        valid=net_r >= minimum_net_r,
        reason="ok" if net_r >= minimum_net_r else "minimum_net_r_not_met",
    )


def calibrate_persistence_prior(
    outcomes: list[dict[str, Any]],
    *,
    persistence_class: PersistenceClass,
    setup_family: SetupFamily,
    minimum_sample: int = 30,
) -> tuple[Decimal | None, int]:
    matched = [
        item for item in outcomes
        if item.get("catalyst_persistence_class") == persistence_class
        and item.get("setup_family") == setup_family
        and item.get("plus_two_r_before_minus_one_r") is not None
    ]
    if len(matched) < minimum_sample:
        return None, len(matched)
    wins = sum(1 for item in matched if item.get("plus_two_r_before_minus_one_r") is True)
    return Decimal(wins) / Decimal(len(matched)), len(matched)


def evaluate_opportunity_episode(
    *,
    arm: AIShadowV2Arm,
    instrument_id: str,
    episode_id: str,
    setup_family: SetupFamily,
    started_at: datetime,
    ended_at: datetime,
    entry_price: Decimal,
    invalidation_price: Decimal | None,
    target_1: Decimal | None,
    bars: list[MarketBar],
    entered: bool,
    catalyst_persistence_class: PersistenceClass | None,
) -> OpportunityEpisodeOutcome:
    from .strategy_outcome_quality import episode_reference_is_valid

    if not episode_reference_is_valid(started_at=started_at, ended_at=ended_at):
        raise ValueError("opportunity_episode_requires_current_regular_session_reference")
    regular = sorted(
        [bar for bar in bars if bar.is_final and bar.session == "regular" and bar.end_time >= started_at and bar.start_time <= ended_at],
        key=lambda item: item.end_time,
    )
    if not regular:
        raise ValueError("opportunity_episode_requires_causal_bars")
    mfe = max((bar.high / entry_price - Decimal("1")) * Decimal("100") for bar in regular)
    mae = min((bar.low / entry_price - Decimal("1")) * Decimal("100") for bar in regular)
    risk = entry_price - invalidation_price if invalidation_price is not None else None
    peak_r = (max(bar.high for bar in regular) - entry_price) / risk if risk is not None and risk > 0 else None

    def forward_return(minutes: int) -> Decimal | None:
        target_ts = started_at.timestamp() + minutes * 60
        eligible = [bar for bar in regular if bar.end_time.timestamp() >= target_ts]
        if not eligible:
            return None
        return (eligible[0].close / entry_price - Decimal("1")) * Decimal("100")

    plus_one = plus_two = None
    if risk is not None and risk > 0:
        plus_one = False
        plus_two = False
        one = entry_price + risk
        two = entry_price + risk * Decimal("2")
        for bar in regular:
            if invalidation_price is not None and bar.low <= invalidation_price:
                break
            if bar.high >= one:
                plus_one = True
            if bar.high >= two:
                plus_two = True
                break
    positive = plus_two if plus_two is not None else (mfe >= Decimal("5"))
    return OpportunityEpisodeOutcome(
        arm=arm,
        instrument_id=instrument_id,
        episode_id=episode_id,
        started_at=started_at,
        ended_at=ended_at,
        setup_family=setup_family,
        catalyst_persistence_class=catalyst_persistence_class,
        entered=entered,
        entry_price=entry_price,
        invalidation_price=invalidation_price,
        target_1=target_1,
        return_1m_pct=forward_return(1),
        return_3m_pct=forward_return(3),
        return_5m_pct=forward_return(5),
        return_10m_pct=forward_return(10),
        return_20m_pct=forward_return(20),
        return_60m_pct=forward_return(60),
        mfe_pct=mfe,
        mae_pct=mae,
        peak_r=peak_r,
        plus_one_r_before_minus_one_r=plus_one,
        plus_two_r_before_minus_one_r=plus_two,
        positive_opportunity=positive,
    )


__all__ = [
    "AI_SHADOW_V2_VERSION",
    "AIShadowV2AlphaDecision",
    "AIShadowV2Analyzer",
    "AIShadowV2Arm",
    "AIShadowV2BatchResponse",
    "AIShadowV2State",
    "CatalystInfluence",
    "CatalystSemanticResponse",
    "CatalystIntelligenceAnalyzer",
    "CatalystIntelligenceSnapshot",
    "DeterministicRiskGeometry",
    "MarketStructureSnapshot",
    "OpportunityEpisodeOutcome",
    "StructuredAlphaTrigger",
    "alpha_prompt_snapshot",
    "build_market_structure_snapshot",
    "calibrate_persistence_prior",
    "derive_catalyst_influence",
    "deterministic_evidence_quality",
    "deterministic_risk_geometry",
    "evaluate_opportunity_episode",
    "evidence_fingerprint",
    "trigger_satisfied",
]
