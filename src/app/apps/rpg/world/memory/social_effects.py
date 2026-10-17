from __future__ import annotations

import logging

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

try:
    from app.apps.rpg.world.npc_biography_registry import get_npc_biography
except Exception:
    get_npc_biography = None  # type: ignore[assignment]  # optional registry

logger = logging.getLogger(__name__)

MAX_SOCIAL_MEMORIES = 100


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


SYNTHETIC_SOCIAL_TARGET_HINTS = {
    "room",
    "environment",
    "atmosphere",
    "ambience",
    "ambient",
    "scene",
    "location",
    "surroundings",
    "area",
    "place",
    "general",
    "npcs general",
    "environment/npcs",
    "environment/npcs general",
}


def _normalized_social_target_text(*values: Any) -> str:
    return " ".join(_safe_str(value).strip().lower() for value in values if _safe_str(value).strip())


def _looks_like_synthetic_social_target(*values: Any) -> bool:
    text = _normalized_social_target_text(*values)
    if not text:
        return True

    if "room/environment" in text:
        return True
    if "environment/npcs" in text:
        return True
    if "tavern atmosphere" in text:
        return True
    if "the room" in text and "environment" in text:
        return True

    compact = (
        text.replace("npc:", "")
        .replace("the ", "")
        .replace("-", " ")
        .replace("_", " ")
        .strip()
    )
    compact_tokens = {token for token in compact.split() if token}

    if compact in SYNTHETIC_SOCIAL_TARGET_HINTS:
        return True
    if compact_tokens and compact_tokens.issubset(SYNTHETIC_SOCIAL_TARGET_HINTS):
        return True

    return False


def _is_known_real_npc(owner_id: str, owner_name: str = "") -> bool:
    owner_id = _safe_str(owner_id).strip()
    owner_name = _safe_str(owner_name).strip()

    if not owner_id.startswith("npc:"):
        return False
    if _looks_like_synthetic_social_target(owner_id, owner_name):
        return False

    # Prefer the biography registry as the positive allowlist when present.
    if get_npc_biography is not None:
        try:
            bio = get_npc_biography(owner_id)
            bio_id = _safe_str(bio.get("npc_id"))
            bio_name = _safe_str(bio.get("name"))
            bio_source = _safe_str(bio.get("source"))

            # Unknown fallback biographies are intentionally generic. Treat them
            # as not enough proof for persistent social state.
            if bio_id == owner_id and bio_source == "deterministic_npc_biography_registry":
                if "no detailed biography" not in _safe_str(bio.get("short_bio")).lower():
                    return True

            # Allow known aliases if the registry returns a concrete biography.
            if bio_id and bio_id != "npc:Unknown" and bio_name and "unknown" not in bio_name.lower():
                if not _looks_like_synthetic_social_target(bio_id, bio_name):
                    if "no detailed biography" not in _safe_str(bio.get("short_bio")).lower():
                        return True
        except Exception:
            logger.debug("suppressed error in %s", "_is_known_real_npc", exc_info=True)

    # Fallback allowlist for pre-biography projects.
    return owner_id in {
        "npc:Bran",
        "npc:Mira",
        "npc:GuardCaptain",
        "npc:Merchant",
    }


def _should_skip_social_target(owner_id: str, owner_name: str, resolved_result: Dict[str, Any]) -> Dict[str, Any]:
    action_type = _safe_str(
        resolved_result.get("action_type")
        or resolved_result.get("semantic_action_type")
        or resolved_result.get("semantic_family")
    ).lower()
    activity_label = _safe_str(resolved_result.get("activity_label")).lower()
    target_id = _safe_str(resolved_result.get("target_id") or owner_id)
    target_name = _safe_str(resolved_result.get("target_name") or owner_name)

    if _looks_like_synthetic_social_target(owner_id, owner_name, target_id, target_name, activity_label):
        return {
            "skip": True,
            "reason": "synthetic_social_target",
        }

    if action_type in {"ambient_wait", "wait", "listen", "observe", "ambient"}:
        if not _is_known_real_npc(owner_id, owner_name):
            return {
                "skip": True,
                "reason": "ambient_non_npc_social_target",
            }

    if not _is_known_real_npc(owner_id, owner_name):
        return {
            "skip": True,
            "reason": "unknown_or_synthetic_npc_target",
        }

    return {"skip": False, "reason": ""}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round(value: float) -> float:
    return round(float(value), 3)


