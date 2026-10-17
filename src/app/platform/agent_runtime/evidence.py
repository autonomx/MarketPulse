"""Evidence classification, source resolution, capability compilation, and evaluation.

Pi owns execution strategy. Omnix owns the evidence contract, authority issued to
the run, provenance receipts, and completion acceptance.
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception
from app.config.env import env_str as _env_str

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable, cast
from urllib.parse import urlparse

from .active_objective import normalize_objective_relation
from .contracts import (
    EvidenceCoverage,
    EvidenceDecision,
    EvidencePolicy,
    EvidenceReceipt,
    EvidenceRequirement,
    EvidenceRequirementEvaluation,
    EvidenceSet,
    EvidenceSourceOption,
    RequestModeCandidate,
    RequestModeSelection,
    RetrievalPolicy,
    SubjectRef,
)
from .profiles import AgentProfile, profile_external_ceiling

_CURRENT = re.compile(r"\b(?:today|tomorrow|tonight|current(?:ly)?|latest|right now|this (?:morning|afternoon|evening|week|month|year)|next (?:morning|day|week|month)|recent|newest|news)\b", re.I)
_NO_EXTERNAL = re.compile(
    r"\b(?:"
    r"without (?:using )?(?:the )?(?:internet|web)|"
    r"without (?:using )?(?:any\s+|anything\s+)?external(?:\s+(?:services?|sources?|tools?))?|"
    r"(?:do not|don't|never) (?:use|access) (?:the )?(?:internet|web)|"
    r"(?:do not|don't|never) (?:use|access) (?:any\s+|anything\s+)?external(?:\s+(?:services?|sources?|tools?))?|"
    r"(?:do not|don't|never) search (?:the )?(?:internet|web)|"
    r"(?:do not|don't|never) browse(?: (?:the )?(?:internet|web))?|"
    r"from memory only"
    r")\b",
    re.I,
)
_SOURCE_REQUEST = re.compile(r"\b(?:find|give|provide|include|cite)\b.{0,40}\b(?:sources?|citations?|evidence)\b|\bresearch\b", re.I)
_MARKET = re.compile(r"\b(?:stock|stocks|ticker|market|shares?|equity|nvda|gme|tsla|\$[A-Z]{1,5})\b", re.I)
_QUOTE = re.compile(r"\b(?:price|quote|trading at|last trade|bid|ask)\b", re.I)
_MARKET_NEWS = re.compile(
    r"\b(?:news|catalysts?|headlines?|developments?|why (?:is|did)|moving|momentum)\b",
    re.I,
)
_CI = re.compile(r"\b(?:ci|github actions?|workflow checks?|checks? (?:failed|passing|status)|build status)\b", re.I)
_REPO = re.compile(r"\b(?:repo(?:sitory)?|github|pull request|\bpr\s*#?\d+|branch|commit)\b", re.I)
_WEATHER = re.compile(r"\b(?:weather|raining|rain|snow|temperature|forecast)\b", re.I)
_HOME = re.compile(r"\b(?:home|lamp|light|plug|outlet|thermostat|kasa)\b", re.I)
_CALENDAR = re.compile(r"\b(?:calendar|meeting|appointment|schedule)\b", re.I)
_EMAIL = re.compile(r"\b(?:gmail|email|inbox|message)\b", re.I)
_FILINGS = re.compile(r"\b(?:10-k|10-q|8-k|sec filing|company filing|filings)\b", re.I)
_RELEASE = re.compile(r"\b(?:release|version|changelog|released)\b", re.I)
_WORKSPACE_MUTATION = re.compile(
    r"\b(?:implement|fix|refactor|edit|modify|patch|write|change|update|create|delete|remove|"
    r"add|apply|make|center|centre|align|move|style|restyle|increase|decrease|"
    r"reduce|expand|widen|narrow|adjust)\b",
    re.I,
)
_WORKSPACE_EXECUTION = re.compile(
    r"\b(?:run|test|pytest|vitest|typecheck|lint|debug|diagnose|investigate|reproduce|verify)\b",
    re.I,
)
_HOME_MUTATION = re.compile(
    r"\b(?:turn|set|adjust|lower|raise|prepare|apply|dim|brighten)\b|"
    r"\b(?:shut|switch)\s+(?:on|off)\b",
    re.I,
)
_EMAIL_SEND = re.compile(r"\b(?:send|reply|forward)\b.{0,80}\b(?:email|gmail|message)\b", re.I)
_EMAIL_DRAFT = re.compile(r"\b(?:draft|compose|write)\b.{0,80}\b(?:email|gmail|message)\b", re.I)
_CALENDAR_CREATE = re.compile(r"\b(?:schedule|create|book|add)\b.{0,80}\b(?:meeting|appointment|calendar event)\b", re.I)
_WORKSPACE_MUTATION_FORBIDDEN = re.compile(
    r"\b(?:do not|don't|never)\s+(?:(?:please|make\s+any)\s+)?"
    r"(?:edit|modify|write|change|patch|update|create|delete|remove|changes?)\b|"
    r"\b(?:without changing|read[- ]only|no changes?)\b",
    re.I,
)
_HOME_MUTATION_FORBIDDEN = re.compile(
    r"\b(?:do not|don't|never)\s+(?:please\s+)?(?:turn|set|adjust|lower|raise|apply)\b",
    re.I,
)
_EMAIL_SEND_FORBIDDEN = re.compile(
    r"\b(?:do not|don't|never)\s+(?:please\s+)?(?:send|reply|forward)\b",
    re.I,
)
_EMAIL_DRAFT_FORBIDDEN = re.compile(
    r"\b(?:do not|don't|never)\s+(?:please\s+)?(?:draft|compose|write)\b",
    re.I,
)
_CALENDAR_CREATE_FORBIDDEN = re.compile(
    r"\b(?:do not|don't|never)\s+(?:please\s+)?(?:schedule|create|book|add)\b",
    re.I,
)
_CONCEPTUAL = re.compile(r"^(?:what is|what are|explain|describe|teach me|how does|why does|compare)\b", re.I)
_TICKER_DOLLAR = re.compile(r"\$([A-Z]{1,5})\b")
_TICKER_BEFORE_CONTEXT = re.compile(
    r"\b([A-Z]{1,5})\b(?=.{0,32}\b(?:stock|shares?|ticker|price|quote|trading at|sec|filings?|catalysts?|news|headlines?)\b)"
)
_TICKER_AFTER_CONTEXT = re.compile(
    r"\b(?i:stock|ticker|price|quote|filing|catalyst|news)\s+"
    r"(?:(?i:of|for)\s+)?([A-Z]{1,5})\b"
)
_NON_TICKER_TOKENS = {
    "SEC",
    "CEO",
    "CFO",
    "CTO",
    "NEWS",
    "STOCK",
    "PRICE",
    "QUOTE",
    "IPO",
    "ETF",
    "USD",
    "USA",
}
_PR = re.compile(r"\bPR\s*#?(\d+)\b", re.I)

TRUST_RANK = {"general": 0, "reputable": 1, "primary": 2, "authoritative": 3}
DEFAULT_FRESHNESS_SECONDS = {
    "market_quote": 60,
    "market_status": 60,
    "repo_ci_state": 300,
    "weather_state": 1800,
    "breaking_news": 21600,
    "market_news": 21600,
    "software_release": 86400,
    "company_leadership": 86400,
    "general_current_web": 86400,
    "company_filing": 86400,
    "repo_contents": 300,
    "home_state": 60,
    "home_energy": 300,
    "calendar_state": 300,
    "email_state": 300,
}

# Returned by an extracted step that did not settle its caller.
_CONTINUE = object()


def freshness_max_age_seconds(source_class: str) -> int | None:
    """Resolve source-specific freshness from policy configuration.

    Environment overrides keep classification semantic while making acceptance
    freshness deploy-time policy rather than classifier code.
    """
    key = "OMNIX_AGENT_EVIDENCE_MAX_AGE_" + re.sub(r"[^A-Z0-9]+", "_", source_class.upper())
    raw = str(_env_str(key, "") or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise EvidenceCompilationError(
                "freshness_policy_unsatisfiable",
                f"{key} must be a positive integer",
            ) from exc
        if value <= 0:
            raise EvidenceCompilationError(
                "freshness_policy_unsatisfiable",
                f"{key} must be a positive integer",
            )
        return value
    return DEFAULT_FRESHNESS_SECONDS.get(source_class)


SOURCE_CAPABILITIES: dict[str, tuple[str, str]] = {
    "general_current_web": ("research.web_search", "reputable"),
    "breaking_news": ("research.web_search", "reputable"),
    "market_news": ("research.web_search", "reputable"),
    "company_filing": ("research.web_search", "primary"),
    "software_release": ("research.web_search", "primary"),
    "repo_contents": ("github.read_repo", "authoritative"),
    "repo_ci_state": ("github.inspect_ci", "authoritative"),
    "home_state": ("home.get_state", "authoritative"),
    "home_energy": ("home.get_energy", "authoritative"),
    "calendar_state": ("calendar.read_availability", "authoritative"),
    "email_state": ("gmail.read_email", "authoritative"),
    # Intentionally no web fallback: a quote/weather request fails compilation
    # until an authoritative provider capability exists in the profile ceiling.
    "market_quote": ("trading.market_quote", "authoritative"),
    "market_status": ("market.status", "authoritative"),
    "weather_state": ("weather.current", "authoritative"),
}

_SEMANTIC_SOURCE_CLASSES = frozenset(SOURCE_CAPABILITIES)


_HERMES_EVIDENCE_SCHEMA = {
    "requirement": "none|optional|required",
    "external_access": "allowed|forbidden",
    "requirements": [
        {
            "source_class": (
                "general_current_web|breaking_news|market_news|company_filing|"
                "software_release|repo_contents|repo_ci_state|home_state|"
                "home_energy|calendar_state|email_state|market_quote|"
                "market_status|weather_state"
            ),
            "freshness": "timeless|current",
            "trust_floor": "authoritative|primary|reputable|general",
            "fallback_policy": "fail_closed|allow_fallback",
        }
    ],
    "user_visible_attribution": "none|when_used|required",
    "retrieval_strategy": "lookup|bounded|adaptive",
    "confidence": "number 0..1",
    "reason": "short string",
}


def _hermes_evidence_decision(client: Any, task: str, profile_id: str) -> dict[str, Any]:
    """Proposal-only semantic evidence classification for ambiguous Agent tasks.

    The sidecar never executes tools. Omnix validates the returned source
    classes against the selected profile ceiling before issuing authority.
    """
    from app.providers import ChatMessage
    from app.providers.hermes_client import JsonObject

    decision = client.structured(
        [
            ChatMessage(
                role="system",
                content=(
                    "You are a non-executing evidence-policy adviser. Return one JSON object "
                    "matching the supplied schema. Determine whether the task needs external "
                    "evidence, what semantic source classes are required, freshness/trust, and "
                    "attribution. Never execute tools, never name unlisted source classes, and "
                    "never grant capabilities."
                ),
            ),
            ChatMessage(
                role="user",
                content=json.dumps(
                    {"task": task, "profile": profile_id, "schema": _HERMES_EVIDENCE_SCHEMA},
                    sort_keys=True,
                ),
            ),
        ],
        output_model=JsonObject,
        contract_id="hermes.evidence_decision",
        json_mode=True,
        timeout=min(client.timeout, 15.0),
        error="Hermes did not return a valid evidence decision",
    )
    return decision.model_dump()


def _semantic_evidence_adviser(task: str, profile_id: str) -> EvidenceDecision | None:
    enabled = str(_env_str("OMNIX_AGENT_EVIDENCE_SEMANTIC_ADVISER", "") or "").strip().casefold()
    if enabled not in {"1", "true", "yes", "hermes"}:
        return None
    try:
        from app.providers.hermes_client import HermesSidecarClient

        client = HermesSidecarClient(
            base_url=str(_env_str("OMNIX_HERMES_URL", "http://127.0.0.1:8642")),
            api_key=_env_str("OMNIX_HERMES_API_KEY"),
            timeout=float(_env_str("OMNIX_AGENT_EVIDENCE_HERMES_TIMEOUT", "15")),
        )
        payload = _hermes_evidence_decision(client, task, profile_id)
    except Exception as exc:
        # Advisory failures never weaken the policy. The caller falls back to
        # conservative Omnix classification.
        log_recovered_exception("advisory evidence classification", exc)
        return None

    requirement = str(payload.get("requirement") or "none").casefold()
    if requirement not in {"none", "optional", "required"}:
        return None
    external_access = str(payload.get("external_access") or "allowed").casefold()
    if external_access not in {"allowed", "forbidden"}:
        external_access = "allowed"
    attribution = str(payload.get("user_visible_attribution") or "when_used").casefold()
    if attribution not in {"none", "when_used", "required"}:
        attribution = "when_used"
    requirements: list[EvidenceRequirement] = []
    for row in payload.get("requirements") or []:
        if not isinstance(row, dict):
            continue
        source_class = str(row.get("source_class") or "").strip()
        if source_class not in _SEMANTIC_SOURCE_CLASSES:
            continue
        freshness = str(row.get("freshness") or "timeless").casefold()
        if freshness not in {"timeless", "current"}:
            freshness = "timeless"
        trust = str(row.get("trust_floor") or SOURCE_CAPABILITIES[source_class][1]).casefold()
        if trust not in TRUST_RANK:
            trust = SOURCE_CAPABILITIES[source_class][1]
        fallback = str(row.get("fallback_policy") or "fail_closed").casefold()
        if fallback not in {"fail_closed", "allow_fallback"}:
            fallback = "fail_closed"
        requirements.append(
            _requirement(
                task,
                source_class,
                freshness=freshness,
                trust=trust,
                fallback=fallback,
            )
        )
    if requirement == "required" and not requirements:
        return None
    confidence = payload.get("confidence", 0.75)
    try:
        parsed_confidence = max(0.0, min(float(confidence), 1.0))
    except (TypeError, ValueError):
        parsed_confidence = 0.75
    strategy = str(payload.get("retrieval_strategy") or "adaptive").casefold()
    if strategy not in {"lookup", "bounded", "adaptive"}:
        strategy = "adaptive"
    return EvidenceDecision(
        policy=EvidencePolicy(
            requirement=cast(Any, requirement),
            external_access=cast(Any, external_access),
            requirements=requirements,
            user_visible_attribution=cast(Any, attribution),
            retrieval=RetrievalPolicy(strategy=cast(Any, strategy)),
        ),
        confidence=parsed_confidence,
        reason=str(payload.get("reason") or "hermes_semantic_evidence_adviser")[:240],
        classifier="semantic",
    )


class EvidenceCompilationError(ValueError):
    def __init__(self, code: str, message: str, *, requirement_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.requirement_id = requirement_id


@dataclass(frozen=True)
class CompiledEvidence:
    decision: EvidenceDecision
    required_local: tuple[str, ...]
    required_external: tuple[str, ...]
    external_groups: tuple[tuple[str, ...], ...] = ()


def resolve_request_mode(
    content: str,
    *,
    turn_research_mode: str | None,
    persistent_agent: bool,
    classifier_lane: str,
) -> RequestModeSelection:
    text = str(content or "").strip()
    candidates: list[RequestModeCandidate] = []
    explicit_research: str | None = None
    if re.match(r"^/(?:search|quick(?:-search)?)\b", text, re.I):
        explicit_research = "quick_research"
    elif re.match(r"^/research\s+quick\b", text, re.I):
        explicit_research = "quick_research"
    elif re.match(r"^/(?:deep(?:-research)?)\b", text, re.I) or re.match(
        r"^/research(?:\s+deep)?\b",
        text,
        re.I,
    ):
        explicit_research = "deep_research"
    if explicit_research is not None:
        candidates.append(
            RequestModeCandidate(
                mode=cast(Any, explicit_research),
                source="explicit_command",
                priority=500,
            )
        )
    if re.match(r"^(?:/agent\b|/agnet\b|agent[,:]\s|use (?:the )?agent\b)", text, re.I):
        candidates.append(RequestModeCandidate(mode="agent", source="explicit_command", priority=500))
    normalized = str(turn_research_mode or "").strip().casefold()
    if normalized == "quick":
        candidates.append(RequestModeCandidate(mode="quick_research", source="turn_setting", priority=400))
    elif normalized == "deep":
        candidates.append(RequestModeCandidate(mode="deep_research", source="turn_setting", priority=400))
    if persistent_agent:
        candidates.append(RequestModeCandidate(mode="agent", source="persistent_setting", priority=300))
    if classifier_lane == "agent":
        candidates.append(RequestModeCandidate(mode="agent", source="classifier", priority=200))
    elif classifier_lane in {"chat", "direct", "workflow"}:
        candidates.append(RequestModeCandidate(mode="auto", source="classifier", priority=200))
    candidates.append(RequestModeCandidate(mode="chat", source="default", priority=0))
    ordered = sorted(enumerate(candidates), key=lambda row: (-row[1].priority, row[0]))
    winner = ordered[0][1]
    suppressed = [row for _, row in ordered[1:]]
    return RequestModeSelection(
        mode=winner.mode,
        source=winner.source,
        priority=winner.priority,
        suppressed=suppressed,
    )


def _extract_ticker(task: str) -> str | None:
    text = str(task or "")
    for pattern in (_TICKER_DOLLAR, _TICKER_AFTER_CONTEXT, _TICKER_BEFORE_CONTEXT):
        for match in pattern.finditer(text):
            ticker = str(match.group(1)).upper()
            if ticker in _NON_TICKER_TOKENS:
                continue
            return ticker
    return None


def _security_subject(ticker: str) -> SubjectRef:
    canonical_id = f"{ticker}:US"
    qualifiers: dict[str, Any] = {"ticker": ticker}
    try:
        from app.runtime.ports import optional

        from .contracts import SECURITY_INSTRUMENTS

        instruments = optional(SECURITY_INSTRUMENTS)
        instrument_id = instruments.equity_instrument_id(ticker) if instruments is not None else None
        if instrument_id:
            canonical_id = instrument_id
            qualifiers["instrument_id"] = instrument_id
    except Exception as exc:
        log_recovered_exception("security instrument resolution", exc, level="DEBUG")
        pass
    return SubjectRef(
        type="security",
        canonical_id=canonical_id,
        display_name=ticker,
        qualifiers=qualifiers,
    )


def resolve_subject(task: str, source_class: str) -> SubjectRef | None:
    text = str(task or "")
    if source_class in {"market_quote", "market_news", "company_filing"}:
        ticker = _extract_ticker(text)
        if ticker:
            return _security_subject(ticker)
    if source_class == "market_status":
        return SubjectRef(
            type="market",
            canonical_id="us_equities",
            display_name="US equities market",
        )
    if source_class in {"repo_ci_state", "repo_contents"}:
        pr = _PR.search(text)
        qualifiers = {"pull_request": int(pr.group(1))} if pr else {}
        return SubjectRef(
            type="repository_ref",
            canonical_id="current_repository",
            display_name="current repository",
            qualifiers=qualifiers,
        )
    if source_class == "weather_state":
        return SubjectRef(type="location", canonical_id="user_location", display_name="user location")
    if source_class in {"home_state", "home_energy"}:
        return SubjectRef(type="home", canonical_id="current_home", display_name="current home")
    if source_class == "calendar_state":
        return SubjectRef(type="calendar", canonical_id="primary_calendar", display_name="primary calendar")
    if source_class == "email_state":
        return SubjectRef(type="mailbox", canonical_id="primary_mailbox", display_name="primary mailbox")
    return None


def evidence_coverage_from_subject(subject: SubjectRef | None) -> EvidenceCoverage | None:
    if subject is None:
        return None
    return EvidenceCoverage(
        kind=subject.type,
        subject=subject,
        coverage_key=f"{subject.type}:{subject.canonical_id}",
    )


def evidence_coverage_key(
    coverage: EvidenceCoverage | None,
    *,
    subject: SubjectRef | None = None,
) -> str:
    resolved = coverage or evidence_coverage_from_subject(subject)
    if resolved is None:
        return "unbound"
    if resolved.coverage_key:
        return resolved.coverage_key
    if resolved.subject is not None:
        return f"{resolved.subject.type}:{resolved.subject.canonical_id}"
    return f"{resolved.kind}:unbound"


def _evidence_temporal_identity(requirement: EvidenceRequirement) -> str:
    """Keep point-in-time obligations distinct from current/timeless facts."""

    if requirement.freshness != "as_of_date":
        return "nonhistorical"
    if requirement.as_of_date is None:
        return "as_of:missing"
    value = requirement.as_of_date
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return f"as_of:{value.isoformat()}"


def evidence_obligation_key(
    requirement: EvidenceRequirement,
) -> tuple[str, str, str, str]:
    return (
        requirement.source_class,
        evidence_coverage_key(requirement.coverage, subject=requirement.subject),
        requirement.purpose,
        _evidence_temporal_identity(requirement),
    )


def _merge_source_options(
    left: list[EvidenceSourceOption],
    right: list[EvidenceSourceOption],
) -> list[EvidenceSourceOption]:
    merged: dict[tuple[str, str | None], EvidenceSourceOption] = {}
    for option in [*left, *right]:
        key = (option.source_class, option.provider_hint)
        existing = merged.get(key)
        if existing is None:
            merged[key] = option
            continue
        trust = (
            option.trust_floor
            if TRUST_RANK.get(option.trust_floor, 0) > TRUST_RANK.get(existing.trust_floor, 0)
            else existing.trust_floor
        )
        merged[key] = existing.model_copy(
            update={
                "trust_floor": trust,
                "preference": min(existing.preference, option.preference),
            }
        )
    return sorted(
        merged.values(),
        key=lambda option: (option.preference, option.source_class, option.provider_hint or ""),
    )


def merge_evidence_requirements(
    requirements: list[EvidenceRequirement],
) -> list[EvidenceRequirement]:
    """Merge only the same semantic obligation, strengthening policy monotonically."""

    merged: list[EvidenceRequirement] = []
    indexes: dict[tuple[str, str, str, str], int] = {}
    for requirement in requirements:
        key = evidence_obligation_key(requirement)
        existing_index = indexes.get(key)
        if existing_index is None:
            indexes[key] = len(merged)
            merged.append(requirement)
            continue

        existing = merged[existing_index]
        trust = (
            requirement.trust_floor
            if TRUST_RANK.get(requirement.trust_floor, 0) > TRUST_RANK.get(existing.trust_floor, 0)
            else existing.trust_floor
        )
        if "current" in {existing.freshness, requirement.freshness}:
            freshness = "current"
        elif "as_of_date" in {existing.freshness, requirement.freshness}:
            freshness = "as_of_date"
        else:
            freshness = "timeless"
        fallback = (
            "fail_closed"
            if "fail_closed" in {existing.fallback_policy, requirement.fallback_policy}
            else "allow_fallback"
        )
        ages = [
            age
            for age in (existing.max_age_seconds, requirement.max_age_seconds)
            if age is not None
        ]
        as_of_dates = [
            value
            for value in (existing.as_of_date, requirement.as_of_date)
            if value is not None
        ]
        merged[existing_index] = existing.model_copy(
            update={
                "subject": existing.subject or requirement.subject,
                "coverage": existing.coverage or requirement.coverage,
                "trust_floor": trust,
                "freshness": freshness,
                "fallback_policy": fallback,
                "minimum_matches": max(existing.minimum_matches, requirement.minimum_matches),
                "acceptable_sources": _merge_source_options(
                    existing.acceptable_sources,
                    requirement.acceptable_sources,
                ),
                "max_age_seconds": min(ages) if ages else None,
                "as_of_date": max(as_of_dates) if as_of_dates else None,
            }
        )
    return merged


def _requirement(
    task: str,
    source_class: str,
    *,
    freshness: str = "current",
    trust: str | None = None,
    fallback: str = "fail_closed",
) -> EvidenceRequirement:
    _, default_trust = SOURCE_CAPABILITIES.get(source_class, ("", "reputable"))
    source_trust = trust or default_trust
    subject = resolve_subject(task, source_class)
    return EvidenceRequirement(
        id=f"evidence-{source_class}",
        source_class=source_class,
        subject=subject,
        coverage=evidence_coverage_from_subject(subject),
        freshness=cast(Any, freshness),
        trust_floor=cast(Any, source_trust),
        acceptable_sources=[
            EvidenceSourceOption(source_class=source_class, trust_floor=cast(Any, source_trust), preference=0)
        ],
        fallback_policy=cast(Any, fallback),
        max_age_seconds=freshness_max_age_seconds(source_class) if freshness == "current" else None,
    )


def evidence_decision_from_semantic(task: str, semantic_decision: object) -> EvidenceDecision:
    """Translate an untrusted semantic interpretation into an evidence proposal.

    This function does not issue authority. Source classes are allowlisted here and
    later compiled against the selected profile ceiling and trust/freshness policy.
    """

    text = " ".join(str(task or "").split())
    requirements: list[EvidenceRequirement] = []

    def add(
        source_class: str,
        *,
        freshness: str = "current",
        trust: str | None = None,
        fallback: str = "fail_closed",
    ) -> None:
        if source_class not in SOURCE_CAPABILITIES:
            return

        # Semantic trust is advisory and must not make a source class
        # deterministically impossible to compile. Bound the model-proposed
        # floor by the strongest trust level that this source class can
        # actually deliver. User/deterministic hard floors are merged later
        # and remain authoritative.
        requested_trust = trust if trust in TRUST_RANK else None
        source_trust = SOURCE_CAPABILITIES[source_class][1]
        bounded_trust = requested_trust
        if (
            requested_trust is not None
            and TRUST_RANK.get(requested_trust, 0) > TRUST_RANK.get(source_trust, 0)
        ):
            bounded_trust = source_trust

        requirements.append(
            _requirement(
                text,
                source_class,
                freshness=freshness if freshness in {"timeless", "current"} else "current",
                trust=bounded_trust,
                fallback=fallback if fallback in {"fail_closed", "allow_fallback"} else "fail_closed",
            )
        )

    for row in list(getattr(semantic_decision, "evidence_requirements", []) or []):
        add(
            str(getattr(row, "source_class", "") or ""),
            freshness=str(getattr(row, "freshness", "current") or "current").casefold(),
            trust=str(getattr(row, "trust_floor", "") or "").casefold() or None,
            fallback=str(getattr(row, "fallback_policy", "fail_closed") or "fail_closed").casefold(),
        )

    actions = {
        str(value)
        for value in (getattr(semantic_decision, "action_intents", []) or [])
        if str(value)
    }
    if actions & {"home_read", "home_mutate"}:
        add("home_state", trust="authoritative")
    if actions & {"email_read", "email_draft", "email_send"}:
        add("email_state", trust="authoritative")
    if actions & {"calendar_read", "calendar_create"}:
        add("calendar_state", trust="authoritative")
    if "market_read" in actions and not any(
        requirement.source_class in {"market_quote", "market_news", "company_filing", "market_status"}
        for requirement in requirements
    ):
        add("market_news", trust="reputable", fallback="allow_fallback")
    if "research_read" in actions and not requirements:
        add(
            "general_current_web",
            freshness="timeless",
            trust="reputable",
            fallback="allow_fallback",
        )

    try:
        confidence = max(0.0, min(float(getattr(semantic_decision, "confidence", 0.75)), 1.0))
    except (TypeError, ValueError):
        confidence = 0.75
    reason = str(getattr(semantic_decision, "reason", "semantic_intent") or "semantic_intent")[:240]
    requirements = merge_evidence_requirements(requirements)
    return EvidenceDecision(
        policy=EvidencePolicy(
            requirement="required" if requirements else "none",
            external_access="allowed",
            requirements=requirements,
            user_visible_attribution="when_used",
            retrieval=RetrievalPolicy(strategy="adaptive"),
        ),
        confidence=confidence,
        reason=f"semantic_intent:{reason}",
        classifier="semantic",
    )


def _merge_requirements(
    primary: list[EvidenceRequirement],
    floors: list[EvidenceRequirement],
) -> list[EvidenceRequirement]:
    return merge_evidence_requirements([*primary, *floors])


def classify_evidence(
    task: str,
    *,
    profile_id: str,
    semantic_adviser: Callable[[str, str], EvidenceDecision | None] | None = None,
) -> EvidenceDecision:
    text = " ".join(str(task or "").split())
    external_forbidden = bool(_NO_EXTERNAL.search(text))
    attribution = "required" if re.search(r"\b(?:sourced|with sources|cite sources|citations)\b", text, re.I) else "when_used"
    requirements: list[EvidenceRequirement] = []
    hard_requirement_sources: set[str] = set()

    def add_requirement(
        source_class: str,
        *,
        freshness: str = "current",
        trust: str | None = None,
        fallback: str = "fail_closed",
        hard: bool = True,
    ) -> None:
        requirements.append(
            _requirement(
                text,
                source_class,
                freshness=freshness,
                trust=trust,
                fallback=fallback,
            )
        )
        if hard:
            hard_requirement_sources.add(source_class)

    market_signal = bool(_MARKET.search(text) or _extract_ticker(text))
    if market_signal and _QUOTE.search(text):
        add_requirement("market_quote", trust="authoritative")
    if _CI.search(text):
        add_requirement("repo_ci_state", trust="authoritative")
    if _WEATHER.search(text) and (_CURRENT.search(text) or re.search(r"\boutside\b", text, re.I)):
        add_requirement("weather_state", trust="authoritative")
    if profile_id == "house" and re.search(r"\b(?:check|inspect|status|state|energy)\b", text, re.I):
        source = "home_energy" if "energy" in text.casefold() else "home_state"
        add_requirement(source, trust="authoritative")
    if profile_id == "personal-assistant" and _CALENDAR.search(text):
        add_requirement("calendar_state", trust="authoritative")
    if profile_id == "personal-assistant" and _EMAIL.search(text):
        add_requirement("email_state", trust="authoritative")
    if _FILINGS.search(text):
        add_requirement("company_filing", trust="primary")
    if _REPO.search(text) and _CURRENT.search(text) and not _CI.search(text):
        add_requirement("repo_contents", trust="authoritative")
    if _RELEASE.search(text) and _CURRENT.search(text):
        add_requirement("software_release", trust="primary", fallback="allow_fallback")
    if (
        market_signal
        and (_CURRENT.search(text) or _SOURCE_REQUEST.search(text))
        and (_MARKET_NEWS.search(text) or (not _QUOTE.search(text) and not _FILINGS.search(text)))
    ):
        add_requirement("market_news", trust="reputable", fallback="allow_fallback")
    if not requirements and _CURRENT.search(text):
        add_requirement(
            "general_current_web",
            trust="reputable",
            fallback="allow_fallback",
            hard=False,
        )
    elif not requirements and _SOURCE_REQUEST.search(text):
        add_requirement(
            "general_current_web",
            freshness="timeless",
            trust="reputable",
            fallback="allow_fallback",
            hard=False,
        )

    requirements = merge_evidence_requirements(requirements)
    adviser = semantic_adviser or _semantic_evidence_adviser
    advised = adviser(text, profile_id)
    potentially_current = profile_id in {"trading-research", "house", "personal-assistant"} and bool(
        market_signal or _HOME.search(text) or _CALENDAR.search(text) or _EMAIL.search(text)
    )

    outcome = _advised_evidence_decision(advised, external_forbidden, attribution, requirements, hard_requirement_sources, potentially_current, market_signal, text)
    if outcome is not _CONTINUE:
        return outcome

    return _deterministic_evidence_decision(requirements, external_forbidden, attribution, text, potentially_current, market_signal)


def _advised_evidence_decision(advised, external_forbidden, attribution, requirements, hard_requirement_sources, potentially_current, market_signal, text):
    """Combine the semantic adviser with the deterministic hard floors; a low-confidence adviser keeps a conservative floor."""
    if advised is not None:
        advised_policy = advised.policy.model_copy(
            update={
                "external_access": (
                    "forbidden" if external_forbidden else advised.policy.external_access
                ),
                "user_visible_attribution": (
                    "required"
                    if attribution == "required"
                    else advised.policy.user_visible_attribution
                ),
            }
        )

        if advised.confidence >= 0.60:
            semantic_requirements = (
                list(advised_policy.requirements)
                if advised_policy.requirement == "required"
                else []
            )
            hard_floors = [
                requirement
                for requirement in requirements
                if requirement.source_class in hard_requirement_sources
            ]
            merged = _merge_requirements(semantic_requirements, hard_floors)
            if merged:
                advised_policy = advised_policy.model_copy(
                    update={
                        "requirement": "required",
                        "requirements": merged,
                    }
                )
            else:
                advised_policy = advised_policy.model_copy(update={"requirements": []})
            return EvidenceDecision(
                policy=advised_policy,
                confidence=advised.confidence,
                reason=(
                    f"hybrid:{advised.reason}"
                    if hard_floors
                    else advised.reason
                )[:240],
                classifier="semantic" if hard_floors else advised.classifier,
            )

        if requirements:
            policy = EvidencePolicy(
                requirement="required",
                external_access="forbidden" if external_forbidden else "allowed",
                requirements=requirements,
                user_visible_attribution=attribution,
            )
            return EvidenceDecision(
                policy=policy,
                confidence=0.98 if any(r.source_class != "general_current_web" for r in requirements) else 0.92,
                reason=f"deterministic_floor:{','.join(r.source_class for r in requirements)}",
                classifier="deterministic",
            )

        if potentially_current and advised_policy.requirement != "required":
            source = (
                "market_news" if market_signal
                else "home_state" if _HOME.search(text)
                else "calendar_state" if _CALENDAR.search(text)
                else "email_state"
            )
            return EvidenceDecision(
                policy=EvidencePolicy(
                    requirement="required",
                    external_access="forbidden" if external_forbidden else "allowed",
                    requirements=[
                        _requirement(
                            text,
                            source,
                            fallback="allow_fallback" if source == "market_news" else "fail_closed",
                        )
                    ],
                    user_visible_attribution=attribution,
                ),
                confidence=advised.confidence,
                reason=f"semantic_low_confidence_conservative_floor:{source}",
                classifier="conservative",
            )
        return advised.model_copy(update={"policy": advised_policy})
    return _CONTINUE


def _deterministic_evidence_decision(requirements, external_forbidden, attribution, text, potentially_current, market_signal):
    """The evidence policy from deterministic floors alone: required sources, timeless concepts, or a conservative current domain."""
    if requirements:
        policy = EvidencePolicy(
            requirement="required",
            external_access="forbidden" if external_forbidden else "allowed",
            requirements=requirements,
            user_visible_attribution=attribution,
        )
        return EvidenceDecision(
            policy=policy,
            confidence=0.98 if any(r.source_class != "general_current_web" for r in requirements) else 0.92,
            reason=f"required:{','.join(r.source_class for r in requirements)}",
            classifier="deterministic",
        )

    if _CONCEPTUAL.search(text):
        return EvidenceDecision(
            policy=EvidencePolicy(
                requirement="none",
                external_access="forbidden" if external_forbidden else "allowed",
                user_visible_attribution=attribution,
            ),
            confidence=0.95,
            reason="timeless_conceptual_request",
            classifier="deterministic",
        )

    if potentially_current:
        source = (
            "market_news" if market_signal
            else "home_state" if _HOME.search(text)
            else "calendar_state" if _CALENDAR.search(text)
            else "email_state"
        )
        return EvidenceDecision(
            policy=EvidencePolicy(
                requirement="required",
                external_access="forbidden" if external_forbidden else "allowed",
                requirements=[
                    _requirement(
                        text,
                        source,
                        fallback="allow_fallback" if source == "market_news" else "fail_closed",
                    )
                ],
                user_visible_attribution=attribution,
            ),
            confidence=0.55,
            reason=f"conservative_current_domain:{source}",
            classifier="conservative",
        )

    return EvidenceDecision(
        policy=EvidencePolicy(
            requirement="none",
            external_access="forbidden" if external_forbidden else "allowed",
            user_visible_attribution=attribution,
        ),
        confidence=0.75,
        reason="model_knowledge_sufficient",
        classifier="conservative",
    )


def _requirement_source_candidates(requirement: EvidenceRequirement) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = [(0, requirement.source_class, requirement.trust_floor)]
    rows.extend(
        (option.preference, option.source_class, option.trust_floor)
        for option in requirement.acceptable_sources
        if option.source_class != requirement.source_class
    )
    return sorted(rows, key=lambda row: row[0])


def capability_for_requirement(requirement: EvidenceRequirement) -> tuple[str, str]:
    for _preference, source_class, option_trust in _requirement_source_candidates(requirement):
        resolved = SOURCE_CAPABILITIES.get(source_class)
        if resolved is None:
            continue
        capability, source_trust = resolved
        effective_trust = (
            option_trust
            if TRUST_RANK.get(option_trust, 0) <= TRUST_RANK.get(source_trust, 0)
            else source_trust
        )
        return capability, effective_trust
    raise EvidenceCompilationError(
        "evidence_required_but_unavailable",
        f"no capability mapping exists for evidence source {requirement.source_class}",
        requirement_id=requirement.id,
    )


def _resolve_requirement_capabilities(
    profile: AgentProfile,
    requirement: EvidenceRequirement,
) -> list[tuple[str, str, str]]:
    """Return all policy-permitted capability/source alternatives in preference order."""
    ceiling = profile_external_ceiling(profile)
    candidates = _requirement_source_candidates(requirement)
    if requirement.fallback_policy == "fail_closed":
        candidates = candidates[:1]
    available: list[tuple[str, str, str]] = []
    unavailable: list[str] = []
    for _preference, source_class, option_trust in candidates:
        resolved = SOURCE_CAPABILITIES.get(source_class)
        if resolved is None:
            unavailable.append(source_class)
            continue
        capability, source_trust = resolved
        required_trust = max(
            TRUST_RANK.get(requirement.trust_floor, 0),
            TRUST_RANK.get(option_trust, 0),
        )
        if TRUST_RANK.get(source_trust, 0) < required_trust:
            unavailable.append(f"{source_class}:trust")
            continue
        permitted = (
            capability in profile.capabilities
            if capability.startswith("workspace.")
            else capability in ceiling
        )
        if not permitted:
            unavailable.append(f"{source_class}:{capability}")
            continue
        if not any(row[0] == capability for row in available):
            available.append((capability, source_trust, source_class))
    if available:
        return available
    raise EvidenceCompilationError(
        "required_source_outside_profile_ceiling",
        (
            f"no acceptable source for {requirement.id} fits profile {profile.id}; "
            f"attempted {', '.join(unavailable) or requirement.source_class}"
        ),
        requirement_id=requirement.id,
    )


def _resolve_requirement_capability(
    profile: AgentProfile,
    requirement: EvidenceRequirement,
) -> tuple[str, str]:
    capability, trust, _source_class = _resolve_requirement_capabilities(
        profile,
        requirement,
    )[0]
    return capability, trust


def fallback_capabilities_for_requirement(
    requirement: EvidenceRequirement,
    *,
    current_capability: str,
    issued_capabilities: list[str] | tuple[str, ...] | set[str],
) -> tuple[str, ...]:
    issued = set(issued_capabilities)
    rows: list[str] = []
    if requirement.fallback_policy == "fail_closed":
        return ()
    for _preference, source_class, option_trust in _requirement_source_candidates(requirement):
        resolved = SOURCE_CAPABILITIES.get(source_class)
        if resolved is None:
            continue
        capability, source_trust = resolved
        required_trust = max(
            TRUST_RANK.get(requirement.trust_floor, 0),
            TRUST_RANK.get(option_trust, 0),
        )
        if TRUST_RANK.get(source_trust, 0) < required_trust:
            continue
        if capability == current_capability or capability not in issued:
            continue
        if capability not in rows:
            rows.append(capability)
    return tuple(rows)

def task_requires_workspace_mutation(
    task: str,
    *,
    semantic_action_intents: list[str] | tuple[str, ...] | set[str] | None = None,
    allow_text_semantic_fallback: bool = True,
) -> bool:
    intents = {str(value) for value in (semantic_action_intents or [])}
    text = str(task or "")
    if _WORKSPACE_MUTATION_FORBIDDEN.search(text) or re.search(
        r"\b(?:just explain|explain only)\b",
        text,
        re.I,
    ):
        return False
    if "workspace_mutate" in intents:
        return True
    return bool(allow_text_semantic_fallback and _WORKSPACE_MUTATION.search(text))


def compile_task_authority(
    profile: AgentProfile,
    task: str,
    decision: EvidenceDecision,
    *,
    semantic_action_intents: list[str] | tuple[str, ...] | set[str] | None = None,
    semantic_workspace_surfaces: list[str] | tuple[str, ...] | set[str] | None = None,
    allow_text_semantic_fallback: bool = True,
) -> CompiledEvidence:
    """Compile minimum authority from deterministic policy plus semantic proposals.

    Semantic action intents and workspace surfaces are untrusted descriptions of
    meaning. They can help Omnix understand arbitrary phrasing, but every
    resulting capability is still checked against the selected profile ceiling
    before a run can start. Surface markers embedded in action intents are a
    compatibility transport for call sites that predate the dedicated surface
    argument; the typed SemanticTask workspace_surfaces field remains canonical.
    """

    evidence = compile_evidence(profile, decision)
    text = str(task or "")
    intents = {str(value) for value in (semantic_action_intents or [])}
    surfaces = {str(value).strip().casefold() for value in (semantic_workspace_surfaces or [])}
    surfaces.update(
        intent.split(":", 1)[1].strip().casefold()
        for intent in intents
        if intent.startswith("workspace_surface:") and ":" in intent
    )

    local = _local_task_capabilities(profile, text, intents, allow_text_semantic_fallback)

    external = list(evidence.required_external)
    if profile.produces_diff:
        # Browser and MCP providers remain outside Pi. SemanticTask describes
        # whether the workspace surface is web UI; deterministic policy maps
        # that description to capabilities inside the coding profile ceiling.
        from .coding_external_authority import coding_external_capabilities_for_task

        external.extend(coding_external_capabilities_for_task(
            text,
            semantic_workspace_surfaces=surfaces,
            allow_text_semantic_fallback=allow_text_semantic_fallback,
        ))
    _add_domain_capabilities(profile, intents, external, text, allow_text_semantic_fallback)

    ceiling = profile_external_ceiling(profile)
    outside = [cap for cap in external if cap not in ceiling]
    if outside:
        raise EvidenceCompilationError(
            "required_source_outside_profile_ceiling",
            f"required capabilities outside profile {profile.id}: {', '.join(outside)}",
        )
    return CompiledEvidence(
        decision=decision,
        required_local=tuple(dict.fromkeys(local)),
        required_external=tuple(dict.fromkeys(external)),
        external_groups=evidence.external_groups,
    )


def _local_task_capabilities(profile, text, intents, allow_text_semantic_fallback):
    """Workspace capabilities: reads always, edits and commands only when the task mutates or executes."""
    if profile.produces_diff:
        read_caps = [
            capability
            for capability in profile.capabilities
            if capability in {
                "workspace.read",
                "workspace.list",
                "workspace.search",
                "workspace.git_status",
                "workspace.git_diff",
                "workspace.run_change_set",
            }
        ]
        local = list(read_caps)
        if task_requires_workspace_mutation(
            text,
            semantic_action_intents=intents,
            allow_text_semantic_fallback=allow_text_semantic_fallback,
        ):
            local.extend(
                capability
                for capability in profile.capabilities
                if capability in {
                    "workspace.edit",
                    "workspace.write",
                    "workspace.command",
                    "workspace.test",
                }
            )
        elif "workspace_execute" in intents or (
            allow_text_semantic_fallback and _WORKSPACE_EXECUTION.search(text)
        ):
            local.extend(
                capability
                for capability in profile.capabilities
                if capability in {"workspace.command", "workspace.test"}
            )
    elif profile.id == "ops":
        local = [
            capability
            for capability in profile.capabilities
            if capability in {
                "workspace.read",
                "workspace.list",
                "workspace.search",
                "workspace.git_status",
                "workspace.git_diff",
            }
        ]
        if intents & {"ops_read", "ops_execute"} or (
            allow_text_semantic_fallback and _WORKSPACE_EXECUTION.search(text)
        ):
            local.extend(
                capability
                for capability in profile.capabilities
                if capability == "workspace.command"
            )
        if "ops_execute" in intents or (
            allow_text_semantic_fallback and _WORKSPACE_EXECUTION.search(text)
        ):
            local.extend(
                capability
                for capability in profile.capabilities
                if capability == "workspace.test"
            )
    else:
        local = list(profile.capabilities)
    return local


def _add_domain_capabilities(profile, intents, external, text, allow_text_semantic_fallback):
    """Home and personal-assistant capabilities the semantic actions (or, for compatibility, the text) ask for."""
    if profile.id == "house":
        if intents & {"home_read", "home_mutate"}:
            external.append("home.get_state")
        if (
            not _HOME_MUTATION_FORBIDDEN.search(text)
            and (
                "home_mutate" in intents
                or (allow_text_semantic_fallback and _HOME_MUTATION.search(text))
            )
        ):
            external.append("home.set_state")
    elif profile.id == "personal-assistant":
        if "email_read" in intents:
            external.append("gmail.read_email")
        if (
            not _EMAIL_SEND_FORBIDDEN.search(text)
            and (
                "email_send" in intents
                or (allow_text_semantic_fallback and _EMAIL_SEND.search(text))
            )
        ):
            external.append("gmail.send_email")
        elif (
            not _EMAIL_DRAFT_FORBIDDEN.search(text)
            and (
                "email_draft" in intents
                or (allow_text_semantic_fallback and _EMAIL_DRAFT.search(text))
            )
        ):
            external.append("gmail.create_draft")
        if "calendar_read" in intents:
            external.append("calendar.read_availability")
        if (
            not _CALENDAR_CREATE_FORBIDDEN.search(text)
            and (
                "calendar_create" in intents
                or (allow_text_semantic_fallback and _CALENDAR_CREATE.search(text))
            )
        ):
            external.append("calendar.create_event")
        if "contacts_read" in intents:
            external.extend(["contacts.search_contacts", "contacts.resolve_recipient"])


def validate_required_evidence_capabilities(
    capabilities: tuple[str, ...] | list[str],
    *,
    alternative_groups: tuple[tuple[str, ...], ...] | list[tuple[str, ...]] = (),
) -> None:
    """Require one live capability per evidence requirement, not every fallback."""
    if not capabilities:
        return
    from app.platform.assistant_tools.contracts import review_assistant_tool_request
    from .capability_requests import AssistantToolRequest

    allowed_set = set(capabilities)

    def decision_for(capability: str):
        return review_assistant_tool_request(
            AssistantToolRequest(
                tool_id=capability.split(".", 1)[0],
                action_id=capability,
                input={},
            )
        )

    grouped: set[str] = set()
    for raw_group in alternative_groups:
        group = tuple(cap for cap in raw_group if cap in allowed_set)
        if not group:
            continue
        grouped.update(group)
        decisions = [(cap, decision_for(cap)) for cap in group]
        if any(decision.allowed for _cap, decision in decisions):
            continue
        reasons = {str(decision.reason or "") for _cap, decision in decisions}
        code = (
            "required_connection_unavailable"
            if reasons and reasons <= {"missing_connection", "tool_disabled"}
            else "evidence_required_but_unavailable"
        )
        raise EvidenceCompilationError(
            code,
            "required evidence alternatives are unavailable: "
            + ", ".join(f"{cap}={decision.reason}" for cap, decision in decisions),
        )

    for capability in capabilities:
        if capability in grouped or capability.startswith("workspace."):
            continue
        decision = decision_for(capability)
        if decision.allowed:
            continue
        code = (
            "required_connection_unavailable"
            if decision.reason in {"missing_connection", "tool_disabled"}
            else "evidence_required_but_unavailable"
        )
        raise EvidenceCompilationError(
            code,
            f"required evidence capability {capability} is unavailable: {decision.reason}",
        )

def compile_evidence(profile: AgentProfile, decision: EvidenceDecision) -> CompiledEvidence:
    policy = decision.policy
    if policy.requirement == "required" and policy.external_access == "forbidden" and policy.requirements:
        raise EvidenceCompilationError(
            "external_evidence_forbidden",
            "required evidence can only be obtained externally, but external access is forbidden",
        )
    required_external: list[str] = []
    required_local: list[str] = []
    external_groups: list[tuple[str, ...]] = []
    if policy.requirement == "required":
        for original_requirement in policy.requirements:
            requirement = original_requirement
            if requirement.freshness == "current" and requirement.max_age_seconds is None:
                configured_age = freshness_max_age_seconds(requirement.source_class)
                if configured_age is None:
                    raise EvidenceCompilationError(
                        "freshness_policy_unsatisfiable",
                        f"current evidence source {requirement.source_class} has no maximum age",
                        requirement_id=requirement.id,
                    )
                requirement = requirement.model_copy(
                    update={"max_age_seconds": configured_age}
                )
            if requirement.freshness == "as_of_date" and requirement.as_of_date is None:
                raise EvidenceCompilationError(
                    "freshness_policy_unsatisfiable",
                    f"as-of evidence requirement {requirement.id} is missing as_of_date",
                    requirement_id=requirement.id,
                )
            resolved = _resolve_requirement_capabilities(profile, requirement)
            external_group: list[str] = []
            for capability, _trust, _source_class in resolved:
                if capability.startswith("workspace."):
                    required_local.append(capability)
                else:
                    required_external.append(capability)
                    if capability not in external_group:
                        external_group.append(capability)
            if external_group:
                external_groups.append(tuple(external_group))
    return CompiledEvidence(
        decision=decision,
        required_local=tuple(dict.fromkeys(required_local)),
        required_external=tuple(dict.fromkeys(required_external)),
        external_groups=tuple(external_groups),
    )


def revise_objective(
    previous: str,
    instruction: str,
    *,
    objective_relation: str | None = None,
) -> str:
    clean = " ".join(str(instruction or "").split())
    prior = str(previous or "").strip()
    if not prior:
        return clean
    raw_relation = str(objective_relation or "").strip().casefold()
    if not raw_relation:
        # Compatibility for non-semantic/internal callers that do not provide
        # an explicit SemanticTask relation. Preserve the historical safe
        # behavior: obvious corrections replace; otherwise the instruction is
        # additive steering.
        if re.search(
            r"^(?:actually|instead|forget that|just\b|do not\b|don't\b)",
            clean,
            re.I,
        ):
            return clean
        return f"{prior}\nLater steering: {clean}"

    relation = normalize_objective_relation(clean, raw_relation)
    if relation in {"revise", "none"}:
        return clean
    if relation == "resume":
        return prior
    if relation == "continue":
        return f"{prior}\nLater steering: {clean}"
    return f"{prior}\nLater steering: {clean}"


def steering_semantic_context(previous: str, instruction: str) -> str:
    """Preserve referential context for steering classification without preserving authority."""
    clean = " ".join(str(instruction or "").split())
    prior = str(previous or "").strip()
    if not prior:
        return clean
    if not clean:
        return prior
    return (
        "Previous task context (non-authoritative; reference resolution only):\n"
        f"{prior}\n\n"
        "Latest user steering (authoritative; overrides conflicting prior instructions):\n"
        f"{clean}\n\n"
        "Classify the effective task after applying the latest steering. "
        "Use previous task context only to resolve references or omitted subjects. "
        "Never carry forward an action that the latest steering cancels, forbids, "
        "narrows, or replaces."
    )


def subject_matches(required: SubjectRef | None, observed: SubjectRef | None) -> bool:
    if required is None:
        return True
    if observed is None:
        return False
    if required.type != observed.type or required.canonical_id != observed.canonical_id:
        return False
    for key, value in required.qualifiers.items():
        if key not in observed.qualifiers or observed.qualifiers.get(key) != value:
            return False
    return True



def coverage_matches(required: EvidenceCoverage, observed: EvidenceCoverage) -> bool:
    required_key = evidence_coverage_key(required)
    observed_key = evidence_coverage_key(observed)
    if required_key != "unbound" and observed_key != "unbound":
        return required_key == observed_key
    if required.subject is not None:
        return subject_matches(required.subject, observed.subject)
    return False


def receipt_satisfies_obligation(
    requirement: EvidenceRequirement,
    receipt: EvidenceReceipt,
) -> bool:
    required_coverage = requirement.coverage
    if required_coverage is None:
        return subject_matches(requirement.subject, receipt.subject)

    observed_coverages = list(receipt.coverage)
    if not observed_coverages and receipt.subject is not None:
        observed = evidence_coverage_from_subject(receipt.subject)
        if observed is not None:
            observed_coverages.append(observed)
    return any(
        coverage_matches(required_coverage, observed)
        for observed in observed_coverages
    )

def _receipt_source_units_for_requirement(
    requirement: EvidenceRequirement,
    receipt: EvidenceReceipt,
) -> int:
    """Count source units for the exact subject/coverage being evaluated."""

    if requirement.coverage is not None:
        coverage_key = evidence_coverage_key(requirement.coverage)
        counts = receipt.metadata.get("evidence_source_counts_by_coverage")
        if isinstance(counts, dict) and coverage_key in counts:
            try:
                return max(0, int(counts[coverage_key]))
            except (TypeError, ValueError):
                return 0
    # Receipts created before per-coverage accounting remain compatible. A
    # newly built multi-coverage web receipt always carries the map above.
    return max(1, int(receipt.source_count or 0))


def evaluate_evidence_set(
    run_id: str,
    policy: EvidencePolicy,
    receipts: list[EvidenceReceipt],
    *,
    now: datetime | None = None,
) -> EvidenceSet:
    if policy.requirement != "required":
        return EvidenceSet(
            run_id=run_id,
            source_manifest_ids=sorted({r.source_manifest_id for r in receipts if r.source_manifest_id}),
            attribution_refs=sorted({
                r.source_manifest_id or f"receipt:{r.receipt_id}"
                for r in receipts
                if r.source_manifest_id or r.provider or r.origin
            }),
            passed=True,
        )
    current = now or datetime.now(timezone.utc)
    evaluations: list[EvidenceRequirementEvaluation] = []
    missing: list[str] = []
    stale: list[str] = []
    wrong_subject: list[str] = []
    low_trust: list[str] = []
    accepted_receipts: set[str] = set()

    for requirement in policy.requirements:
        options = [
            EvidenceSourceOption(
                source_class=requirement.source_class,
                trust_floor=requirement.trust_floor,
                preference=0,
            ),
            *requirement.acceptable_sources,
        ]
        source_classes = {option.source_class for option in options}
        candidates = [r for r in receipts if r.source_class in source_classes]
        matched: list[str] = []
        matched_units = 0
        rejected: list[str] = []
        statuses: list[str] = []
        for receipt in candidates:
            compatible_options = [
                option
                for option in options
                if option.source_class == receipt.source_class
                and (
                    not option.provider_hint
                    or str(receipt.provider or receipt.origin or "").casefold()
                    == option.provider_hint.casefold()
                )
            ]
            if not compatible_options:
                rejected.append(receipt.receipt_id)
                statuses.append("rejected")
                continue
            if not receipt_satisfies_obligation(requirement, receipt):
                wrong_subject.append(receipt.receipt_id)
                rejected.append(receipt.receipt_id)
                statuses.append("wrong_subject")
                continue
            option_trust = max(
                (TRUST_RANK.get(option.trust_floor, 0) for option in compatible_options),
                default=0,
            )
            required_trust = max(
                TRUST_RANK.get(requirement.trust_floor, 0),
                option_trust,
            )
            if TRUST_RANK.get(receipt.trust_level, 0) < required_trust:
                low_trust.append(receipt.receipt_id)
                rejected.append(receipt.receipt_id)
                statuses.append("insufficient_trust")
                continue
            max_age = requirement.max_age_seconds
            freshness_time = receipt.freshest_source_at or receipt.observed_at
            if requirement.freshness == "as_of_date":
                if requirement.as_of_date is None or receipt.freshest_source_at is None:
                    rejected.append(receipt.receipt_id)
                    statuses.append("rejected")
                    continue
                as_of = requirement.as_of_date
                if as_of.tzinfo is None:
                    as_of = as_of.replace(tzinfo=timezone.utc)
                else:
                    as_of = as_of.astimezone(timezone.utc)
                if freshness_time > as_of:
                    rejected.append(receipt.receipt_id)
                    statuses.append("rejected")
                    continue
                if max_age and (as_of - freshness_time).total_seconds() > max_age:
                    stale.append(receipt.receipt_id)
                    rejected.append(receipt.receipt_id)
                    statuses.append("stale")
                    continue
            elif max_age and (current - freshness_time).total_seconds() > max_age:
                stale.append(receipt.receipt_id)
                rejected.append(receipt.receipt_id)
                statuses.append("stale")
                continue
            matched.append(receipt.receipt_id)
            matched_units += _receipt_source_units_for_requirement(requirement, receipt)
            accepted_receipts.add(receipt.receipt_id)

        reason, status = _requirement_status(matched_units, requirement, candidates, missing, statuses)
        evaluations.append(EvidenceRequirementEvaluation(
            requirement_id=requirement.id,
            status=status,
            matching_receipt_ids=matched,
            rejected_receipt_ids=rejected,
            reason=reason,
        ))

    return EvidenceSet(
        run_id=run_id,
        requirements=evaluations,
        missing_requirements=missing,
        stale_receipts=sorted(set(stale)),
        wrong_subject_receipts=sorted(set(wrong_subject)),
        insufficient_trust_receipts=sorted(set(low_trust)),
        source_manifest_ids=sorted({
            r.source_manifest_id
            for r in receipts
            if r.receipt_id in accepted_receipts and r.source_manifest_id
        }),
        attribution_refs=sorted({
            r.source_manifest_id or f"receipt:{r.receipt_id}"
            for r in receipts
            if r.receipt_id in accepted_receipts
            and (r.source_manifest_id or r.provider or r.origin)
        }),
        passed=not missing,
    )


def _requirement_status(matched_units, requirement, candidates, missing, statuses):
    """A requirement's verdict from its matched units and the rejection reasons seen."""
    if matched_units >= requirement.minimum_matches:
        status = "satisfied"
        reason = None
    elif not candidates:
        status = "missing"
        reason = "no receipt for required source class"
        missing.append(requirement.id)
    elif "stale" in statuses:
        status = "stale"
        reason = "matching receipts are stale"
        missing.append(requirement.id)
    elif "wrong_subject" in statuses:
        status = "wrong_subject"
        reason = "receipts do not match required subject"
        missing.append(requirement.id)
    elif "insufficient_trust" in statuses:
        status = "insufficient_trust"
        reason = "receipts are below trust floor"
        missing.append(requirement.id)
    else:
        status = "rejected"
        reason = "insufficient acceptable receipts"
        missing.append(requirement.id)
    return reason, status


