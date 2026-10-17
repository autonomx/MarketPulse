"""Route-free dispatcher for deterministic RPG item session actions.

The lower-level bridges remain the source of truth for each item subsystem. This
module gives autoplay, tests, and future routes one compact entry point without
adding a route schema or presentation dependency.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_combat_session import apply_session_item_combat
from app.apps.rpg.session.item_effect_session import apply_item_effect_for_session, available_item_effects_for_session
from app.apps.rpg.session.item_market_session import apply_session_market_action, market_offer_quantities
from app.apps.rpg.session.item_pickup_session import apply_session_scene_item_pickup, available_scene_pickups_for_session
from app.apps.rpg.session.item_report_session import build_item_report_for_session, record_item_report_for_session
from app.apps.rpg.session.recipe_discovery_session import apply_recipe_discovery_for_session
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

ITEM_SESSION_ACTIONS_SOURCE = "engine_item_session_actions_v1"


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _as_int(value: Any, fallback: int = 1) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def available_item_session_actions(state: dict[str, Any], *, merchant_profile: str = "general_store") -> dict[str, Any]:
    """Return a deterministic snapshot of available session-level item actions."""

    state = _safe_dict(state)
    pickups = available_scene_pickups_for_session(state)
    effects = available_item_effects_for_session(state)
    market_quantities = market_offer_quantities(state, merchant_profile)
    actions = {
        "pickup": bool(pickups),
        "effect": bool(effects),
        "market": bool(market_quantities),
        "recipe_discovery": True,
        "report": True,
        "combat": bool(state.get("player") and _safe_dict(state.get("combat") or {}).get("participants")),
    }
    return {
        "actions": actions,
        "pickups": pickups,
        "effects": effects,
        "market_offer_quantities": market_quantities,
        "mechanics_source": ITEM_SESSION_ACTIONS_SOURCE,
    }


def apply_item_session_action(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """Apply a deterministic item action to mutable session state.

    Supported action families deliberately mirror the route-free bridges:
    ``buy``/``sell``/``market``, ``pickup``, ``effect``/``use_effect``,
    ``combat``/``attack``, ``recipe_discovery``/``discover_recipes``, and
    ``report``.
    """

    state = _safe_dict(state)
    request = _safe_dict(action)
    kind = _norm(request.get("action") or request.get("kind"))
    source = _text(request.get("source"), "item_session_action")

    if kind in {"buy", "sell", "market"}:
        market_action = _norm(request.get("market_action") or (kind if kind in {"buy", "sell"} else "buy"))
        result = apply_session_market_action(
            state,
            _text(request.get("item_id") or request.get("item_name")),
            action=market_action,
            quantity=max(1, _as_int(request.get("quantity"), 1)),
            merchant_profile=_text(request.get("merchant_profile"), "general_store"),
        )
    elif kind in {"pickup", "collect", "take"}:
        result = apply_session_scene_item_pickup(
            state,
            _text(request.get("node_id") or request.get("item_node_id")),
            seed=request.get("seed"),
            source=source,
        )
    elif kind in {"effect", "use_effect", "activate"}:
        result = apply_item_effect_for_session(
            state,
            _text(request.get("item_name") or request.get("item_id")),
            effect_id=request.get("effect_id"),
            source=source,
        )
    elif kind in {"combat", "attack", "item_combat"}:
        source_item = raw_source_item if isinstance(raw_source_item := request.get("source_item"), dict) else None
        result = apply_session_item_combat(
            state,
            attacker_id=request.get("attacker_id"),
            defender_id=request.get("defender_id"),
            source_item=source_item,
            preferred_slot=_text(request.get("preferred_slot"), "Weapon"),
        )
    elif kind in {"recipe_discovery", "discover_recipes", "recipes"}:
        result = apply_recipe_discovery_for_session(
            state,
            source=source,
            record_empty=bool(request.get("record_empty")),
        )
    elif kind in {"report", "item_report"}:
        if request.get("record") is False:
            result = build_item_report_for_session(
                state,
                station=request.get("station"),
                genre=_text(request.get("genre"), "classic_fantasy"),
                source=source,
            )
        else:
            result = record_item_report_for_session(
                state,
                station=request.get("station"),
                genre=_text(request.get("genre"), "classic_fantasy"),
                source=source,
            )
    else:
        return {"ok": False, "error": "unsupported_item_session_action", "action": request.get("action") or request.get("kind")}

    response = deepcopy(_safe_dict(result))
    response["session_action"] = kind
    response["mechanics_source"] = ITEM_SESSION_ACTIONS_SOURCE
    return response
