"""Deterministic runtime effects generated from World Forge pressure plans."""
from __future__ import annotations

from typing import Any, Mapping, cast

from pydantic import BaseModel, ConfigDict, Field

from .causal_reducer import (
    WorldStateDelta,
    WorldStateReductionReceipt,
    reduce_world_state,
)

_PRESSURE_STATUSES = frozenset({"active", "escalated", "contained", "resolved"})


class PressureEffectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pressure_id: str = Field(min_length=1)
    tick: int = Field(ge=0)
    trend: str
    severity_before: float
    severity_delta: float
    affected_delta_ids: tuple[str, ...]
    status_before: str = "active"
    status_after: str = "active"
    transitioned: bool = False
    escalated: bool
    resolved: bool


class PressureTickResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "rpg_pressure_tick_result_v1"
    tick: int = Field(ge=0)
    deltas: tuple[WorldStateDelta, ...]
    effects: tuple[PressureEffectRecord, ...]
    reduction: WorldStateReductionReceipt


def _severity_change(trend: str, tick: int) -> float:
    if trend == "escalating":
        return 3.0
    if trend == "contained":
        return -2.0
    if trend == "volatile":
        return 2.0 if tick % 2 else -1.0
    return 0.0


def _region_change(trend: str, base_value: float, tick: int) -> float:
    if trend == "escalating":
        return max(1.0, base_value)
    if trend == "contained":
        return max(0.0, base_value / 2.0)
    if trend == "volatile":
        return max(1.0, base_value + (1.0 if tick % 2 else 0.0))
    return max(0.0, base_value)


def _status_before(
    pressure_id: str,
    pressure_statuses: Mapping[str, str] | None,
) -> str:
    status = str(dict(pressure_statuses or {}).get(pressure_id) or "active")
    return status if status in _PRESSURE_STATUSES else "active"


def _status_after(
    status_before: str,
    *,
    trend: str,
    projected: float,
    escalation_threshold: float,
    resolution_threshold: float,
) -> str:
    if status_before == "resolved":
        return "resolved"
    if projected <= resolution_threshold:
        return "resolved"
    if projected >= escalation_threshold:
        return "escalated"
    if trend == "contained":
        return "contained"
    return "active"


def pressure_deltas_for_tick(
    pressure_plan: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    tick: int,
    pressure_statuses: Mapping[str, str] | None = None,
) -> tuple[tuple[WorldStateDelta, ...], tuple[PressureEffectRecord, ...]]:
    cells = dict(state.get("cells") or {})
    deltas: list[WorldStateDelta] = []
    effects: list[PressureEffectRecord] = []
    pressures = sorted(
        (
            dict(row)
            for row in pressure_plan.get("pressures") or ()
            if isinstance(row, Mapping) and str(row.get("pressure_id") or "")
        ),
        key=lambda row: str(row["pressure_id"]),
    )
    for pressure_index, pressure in enumerate(pressures):
        pressure_id = str(pressure["pressure_id"])
        pressure_cell = dict(cells.get(pressure_id) or {})
        values = dict(pressure_cell.get("values") or {})
        severity_before = float(
            values.get("pressure_severity", pressure.get("severity", 0))
        )
        trend = str(pressure.get("trend") or "contained")
        status_before = _status_before(pressure_id, pressure_statuses)
        if status_before == "resolved":
            effects.append(
                PressureEffectRecord(
                    pressure_id=pressure_id,
                    tick=tick,
                    trend=trend,
                    severity_before=severity_before,
                    severity_delta=0.0,
                    affected_delta_ids=(),
                    status_before="resolved",
                    status_after="resolved",
                    transitioned=False,
                    escalated=False,
                    resolved=False,
                )
            )
            continue

        severity_delta = _severity_change(trend, tick)
        effect_delta_ids: list[str] = []
        severity_delta_id = f"delta:pressure:{tick}:{pressure_id}:severity"
        deltas.append(
            WorldStateDelta(
                delta_id=severity_delta_id,
                tick=tick,
                sequence=pressure_index * 10,
                target_id=pressure_id,
                dimension_id="pressure_severity",
                operation="increase",
                value=severity_delta,
                source_event_id=f"event:pressure:{tick}:{pressure_id}",
                source_kind="pressure_tick",
            )
        )
        effect_delta_ids.append(severity_delta_id)

        planned = pressure.get("next_tick_delta")
        if (
            isinstance(planned, Mapping)
            and planned.get("target_id")
            and planned.get("dimension")
        ):
            region_delta_id = f"delta:pressure:{tick}:{pressure_id}:affected"
            base_value = float(planned.get("value") or 0)
            deltas.append(
                WorldStateDelta(
                    delta_id=region_delta_id,
                    tick=tick,
                    sequence=pressure_index * 10 + 1,
                    target_id=str(planned["target_id"]),
                    dimension_id=str(planned["dimension"]),
                    operation=str(planned.get("operation") or "decrease"),  # type: ignore[arg-type]
                    value=_region_change(trend, base_value, tick),
                    source_event_id=f"event:pressure:{tick}:{pressure_id}",
                    source_kind="pressure_tick",
                )
            )
            effect_delta_ids.append(region_delta_id)

        projected = max(0.0, min(100.0, severity_before + severity_delta))
        status_after = _status_after(
            status_before,
            trend=trend,
            projected=projected,
            escalation_threshold=float(
                cast(Any, pressure.get("escalation_threshold"))
                if pressure.get("escalation_threshold") is not None
                else 101
            ),
            resolution_threshold=float(
                cast(Any, pressure.get("resolution_threshold"))
                if pressure.get("resolution_threshold") is not None
                else -1
            ),
        )
        transitioned = status_after != status_before
        effects.append(
            PressureEffectRecord(
                pressure_id=pressure_id,
                tick=tick,
                trend=trend,
                severity_before=severity_before,
                severity_delta=severity_delta,
                affected_delta_ids=tuple(effect_delta_ids),
                status_before=status_before,
                status_after=status_after,
                transitioned=transitioned,
                escalated=transitioned and status_after == "escalated",
                resolved=transitioned and status_after == "resolved",
            )
        )
    return tuple(deltas), tuple(effects)


def apply_pressure_tick(
    pressure_plan: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    tick: int,
    pressure_statuses: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], PressureTickResult]:
    deltas, effects = pressure_deltas_for_tick(
        pressure_plan,
        state,
        tick=tick,
        pressure_statuses=pressure_statuses,
    )
    next_state, receipt = reduce_world_state(state, deltas)
    return next_state, PressureTickResult(
        tick=tick,
        deltas=deltas,
        effects=effects,
        reduction=receipt,
    )


__all__ = [
    "PressureEffectRecord",
    "PressureTickResult",
    "apply_pressure_tick",
    "pressure_deltas_for_tick",
]
