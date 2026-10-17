from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.location_registry import current_location_id, has_explicit_location
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _current_location_id(simulation_state: Dict[str, Any]) -> str:
    state = _safe_dict(simulation_state)
    return current_location_id(state) if has_explicit_location(state) else ""


def _provider_present(
    simulation_state: Dict[str, Any],
    provider_id: str,
    provider_name: str,
) -> bool:
    state = _safe_dict(simulation_state)
    present = (
        state.get("present_npcs")
        or state.get("present_actors")
        or state.get("actors_present")
    )
    if not isinstance(present, list):
        return True

    provider_id_l = provider_id.lower()
    provider_name_l = provider_name.lower()
    for actor in present:
        if isinstance(actor, str):
            actor_l = actor.lower()
            if actor_l in {provider_id_l, provider_name_l}:
                return True
            continue

        actor = _safe_dict(actor)
        actor_id = _safe_str(
            actor.get("id") or actor.get("actor_id") or actor.get("npc_id")
        ).lower()
        actor_name = _safe_str(
            actor.get("name") or actor.get("display_name")
        ).lower()
        if actor_id == provider_id_l or actor_name == provider_name_l:
            return True

    return False


def _ensure_offer_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    service_offer_state = _safe_dict(state.get("service_offer_state"))
    if not service_offer_state:
        service_offer_state = {}
        state["service_offer_state"] = service_offer_state

    offers = service_offer_state.get("offers")
    if not isinstance(offers, dict):
        offers = {}
        service_offer_state["offers"] = offers

    return service_offer_state


def _runtime_offer_record(
    simulation_state: Dict[str, Any],
    offer: Dict[str, Any],
) -> Dict[str, Any]:
    offer = _safe_dict(offer)
    offer_id = _safe_str(offer.get("offer_id"))
    service_offer_state = _safe_dict(_safe_dict(simulation_state).get("service_offer_state"))
    offers = _safe_dict(service_offer_state.get("offers"))
    return _safe_dict(offers.get(offer_id))


def annotate_offer_availability(
    simulation_state: Dict[str, Any],
    offer: Dict[str, Any],
    *,
    provider: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    offer = deepcopy(_safe_dict(offer))
    provider = _safe_dict(provider)
    rules = _safe_dict(offer.get("availability_rules"))
    provider_id = _safe_str(provider.get("provider_id"))
    provider_name = _safe_str(provider.get("provider_name"))

    runtime_record = _runtime_offer_record(simulation_state, offer)
    unavailable_reasons: List[str] = []

    stock_remaining = None
    if "stock" in offer or "stock_remaining" in runtime_record:
        stock_remaining = max(
            0,
            _safe_int(
                runtime_record.get("stock_remaining"),
                _safe_int(offer.get("stock"), 0),
            ),
        )
        offer["stock"] = max(0, _safe_int(offer.get("stock"), stock_remaining))
        offer["stock_remaining"] = stock_remaining
        if stock_remaining <= 0:
            unavailable_reasons.append("out_of_stock")

    required_location = _safe_str(rules.get("requires_location"))
    current_location = _current_location_id(simulation_state)
    if required_location and current_location and current_location != required_location:
        unavailable_reasons.append("wrong_location")

    if rules.get("requires_provider_present") is True:
        if not _provider_present(simulation_state, provider_id, provider_name):
            unavailable_reasons.append("provider_absent")

    if unavailable_reasons:
        offer["availability"] = "unavailable"
        offer["unavailable_reason"] = unavailable_reasons[0]
        offer["unavailable_reasons"] = unavailable_reasons
    else:
        offer["availability"] = _safe_str(offer.get("availability") or "available")
        offer["unavailable_reason"] = ""
        offer["unavailable_reasons"] = []

    return offer


def filter_available_offers(
    simulation_state: Dict[str, Any],
    offers: List[Dict[str, Any]],
    *,
    provider: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    available: List[Dict[str, Any]] = []
    for offer in _safe_list(offers):
        annotated = annotate_offer_availability(
            simulation_state,
            _safe_dict(offer),
            provider=provider,
        )
        if _safe_str(annotated.get("availability")) == "available":
            available.append(annotated)
    return available


def apply_service_stock_purchase(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    service_result = _safe_dict(service_result)
    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))
    if not selected_offer_id:
        return {}

    selected_offer = {}
    for offer in _safe_list(service_result.get("offers")):
        offer = _safe_dict(offer)
        if _safe_str(offer.get("offer_id")) == selected_offer_id:
            selected_offer = offer
            break

    if not selected_offer or "stock" not in selected_offer:
        return {}

    service_offer_state = _ensure_offer_state(simulation_state)
    offers = _safe_dict(service_offer_state.get("offers"))
    record = _safe_dict(offers.get(selected_offer_id))
    if not record:
        record = {}
        offers[selected_offer_id] = record

    stock_initial = max(0, _safe_int(selected_offer.get("stock"), 0))
    before = max(0, _safe_int(record.get("stock_remaining"), stock_initial))
    after = max(0, before - 1)

    record["stock_initial"] = max(
        0,
        _safe_int(record.get("stock_initial"), stock_initial),
    )
    record["stock_remaining"] = after
    record["updated_tick"] = int(tick or 0)
    record["provider_id"] = _safe_str(service_result.get("provider_id"))
    record["provider_name"] = _safe_str(service_result.get("provider_name"))
    record["service_kind"] = _safe_str(service_result.get("service_kind"))
    record["offer_id"] = selected_offer_id
    record["label"] = _safe_str(selected_offer.get("label") or selected_offer_id)

    return {
        "offer_id": selected_offer_id,
        "provider_id": _safe_str(service_result.get("provider_id")),
        "before": before,
        "after": after,
        "decrement": 1,
        "exhausted": after <= 0,
        "runtime_state": deepcopy(record),
        "tick": int(tick or 0),
    }
