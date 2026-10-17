"""Phase 17.0 — Integrity validation and fail-fast enforcement."""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from app.apps.rpg.rules.survival import SURVIVAL_EVENT_LIMIT
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MAX_VISUAL_REQUESTS = 100
MAX_VISUAL_ASSETS = 200
MAX_ACTOR_MEMORY_ENTRIES = 50
MAX_WORLD_RUMORS = 50


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _err(code: str, detail: str = "") -> Dict[str, Any]:
    return {"code": code, "detail": detail}


def _coerce_float(value: Any) -> Tuple[float, bool]:
    try:
        return float(value or 0.0), True
    except Exception:
        return 0.0, False


def _coerce_int(value: Any) -> Tuple[int, bool]:
    try:
        return int(value or 0), True
    except Exception:
        return 0, False


def _request_sort_key(item: Any) -> str:
    return _safe_str(_safe_dict(item).get("request_id")).strip()


def _asset_sort_key(item: Any) -> str:
    return _safe_str(_safe_dict(item).get("asset_id")).strip()


def validate_visual_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    presentation_state = _safe_dict(simulation_state.get("presentation_state"))
    visual_state = _safe_dict(presentation_state.get("visual_state"))

    image_requests = _safe_list(visual_state.get("image_requests"))
    visual_assets = _safe_list(visual_state.get("visual_assets"))

    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    if len(image_requests) > MAX_VISUAL_REQUESTS:
        errors.append(_err("visual_requests_over_cap", f"{len(image_requests)} > {MAX_VISUAL_REQUESTS}"))
    if len(visual_assets) > MAX_VISUAL_ASSETS:
        errors.append(_err("visual_assets_over_cap", f"{len(visual_assets)} > {MAX_VISUAL_ASSETS}"))

    # Collect ALL request IDs first (Fix #1: order-independent validation)
    all_request_ids: Set[str] = set()
    for item in sorted(image_requests, key=_request_sort_key):
        item = _safe_dict(item)
        rid = _safe_str(item.get("request_id")).strip()
        if rid:
            all_request_ids.add(rid)

    seen_request_ids: Set[str] = set()
    for idx, item in enumerate(sorted(image_requests, key=_request_sort_key)):
        item = _safe_dict(item)
        request_id = _safe_str(item.get("request_id")).strip()
        status = _safe_str(item.get("status")).strip()
        if not request_id:
            errors.append(_err("visual_request_missing_id", f"index={idx}"))
        elif request_id in seen_request_ids:
            errors.append(_err("visual_request_duplicate_id", request_id))
        else:
            seen_request_ids.add(request_id)

        if status and status not in {"pending", "complete", "failed", "blocked"}:
            errors.append(_err("visual_request_invalid_status", f"{request_id}:{status}"))

    seen_asset_ids: Set[str] = set()
    for idx, item in enumerate(sorted(visual_assets, key=_asset_sort_key)):
        item = _safe_dict(item)
        asset_id = _safe_str(item.get("asset_id")).strip()
        status = _safe_str(item.get("status")).strip()
        if not asset_id:
            errors.append(_err("visual_asset_missing_id", f"index={idx}"))
        elif asset_id in seen_asset_ids:
            errors.append(_err("visual_asset_duplicate_id", asset_id))
        else:
            seen_asset_ids.add(asset_id)

        if status and status not in {"complete", "failed", "blocked", "pending"}:
            errors.append(_err("visual_asset_invalid_status", f"{asset_id}:{status}"))

        created_from = _safe_str(item.get("created_from_request_id")).strip()
        if created_from and created_from not in all_request_ids:
            warnings.append(_err("visual_asset_request_reference_missing", f"{asset_id}:{created_from}"))

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "image_requests": len(image_requests),
            "visual_assets": len(visual_assets),
        },
    }


def validate_memory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    memory_state = _safe_dict(simulation_state.get("memory_state"))

    actor_memory = _safe_dict(memory_state.get("actor_memory"))
    world_memory = _safe_dict(memory_state.get("world_memory"))
    rumors = _safe_list(world_memory.get("rumors"))

    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    for actor_id in sorted(actor_memory.keys()):
        bucket = _safe_dict(actor_memory.get(actor_id))
        entries = _safe_list(bucket.get("entries"))
        if len(entries) > MAX_ACTOR_MEMORY_ENTRIES:
            errors.append(_err("actor_memory_over_cap", f"{actor_id}:{len(entries)}"))

        seen_texts: Set[str] = set()
        for entry in entries:
            entry = _safe_dict(entry)
            text = _safe_str(entry.get("text")).strip()
            strength, ok_strength = _coerce_float(entry.get("strength"))
            if not text:
                errors.append(_err("actor_memory_missing_text", actor_id))
            if not ok_strength:
                errors.append(_err("actor_memory_invalid_strength_type", actor_id))
            if strength < 0.0 or strength > 1.0:
                errors.append(_err("actor_memory_strength_out_of_bounds", f"{actor_id}:{strength}"))
            if text in seen_texts:
                warnings.append(_err("actor_memory_duplicate_text", f"{actor_id}:{text}"))
            else:
                seen_texts.add(text)

    if len(rumors) > MAX_WORLD_RUMORS:
        errors.append(_err("world_rumors_over_cap", f"{len(rumors)}"))

    seen_rumor_texts: Set[str] = set()
    for rumor in rumors:
        rumor = _safe_dict(rumor)
        text = _safe_str(rumor.get("text")).strip()
        strength, ok_strength = _coerce_float(rumor.get("strength"))
        reach, ok_reach = _coerce_int(rumor.get("reach"))
        if not text:
            errors.append(_err("world_rumor_missing_text"))
        if not ok_strength:
            errors.append(_err("world_rumor_invalid_strength_type", text))
        if not ok_reach:
            errors.append(_err("world_rumor_invalid_reach_type", text))
        if strength < 0.0 or strength > 1.0:
            errors.append(_err("world_rumor_strength_out_of_bounds", f"{strength}"))
        if reach < 0:
            errors.append(_err("world_rumor_negative_reach", f"{reach}"))
        if text in seen_rumor_texts:
            warnings.append(_err("world_rumor_duplicate_text", text))
        else:
            seen_rumor_texts.add(text)

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "actors": len(actor_memory),
            "rumors": len(rumors),
        },
    }


