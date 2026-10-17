"""Pure deterministic reducer for typed causal world-state deltas."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .causal_state import mutable_dimension_registry, validate_mutable_world_state


class WorldStateDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    delta_id: str = Field(min_length=1)
    tick: int = Field(ge=0)
    sequence: int = Field(default=0, ge=0)
    target_id: str = Field(min_length=1)
    dimension_id: str = Field(min_length=1)
    operation: Literal["increase", "decrease", "replace", "multiply"]
    value: float
    source_event_id: str = ""
    source_kind: str = "runtime"


class WorldStateReductionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "rpg_causal_state_reduction_receipt_v1"
    previous_state_hash: str
    next_state_hash: str
    tick: int = Field(ge=0)
    applied_delta_ids: tuple[str, ...]
    changed_target_ids: tuple[str, ...]


def causal_state_hash(value: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop("state_hash", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _specs() -> dict[str, Any]:
    return {spec.dimension_id: spec for spec in mutable_dimension_registry()}


def _apply(before: float, delta: WorldStateDelta) -> float:
    if delta.operation == "increase":
        return before + delta.value
    if delta.operation == "decrease":
        return before - delta.value
    if delta.operation == "replace":
        return delta.value
    if delta.operation == "multiply":
        return before * delta.value
    raise ValueError(f"unsupported_world_state_delta_operation:{delta.operation}")


def reduce_world_state(
    state: Mapping[str, Any],
    deltas: Sequence[WorldStateDelta | Mapping[str, Any]],
) -> tuple[dict[str, Any], WorldStateReductionReceipt]:
    """Apply an ordered delta batch without mutating the input state."""

    issues = validate_mutable_world_state(state)
    if issues:
        raise ValueError("invalid_mutable_world_state:" + ",".join(issues))
    canonical = [
        delta if isinstance(delta, WorldStateDelta) else WorldStateDelta.model_validate(delta)
        for delta in deltas
    ]
    canonical.sort(key=lambda row: (row.tick, row.sequence, row.delta_id))
    ids = [row.delta_id for row in canonical]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_world_state_delta_id")

    next_state = copy.deepcopy(dict(state))
    previous_hash = str(state.get("state_hash") or causal_state_hash(state))
    cells = next_state.get("cells")
    if not isinstance(cells, dict):
        raise ValueError("mutable_state_cells_object_required")
    specs = _specs()
    changed: set[str] = set()
    max_tick = int(next_state.get("tick") or 0)

    for delta in canonical:
        cell = cells.get(delta.target_id)
        if not isinstance(cell, dict):
            raise ValueError(f"world_state_delta_target_unknown:{delta.target_id}")
        values = cell.get("values")
        if not isinstance(values, dict):
            raise ValueError(f"world_state_delta_values_missing:{delta.target_id}")
        spec = specs.get(delta.dimension_id)
        if spec is None:
            raise ValueError(f"world_state_delta_dimension_unknown:{delta.dimension_id}")
        if str(cell.get("target_kind") or "") != spec.target_kind:
            raise ValueError(
                f"world_state_delta_scope_mismatch:{delta.target_id}:{delta.dimension_id}"
            )
        before = float(values.get(delta.dimension_id, spec.default))
        after = _apply(before, delta)
        after = max(spec.minimum, min(spec.maximum, after))
        after = float(round(after)) if spec.integer else round(after, 6)
        values[delta.dimension_id] = after
        cell["revision"] = int(cell.get("revision") or 1) + 1
        changed.add(delta.target_id)
        max_tick = max(max_tick, delta.tick)

    next_state["tick"] = max_tick
    next_state["event_cursor"] = int(next_state.get("event_cursor") or 0) + len(canonical)
    next_state["revision"] = int(next_state.get("revision") or 1) + (1 if canonical else 0)
    next_hash = causal_state_hash(next_state)
    next_state["state_hash"] = next_hash
    post_issues = validate_mutable_world_state(next_state)
    if post_issues:
        raise ValueError("invalid_reduced_mutable_world_state:" + ",".join(post_issues))
    receipt = WorldStateReductionReceipt(
        previous_state_hash=previous_hash,
        next_state_hash=next_hash,
        tick=max_tick,
        applied_delta_ids=tuple(ids),
        changed_target_ids=tuple(sorted(changed)),
    )
    return next_state, receipt


__all__ = [
    "WorldStateDelta",
    "WorldStateReductionReceipt",
    "causal_state_hash",
    "reduce_world_state",
]
