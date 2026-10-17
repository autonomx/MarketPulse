"""Deterministic setup effects for RPG new-game creation.

These helpers translate wizard difficulty, economy, world, combat, companion,
and permadeath options into saved session state. They intentionally avoid LLM or
runtime side effects so new-game creation remains synchronous and replayable.
"""
from __future__ import annotations

from typing import Any

CoinPurse = dict[str, int]

DIFFICULTY_EFFECTS: dict[str, dict[str, Any]] = {
    "story": {
        "label": "Story",
        "risk_label": "low",
        "encounter_pressure": "low",
        "currency_delta": {"gold": 4, "silver": 10, "copper": 0},
        "objective_pressure": "lenient",
    },
    "normal": {
        "label": "Normal",
        "risk_label": "standard",
        "encounter_pressure": "standard",
        "currency_delta": {"gold": 0, "silver": 0, "copper": 0},
        "objective_pressure": "standard",
    },
    "harsh": {
        "label": "Harsh",
        "risk_label": "high",
        "encounter_pressure": "high",
        "currency_delta": {"gold": -3, "silver": -10, "copper": 0},
        "objective_pressure": "strict",
    },
}

ECONOMY_EFFECTS: dict[str, dict[str, Any]] = {
    "relaxed": {
        "label": "Relaxed",
        "price_multiplier": 0.85,
        "service_availability": "friendly",
        "currency_delta": {"gold": 2, "silver": 15, "copper": 0},
    },
    "normal": {
        "label": "Normal",
        "price_multiplier": 1.0,
        "service_availability": "standard",
        "currency_delta": {"gold": 0, "silver": 0, "copper": 0},
    },
    "strict": {
        "label": "Strict",
        "price_multiplier": 1.25,
        "service_availability": "scarce",
        "currency_delta": {"gold": -1, "silver": -10, "copper": 0},
    },
}

WORLD_ACTIVITY_EFFECTS: dict[str, dict[str, Any]] = {
    "quiet": {
        "label": "Quiet",
        "activity_density": "low",
        "living_world": False,
        "local_activity": ["Few travelers are moving yet."],
        "timeline": {"title": "Quiet start", "detail": "The local scene is calm, with fewer autonomous events expected early.", "kind": "world_activity"},
    },
    "standard": {
        "label": "Standard",
        "activity_density": "standard",
        "living_world": True,
        "local_activity": ["Local routines continue around the opening scene."],
        "timeline": {"title": "Local routines active", "detail": "Merchants, guards, and tavern regulars continue their ordinary business.", "kind": "world_activity"},
    },
    "living_world": {
        "label": "Living world",
        "activity_density": "high",
        "living_world": True,
        "local_activity": ["NPC schedules are busy.", "Rumors and faction pressure can surface early."],
        "timeline": {"title": "Living world in motion", "detail": "Nearby NPCs are already moving, talking, and reacting to local pressure.", "kind": "world_activity"},
    },
}

COMBAT_LETHALITY_EFFECTS: dict[str, dict[str, Any]] = {
    "safe": {
        "label": "Safe",
        "safety": "guarded",
        "encounter_pressure": "low",
        "defeat_policy": "fail_forward",
        "defeat_consequence": "injury_or_setback",
    },
    "normal": {
        "label": "Normal",
        "safety": "standard",
        "encounter_pressure": "standard",
        "defeat_policy": "standard",
        "defeat_consequence": "injury_loss_or_escape",
    },
    "deadly": {
        "label": "Deadly",
        "safety": "deadly",
        "encounter_pressure": "high",
        "defeat_policy": "severe",
        "defeat_consequence": "severe_injury_or_death",
    },
}


def _effect(table: dict[str, dict[str, Any]], key: str, fallback: str) -> dict[str, Any]:
    return dict(table.get(key) or table[fallback])


