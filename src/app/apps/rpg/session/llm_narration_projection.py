from __future__ import annotations

import logging
from typing import Any, Iterable

# RPG session runtime responsibility module.
# Phase 8.31: the visible response for a player turn must come from the
# narration pipeline, not from rule-based text replacement.  This wrapper
# intentionally bypasses complete_narration_fallback's deterministic dialogue text and uses
# the real narrator synchronously when the authoritative turn has only queued
# narration available.
from app.apps.rpg.session.state_normalization import (
    _safe_dict as _safe_dict, _safe_str as _safe_str,
)

from app.apps.rpg.session.deferred_narration_guard import runtime_narration_follows

from .travel_panel_response import _apply_visible_fallback_with_travel_panels as _PHASE8_PART31_BASE_APPLY_TURN_AUTHORITATIVE

logger = logging.getLogger(__name__)

_PHASE8_PART31_SOURCE = "provider_sync_visible_turn_narration"


def _phase8_part31_iter_payload_dicts(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    payload = _safe_dict(payload)
    if payload:
        yield payload
    for key in ("result", "authoritative", "payload"):
        nested = _safe_dict(payload.get(key))
        if nested:
            yield nested


def _phase8_part31_clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text.casefold() in {"", "[]", "{}", "null", "none"} else text


def _phase8_part31_existing_completed_narration(payload: dict[str, Any]) -> str:
    for source in _phase8_part31_iter_payload_dicts(payload):
        status = _safe_str(source.get("narration_status")).strip().casefold()
        text = _phase8_part31_clean_text(
            source.get("narration")
            or source.get("final_narration")
            or source.get("raw_payload_narration")
        )
        if text and status == "completed":
            return text
    return ""


def _phase8_part31_narration_request(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_dict(payload)
    return (
        _safe_dict(payload.get("narration_request"))
        or _safe_dict(_safe_dict(payload.get("authoritative")).get("narration_request"))
        or _safe_dict(_safe_dict(payload.get("result")).get("narration_request"))
        or _safe_dict(_safe_dict(payload.get("payload")).get("narration_request"))
    )


def _phase8_part31_should_sync_narration(payload: dict[str, Any]) -> bool:
    if _phase8_part31_existing_completed_narration(payload):
        return False
    if runtime_narration_follows():
        return False
    request = _phase8_part31_narration_request(payload)
    if not request:
        return False
    perf = _safe_dict(request.get("performance"))
    if perf.get("enable_live_narration_llm") is False:
        return False
    scene = _safe_dict(request.get("scene"))
    context = _safe_dict(request.get("narration_context"))
    return bool(scene or context)


def _phase8_part31_sync_narration(payload: dict[str, Any]) -> dict[str, Any]:
    request = _phase8_part31_narration_request(payload)
    if not request:
        return {}

    try:
        from app.apps.rpg.narration.ai.world_scene_narrator import narrate_scene
        from app.apps.rpg.foundation.llm_app_gateway import build_app_llm_gateway

        scene = _safe_dict(request.get("scene"))
        context = _safe_dict(request.get("narration_context"))
        llm_gateway = build_app_llm_gateway()
        narration_payload = narrate_scene(scene, context, llm_gateway=llm_gateway)
    except Exception as exc:
        logger.exception(
            "Synchronous visible RPG narration failed",
            extra={
                "session_id": _safe_str(request.get("session_id")),
                "turn_id": _safe_str(request.get("turn_id")),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return {}

    narration_payload = _safe_dict(narration_payload)
    text = _phase8_part31_clean_text(
        narration_payload.get("narration")
        or narration_payload.get("final_narration")
        or narration_payload.get("text")
    )
    if not text:
        return {}

    return {
        "narration": text,
        "final_narration": text,
        "raw_payload_narration": text,
        "deterministic_fallback_narration": text,
        "narration_status": "completed",
        "used_llm": bool(narration_payload.get("used_llm", True)),
        "raw_llm_narrative": narration_payload,
        "narration_json": _safe_dict(narration_payload.get("narration_json")),
        "npc": _safe_dict(narration_payload.get("npc")),
        "grounding_validation": _safe_dict(narration_payload.get("grounding_validation")),
        "grounding_fallback": bool(narration_payload.get("grounding_fallback", False)),
        "fallback_narration_source": _PHASE8_PART31_SOURCE,
    }


def _phase8_part31_patch_visible_ai_narration(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if not _phase8_part31_should_sync_narration(payload):
        return payload

    narration_fields = _phase8_part31_sync_narration(payload)
    if not narration_fields:
        return payload

    patched = dict(payload)
    for key, value in narration_fields.items():
        if key == "npc" and not value:
            continue
        patched[key] = value

    for key in ("result", "authoritative", "payload"):
        nested = _safe_dict(patched.get(key))
        if not nested:
            continue
        nested = dict(nested)
        for field_key, field_value in narration_fields.items():
            if field_key == "npc" and not field_value:
                continue
            nested[field_key] = field_value
        patched[key] = nested

    return patched


def _apply_ai_narration_projection(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_authoritative: Any = _PHASE8_PART31_BASE_APPLY_TURN_AUTHORITATIVE,
) -> dict[str, Any]:
    payload = _base_authoritative(
        session_id,
        player_input,
        action,
        performance_override=performance_override,
    )
    return _phase8_part31_patch_visible_ai_narration(payload)


__all__ = ['_PHASE8_PART31_BASE_APPLY_TURN_AUTHORITATIVE', '_PHASE8_PART31_SOURCE', '_apply_ai_narration_projection', '_phase8_part31_clean_text', '_phase8_part31_iter_payload_dicts', '_phase8_part31_narration_request', '_phase8_part31_patch_visible_ai_narration', '_phase8_part31_sync_narration']