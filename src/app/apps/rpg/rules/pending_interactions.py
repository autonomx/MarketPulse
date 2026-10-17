"""Generic persisted state machines for offers awaiting player resolution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

PENDING_INTERACTION_VERSION = "rpg_pending_interaction_v1"
_CONFIRMATION_TERMS = (
    "accept",
    "agreed",
    "confirmed",
    "deal",
    "i'll take it",
    "ill take it",
    "i will take it",
    "sounds good",
    "yes",
)


def record_service_offer(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    *,
    tick: int,
    expires_after_turns: int = 6,
) -> Dict[str, Any]:
    """Create or replace a pending service offer from registered offers."""

    state = _dict(simulation_state)
    result = _dict(service_result)
    offers = [_offer_snapshot(value) for value in _list(result.get("offers")) if isinstance(value, dict)]
    if not result.get("matched") or not offers:
        return {}
    provider_id = _text(result.get("provider_id"))
    service_kind = _text(result.get("service_kind"))
    interaction_id = f"pending:service:{provider_id}:{service_kind}"
    pending = _dict(state.get("pending_interactions"))
    record = {
        "schema_version": PENDING_INTERACTION_VERSION,
        "interaction_id": interaction_id,
        "kind": "service_offer",
        "owner_actor_id": provider_id,
        "owner_actor_name": _text(result.get("provider_name")),
        "service_kind": service_kind,
        "candidate_ids": [_text(offer.get("offer_id")) for offer in offers],
        "offers": offers,
        "created_tick": int(tick or 0),
        "expires_at_turn": int(tick or 0) + max(1, int(expires_after_turns)),
        "status": "pending",
        "source": "deterministic_service_registry",
    }
    pending[interaction_id] = record
    state["pending_interactions"] = pending
    return deepcopy(record)


def select_pending_service_offer(
    simulation_state: Dict[str, Any],
    *,
    provider_id: str,
    service_kind: str,
    player_input: str,
    structured_offer_id: str = "",
    tick: int = 0,
) -> Dict[str, Any]:
    """Resolve a pending service candidate without applying its effects."""

    record = _pending_service_record(
        simulation_state,
        provider_id=provider_id,
        service_kind=service_kind,
        tick=tick,
    )
    if not record:
        return {}
    offers = [_dict(value) for value in _list(record.get("offers"))]
    if structured_offer_id:
        return next(
            (deepcopy(offer) for offer in offers if _text(offer.get("offer_id")) == structured_offer_id),
            {},
        )
    text = _text(player_input).casefold()
    direct = [offer for offer in offers if _offer_referenced(offer, text)]
    if len(direct) == 1:
        return deepcopy(direct[0])
    if len(offers) == 1 and any(term in text for term in _CONFIRMATION_TERMS):
        return deepcopy(offers[0])
    return {}


def close_pending_service_offer(
    simulation_state: Dict[str, Any],
    *,
    provider_id: str,
    service_kind: str,
    selected_offer_id: str,
    tick: int,
) -> Dict[str, Any]:
    """Mark a pending interaction resolved after an authoritative purchase."""

    record = _pending_service_record(
        simulation_state,
        provider_id=provider_id,
        service_kind=service_kind,
        tick=tick,
        include_expired=True,
    )
    if not record:
        return {}
    record["status"] = "resolved"
    record["resolved_offer_id"] = _text(selected_offer_id)
    record["resolved_tick"] = int(tick or 0)
    return deepcopy(record)


def _pending_service_record(
    simulation_state: Dict[str, Any],
    *,
    provider_id: str,
    service_kind: str,
    tick: int,
    include_expired: bool = False,
) -> Dict[str, Any]:
    pending = _dict(_dict(simulation_state).get("pending_interactions"))
    interaction_id = f"pending:service:{provider_id}:{service_kind}"
    record = _dict(pending.get(interaction_id))
    if not record or record.get("status") != "pending":
        return {}
    if not include_expired and int(record.get("expires_at_turn") or 0) < int(tick or 0):
        record["status"] = "expired"
        record["expired_tick"] = int(tick or 0)
        return {}
    return record


def _offer_snapshot(value: Dict[str, Any]) -> Dict[str, Any]:
    offer = _dict(value)
    return {
        key: deepcopy(offer.get(key))
        for key in (
            "offer_id",
            "service_kind",
            "provider_id",
            "provider_name",
            "label",
            "description",
            "price",
            "effects",
        )
        if offer.get(key) is not None
    }


def _offer_referenced(offer: Dict[str, Any], text: str) -> bool:
    terms = {
        _text(offer.get("offer_id")).casefold().replace("_", " "),
        _text(offer.get("label")).casefold(),
    }
    return any(term and term in text for term in terms)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


__all__ = [
    "PENDING_INTERACTION_VERSION",
    "close_pending_service_offer",
    "record_service_offer",
    "select_pending_service_offer",
]