def validate_survival_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    survival = _safe_dict(simulation_state.get("survival"))
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    if not survival:
        return {
            "ok": True,
            "errors": [],
            "warnings": [],
            "counts": {"survival_events": 0},
        }

    for key in ("hunger", "thirst", "fatigue"):
        value, ok_value = _coerce_int(survival.get(key))
        if not ok_value:
            errors.append(_err("survival_need_invalid_type", key))
        elif value < 0 or value > 100:
            errors.append(_err("survival_need_out_of_bounds", f"{key}:{value}"))

    for key in ("last_food_turn", "last_water_turn", "last_rest_turn"):
        turn_value = survival.get(key)
        if turn_value is None or turn_value == "":
            continue
        _, ok_value = _coerce_int(turn_value)
        if not ok_value:
            warnings.append(_err("survival_last_turn_invalid_type", key))

    events = _safe_list(survival.get("events"))
    if len(events) > SURVIVAL_EVENT_LIMIT:
        errors.append(_err("survival_events_over_cap", f"{len(events)} > {SURVIVAL_EVENT_LIMIT}"))
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(_err("survival_event_invalid_type", str(index)))
            continue
        if not _safe_str(event.get("source")).strip():
            warnings.append(_err("survival_event_missing_source", str(index)))

    known_keys = {
        "enabled",
        "hunger",
        "thirst",
        "fatigue",
        "last_food_turn",
        "last_water_turn",
        "last_rest_turn",
        "events",
    }
    extra_keys = sorted(key for key in survival.keys() if key not in known_keys)
    if extra_keys:
        warnings.append(_err("survival_unknown_keys_will_be_pruned", ",".join(extra_keys)))

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "counts": {"survival_events": len(events)},
    }


def validate_session_manifest(session: Dict[str, Any]) -> Dict[str, Any]:
    session = _safe_dict(session)
    manifest = _safe_dict(session.get("manifest"))
    errors: List[Dict[str, Any]] = []

    session_id = _safe_str(manifest.get("id")).strip()
    schema_version = int(manifest.get("schema_version") or 0)

    if not session_id:
        errors.append(_err("manifest_missing_id"))
    if schema_version <= 0:
        errors.append(_err("manifest_invalid_schema_version", str(schema_version)))

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": [],
        "counts": {},
    }


def validate_simulation_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    visual = validate_visual_state(simulation_state)
    memory = validate_memory_state(simulation_state)
    survival = validate_survival_state(simulation_state)
    errors = list(visual["errors"]) + list(memory["errors"]) + list(survival["errors"])
    warnings = list(visual["warnings"]) + list(memory["warnings"]) + list(survival["warnings"])
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "visual_requests": visual["counts"]["image_requests"],
            "visual_assets": visual["counts"]["visual_assets"],
            "memory_actors": memory["counts"]["actors"],
            "memory_rumors": memory["counts"]["rumors"],
            "survival_events": survival["counts"]["survival_events"],
        },
    }


def validate_session_integrity(session: Dict[str, Any]) -> Dict[str, Any]:
    session = _safe_dict(session)
    manifest_result = validate_session_manifest(session)
    simulation_result = validate_simulation_state(_safe_dict(session.get("simulation_state")))

    errors = list(manifest_result["errors"]) + list(simulation_result["errors"])
    warnings = list(manifest_result["warnings"]) + list(simulation_result["warnings"])

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "counts": simulation_result["counts"],
    }


def validate_package_integrity(package_payload: Dict[str, Any]) -> Dict[str, Any]:
    package_payload = _safe_dict(package_payload)
    session_manifest = _safe_dict(package_payload.get("session_manifest"))
    simulation_state = _safe_dict(package_payload.get("simulation_state"))

    session_like = {
        "manifest": session_manifest,
        "simulation_state": simulation_state,
    }
    return validate_session_integrity(session_like)


def assert_session_integrity(session: Dict[str, Any]) -> Dict[str, Any]:
    result = validate_session_integrity(session)
    if not result["ok"]:
        raise ValueError(
            {
                "type": "session_integrity_failed",
                "errors": result["errors"],
                "warnings": result["warnings"],
            }
        )
    return result


def assert_package_integrity(package_payload: Dict[str, Any]) -> Dict[str, Any]:
    result = validate_package_integrity(package_payload)
    if not result["ok"]:
        raise ValueError(
            {
                "type": "package_integrity_failed",
                "errors": result["errors"],
                "warnings": result["warnings"],
            }
        )
    return result