def request_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def result_digest(payload: dict[str, Any]) -> str:
    return request_digest(payload)


_REVERSE_SOURCE_CAPABILITIES: dict[str, str] = {
    "research.web_search": "general_current_web",
    "trading.market_quote": "market_quote",
    "github.read_repo": "repo_contents",
    "github.inspect_ci": "repo_ci_state",
    "home.get_state": "home_state",
    "home.get_energy": "home_energy",
    "calendar.read_availability": "calendar_state",
    "gmail.read_email": "email_state",
    "market.status": "market_status",
    "weather.current": "weather_state",
}


def _result_output(result_payload: dict[str, Any]) -> dict[str, Any]:
    value = result_payload.get("output")
    return dict(value) if isinstance(value, dict) else {}


_SOFTWARE_RELEASE_PRIMARY_DOMAINS = frozenset({
    "react.dev",
    "vuejs.org",
    "nodejs.org",
    "deno.com",
    "deno.land",
    "bun.sh",
    "postgresql.org",
    "python.org",
    "go.dev",
    "rust-lang.org",
    "kubernetes.io",
    "docker.com",
})
_SOFTWARE_RELEASE_PRIMARY_GITHUB_REPOSITORIES = frozenset({
    "facebook/react",
    "vuejs/core",
    "nodejs/node",
    "denoland/deno",
    "oven-sh/bun",
    "postgres/postgres",
    "python/cpython",
    "golang/go",
    "rust-lang/rust",
    "kubernetes/kubernetes",
    "docker/cli",
})


