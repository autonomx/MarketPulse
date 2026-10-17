from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping


class RolloutStage(IntEnum):
    SHADOW = 0
    RENDERER_ONLY = 1
    ELIGIBILITY_QUALITY = 2
    COMPACT_CONTEXT = 3
    LOCAL_RECOVERY = 4
    HERMES_RECOVERY = 5
    EPHEMERAL_PROPOSALS = 6
    PERSISTENT_PROMOTION = 7
    VALIDATED_DELIVERY = 8
    CANONICAL_DEFAULT = 9
    LEGACY_REMOVED = 10


_STAGE_FLAGS: dict[RolloutStage, tuple[str, ...]] = {
    RolloutStage.SHADOW: ("shadow_compare",),
    RolloutStage.RENDERER_ONLY: ("canonical_renderer",),
    RolloutStage.ELIGIBILITY_QUALITY: ("hard_eligibility", "final_quality_cycle"),
    RolloutStage.COMPACT_CONTEXT: ("compact_context", "typed_claim_ledger"),
    RolloutStage.LOCAL_RECOVERY: ("local_retrieval", "narrative_affordances"),
    RolloutStage.HERMES_RECOVERY: ("bounded_hermes_recovery",),
    RolloutStage.EPHEMERAL_PROPOSALS: ("turn_scene_soft_truth",),
    RolloutStage.PERSISTENT_PROMOTION: ("deterministic_promotion",),
    RolloutStage.VALIDATED_DELIVERY: ("validated_delivery",),
    RolloutStage.CANONICAL_DEFAULT: ("canonical_default",),
    RolloutStage.LEGACY_REMOVED: ("legacy_removed",),
}


@dataclass(frozen=True)
class RolloutConfig:
    stage: RolloutStage
    enabled_flags: tuple[str, ...]
    rollback_stage: RolloutStage
    publishes_canonical: bool
    legacy_available: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.name.casefold(),
            "stage_value": int(self.stage),
            "enabled_flags": list(self.enabled_flags),
            "rollback_stage": self.rollback_stage.name.casefold(),
            "publishes_canonical": self.publishes_canonical,
            "legacy_available": self.legacy_available,
        }


@dataclass(frozen=True)
class RolloutEvidence:
    production_turns: int = 0
    exact_head_checks_passed: bool = False
    release_gate_passed: bool = False
    shadow_mismatch_rate: float = 1.0
    rollback_tested: bool = False


class ResponseRolloutController:
    def config(self, stage: RolloutStage | str | int) -> RolloutConfig:
        resolved = coerce_rollout_stage(stage)
        flags = tuple(
            flag
            for candidate in RolloutStage
            if candidate <= resolved
            for flag in _STAGE_FLAGS[candidate]
        )
        rollback = (
            RolloutStage.SHADOW
            if resolved is RolloutStage.SHADOW
            else RolloutStage(int(resolved) - 1)
        )
        return RolloutConfig(
            stage=resolved,
            enabled_flags=flags,
            rollback_stage=rollback,
            publishes_canonical=resolved >= RolloutStage.RENDERER_ONLY,
            legacy_available=resolved < RolloutStage.LEGACY_REMOVED,
        )

    def rollback(
        self,
        config: RolloutConfig,
        target: RolloutStage | str | int | None = None,
    ) -> RolloutConfig:
        resolved = (
            coerce_rollout_stage(target)
            if target is not None
            else config.rollback_stage
        )
        if resolved > config.stage:
            raise ValueError("rollback target cannot be ahead of current stage")
        return self.config(resolved)

    def compare(
        self,
        *,
        turn_id: str,
        legacy_text: str,
        canonical_text: str,
        authoritative_state_hash_before: str,
        authoritative_state_hash_after: str,
    ) -> dict[str, Any]:
        return {
            "format_version": "rpg_response_rollout_compare_v1",
            "turn_id": turn_id,
            "legacy_text": legacy_text,
            "canonical_text": canonical_text,
            "visible_text_changed": legacy_text.strip() != canonical_text.strip(),
            "authoritative_state_unchanged": (
                authoritative_state_hash_before == authoritative_state_hash_after
            ),
        }

def coerce_rollout_stage(value: RolloutStage | str | int) -> RolloutStage:
    if isinstance(value, RolloutStage):
        return value
    if isinstance(value, int):
        return RolloutStage(value)
    normalized = str(value or "shadow").strip().casefold().replace("-", "_")
    aliases = {
        "renderer": "renderer_only",
        "quality": "eligibility_quality",
        "context": "compact_context",
        "local": "local_recovery",
        "hermes": "hermes_recovery",
        "ephemeral": "ephemeral_proposals",
        "persistent": "persistent_promotion",
        "delivery": "validated_delivery",
        "canonical": "canonical_default",
    }
    normalized = aliases.get(normalized, normalized)
    try:
        return RolloutStage[normalized.upper()]
    except KeyError as exc:
        raise ValueError(f"unknown response rollout stage: {value}") from exc


def rollout_stage_from_context(
    context: Mapping[str, Any] | None,
    *,
    default: RolloutStage = RolloutStage.SHADOW,
) -> RolloutStage:
    if not isinstance(context, Mapping):
        return default
    value = context.get("response_rollout_stage")
    return default if value in (None, "") else coerce_rollout_stage(value)
