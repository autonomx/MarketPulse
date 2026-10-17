"""Typed platform replay/persistence contracts."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ReplayPrimitiveKind = Literal[
    "nondeterministic_input",
    "provider_recording",
    "state_hash",
    "replay_comparison",
    "divergence_error",
    "checkpoint",
    "run_artifact",
    "session_persistence",
    "migration",
]


class ReplayPrimitive(BaseModel):
    kind: ReplayPrimitiveKind
    source: str
    owner_module: str
    behavior: str
    compatibility_policy: str
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class ReplayPrimitiveList(BaseModel):
    primitives: list[ReplayPrimitive]


class StateHashRequest(BaseModel):
    state: dict[str, Any]


class StateHashResponse(BaseModel):
    hash: str
    source: str
    format_version: str


class CheckpointEnvelope(BaseModel):
    checkpoint_id: str
    version: str
    source: str
    checksum: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class CheckpointBundleRequest(BaseModel):
    """Extensible state bundle with explicit checkpoint control fields."""

    model_config = ConfigDict(extra="allow")

    turn_index: int | None = Field(default=None, ge=0)
    state_versions: dict[str, Any] = Field(default_factory=dict)


class PersistenceInventory(BaseModel):
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
