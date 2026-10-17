"""Present-pressure and opening-scope planning for World Forge."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

_PRESSURE_DIMENSIONS = (
    "political_stability",
    "trade_access",
    "resource_access",
    "population_index",
)
_TRENDS = ("escalating", "volatile", "contained")


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


def build_pressure_plan(
    anchor_registry: Mapping[str, Any],
    present_day_state: Mapping[str, Any],
    political_claim_graph: Mapping[str, Any],
    settlement_origin_plan: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, Any]:
    state = dict(present_day_state.get("state") or {})
    claims = tuple(
        dict(row)
        for row in political_claim_graph.get("claims") or ()
        if isinstance(row, Mapping)
    )
    settlements = tuple(
        dict(row)
        for row in settlement_origin_plan.get("settlements") or ()
        if isinstance(row, Mapping)
    )
    groups = _rows(anchor_registry, "groups")
    pressures = []
    count = max(1, min(8, len(claims) or len(settlements) or 1))
    for index in range(count):
        claim = claims[index % len(claims)] if claims else {}
        settlement = settlements[index % len(settlements)] if settlements else {}
        region_id = str(
            claim.get("target_region_id") or settlement.get("region_id") or ""
        )
        region_state = dict(state.get(region_id) or {})
        dimension = _PRESSURE_DIMENSIONS[(seed + index) % len(_PRESSURE_DIMENSIONS)]
        current_value = int(region_state.get(dimension) or 50)
        severity = max(0, min(100, 100 - current_value + ((seed + index * 13) % 21)))
        group_id = str(claim.get("claimant_group_id") or "")
        if not group_id and groups:
            group_id = str(groups[index % len(groups)]["id"])
        pressures.append(
            {
                "pressure_id": f"pressure:{index + 1:03d}",
                "root_claim_id": str(claim.get("claim_id") or ""),
                "affected_region_ids": [region_id] if region_id else [],
                "affected_place_ids": [str(settlement.get("place_id"))]
                if settlement.get("place_id")
                else [],
                "affected_group_ids": [group_id] if group_id else [],
                "dimension": dimension,
                "severity": severity,
                "trend": _TRENDS[(seed * 3 + index) % len(_TRENDS)],
                "next_tick_delta": {
                    "target_id": region_id,
                    "dimension": dimension,
                    "operation": "decrease",
                    "value": 1 + severity // 20,
                } if region_id else {},
                "escalation_threshold": min(100, severity + 15),
                "resolution_threshold": max(0, severity - 25),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": "rpg_pressure_plan_v1",
        "revision": 1,
        "present_day_state_hash": str(present_day_state.get("content_hash") or ""),
        "political_claim_graph_hash": str(political_claim_graph.get("content_hash") or ""),
        "settlement_origin_plan_hash": str(settlement_origin_plan.get("content_hash") or ""),
        "pressures": pressures,
    }
    payload["content_hash"] = _digest(payload)
    return payload


def build_opening_scope_plan(
    anchor_registry: Mapping[str, Any],
    pressure_plan: Mapping[str, Any],
    *,
    seed: int,
    maximum_pressures: int = 3,
) -> dict[str, Any]:
    pressures = sorted(
        (
            dict(row)
            for row in pressure_plan.get("pressures") or ()
            if isinstance(row, Mapping)
        ),
        key=lambda row: (-int(row.get("severity") or 0), str(row.get("pressure_id") or "")),
    )[: max(1, int(maximum_pressures))]
    actors = _rows(anchor_registry, "actors")
    groups = _rows(anchor_registry, "groups")
    places = _rows(anchor_registry, "places")
    selected_place_ids = tuple(
        dict.fromkeys(
            str(place_id)
            for pressure in pressures
            for place_id in pressure.get("affected_place_ids") or ()
            if str(place_id)
        )
    )
    if not selected_place_ids and places:
        selected_place_ids = (str(places[seed % len(places)]["id"]),)
    selected_group_ids = tuple(
        dict.fromkeys(
            str(group_id)
            for pressure in pressures
            for group_id in pressure.get("affected_group_ids") or ()
            if str(group_id)
        )
    )
    payload: dict[str, Any] = {
        "schema_version": "rpg_opening_scope_plan_v1",
        "revision": 1,
        "pressure_plan_hash": str(pressure_plan.get("content_hash") or ""),
        "pressure_ids": [str(row.get("pressure_id") or "") for row in pressures],
        "place_ids": list(selected_place_ids[:3]),
        "group_ids": list(selected_group_ids[:3]),
        "actor_ids": [str(row["id"]) for row in actors[:3]],
        "thread_slots": [
            {
                "thread_id": f"opening-thread:{index + 1:02d}",
                "pressure_id": str(pressure.get("pressure_id") or ""),
                "evidence_dimension": str(pressure.get("dimension") or ""),
                "initial_visibility": "local_observable",
            }
            for index, pressure in enumerate(pressures)
        ],
    }
    payload["content_hash"] = _digest(payload)
    return payload


def build_pressure_planning_topics(
    anchor_registry: Mapping[str, Any],
    present_day_state: Mapping[str, Any],
    political_claim_graph: Mapping[str, Any],
    settlement_origin_plan: Mapping[str, Any],
    *,
    seed: int,
) -> dict[str, Any]:
    pressure = build_pressure_plan(
        anchor_registry,
        present_day_state,
        political_claim_graph,
        settlement_origin_plan,
        seed=seed,
    )
    opening = build_opening_scope_plan(
        anchor_registry,
        pressure,
        seed=seed,
    )
    return {
        "pressure_plan": pressure,
        "opening_scope_plan": opening,
    }


__all__ = [
    "build_opening_scope_plan",
    "build_pressure_plan",
    "build_pressure_planning_topics",
]
