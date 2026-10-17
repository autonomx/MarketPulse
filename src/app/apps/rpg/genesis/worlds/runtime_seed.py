"""Compile immutable runtime seeds and deterministic player-absent validation."""
from __future__ import annotations

import json
from typing import Any, Final, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_content_hash

_RUNTIME_SEED_VERSION: Final = "rpg_world_runtime_seed_v1"
_MATERIALIZATION_VERSION: Final = "rpg_vertical_slice_materialization_v1"
_PLAYTEST_VERSION: Final = "rpg_player_absent_playtest_v1"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeAgentPlan(_FrozenModel):
    agent_id: str = Field(min_length=1)
    agent_kind: str = Field(min_length=1)
    location_id: str = ""
    objective: str = ""
    next_action: str = Field(min_length=1)
    dependency: Any = None
    reaction_conditions: Any = None
    schedule: dict[str, Any] = Field(
        default_factory=lambda: {"cadence_days": 1, "phase": "daily"}
    )


class RuntimeClockSeed(_FrozenModel):
    clock_id: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)
    current_state: Any = None
    next_tick_change: str = Field(min_length=1)
    escalation_condition: str = Field(min_length=1)
    threshold: int = Field(default=3, ge=1)
    actor_ids: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    place_ids: tuple[str, ...] = ()


class RuntimeResourceSeed(_FrozenModel):
    resource_id: str = Field(min_length=1)
    source_entity_id: str = Field(min_length=1)
    state: Any = None
    quantity: float | None = None
    daily_delta: float | None = None
    producer_ids: tuple[str, ...] = ()
    consumer_ids: tuple[str, ...] = ()


class RuntimeSeedDocument(_FrozenModel):
    schema_version: Literal["rpg_world_runtime_seed_v1"] = _RUNTIME_SEED_VERSION
    world_id: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    source_canon_hash: str = Field(pattern=r"^sha256:")
    resolved_profile_hash: str = ""
    seed: int = 0
    capabilities: dict[str, bool] = Field(default_factory=dict)
    agents: tuple[RuntimeAgentPlan, ...] = ()
    clocks: tuple[RuntimeClockSeed, ...] = ()
    resources: tuple[RuntimeResourceSeed, ...] = ()
    checks: dict[str, bool] = Field(default_factory=dict)
    passed: bool = False
    content_hash: str = ""

    @model_validator(mode="after")
    def validate_hash(self) -> "RuntimeSeedDocument":
        if self.content_hash and not self.content_hash.startswith("sha256:"):
            raise ValueError("runtime_seed_hash_invalid")
        return self


class VerticalSliceMaterializationDocument(_FrozenModel):
    schema_version: Literal[
        "rpg_vertical_slice_materialization_v1"
    ] = _MATERIALIZATION_VERSION
    world_id: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    runtime_seed_hash: str = Field(pattern=r"^sha256:")
    hub_location_id: str = ""
    sublocation_ids: tuple[str, ...] = ()
    nearby_location_ids: tuple[str, ...] = ()
    actor_ids: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    clock_ids: tuple[str, ...] = ()
    resource_ids: tuple[str, ...] = ()
    opening_thread_ids: tuple[str, ...] = ()
    checks: dict[str, bool] = Field(default_factory=dict)
    passed: bool = False
    content_hash: str = ""


class PlayerAbsentPlaytestReport(_FrozenModel):
    schema_version: Literal["rpg_player_absent_playtest_v1"] = _PLAYTEST_VERSION
    runtime_seed_hash: str = Field(pattern=r"^sha256:")
    days_simulated: int = Field(ge=1)
    daily_events: tuple[dict[str, Any], ...] = ()
    direct_final_state_hash: str = Field(pattern=r"^sha256:")
    reloaded_final_state_hash: str = Field(pattern=r"^sha256:")
    checks: dict[str, bool] = Field(default_factory=dict)
    passed: bool = False
    content_hash: str = ""


