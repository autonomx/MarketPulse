from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.rules.economy.currency import normalize_currency
from app.apps.rpg.rules.economy.pricing import resolve_registry_price
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def enrich_action_with_registry_price(action: Dict[str, Any]) -> Dict[str, Any]:
    action = dict(_safe_dict(action))

    # If authoritative price already exists, preserve it.
    if _safe_dict(action.get("cost")) or _safe_dict(action.get("currency_cost")):
        return action

    price = normalize_currency(resolve_registry_price(action))
    if any(int(price.get(k, 0) or 0) > 0 for k in ("gold", "silver", "copper")):
        action["currency_cost"] = price

    return action


def build_transaction_metadata(action: Dict[str, Any]) -> Dict[str, Any]:
    action = _safe_dict(action)
    action_type = _safe_str(action.get("action_type") or action.get("type")).lower()

    metadata: Dict[str, Any] = {
        "transaction_kind": "",
        "price_source": "",
        "provider_id": _safe_str(action.get("provider_id")),
        "provider_name": _safe_str(action.get("provider_name")),
    }

    if _safe_dict(action.get("currency_cost")) or _safe_dict(action.get("cost")):
        metadata["price_source"] = "registry_or_authoritative"

    if action_type in {"buy", "purchase", "shop_purchase", "trade"}:
        metadata["transaction_kind"] = "item_purchase"
    elif action_type in {"rent_room", "rent_bed", "use_service", "pay"}:
        metadata["transaction_kind"] = "service_purchase"

    return metadata
