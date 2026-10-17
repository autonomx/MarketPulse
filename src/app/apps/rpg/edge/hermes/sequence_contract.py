from __future__ import annotations

from typing import Any


def text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def hermes_sequence_item(raw: Any, index: int = 0) -> dict[str, Any]:
    data = dict_value(raw)
    return {
        "item_id": text_value(data.get("item_id")) or f"item-{index + 1}",
        "statement": text_value(data.get("statement")),
        "guards": text_list(data.get("guards")),
        "expected_effect": text_value(data.get("expected_effect")),
        "user_gate": data.get("user_gate") is not False,
        "status": text_value(data.get("status")) or "pending",
    }


def hermes_sequence_contract(raw: Any) -> dict[str, Any]:
    data = dict_value(raw)
    raw_items = raw_items if isinstance(raw_items := data.get("items"), list) else []
    return {
        "sequence_id": text_value(data.get("sequence_id")) or "hermes-sequence-draft",
        "objective": text_value(data.get("objective")),
        "domain": text_value(data.get("domain")) or "rpg",
        "state_owner": text_value(data.get("state_owner")) or "rpg_sim",
        "risk": text_value(data.get("risk")) or "medium",
        "user_gate": data.get("user_gate") is not False,
        "status": text_value(data.get("status")) or "draft",
        "items": [hermes_sequence_item(item, index) for index, item in enumerate(raw_items)],
    }


def hermes_sequence_contract_validate(raw: Any) -> dict[str, Any]:
    sequence = hermes_sequence_contract(raw)
    errors: list[str] = []
    if not sequence["objective"]:
        errors.append("missing_objective")
    if sequence["domain"] != "rpg":
        errors.append("unsupported_domain")
    if sequence["state_owner"] != "rpg_sim":
        errors.append("invalid_state_owner")
    if not sequence["items"]:
        errors.append("missing_items")
    for index, item in enumerate(sequence["items"]):
        if not item["statement"]:
            errors.append(f"item_{index + 1}_missing_statement")
    return {"ok": not errors, "source": "hermes_sequence_contract", "sequence": sequence, "errors": errors}
