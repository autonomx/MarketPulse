from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


_STALE_FAST_COMBAT_NARRATION = {
    "the confrontation remains tense, but no injury is resolved.",
    "no combat, damage, death, or injury is resolved by the turn contract.",
}


def _candidate_payloads(result: Dict[str, Any]) -> list[Dict[str, Any]]:
    result = _safe_dict(result)
    nested = _safe_dict(result.get("result"))
    resolved = _safe_dict(result.get("resolved_result")) or _safe_dict(nested.get("resolved_result"))
    return [
        _safe_dict(result.get("combat_narration_payload")),
        _safe_dict(nested.get("combat_narration_payload")),
        _safe_dict(resolved.get("combat_narration_payload")),
        _safe_dict(result.get("structured_narration")),
        _safe_dict(nested.get("structured_narration")),
        _safe_dict(resolved.get("structured_narration")),
        _safe_dict(result.get("narration_payload")),
        _safe_dict(nested.get("narration_payload")),
        _safe_dict(resolved.get("narration_payload")),
    ]


def _has_valid_combat_delta(payload: Dict[str, Any]) -> bool:
    delta = _safe_dict(payload.get("combat_delta")) or _safe_dict(payload.get("combat_delta_contract"))
    if not delta:
        return False
    if delta.get("damage_applied") not in (None, ""):
        return True
    if delta.get("defeated") is True or delta.get("combat_ended") is True:
        return True
    if delta.get("target_hp_before") not in (None, "") and delta.get("target_hp_after") not in (None, ""):
        return True
    return False


def deterministic_fast_combat_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return deterministic fast-combat presentation when backed by combat delta.

    Fast combat deliberately skips provider narration, so its validation payload is
    not an accepted provider combat narration. This helper lets report/UI
    presentation still prefer the deterministic combat summary when it carries a
    backed combat delta, preventing stale deferred fallback text from hiding real
    damage.
    """

    for payload in _candidate_payloads(result):
        if _safe_str(payload.get("source")) != "deterministic_combat_fast_summary":
            continue
        narration = _safe_str(payload.get("narration")).strip()
        if narration and _has_valid_combat_delta(payload):
            return payload
    return {}


def _remove_unsupported_combat_claims(validation: Dict[str, Any]) -> Dict[str, Any]:
    validation = _safe_dict(validation)
    if not validation:
        return validation
    original_violations = _safe_list(validation.get("violations"))
    violations = [
        violation
        for violation in original_violations
        if _safe_dict(violation).get("code") != "unsupported_combat_claim"
    ]
    if len(violations) == len(original_violations):
        return validation
    validation["violations"] = violations
    validation["fast_combat_delta_supported"] = True
    validation["fast_combat_delta_support_source"] = "deterministic_combat_fast_summary"
    validation["ok"] = not violations
    if not violations:
        validation["fallback_used"] = False
        validation["fallback_source"] = "deterministic_combat_fast_summary"
        validation["selected_candidate"] = "deterministic_combat_fast_summary"
    return validation


def _normalize_grounding_mirror_payload(payload: Dict[str, Any], *, narration: str) -> Dict[str, Any]:
    payload = _safe_dict(payload)
    if not payload:
        return payload

    validation = _safe_dict(payload.get("grounding_validation"))
    if validation:
        payload["grounding_validation"] = _remove_unsupported_combat_claims(validation)

    codes = [code for code in _safe_list(payload.get("grounding_violation_codes")) if code != "unsupported_combat_claim"]
    if codes or "grounding_violation_codes" in payload:
        payload["grounding_violation_codes"] = codes

    if _safe_str(payload.get("narration")).strip().casefold() in _STALE_FAST_COMBAT_NARRATION:
        payload["narration"] = narration
    if _safe_str(payload.get("json_narration")).strip().casefold() in _STALE_FAST_COMBAT_NARRATION:
        payload["json_narration"] = narration

    extracted = _safe_dict(payload.get("extracted"))
    if extracted:
        payload["extracted"] = _normalize_grounding_mirror_payload(extracted, narration=narration)

    return payload


def _normalize_container(container: Dict[str, Any], *, narration: str) -> Dict[str, Any]:
    container = _safe_dict(container)
    if not container:
        return container

    container = _normalize_grounding_mirror_payload(container, narration=narration)
    for key in (
        "extracted",
        "narration_debug",
        "narration_payload_compact",
        "raw_narration_payload",
        "structured_narration_compact",
        "narration_payload",
        "structured_narration",
    ):
        value = _safe_dict(container.get(key))
        if value:
            container[key] = _normalize_grounding_mirror_payload(value, narration=narration)

    return container


def repair_fast_combat_grounding_validation(result: Dict[str, Any]) -> Dict[str, Any]:
    """Suppress false unsupported-combat-claim warnings backed by combat delta.

    This is intentionally narrow: it only repairs grounding_validation objects
    when the same result has deterministic_combat_fast_summary plus a valid combat
    delta. It does not suppress unrelated grounding violations.
    """

    result = _safe_dict(result)
    payload = deterministic_fast_combat_payload(result)
    narration = _safe_str(payload.get("narration")).strip()
    if not narration:
        return result

    nested = _safe_dict(result.get("result"))
    resolved = _safe_dict(result.get("resolved_result")) or _safe_dict(nested.get("resolved_result"))

    result = _normalize_container(result, narration=narration)
    if nested:
        nested = _normalize_container(nested, narration=narration)
        result["result"] = nested
    if resolved:
        resolved = _normalize_container(resolved, narration=narration)
        if "resolved_result" in result:
            result["resolved_result"] = resolved
        elif nested and "resolved_result" in nested:
            nested["resolved_result"] = resolved
            result["result"] = nested

    result["fast_combat_grounding_delta_repair"] = {
        "applied": True,
        "source": "deterministic_combat_fast_summary",
        "combat_delta": _safe_dict(payload.get("combat_delta") or payload.get("combat_delta_contract")),
    }
    return result