def _web_item_trust(source_class: str, item: dict[str, Any]) -> str:
    parsed = urlparse(str(item.get("url") or ""))
    domain = (parsed.hostname or "").casefold().removeprefix("www.")
    if not domain:
        return "general"
    if source_class == "company_filing":
        return (
            "primary"
            if domain == "sec.gov" or domain.endswith(".sec.gov")
            else "reputable"
        )
    if source_class == "software_release":
        if domain in _SOFTWARE_RELEASE_PRIMARY_DOMAINS:
            return "primary"
        if domain == "github.com":
            segments = [part.casefold() for part in parsed.path.split("/") if part]
            repository = "/".join(segments[:2]) if len(segments) >= 2 else ""
            if repository in _SOFTWARE_RELEASE_PRIMARY_GITHUB_REPOSITORIES:
                return "primary"
        return "reputable"
    return "reputable"


def _actual_web_trust(output: dict[str, Any], source_class: str) -> str:
    items = _web_source_items(output)
    if not items:
        return "general"
    levels = [_web_item_trust(source_class, item) for item in items]
    # A multi-result receipt receives only the trust shared by every result.
    return min(levels, key=lambda value: TRUST_RANK.get(value, 0))


def _observed_subject(
    capability_id: str,
    source_class: str,
    request_input: dict[str, Any],
    output: dict[str, Any],
) -> SubjectRef | None:
    if capability_id == "trading.market_quote":
        ticker = str(output.get("ticker") or request_input.get("ticker") or "").strip().upper()
        instrument_id = str(output.get("instrument_id") or "").strip()
        if ticker:
            subject = _security_subject(ticker)
            if instrument_id:
                return subject.model_copy(update={
                    "canonical_id": instrument_id,
                    "qualifiers": {**subject.qualifiers, "instrument_id": instrument_id},
                })
            return subject
        return None
    if capability_id in {"github.inspect_ci", "github.read_repo"}:
        repository = str(output.get("repository") or request_input.get("repository") or "").strip()
        if not repository:
            return None
        requested_ref = str(
            output.get("requested_ref")
            or request_input.get("requested_ref")
            or ""
        ).strip()
        resolved_commit = str(
            output.get("resolved_commit")
            or output.get("ref")
            or request_input.get("resolved_commit")
            or request_input.get("ref")
            or request_input.get("sha")
            or ""
        ).strip()
        qualifiers: dict[str, Any] = {}
        if requested_ref:
            qualifiers["requested_ref"] = requested_ref
        if resolved_commit:
            qualifiers["resolved_commit"] = resolved_commit
        return SubjectRef(
            type="repository_ref",
            canonical_id=repository,
            display_name=repository,
            qualifiers=qualifiers,
        )
    if capability_id == "research.web_search":
        query = str(request_input.get("query") or "")
        web_subject = resolve_subject(query, source_class)
        if web_subject is not None:
            return web_subject
        query_ticker = _extract_ticker(query)
        return _security_subject(query_ticker) if query_ticker else None
    if source_class == "home_state":
        return SubjectRef(type="home", canonical_id="current_home", display_name="current home")
    if source_class == "home_energy":
        return SubjectRef(type="home", canonical_id="current_home", display_name="current home")
    if source_class == "calendar_state":
        return SubjectRef(type="calendar", canonical_id="primary_calendar", display_name="primary calendar")
    if source_class == "email_state":
        return SubjectRef(type="mailbox", canonical_id="primary_mailbox", display_name="primary mailbox")
    if source_class == "market_status":
        return SubjectRef(type="market", canonical_id="us_equities", display_name="US equities market")
    if capability_id == "weather.current":
        location = str(output.get("location") or request_input.get("location") or "").strip()
        if not location or location.casefold() == "user_location":
            return SubjectRef(type="location", canonical_id="user_location", display_name="user location")
        return SubjectRef(type="location", canonical_id=location.casefold(), display_name=location)
    return None

