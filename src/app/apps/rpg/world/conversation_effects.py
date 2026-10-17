from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

ALLOWED_SIGNAL_KINDS = {
    "rumor_interest",
    "rumor_pressure",
    "danger_warning",
    "market_pressure",
    "social_tension",
    "npc_concern",
    "service_demand",
    "quest_interest",
    "event_attention",
    "location_interest",
    "ambient_interest",
}


FORBIDDEN_CONVERSATION_RESULT_KEYS = {
    "quest_started",
    "quest_completed",
    "reward",
    "reward_granted",
    "item_created",
    "currency_delta",
    "currency_changed",
    "stock_update",
    "stock_changed",
    "journal_entry",
    "journal_entry_created",
    "transaction_record",
    "inventory_delta",
    "location_changed",
    "combat_started",
    "npc_moved",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def build_conversation_world_signal(
    *,
    tick: int,
    thread_id: str,
    beat_id: str,
    topic: Dict[str, Any],
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    topic = _safe_dict(topic)
    settings = _safe_dict(settings)
    allowed = _safe_list(topic.get("allowed_signal_kinds"))
    signal_kind = _safe_str(allowed[0] if allowed else "ambient_interest")
    if signal_kind not in ALLOWED_SIGNAL_KINDS:
        signal_kind = "ambient_interest"

    cap = max(1, _safe_int(settings.get("signal_strength_cap"), 1))
    strength = min(cap, 1)

    return {
        "signal_id": f"world_signal:conversation:{int(tick or 0)}:{thread_id}:{beat_id}",
        "kind": signal_kind,
        "strength": strength,
        "topic_id": _safe_str(topic.get("topic_id")),
        "topic_type": _safe_str(topic.get("topic_type")),
        "summary": _safe_str(topic.get("summary")),
        "source_id": _safe_str(topic.get("source_id")),
        "source_kind": _safe_str(topic.get("source_kind")),
        "source_thread_id": thread_id,
        "source_beat_id": beat_id,
        "location_id": _safe_str(topic.get("location_id")),
        "tick": int(tick or 0),
        "source": "deterministic_conversation_effect_runtime",
    }


def validate_conversation_effects(
    conversation_result: Dict[str, Any],
    *,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    conversation_result = _safe_dict(conversation_result)
    settings = _safe_dict(settings)
    violations: List[str] = []

    for key in FORBIDDEN_CONVERSATION_RESULT_KEYS:
        value = conversation_result.get(key)
        if value:
            violations.append(f"forbidden_key:{key}")

    signal = _safe_dict(conversation_result.get("world_signal"))
    if signal:
        signal_kind = _safe_str(signal.get("kind"))
        if signal_kind not in ALLOWED_SIGNAL_KINDS:
            violations.append(f"invalid_signal_kind:{signal_kind}")
        cap = max(1, _safe_int(settings.get("signal_strength_cap"), 1))
        if _safe_int(signal.get("strength"), 0) > cap:
            violations.append("signal_strength_exceeds_cap")

    topic = _safe_dict(conversation_result.get("topic"))
    if topic:
        topic_type = _safe_str(topic.get("topic_type"))
        if topic_type == "quest" and not settings.get("allow_quest_discussion", True):
            violations.append("quest_discussion_disabled")
        if topic_type == "recent_event" and not settings.get("allow_event_discussion", True):
            violations.append("event_discussion_disabled")
        if topic_type == "rumor" and not settings.get("allow_rumor_discussion", True):
            violations.append("rumor_discussion_disabled")

    return {
        "ok": not violations,
        "violations": violations,
        "checked_forbidden_keys": sorted(FORBIDDEN_CONVERSATION_RESULT_KEYS),
        "allowed_signal_kinds": sorted(ALLOWED_SIGNAL_KINDS),
        "source": "deterministic_conversation_effect_validator",
    }


def strip_forbidden_conversation_effects(conversation_result: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(_safe_dict(conversation_result))
    for key in FORBIDDEN_CONVERSATION_RESULT_KEYS:
        result.pop(key, None)
    return result