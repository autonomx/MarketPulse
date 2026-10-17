"""Release controls for Web Research capabilities."""
from __future__ import annotations

from app.config.env import env_str, environment

import hashlib
import os
from typing import Literal

from pydantic import BaseModel, Field

from .contracts import ResearchMode
from .settings import ResearchRuntimeSettings

ReleaseDecisionStatus = Literal["allowed", "downgraded", "unavailable"]


class ResearchReleasePolicy(BaseModel):
    master_enabled: bool = True
    quick_enabled: bool = True
    quick_percentage: int = Field(default=100, ge=0, le=100)
    deep_local_enabled: bool = True
    deep_local_percentage: int = Field(default=100, ge=0, le=100)
    hermes_enabled: bool = False
    hermes_percentage: int = Field(default=0, ge=0, le=100)


class ResearchReleaseDecision(BaseModel):
    requested_mode: ResearchMode
    effective_mode: ResearchMode
    status: ReleaseDecisionStatus
    reason: str
    warnings: list[str] = Field(default_factory=list)
    use_hermes_planner: bool = False


class ResearchReleaseAvailability(BaseModel):
    disabled: bool = True
    quick: bool
    deep: bool
    hermes_planner: bool


def research_release_policy_from_env() -> ResearchReleasePolicy:
    return ResearchReleasePolicy(
        master_enabled=_env_bool("OMNIX_RESEARCH_ENABLED", True),
        quick_enabled=_env_bool("OMNIX_RESEARCH_QUICK_ENABLED", True),
        quick_percentage=_env_percent("OMNIX_RESEARCH_QUICK_PERCENT", 100),
        deep_local_enabled=_env_bool("OMNIX_RESEARCH_DEEP_LOCAL_ENABLED", True),
        deep_local_percentage=_env_percent("OMNIX_RESEARCH_DEEP_LOCAL_PERCENT", 100),
        hermes_enabled=_env_bool("OMNIX_RESEARCH_HERMES_ROLLOUT_ENABLED", False),
        hermes_percentage=_env_percent("OMNIX_RESEARCH_HERMES_PERCENT", 0),
    )


def research_release_availability(
    settings: ResearchRuntimeSettings,
    policy: ResearchReleasePolicy,
    *,
    identity: str,
) -> ResearchReleaseAvailability:
    quick = _quick_available(settings, policy, identity)
    deep = _deep_available(settings, policy, identity)
    hermes = (
        deep
        and settings.hermes_planner_enabled
        and policy.hermes_enabled
        and _in_cohort(identity, policy.hermes_percentage, "hermes")
    )
    return ResearchReleaseAvailability(
        quick=quick,
        deep=deep,
        hermes_planner=hermes,
    )


def resolve_research_release(
    requested_mode: ResearchMode,
    settings: ResearchRuntimeSettings,
    policy: ResearchReleasePolicy,
    *,
    identity: str,
    allow_downgrade: bool = False,
) -> ResearchReleaseDecision:
    if requested_mode == "disabled":
        return ResearchReleaseDecision(
            requested_mode="disabled",
            effective_mode="disabled",
            status="allowed",
            reason="research_disabled_for_turn",
        )

    availability = research_release_availability(settings, policy, identity=identity)
    if requested_mode == "quick":
        if availability.quick:
            return ResearchReleaseDecision(
                requested_mode="quick",
                effective_mode="quick",
                status="allowed",
                reason="quick_research_available",
            )
        return ResearchReleaseDecision(
            requested_mode="quick",
            effective_mode="disabled",
            status="unavailable",
            reason=_unavailable_reason(settings, policy, "quick", identity),
        )

    if availability.deep:
        return ResearchReleaseDecision(
            requested_mode="deep",
            effective_mode="deep",
            status="allowed",
            reason="deep_research_available",
            use_hermes_planner=availability.hermes_planner,
        )
    if allow_downgrade and availability.quick:
        return ResearchReleaseDecision(
            requested_mode="deep",
            effective_mode="quick",
            status="downgraded",
            reason=_unavailable_reason(settings, policy, "deep", identity),
            warnings=["deep_research_downgraded_to_quick"],
        )
    return ResearchReleaseDecision(
        requested_mode="deep",
        effective_mode="disabled",
        status="unavailable",
        reason=_unavailable_reason(settings, policy, "deep", identity),
    )


def research_release_notice(decision: ResearchReleaseDecision) -> str | None:
    if decision.status != "downgraded":
        return None
    return (
        "Deep Research was unavailable, so this turn used Quick Search because "
        "you explicitly allowed a downgrade."
    )


def _quick_available(
    settings: ResearchRuntimeSettings,
    policy: ResearchReleasePolicy,
    identity: str,
) -> bool:
    return (
        policy.master_enabled
        and policy.quick_enabled
        and settings.provider_available
        and _in_cohort(identity, policy.quick_percentage, "quick")
    )


def _deep_available(
    settings: ResearchRuntimeSettings,
    policy: ResearchReleasePolicy,
    identity: str,
) -> bool:
    return (
        policy.master_enabled
        and policy.deep_local_enabled
        and settings.deep_enabled
        and settings.provider_available
        and _in_cohort(identity, policy.deep_local_percentage, "deep_local")
    )


def _unavailable_reason(
    settings: ResearchRuntimeSettings,
    policy: ResearchReleasePolicy,
    mode: Literal["quick", "deep"],
    identity: str,
) -> str:
    if not policy.master_enabled:
        return "research_master_rollback_active"
    if not settings.provider_available:
        return "research_provider_unavailable"
    if mode == "quick":
        if not policy.quick_enabled:
            return "quick_research_release_disabled"
        if not _in_cohort(identity, policy.quick_percentage, "quick"):
            return "quick_research_outside_release_cohort"
        return "quick_research_unavailable"
    if not settings.deep_enabled:
        return "deep_research_disabled_in_settings"
    if not policy.deep_local_enabled:
        return "deep_research_release_disabled"
    if not _in_cohort(identity, policy.deep_local_percentage, "deep_local"):
        return "deep_research_outside_release_cohort"
    return "deep_research_unavailable"


def _in_cohort(identity: str, percentage: int, salt: str) -> bool:
    if percentage <= 0:
        return False
    if percentage >= 100:
        return True
    normalized = str(identity or "anonymous").strip() or "anonymous"
    digest = hashlib.sha256(f"{salt}:{normalized}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return bucket < percentage


def _env_bool(name: str, default: bool) -> bool:
    value = environment().get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_percent(name: str, default: int) -> int:
    try:
        value = int(environment().get(name, str(default)))
    except ValueError:
        value = default
    return max(0, min(100, value))
