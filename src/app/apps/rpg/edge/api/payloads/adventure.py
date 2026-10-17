"""Small RPG adventure-builder compatibility surface for the gateway."""
from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def list_adventure_templates_payload() -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_preview_service import get_templates

    return {"success": True, "templates": get_templates()}


def validate_adventure_payload(data: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_preview_service import validate_setup

    result = validate_setup(_safe_dict(data))
    validation = _safe_dict(result.get("validation"))
    return {
        "ok": not bool(validation.get("blocking")),
        "validation": validation,
        "errors": [
            issue
            for issue in _safe_list(validation.get("issues"))
            if isinstance(issue, dict) and issue.get("severity") == "error"
        ],
        "warnings": _safe_list(result.get("warnings")),
        "notices": _safe_list(result.get("notices")),
        "semantic_scores": _safe_dict(result.get("semantic_scores")),
    }


def preview_adventure_payload(data: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_preview_service import build_adventure_preview, preview_setup

    payload = _safe_dict(data)
    setup = raw_setup if isinstance(raw_setup := payload.get("setup"), dict) else payload
    result = preview_setup(_safe_dict(setup))
    if "adventure_preview" not in result:
        result["adventure_preview"] = _safe_dict(build_adventure_preview(_safe_dict(setup)))
    return result


def inspect_adventure_world_payload(data: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_world_service import inspect_world

    payload = _safe_dict(data)
    setup = _safe_dict(payload.get("setup"))
    return inspect_world(setup)


def inspect_adventure_world_snapshot_payload(data: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_world_service import inspect_world_snapshot

    payload = _safe_dict(data)
    setup = _safe_dict(payload.get("setup"))
    label = payload.get("label")
    return inspect_world_snapshot(setup, label=str(label) if label is not None else None)


def compare_adventure_world_payload(data: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_world_service import compare_world

    payload = _safe_dict(data)
    return compare_world(
        _safe_dict(payload.get("before_setup")),
        _safe_dict(payload.get("after_setup")),
    )


def compare_adventure_entity_payload(data: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_world_service import compare_world_entity

    payload = _safe_dict(data)
    entity_id = str(payload.get("entity_id") or "")
    if not entity_id:
        return {"success": False, "error": "Missing entity_id"}
    return compare_world_entity(
        _safe_dict(payload.get("before_setup")),
        _safe_dict(payload.get("after_setup")),
        entity_id,
    )


def simulate_adventure_step_payload(data: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_world_service import advance_world_simulation

    payload = _safe_dict(data)
    return advance_world_simulation(_safe_dict(payload.get("setup")))


def adventure_simulation_state_payload(data: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.edge.services.adventure_world_service import get_simulation_state

    payload = _safe_dict(data)
    return get_simulation_state(_safe_dict(payload.get("setup")))
