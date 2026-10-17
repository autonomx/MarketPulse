"""Strict provider contract for foreground RPG semantic action packets."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

ActionType = Literal[
    "attack_unarmed",
    "attack_melee",
    "attack_ranged",
    "block",
    "dodge",
    "parry",
    "persuade",
    "intimidate",
    "deceive",
    "sneak",
    "investigate",
    "hack",
    "cast_spell",
    "use_item",
    "pickup_item",
    "drop_item",
    "equip_item",
    "unequip_item",
    "observe",
    "social_activity",
    "social_competition",
    "social_affection",
    "social_performance",
    "trade",
    "ritual",
    "exploration",
    "threat",
    "service_inquiry",
    "service_purchase",
    "service_consumption",
    "duration_action",
]
SemanticFamily = Literal[
    "combat",
    "defense",
    "social",
    "trade",
    "commerce",
    "ritual",
    "exploration",
    "stealth",
    "magic",
    "technical",
    "item",
    "threat",
    "observation",
]
InteractionMode = Literal["solo", "direct", "group", "public"]
UtteranceMode = Literal[
    "action_request",
    "casual_conversation",
    "clarification",
    "emotional_expression",
    "greeting",
    "identity_inquiry",
    "local_knowledge",
    "lore_question",
    "opinion_question",
    "wellbeing_inquiry",
]
RiskDomain = Literal[
    "none",
    "combat",
    "commerce",
    "inventory",
    "item",
    "persuasion_outcome",
    "quest",
    "relationship_change",
    "reward",
    "service",
    "threat",
    "travel",
    "unknown",
]


class _StrictSemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticActionIntent(_StrictSemanticModel):
    action_type: ActionType
    target_id: str = Field(default="", max_length=180)
    target_name: str = Field(default="", max_length=80)
    service_kind: str = Field(default="", max_length=48)
    offer_id: str = Field(default="", max_length=120)
    confirmation: StrictBool = False
    duration_policy: str = Field(default="", max_length=64)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list, max_length=6)
    stateful: StrictBool
    needs_runtime_resolution: StrictBool


class SemanticAdvisory(_StrictSemanticModel):
    semantic_family: SemanticFamily
    interaction_mode: InteractionMode
    activity_label: str = Field(default="", max_length=64)
    utterance_mode: UtteranceMode
    literal_action_requested: StrictBool
    state_mutation_requested: StrictBool
    risk_domain: RiskDomain
    intent_summary: str = Field(default="", max_length=220)
    evidence_spans: list[str] = Field(default_factory=list, max_length=6)


class SemanticDialogueGate(_StrictSemanticModel):
    safe_to_display_now: StrictBool
    reason: str = Field(default="", max_length=160)
    risk_flags: list[str] = Field(default_factory=list, max_length=8)


class SemanticNpcLine(_StrictSemanticModel):
    speaker: str = Field(default="", max_length=80)
    line: str = Field(default="", max_length=900)


class SemanticNarrationCandidate(_StrictSemanticModel):
    narration: str = Field(default="", max_length=500)
    npc: SemanticNpcLine = Field(default_factory=SemanticNpcLine)


class SemanticPacketEnvelope(_StrictSemanticModel):
    action_intent: SemanticActionIntent
    semantic_advisory: SemanticAdvisory
    dialogue_gate: SemanticDialogueGate
    final_narration_candidate: SemanticNarrationCandidate
    reason: str = Field(default="", max_length=1000)


__all__ = [
    "SemanticActionIntent",
    "SemanticAdvisory",
    "SemanticDialogueGate",
    "SemanticNarrationCandidate",
    "SemanticNpcLine",
    "SemanticPacketEnvelope",
]
