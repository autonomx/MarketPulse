from __future__ import annotations

"""Runtime-promotion payload and deterministic survival helpers.

N122.2 introduced live runtime-shaped climate/survival payloads.  N123.1 adds
actual deterministic mutation helpers so the same payload reflects persisted
turn-by-turn state instead of report-only projection.
"""

from typing import Any, Dict, List, cast
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clamp_int(value: Any, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, _safe_int(value, minimum)))


_MINUTES_PER_TURN = 15
_SEASONS = ("spring", "summer", "autumn", "winter")
_WEATHER_BY_SEASON = {
    "spring": ("cool rain", "mist", "bright overcast", "mild sun"),
    "summer": ("warm sun", "dry wind", "clear sky", "humid haze"),
    "autumn": ("cold drizzle", "leaf wind", "low cloud", "pale sun"),
    "winter": ("snow flurries", "hard frost", "grey sleet", "clear cold"),
}
_SURVIVAL_DELTAS_PER_TURN = {
    "hunger": 1,
    "thirst": 2,
    "fatigue": 1,
}


def _derive_time_weather(tick: int, existing: Dict[str, Any] | None = None) -> Dict[str, Any]:
    existing = _safe_dict(existing)
    total_minutes = max(0, tick) * _MINUTES_PER_TURN
    day = total_minutes // (24 * 60) + 1
    minute_of_day = total_minutes % (24 * 60)
    hour = minute_of_day // 60
    minute = minute_of_day % 60

    season = _safe_str(existing.get("season"))
    if not season:
        season = _SEASONS[((day - 1) // 30) % len(_SEASONS)]
    phase = _safe_str(existing.get("phase"))
    if not phase:
        if 5 <= hour < 8:
            phase = "dawn"
        elif 8 <= hour < 17:
            phase = "day"
        elif 17 <= hour < 21:
            phase = "dusk"
        else:
            phase = "night"

    weather = _safe_str(existing.get("weather"))
    if not weather:
        options = _WEATHER_BY_SEASON.get(season, _WEATHER_BY_SEASON["spring"])
        weather = options[(day + tick) % len(options)]

    temperature = existing.get("temperature_c")
    if temperature is None:
        base_by_season = {"spring": 11, "summer": 23, "autumn": 9, "winter": -2}
        daily_wave = ((hour - 6) % 24) // 6
        temperature = base_by_season.get(season, 10) + int(daily_wave)

    return {
        "day": day,
        "hour": hour,
        "minute": minute,
        "phase": phase,
        "season": season,
        "weather": weather,
        "temperature_c": temperature,
    }


def _survival_warnings(hunger: int, thirst: int, fatigue: int, temperature: int) -> List[str]:
    warnings: List[str] = []
    if hunger >= 70:
        warnings.append("hunger_high")
    if thirst >= 70:
        warnings.append("thirst_high")
    if fatigue >= 70:
        warnings.append("fatigue_high")
    if temperature <= 0:
        warnings.append("freezing_conditions")
    elif temperature >= 30:
        warnings.append("heat_risk")
    return warnings


def _survival_effects(warnings: List[str]) -> List[Dict[str, Any]]:
    effects: List[Dict[str, Any]] = []
    if "hunger_high" in warnings:
        effects.append({"effect_id": "survival_hunger_high", "kind": "survival_pressure", "stat": "hunger", "severity": "warning"})
    if "thirst_high" in warnings:
        effects.append({"effect_id": "survival_thirst_high", "kind": "survival_pressure", "stat": "thirst", "severity": "warning"})
    if "fatigue_high" in warnings:
        effects.append({"effect_id": "survival_fatigue_high", "kind": "survival_pressure", "stat": "fatigue", "severity": "warning"})
    if "freezing_conditions" in warnings:
        effects.append({"effect_id": "climate_freezing_conditions", "kind": "climate_pressure", "stat": "temperature", "severity": "warning"})
    if "heat_risk" in warnings:
        effects.append({"effect_id": "climate_heat_risk", "kind": "climate_pressure", "stat": "temperature", "severity": "warning"})
    return effects


def apply_climate_survival_turn_effects(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Mutate live state with one deterministic climate/survival turn tick.

    This is intentionally LLM-free and bounded.  The authoritative turn loop can
    call it once per turn; tests can also call it directly.  It persists state in
    both ``simulation_state['climate_survival']`` and
    ``simulation_state['player_state']['resources']`` so N122.2 payloads and
    older player-resource surfaces stay in sync.
    """

    state = simulation_state if isinstance(simulation_state, dict) else {}
    runtime_state = _safe_dict(runtime_state)
    player_state = raw_player_state if isinstance(raw_player_state := state.get("player_state"), dict) else {}
    state["player_state"] = player_state
    resources = raw_resources if isinstance(raw_resources := player_state.get("resources"), dict) else {}
    player_state["resources"] = resources

    existing = raw_climate_survival if isinstance(raw_climate_survival := state.get("climate_survival"), dict) else {}
    existing_survival = _safe_dict(existing.get("survival"))

    base_tick = max(
        _safe_int(existing.get("tick"), 0),
        _safe_int(runtime_state.get("tick"), 0),
        _safe_int(state.get("tick"), 0),
    )
    next_tick = base_tick + 1
    before = {
        "hunger": _clamp_int(existing_survival.get("hunger", resources.get("hunger", 0))),
        "thirst": _clamp_int(existing_survival.get("thirst", resources.get("thirst", 0))),
        "fatigue": _clamp_int(existing_survival.get("fatigue", resources.get("fatigue", 0))),
    }
    after = {
        key: _clamp_int(before[key] + delta)
        for key, delta in _SURVIVAL_DELTAS_PER_TURN.items()
    }
    action_count = _safe_int(existing_survival.get("action_count"), _safe_int(resources.get("action_count"), 0)) + 1

    time_weather = _derive_time_weather(next_tick, existing.get("weather") or existing)
    warnings = _survival_warnings(
        after["hunger"],
        after["thirst"],
        after["fatigue"],
        _safe_int(time_weather.get("temperature_c"), 10),
    )
    effects = _survival_effects(warnings)

    climate_survival = {
        "format_version": "n1231_climate_survival_state_v1",
        "runtime_enforced": True,
        "source": "deterministic_authoritative_turn_tick",
        "tick": next_tick,
        "minutes_per_turn": _MINUTES_PER_TURN,
        "time": {
            "day": time_weather["day"],
            "hour": time_weather["hour"],
            "minute": time_weather["minute"],
            "phase": time_weather["phase"],
            "season": time_weather["season"],
        },
        "weather": {
            "season": time_weather["season"],
            "weather": time_weather["weather"],
            "temperature_c": time_weather["temperature_c"],
        },
        "survival": {
            "hunger": after["hunger"],
            "thirst": after["thirst"],
            "fatigue": after["fatigue"],
            "action_count": action_count,
            "warnings": warnings,
        },
    }
    state["climate_survival"] = climate_survival
    resources.update({
        "hunger": after["hunger"],
        "thirst": after["thirst"],
        "fatigue": after["fatigue"],
        "action_count": action_count,
    })

    resource_changes = {
        "source": "n1231_climate_survival_tick",
        "hunger_delta": after["hunger"] - before["hunger"],
        "thirst_delta": after["thirst"] - before["thirst"],
        "fatigue_delta": after["fatigue"] - before["fatigue"],
        "before": before,
        "after": after,
        "warnings": warnings,
    }
    effect_result = {
        "source": "n1231_climate_survival_tick",
        "applied": bool(effects),
        "effects": effects,
        "warnings": warnings,
    }
    return {
        "ok": True,
        "simulation_state": state,
        "climate_survival": climate_survival,
        "resource_changes": resource_changes,
        "effect_result": effect_result,
    }


def build_climate_survival_runtime_payload(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return live deterministic climate/survival state for the current turn.

    Existing persisted state wins when present, so N123.1 authoritative mutation
    is reflected directly by N122.2 payload consumers.
    """

    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    player_state = _safe_dict(simulation_state.get("player_state"))

    existing = _safe_dict(
        runtime_state.get("climate_survival")
        or runtime_state.get("climate_survival_runtime")
        or simulation_state.get("climate_survival")
    )

    tick = _safe_int(
        existing.get("tick"),
        _safe_int(runtime_state.get("tick"), _safe_int(simulation_state.get("tick"), 0)),
    )
    derived = _derive_time_weather(tick, existing.get("weather") or existing)
    day = _safe_int(_safe_dict(existing.get("time")).get("day"), derived["day"])
    hour = _safe_int(_safe_dict(existing.get("time")).get("hour"), derived["hour"])
    minute = _safe_int(_safe_dict(existing.get("time")).get("minute"), derived["minute"])
    phase = _safe_str(_safe_dict(existing.get("time")).get("phase") or existing.get("phase") or derived["phase"])
    season = _safe_str(_safe_dict(existing.get("weather")).get("season") or _safe_dict(existing.get("time")).get("season") or existing.get("season") or derived["season"])
    weather = _safe_str(_safe_dict(existing.get("weather")).get("weather") or existing.get("weather") or derived["weather"])
    temperature = _safe_dict(existing.get("weather")).get("temperature_c")
    if temperature is None:
        temperature = existing.get("temperature_c", derived["temperature_c"])

    survival = _safe_dict(existing.get("survival"))
    resources = _safe_dict(player_state.get("resources"))
    hunger = _safe_int(survival.get("hunger"), _safe_int(resources.get("hunger"), min(100, tick * 2)))
    thirst = _safe_int(survival.get("thirst"), _safe_int(resources.get("thirst"), min(100, tick * 3)))
    fatigue = _safe_int(survival.get("fatigue"), _safe_int(resources.get("fatigue"), min(100, tick * 2)))
    action_count = _safe_int(survival.get("action_count"), _safe_int(resources.get("action_count"), tick))

    warnings = _safe_list(survival.get("warnings")) or _survival_warnings(hunger, thirst, fatigue, _safe_int(temperature, 10))

    recommended = []
    if thirst >= 50:
        recommended.append("drink water")
    if hunger >= 50:
        recommended.append("eat food")
    if fatigue >= 60:
        recommended.append("rest")
    if phase == "night":
        recommended.append("find lodging or camp")

    time_label = f"Day {day}, {hour:02d}:{minute:02d} {phase}"
    weather_label = f"{season} / {weather} / {temperature}°C"
    needs_label = f"Hunger {hunger} · Thirst {thirst} · Fatigue {fatigue}"

    return {
        "format_version": "n1222_climate_survival_runtime_payload_v1",
        "ok": True,
        "runtime_promoted": True,
        "runtime_enforced": bool(existing.get("runtime_enforced")),
        "source": existing.get("source") or "deterministic_live_session_runtime",
        "tick": tick,
        "minutes_per_turn": _MINUTES_PER_TURN,
        "time": {
            "day": day,
            "hour": hour,
            "minute": minute,
            "phase": phase,
            "season": season,
            "label": time_label,
        },
        "weather": {
            "season": season,
            "weather": weather,
            "temperature_c": temperature,
            "label": weather_label,
        },
        "survival": {
            "hunger": hunger,
            "thirst": thirst,
            "fatigue": fatigue,
            "action_count": action_count,
            "warnings": warnings,
            "next_recommended_actions": recommended[:4],
            "label": needs_label,
        },
        "display": {
            "title": "Climate + Survival",
            "time_label": time_label,
            "weather_label": weather_label,
            "needs_label": needs_label,
            "warnings_label": ", ".join(warnings) if warnings else "Stable",
        },
        "turn_contract_keys": ["climate_survival", "runtime_state.climate_survival", "resource_changes", "effect_result"],
        "frontend_event": "rpg:climate-survival-update",
    }


def build_runtime_promotion_summary(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Classify N97-N108/N122 systems by live runtime evidence."""

    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    climate = build_climate_survival_runtime_payload(simulation_state, runtime_state)
    combat_state = _safe_dict(runtime_state.get("combat_state") or simulation_state.get("combat_state"))
    actor_activities = _safe_dict(runtime_state.get("actor_activities"))
    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory = _safe_dict(player_state.get("inventory_state"))

    systems = [
        {
            "name": "N97-N99 memory_aging_world_state_compression",
            "status": "runtime_promoted" if (
                "world_rumors" in runtime_state
                or "world_pressure" in runtime_state
                or "world_consequences" in runtime_state
                or "memory_state" in simulation_state
            ) else "partial_runtime",
            "source": "live simulation_state/runtime_state",
            "runtime_state_keys": [
                key for key in (
                    "world_rumors",
                    "world_pressure",
                    "location_conditions",
                    "world_consequences",
                    "recent_world_event_rows",
                ) if key in runtime_state
            ],
            "evidence": {
                "world_rumor_count": len(_safe_list(runtime_state.get("world_rumors"))),
                "world_pressure_count": len(_safe_list(runtime_state.get("world_pressure"))),
                "world_consequence_count": len(_safe_list(runtime_state.get("world_consequences"))),
                "recent_world_event_count": len(_safe_list(runtime_state.get("recent_world_event_rows"))),
            },
        },
        {
            "name": "N100-N102 npc_goal_agency_schedules",
            "status": "runtime_promoted" if actor_activities else "partial_runtime",
            "source": "live runtime_state.actor_activities + npc presence",
            "runtime_state_keys": [key for key in ("actor_activities", "npcs") if key in runtime_state],
            "evidence": {
                "actor_activity_count": len(actor_activities),
                "npc_count": len(_safe_list(runtime_state.get("npcs"))),
                "nearby_npc_count": len(_safe_list(player_state.get("nearby_npc_ids"))),
            },
        },
        {
            "name": "N103-N105 economy_pressure_resource_sinks",
            "status": "runtime_promoted" if (
                inventory
                or runtime_state.get("transaction_menus")
                or simulation_state.get("economy_state")
            ) else "partial_runtime",
            "source": "live player inventory/resources + transaction menus",
            "runtime_state_keys": [key for key in ("transaction_menus", "economy_state") if key in runtime_state],
            "evidence": {
                "currency_keys": sorted(_safe_dict(inventory.get("currency")).keys()),
                "item_count": len(_safe_list(inventory.get("items"))),
                "transaction_menu_count": len(_safe_list(runtime_state.get("transaction_menus"))),
            },
        },
        {
            "name": "N106-N108 combat_lifecycle_expansion",
            "status": "runtime_promoted" if combat_state else "partial_runtime",
            "source": "live runtime_state.combat_state",
            "runtime_state_keys": ["combat_state"] if combat_state else [],
            "evidence": {
                "has_combat_state": bool(combat_state),
                "participant_count": len(_safe_dict(combat_state.get("participants"))),
                "round": combat_state.get("round"),
                "turn_index": combat_state.get("turn_index"),
            },
        },
        {
            "name": "N122 climate_survival_runtime_payload",
            "status": "runtime_enforced" if climate.get("runtime_enforced") else "runtime_promoted",
            "source": "live deterministic climate/survival runtime payload",
            "runtime_state_keys": ["climate_survival"],
            "evidence": {
                "time": climate.get("time"),
                "weather": climate.get("weather"),
                "survival": climate.get("survival"),
            },
        },
    ]
    promoted_count = sum(1 for item in cast(list[dict[str, Any]], systems) if item.get("status") in {"runtime_promoted", "runtime_enforced"})
    return {
        "format_version": "n1222_runtime_promotion_summary_v1",
        "ok": promoted_count == len(systems),
        "advisory_only": True,
        "system_count": len(systems),
        "runtime_promoted_count": promoted_count,
        "runtime_enforced_count": sum(1 for item in cast(list[dict[str, Any]], systems) if item.get("status") == "runtime_enforced"),
        "partial_or_missing_count": len(systems) - promoted_count,
        "systems": systems,
        "policy": "Runtime promotion requires sourceable deterministic live session/turn-contract evidence, not report-only artifacts.",
    }


def build_runtime_promotion_panel_payload(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    climate = build_climate_survival_runtime_payload(simulation_state, runtime_state)
    audit = build_runtime_promotion_summary(simulation_state, runtime_state)
    return {
        "format_version": "n1222_runtime_promotion_panel_v1",
        "climate_survival": climate,
        "runtime_promotion": audit,
        "cards": [
            {
                "id": "climate_survival",
                "title": climate["display"]["title"],
                "rows": [
                    {"label": "Time", "value": climate["display"]["time_label"]},
                    {"label": "Weather", "value": climate["display"]["weather_label"]},
                    {"label": "Needs", "value": climate["display"]["needs_label"]},
                    {"label": "Warnings", "value": climate["display"]["warnings_label"]},
                ],
            },
            {
                "id": "runtime_promotion",
                "title": "Runtime Promotion",
                "rows": [
                    {"label": "Promoted", "value": f"{audit['runtime_promoted_count']} / {audit['system_count']}"},
                    {"label": "Enforced", "value": str(audit.get("runtime_enforced_count", 0))},
                    {"label": "Partial/Missing", "value": str(audit["partial_or_missing_count"])},
                ],
            },
        ],
    }


def attach_runtime_promotion_payloads(
    payload: Dict[str, Any],
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = _safe_dict(payload)
    runtime_state = _safe_dict(runtime_state)
    climate = build_climate_survival_runtime_payload(simulation_state, runtime_state)
    audit = build_runtime_promotion_summary(simulation_state, runtime_state)
    panel = build_runtime_promotion_panel_payload(simulation_state, runtime_state)

    payload["climate_survival"] = climate
    payload["climate_survival_runtime_payload"] = climate
    payload["runtime_promotion_summary"] = audit
    payload["runtime_promotion_panel"] = panel

    live_runtime_state = _safe_dict(payload.get("runtime_state"))
    live_runtime_state["climate_survival"] = climate
    live_runtime_state["runtime_promotion_summary"] = audit
    payload["runtime_state"] = live_runtime_state

    presentation = _safe_dict(payload.get("presentation"))
    presentation["climate_survival"] = climate
    presentation["runtime_promotion_panel"] = panel
    payload["presentation"] = presentation

    return payload
