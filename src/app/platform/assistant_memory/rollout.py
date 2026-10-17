"""Reversible staged rollout policy for companion memory capabilities."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .settings import AssistantMemoryRuntimeSettings, CompanionRolloutStage

_STAGE_ORDER: tuple[CompanionRolloutStage, ...] = (
    "authority_only",
    "shadow",
    "read_only_pilot",
    "explicit_typed",
    "review_required",
    "automatic_assertions",
    "gentle_initiative",
    "active_initiative",
    "paralinguistic_pilot",
)


class CompanionRolloutPolicy(BaseModel):
    """Effective features for one server process; disabling never deletes memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: CompanionRolloutStage
    master_enabled: bool
    authority_enabled: bool
    shadow_metrics_enabled: bool
    memory_read_enabled: bool
    explicit_typed_memory_enabled: bool
    review_candidates_enabled: bool
    automatic_direct_assertions_enabled: bool
    proactive_memory_enabled: bool
    active_initiative_enabled: bool
    paralinguistic_signals_enabled: bool

    def content_free_diagnostics(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def _at_least(stage: CompanionRolloutStage, required: CompanionRolloutStage) -> bool:
    return _STAGE_ORDER.index(stage) >= _STAGE_ORDER.index(required)


def companion_rollout_policy(
    settings: AssistantMemoryRuntimeSettings,
) -> CompanionRolloutPolicy:
    stage = settings.companion_rollout_stage
    master = settings.companion_master_enabled
    if not master:
        return CompanionRolloutPolicy(
            stage=stage,
            master_enabled=False,
            authority_enabled=True,
            shadow_metrics_enabled=False,
            memory_read_enabled=False,
            explicit_typed_memory_enabled=False,
            review_candidates_enabled=False,
            automatic_direct_assertions_enabled=False,
            proactive_memory_enabled=False,
            active_initiative_enabled=False,
            paralinguistic_signals_enabled=False,
        )
    proactive = settings.proactive_memory_enabled and _at_least(stage, "gentle_initiative")
    return CompanionRolloutPolicy(
        stage=stage,
        master_enabled=True,
        authority_enabled=True,
        shadow_metrics_enabled=_at_least(stage, "shadow"),
        memory_read_enabled=_at_least(stage, "read_only_pilot"),
        explicit_typed_memory_enabled=_at_least(stage, "explicit_typed"),
        review_candidates_enabled=_at_least(stage, "review_required"),
        automatic_direct_assertions_enabled=(
            settings.automatic_direct_assertion_memory
            and _at_least(stage, "automatic_assertions")
        ),
        proactive_memory_enabled=proactive,
        active_initiative_enabled=proactive and _at_least(stage, "active_initiative"),
        paralinguistic_signals_enabled=(
            settings.paralinguistic_signals_enabled
            and _at_least(stage, "paralinguistic_pilot")
        ),
    )


__all__ = ["CompanionRolloutPolicy", "companion_rollout_policy"]