def _request_supports_subject(subject: SubjectRef | None, request_input: dict[str, Any]) -> bool:
    if subject is None:
        return True
    serialized = json.dumps(request_input, sort_keys=True, default=str).casefold()
    tokens = [
        subject.canonical_id,
        subject.display_name or "",
        *[str(value) for value in subject.qualifiers.values()],
    ]
    meaningful = [token.casefold() for token in tokens if token and token not in {"current_repository", "current_home", "primary_calendar", "primary_mailbox", "user_location"}]
    if not meaningful:
        return True
    return any(token in serialized for token in meaningful)


def _source_intent_score(source_class: str, text: str) -> int:
    value = str(text or "")
    if source_class == "company_filing":
        return 100 if _FILINGS.search(value) else 0
    if source_class == "software_release":
        return 90 if _RELEASE.search(value) else 0
    if source_class == "market_news":
        return 80 if _MARKET_NEWS.search(value) else 20 if (_MARKET.search(value) or _extract_ticker(value)) else 0
    if source_class == "general_current_web":
        return 10
    if source_class == "repo_ci_state":
        return 100 if _CI.search(value) else 0
    if source_class == "repo_contents":
        return 70 if _REPO.search(value) else 0
    return 50


def resolve_evidence_call(
    policy: EvidencePolicy,
    capability_id: str,
    request_input: dict[str, Any],
) -> tuple[EvidenceRequirement | None, str | None]:
    """Bind one broker call to one requirement/source deterministically."""
    serialized = json.dumps(request_input, sort_keys=True, default=str)
    rows: list[tuple[int, int, EvidenceRequirement, str]] = []
    for requirement in policy.requirements:
        candidates = _requirement_source_candidates(requirement)
        if requirement.fallback_policy == "fail_closed":
            candidates = candidates[:1]
        for preference, source_class, option_trust in candidates:
            resolved = SOURCE_CAPABILITIES.get(source_class)
            if resolved is None or resolved[0] != capability_id:
                continue
            _resolved_capability, source_trust = resolved
            required_trust = max(
                TRUST_RANK.get(requirement.trust_floor, 0),
                TRUST_RANK.get(option_trust, 0),
            )
            if TRUST_RANK.get(source_trust, 0) < required_trust:
                continue
            score = _source_intent_score(source_class, serialized)
            if requirement.subject is not None and _request_supports_subject(
                requirement.subject,
                request_input,
            ):
                score += 25
            rows.append((score, -preference, requirement, source_class))
    if not rows:
        return None, _REVERSE_SOURCE_CAPABILITIES.get(capability_id)
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    _score, _preference, requirement, source_class = rows[0]
    return requirement, source_class


