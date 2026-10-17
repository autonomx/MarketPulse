"""Normalize transcript-facing results for deterministic fast combat."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.combat_lifecycle import enrich_combat_lifecycle_result
from app.apps.rpg.session.fast_combat_presentation import (
    deterministic_fast_combat_payload,
    repair_fast_combat_grounding_validation,
)
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def _combat_grounding_validation() -> dict[str, Any]:
    return {
        "ok": True,
        "fallback_used": False,
        "fallback_source": "deterministic_combat_fast_summary",
        "selected_candidate": "deterministic_combat_fast_summary",
        "violations": [],
        "fast_combat_delta_supported": True,
        "fast_combat_delta_support_source": "deterministic_combat_fast_summary",
    }


def _normalized_fast_combat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_dict(payload)
    if not payload:
        return payload
    normalized = dict(payload)
    normalized["grounding_validation"] = _combat_grounding_validation()
    normalized["grounding_fallback"] = False
    normalized["grounding_fallback_source"] = "deterministic_combat_fast_summary"
    normalized["grounding_selected_candidate"] = "deterministic_combat_fast_summary"
    normalized["grounding_violation_codes"] = []
    return normalized


def normalize_interactive_fast_combat_result(result: dict[str, Any]) -> dict[str, Any]:
    """Restore a deterministic combat summary when it is backed by a combat delta."""

    result = repair_fast_combat_grounding_validation(result)
    payload = deterministic_fast_combat_payload(result)
    if not payload:
        return result
    payload = _normalized_fast_combat_payload(payload)
    result["narration_payload"] = dict(payload)
    result["structured_narration"] = dict(payload)
    result["grounding_validation"] = _combat_grounding_validation()
    result["grounding_violation_codes"] = []
    result["grounding_fallback"] = False
    result["grounding_fallback_source"] = "deterministic_combat_fast_summary"
    result["grounding_selected_candidate"] = "deterministic_combat_fast_summary"

    nested = _safe_dict(result.get("result"))
    if nested:
        nested["narration_payload"] = dict(payload)
        nested["structured_narration"] = dict(payload)
        nested["grounding_validation"] = _combat_grounding_validation()
        nested["grounding_violation_codes"] = []
        nested["grounding_fallback"] = False
        nested["grounding_fallback_source"] = "deterministic_combat_fast_summary"
        nested["grounding_selected_candidate"] = "deterministic_combat_fast_summary"
        result["result"] = nested
    return enrich_combat_lifecycle_result(result)


__all__ = ["normalize_interactive_fast_combat_result"]
