from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _selected_offer(service_result: Dict[str, Any]) -> Dict[str, Any]:
    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))
    for offer in _safe_list(service_result.get("offers")):
        offer = _safe_dict(offer)
        if _safe_str(offer.get("offer_id")) == selected_offer_id:
            return deepcopy(offer)
    return {}


def _ensure_transaction_state(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    state = _safe_dict(simulation_state)
    transactions = state.get("transaction_history")
    if not isinstance(transactions, list):
        transactions = []
        state["transaction_history"] = transactions
    return transactions


def build_service_transaction_record(
    *,
    service_result: Dict[str, Any],
    purchase_application: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    service_result = _safe_dict(service_result)
    purchase_application = _safe_dict(purchase_application)
    purchase = _safe_dict(service_result.get("purchase"))
    offer = _selected_offer(service_result)

    provider_id = _safe_str(service_result.get("provider_id"))
    provider_name = _safe_str(service_result.get("provider_name"))
    offer_id = _safe_str(service_result.get("selected_offer_id") or offer.get("offer_id"))
    service_kind = _safe_str(service_result.get("service_kind"))
    blocked = bool(purchase_application.get("blocked") or purchase.get("blocked"))
    applied = bool(purchase_application.get("applied") or purchase.get("applied"))

    kind = "service_purchase_blocked" if blocked else "service_purchase"
    transaction_id = (
        f"txn:{tick}:{provider_id or 'provider'}:{offer_id or service_kind or 'service'}:{kind}"
    )

    return {
        "transaction_id": transaction_id,
        "kind": kind,
        "service_kind": service_kind,
        "provider_id": provider_id,
        "provider_name": provider_name,
        "offer_id": offer_id,
        "label": _safe_str(offer.get("label") or offer_id),
        "status": _safe_str(service_result.get("status")),
        "applied": applied,
        "blocked": blocked,
        "blocked_reason": _safe_str(
            purchase_application.get("blocked_reason") or purchase.get("blocked_reason")
        ),
        "currency_delta": deepcopy(
            _safe_dict(_safe_dict(purchase.get("resource_changes")).get("currency"))
        ),
        "currency_before": deepcopy(_safe_dict(purchase_application.get("currency_before"))),
        "currency_after": deepcopy(_safe_dict(purchase_application.get("currency_after"))),
        "items_added": deepcopy(_safe_list(purchase_application.get("items_added"))),
        "active_service": deepcopy(_safe_dict(purchase_application.get("active_service"))),
        "rumor_added": deepcopy(_safe_dict(purchase_application.get("rumor_added"))),
        "tick": _safe_int(tick, 0),
        "source": "deterministic_service_runtime",
    }


def append_service_transaction_record(
    simulation_state: Dict[str, Any],
    record: Dict[str, Any],
    *,
    max_records: int = 100,
) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    transactions = _ensure_transaction_state(state)
    record = deepcopy(_safe_dict(record))
    if not record:
        return {}

    transaction_id = _safe_str(record.get("transaction_id"))
    if transaction_id:
        for existing in transactions:
            if _safe_str(_safe_dict(existing).get("transaction_id")) == transaction_id:
                return deepcopy(existing)

    transactions.append(record)
    if max_records > 0 and len(transactions) > max_records:
        del transactions[:-max_records]
    return deepcopy(record)
