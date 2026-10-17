"""Typed mutable world state derived from World Forge planning artefacts."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


class MutableDimensionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension_id: str = Field(min_length=1)
    target_kind: str = Field(min_length=1)
    minimum: float = 0.0
    maximum: float = 100.0
    default: float = 50.0
    integer: bool = True


class MutableStateCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    target_kind: str = Field(min_length=1)
    values: dict[str, float]
    revision: int = Field(default=1, ge=1)


_DIMENSIONS = (
    MutableDimensionSpec(dimension_id="political_stability", target_kind="region", default=60),
    MutableDimensionSpec(dimension_id="trade_access", target_kind="region", default=50),
    MutableDimensionSpec(dimension_id="resource_access", target_kind="region", default=50),
    MutableDimensionSpec(dimension_id="population_index", target_kind="region", default=50),
    MutableDimensionSpec(dimension_id="control_index", target_kind="claim", default=50),
    MutableDimensionSpec(dimension_id="settlement_viability", target_kind="settlement", default=50),
    MutableDimensionSpec(dimension_id="cultural_cohesion", target_kind="culture", default=50),
    MutableDimensionSpec(dimension_id="pressure_severity", target_kind="pressure", default=25),
)


def mutable_dimension_registry() -> tuple[MutableDimensionSpec, ...]:
    return _DIMENSIONS


def _specs() -> dict[str, MutableDimensionSpec]:
    return {spec.dimension_id: spec for spec in _DIMENSIONS}


def _bounded(spec: MutableDimensionSpec, value: Any) -> float:
    number = float(value)
    number = max(spec.minimum, min(spec.maximum, number))
    return float(round(number)) if spec.integer else round(number, 6)


def _hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _cell(target_id: str, target_kind: str, values: Mapping[str, Any]) -> dict[str, Any]:
    specs = _specs()
    bounded = {
        dimension_id: _bounded(specs[dimension_id], value)
        for dimension_id, value in values.items()
        if dimension_id in specs
    }
    return MutableStateCell(
        target_id=target_id,
        target_kind=target_kind,
        values=bounded,
    ).model_dump(mode="python")


def build_mutable_world_state(planning_topics: Mapping[str, Any]) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    present = dict(planning_topics.get("present_day_state") or {})
    for region_id, values in dict(present.get("state") or {}).items():
        if isinstance(values, Mapping):
            cells[str(region_id)] = _cell(str(region_id), "region", values)

    political = dict(planning_topics.get("political_claim_graph") or {})
    for claim in political.get("claims") or ():
        if not isinstance(claim, Mapping) or not claim.get("claim_id"):
            continue
        claim_id = str(claim["claim_id"])
        cells[claim_id] = _cell(
            claim_id,
            "claim",
            {"control_index": claim.get("control_index", 50)},
        )

    settlements = dict(planning_topics.get("settlement_origin_plan") or {})
    for settlement in settlements.get("settlements") or ():
        if not isinstance(settlement, Mapping) or not settlement.get("place_id"):
            continue
        target_id = str(settlement["place_id"])
        viability = (
            float(settlement.get("route_dependency") or 0)
            + float(settlement.get("strategic_value") or 0)
        ) / 2.0
        cells[target_id] = _cell(
            target_id,
            "settlement",
            {"settlement_viability": viability},
        )

    cultures = dict(planning_topics.get("culture_lineage_plan") or {})
    for lineage in cultures.get("lineages") or ():
        if not isinstance(lineage, Mapping) or not lineage.get("culture_id"):
            continue
        target_id = str(lineage["culture_id"])
        cells[target_id] = _cell(
            target_id,
            "culture",
            {"cultural_cohesion": lineage.get("cohesion_index", 50)},
        )

    pressures = dict(planning_topics.get("pressure_plan") or {})
    for pressure in pressures.get("pressures") or ():
        if not isinstance(pressure, Mapping) or not pressure.get("pressure_id"):
            continue
        target_id = str(pressure["pressure_id"])
        cells[target_id] = _cell(
            target_id,
            "pressure",
            {"pressure_severity": pressure.get("severity", 25)},
        )

    payload: dict[str, Any] = {
        "schema_version": "rpg_causal_world_state_v1",
        "revision": 1,
        "tick": 0,
        "event_cursor": 0,
        "dimension_registry": [spec.model_dump(mode="python") for spec in _DIMENSIONS],
        "cells": {key: cells[key] for key in sorted(cells)},
    }
    issues = validate_mutable_world_state(payload)
    if issues:
        raise ValueError("invalid_mutable_world_state:" + ",".join(issues))
    payload["state_hash"] = _hash(payload)
    return payload


def validate_mutable_world_state(value: Mapping[str, Any]) -> tuple[str, ...]:
    registry = _specs()
    issues: list[str] = []
    cells = value.get("cells")
    if not isinstance(cells, Mapping):
        return ("mutable_state_cells_object_required",)
    for target_id, raw_cell in cells.items():
        if not isinstance(raw_cell, Mapping):
            issues.append(f"mutable_state_cell_object_required:{target_id}")
            continue
        try:
            cell = MutableStateCell.model_validate(raw_cell)
        except Exception:
            issues.append(f"mutable_state_cell_invalid:{target_id}")
            continue
        if cell.target_id != str(target_id):
            issues.append(f"mutable_state_cell_id_mismatch:{target_id}")
        for dimension_id, number in cell.values.items():
            spec = registry.get(dimension_id)
            if spec is None:
                issues.append(f"mutable_state_dimension_unknown:{dimension_id}")
                continue
            if spec.target_kind != cell.target_kind:
                issues.append(
                    f"mutable_state_dimension_scope_mismatch:{target_id}:{dimension_id}"
                )
            if number < spec.minimum or number > spec.maximum:
                issues.append(f"mutable_state_dimension_out_of_bounds:{target_id}:{dimension_id}")
    return tuple(dict.fromkeys(issues))


__all__ = [
    "MutableDimensionSpec",
    "MutableStateCell",
    "build_mutable_world_state",
    "mutable_dimension_registry",
    "validate_mutable_world_state",
]
