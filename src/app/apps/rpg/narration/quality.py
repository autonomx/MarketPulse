from __future__ import annotations

import re
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_RECENT_NARRATION_ITEMS = 24
MAX_RECENT_PHRASES = 48


GENERIC_PHRASES = {
    "the air grows tense",
    "the room falls silent",
    "a hush falls",
    "a hush settles",
    "his eyes narrow",
    "her eyes narrow",
    "their eyes narrow",
    "he leans closer",
    "she leans closer",
    "the tension is palpable",
    "you feel a sense of",
    "you can't help but feel",
    "for a moment",
    "for a heartbeat",
}


ACTION_IMPORTANCE_LENGTH = {
    "minor": {"min_sentences": 1, "max_sentences": 2},
    "normal": {"min_sentences": 2, "max_sentences": 4},
    "major": {"min_sentences": 3, "max_sentences": 6},
}


def normalize_text(value: Any) -> str:
    text = _safe_str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    return text


def split_sentences(text: str) -> List[str]:
    text = _safe_str(text).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def phrase_fingerprint(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^a-z0-9\s']", "", text)
    words = [w for w in text.split() if w]
    return " ".join(words[:8])


def extract_repeated_generic_phrases(text: str) -> List[str]:
    normalized = normalize_text(text)
    found: List[str] = []
    for phrase in sorted(GENERIC_PHRASES):
        if phrase in normalized:
            found.append(phrase)
    return found


def build_narration_quality_context(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    history = _safe_dict(runtime_state.get("narration_quality"))
    return {
        "recent_openings": list(_safe_list(history.get("recent_openings")))[-10:],
        "recent_fingerprints": list(_safe_list(history.get("recent_fingerprints")))[-MAX_RECENT_PHRASES:],
        "recent_generic_phrases": list(_safe_list(history.get("recent_generic_phrases")))[-20:],
        "style_rules": [
            "Do not repeat recent sentence openings.",
            "Avoid generic atmosphere phrases unless they are directly grounded in the resolved state.",
            "Prefer concrete consequences over vague mood.",
            "Do not restate the player's input as the action result.",
            "Do not invent outcomes, rewards, injuries, item use, movement, or NPC agreement.",
            "Use short narration for minor turns and richer narration only for major state changes.",
        ],
    }


def update_narration_quality_memory(
    runtime_state: Dict[str, Any],
    narration_text: str,
) -> Dict[str, Any]:
    runtime_state = dict(_safe_dict(runtime_state))
    history = dict(_safe_dict(runtime_state.get("narration_quality")))

    sentences = split_sentences(narration_text)
    opening = phrase_fingerprint(sentences[0]) if sentences else ""
    fingerprints = [phrase_fingerprint(sentence) for sentence in sentences if phrase_fingerprint(sentence)]
    generic_phrases = extract_repeated_generic_phrases(narration_text)

    recent_openings = list(_safe_list(history.get("recent_openings")))
    if opening:
        recent_openings.append(opening)

    recent_fingerprints = list(_safe_list(history.get("recent_fingerprints")))
    recent_fingerprints.extend(fingerprints)

    recent_generic = list(_safe_list(history.get("recent_generic_phrases")))
    recent_generic.extend(generic_phrases)

    history["recent_openings"] = recent_openings[-MAX_RECENT_NARRATION_ITEMS:]
    history["recent_fingerprints"] = recent_fingerprints[-MAX_RECENT_PHRASES:]
    history["recent_generic_phrases"] = recent_generic[-MAX_RECENT_PHRASES:]

    runtime_state["narration_quality"] = history
    return runtime_state


def classify_action_importance(resolved_result: Dict[str, Any]) -> str:
    resolved_result = _safe_dict(resolved_result)
    reason = normalize_text(
        resolved_result.get("visible_interaction_reason")
        or resolved_result.get("outcome")
        or resolved_result.get("action_type")
    )

    if any(token in reason for token in [
        "victory",
        "defeat",
        "level",
        "reward",
        "loot",
        "world_event",
        "combat_defeat_resolved",
        "party_defeat",
        "companion",
        "revive",
        "stabilize",
    ]):
        return "major"

    if any(token in reason for token in [
        "attack",
        "ability",
        "condition",
        "reposition",
        "flee",
        "defend",
        "service",
        "social",
        "conversation",
    ]):
        return "normal"

    return "minor"


def validate_narration_quality(
    narration_text: str,
    runtime_state: Dict[str, Any],
    resolved_result: Dict[str, Any],
) -> List[str]:
    warnings: List[str] = []
    text = _safe_str(narration_text).strip()
    normalized = normalize_text(text)
    sentences = split_sentences(text)
    quality_context = build_narration_quality_context(runtime_state)

    if not text:
        warnings.append("narration_missing")

    if normalized in {"you act.", "you do that.", "action: you act.", "result: you act."}:
        warnings.append("narration_placeholder_generic")

    generic_phrases = extract_repeated_generic_phrases(text)
    if generic_phrases:
        warnings.append("narration_contains_generic_phrase")

    opening = phrase_fingerprint(sentences[0]) if sentences else ""
    if opening and opening in set(_safe_list(quality_context.get("recent_openings"))):
        warnings.append("narration_repeated_recent_opening")

    fingerprints = set(_safe_list(quality_context.get("recent_fingerprints")))
    repeated_sentence = False
    for sentence in sentences:
        fp = phrase_fingerprint(sentence)
        if fp and fp in fingerprints:
            repeated_sentence = True
            break
    if repeated_sentence:
        warnings.append("narration_repeated_recent_sentence_shape")

    importance = classify_action_importance(resolved_result)
    bounds = ACTION_IMPORTANCE_LENGTH.get(importance, ACTION_IMPORTANCE_LENGTH["normal"])
    if len(sentences) > bounds["max_sentences"] + 2:
        warnings.append("narration_overlong_for_action_importance")
    if importance == "major" and len(sentences) < bounds["min_sentences"]:
        warnings.append("narration_too_short_for_major_event")

    return list(dict.fromkeys(warnings))