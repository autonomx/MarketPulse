"""Versioned semantic interaction frames shared by LLM and deterministic code."""
from __future__ import annotations

from typing import Any, Dict

SEMANTIC_INTERACTION_VERSION = "rpg_semantic_interaction_v2"
_SERVICE_ACTIONS = {"service_inquiry", "service_purchase"}


def normalize_semantic_interaction(
    advisory: Dict[str, Any] | None,
    *,
    player_input: str,
) -> Dict[str, Any]:
    """Normalize an LLM advisory into a bounded runtime-safe frame.

    This function only describes intent.  It never validates or applies state.
    """

    source = _dict(advisory)
    action_intent = _dict(source.get("action_intent"))
    merged = {**source, **action_intent}
    action_type = _text(merged.get("action_type") or merged.get("intent"))
    service_kind = _text(merged.get("service_kind"))
    provider_id = _text(merged.get("provider_id") or merged.get("target_id"))
    provider_name = _text(merged.get("provider_name") or merged.get("target_name"))
    offer_id = _text(
        merged.get("offer_id")
        or merged.get("offer_ref")
        or merged.get("selected_offer_id")
    )
    confidence = _confidence(merged.get("confidence"), default=0.5 if action_type else 0.0)
    family = _text(merged.get("semantic_family"))
    if not family and action_type in _SERVICE_ACTIONS:
        family = "commerce"
    return {
        "schema_version": SEMANTIC_INTERACTION_VERSION,
        "player_input": str(player_input or "").strip(),
        "intent": action_type,
        "semantic_family": family,
        "actor_ref": provider_id,
        "actor_name": provider_name,
        "service_kind": service_kind,
        "offer_ref": offer_id,
        "confirmation": bool(merged.get("confirmation")),
        "duration_policy": _text(merged.get("duration_policy")),
        "confidence": confidence,
        "ambiguities": [str(value) for value in merged.get("ambiguities", []) if value] if isinstance(merged.get("ambiguities"), list) else [],
        "source": _text(merged.get("source") or source.get("source") or "llm_semantic_advisory"),
    }


def attach_semantic_interaction(
    action: Dict[str, Any] | None,
    advisory: Dict[str, Any] | None,
    *,
    player_input: str,
) -> Dict[str, Any]:
    """Attach a normalized semantic frame to an action without mutating input."""

    result = _dict(action)
    metadata = _dict(result.get("metadata"))
    frame = normalize_semantic_interaction(advisory, player_input=player_input)
    metadata["semantic_interaction"] = frame
    result["metadata"] = metadata
    for target, source_key in (
        ("action_type", "intent"),
        ("service_kind", "service_kind"),
        ("provider_id", "actor_ref"),
        ("provider_name", "actor_name"),
        ("offer_id", "offer_ref"),
    ):
        if not result.get(target) and frame.get(source_key):
            result[target] = frame[source_key]
    if not result.get("target_id") and frame.get("actor_ref"):
        result["target_id"] = frame["actor_ref"]
    if not result.get("target_name") and frame.get("actor_name"):
        result["target_name"] = frame["actor_name"]
    return result


def semantic_interaction_from_action(action: Dict[str, Any] | None) -> Dict[str, Any]:
    action = _dict(action)
    metadata = _dict(action.get("metadata"))
    frame = _dict(metadata.get("semantic_interaction"))
    if frame.get("schema_version") == SEMANTIC_INTERACTION_VERSION:
        return frame
    return normalize_semantic_interaction(action, player_input=_text(metadata.get("player_input")))


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _confidence(value: Any, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


__all__ = [
    "SEMANTIC_INTERACTION_VERSION",
    "attach_semantic_interaction",
    "normalize_semantic_interaction",
    "semantic_interaction_from_action",
]
