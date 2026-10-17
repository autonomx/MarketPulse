from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.conversation_topics import conversation_topics_for_state
from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_goal_state import active_goals_for_npc
from app.apps.rpg.world.npc_presence_runtime import (
    update_present_npcs_for_location,
)
from app.apps.rpg.world.scene_continuity_state import scene_continuity_for_location
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

DEFAULT_LOCATION_NPCS = {
    # Conservative fallbacks only. Do not introduce authority/guest NPCs such
    # as GuardCaptain unless the current scene/presence state explicitly lists
    # them as present.
    "loc_tavern": ["npc:Bran", "npc:Mira"],
    "loc_market": ["npc:Merchant"],
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_npc_id(value: Any) -> str:
    text = _safe_str(value).strip()
    if not text:
        return ""
    if text.startswith("npc:"):
        return text
    if text == "player":
        return "player"
    return f"npc:{text}"


def _extract_npc_ids_from_value(value: Any) -> List[str]:
    found: List[str] = []
    if isinstance(value, str):
        npc_id = _normalize_npc_id(value)
        if npc_id.startswith("npc:"):
            found.append(npc_id)
        return found
    if isinstance(value, dict):
        for key in ("npc_id", "id", "character_id", "actor_id", "speaker_id", "listener_id"):
            npc_id = _normalize_npc_id(value.get(key))
            if npc_id.startswith("npc:"):
                found.append(npc_id)
        return found
    if isinstance(value, list):
        for item in value:
            found.extend(_extract_npc_ids_from_value(item))
    return found


def present_npcs_for_location(simulation_state: Dict[str, Any], *, location_id: str = "") -> List[str]:
    simulation_state = _safe_dict(simulation_state)
    location_id = _safe_str(location_id or current_location_id(simulation_state))

    ordered: List[str] = []

    def add_many(values: List[str]) -> None:
        for npc_id in values:
            npc_id = _normalize_npc_id(npc_id)
            if npc_id.startswith("npc:") and npc_id not in ordered:
                ordered.append(npc_id)

    # 1. Explicit presence state by location.
    present_state = _safe_dict(simulation_state.get("present_npc_state"))
    explicit_for_location = present_state.get(location_id)
    add_many(_extract_npc_ids_from_value(explicit_for_location))

    # 2. Common alternate presence roots used by different phases.
    for root_name in (
        "location_npc_state",
        "scene_npc_state",
        "npc_presence_state",
        "location_presence_state",
    ):
        root = _safe_dict(simulation_state.get(root_name))
        add_many(_extract_npc_ids_from_value(root.get(location_id)))
        add_many(_extract_npc_ids_from_value(root.get("present")))
        add_many(_extract_npc_ids_from_value(root.get("npcs")))

    # 3. Existing active conversation participants at this location are valid
    # only if already present in state.
    conversation_state = _safe_dict(simulation_state.get("conversation_thread_state"))
    for thread in _safe_list(conversation_state.get("threads")):
        thread = _safe_dict(thread)
        if _safe_str(thread.get("location_id") or location_id) != location_id:
            continue
        add_many(_extract_npc_ids_from_value(thread.get("participants")))

    # 4. Conservative scene fallback.
    if not ordered:
        add_many(DEFAULT_LOCATION_NPCS.get(location_id, []))

    return ordered


def _present_npcs(simulation_state: Dict[str, Any]) -> List[str]:
    return present_npcs_for_location(
        simulation_state,
        location_id=current_location_id(simulation_state),
    )


def _topic_score_for_npc(
    *,
    npc_id: str,
    topic: Dict[str, Any],
    simulation_state: Dict[str, Any],
) -> int:
    topic = _safe_dict(topic)
    topic_type = _safe_str(topic.get("topic_type"))
    bio = get_npc_biography(npc_id)
    role = _safe_str(bio.get("role")).lower()
    knowledge = _safe_dict(bio.get("knowledge_boundaries"))
    knows_about = " ".join(_safe_str(v).lower() for v in _safe_list(knowledge.get("knows_about")))
    summary = " ".join(
        _safe_str(topic.get(key)).lower()
        for key in ("title", "summary", "topic_type", "source_kind")
    )

    score = _safe_int(topic.get("priority"), 0)

    if topic_type == "quest":
        score += 2
    if topic_type == "recent_event":
        score += 1
    if topic_type == "rumor":
        score += 1

    if "tavern" in role and any(word in summary for word in ("tavern", "traveler", "road", "debt", "lodging")):
        score += 2
    if "informant" in role and any(word in summary for word in ("rumor", "pattern", "whisper", "avoid")):
        score += 2
    if "guard" in role and any(word in summary for word in ("danger", "road", "armed", "report", "trouble")):
        score += 2
    if "merchant" in role and any(word in summary for word in ("market", "road", "trade", "price", "goods")):
        score += 2

    for token in knows_about.split():
        if len(token) > 3 and token in summary:
            score += 1
            break

    for goal in active_goals_for_npc(simulation_state, npc_id)[:3]:
        kind = _safe_str(goal.get("kind"))
        if kind == "maintain_order" and topic_type in {"recent_event", "quest"}:
            score += 1
        if kind == "gather_rumors" and topic_type in {"rumor", "recent_event", "quest"}:
            score += 1
        if kind == "warn_player" and topic_type in {"quest", "recent_event"}:
            score += 1

    return score


def _director_cooldown_key(speaker_id: str, listener_id: str, topic_id: str) -> str:
    return f"{speaker_id}->{listener_id}:{topic_id}"


def ensure_conversation_director_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("conversation_director_state"))
    if not isinstance(state.get("cooldowns"), dict):
        state["cooldowns"] = {}
    if not isinstance(state.get("recent_intents"), list):
        state["recent_intents"] = []
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["conversation_director_state"] = state
    return state


