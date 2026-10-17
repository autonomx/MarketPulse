"""Shared RPG ability schema models and constants."""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

Capability = Literal["combat", "recon", "influence", "technical", "survival", "knowledge", "support", "custom"]
PowerSource = Literal["mundane", "martial", "magic", "technology", "psionic", "divine", "occult", "mutation", "mythic", "social_power", "scrap", "custom"]
GameplayDimension = Literal["resources", "information", "relationships", "access", "environment", "position", "narrative", "economy", "world"]
AbilityKind = Literal["active", "passive", "narrative_trait"]
AbilityPurpose = Literal[
    "damage",
    "defense",
    "healing",
    "information_gathering",
    "mobility",
    "social_manipulation",
    "access_bypass",
    "crafting",
    "resource_generation",
    "control",
    "escape",
    "summoning",
    "world_influence",
    "quest_progression",
    "economic_advantage",
    "relationship_building",
    "survival",
    "utility",
]

ALLOWED_CAPABILITIES = set(get_args(Capability))
ALLOWED_POWER_SOURCES = set(get_args(PowerSource))
ALLOWED_DIMENSIONS = set(get_args(GameplayDimension))
ALLOWED_KINDS = set(get_args(AbilityKind))
ALLOWED_PURPOSES = set(get_args(AbilityPurpose))
ALLOWED_EFFECT_OPS = {
    "resource_delta",
    "modify_next_check",
    "apply_status",
    "clear_status",
    "reveal_clue",
    "unlock_dialogue_option",
    "unlock_travel_option",
    "unlock_scene_affordance",
    "modify_relationship",
    "modify_reputation",
    "modify_faction_alert",
    "modify_price_modifier",
    "apply_scene_status",
    "create_hazard",
    "clear_hazard",
    "change_location_state",
    "add_world_rumor",
    "advance_quest_signal",
    "complete_objective",
    "grant_temp_affordance",
}
ALLOWED_COST_RESOURCES = {"hp", "stamina", "mana", "gold", "silver", "copper", "renown"}
ALLOWED_XP_SOURCES = {"quest", "objective", "kill"}
DEFAULT_SKILL_XP_PER_ABILITY_USE = 5


class RpgCharacterIdentity(BaseModel):
    genre: str = "classic_fantasy"
    tone: str = "heroic adventure"
    background: str = "Wanderer"
    primary_capability: Capability = "recon"
    secondary_capabilities: list[Capability] = Field(default_factory=list)
    power_source: PowerSource = "martial"
    generated_class_name: str = "Ranger"
    generated_class_summary: str = "A capable character whose talents alter deterministic gameplay dimensions."


class RpgEffectOp(BaseModel):
    dimension: GameplayDimension
    op: str
    target: str | None = None
    target_id: str | None = None
    amount: int | None = None
    resource: str | None = None
    check: str | None = None
    status: str | None = None
    hazard: str | None = None
    clue_tag: str | None = None
    affordance: str | None = None
    option_tag: str | None = None
    duration_turns: int | None = None
    strength: int | None = None
    relationship: str | None = None
    tag: str | None = None
    faction_id: str | None = None
    location_id: str | None = None
    quest_id: str | None = None
    objective_id: str | None = None
    state_key: str | None = None
    state_value: str | int | bool | None = None
    rumor_id: str | None = None
    rumor: str | None = None
    signal: str | None = None


class RpgAbilityDefinition(BaseModel):
    ability_id: str
    kind: AbilityKind = "active"
    name: str
    icon: str = "✦"
    description: str
    capability: Capability
    power_source: PowerSource = "mundane"
    purpose: AbilityPurpose
    dimensions: list[GameplayDimension]
    level_required: int = 1
    rank: int = 1
    max_rank: int = 3
    resource_cost: dict[str, int] = Field(default_factory=dict)
    cooldown_turns: int = 0
    prerequisites: list[str] = Field(default_factory=list)
    targeting: dict[str, Any] = Field(default_factory=dict)
    effect_ops: list[RpgEffectOp] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    influence_tags: list[str] = Field(default_factory=list)
    flavor_tags: list[str] = Field(default_factory=list)


class RpgAbilityValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)


class RpgAbilityUseResult(BaseModel):
    ok: bool
    ability_id: str | None = None
    name: str | None = None
    detail: str
    error: str | None = None
    effects: list[dict[str, Any]] = Field(default_factory=list)


class RpgAbilityStateResult(BaseModel):
    ok: bool
    ability_id: str | None = None
    detail: str
    error: str | None = None
    slot: str | None = None
    ability_state: dict[str, Any] = Field(default_factory=dict)


class RpgProgressionResult(BaseModel):
    ok: bool
    detail: str
    error: str | None = None
    xp_gained: int = 0
    source: str | None = None
    level_ups: list[dict[str, Any]] = Field(default_factory=list)
    ability_points_granted: int = 0
    skill_awards: dict[str, dict[str, int]] = Field(default_factory=dict)
    skill_level_ups: list[dict[str, Any]] = Field(default_factory=list)
