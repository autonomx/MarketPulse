"""Typed immutable contracts for reusable RPG worlds and campaign launches."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Final, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

WORLD_CONTRACT_VERSION: Final = "rpg_world_contract_v1"
WORLD_RELEASE_VERSION: Final = "rpg_world_release_v1"
SCENARIO_CONTRACT_VERSION: Final = "rpg_scenario_contract_v1"
CAMPAIGN_LAUNCH_VERSION: Final = "rpg_campaign_launch_v1"
WorldArtifactStage = Literal[
    "canon_validated",
    "runtime_seeded",
    "materialized",
    "playtested",
]


def canonical_payload(value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return dict(value)


def canonical_content_hash(value: BaseModel | Mapping[str, Any]) -> str:
    encoded = json.dumps(
        canonical_payload(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorldProjectCreate(FrozenContract):
    contract_version: Literal["rpg_world_contract_v1"] = WORLD_CONTRACT_VERSION
    world_id: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    source_mode: Literal["manual", "ai", "hybrid", "imported"] = "manual"
    genre: str = "classic_fantasy"
    tone: str = "heroic adventure"
    seed: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorldRevisionDocument(FrozenContract):
    contract_version: Literal["rpg_world_contract_v1"] = WORLD_CONTRACT_VERSION
    world_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    title: str = Field(min_length=1)
    canon: dict[str, Any] = Field(default_factory=dict)
    entity_manifest: dict[str, Any] = Field(default_factory=dict)
    topology: dict[str, Any] = Field(default_factory=dict)
    adventure_seeds: tuple[dict[str, Any], ...] = ()
    blueprint_requirements: tuple[dict[str, Any], ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = ""

    @model_validator(mode="after")
    def validate_hash(self) -> "WorldRevisionDocument":
        if self.content_hash and not self.content_hash.startswith("sha256:"):
            raise ValueError("world_revision_hash_invalid")
        return self


class MapDefinitionBinding(FrozenContract):
    map_id: str = Field(min_length=1)
    blueprint_revision: int = Field(ge=1)
    definition_revision: int = Field(ge=1)
    definition_hash: str = Field(pattern=r"^sha256:")
    semantic_interface_hash: str = Field(pattern=r"^sha256:")
    simulation_readiness: Literal[
        "stub", "semantic", "navigable", "certified", "failed"
    ] = "certified"
    presentation_readiness: Literal[
        "placeholder", "assets_pending", "ready", "failed"
    ] = "placeholder"


class WorldReleaseDocument(FrozenContract):
    release_version: Literal["rpg_world_release_v1"] = WORLD_RELEASE_VERSION
    world_id: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    release: int = Field(ge=1)
    world_revision_hash: str = Field(pattern=r"^sha256:")
    map_bindings: tuple[MapDefinitionBinding, ...] = ()
    indexes: dict[str, Any] = Field(default_factory=dict)
    asset_bindings: dict[str, Any] = Field(default_factory=dict)
    compiler_provenance: dict[str, Any] = Field(default_factory=dict)
    certification: dict[str, Any] = Field(default_factory=dict)
    artifact_stage: WorldArtifactStage = "canon_validated"
    runtime_seed: dict[str, Any] = Field(default_factory=dict)
    materialization: dict[str, Any] = Field(default_factory=dict)
    playtest_report: dict[str, Any] = Field(default_factory=dict)
    release_hash: str = ""

    @model_validator(mode="after")
    def unique_maps(self) -> "WorldReleaseDocument":
        ids = [binding.map_id for binding in self.map_bindings]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_world_release_map_binding")
        if self.release_hash and not self.release_hash.startswith("sha256:"):
            raise ValueError("world_release_hash_invalid")
        for field_name in ("runtime_seed", "materialization", "playtest_report"):
            payload = getattr(self, field_name)
            content_hash = str(payload.get("content_hash") or "")
            if content_hash and not content_hash.startswith("sha256:"):
                raise ValueError(f"{field_name}_hash_invalid")
        return self


class MapInitializationOperation(FrozenContract):
    operation_id: str = Field(min_length=1)
    map_id: str = Field(min_length=1)
    type: Literal[
        "set_object_state",
        "place_actor",
        "set_route_state",
        "set_hazard_state",
    ]
    target_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ScenarioProjectCreate(FrozenContract):
    contract_version: Literal["rpg_scenario_contract_v1"] = SCENARIO_CONTRACT_VERSION
    scenario_id: str | None = Field(default=None, min_length=1)
    world_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScenarioRevisionDocument(FrozenContract):
    contract_version: Literal["rpg_scenario_contract_v1"] = SCENARIO_CONTRACT_VERSION
    scenario_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    world_id: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    world_revision_hash: str = Field(pattern=r"^sha256:")
    compatible_release: int | None = Field(default=None, ge=1)
    starting_epoch: str = ""
    starting_location_id: str = Field(min_length=1)
    activated_conflict_ids: tuple[str, ...] = ()
    initial_npc_ids: tuple[str, ...] = ()
    protagonist_options: tuple[dict[str, Any], ...] = ()
    starting_resources: dict[str, Any] = Field(default_factory=dict)
    opening_seed_ids: tuple[str, ...] = ()
    map_initialization: tuple[MapInitializationOperation, ...] = ()
    runtime_seed_hash: str = ""
    content_hash: str = ""

    @model_validator(mode="after")
    def unique_initialization_operations(self) -> "ScenarioRevisionDocument":
        ids = [operation.operation_id for operation in self.map_initialization]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_scenario_map_initialization_operation")
        if self.runtime_seed_hash and not self.runtime_seed_hash.startswith("sha256:"):
            raise ValueError("scenario_runtime_seed_hash_invalid")
        if self.content_hash and not self.content_hash.startswith("sha256:"):
            raise ValueError("scenario_revision_hash_invalid")
        return self


class CampaignWorldBinding(FrozenContract):
    campaign_id: str = Field(min_length=1)
    world_id: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    world_revision_hash: str = Field(pattern=r"^sha256:")
    world_release: int = Field(ge=1)
    world_release_hash: str = Field(pattern=r"^sha256:")
    scenario_id: str = Field(min_length=1)
    scenario_revision: int = Field(ge=1)
    scenario_revision_hash: str = Field(pattern=r"^sha256:")
    map_definition_pins: dict[str, str] = Field(default_factory=dict)


class CampaignLaunchContract(FrozenContract):
    contract_version: Literal["rpg_campaign_launch_v1"] = CAMPAIGN_LAUNCH_VERSION
    campaign_id: str = Field(min_length=1)
    campaign_title: str = Field(min_length=1)
    world_id: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    world_release: int = Field(ge=1)
    scenario_id: str = Field(min_length=1)
    scenario_revision: int = Field(ge=1)
    protagonist: dict[str, Any] = Field(default_factory=dict)
    gameplay: dict[str, Any] = Field(default_factory=dict)
    runtime_features: dict[str, bool] = Field(default_factory=dict)


