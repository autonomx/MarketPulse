"""Runtime adapter for centralized Settings Control Center research defaults."""
from __future__ import annotations

from app.config.env import environment

import os
from typing import Literal, cast

from pydantic import BaseModel, Field

from .policy import ResearchPolicy, research_policy_from_env
from .provider_chain import (
    DEFAULT_RESEARCH_PROVIDER,
    DEFAULT_RESEARCH_PROVIDER_FALLBACKS,
    normalize_provider_chain,
    provider_credential_configured,
)

ResearchProvider = Literal["duckduckgo", "brave", "tavily", "playwright"]


class ResearchRuntimeSettings(BaseModel):
    default_mode: str = "disabled"
    provider: ResearchProvider = DEFAULT_RESEARCH_PROVIDER
    provider_fallbacks: tuple[ResearchProvider, ...] = DEFAULT_RESEARCH_PROVIDER_FALLBACKS
    max_results: int = Field(default=5, ge=1, le=8)
    max_steps: int = Field(default=6, ge=1, le=12)
    max_queries: int = Field(default=5, ge=1, le=10)
    max_sources: int = Field(default=12, ge=1, le=100)
    max_extracts: int = Field(default=8, ge=0, le=20)
    show_diagnostics: bool = True
    deep_enabled: bool = False
    hermes_planner_enabled: bool = False
    policy: ResearchPolicy

    @property
    def credential_configured(self) -> bool:
        return provider_credential_configured(self.effective_provider)

    @property
    def provider_available(self) -> bool:
        return any(
            provider_credential_configured(provider)
            for provider in self.effective_provider_chain
        )

    @property
    def effective_provider(self) -> ResearchProvider:
        env_provider = environment().get("OMNIX_WEB_SEARCH_PROVIDER", "").strip().lower()
        if (
            self.provider == "duckduckgo"
            and (
                env_provider == "playwright"
                or (env_provider in {"brave", "tavily"} and environment().get("OMNIX_WEB_SEARCH_API_KEY"))
            )
        ):
            return cast(ResearchProvider, env_provider)
        return self.provider

    @property
    def effective_provider_chain(self) -> tuple[ResearchProvider, ...]:
        chain = normalize_provider_chain(self.effective_provider, self.provider_fallbacks)
        return cast(tuple[ResearchProvider, ...], chain)


def load_research_runtime_settings() -> ResearchRuntimeSettings:
    """Load saved profile values, falling back safely to environment policy defaults."""

    environment_policy = research_policy_from_env()
    try:
        from app.settings.access import load_settings
        from app.settings.profile_repository import load_settings_profile

        profile = load_settings_profile(load_settings())
        assistant = profile.assistant
    except Exception:
        return ResearchRuntimeSettings(policy=environment_policy)

    policy = ResearchPolicy(
        search_cache_ttl_seconds=assistant.research_search_cache_ttl_seconds,
        extraction_cache_ttl_seconds=assistant.research_extraction_cache_ttl_seconds,
        raw_snapshot_retention_days=assistant.research_raw_retention_days,
        source_manifest_retention_days=assistant.research_manifest_retention_days,
        planner_receives_conversation_history=False,
        synthesis_receives_raw_page_bodies=False,
    )
    return ResearchRuntimeSettings(
        default_mode=assistant.research_default_mode,
        provider=assistant.research_provider,
        provider_fallbacks=tuple(assistant.research_provider_fallbacks),
        max_results=assistant.research_max_results,
        max_steps=assistant.research_max_steps,
        max_queries=assistant.research_max_queries,
        max_sources=assistant.research_max_sources,
        max_extracts=assistant.research_max_extracts,
        show_diagnostics=assistant.research_show_diagnostics,
        deep_enabled=assistant.research_deep_enabled,
        hermes_planner_enabled=assistant.research_hermes_planner_enabled,
        policy=policy,
    )


def apply_research_planner_environment(settings: ResearchRuntimeSettings) -> None:
    """Expose the central planner preference to the existing planner feature boundary."""

    if settings.hermes_planner_enabled:
        environment().setdefault("OMNIX_DEEP_RESEARCH_HERMES_ENABLED", "1")
