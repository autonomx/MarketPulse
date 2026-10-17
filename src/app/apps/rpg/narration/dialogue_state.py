from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _norm(value: Any) -> str:
    return " ".join(_safe_str(value).lower().strip().split())


def infer_dialogue_topic(player_action: str) -> str:
    lower = _norm(player_action)
    if (
        "cloaked traveler" in lower
        or "traveler" in lower
        or "witness" in lower
        or "side door" in lower
        or "trail points toward the road" in lower
        or ("road" in lower and "danger" in lower)
    ):
        return "cloaked_traveler"
    if "road" in lower or "trail" in lower or "tracks" in lower:
        return "road_trail"
    if "room" in lower or "rent" in lower or "lodging" in lower:
        return "lodging"
    if "bandit" in lower:
        return "bandit_road"
    return "general"


def _dialogue_bucket(state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(state)
    bucket = state.setdefault("dialogue_state", {})
    if not isinstance(bucket, dict):
        bucket = {}
        state["dialogue_state"] = bucket
    bucket.setdefault("npc_topics", {})
    bucket.setdefault("recent_exchanges", [])
    return bucket


def get_dialogue_context(
    state: Dict[str, Any],
    *,
    npc_id: str,
    player_action: str,
) -> Dict[str, Any]:
    bucket = _dialogue_bucket(state)
    topic = infer_dialogue_topic(player_action)
    key = f"{npc_id}:{topic}"
    topic_state = _safe_dict(_safe_dict(bucket.get("npc_topics")).get(key))
    recent = [
        row
        for row in _safe_list(bucket.get("recent_exchanges"))[-10:]
        if _safe_dict(row).get("npc_id") == npc_id
    ]
    repeat_count = int(topic_state.get("repeat_count") or 0)
    last_player_question = _safe_str(topic_state.get("last_player_question"))
    # Topic-level repeat detection matters more than exact string matching.
    # The player may alternate between:
    #   "where did the cloaked traveler go?"
    #   "what danger does the road trail confirm?"
    # Both are still the same Bran/road/witness loop once the topic has been answered.
    if topic_state:
        repeat_count += 1
    else:
        repeat_count = 0
    return {
        "npc_id": npc_id,
        "topic": topic,
        "topic_key": key,
        "repeat_count": repeat_count,
        "is_repeat": repeat_count >= 1,
        "last_player_question": last_player_question,
        "last_npc_answer": _safe_str(topic_state.get("last_npc_answer")),
        "facts_already_revealed": _safe_list(topic_state.get("facts_already_revealed")),
        "recent_exchanges": recent,
    }


def update_dialogue_state(
    state: Dict[str, Any],
    *,
    npc_id: str,
    player_action: str,
    npc_line: str,
    facts_revealed: List[str] | None = None,
) -> Dict[str, Any]:
    bucket = _dialogue_bucket(state)
    topic = infer_dialogue_topic(player_action)
    key = f"{npc_id}:{topic}"
    topics = _safe_dict(bucket.get("npc_topics"))
    previous = _safe_dict(topics.get(key))
    repeat_count = int(previous.get("repeat_count") or 0)
    if previous:
        repeat_count += 1
    facts = list(_safe_list(previous.get("facts_already_revealed")))
    for fact in _safe_list(facts_revealed):
        fact = _safe_str(fact).strip()
        if fact and fact not in facts:
            facts.append(fact)
    topics[key] = {
        "npc_id": npc_id,
        "topic": topic,
        "last_player_question": _safe_str(player_action),
        "last_npc_answer": _safe_str(npc_line),
        "repeat_count": repeat_count,
        "facts_already_revealed": facts[-12:],
    }
    bucket["npc_topics"] = topics
    recent = _safe_list(bucket.get("recent_exchanges"))
    recent.append(
        {
            "npc_id": npc_id,
            "topic": topic,
            "player_action": _safe_str(player_action),
            "npc_line": _safe_str(npc_line),
        }
    )
    bucket["recent_exchanges"] = recent[-30:]
    return bucket