def select_conversation_intent(
    simulation_state: Dict[str, Any],
    *,
    settings: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    state = ensure_conversation_director_state(simulation_state)
    location_id = current_location_id(simulation_state)
    if settings.get("npc_presence_enabled", True):
        update_present_npcs_for_location(
            simulation_state,
            location_id=location_id,
            tick=tick,
        )
    npcs = _present_npcs(simulation_state)
    present_set = set(npcs)
    topics = conversation_topics_for_state(simulation_state, settings=settings)
    cooldowns = _safe_dict(state.get("cooldowns"))
    cooldown_ticks = max(1, _safe_int(settings.get("conversation_director_cooldown_ticks"), 4))

    if len(npcs) < 2 or not topics:
        state["debug"] = {
            "selected": False,
            "reason": "not_enough_npcs_or_topics",
            "npc_count": len(npcs),
            "topic_count": len(topics),
            "tick": int(tick or 0),
        }
        return {"selected": False, "reason": "not_enough_npcs_or_topics"}

    continuity = scene_continuity_for_location(simulation_state, location_id=location_id)
    recent_focus_ids = {
        _safe_str(focus.get("topic_id"))
        for focus in _safe_list(continuity.get("recent_focus"))
    }

    candidates: List[Dict[str, Any]] = []
    for speaker_id in npcs:
        for listener_id in npcs:
            if listener_id == speaker_id:
                continue
            for topic in topics[:8]:
                topic_id = _safe_str(topic.get("topic_id"))
                key = _director_cooldown_key(speaker_id, listener_id, topic_id)
                cooldown_until = _safe_int(cooldowns.get(key), 0)
                if cooldown_until and int(tick or 0) < cooldown_until:
                    continue
                score = _topic_score_for_npc(
                    npc_id=speaker_id,
                    topic=topic,
                    simulation_state=simulation_state,
                )
                if topic_id in recent_focus_ids:
                    score += 1
                candidates.append(
                    {
                        "speaker_id": speaker_id,
                        "listener_id": listener_id,
                        "topic": topic,
                        "score": score,
                        "cooldown_key": key,
                    }
                )

    if not candidates:
        state["debug"] = {
            "selected": False,
            "reason": "all_candidates_on_cooldown",
            "tick": int(tick or 0),
        }
        return {"selected": False, "reason": "all_candidates_on_cooldown"}

    candidates.sort(
        key=lambda item: (
            _safe_int(item.get("score"), 0),
            _safe_str(item.get("speaker_id")),
            _safe_str(item.get("listener_id")),
            _safe_str(_safe_dict(item.get("topic")).get("topic_id")),
        ),
        reverse=True,
    )
    selected = candidates[0]
    topic = _safe_dict(selected.get("topic"))

    intent = {
        "selected": True,
        "speaker_id": _safe_str(selected.get("speaker_id")),
        "listener_id": _safe_str(selected.get("listener_id")),
        "topic_id": _safe_str(topic.get("topic_id")),
        "topic_type": _safe_str(topic.get("topic_type")),
        "topic": deepcopy(topic),
        "intent": "discuss_backed_topic",
        "reason": "highest_scored_biography_goal_topic_candidate",
        "priority": _safe_int(selected.get("score"), 0),
        "location_id": location_id,
        "tick": int(tick or 0),
        "source": "deterministic_conversation_director",
    }

    if intent["speaker_id"] not in present_set or intent["listener_id"] not in present_set:
        state["debug"] = {
            "selected": False,
            "reason": "selected_participant_not_present",
            "speaker_id": intent["speaker_id"],
            "listener_id": intent["listener_id"],
            "present_npcs": list(npcs),
            "tick": int(tick or 0),
            "source": "deterministic_conversation_director",
        }
        return {
            "selected": False,
            "reason": "selected_participant_not_present",
            "speaker_id": intent["speaker_id"],
            "listener_id": intent["listener_id"],
            "present_npcs": list(npcs),
            "source": "deterministic_conversation_director",
        }

    cooldowns[_safe_str(selected.get("cooldown_key"))] = int(tick or 0) + cooldown_ticks
    state["cooldowns"] = cooldowns

    recent = _safe_list(state.get("recent_intents"))
    recent.append(intent)
    state["recent_intents"] = recent[-20:]
    state["debug"] = {
        "selected": True,
        "candidate_count": len(candidates),
        "selected_intent": intent,
        "tick": int(tick or 0),
        "source": "deterministic_conversation_director",
    }

    return deepcopy(intent)
