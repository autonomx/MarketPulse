"""Credential-safe runtime status for Web Research settings and capabilities."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.conversation.research_compatibility import ResearchCompatibilityStatus, research_compatibility_status
from .policy import privacy_contract
from .provider_chain import provider_credential_configured, provider_requires_credential
from .quick_search import provider_coverage
from .release_policy import (
    ResearchReleaseAvailability,
    ResearchReleasePolicy,
    research_release_availability,
    research_release_policy_from_env,
)
from .settings import ResearchRuntimeSettings, load_research_runtime_settings


class ResearchProviderStatus(BaseModel):
    provider: str
    available: bool
    credential_required: bool
    credential_configured: bool
    coverage: str


class ResearchBudgetStatus(BaseModel):
    quick_max_results: int
    deep_max_steps: int
    deep_max_queries: int
    deep_max_sources: int
    deep_max_extracts: int


class ResearchRetentionStatus(BaseModel):
    search_cache_ttl_seconds: int
    extraction_cache_ttl_seconds: int
    raw_snapshot_retention_days: int
    source_manifest_retention_days: int


class ResearchReleaseStatus(BaseModel):
    master_enabled: bool
    quick_enabled: bool
    quick_percentage: int
    deep_local_enabled: bool
    deep_local_percentage: int
    hermes_enabled: bool
    hermes_percentage: int
    availability: ResearchReleaseAvailability


class ResearchRuntimeStatus(BaseModel):
    default_mode: str
    provider: ResearchProviderStatus
    provider_chain: list[ResearchProviderStatus] = Field(default_factory=list)
    budgets: ResearchBudgetStatus
    retention: ResearchRetentionStatus
    release: ResearchReleaseStatus
    compatibility: ResearchCompatibilityStatus
    deep_enabled: bool
    hermes_planner_enabled: bool
    diagnostics_enabled: bool
    privacy: dict[str, object] = Field(default_factory=dict)


def _provider_status(provider: str) -> ResearchProviderStatus:
    credential_required = provider_requires_credential(provider)
    configured = provider_credential_configured(provider)
    return ResearchProviderStatus(
        provider=provider,
        available=configured,
        credential_required=credential_required,
        credential_configured=configured,
        coverage=provider_coverage(provider),
    )


def research_runtime_status(
    settings: ResearchRuntimeSettings | None = None,
    release_policy: ResearchReleasePolicy | None = None,
    *,
    identity: str = "status-preview",
) -> ResearchRuntimeStatus:
    resolved = settings or load_research_runtime_settings()
    release = release_policy or research_release_policy_from_env()
    availability = research_release_availability(resolved, release, identity=identity)
    provider = resolved.effective_provider
    return ResearchRuntimeStatus(
        default_mode=resolved.default_mode,
        provider=_provider_status(provider),
        provider_chain=[_provider_status(item) for item in resolved.effective_provider_chain],
        budgets=ResearchBudgetStatus(
            quick_max_results=resolved.max_results,
            deep_max_steps=resolved.max_steps,
            deep_max_queries=resolved.max_queries,
            deep_max_sources=resolved.max_sources,
            deep_max_extracts=resolved.max_extracts,
        ),
        retention=ResearchRetentionStatus(
            search_cache_ttl_seconds=resolved.policy.search_cache_ttl_seconds,
            extraction_cache_ttl_seconds=resolved.policy.extraction_cache_ttl_seconds,
            raw_snapshot_retention_days=resolved.policy.raw_snapshot_retention_days,
            source_manifest_retention_days=resolved.policy.source_manifest_retention_days,
        ),
        release=ResearchReleaseStatus(
            master_enabled=release.master_enabled,
            quick_enabled=release.quick_enabled,
            quick_percentage=release.quick_percentage,
            deep_local_enabled=release.deep_local_enabled,
            deep_local_percentage=release.deep_local_percentage,
            hermes_enabled=release.hermes_enabled,
            hermes_percentage=release.hermes_percentage,
            availability=availability,
        ),
        compatibility=research_compatibility_status(),
        deep_enabled=resolved.deep_enabled,
        hermes_planner_enabled=resolved.hermes_planner_enabled,
        diagnostics_enabled=resolved.show_diagnostics,
        privacy=privacy_contract(resolved.policy),
    )