def _relationship_key(owner_id: str, subject_id: str = "player") -> str:
    return f"{owner_id}::{subject_id}"





def _ensure_relationship_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    relationships = simulation_state.get("relationship_state")
    if not isinstance(relationships, dict):
        relationships = {}
        simulation_state["relationship_state"] = relationships
    return relationships


def _ensure_emotion_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    emotions = simulation_state.get("npc_emotion_state")
    if not isinstance(emotions, dict):
        emotions = {}
        simulation_state["npc_emotion_state"] = emotions
    return emotions


def _ensure_memory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    memory_state = simulation_state.get("memory_state")
    if not isinstance(memory_state, dict):
        memory_state = {}
        simulation_state["memory_state"] = memory_state
    social_memories = memory_state.get("social_memories")
    if not isinstance(social_memories, list):
        social_memories = []
        memory_state["social_memories"] = social_memories
    return memory_state


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


def _profile_for_social_action(resolved_result: Dict[str, Any]) -> Dict[str, float]:
    action_type = _safe_str(resolved_result.get("action_type"))
    outcome = _safe_str(resolved_result.get("outcome"))
    margin = _safe_int(resolved_result.get("margin"), 0)
    metadata = _safe_dict(resolved_result.get("action_metadata") or resolved_result.get("metadata"))
    intent_tags = set(_safe_list(metadata.get("intent_tags")))

    profile = {
        "familiarity": 0.1,
        "trust": 0.0,
        "annoyance": 0.0,
        "valence": 0.0,
        "arousal": 0.02,
    }

    if action_type in {"social_activity", "persuade", "investigate", "observe"}:
        profile["familiarity"] = 0.2
        if outcome == "success":
            profile["familiarity"] += 0.15
            profile["trust"] += 0.1
            profile["valence"] += 0.04
        elif outcome == "failure":
            profile["annoyance"] += 0.1
            profile["valence"] -= 0.04

    if action_type in {"intimidate", "threaten"} or "threat" in intent_tags:
        profile["familiarity"] += 0.25
        profile["trust"] -= 0.75
        profile["annoyance"] += 1.0
        profile["valence"] -= 0.2
        profile["arousal"] += 0.25

    if action_type in {"help", "assist"} or "helpful" in intent_tags:
        profile["familiarity"] += 0.4
        profile["trust"] += 0.35
        profile["annoyance"] -= 0.1
        profile["valence"] += 0.15

    if action_type in {"insult", "mock"} or "insult" in intent_tags:
        profile["familiarity"] += 0.25
        profile["trust"] -= 1.0
        profile["annoyance"] += 1.25
        profile["valence"] -= 0.25
        profile["arousal"] += 0.2

    if margin >= 5:
        profile["trust"] += 0.05
        profile["valence"] += 0.02
    elif margin <= -5:
        profile["annoyance"] += 0.05
        profile["valence"] -= 0.02

    return profile


def _social_memory_kind(resolved_result: Dict[str, Any], profile: Dict[str, float]) -> str:
    if profile.get("annoyance", 0.0) > 0.5 or profile.get("trust", 0.0) < -0.5:
        return "social_negative"
    if profile.get("trust", 0.0) > 0.15 or profile.get("valence", 0.0) > 0.1:
        return "social_positive"
    return "social_interaction"


def _social_summary(resolved_result: Dict[str, Any], owner_name: str) -> str:
    action_type = _safe_str(resolved_result.get("action_type") or "interaction")
    outcome = _safe_str(resolved_result.get("outcome"))
    if owner_name:
        if outcome:
            return f"The player had a {outcome} {action_type} interaction with {owner_name}."
        return f"The player interacted with {owner_name}."
    if outcome:
        return f"The player had a {outcome} {action_type} interaction."
    return "The player had a social interaction."


