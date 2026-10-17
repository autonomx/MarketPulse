from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round(value: float) -> float:
    return round(float(value), 3)


def _relationship_key(owner_id: str, subject_id: str = "player") -> str:
    return f"{owner_id}::{subject_id}"


def _ensure_relationship_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    relationships = state.get("relationship_state")
    if not isinstance(relationships, dict):
        relationships = {}
        state["relationship_state"] = relationships
    return relationships


def _ensure_emotion_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    emotion_state = state.get("npc_emotion_state")
    if not isinstance(emotion_state, dict):
        emotion_state = {}
        state["npc_emotion_state"] = emotion_state
    return emotion_state


def _effect_profile(
    service_result: Dict[str, Any],
    service_application: Dict[str, Any],
) -> Dict[str, float]:
    kind = _safe_str(service_result.get("kind"))
    status = _safe_str(service_result.get("status"))
    purchase = _safe_dict(service_result.get("purchase"))
    blocked_reason = _safe_str(
        service_application.get("blocked_reason") or purchase.get("blocked_reason")
    )
    applied = bool(
        service_application.get("applied")
        or purchase.get("applied")
        or status == "purchased"
    )

    if kind == "service_inquiry":
        return {
            "familiarity": 0.25,
            "trust": 0.0,
            "annoyance": 0.0,
            "valence": 0.02,
            "arousal": 0.03,
        }

    if kind == "service_purchase" and applied:
        return {
            "familiarity": 1.0,
            "trust": 0.25,
            "annoyance": -0.1,
            "valence": 0.1,
            "arousal": 0.05,
        }

    if kind == "service_purchase" and blocked_reason == "insufficient_funds":
        return {
            "familiarity": 0.25,
            "trust": 0.0,
            "annoyance": 0.25,
            "valence": -0.1,
            "arousal": 0.18,
        }

    if kind == "service_purchase":
        return {
            "familiarity": 0.1,
            "trust": 0.0,
            "annoyance": 0.1,
            "valence": -0.04,
            "arousal": 0.1,
        }

    return {
        "familiarity": 0.0,
        "trust": 0.0,
        "annoyance": 0.0,
        "valence": 0.0,
        "arousal": 0.0,
    }


def _dominant_emotion(valence: float, arousal: float, annoyance: float) -> str:
    if annoyance >= 0.75:
        return "annoyed"
    if annoyance >= 0.25:
        return "mildly_annoyed"
    if valence >= 0.2:
        return "pleased"
    if valence <= -0.2:
        return "displeased"
    if arousal >= 0.4:
        return "alert"
    return "neutral"


def apply_service_social_effects(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    service_application: Dict[str, Any] | None = None,
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    service_result = _safe_dict(service_result)
    service_application = _safe_dict(service_application)
    if not service_result.get("matched"):
        return {}

    owner_id = _safe_str(service_result.get("provider_id"))
    owner_name = _safe_str(service_result.get("provider_name"))
    if not owner_id:
        return {}

    profile = _effect_profile(service_result, service_application)
    relationships = _ensure_relationship_state(simulation_state)
    key = _relationship_key(owner_id)
    relationship = _safe_dict(relationships.get(key))
    axes = _safe_dict(relationship.get("axes"))

    before_axes = deepcopy(axes)
    familiarity = _clamp(
        float(axes.get("familiarity") or 0.0) + profile["familiarity"],
        0.0,
        100.0,
    )
    trust = _clamp(float(axes.get("trust") or 0.0) + profile["trust"], -100.0, 100.0)
    annoyance = _clamp(
        float(axes.get("annoyance") or 0.0) + profile["annoyance"],
        0.0,
        100.0,
    )

    axes["familiarity"] = _round(familiarity)
    axes["trust"] = _round(trust)
    axes["annoyance"] = _round(annoyance)

    relationship = {
        **relationship,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "subject_id": "player",
        "axes": axes,
        "updated_tick": int(tick or 0),
        "source": "deterministic_service_runtime",
    }
    relationships[key] = relationship

    emotion_state = _ensure_emotion_state(simulation_state)
    emotion = _safe_dict(emotion_state.get(owner_id))
    valence = _clamp(float(emotion.get("valence") or 0.0) + profile["valence"], -1.0, 1.0)
    arousal = _clamp(float(emotion.get("arousal") or 0.0) + profile["arousal"], 0.0, 1.0)
    dominant = _dominant_emotion(valence, arousal, annoyance)

    emotion = {
        **emotion,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "dominant_emotion": dominant,
        "valence": _round(valence),
        "arousal": _round(arousal),
        "updated_tick": int(tick or 0),
        "source": "deterministic_service_runtime",
    }
    emotion_state[owner_id] = emotion

    return {
        "relationship_key": key,
        "relationship": deepcopy(relationship),
        "emotion": deepcopy(emotion),
        "deltas": {
            "familiarity": profile["familiarity"],
            "trust": profile["trust"],
            "annoyance": profile["annoyance"],
            "valence": profile["valence"],
            "arousal": profile["arousal"],
        },
        "before_axes": before_axes,
    }
