"""Durable causal-world runtime, event propagation, and deterministic replay."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .causal_reducer import WorldStateDelta, causal_state_hash, reduce_world_state
from .causal_state import build_mutable_world_state, validate_mutable_world_state
from .pressure_effects import apply_pressure_tick
from .world_event_log import add_world_event


class CausalWorldEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    tick: int = Field(ge=0)
    sequence: int = Field(ge=0)
    event_type: str = Field(min_length=1)
    parent_event_id: str = ""
    pressure_id: str = ""
    deltas: tuple[WorldStateDelta, ...] = ()
    before_state_hash: str = ""
    after_state_hash: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


def _hash(value: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop("runtime_hash", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _initial_pressure_statuses(planning_topics: Mapping[str, Any]) -> dict[str, str]:
    pressure_plan = planning_topics.get("pressure_plan")
    if not isinstance(pressure_plan, Mapping):
        return {}
    return {
        str(row.get("pressure_id") or ""): "active"
        for row in pressure_plan.get("pressures") or ()
        if isinstance(row, Mapping) and str(row.get("pressure_id") or "")
    }


def _projection_plan(planning_topics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(dict(value))
        for key in (
            "political_claim_graph",
            "settlement_origin_plan",
            "culture_lineage_plan",
            "opening_scope_plan",
        )
        if isinstance((value := planning_topics.get(key)), Mapping)
    }


def bootstrap_causal_runtime(planning_topics: Mapping[str, Any]) -> dict[str, Any]:
    initial_state = build_mutable_world_state(planning_topics)
    runtime: dict[str, Any] = {
        # Additive lifecycle and projection fields remain compatible with v1 readers.
        "schema_version": "rpg_causal_world_runtime_v1",
        "revision": 1,
        "initial_state": copy.deepcopy(initial_state),
        "state": copy.deepcopy(initial_state),
        "pressure_plan": copy.deepcopy(
            dict(planning_topics.get("pressure_plan") or {})
        ),
        "projection_plan": _projection_plan(planning_topics),
        "pressure_statuses": _initial_pressure_statuses(planning_topics),
        "events": [],
        "last_tick": 0,
    }
    runtime["runtime_hash"] = _hash(runtime)
    return runtime


def _event_dict(event: CausalWorldEvent) -> dict[str, Any]:
    return event.model_dump(mode="python")


def _events(runtime: Mapping[str, Any]) -> tuple[CausalWorldEvent, ...]:
    return tuple(
        CausalWorldEvent.model_validate(row)
        for row in runtime.get("events") or ()
        if isinstance(row, Mapping)
    )


def advance_causal_runtime(
    runtime: Mapping[str, Any],
    *,
    tick: int,
) -> tuple[dict[str, Any], tuple[CausalWorldEvent, ...]]:
    """Advance one monotonic tick and emit a replayable aggregate event chain."""

    current = copy.deepcopy(dict(runtime))
    existing = _events(current)
    aggregate_id = f"world:event:causal:{tick}:pressure_tick"
    existing_tick = tuple(event for event in existing if event.event_id == aggregate_id)
    if existing_tick:
        related = tuple(
            event
            for event in existing
            if event.event_id == aggregate_id or event.parent_event_id == aggregate_id
        )
        return current, related
    last_tick = int(current.get("last_tick") or 0)
    if tick <= last_tick:
        raise ValueError(f"causal_runtime_tick_not_monotonic:{tick}:{last_tick}")
    state = dict(current.get("state") or {})
    issues = validate_mutable_world_state(state)
    if issues:
        raise ValueError("invalid_causal_runtime_state:" + ",".join(issues))
    before_hash = str(state.get("state_hash") or causal_state_hash(state))
    statuses = {
        str(key): str(value)
        for key, value in dict(current.get("pressure_statuses") or {}).items()
    }
    next_state, pressure_result = apply_pressure_tick(
        dict(current.get("pressure_plan") or {}),
        state,
        tick=tick,
        pressure_statuses=statuses,
    )
    next_statuses = dict(statuses)
    for effect in pressure_result.effects:
        next_statuses[effect.pressure_id] = effect.status_after
    aggregate = CausalWorldEvent(
        event_id=aggregate_id,
        tick=tick,
        sequence=0,
        event_type="pressure_tick",
        deltas=pressure_result.deltas,
        before_state_hash=before_hash,
        after_state_hash=next_state["state_hash"],
        payload={
            "effects": [
                effect.model_dump(mode="python") for effect in pressure_result.effects
            ],
            "reduction": pressure_result.reduction.model_dump(mode="python"),
            "pressure_statuses_before": statuses,
            "pressure_statuses_after": next_statuses,
        },
    )
    emitted: list[CausalWorldEvent] = [aggregate]
    sequence = 1
    for effect in pressure_result.effects:
        if not effect.transitioned:
            continue
        event_type = ""
        if effect.status_after == "escalated":
            event_type = "pressure_escalated"
        elif effect.status_after == "resolved":
            event_type = "pressure_resolved"
        elif effect.status_after == "contained":
            event_type = "pressure_contained"
        elif effect.status_after == "active":
            event_type = "pressure_reactivated"
        if not event_type:
            continue
        emitted.append(
            CausalWorldEvent(
                event_id=(
                    f"world:event:causal:{tick}:{effect.pressure_id}:{event_type}"
                ),
                tick=tick,
                sequence=sequence,
                event_type=event_type,
                parent_event_id=aggregate_id,
                pressure_id=effect.pressure_id,
                before_state_hash=next_state["state_hash"],
                after_state_hash=next_state["state_hash"],
                payload={
                    "severity_before": effect.severity_before,
                    "severity_delta": effect.severity_delta,
                    "affected_delta_ids": list(effect.affected_delta_ids),
                    "status_before": effect.status_before,
                    "status_after": effect.status_after,
                },
            )
        )
        sequence += 1
    all_events = sorted(
        (*existing, *emitted),
        key=lambda event: (event.tick, event.sequence, event.event_id),
    )
    current["state"] = next_state
    current["pressure_statuses"] = next_statuses
    current["events"] = [_event_dict(event) for event in all_events]
    current["last_tick"] = tick
    current["revision"] = int(current.get("revision") or 1) + 1
    current["runtime_hash"] = _hash(current)
    return current, tuple(emitted)


def replay_causal_events(
    initial_state: Mapping[str, Any],
    events: Sequence[CausalWorldEvent | Mapping[str, Any]],
) -> dict[str, Any]:
    """Rebuild state solely from aggregate event deltas and verify hash continuity."""

    state = copy.deepcopy(dict(initial_state))
    ordered = sorted(
        (
            event
            if isinstance(event, CausalWorldEvent)
            else CausalWorldEvent.model_validate(event)
            for event in events
        ),
        key=lambda event: (event.tick, event.sequence, event.event_id),
    )
    for event in ordered:
        if not event.deltas:
            continue
        current_hash = str(state.get("state_hash") or causal_state_hash(state))
        if event.before_state_hash and event.before_state_hash != current_hash:
            raise ValueError(f"causal_replay_before_hash_mismatch:{event.event_id}")
        state, _ = reduce_world_state(state, event.deltas)
        if event.after_state_hash and event.after_state_hash != state["state_hash"]:
            raise ValueError(f"causal_replay_after_hash_mismatch:{event.event_id}")
    return state


def initialize_causal_runtime(
    simulation_state: dict[str, Any],
    runtime_bootstrap: Mapping[str, Any],
) -> dict[str, Any]:
    installed = copy.deepcopy(dict(runtime_bootstrap))
    simulation_state["causal_world_runtime"] = installed
    return installed


def ensure_causal_runtime_installed(
    simulation_state: dict[str, Any],
) -> dict[str, Any]:
    runtime = simulation_state.get("causal_world_runtime")
    if isinstance(runtime, Mapping):
        return dict(runtime)
    campaign_bible = simulation_state.get("campaign_bible")
    if isinstance(campaign_bible, Mapping):
        manifest = campaign_bible.get("manifest")
        if isinstance(manifest, Mapping):
            bootstrap = manifest.get("causal_runtime_bootstrap")
            if isinstance(bootstrap, Mapping):
                return initialize_causal_runtime(simulation_state, bootstrap)
    raise ValueError("causal_world_runtime_not_installed")


def advance_installed_causal_runtime(
    simulation_state: dict[str, Any],
    *,
    tick: int,
) -> tuple[dict[str, Any], tuple[CausalWorldEvent, ...]]:
    runtime = ensure_causal_runtime_installed(simulation_state)
    next_runtime, emitted = advance_causal_runtime(runtime, tick=tick)
    simulation_state["causal_world_runtime"] = next_runtime
    for event in emitted:
        add_world_event(
            simulation_state,
            {
                "event_id": event.event_id,
                "kind": event.event_type,
                "title": event.event_type.replace("_", " ").title(),
                "summary": (
                    f"Causal world state advanced at tick {event.tick}."
                    if event.event_type == "pressure_tick"
                    else f"{event.pressure_id} changed status at tick {event.tick}."
                ),
                "tick": event.tick,
                "source": "deterministic_causal_runtime",
                "parent_event_id": event.parent_event_id,
                "pressure_id": event.pressure_id,
                "before_state_hash": event.before_state_hash,
                "after_state_hash": event.after_state_hash,
            },
        )
    return next_runtime, emitted


__all__ = [
    "CausalWorldEvent",
    "advance_causal_runtime",
    "advance_installed_causal_runtime",
    "bootstrap_causal_runtime",
    "ensure_causal_runtime_installed",
    "initialize_causal_runtime",
    "replay_causal_events",
]