def apply_general_social_effects(
    simulation_state: Dict[str, Any],
    resolved_result: Dict[str, Any],
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    resolved_result = _safe_dict(resolved_result)
    action_type = _safe_str(resolved_result.get("action_type"))

    service_result = _safe_dict(resolved_result.get("service_result"))
    if service_result.get("matched"):
        return {}

    social_action_types = {
        "social_activity",
        "persuade",
        "investigate",
        "observe",
        "intimidate",
        "threaten",
        "help",
        "assist",
        "insult",
        "mock",
    }
    if action_type not in social_action_types:
        return {}

    owner_id = _safe_str(resolved_result.get("target_id"))
    owner_name = _safe_str(resolved_result.get("target_name"))
    if not owner_id and owner_name:
        owner_id = owner_name if owner_name.startswith("npc:") else f"npc:{owner_name}"
    if not owner_id:
        return {}

    owner_id = _safe_str(owner_id)
    owner_name = _safe_str(owner_name)

    skip_social = _should_skip_social_target(owner_id, owner_name, resolved_result)
    if skip_social.get("skip"):
        return {
            "skipped": True,
            "reason": _safe_str(skip_social.get("reason")) or "synthetic_social_target",
            "owner_id": owner_id,
            "owner_name": owner_name,
            "target_id": _safe_str(resolved_result.get("target_id")),
            "target_name": _safe_str(resolved_result.get("target_name")),
            "action_type": _safe_str(resolved_result.get("action_type")),
            "source": "deterministic_social_runtime",
        }

    profile = _profile_for_social_action(resolved_result)

    relationships = _ensure_relationship_state(simulation_state)
    relationship_key = _relationship_key(owner_id)
    relationship = _safe_dict(relationships.get(relationship_key))
    axes = _safe_dict(relationship.get("axes"))
    before_axes = deepcopy(axes)

    familiarity = _clamp(float(axes.get("familiarity") or 0.0) + profile["familiarity"], 0.0, 100.0)
    trust = _clamp(float(axes.get("trust") or 0.0) + profile["trust"], -100.0, 100.0)
    annoyance = _clamp(float(axes.get("annoyance") or 0.0) + profile["annoyance"], 0.0, 100.0)

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
        "source": "deterministic_social_runtime",
    }
    relationships[relationship_key] = relationship

    emotions = _ensure_emotion_state(simulation_state)
    emotion = _safe_dict(emotions.get(owner_id))
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
        "source": "deterministic_social_runtime",
    }
    emotions[owner_id] = emotion

    memory_kind = _social_memory_kind(resolved_result, profile)
    memory = {
        "memory_id": f"memory:social:{int(tick or 0)}:{owner_id}:{action_type}",
        "kind": memory_kind,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "subject_id": "player",
        "summary": _social_summary(resolved_result, owner_name),
        "sentiment": "positive" if memory_kind == "social_positive" else "negative" if memory_kind == "social_negative" else "neutral",
        "importance": 0.3 if memory_kind == "social_interaction" else 0.45,
        "action_type": action_type,
        "outcome": _safe_str(resolved_result.get("outcome")),
        "tick": int(tick or 0),
        "source": "deterministic_social_runtime",
    }

    memory_state = _ensure_memory_state(simulation_state)
    social_memories = _safe_list(memory_state.get("social_memories"))
    if not any(_safe_str(existing.get("memory_id")) == memory["memory_id"] for existing in social_memories if isinstance(existing, dict)):
        social_memories.append(deepcopy(memory))
        if len(social_memories) > MAX_SOCIAL_MEMORIES:
            del social_memories[:-MAX_SOCIAL_MEMORIES]

    return {
        "memory_entry": deepcopy(memory),
        "relationship_key": relationship_key,
        "relationship": deepcopy(relationship),
        "emotion": deepcopy(emotion),
        "deltas": deepcopy(profile),
        "before_axes": before_axes,
        "source": "deterministic_social_runtime",
    }