def _normalized_coverage_text(value: object) -> str:
    return re.sub(
        r"[^a-z0-9._:/+-]+",
        "-",
        str(value or "").casefold(),
    ).strip("-")


def _web_source_items(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Project web results down to provider-originated evidence fields only.

    Search adapters may synthesize display titles from the request query when a
    provider omits a title. Titles and metadata are therefore presentation data,
    not evidence identity. Only returned URL/content/snippet fields can prove
    subject coverage.
    """

    items = output.get("items")
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        projected = {
            key: value
            for key in ("url", "content", "snippet")
            if isinstance((value := item.get(key)), str) and value.strip()
        }
        if projected:
            rows.append(projected)
    return rows


def _coverage_token_observed(token: str, output: dict[str, Any]) -> bool:
    normalized_token = _normalized_coverage_text(token)
    if not normalized_token:
        return False
    items = _web_source_items(output)
    observed = _normalized_coverage_text(
        json.dumps(items, sort_keys=True, default=str)
    )
    if not observed:
        return False
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_token)}(?![a-z0-9])",
            observed,
        )
    )


def _subject_supported_by_web_output(
    subject: SubjectRef,
    output: dict[str, Any],
) -> bool:
    """A web query names intent; only returned content proves the subject."""

    candidates = [
        str(subject.qualifiers.get("ticker") or "").strip(),
        str(subject.display_name or "").strip(),
        str(subject.canonical_id or "").strip(),
    ]
    meaningful = [
        value
        for value in candidates
        if value
        and value.casefold() not in {
            "current_repository",
            "current_home",
            "primary_calendar",
            "primary_mailbox",
            "user_location",
        }
    ]
    return any(
        _coverage_token_observed(value, output)
        for value in meaningful
    )


def _coverage_supported_by_observation(
    coverage: EvidenceCoverage,
    *,
    subject: SubjectRef | None,
    output: dict[str, Any],
) -> bool:
    """Require coverage identity to be evidenced by the actual tool result."""

    if coverage.subject is not None:
        return subject_matches(coverage.subject, subject)

    key = str(coverage.coverage_key or "").strip()
    if not key:
        return False
    if key.startswith("software_package:"):
        return _coverage_token_observed(key.split(":", 1)[1], output)
    if key.startswith("semantic_dependency:"):
        parts = key.split(":", 2)
        return len(parts) == 3 and _coverage_token_observed(parts[2], output)

    # Opaque/legacy claim hashes cannot be independently recovered from a
    # provider result, so they fail closed instead of being copied from policy.
    return False


def _compatible_observed_coverages(
    policy: EvidencePolicy,
    *,
    capability_id: str,
    source_class: str,
    subject: SubjectRef | None,
    output: dict[str, Any],
) -> list[EvidenceCoverage]:
    rows: list[EvidenceCoverage] = []
    seen: set[str] = set()

    observed_subject = evidence_coverage_from_subject(subject)
    if observed_subject is not None:
        encoded = observed_subject.model_dump_json()
        seen.add(encoded)
        rows.append(observed_subject)

    for requirement in policy.requirements:
        coverage = requirement.coverage
        if coverage is None:
            continue
        candidates = _requirement_source_candidates(requirement)
        if requirement.fallback_policy == "fail_closed":
            candidates = candidates[:1]
        compatible = False
        for _preference, candidate_source, option_trust in candidates:
            resolved = SOURCE_CAPABILITIES.get(candidate_source)
            if resolved is None:
                continue
            resolved_capability, resolved_trust = resolved
            required_trust = max(
                TRUST_RANK.get(requirement.trust_floor, 0),
                TRUST_RANK.get(option_trust, 0),
            )
            if (
                resolved_capability == capability_id
                and candidate_source == source_class
                and TRUST_RANK.get(resolved_trust, 0) >= required_trust
            ):
                compatible = True
                break
        if not compatible or not _coverage_supported_by_observation(
            coverage,
            subject=subject,
            output=output,
        ):
            continue
        encoded = coverage.model_dump_json()
        if encoded not in seen:
            seen.add(encoded)
            rows.append(coverage)
    return rows


def _coverage_source_counts(
    coverages: list[EvidenceCoverage],
    output: dict[str, Any],
) -> dict[str, int]:
    """Count returned web source records separately for every coverage key."""

    items = _web_source_items(output)
    if not items:
        return {}
    counts: dict[str, int] = {}
    for coverage in coverages:
        coverage_key = evidence_coverage_key(coverage)
        if coverage_key == "unbound":
            continue
        count = 0
        for item in items:
            item_output: dict[str, Any] = {"items": [item]}
            if coverage.subject is not None:
                supported = _subject_supported_by_web_output(coverage.subject, item_output)
            else:
                supported = _coverage_supported_by_observation(
                    coverage,
                    subject=None,
                    output=item_output,
                )
            if supported:
                count += 1
        counts[coverage_key] = count
    return counts


def build_evidence_receipt(
    *,
    run_id: str,
    task_revision_id: str | None,
    policy: EvidencePolicy,
    capability_id: str,
    request_input: dict[str, Any],
    result_payload: dict[str, Any],
    error: str | None,
    requirement_id: str | None = None,
    source_class_hint: str | None = None,
) -> EvidenceReceipt | None:
    if error:
        return None
    source_class = source_class_hint or _REVERSE_SOURCE_CAPABILITIES.get(capability_id)
    subject: SubjectRef | None = None
    matched_requirement: EvidenceRequirement | None = None
    trust = "general"
    requirements = [
        requirement
        for requirement in policy.requirements
        if requirement_id is None or requirement.id == requirement_id
    ]
    for requirement in requirements:
        candidates = _requirement_source_candidates(requirement)
        if requirement.fallback_policy == "fail_closed":
            candidates = candidates[:1]
        matched_requirement = None
        for _preference, candidate_source, option_trust in candidates:
            resolved = SOURCE_CAPABILITIES.get(candidate_source)
            if resolved is None:
                continue
            resolved_capability, resolved_trust = resolved
            if resolved_capability != capability_id:
                continue
            required_trust = max(
                TRUST_RANK.get(requirement.trust_floor, 0),
                TRUST_RANK.get(option_trust, 0),
            )
            if TRUST_RANK.get(resolved_trust, 0) < required_trust:
                continue
            if source_class_hint is not None and candidate_source != source_class_hint:
                continue
            source_class = candidate_source
            subject = None
            trust = (
                option_trust
                if TRUST_RANK.get(option_trust, 0) <= TRUST_RANK.get(resolved_trust, 0)
                else resolved_trust
            )
            matched_requirement = requirement
            break
        if matched_requirement is not None:
            break
    if source_class is None:
        return None

    output = _result_output(result_payload)
    subject = _observed_subject(
        capability_id,
        source_class,
        request_input,
        output,
    )
    if (
        capability_id == "research.web_search"
        and subject is not None
        and not _subject_supported_by_web_output(subject, output)
    ):
        subject = None
    diagnostics = output.get("diagnostics")
    diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
    provider = str(diagnostics.get("provider") or output.get("provider") or "").strip() or None
    source_manifest_id = str(output.get("source_manifest_id") or "").strip() or None
    items = output.get("items")
    source_count = len(items) if isinstance(items, list) else int(output.get("source_count") or 0)
    origin = provider
    if capability_id == "research.web_search":
        trust = _actual_web_trust(output, source_class)
    elif capability_id.startswith(("github.", "home.", "calendar.", "gmail.")):
        trust = "authoritative"

    now = datetime.now(timezone.utc)
    freshest_source_at = None
    raw_source_time = output.get("source_time")
    if isinstance(raw_source_time, str) and raw_source_time.strip():
        try:
            freshest_source_at = datetime.fromisoformat(raw_source_time.replace("Z", "+00:00"))
            if freshest_source_at.tzinfo is None:
                freshest_source_at = freshest_source_at.replace(tzinfo=timezone.utc)
            else:
                freshest_source_at = freshest_source_at.astimezone(timezone.utc)
        except ValueError:
            freshest_source_at = None
    observed_coverage = _compatible_observed_coverages(
        policy,
        capability_id=capability_id,
        source_class=source_class,
        subject=subject,
        output=output,
    )
    coverage_source_counts = (
        _coverage_source_counts(observed_coverage, output)
        if capability_id == "research.web_search"
        else {}
    )
    return EvidenceReceipt(
        run_id=run_id,
        task_revision_id=task_revision_id,
        capability_id=capability_id,
        source_class=source_class,
        subject=subject,
        coverage=observed_coverage,
        request_digest=request_digest(request_input),
        provider=provider,
        origin=origin,
        source_manifest_id=source_manifest_id,
        source_count=source_count,
        executed_at=now,
        observed_at=now,
        freshest_source_at=freshest_source_at,
        trust_level=cast(Any, trust),
        result_digest=result_digest(result_payload),
        metadata={
            "broker": True,
            "provider_atomicity": "omnix_local_commit_only",
            "evidence_requirement_id": requirement_id,
            "evidence_source_class": source_class,
            "evidence_coverage_count": len(observed_coverage),
            "evidence_source_counts_by_coverage": coverage_source_counts,
        },
    )



def is_evidence_capability(capability_id: str) -> bool:
    return str(capability_id or "") in _REVERSE_SOURCE_CAPABILITIES
