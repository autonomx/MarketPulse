"""Canonical contracts for assistant web research modes and provenance."""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from app.conversation.contracts import ResearchMode, normalize_research_mode
# Research-mode compatibility moved to app.conversation (ADR-0016): chat's request
# model needs it whether or not the research feature is enabled.
from app.conversation.research_compatibility import (
    LEGACY_RESEARCH_FIELDS as LEGACY_RESEARCH_FIELDS,
    LEGACY_RESEARCH_MODES as LEGACY_RESEARCH_MODES,
    legacy_research_aliases_enabled as legacy_research_aliases_enabled,
    legacy_research_warnings as legacy_research_warnings,
    record_legacy_research_aliases as record_legacy_research_aliases,
)

ResearchModeSource = Literal["turn", "conversation", "profile", "fallback"]
ResearchStatus = Literal["completed", "partial", "failed", "canceled"]
ResearchSectionKind = Literal["fact", "inference", "limitation", "recommendation"]

RESEARCH_JOB_MODULE = "assistant"
RESEARCH_JOB_TYPE = "assistant.deep_research"
RESEARCH_STAGE_IDS = (
    "planning",
    "searching",
    "extracting",
    "evaluating",
    "synthesizing",
    "persisting",
)


class ResearchModeResolution(BaseModel):
    requested_mode: ResearchMode
    effective_mode: ResearchMode
    source: ResearchModeSource
    available: bool = True
    downgraded: bool = False
    warning: str | None = None


def resolve_research_mode(
    *,
    turn_override: Any = None,
    conversation_override: Any = None,
    profile_default: Any = None,
    quick_enabled: bool = True,
    deep_enabled: bool = False,
    allow_deep_downgrade: bool = False,
) -> ResearchModeResolution:
    """Resolve mode precedence, then constrain it by runtime availability.

    Precedence is turn override, conversation override, profile default, then the
    disabled fallback. An explicitly supplied malformed value resolves to
    disabled rather than falling through to a lower-precedence value.
    """

    candidates = (
        ("turn", turn_override),
        ("conversation", conversation_override),
        ("profile", profile_default),
    )
    source: ResearchModeSource = "fallback"
    requested: ResearchMode = "disabled"
    for candidate_source, value in candidates:
        if value is None:
            continue
        source = candidate_source  # type: ignore[assignment]
        requested = normalize_research_mode(value)
        break

    if requested == "quick" and not quick_enabled:
        return ResearchModeResolution(
            requested_mode=requested,
            effective_mode="disabled",
            source=source,
            available=False,
            warning="Quick Search is unavailable; no web request was made.",
        )
    if requested == "deep" and not deep_enabled:
        if allow_deep_downgrade and quick_enabled:
            return ResearchModeResolution(
                requested_mode=requested,
                effective_mode="quick",
                source=source,
                available=False,
                downgraded=True,
                warning="Deep Research is unavailable; Quick Search was used instead.",
            )
        return ResearchModeResolution(
            requested_mode=requested,
            effective_mode="disabled",
            source=source,
            available=False,
            warning="Deep Research is unavailable; no research request was started.",
        )
    return ResearchModeResolution(
        requested_mode=requested,
        effective_mode=requested,
        source=source,
    )


class ResearchQuery(BaseModel):
    query_id: str
    text: str
    logical_index: int = Field(default=0, ge=0)
    transport_attempts: int = Field(default=0, ge=0)


class ResearchSource(BaseModel):
    """Stable provenance identity independent of any individual retrieval."""

    source_record_id: str
    provider: str
    original_url: str | None = None
    canonical_url: str | None = None
    title: str
    first_seen_at: str


class ResearchSourceSnapshot(BaseModel):
    """One versioned search or extraction observation for a source."""

    snapshot_id: str
    source_record_id: str
    citation_label: str
    query_id: str | None = None
    rank: int | None = Field(default=None, ge=1)
    snippet: str = ""
    published_at: str | None = None
    retrieved_at: str
    extractor_version: str | None = None
    extraction_status: str = "not_requested"
    content_hash: str | None = None
    extracted_text_ref: str | None = None
    retention_policy: str = "default"
    expires_at: str | None = None


