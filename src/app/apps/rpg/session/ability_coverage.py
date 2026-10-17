"""Ability coverage telemetry for RPG autoplay and endurance reports.

N128 layer: summarize which deterministic gameplay dimensions have actually
changed through active abilities, passive hooks, narrative traits, world-scale
abilities, and active effects. This module does not execute mechanics; it only
reads traceable state written by the deterministic ability systems.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any, Sequence

from pydantic import BaseModel, Field

from app.apps.rpg.session.ability_system import ALLOWED_DIMENSIONS
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

REQUIRED_ABILITY_COVERAGE_DIMENSIONS = (
    "resources",
    "information",
    "relationships",
    "access",
    "environment",
    "position",
    "narrative",
    "economy",
    "world",
)


class RpgAbilityCoverageReport(BaseModel):
    ok: bool
    total_observations: int = 0
    coverage_score: float = 0.0
    required_dimensions: list[str] = Field(default_factory=list)
    covered_dimensions: list[str] = Field(default_factory=list)
    missing_dimensions: list[str] = Field(default_factory=list)
    dimension_counts: dict[str, int] = Field(default_factory=dict)
    kind_counts: dict[str, int] = Field(default_factory=dict)
    purpose_counts: dict[str, int] = Field(default_factory=dict)
    capability_counts: dict[str, int] = Field(default_factory=dict)
    ability_counts: dict[str, int] = Field(default_factory=dict)
    source_counts: dict[str, int] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _append(target: dict[str, Any], key: str, value: dict[str, Any], *, limit: int = 20) -> None:
    values = _safe_list(target.get(key))
    values.insert(0, value)
    target[key] = values[:limit]


def _ability_index(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    abilities = _safe_list(_safe_dict(state.get("ability_tree")).get("abilities"))
    return {str(_safe_dict(ability).get("ability_id") or ""): _safe_dict(ability) for ability in abilities if _safe_dict(ability).get("ability_id")}


def _unlocked_ids(state: dict[str, Any]) -> set[str]:
    return {str(value) for value in _safe_list(_safe_dict(state.get("ability_state")).get("unlocked"))}


def _clean_dimensions(raw_dimensions: Any, warnings: list[str], *, source: str) -> list[str]:
    if isinstance(raw_dimensions, str):
        candidates = [raw_dimensions]
    else:
        candidates = [str(value) for value in _safe_list(raw_dimensions)]
    dimensions: list[str] = []
    for dimension in candidates:
        if not dimension:
            continue
        if dimension not in ALLOWED_DIMENSIONS:
            warnings.append(f"{source}: ignored unsupported dimension {dimension}")
            continue
        if dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions


def _increment(counter: dict[str, int], key: str | None) -> None:
    if not key:
        return
    counter[key] = int(counter.get(key, 0)) + 1


def _record_observation(
    observations: list[dict[str, Any]],
    counters: dict[str, dict[str, int]],
    warnings: list[str],
    *,
    source: str,
    dimensions: Any,
    ability_id: str | None = None,
    ability_name: str | None = None,
    kind: str | None = None,
    purpose: str | None = None,
    capability: str | None = None,
    op: str | None = None,
    applied: bool = True,
    extra: dict[str, Any] | None = None,
) -> None:
    if not applied:
        return
    clean_dimensions = _clean_dimensions(dimensions, warnings, source=source)
    if not clean_dimensions:
        return
    observation = {
        "source": source,
        "dimensions": clean_dimensions,
    }
    if ability_id:
        observation["ability_id"] = ability_id
    if ability_name:
        observation["ability_name"] = ability_name
    if kind:
        observation["kind"] = kind
    if purpose:
        observation["purpose"] = purpose
    if capability:
        observation["capability"] = capability
    if op:
        observation["op"] = op
    if extra:
        observation.update(extra)
    observations.append(observation)

    _increment(counters["sources"], source)
    _increment(counters["abilities"], ability_id or ability_name)
    _increment(counters["kinds"], kind)
    _increment(counters["purposes"], purpose)
    _increment(counters["capabilities"], capability)
    for dimension in clean_dimensions:
        _increment(counters["dimensions"], dimension)


def _enrich_from_ability(index: dict[str, dict[str, Any]], ability_id: str | None) -> dict[str, Any]:
    if not ability_id:
        return {}
    return deepcopy(index.get(ability_id) or {})


def _scan_trace_list(
    observations: list[dict[str, Any]],
    counters: dict[str, dict[str, int]],
    warnings: list[str],
    *,
    source: str,
    records: list[Any],
    ability_index: dict[str, dict[str, Any]],
) -> None:
    for raw_record in records:
        record = _safe_dict(raw_record)
        ability_id = _text(record.get("ability_id"))
        ability = _enrich_from_ability(ability_index, ability_id)
        _record_observation(
            observations,
            counters,
            warnings,
            source=source,
            dimensions=record.get("dimensions") or record.get("dimension") or ability.get("dimensions"),
            ability_id=ability_id or _text(ability.get("ability_id")) or None,
            ability_name=_text(record.get("ability_name") or record.get("name") or ability.get("name")) or None,
            kind=_text(record.get("kind") or ability.get("kind")) or None,
            purpose=_text(record.get("purpose") or ability.get("purpose")) or None,
            capability=_text(record.get("capability") or ability.get("capability")) or None,
            op=_text(record.get("op")) or None,
            applied=record.get("applied") is not False,
        )


def _scan_timeline_events(
    observations: list[dict[str, Any]],
    counters: dict[str, dict[str, int]],
    warnings: list[str],
    *,
    state: dict[str, Any],
    ability_index: dict[str, dict[str, Any]],
) -> None:
    for raw_event in _safe_list(state.get("timeline")):
        event = _safe_dict(raw_event)
        kind = _text(event.get("kind"))
        if kind not in {"ability", "ability_effect", "world_ability", "passive", "trait"}:
            continue
        for raw_effect in _safe_list(event.get("effects")):
            effect = _safe_dict(raw_effect)
            ability_id = _text(effect.get("ability_id") or event.get("ability_id"))
            ability = _enrich_from_ability(ability_index, ability_id)
            _record_observation(
                observations,
                counters,
                warnings,
                source=f"timeline:{kind}",
                dimensions=effect.get("dimensions") or effect.get("dimension") or ability.get("dimensions"),
                ability_id=ability_id or _text(ability.get("ability_id")) or None,
                ability_name=_text(effect.get("ability_name") or event.get("title") or ability.get("name")) or None,
                kind=_text(ability.get("kind")) or kind,
                purpose=_text(ability.get("purpose")) or None,
                capability=_text(ability.get("capability")) or None,
                op=_text(effect.get("op")) or None,
                applied=effect.get("applied") is not False,
                extra={"event_kind": kind},
            )


def summarize_ability_coverage(state: dict[str, Any], *, required_dimensions: Sequence[str] | None = None) -> RpgAbilityCoverageReport:
    """Summarize observed ability coverage for reporting/autoplay checks.

    The report is dimension-first: it shows which gameplay dimensions were
    actually changed by deterministic traces/events rather than which buttons
    merely existed in the tree.
    """
    required = list(required_dimensions or REQUIRED_ABILITY_COVERAGE_DIMENSIONS)
    warnings: list[str] = []
    required = _clean_dimensions(required, warnings, source="required_dimensions")
    ability_index = _ability_index(state)
    observations: list[dict[str, Any]] = []
    counters: dict[str, dict[str, int]] = {
        "dimensions": {},
        "kinds": {},
        "purposes": {},
        "capabilities": {},
        "abilities": {},
        "sources": {},
    }

    mechanics = _safe_dict(state.get("mechanics"))
    _scan_trace_list(observations, counters, warnings, source="ability_effect_trace", records=_safe_list(mechanics.get("ability_effect_trace")), ability_index=ability_index)
    _scan_trace_list(observations, counters, warnings, source="world_effect_trace", records=_safe_list(mechanics.get("world_effect_trace")), ability_index=ability_index)
    _scan_trace_list(observations, counters, warnings, source="passive_hook_trace", records=_safe_list(mechanics.get("passive_hook_trace")), ability_index=ability_index)

    runtime = _safe_dict(state.get("runtime"))
    _scan_trace_list(observations, counters, warnings, source="runtime_effect", records=_safe_list(runtime.get("effects")), ability_index=ability_index)
    _scan_trace_list(observations, counters, warnings, source="passive_modifier", records=_safe_list(runtime.get("passive_modifiers")), ability_index=ability_index)

    ability_state = _safe_dict(state.get("ability_state"))
    _scan_trace_list(observations, counters, warnings, source="active_effect", records=_safe_list(ability_state.get("active_effects")), ability_index=ability_index)

    unlocked = _unlocked_ids(state)
    for ability_id, ability in ability_index.items():
        if ability_id not in unlocked or ability.get("kind") != "narrative_trait":
            continue
        _record_observation(
            observations,
            counters,
            warnings,
            source="unlocked_narrative_trait",
            dimensions=ability.get("dimensions"),
            ability_id=ability_id,
            ability_name=_text(ability.get("name")) or None,
            kind="narrative_trait",
            purpose=_text(ability.get("purpose")) or None,
            capability=_text(ability.get("capability")) or None,
        )

    _scan_timeline_events(observations, counters, warnings, state=state, ability_index=ability_index)

    covered_required = [dimension for dimension in required if counters["dimensions"].get(dimension, 0) > 0]
    missing = [dimension for dimension in required if dimension not in covered_required]
    coverage_score = round(len(covered_required) / len(required), 4) if required else 1.0
    return RpgAbilityCoverageReport(
        ok=not missing,
        total_observations=len(observations),
        coverage_score=coverage_score,
        required_dimensions=required,
        covered_dimensions=covered_required,
        missing_dimensions=missing,
        dimension_counts=dict(sorted(counters["dimensions"].items())),
        kind_counts=dict(sorted(counters["kinds"].items())),
        purpose_counts=dict(sorted(counters["purposes"].items())),
        capability_counts=dict(sorted(counters["capabilities"].items())),
        ability_counts=dict(sorted(counters["abilities"].items())),
        source_counts=dict(sorted(counters["sources"].items())),
        observations=observations,
        warnings=warnings,
    )


def write_ability_coverage_snapshot(state: dict[str, Any], *, required_dimensions: Sequence[str] | None = None) -> RpgAbilityCoverageReport:
    """Append a compact coverage snapshot to mechanics for reports."""
    report = summarize_ability_coverage(state, required_dimensions=required_dimensions)
    mechanics = _safe_dict(state.get("mechanics"))
    snapshot = report.model_dump(exclude={"observations"})
    snapshot["created_at"] = _utc_now()
    _append(mechanics, "ability_coverage_snapshots", snapshot, limit=20)
    state["mechanics"] = mechanics
    return report
