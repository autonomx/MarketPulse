"""Session-level item market action helpers.

This module bridges deterministic merchant catalogs and transaction mutation into a
session-state helper without depending on route or UI request schemas. Loadout,
routes, autoplay, and tests can use it to apply buy/sell actions consistently.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import inventory_quantity
from app.apps.rpg.session.item_market import build_merchant_catalog
from app.apps.rpg.session.item_transactions import apply_item_transaction
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MARKET_ACTION_SOURCE = "engine_item_market_session_v1"


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _player(state: dict[str, Any]) -> dict[str, Any]:
    player = _safe_dict(state.get("player"))
    state["player"] = player
    return player


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _prepend_trace(state: dict[str, Any], key: str, trace: dict[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(_safe_dict(trace))
    enriched["event"] = enriched.get("event") or "session_market_action"
    enriched["mechanics_source"] = MARKET_ACTION_SOURCE
    enriched["turn"] = int(state.get("current_turn") or state.get("turn_count") or 0)
    enriched["timestamp"] = _utc_now()
    mechanics = _mechanics(state)
    traces = _safe_list(mechanics.get(key))
    mechanics[key] = [enriched, *traces][:50]
    item_traces = _safe_list(mechanics.get("item_traces"))
    mechanics["item_traces"] = [enriched, *item_traces][:50]
    return enriched


def _market_state(state: dict[str, Any]) -> dict[str, Any]:
    market = _safe_dict(state.get("item_market"))
    state["item_market"] = market
    catalogs = _safe_dict(market.get("catalogs"))
    market["catalogs"] = catalogs
    return market


def _cached_catalog(state: dict[str, Any], profile: str, *, genre: str, level: int, reputation: int) -> tuple[str, dict[str, Any]]:
    market = _market_state(state)
    catalogs = _safe_dict(market.get("catalogs"))
    requested_profile = _text(profile, "general_store")
    cached = _safe_dict(catalogs.get(requested_profile))
    if cached.get("offers"):
        return requested_profile, deepcopy(cached)
    built = build_merchant_catalog(requested_profile, genre=genre, level=level, reputation=reputation)
    normalized_profile = _text(built.get("profile"), "general_store")
    catalog = {"profile": normalized_profile, "offers": _safe_list(built.get("offers")), "trace": _safe_dict(built.get("trace"))}
    catalogs[normalized_profile] = deepcopy(catalog)
    market["catalogs"] = catalogs
    return normalized_profile, catalog


def _session_genre(state: dict[str, Any], genre: str | None = None) -> str:
    if genre:
        return str(genre)
    metadata = _safe_dict(state.get("metadata"))
    identity = _safe_dict(state.get("character_identity"))
    return str(metadata.get("genre") or identity.get("genre") or metadata.get("campaign_template") or "classic_fantasy")


def _session_level(state: dict[str, Any], level: int | None = None) -> int:
    if level is not None:
        return max(1, int(level or 1))
    return max(1, int(_player(state).get("level") or 1))


def _session_reputation(state: dict[str, Any], reputation: int | None = None) -> int:
    if reputation is not None:
        return int(reputation or 0)
    player = _player(state)
    return int(player.get("merchant_reputation") or player.get("reputation") or 0)


def apply_session_market_action(
    state: dict[str, Any],
    item_id: str | None,
    *,
    action: str = "buy",
    quantity: int = 1,
    merchant_profile: str | None = None,
    genre: str | None = None,
    level: int | None = None,
    reputation: int | None = None,
) -> dict[str, Any]:
    """Apply a deterministic buy/sell action to a mutable session state."""

    normalized_action = _norm(action)
    if normalized_action not in {"buy", "sell"}:
        return {"ok": False, "error": "unsupported_market_action", "action": action}
    amount = max(1, int(quantity or 1))
    profile, catalog = _cached_catalog(
        state,
        _text(merchant_profile, "general_store"),
        genre=_session_genre(state, genre),
        level=_session_level(state, level),
        reputation=_session_reputation(state, reputation),
    )
    player = _player(state)
    result = apply_item_transaction(player, catalog, item_id, action=normalized_action, quantity=amount)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "market_action_failed",
            "action": normalized_action,
            "merchant_profile": profile,
            "item_id": item_id,
            "quantity": amount,
            "quote": result.get("quote"),
        }

    state["player"] = _safe_dict(result.get("player"))
    market = _market_state(state)
    catalogs = _safe_dict(market.get("catalogs"))
    catalogs[profile] = _safe_dict(result.get("catalog"))
    market["catalogs"] = catalogs
    trace = _prepend_trace(state, "market_traces", _safe_dict(result.get("trace")))
    quote = _safe_dict(result.get("quote"))
    item = _safe_dict(result.get("item"))
    item_name = _text(quote.get("name") or item.get("name") or item.get("item_id") or item_id, "item")
    verb = "Bought" if normalized_action == "buy" else "Sold"
    detail = f"{verb} {amount} {item_name}."
    return {
        "ok": True,
        "action": normalized_action,
        "merchant_profile": profile,
        "item": item,
        "quote": quote,
        "catalog": catalogs[profile],
        "detail": detail,
        "mechanics_trace": trace,
    }


def market_offer_quantities(state: dict[str, Any], merchant_profile: str = "general_store") -> dict[str, int]:
    """Return current cached offer quantities for diagnostics/tests."""

    catalogs = _safe_dict(_market_state(state).get("catalogs"))
    catalog = _safe_dict(catalogs.get(merchant_profile))
    return {
        _text(offer.get("item_id") or offer.get("name")): inventory_quantity(_safe_dict(offer))
        for offer in _safe_list(catalog.get("offers"))
    }
