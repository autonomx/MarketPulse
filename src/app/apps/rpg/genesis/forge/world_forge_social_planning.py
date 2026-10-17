"""Profile-aware political, settlement-origin, and culture-lineage planning."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

_CLAIM_TYPES = ("sovereignty", "stewardship", "ancestral", "resource", "security")
_SOCIAL_VOCABULARIES = {
    "cyberpunk": {
        "founding": ("data_exchange", "security_hub", "industrial_platform", "displaced_enclave", "infrastructure_node"),
        "lineage": ("local_network", "migrant_collective", "hybrid_subculture", "corporate_shaped", "splinter_movement"),
        "adaptation": ("network_access", "vertical_security", "flood_control", "supply_brokering", "surveillance_evasion"),
    },
    "space": {
        "founding": ("trade_station", "defence_outpost", "extraction_base", "refuge_habitat", "navigation_node"),
        "lineage": ("planetary_native", "diaspora", "hybrid_colony", "institutional", "splinter_fleet"),
        "adaptation": ("vacuum_operations", "orbital_navigation", "radiation_shelter", "closed_loop_ecology", "long_range_trade"),
    },
    "post_apocalyptic": {
        "founding": ("salvage_market", "fortified_shelter", "resource_camp", "refuge", "water_node"),
        "lineage": ("survivor_local", "displaced", "hybrid_enclave", "institutional_remnant", "splinter_band"),
        "adaptation": ("salvage_craft", "contamination_avoidance", "water_conservation", "convoy_defence", "scarcity_barter"),
    },
    "fantasy": {
        "founding": ("trade_hub", "fortress", "resource_camp", "refuge", "ritual_centre"),
        "lineage": ("indigenous", "diaspora", "syncretic", "court_shaped", "schismatic"),
        "adaptation": ("river_trade", "highland_defence", "coastal_navigation", "forest_stewardship", "dryland_conservation"),
    },
    "modern": {
        "founding": ("commercial_centre", "security_hub", "industrial_zone", "resettlement_district", "transport_node"),
        "lineage": ("local", "diaspora", "hybrid", "institutional", "splinter_movement"),
        "adaptation": ("public_transit", "urban_security", "coastal_resilience", "supply_logistics", "information_networks"),
    },
    "generic": {
        "founding": ("exchange_node", "defence_node", "production_site", "refuge", "coordination_centre"),
        "lineage": ("local", "migrant", "hybrid", "institutional", "splinter"),
        "adaptation": ("trade_coordination", "collective_defence", "resource_stewardship", "mobility", "information_sharing"),
    },
}


def _rows(registry: Mapping[str, Any], domain_id: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(row)
        for row in registry.get("anchors") or ()
        if isinstance(row, Mapping) and str(row.get("domain_id") or "") == domain_id
    )


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _event_ids(historical_plan: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(event.get("event_id") or "")
        for epoch in historical_plan.get("epochs") or ()
        if isinstance(epoch, Mapping)
        for event in epoch.get("events") or ()
        if isinstance(event, Mapping) and str(event.get("event_id") or "")
    )


def _family(source: Mapping[str, Any] | None) -> str:
    candidate = str(dict(source or {}).get("planning_family") or "generic")
    return candidate if candidate in _SOCIAL_VOCABULARIES else "generic"


def build_political_claim_graph(
    anchor_registry: Mapping[str, Any],
    present_day_state: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, Any]:
    groups = _rows(anchor_registry, "groups")
    regions = _rows(anchor_registry, "regions")
    state = dict(present_day_state.get("state") or {})
    claims = []
    for index, group in enumerate(groups):
        if not regions:
            break
        region = regions[(seed + index) % len(regions)]
        stability = int(dict(state.get(region["id"]) or {}).get("political_stability") or 0)
        claims.append(
            {
                "claim_id": f"claim:{index + 1:03d}",
                "claimant_group_id": group["id"],
                "target_region_id": region["id"],
                "claim_type": _CLAIM_TYPES[(seed * 5 + index) % len(_CLAIM_TYPES)],
                "legitimacy": 25 + ((seed + index * 19) % 76),
                "control_index": max(0, min(100, 100 - stability + (index % 17))),
                "status": "contested" if stability < 65 else "recognised",
                "break_condition": "control_below_20",
            }
        )
    rivalries = []
    by_region: dict[str, list[str]] = {}
    for claim in claims:
        by_region.setdefault(claim["target_region_id"], []).append(claim["claimant_group_id"])
    for region_id, claimants in sorted(by_region.items()):
        for left, right in zip(claimants, claimants[1:]):
            rivalries.append(
                {
                    "source_group_id": left,
                    "target_group_id": right,
                    "region_id": region_id,
                    "relationship": "rival_claimants",
                }
            )
    payload: dict[str, Any] = {
        "schema_version": "rpg_political_claim_graph_v2",
        "revision": 2,
        "present_day_state_hash": str(present_day_state.get("content_hash") or ""),
        "claims": claims,
        "rivalries": rivalries,
    }
    payload["content_hash"] = _digest(payload)
    return payload


def build_settlement_origin_plan(
    anchor_registry: Mapping[str, Any],
    geography_plan: Mapping[str, Any],
    historical_plan: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, Any]:
    places = _rows(anchor_registry, "places")
    geography = tuple(dict(row) for row in geography_plan.get("regions") or () if isinstance(row, Mapping))
    events = _event_ids(historical_plan)
    family = _family(geography_plan)
    purposes = _SOCIAL_VOCABULARIES[family]["founding"]
    settlements = []
    for index, place in enumerate(places):
        if not geography:
            break
        region = geography[(seed + index) % len(geography)]
        event_id = events[index % len(events)] if events else ""
        settlements.append(
            {
                "place_id": place["id"],
                "region_id": region["region_id"],
                "founding_event_id": event_id,
                "founding_purpose": purposes[(seed + index) % len(purposes)],
                "founded_year": 120 + index * 35,
                "resource_dependency": region["primary_resource"],
                "route_dependency": int(region["route_capacity"]),
                "strategic_value": int(region["strategic_value"]),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": "rpg_settlement_origin_plan_v2",
        "revision": 2,
        "planning_family": family,
        "geography_plan_hash": str(geography_plan.get("content_hash") or ""),
        "historical_plan_hash": str(historical_plan.get("content_hash") or ""),
        "settlements": settlements,
    }
    payload["content_hash"] = _digest(payload)
    return payload


def build_culture_lineage_plan(
    anchor_registry: Mapping[str, Any],
    historical_plan: Mapping[str, Any],
    present_day_state: Mapping[str, Any],
    *,
    seed: int,
    geography_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build lineages while preserving the established positional call contract."""

    cultures = _rows(anchor_registry, "cultures")
    regions = _rows(anchor_registry, "regions")
    events = _event_ids(historical_plan)
    state = dict(present_day_state.get("state") or {})
    family = _family(geography_plan or historical_plan)
    vocabulary = _SOCIAL_VOCABULARIES[family]
    lineages = []
    for index, culture in enumerate(cultures):
        region = regions[(seed + index) % len(regions)] if regions else None
        region_id = str(region["id"]) if region else ""
        region_state = dict(state.get(region_id) or {})
        parent_id = cultures[index - 1]["id"] if index > 0 and index % 3 == 0 else ""
        lineages.append(
            {
                "culture_id": culture["id"],
                "parent_culture_id": parent_id,
                "homeland_region_ids": [region_id] if region_id else [],
                "origin_event_id": events[index % len(events)] if events else "",
                "lineage_type": vocabulary["lineage"][(seed * 2 + index) % len(vocabulary["lineage"])],
                "environmental_adaptation": vocabulary["adaptation"][(seed + index) % len(vocabulary["adaptation"])],
                "cohesion_index": max(
                    0,
                    min(100, int(region_state.get("political_stability") or 50) + (index % 9) - 4),
                ),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": "rpg_culture_lineage_plan_v2",
        "revision": 2,
        "planning_family": family,
        "historical_plan_hash": str(historical_plan.get("content_hash") or ""),
        "present_day_state_hash": str(present_day_state.get("content_hash") or ""),
        "lineages": lineages,
    }
    payload["content_hash"] = _digest(payload)
    return payload


def build_social_planning_topics(
    anchor_registry: Mapping[str, Any],
    geography_plan: Mapping[str, Any],
    historical_plan: Mapping[str, Any],
    present_day_state: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, Any]:
    return {
        "political_claim_graph": build_political_claim_graph(
            anchor_registry, present_day_state, seed=seed
        ),
        "settlement_origin_plan": build_settlement_origin_plan(
            anchor_registry,
            geography_plan,
            historical_plan,
            seed=seed,
        ),
        "culture_lineage_plan": build_culture_lineage_plan(
            anchor_registry,
            historical_plan,
            present_day_state,
            seed=seed,
            geography_plan=geography_plan,
        ),
    }


__all__ = [
    "build_culture_lineage_plan",
    "build_political_claim_graph",
    "build_settlement_origin_plan",
    "build_social_planning_topics",
]