class ResearchWarning(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ResearchDiagnostics(BaseModel):
    provider: str | None = None
    coverage: str | None = None
    planner_backend: str | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    logical_queries: int = Field(default=0, ge=0)
    transport_attempts: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    snapshot_count: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchEvidence(BaseModel):
    evidence_id: str
    claim: str
    source_snapshot_ids: list[str] = Field(default_factory=list)
    contradicting_snapshot_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""


class ResearchAnswerSection(BaseModel):
    kind: ResearchSectionKind
    text: str
    source_snapshot_ids: list[str] = Field(default_factory=list)


class QuickSearchResult(BaseModel):
    mode: Literal["quick"] = "quick"
    query: ResearchQuery
    context_blocks: list[str] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    snapshots: list[ResearchSourceSnapshot] = Field(default_factory=list)
    answer_sections: list[ResearchAnswerSection] = Field(default_factory=list)
    diagnostics: ResearchDiagnostics = Field(default_factory=ResearchDiagnostics)
    warnings: list[ResearchWarning] = Field(default_factory=list)


class DeepResearchResult(BaseModel):
    mode: Literal["deep"] = "deep"
    job_id: str
    research_status: ResearchStatus
    objective: str
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    synthesis: str = ""
    answer_sections: list[ResearchAnswerSection] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    snapshots: list[ResearchSourceSnapshot] = Field(default_factory=list)
    diagnostics: ResearchDiagnostics = Field(default_factory=ResearchDiagnostics)
    warnings: list[ResearchWarning] = Field(default_factory=list)


ResearchResult = Annotated[
    Union[QuickSearchResult, DeepResearchResult],
    Field(discriminator="mode"),
]


class ResearchMessageMetadata(BaseModel):
    research_mode: ResearchMode
    research_status: ResearchStatus | None = None
    research_job_id: str | None = None
    source_manifest_id: str | None = None
    warning: str | None = None


# Research entry points for other features (WP-8.2). Chat builds enriched turns
# and deep-research jobs through these names; the lint allows a feature to import
# another feature only through its ``contracts`` module. They load on first use
# because the modules that define them import this one.
_ENTRY_POINTS = {
    "citation_labels": "evidence",
    "prepare_evidence_context_items": "evidence",
    "render_answer_with_compatibility_fallback": "evidence",
    "source_manifest_id": "evidence",
    "ReadablePageExtractor": "extraction",
    "DeepResearchJobInput": "jobs",
    "create_deep_research_job_request": "jobs",
    "start_research_job": "jobs",
    "ResearchPlanner": "planner",
    "ResearchPlanningBudget": "planner",
    "ResearchPlanningRequest": "planner",
    "ResearchPolicy": "policy",
    "research_policy_from_env": "policy",
    "ProviderFallbackSearchClient": "provider_chain",
    "normalize_provider_chain": "provider_chain",
    "QuickSearchService": "quick_search",
    "ResearchReleaseDecision": "release_policy",
    "ResearchReleasePolicy": "release_policy",
    "research_release_availability": "release_policy",
    "research_release_notice": "release_policy",
    "research_release_policy_from_env": "release_policy",
    "resolve_research_release": "release_policy",
    "ResearchRuntimeSettings": "settings",
    "load_research_runtime_settings": "settings",
    "ResearchRuntimeStatus": "status",
    "research_runtime_status": "status",
    "WebSearchClient": "web_search",
}

if TYPE_CHECKING:
    from .evidence import (
        citation_labels as citation_labels,
        prepare_evidence_context_items as prepare_evidence_context_items,
        render_answer_with_compatibility_fallback as render_answer_with_compatibility_fallback,
        source_manifest_id as source_manifest_id,
    )
    from .extraction import (
        ReadablePageExtractor as ReadablePageExtractor,
    )
    from .jobs import (
        DeepResearchJobInput as DeepResearchJobInput,
        create_deep_research_job_request as create_deep_research_job_request,
        start_research_job as start_research_job,
    )
    from .planner import (
        ResearchPlanner as ResearchPlanner,
        ResearchPlanningBudget as ResearchPlanningBudget,
        ResearchPlanningRequest as ResearchPlanningRequest,
    )
    from .policy import (
        ResearchPolicy as ResearchPolicy,
        research_policy_from_env as research_policy_from_env,
    )
    from .provider_chain import (
        ProviderFallbackSearchClient as ProviderFallbackSearchClient,
        normalize_provider_chain as normalize_provider_chain,
    )
    from .quick_search import (
        QuickSearchService as QuickSearchService,
    )
    from .release_policy import (
        ResearchReleaseDecision as ResearchReleaseDecision,
        ResearchReleasePolicy as ResearchReleasePolicy,
        research_release_availability as research_release_availability,
        research_release_notice as research_release_notice,
        research_release_policy_from_env as research_release_policy_from_env,
        resolve_research_release as resolve_research_release,
    )
    from .settings import (
        ResearchRuntimeSettings as ResearchRuntimeSettings,
        load_research_runtime_settings as load_research_runtime_settings,
    )
    from .status import (
        ResearchRuntimeStatus as ResearchRuntimeStatus,
        research_runtime_status as research_runtime_status,
    )
    from .web_search import (
        WebSearchClient as WebSearchClient,
    )


def __getattr__(name: str) -> Any:
    module = _ENTRY_POINTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__package__}.{module}"), name)