def _hash_model(model_type: type[_FrozenModel], payload: Mapping[str, Any]) -> Any:
    value = dict(payload)
    value["content_hash"] = ""
    value["content_hash"] = canonical_content_hash(value)
    return model_type.model_validate(value)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    return tuple(str(item).strip() for item in _sequence(value) if str(item).strip())


def _entities(canon: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    value = canon.get("entities")
    if not isinstance(value, Mapping):
        return {}
    return {
        str(entity_id): dict(entity)
        for entity_id, entity in value.items()
        if isinstance(entity, Mapping) and str(entity_id)
    }


def _profile_domains(canon: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    graph = _mapping(canon.get("topic_graph"))
    metadata = _mapping(graph.get("metadata"))
    profile = _mapping(metadata.get("resolved_profile"))
    return tuple(
        dict(domain)
        for domain in _sequence(profile.get("domains"))
        if isinstance(domain, Mapping)
    )


def _domain_kinds_by_role(canon: Mapping[str, Any], role: str) -> set[str]:
    return {
        str(domain.get("entity_kind") or "")
        for domain in _profile_domains(canon)
        if role in {str(item) for item in _sequence(domain.get("semantic_roles"))}
        and str(domain.get("entity_kind") or "")
    }


def _domain_kinds(canon: Mapping[str, Any], *domain_ids: str) -> set[str]:
    wanted = set(domain_ids)
    return {
        str(domain.get("entity_kind") or "")
        for domain in _profile_domains(canon)
        if str(domain.get("domain_id") or "") in wanted
        and str(domain.get("entity_kind") or "")
    }


def _first_text(entity: Mapping[str, Any], *field_ids: str) -> str:
    return next(
        (
            " ".join(str(entity.get(field_id) or "").split())
            for field_id in field_ids
            if str(entity.get(field_id) or "").strip()
        ),
        "",
    )


def _agent_plans(
    canon: Mapping[str, Any],
    entities: Mapping[str, Mapping[str, Any]],
) -> tuple[RuntimeAgentPlan, ...]:
    actor_kinds = _domain_kinds_by_role(canon, "initial_actors") or {"actor", "npc"}
    group_kinds = _domain_kinds(canon, "groups") or {"group", "faction"}
    plans: list[RuntimeAgentPlan] = []
    for entity_id, entity in sorted(entities.items()):
        kind = str(entity.get("kind") or "")
        if kind not in actor_kinds | group_kinds:
            continue
        next_action = _first_text(entity, "next_action", "next_tick_change")
        if not next_action:
            continue
        plans.append(
            RuntimeAgentPlan(
                agent_id=entity_id,
                agent_kind=kind,
                location_id=_first_text(
                    entity,
                    "location_id",
                    "place_id",
                    "current_place_id",
                ),
                objective=_first_text(
                    entity,
                    "current_objective",
                    "goal",
                    "current_pressure",
                ),
                next_action=next_action,
                dependency=entity.get("dependency") or entity.get("dependencies"),
                reaction_conditions=(
                    entity.get("reaction_conditions")
                    or entity.get("failure_response")
                ),
            )
        )
    return tuple(plans)


def _clock_seeds(
    canon: Mapping[str, Any],
    entities: Mapping[str, Mapping[str, Any]],
) -> tuple[RuntimeClockSeed, ...]:
    pressure_kinds = _domain_kinds(canon, "pressures") or {"pressure", "conflict"}
    clocks: list[RuntimeClockSeed] = []
    for entity_id, entity in sorted(entities.items()):
        if str(entity.get("kind") or "") not in pressure_kinds:
            continue
        next_tick = _first_text(entity, "next_tick_change", "next_action")
        escalation = _first_text(
            entity,
            "escalation_condition",
            "failure_response",
        )
        if not next_tick or not escalation:
            continue
        clocks.append(
            RuntimeClockSeed(
                clock_id=f"clock:{entity_id.replace(':', '_')}",
                source_entity_id=entity_id,
                current_state=entity.get("current_state")
                or entity.get("current_pressure"),
                next_tick_change=next_tick,
                escalation_condition=escalation,
                actor_ids=_strings(entity.get("actor_ids")),
                group_ids=_strings(entity.get("group_ids")),
                place_ids=_strings(entity.get("place_ids")),
            )
        )
    return tuple(clocks)


def _resource_seeds(
    canon: Mapping[str, Any],
    entities: Mapping[str, Mapping[str, Any]],
) -> tuple[RuntimeResourceSeed, ...]:
    resource_kinds = _domain_kinds(
        canon,
        "resources",
        "survival_resources",
    ) | {"resource", "resource_system"}
    resources: list[RuntimeResourceSeed] = []
    for entity_id, entity in sorted(entities.items()):
        if str(entity.get("kind") or "") not in resource_kinds:
            continue
        quantity_value = entity.get("quantity") or entity.get("current_amount")
        quantity = (
            float(quantity_value)
            if isinstance(quantity_value, (int, float))
            and not isinstance(quantity_value, bool)
            else None
        )
        delta_value = entity.get("daily_delta") or entity.get("consumption_per_day")
        daily_delta = (
            float(delta_value)
            if isinstance(delta_value, (int, float))
            and not isinstance(delta_value, bool)
            else None
        )
        if entity.get("consumption_per_day") is not None and daily_delta is not None:
            daily_delta = -abs(daily_delta)
        resources.append(
            RuntimeResourceSeed(
                resource_id=f"runtime_resource:{entity_id.replace(':', '_')}",
                source_entity_id=entity_id,
                state=entity.get("state")
                or entity.get("resources")
                or entity.get("scarcity"),
                quantity=quantity,
                daily_delta=daily_delta,
                producer_ids=_strings(
                    entity.get("producer_ids")
                    or entity.get("controller_group_ids")
                ),
                consumer_ids=_strings(entity.get("consumer_ids")),
            )
        )
    return tuple(resources)


def compile_runtime_seed(
    *,
    world_id: str,
    world_revision: int,
    source_canon_hash: str,
    canon: Mapping[str, Any],
    seed: int = 0,
) -> RuntimeSeedDocument:
    entities = _entities(canon)
    graph = _mapping(canon.get("topic_graph"))
    metadata = _mapping(graph.get("metadata"))
    agents = _agent_plans(canon, entities)
    clocks = _clock_seeds(canon, entities)
    resources = _resource_seeds(canon, entities)
    capabilities = {
        str(key): bool(value)
        for key, value in _mapping(metadata.get("runtime_capabilities")).items()
    }
    checks = {
        "source_canon_pinned": source_canon_hash.startswith("sha256:"),
        "agents_have_plans": bool(agents)
        and all(plan.next_action for plan in agents),
        "active_clocks_defined": bool(clocks)
        and all(clock.next_tick_change for clock in clocks),
        "resource_states_structured": all(
            resource.state is not None or resource.quantity is not None
            for resource in resources
        ),
    }
    if not resources:
        checks["resource_states_structured"] = True
    payload = {
        "world_id": world_id,
        "world_revision": world_revision,
        "source_canon_hash": source_canon_hash,
        "resolved_profile_hash": str(metadata.get("resolved_profile_hash") or ""),
        "seed": seed,
        "capabilities": capabilities,
        "agents": tuple(plan.model_dump(mode="json") for plan in agents),
        "clocks": tuple(clock.model_dump(mode="json") for clock in clocks),
        "resources": tuple(resource.model_dump(mode="json") for resource in resources),
        "checks": checks,
        "passed": all(checks.values()),
    }
    return _hash_model(RuntimeSeedDocument, payload)


def _normalize_identity(value: Any) -> str:
    rendered = str(value or "").strip().casefold()
    if ":" in rendered:
        rendered = rendered.split(":", 1)[-1]
    return "_".join(
        "".join(character if character.isalnum() else " " for character in rendered).split()
    )


def compile_vertical_slice(
    *,
    runtime_seed: RuntimeSeedDocument,
    canon: Mapping[str, Any],
    starting_location: str = "",
) -> VerticalSliceMaterializationDocument:
    entities = _entities(canon)
    place_kinds = _domain_kinds(canon, "places") or {"place", "location"}
    group_kinds = _domain_kinds(canon, "groups") or {"group", "faction"}
    opening_kinds = _domain_kinds(canon, "opening_threads") or {"opening_thread"}
    places = [
        (entity_id, entity)
        for entity_id, entity in sorted(entities.items())
        if str(entity.get("kind") or "") in place_kinds
    ]
    requested = _normalize_identity(starting_location)
    hub_matches = [
        entity_id
        for entity_id, entity in places
        if requested
        and requested
        in {
            _normalize_identity(entity_id),
            _normalize_identity(entity.get("name")),
        }
    ]
    hub_id = hub_matches[0] if len(hub_matches) == 1 else ""
    if not requested and places:
        hub_id = places[0][0]
    sublocations = tuple(
        entity_id
        for entity_id, entity in places
        if str(entity.get("parent_place_id") or "") == hub_id
    )[:5]
    nearby = tuple(
        entity_id
        for entity_id, _ in places
        if entity_id != hub_id and entity_id not in sublocations
    )[: max(0, 5 - len(sublocations))]
    actor_ids = tuple(
        plan.agent_id
        for plan in runtime_seed.agents
        if plan.agent_kind not in group_kinds
    )[:6]
    group_ids = tuple(
        plan.agent_id
        for plan in runtime_seed.agents
        if plan.agent_kind in group_kinds
    )[:3]
    clock_ids = tuple(clock.clock_id for clock in runtime_seed.clocks)[:3]
    resource_ids = tuple(resource.resource_id for resource in runtime_seed.resources)[:5]
    opening_ids = tuple(
        entity_id
        for entity_id, entity in sorted(entities.items())
        if str(entity.get("kind") or "") in opening_kinds
    )[:3]
    location_count = int(bool(hub_id)) + len(sublocations) + len(nearby)
    checks = {
        "runtime_seed_valid": runtime_seed.passed,
        "starting_location_resolved": bool(hub_id),
        "three_locations_materialized": location_count >= 3,
        "three_major_actors_materialized": len(actor_ids) >= 3,
        "three_groups_materialized": len(group_ids) >= 3,
        "three_clocks_materialized": len(clock_ids) >= 3,
        "opening_thread_materialized": bool(opening_ids),
    }
    payload = {
        "world_id": runtime_seed.world_id,
        "world_revision": runtime_seed.world_revision,
        "runtime_seed_hash": runtime_seed.content_hash,
        "hub_location_id": hub_id,
        "sublocation_ids": sublocations,
        "nearby_location_ids": nearby,
        "actor_ids": actor_ids,
        "group_ids": group_ids,
        "clock_ids": clock_ids,
        "resource_ids": resource_ids,
        "opening_thread_ids": opening_ids,
        "checks": checks,
        "passed": all(checks.values()),
    }
    return _hash_model(VerticalSliceMaterializationDocument, payload)


def _initial_runtime_state(seed: RuntimeSeedDocument) -> dict[str, Any]:
    return {
        "day": 0,
        "agents": {
            plan.agent_id: {
                "location_id": plan.location_id,
                "objective": plan.objective,
                "next_action": plan.next_action,
                "executions": 0,
                "last_action": "",
            }
            for plan in seed.agents
        },
        "clocks": {
            clock.clock_id: {
                "step": 0,
                "threshold": clock.threshold,
                "status": "active",
                "last_change": "",
            }
            for clock in seed.clocks
        },
        "resources": {
            resource.resource_id: {
                "state": resource.state,
                "quantity": resource.quantity,
                "daily_delta": resource.daily_delta,
                "status": "available",
            }
            for resource in seed.resources
        },
    }


def _advance_day(state: Mapping[str, Any], day: int) -> tuple[dict[str, Any], dict[str, Any]]:
    next_state = json.loads(json.dumps(state, sort_keys=True, ensure_ascii=False))
    next_state["day"] = day
    events: list[dict[str, Any]] = []
    for agent_id, agent in sorted(_mapping(next_state.get("agents")).items()):
        agent["executions"] = int(agent.get("executions") or 0) + 1
        agent["last_action"] = str(agent.get("next_action") or "")
        events.append(
            {
                "type": "agent_action",
                "agent_id": agent_id,
                "action": agent["last_action"],
            }
        )
    for clock_id, clock in sorted(_mapping(next_state.get("clocks")).items()):
        clock["step"] = int(clock.get("step") or 0) + 1
        threshold = int(clock.get("threshold") or 1)
        clock["status"] = "escalated" if clock["step"] >= threshold else "active"
        clock["last_change"] = f"advanced_to_{clock['step']}"
        events.append(
            {
                "type": "clock_advanced",
                "clock_id": clock_id,
                "step": clock["step"],
                "status": clock["status"],
            }
        )
    for resource_id, resource in sorted(_mapping(next_state.get("resources")).items()):
        quantity = resource.get("quantity")
        daily_delta = resource.get("daily_delta")
        if isinstance(quantity, (int, float)) and isinstance(daily_delta, (int, float)):
            resource["quantity"] = float(quantity) + float(daily_delta)
            if resource["quantity"] <= 0:
                resource["status"] = "depleted"
            events.append(
                {
                    "type": "resource_changed",
                    "resource_id": resource_id,
                    "quantity": resource["quantity"],
                    "status": resource["status"],
                }
            )
    return next_state, {"day": day, "events": events}


def _simulate(
    seed: RuntimeSeedDocument,
    *,
    days: int,
    initial_state: Mapping[str, Any] | None = None,
    start_day: int = 1,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    state = dict(initial_state or _initial_runtime_state(seed))
    events: list[dict[str, Any]] = []
    for day in range(start_day, start_day + days):
        state, day_events = _advance_day(state, day)
        events.append(day_events)
    return state, tuple(events)


def run_player_absent_playtest(
    runtime_seed: RuntimeSeedDocument,
    *,
    days: int = 7,
) -> PlayerAbsentPlaytestReport:
    direct_state, direct_events = _simulate(runtime_seed, days=days)
    midpoint = max(1, days // 2)
    first_state, first_events = _simulate(runtime_seed, days=midpoint)
    reloaded = json.loads(json.dumps(first_state, sort_keys=True, ensure_ascii=False))
    resumed_state, resumed_events = _simulate(
        runtime_seed,
        days=days - midpoint,
        initial_state=reloaded,
        start_day=midpoint + 1,
    )
    direct_hash = canonical_content_hash(direct_state)
    resumed_hash = canonical_content_hash(resumed_state)
    all_events = (*first_events, *resumed_events)
    checks = {
        "runtime_seed_valid": runtime_seed.passed,
        "seven_day_window_completed": days == 7 and direct_state.get("day") == 7,
        "every_day_produced_events": len(direct_events) == days
        and all(day.get("events") for day in direct_events),
        "agents_advanced": bool(runtime_seed.agents)
        and all(
            int(agent.get("executions") or 0) == days
            for agent in _mapping(direct_state.get("agents")).values()
        ),
        "clocks_advanced": bool(runtime_seed.clocks)
        and all(
            int(clock.get("step") or 0) == days
            for clock in _mapping(direct_state.get("clocks")).values()
        ),
        "save_load_equivalent": direct_hash == resumed_hash,
    }
    payload = {
        "runtime_seed_hash": runtime_seed.content_hash,
        "days_simulated": days,
        "daily_events": all_events,
        "direct_final_state_hash": direct_hash,
        "reloaded_final_state_hash": resumed_hash,
        "checks": checks,
        "passed": all(checks.values()),
    }
    return _hash_model(PlayerAbsentPlaytestReport, payload)
