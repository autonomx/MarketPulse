"""Pure survival grounding helpers used by the world-scene narrator owners."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.narration.ai.survival_narration_grounding import (
    build_survival_narration_evidence,
    sanitize_survival_narration_payload,
    survival_narration_prompt_block,
    validate_survival_narration_text,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

_LEGACY_FALLBACK_TEXTS = {
    "the action resolves according to the current survival state.",
    "the action changes the scene, and the people nearby react according to what just happened.",
    "the survival action resolves.",
}


def _has_survival_evidence(narration_context: dict[str, Any]) -> bool:
    evidence = build_survival_narration_evidence(_safe_dict(narration_context))
    return bool(
        evidence.get("survival")
        or evidence.get("actions")
        or evidence.get("successful_actions")
        or evidence.get("blocked_actions")
        or evidence.get("effects")
        or evidence.get("inventory_delta")
        or evidence.get("backed_categories")
    )


def _is_legacy_survival_fallback(value: Any) -> bool:
    text = " ".join(_safe_str(value).split()).strip().lower()
    return text in _LEGACY_FALLBACK_TEXTS


def append_survival_grounding_to_prompt(prompt: str, narration_context: dict[str, Any]) -> str:
    """Append the survival grounding contract only when evidence exists."""
    prompt = str(prompt or "")
    narration_context = _safe_dict(narration_context)
    if not _has_survival_evidence(narration_context):
        return prompt
    block = survival_narration_prompt_block(narration_context)
    if block and block not in prompt:
        return prompt.rstrip() + "\n\n" + block + "\n"
    return prompt


def sanitize_world_scene_survival_payload(payload: dict[str, Any], narration_context: dict[str, Any]) -> dict[str, Any]:
    """Apply BS survival sanitizer while preserving existing narrator metadata."""
    payload = dict(_safe_dict(payload))
    narration_context = _safe_dict(narration_context)
    if not _has_survival_evidence(narration_context):
        return payload
    sanitized = sanitize_survival_narration_payload(payload, narration_context)
    # Preserve existing grounding metadata from the older presentation validator.
    for key in ("grounding_validation", "grounding_fallback", "grounding_fallback_reason"):
        if key in payload and key not in sanitized:
            sanitized[key] = payload[key]
    return sanitized
def _merge_bs1_sanitized_payload(
    *,
    original_payload: dict[str, Any],
    legacy_payload: dict[str, Any],
    narration_context: dict[str, Any],
) -> dict[str, Any]:
    """Run BS after legacy sanitize, salvaging grounded original sentences if legacy over-fell back."""
    legacy_payload = dict(_safe_dict(legacy_payload))
    narration_context = _safe_dict(narration_context)
    original_payload = dict(_safe_dict(original_payload))

    sanitized = sanitize_world_scene_survival_payload(legacy_payload, narration_context)
    original_sanitized = sanitize_world_scene_survival_payload(original_payload, narration_context)

    if _is_legacy_survival_fallback(sanitized.get("narration")):
        original_narration = _safe_str(original_sanitized.get("narration")).strip()
        if original_narration and not _is_legacy_survival_fallback(original_narration):
            validation = validate_survival_narration_text(original_narration, narration_context)
            if validation.get("ok"):
                sanitized["narration"] = original_narration

    # Preserve the most complete BS grounding record after any salvage.
    combined_text = " ".join(
        [
            _safe_str(sanitized.get("narration")),
            _safe_str(sanitized.get("action")),
            _safe_str(_safe_dict(sanitized.get("npc")).get("line")),
        ]
    )
    validation = validate_survival_narration_text(combined_text, narration_context)
    sanitized["survival_narration_grounding"] = {
        "ok": validation.get("ok"),
        "violations": validation.get("violations"),
        "evidence": validation.get("evidence"),
        "source": "survival_narration_grounding_contract",
        "legacy_salvage_checked": True,
    }
    return sanitized
