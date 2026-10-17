from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str

DEFAULT_GROUNDING_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "primary_validation": True,
    "llm_safe_fallback_candidate": True,
    "deterministic_fallback": True,
    "background_soft_audit": True,
    "background_soft_audit_mode": "append_correction",
    "background_soft_audit_can_update_state": False,
    "background_soft_audit_validate_correction": True,
}


def normalize_grounding_settings(value: Any) -> Dict[str, Any]:
    raw = _safe_dict(value)
    result = dict(DEFAULT_GROUNDING_SETTINGS)

    for key in (
        "enabled",
        "primary_validation",
        "llm_safe_fallback_candidate",
        "deterministic_fallback",
        "background_soft_audit",
        "background_soft_audit_can_update_state",
        "background_soft_audit_validate_correction",
    ):
        if key in raw:
            result[key] = bool(raw.get(key))

    mode = _safe_str(raw.get("background_soft_audit_mode")).strip().lower()
    if mode in {"append_correction", "disabled"}:
        result["background_soft_audit_mode"] = mode

    result["background_soft_audit_can_update_state"] = False
    result["enabled"] = True
    result["primary_validation"] = True
    result["deterministic_fallback"] = True

    return result