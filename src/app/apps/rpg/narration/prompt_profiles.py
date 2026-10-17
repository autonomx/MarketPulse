"""Prompt/model profile contracts for RPG LLM tasks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping, cast

RpgPromptTask = Literal[
    "intent_classification",
    "narration",
    "npc_dialogue",
    "combat_narration",
    "memory_summary",
    "journal_recap",
    "quality_rewrite",
    "grounding_audit",
    "image_prompt",
]

ExecutionMode = Literal["blocking", "background"]


@dataclass(frozen=True)
class RpgPromptProfile:
    """Runtime settings for one RPG prompt task."""

    task: RpgPromptTask
    profile_id: str
    provider: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    retry_count: int = 0
    streaming: bool = False
    execution_mode: ExecutionMode = "blocking"

    def with_overrides(self, **overrides: object) -> "RpgPromptProfile":
        allowed = set(self.__dataclass_fields__)
        unknown = sorted(key for key in overrides if key not in allowed)
        if unknown:
            raise ValueError(f"unknown RPG prompt profile override(s): {', '.join(unknown)}")
        return replace(self, **cast(dict[str, Any], overrides))

    def debug_payload(self, *, latency_ms: float | None = None, status: str = "configured") -> dict[str, Any]:
        return {
            "task": self.task,
            "profile_id": self.profile_id,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "streaming": self.streaming,
            "execution_mode": self.execution_mode,
            "latency_ms": latency_ms,
            "status": status,
        }


DEFAULT_RPG_PROMPT_PROFILES: tuple[RpgPromptProfile, ...] = (
    RpgPromptProfile("intent_classification", "rpg-intent-fast", "lmstudio", "fast-local", 0.0, 256, 4.0),
    RpgPromptProfile("narration", "rpg-narration-primary", "lmstudio", "story-local", 0.7, 900, 18.0, streaming=True),
    RpgPromptProfile("npc_dialogue", "rpg-dialogue-primary", "lmstudio", "story-local", 0.65, 700, 15.0, streaming=True),
    RpgPromptProfile("combat_narration", "rpg-combat-primary", "lmstudio", "story-local", 0.55, 650, 12.0),
    RpgPromptProfile("memory_summary", "rpg-memory-background", "lmstudio", "fast-local", 0.2, 400, 8.0, execution_mode="background"),
    RpgPromptProfile("journal_recap", "rpg-journal-background", "lmstudio", "fast-local", 0.25, 500, 8.0, execution_mode="background"),
    RpgPromptProfile("quality_rewrite", "rpg-quality-rewrite", "lmstudio", "story-local", 0.45, 600, 10.0, retry_count=1),
    RpgPromptProfile("grounding_audit", "rpg-grounding-audit", "lmstudio", "fast-local", 0.0, 350, 6.0, execution_mode="background"),
    RpgPromptProfile("image_prompt", "rpg-image-prompt", "lmstudio", "fast-local", 0.35, 300, 6.0, execution_mode="background"),
)


def default_rpg_prompt_profile_registry() -> dict[RpgPromptTask, RpgPromptProfile]:
    return {profile.task: profile for profile in DEFAULT_RPG_PROMPT_PROFILES}


def resolve_rpg_prompt_profile(
    task: RpgPromptTask,
    *,
    registry: Mapping[RpgPromptTask, RpgPromptProfile] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> RpgPromptProfile:
    active_registry = registry or default_rpg_prompt_profile_registry()
    try:
        profile = active_registry[task]
    except KeyError as exc:
        raise KeyError(f"missing RPG prompt profile for task: {task}") from exc
    if not overrides:
        return profile
    return profile.with_overrides(**dict(overrides))


