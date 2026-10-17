"""Typed Campaign Genesis Contract v2 models.

The genesis contract is declarative player/scenario intent. It is the durable
input to compiler/bootstrap phases and must not depend on rendered summary text
for machine-readable state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

CAMPAIGN_GENESIS_CONTRACT_VERSION: Final = "rpg_genesis_v2"
DEFAULT_GENESIS_CREATED_BY = "wizard_v2"


class GenesisIdentity(BaseModel):
    """Stable identity/origin facts for a campaign protagonist."""

    name: str = "Alyndra"
    pronouns: str = "she/her"
    background: str = "Wanderer"
    origin: str = "unknown_origin"
    power_source: str | None = None


class GenesisMotivation(BaseModel):
    """Motivation is mutable later, but the starting value is genesis data."""

    primary: str = "survival"
    target: str | None = None
    intensity: int = 100
    fulfilled: bool = False


class GenesisTalent(BaseModel):
    id: str
    rank: int = 1


class GenesisDrivers(BaseModel):
    """Story/simulation drivers that can evolve after session start."""

    archetype: str = "balanced_adventurer"
    motivation: GenesisMotivation = Field(default_factory=GenesisMotivation)
    flaw: str | None = None
    talents: list[GenesisTalent] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)


class GenesisInitialStats(BaseModel):
    strength: int = 10
    agility: int = 10
    endurance: int = 10
    intellect: int = 10
    charisma: int = 10
    perception: int = 10
    archery: int = 8
    survival: int = 8


class GenesisStoryOptions(BaseModel):
    opening_hook: str | None = None
    opening_pace: str | None = None
    relationship_preset: str | None = None


class GenesisWorldOptions(BaseModel):
    world_profile: str | None = None
    starting_location: str = "rusty_flagon_tavern"
    difficulty: Literal["story", "normal", "harsh"] = "normal"
    world_activity: Literal["quiet", "standard", "living_world"] = "standard"
    economy_pressure: Literal["relaxed", "normal", "strict"] = "normal"
    combat_lethality: Literal["safe", "normal", "deadly"] = "normal"
    seed: int | None = None


class GenesisWorldForgeOptions(BaseModel):
    """Campaign-scoped rich world generation policy."""

    enabled: bool = True
    depth: Literal["quick", "standard", "epic"] = "standard"
    background_expansion: bool = False
    use_hermes: bool = True
    require_consistency_audit: bool = True
    require_opening_dossiers: bool = True
    max_parallel_jobs: int | None = None
    custom_directives: list[str] = Field(default_factory=list)


class GenesisSystemOptions(BaseModel):
    autosave: bool = True
    companions: bool = True
    permadeath: bool = False
    validator: bool = True
    background_soft_audit: bool = True
    llm_narration: bool = True
    image_generation: bool = False
    tts: bool = False
    stt: bool = False


class CampaignGenesisContract(BaseModel):
    """Authoritative v2 input format for deterministic campaign creation."""

    contract_version: Literal["rpg_genesis_v2"] = CAMPAIGN_GENESIS_CONTRACT_VERSION
    campaign_template: str = "classic_fantasy"
    genre: str | None = None
    tone: str = "heroic adventure"
    identity: GenesisIdentity = Field(default_factory=GenesisIdentity)
    drivers: GenesisDrivers = Field(default_factory=GenesisDrivers)
    initial_stats: GenesisInitialStats = Field(default_factory=GenesisInitialStats)
    starter_gear_tags: list[str] = Field(default_factory=list)
    story_options: GenesisStoryOptions = Field(default_factory=GenesisStoryOptions)
    world_options: GenesisWorldOptions = Field(default_factory=GenesisWorldOptions)
    world_forge: GenesisWorldForgeOptions = Field(default_factory=GenesisWorldForgeOptions)
    system_options: GenesisSystemOptions = Field(default_factory=GenesisSystemOptions)


def canonical_genesis_payload(contract: CampaignGenesisContract) -> dict[str, Any]:
    """Return the normalized payload that should be hashed for provenance."""

    return contract.model_dump(mode="json", exclude_none=True)


def genesis_contract_hash(contract: CampaignGenesisContract) -> str:
    """Hash the canonical declarative contract, not compiled/session state."""

    encoded = json.dumps(
        canonical_genesis_payload(contract),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
