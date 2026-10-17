"""Deterministic, profile-aware geography and historical planning for World Forge."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

_VOCABULARIES = {
    "cyberpunk": {
        "terrains": ("megacity_core", "industrial_belt", "flooded_arcology", "network_zone", "peripheral_wastes"),
        "resources": ("compute", "energy", "data", "biotech", "clean_water"),
    },
    "space": {
        "terrains": ("orbital_habitat", "planetary_basin", "asteroid_belt", "jump_corridor", "frontier_station"),
        "resources": ("reactor_fuel", "water", "rare_metals", "biomass", "navigation_data"),
    },
    "post_apocalyptic": {
        "terrains": ("ruined_city", "scarred_plain", "contaminated_zone", "survivor_enclave", "dry_basin"),
        "resources": ("clean_water", "salvage", "fuel", "medicine", "food_stock"),
    },
    "fantasy": {
        "terrains": ("river_plain", "highland", "coast", "ancient_forest", "dry_basin"),
        "resources": ("grain", "ore", "timber", "salt", "fresh_water"),
    },
    "modern": {
        "terrains": ("urban_core", "industrial_zone", "coastal_district", "rural_belt", "transport_corridor"),
        "resources": ("energy", "food_supply", "materials", "capital", "water"),
    },
    "generic": {
        "terrains": ("population_core", "production_zone", "transit_zone", "resource_zone", "frontier_zone"),
        "resources": ("energy", "materials", "food_supply", "information", "water"),
    },
}


def _rows(registry: Mapping[str, Any], domain_id: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(row)
        for row in registry.get("anchors") or ()
        if isinstance(row, Mapping) and str(row.get("domain_id") or "") == domain_id
    )


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _context_text(world_context: Mapping[str, Any] | None) -> str:
    context = dict(world_context or {})
    brief = context.get("world_brief")
    profile = context.get("resolved_genre_profile")
    values = [
        context.get("genre"),
        context.get("campaign_template"),
        context.get("tone"),
    ]
    if isinstance(brief, Mapping):
        values.extend(brief.get(key) for key in ("title", "description", "genre", "tone"))
    if isinstance(profile, Mapping):
        values.extend(profile.get(key) for key in ("profile_id", "display_name"))
        values.extend(profile.get("genre_tags") or ())
    return " ".join(str(value or "") for value in values).casefold()


def resolve_planning_family(world_context: Mapping[str, Any] | None) -> str:
    text = _context_text(world_context)
    families = (
        ("cyberpunk", ("cyberpunk", "megacity", "neon", "corporate", "netrunner", "digital dystopia")),
        ("space", ("space opera", "spaceship", "starship", "galactic", "orbital", "interstellar", "sci-fi")),
        ("post_apocalyptic", ("post-apocalyptic", "post apocalyptic", "fallout", "wasteland", "nuclear ruin")),
        ("fantasy", ("fantasy", "medieval", "magic", "sorcery", "kingdom")),
        ("modern", ("modern", "contemporary", "present day", "urban thriller")),
    )
    for family, markers in families:
        if any(marker in text for marker in markers):
            return family
    return "generic"


def build_world_invariants(
    *,
    seed: int,
    world_key: str,
    world_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(world_context or {})
    payload: dict[str, Any] = {
        "schema_version": "rpg_world_invariants_v2",
        "revision": 2,
        "seed": int(seed),
        "world_key": str(world_key),
        "planning_family": resolve_planning_family(context),
        "resolved_profile_hash": str(context.get("resolved_profile_hash") or ""),
        "world_context_hash": _digest({"context": context}),
        "invariants": {
            "causality_is_ordered": True,
            "canon_is_materialised_once": True,
            "planning_is_internal": True,
            "prose_cannot_mutate_state": True,
            "planning_must_match_world_context": True,
        },
    }
    payload["content_hash"] = _digest(payload)
    return payload


def build_geography_resource_plan(
    anchor_registry: Mapping[str, Any],
    *,
    seed: int,
    world_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    family = resolve_planning_family(world_context)
    vocabulary = _VOCABULARIES[family]
    terrains = vocabulary["terrains"]
    resources = vocabulary["resources"]
    regions = _rows(anchor_registry, "regions")
    region_plans = []
    for index, region in enumerate(regions):
        region_plans.append(
            {
                "region_id": region["id"],
                "terrain": terrains[(seed + index) % len(terrains)],
                "primary_resource": resources[(seed * 3 + index) % len(resources)],
                "route_capacity": 40 + ((seed + index * 17) % 61),
                "settlement_capacity": 1 + ((seed + index) % 4),
                "strategic_value": 20 + ((seed * 7 + index * 11) % 81),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": "rpg_geography_resource_plan_v2",
        "revision": 2,
        "planning_family": family,
        "anchor_registry_hash": str(anchor_registry.get("registry_hash") or ""),
        "regions": region_plans,
    }
    payload["content_hash"] = _digest(payload)
    return payload


def _initial_state(geography_plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["region_id"]): {
            "population_index": 50,
            "trade_access": int(row["route_capacity"]),
            "political_stability": 60,
            "resource_access": 70,
        }
        for row in geography_plan.get("regions") or ()
        if isinstance(row, Mapping) and str(row.get("region_id") or "")
    }


def apply_historical_deltas(
    state: Mapping[str, Mapping[str, Any]],
    deltas: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    next_state = {target: dict(values) for target, values in state.items()}
    for delta in deltas:
        target_id = str(delta.get("target_id") or "")
        dimension = str(delta.get("dimension") or "")
        operation = str(delta.get("operation") or "")
        if target_id not in next_state or not dimension:
            raise ValueError(f"historical_delta_target_unknown:{target_id}:{dimension}")
        before = next_state[target_id].get(dimension, 0)
        value = delta.get("value")
        if operation == "replace":
            after = value
        elif operation == "increase":
            after = before + value
        elif operation == "decrease":
            after = before - value
        elif operation == "multiply":
            after = before * value
        else:
            raise ValueError(f"historical_delta_operation_unsupported:{operation}")
        if isinstance(after, (int, float)):
            after = max(0, min(100, round(after, 4)))
        next_state[target_id][dimension] = after
    return next_state


def build_historical_epoch_plan(
    anchor_registry: Mapping[str, Any],
    geography_plan: Mapping[str, Any],
    *,
    seed: int,
    era_count: int = 3,
) -> dict[str, Any]:
    events = _rows(anchor_registry, "history_timeline")
    regions = tuple(_initial_state(geography_plan))
    eras = max(1, int(era_count))
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(eras)]
    for index, event in enumerate(events):
        target_id = regions[index % len(regions)] if regions else ""
        dimension = (
            "trade_access",
            "political_stability",
            "population_index",
            "resource_access",
        )[index % 4]
        operation = "increase" if index % 3 == 0 else "decrease"
        value = 5 + ((seed + index * 7) % 16)
        buckets[index % eras].append(
            {
                "event_id": event["id"],
                "year": 100 + index * 40,
                "deltas": [
                    {
                        "target_id": target_id,
                        "dimension": dimension,
                        "operation": operation,
                        "value": value,
                    }
                ] if target_id else [],
            }
        )
    state = _initial_state(geography_plan)
    epoch_rows = []
    for era_index, event_rows in enumerate(buckets, start=1):
        start_state = {key: dict(value) for key, value in state.items()}
        deltas = [delta for event in event_rows for delta in event["deltas"]]
        state = apply_historical_deltas(state, deltas)
        epoch_rows.append(
            {
                "era_id": f"era:{era_index:02d}",
                "sequence": era_index,
                "events": event_rows,
                "start_state": start_state,
                "end_state": {key: dict(value) for key, value in state.items()},
            }
        )
    payload: dict[str, Any] = {
        "schema_version": "rpg_historical_epoch_plan_v2",
        "revision": 2,
        "planning_family": str(geography_plan.get("planning_family") or "generic"),
        "initial_state": _initial_state(geography_plan),
        "epochs": epoch_rows,
    }
    payload["content_hash"] = _digest(payload)
    return payload


def build_present_day_state(historical_plan: Mapping[str, Any]) -> dict[str, Any]:
    epochs = historical_plan.get("epochs") or ()
    if not isinstance(epochs, Sequence):
        raise ValueError("historical_epochs_array_required")
    final_state = (
        dict(epochs[-1].get("end_state") or {})
        if epochs and isinstance(epochs[-1], Mapping)
        else dict(historical_plan.get("initial_state") or {})
    )
    payload: dict[str, Any] = {
        "schema_version": "rpg_present_day_state_v2",
        "revision": 2,
        "historical_plan_hash": str(historical_plan.get("content_hash") or ""),
        "state": final_state,
    }
    payload["content_hash"] = _digest(payload)
    return payload


def build_historical_planning_topics(
    anchor_registry: Mapping[str, Any],
    *,
    seed: int,
    world_key: str,
    world_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    invariants = build_world_invariants(
        seed=seed,
        world_key=world_key,
        world_context=world_context,
    )
    geography = build_geography_resource_plan(
        anchor_registry,
        seed=seed,
        world_context=world_context,
    )
    history = build_historical_epoch_plan(anchor_registry, geography, seed=seed)
    present = build_present_day_state(history)
    return {
        "world_invariants": invariants,
        "geography_resource_plan": geography,
        "historical_epoch_plan": history,
        "present_day_state": present,
    }


__all__ = [
    "apply_historical_deltas",
    "build_geography_resource_plan",
    "build_historical_epoch_plan",
    "build_historical_planning_topics",
    "build_present_day_state",
    "build_world_invariants",
    "resolve_planning_family",
]
