from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_QUEST_RUMORS = 24
DEFAULT_QUEST_RUMOR_TTL_TICKS = 120


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_quest_rumor_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("quest_rumor_state"))
    if not isinstance(state.get("rumors"), list):
        state["rumors"] = []
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["quest_rumor_state"] = state
    return state


def prune_quest_rumors(
    simulation_state: Dict[str, Any],
    *,
    current_tick: int,
) -> Dict[str, Any]:
    state = ensure_quest_rumor_state(simulation_state)
    kept = []
    expired = []

    for rumor in _safe_list(state.get("rumors")):
        rumor = _safe_dict(rumor)
        expires_tick = _safe_int(rumor.get("expires_tick"), 0)
        if expires_tick and int(current_tick or 0) >= expires_tick:
            expired.append(_safe_str(rumor.get("rumor_id")))
            continue
        kept.append(rumor)

    state["rumors"] = kept[:MAX_QUEST_RUMORS]
    state["debug"] = {
        **_safe_dict(state.get("debug")),
        "last_prune_tick": int(current_tick or 0),
        "expired_rumor_ids": expired,
        "source": "deterministic_quest_rumor_runtime",
    }

    return {
        "expired_rumor_ids": expired,
        "source": "deterministic_quest_rumor_runtime",
    }


def maybe_seed_quest_rumor_from_conversation(
    simulation_state: Dict[str, Any],
    *,
    conversation_result: Dict[str, Any],
    tick: int,
    ttl_ticks: int = DEFAULT_QUEST_RUMOR_TTL_TICKS,
) -> Dict[str, Any]:
    conversation = _safe_dict(conversation_result)
    topic_pivot = _safe_dict(conversation.get("topic_pivot"))
    quest_access = _safe_dict(conversation.get("quest_conversation_access"))
    requested_access = _safe_dict(conversation.get("requested_topic_access"))

    topic = _safe_dict(
        conversation.get("topic_payload")
        or _safe_dict(conversation.get("thread")).get("topic_payload")
        or _safe_dict(conversation.get("npc_response_beat")).get("topic_payload")
    )

    topic_id = _safe_str(
        quest_access.get("topic_id")
        or topic.get("topic_id")
        or conversation.get("topic_id")
    )
    topic_type = _safe_str(
        quest_access.get("topic_type")
        or topic.get("topic_type")
        or conversation.get("topic_type")
    )

    if requested_access.get("requested") and requested_access.get("access") == "none":
        return {
            "created": False,
            "reason": "requested_topic_unbacked",
            "source": "deterministic_quest_rumor_runtime",
        }

    if topic_pivot.get("requested") and not topic_pivot.get("accepted"):
        return {
            "created": False,
            "reason": "topic_pivot_rejected",
            "source": "deterministic_quest_rumor_runtime",
        }

    if topic_type != "quest" and "quest" not in topic_id:
        return {
            "created": False,
            "reason": "not_quest_topic",
            "source": "deterministic_quest_rumor_runtime",
        }

    summary = _safe_str(topic.get("summary") or topic.get("title"))
    if not topic_id or not summary:
        return {
            "created": False,
            "reason": "missing_backed_topic",
            "source": "deterministic_quest_rumor_runtime",
        }

    state = ensure_quest_rumor_state(simulation_state)
    rumors = _safe_list(state.get("rumors"))
    current_tick = int(tick or 0)

    rumor_id = f"quest_rumor:{topic_id}"
    existing = [
        _safe_dict(rumor)
        for rumor in rumors
        if _safe_str(_safe_dict(rumor).get("rumor_id")) != rumor_id
    ]

    rumor = {
        "rumor_id": rumor_id,
        "topic_id": topic_id,
        "topic_type": topic_type,
        "summary": summary[:280],
        "source_kind": "quest_conversation",
        "seed_tick": current_tick,
        "expires_tick": current_tick + max(1, int(ttl_ticks or DEFAULT_QUEST_RUMOR_TTL_TICKS)),
        "speaker_id": _safe_str(_safe_dict(conversation.get("npc_response_beat")).get("speaker_id")),
        "source": "deterministic_quest_rumor_runtime",
    }

    existing.insert(0, rumor)
    state["rumors"] = existing[:MAX_QUEST_RUMORS]

    return {
        "created": True,
        "rumor": deepcopy(rumor),
        "source": "deterministic_quest_rumor_runtime",
    }