def _apply_currency_delta(currency: CoinPurse, delta: dict[str, int]) -> CoinPurse:
    result = {"gold": int(currency.get("gold") or 0), "silver": int(currency.get("silver") or 0), "copper": int(currency.get("copper") or 0)}
    for coin, amount in delta.items():
        result[coin] = max(0, int(result.get(coin) or 0) + int(amount))
    return result


def _dedupe_actions(actions: list[str], limit: int = 6) -> list[str]:
    return list(dict.fromkeys(action for action in actions if action))[:limit]


def build_new_game_setup_effects(request: Any, loadout: dict[str, Any], story_setup: dict[str, Any], location: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic setup effects and adjusted state slices.

    The request is typed as ``Any`` to keep this helper independent from the
    Pydantic request model and avoid import cycles.
    """
    difficulty = _effect(DIFFICULTY_EFFECTS, str(getattr(request, "difficulty", "normal")), "normal")
    economy = _effect(ECONOMY_EFFECTS, str(getattr(request, "economy_pressure", "normal")), "normal")
    world_activity = _effect(WORLD_ACTIVITY_EFFECTS, str(getattr(request, "world_activity", "standard")), "standard")
    combat = _effect(COMBAT_LETHALITY_EFFECTS, str(getattr(request, "combat_lethality", "normal")), "normal")

    adjusted_loadout = dict(loadout)
    currency = dict(loadout.get("currency") or {})
    currency = _apply_currency_delta(currency, difficulty["currency_delta"])
    currency = _apply_currency_delta(currency, economy["currency_delta"])
    adjusted_loadout["currency"] = currency

    companions_enabled = bool(getattr(request, "companions_enabled", True))
    quick_actions = list(story_setup.get("quick_actions") or [])
    if companions_enabled:
        quick_actions.append("Look for a companion")
    else:
        quick_actions = [action for action in quick_actions if "companion" not in action.lower() and "party" not in action.lower()]
    quick_actions = _dedupe_actions(quick_actions)

    permadeath = bool(getattr(request, "permadeath", False))
    defeat_rules = {
        "permadeath": permadeath,
        "defeat_policy": "permadeath_enabled" if permadeath else combat["defeat_policy"],
        "defeat_consequence": "character_death_allowed" if permadeath else combat["defeat_consequence"],
    }
    companion_effect = {
        "enabled": companions_enabled,
        "recruitment_affordance": "available" if companions_enabled else "disabled_by_setup",
    }
    economy_state = {
        "pressure": str(getattr(request, "economy_pressure", "normal")),
        "price_multiplier": economy["price_multiplier"],
        "service_availability": economy["service_availability"],
    }
    world_state = {
        "mode": str(getattr(request, "world_activity", "standard")),
        "label": world_activity["label"],
        "activity_density": world_activity["activity_density"],
        "living_world": world_activity["living_world"],
        "local_activity": list(world_activity["local_activity"]),
    }
    combat_state = {
        "lethality": str(getattr(request, "combat_lethality", "normal")),
        "label": combat["label"],
        "safety": combat["safety"],
        "encounter_pressure": combat["encounter_pressure"],
    }
    setup_effects = {
        "difficulty": {key: value for key, value in difficulty.items() if key != "currency_delta"},
        "economy": economy_state,
        "world_activity": world_state,
        "combat_lethality": combat_state,
        "companions": companion_effect,
        "permadeath": defeat_rules,
    }
    time_label = str(location.get("time_label") or "Day 1 • 08:00")
    timeline = [{"turn": 0, "time": time_label, **dict(world_activity["timeline"])}]
    return {
        "loadout": adjusted_loadout,
        "quick_actions": quick_actions,
        "timeline": timeline,
        "setup_effects": setup_effects,
        "defeat_rules": defeat_rules,
        "economy": economy_state,
        "world_activity": world_state,
        "combat": combat_state,
        "encounter_pressure": difficulty["encounter_pressure"],
        "living_world": world_state,
    }
