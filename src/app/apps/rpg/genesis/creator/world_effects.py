"""Phase 3C — Active Effects + Consequence Application.

Deterministic transformation of consequences into persistent active effects,
plus effect application/decay across simulation ticks.
"""

from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MAX_ACTIVE_EFFECTS = 50


def _cap(value: int, lo: int = 0, hi: int = 5) -> int:
    try:
        value = int(value)
    except Exception:
        value = lo
    return max(lo, min(hi, value))


def consequence_to_effect(consequence: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a consequence into a persistent effect."""
    consequence = _safe_dict(consequence)
    ctype = consequence.get("type")
    entity_id = consequence.get("entity_id")
    source_id = consequence.get("consequence_id")
    if not ctype or not entity_id or not source_id:
        return None

    if ctype == "pressure_increase":
        return {
            "effect_id": f"eff_{entity_id}_pressure_boost",
            "type": "pressure_boost",
            "target_type": "thread",
            "target_id": entity_id,
            "magnitude": 1,
            "duration": 2,
            "source_consequence_id": source_id,
        }
    if ctype == "faction_response":
        return {
            "effect_id": f"eff_{entity_id}_faction_strain",
            "type": "faction_strain_boost",
            "target_type": "faction",
            "target_id": entity_id,
            "magnitude": 1,
            "duration": 2,
            "source_consequence_id": source_id,
        }
    if ctype == "hotspot":
        return {
            "effect_id": f"eff_{entity_id}_heat_boost",
            "type": "heat_boost",
            "target_type": "location",
            "target_id": entity_id,
            "magnitude": 1,
            "duration": 3,
            "source_consequence_id": source_id,
        }
    return None


def build_effects_from_consequences(consequences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build active effects from a list of consequences."""
    effects: list[dict[str, Any]] = []
    for c in _safe_list(consequences):
        eff = consequence_to_effect(c)
        if eff:
            effects.append(eff)
    return effects[:MAX_ACTIVE_EFFECTS]


def merge_active_effects(
    current_effects: list[dict[str, Any]],
    new_effects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge by effect_id with proper stacking (duration=max, magnitude=sum)."""
    merged: dict[str, dict[str, Any]] = {}
    for eff in _safe_list(current_effects):
        eid = eff.get("effect_id")
        if eid:
            merged[eid] = dict(eff)
    for eff in _safe_list(new_effects):
        eid = eff.get("effect_id")
        if not eid:
            continue
        if eid in merged:
            existing = merged[eid]
            existing["duration"] = max(existing.get("duration", 0), eff.get("duration", 0))
            existing["magnitude"] = existing.get("magnitude", 0) + eff.get("magnitude", 0)
        else:
            merged[eid] = dict(eff)
    return sorted(merged.values(), key=lambda e: e.get("effect_id", ""))[:MAX_ACTIVE_EFFECTS]


def apply_effects_to_simulation_state(
    simulation_state: dict[str, Any],
) -> dict[str, Any]:
    """Apply effect magnitudes to thread/faction/location state."""
    state = _safe_dict(simulation_state)
    threads = _safe_dict(state.get("threads"))
    factions = _safe_dict(state.get("factions"))
    locations = _safe_dict(state.get("locations"))
    effects = _safe_list(state.get("active_effects"))

    for eff in sorted(effects, key=lambda e: e.get("effect_id", "")):
        etype = eff.get("type")
        target_type = eff.get("target_type")
        target_id = eff.get("target_id")
        magnitude = int(eff.get("magnitude", 0))

        if target_type == "thread" and target_id in threads:
            cur = _safe_dict(threads[target_id])
            pressure = int(cur.get("pressure", 0))
            if etype == "pressure_boost":
                cur["pressure"] = _cap(pressure + magnitude)
            elif etype == "pressure_dampen":
                cur["pressure"] = _cap(pressure - magnitude)
            threads[target_id] = cur

        elif target_type == "faction" and target_id in factions:
            cur = _safe_dict(factions[target_id])
            pressure = int(cur.get("pressure", 0))
            if etype == "faction_strain_boost":
                cur["pressure"] = _cap(pressure + magnitude)
            elif etype == "faction_relief":
                cur["pressure"] = _cap(pressure - magnitude)
            factions[target_id] = cur

        elif target_type == "location" and target_id in locations:
            cur = _safe_dict(locations[target_id])
            heat = int(cur.get("heat", 0))
            if etype == "heat_boost":
                cur["heat"] = _cap(heat + magnitude)
            elif etype == "heat_dampen":
                cur["heat"] = _cap(heat - magnitude)
            locations[target_id] = cur

    state["threads"] = threads
    state["factions"] = factions
    state["locations"] = locations
    return state


def decay_active_effects(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tick down durations and remove expired effects."""
    remaining: list[dict[str, Any]] = []
    for eff in _safe_list(effects):
        item = dict(eff)
        duration = int(item.get("duration", 0))
        duration -= 1
        if duration > 0:
            item["duration"] = duration
            remaining.append(item)
    return remaining[:MAX_ACTIVE_EFFECTS]


def compute_effect_diff(
    before_effects: list[dict[str, Any]],
    after_effects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute a structured diff of active effects between ticks."""
    before_map = {
        e.get("effect_id"): e for e in _safe_list(before_effects) if e.get("effect_id")
    }
    after_map = {
        e.get("effect_id"): e for e in _safe_list(after_effects) if e.get("effect_id")
    }

    added = sorted([eid for eid in after_map if eid not in before_map])
    removed = sorted([eid for eid in before_map if eid not in after_map])
    changed: list[dict[str, Any]] = []

    for eid in sorted(set(before_map.keys()) & set(after_map.keys())):
        before = before_map[eid]
        after = after_map[eid]
        fields: list[str] = []
        for key in sorted(set(before.keys()) | set(after.keys())):
            if before.get(key) != after.get(key):
                fields.append(key)
        if fields:
            changed.append({
                "id": eid,
                "fields": fields,
                "before": {k: before.get(k) for k in fields},
                "after": {k: after.get(k) for k in fields},
            })

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }