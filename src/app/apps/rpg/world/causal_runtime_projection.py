"""Project causal runtime state into deterministic gameplay subsystems."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _clamp(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


def _hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _cells(runtime: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    state = _dict(runtime.get("state"))
    return {
        str(target_id): _dict(cell)
        for target_id, cell in _dict(state.get("cells")).items()
    }


def _values(cell: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(key): float(value)
        for key, value in _dict(cell.get("values")).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _region_summary(cells: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    rows = []
    for target_id, cell in sorted(cells.items()):
        if str(cell.get("target_kind") or "") != "region":
            continue
        rows.append(
            {
                "region_id": target_id,
                **_values(cell),
                "revision": int(cell.get("revision") or 1),
            }
        )
    return tuple(rows)


def _average(rows: tuple[Mapping[str, Any], ...], field: str, default: float = 50.0) -> float:
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    return sum(values) / len(values) if values else default


def _pressure_rows(runtime: Mapping[str, Any], cells: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    statuses = _dict(runtime.get("pressure_statuses"))
    rows = []
    for raw in _list(_dict(runtime.get("pressure_plan")).get("pressures")):
        pressure = _dict(raw)
        pressure_id = str(pressure.get("pressure_id") or "")
        if not pressure_id:
            continue
        severity = _values(cells.get(pressure_id, {})).get(
            "pressure_severity",
            float(pressure.get("severity") or 0),
        )
        rows.append(
            {
                **pressure,
                "pressure_id": pressure_id,
                "severity": severity,
                "status": str(statuses.get(pressure_id) or "active"),
            }
        )
    return tuple(rows)


def _project_economy(
    simulation_state: dict[str, Any],
    regions: tuple[Mapping[str, Any], ...],
    pressures: tuple[Mapping[str, Any], ...],
    *,
    tick: int,
) -> dict[str, Any]:
    trade = _average(regions, "trade_access")
    resources = _average(regions, "resource_access")
    active_severity = _average(
        tuple(row for row in pressures if row.get("status") != "resolved"),
        "severity",
        0.0,
    )
    multiplier_bps = _clamp(
        10000 + (100.0 - trade) * 18.0 + (100.0 - resources) * 22.0 + active_severity * 8.0,
        8000,
        15000,
    )
    economy = _dict(simulation_state.get("economy_state"))
    economy.update(
        {
            "causal_price_multiplier_bps": multiplier_bps,
            "causal_trade_access": round(trade, 2),
            "causal_resource_access": round(resources, 2),
            "causal_projection_tick": tick,
            "causal_projection_source": "deterministic_causal_runtime",
        }
    )
    simulation_state["economy_state"] = economy
    merchant_root = _dict(simulation_state.get("merchant_state"))
    merchant_root["causal_price_multiplier_bps"] = multiplier_bps
    merchants = _dict(merchant_root.get("merchants"))
    for merchant_id, raw in merchants.items():
        merchant = _dict(raw)
        merchant["causal_price_multiplier_bps"] = multiplier_bps
        merchants[str(merchant_id)] = merchant
    if merchants:
        merchant_root["merchants"] = merchants
    simulation_state["merchant_state"] = merchant_root
    return {
        "price_multiplier_bps": multiplier_bps,
        "trade_access": round(trade, 2),
        "resource_access": round(resources, 2),
    }


def _project_travel(
    simulation_state: dict[str, Any],
    regions: tuple[Mapping[str, Any], ...],
    pressures: tuple[Mapping[str, Any], ...],
    *,
    tick: int,
) -> dict[str, Any]:
    stability = _average(regions, "political_stability", 60.0)
    trade = _average(regions, "trade_access")
    active_severity = _average(
        tuple(row for row in pressures if row.get("status") != "resolved"),
        "severity",
        0.0,
    )
    safety = _clamp(stability - active_severity * 0.25, 0, 100)
    multiplier_bps = _clamp(
        10000 + (100.0 - trade) * 12.0 + (100.0 - safety) * 10.0,
        8000,
        16000,
    )
    travel = _dict(simulation_state.get("travel_state"))
    travel.update(
        {
            "causal_cost_multiplier_bps": multiplier_bps,
            "causal_safety_index": safety,
            "causal_projection_tick": tick,
            "causal_projection_source": "deterministic_causal_runtime",
        }
    )
    simulation_state["travel_state"] = travel
    return {"cost_multiplier_bps": multiplier_bps, "safety_index": safety}


def _project_factions(
    simulation_state: dict[str, Any],
    runtime: Mapping[str, Any],
    cells: Mapping[str, Mapping[str, Any]],
    pressures: tuple[Mapping[str, Any], ...],
    *,
    tick: int,
) -> dict[str, Any]:
    projection_plan = _dict(runtime.get("projection_plan"))
    claims = _list(_dict(projection_plan.get("political_claim_graph")).get("claims"))
    faction_state = _dict(simulation_state.get("faction_reputation"))
    affected: set[str] = set()
    for pressure in pressures:
        for group_id in _list(pressure.get("affected_group_ids")):
            if str(group_id):
                affected.add(str(group_id))
    projected = []
    for raw in claims:
        claim = _dict(raw)
        group_id = str(claim.get("claimant_group_id") or "")
        claim_id = str(claim.get("claim_id") or "")
        if not group_id:
            continue
        row = _dict(faction_state.get(group_id))
        row.setdefault("faction_id", group_id)
        row.setdefault("reputation", 0)
        row.setdefault("tier", "neutral")
        control = _values(cells.get(claim_id, {})).get(
            "control_index",
            float(claim.get("control_index") or 50),
        )
        group_pressures = [
            pressure
            for pressure in pressures
            if group_id in {str(item) for item in _list(pressure.get("affected_group_ids"))}
        ]
        row.update(
            {
                "causal_control_index": int(round(control)),
                "causal_pressure_ids": [str(item.get("pressure_id") or "") for item in group_pressures],
                "causal_projection_tick": tick,
                "causal_projection_source": "deterministic_causal_runtime",
            }
        )
        faction_state[group_id] = row
        projected.append(group_id)
    simulation_state["faction_reputation"] = faction_state
    return {"faction_ids": sorted(projected), "affected_faction_ids": sorted(affected)}


def _project_npcs(
    simulation_state: dict[str, Any],
    runtime: Mapping[str, Any],
    pressures: tuple[Mapping[str, Any], ...],
    *,
    tick: int,
) -> dict[str, Any]:
    opening = _dict(_dict(runtime.get("projection_plan")).get("opening_scope_plan"))
    actor_ids = [str(value) for value in _list(opening.get("actor_ids")) if str(value)]
    active = sorted(
        (row for row in pressures if row.get("status") != "resolved"),
        key=lambda row: (-float(row.get("severity") or 0), str(row.get("pressure_id") or "")),
    )
    leading = active[0] if active else {}
    pressure_id = str(leading.get("pressure_id") or "")
    status = str(leading.get("status") or "resolved")
    npc_presence = _dict(simulation_state.get("npc_presence"))
    for actor_id in actor_ids:
        row = _dict(npc_presence.get(actor_id))
        if pressure_id:
            row["activity"] = f"responding to {pressure_id} ({status})"
            row["next_action"] = {
                "kind": "respond_to_world_pressure",
                "pressure_id": pressure_id,
                "status": status,
                "scheduled_tick": tick + 1,
                "source": "deterministic_causal_runtime",
            }
        else:
            row["activity"] = "maintaining local responsibilities"
            row["next_action"] = {
                "kind": "maintain_position",
                "scheduled_tick": tick + 1,
                "source": "deterministic_causal_runtime",
            }
        row["causal_projection_tick"] = tick
        npc_presence[actor_id] = row
    simulation_state["npc_presence"] = npc_presence
    return {"actor_ids": actor_ids, "leading_pressure_id": pressure_id}


def project_causal_runtime_to_subsystems(
    simulation_state: dict[str, Any],
    runtime: Mapping[str, Any],
    *,
    tick: int,
) -> dict[str, Any]:
    """Project authoritative causal cells without mutating their source values."""

    cells = _cells(runtime)
    regions = _region_summary(cells)
    pressures = _pressure_rows(runtime, cells)
    receipt: dict[str, Any] = {
        "schema_version": "rpg_causal_runtime_projection_v1",
        "tick": int(tick),
        "runtime_hash": str(runtime.get("runtime_hash") or ""),
        "region_state": [dict(row) for row in regions],
        "pressure_state": [
            {
                "pressure_id": row["pressure_id"],
                "severity": row["severity"],
                "status": row["status"],
            }
            for row in pressures
        ],
    }
    receipt["economy"] = _project_economy(
        simulation_state, regions, pressures, tick=tick
    )
    receipt["travel"] = _project_travel(
        simulation_state, regions, pressures, tick=tick
    )
    receipt["factions"] = _project_factions(
        simulation_state, runtime, cells, pressures, tick=tick
    )
    receipt["npcs"] = _project_npcs(
        simulation_state, runtime, pressures, tick=tick
    )
    receipt["projection_hash"] = _hash(receipt)
    simulation_state["causal_runtime_projection"] = copy.deepcopy(receipt)
    return receipt


__all__ = ["project_causal_runtime_to_subsystems"]
