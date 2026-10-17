from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.npc_goal_state import (
    dominant_goal_for_npc,
    record_goal_influence,
    response_style_from_goal,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_RECENT_PLAYER_REPLIES_PER_NPC = 8   # Bundle I cap
MAX_RECENT_CONVERSATION_TOPICS_PER_NPC = 12
MAX_NPC_CONVERSATION_MEMORIES = 20
MAX_GLOBAL_PLAYER_REPLIES = 64


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# ── Synthetic NPC guard ──────────────────────────────────────────────────────

_SYNTHETIC_NPC_PATTERNS = frozenset({
    "room/environment",
    "environment/npcs",
    "environment/npc",
    "the room/environment",
    "environment (general)",
    "npcs (general)",
    "ambient_wait",
})


def _is_synthetic_npc_participant(participant: Dict[str, Any]) -> bool:
    """Return True when the participant is a synthetic / non-NPC entity.

    Synthetic entities must never accumulate familiarity or social history:
    - npc:The Room/Environment
    - npc:Environment/NPCs (General)
    - ambient_wait / observe-environment synthetic targets
    """
    participant = _safe_dict(participant)
    npc_id = _safe_str(participant.get("id") or participant.get("npc_id")).lower()
    name = _safe_str(participant.get("name")).lower()
    combined = f"{npc_id} {name}"
    for pattern in _SYNTHETIC_NPC_PATTERNS:
        if pattern in combined:
            return True
    # Guard: any participant whose id does not start with "npc:" is synthetic
    # unless it has no id at all (handled upstream by the empty-id skip).
    raw_id = _safe_str(participant.get("id") or participant.get("npc_id"))
    if raw_id and not raw_id.lower().startswith("npc:"):
        return True
    return False


# ── Bundle I: relationship helpers ──────────────────────────────────────────


def ensure_conversation_social_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    state = simulation_state.get("conversation_social_state")
    if not isinstance(state, dict):
        state = {}
        simulation_state["conversation_social_state"] = state
    if not isinstance(state.get("npc_state"), dict):
        state["npc_state"] = {}
    if not isinstance(state.get("recent_player_replies"), list):
        state["recent_player_replies"] = []
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    return state


def record_player_joined_conversation(
    simulation_state: Dict[str, Any],
    *,
    tick: int,
    thread: Dict[str, Any],
    player_response: Dict[str, Any],
    topic: Dict[str, Any],
) -> Dict[str, Any]:
    """Record bounded social context from a player reply to an NPC invitation.

    This is presentation/social context only. It does not mutate inventory,
    currency, quests, journal, service stock, location, or combat state.
    """
    state = ensure_conversation_social_state(simulation_state)
    npc_state = _safe_dict(state.get("npc_state"))
    participants = _safe_list(_safe_dict(thread).get("participants"))
    reply_text = _safe_str(_safe_dict(player_response).get("line"))
    topic_id = _safe_str(_safe_dict(topic).get("topic_id") or _safe_dict(thread).get("topic_id"))
    topic_type = _safe_str(_safe_dict(topic).get("topic_type") or _safe_dict(thread).get("topic_type"))

    entry = {
        "reply_id": _safe_str(_safe_dict(player_response).get("beat_id")),
        "thread_id": _safe_str(_safe_dict(thread).get("thread_id")),
        "topic_id": topic_id,
        "topic_type": topic_type,
        "reply_preview": reply_text[:220],
        "tick": int(tick or 0),
        "source": "deterministic_conversation_social_state",
    }

    recent = _safe_list(state.get("recent_player_replies"))
    recent.append(deepcopy(entry))
    state["recent_player_replies"] = recent[-MAX_GLOBAL_PLAYER_REPLIES:]

    updated_npcs: List[str] = []
    skipped_synthetic: List[str] = []
    for participant in participants:
        participant = _safe_dict(participant)
        npc_id = _safe_str(participant.get("id") or participant.get("npc_id"))
        if not npc_id:
            continue
        if _is_synthetic_npc_participant(participant):
            skipped_synthetic.append(npc_id)
            continue
        npc_record = _safe_dict(npc_state.get(npc_id))
        npc_record["npc_id"] = npc_id
        npc_record["npc_name"] = _safe_str(participant.get("name") or npc_record.get("npc_name"))
        npc_record["last_player_reply_tick"] = int(tick or 0)
        npc_record["last_topic_id"] = topic_id
        npc_record["last_topic_type"] = topic_type
        npc_record["familiarity"] = min(100, _safe_int(npc_record.get("familiarity"), 0) + 1)
        replies = _safe_list(npc_record.get("recent_player_replies"))
        replies.append(deepcopy(entry))
        npc_record["recent_player_replies"] = replies[-MAX_RECENT_PLAYER_REPLIES_PER_NPC:]
        # Bundle I: also track conversation topics per NPC (capped)
        topic_entry = {
            "topic_id": topic_id,
            "topic_type": topic_type,
            "topic_title": _safe_str(_safe_dict(topic).get("title") or _safe_dict(topic).get("topic")),
            "tick": int(tick or 0),
        }
        recent_topics = _safe_list(npc_record.get("recent_conversation_topics"))
        recent_topics.append(topic_entry)
        npc_record["recent_conversation_topics"] = recent_topics[-MAX_RECENT_CONVERSATION_TOPICS_PER_NPC:]
        npc_state[npc_id] = npc_record
        updated_npcs.append(npc_id)
    state["npc_state"] = npc_state
    state["debug"] = {
        "last_update": "player_joined_conversation",
        "updated_npc_ids": updated_npcs,
        "skipped_synthetic_ids": skipped_synthetic,
        "tick": int(tick or 0),
    }
    return deepcopy(state)


def choose_npc_response_style(
    simulation_state: Dict[str, Any],
    *,
    thread: Dict[str, Any],
    player_response: Dict[str, Any],
    topic_pivot: Dict[str, Any],
    tick: int = 0,
    settings: Dict[str, Any] | None = None,
) -> str:
    """Return a deterministic response style for the NPC replying to the player.

    Priority order:
    1. Topic pivot accepted + familiarity ≥ 2 → "helpful"
    2. Topic requested but rejected → "evasive"
    3. NPC goal style (if allow_npc_goal_influence)
    4. Familiarity ladder
    """
    settings = _safe_dict(settings)
    state = ensure_conversation_social_state(simulation_state)
    participants = _safe_list(_safe_dict(thread).get("participants"))
    npc_ids = [
        _safe_str(participant.get("id"))
        for participant in participants
        if _safe_str(participant.get("id")).startswith("npc:")
    ]
    npc_state = _safe_dict(state.get("npc_state"))
    familiarities = [
        _safe_int(_safe_dict(npc_state.get(npc_id)).get("familiarity"), 0)
        for npc_id in npc_ids
    ]
    avg_familiarity = sum(familiarities) / len(familiarities) if familiarities else 0

    if _safe_dict(topic_pivot).get("accepted") and avg_familiarity >= 2:
        return "helpful"
    if _safe_dict(topic_pivot).get("requested") and not _safe_dict(topic_pivot).get("accepted"):
        return "evasive"
    if settings.get("allow_npc_goal_influence", True) and npc_ids:
        goal = dominant_goal_for_npc(
            simulation_state,
            npc_ids[0],
            tick=tick,
            location_id=_safe_str(_safe_dict(thread).get("location_id")),
        )
        goal_style = response_style_from_goal(goal)
        if goal_style:
            record_goal_influence(
                simulation_state,
                tick=tick,
                npc_id=npc_ids[0],
                goal=goal,
                influence_kind="npc_response_style",
                details={"response_style": goal_style},
            )
            return goal_style
    if avg_familiarity >= 3:
        return "friendly"
    if avg_familiarity <= 0:
        return "guarded"
    return "neutral"


def record_npc_response_beat(
    simulation_state: Dict[str, Any],
    *,
    beat: Dict[str, Any],
    tick: int = 0,
) -> Dict[str, Any]:
    """Record an NPC response beat reference in social state debug."""
    state = ensure_conversation_social_state(simulation_state)
    debug = _safe_dict(state.get("debug"))
    debug["last_npc_response_beat"] = {
        "beat_id": _safe_str(_safe_dict(beat).get("beat_id")),
        "speaker_id": _safe_str(_safe_dict(beat).get("speaker_id")),
        "response_style": _safe_str(_safe_dict(beat).get("response_style")),
        "tick": int(tick or 0),
    }
    state["debug"] = debug
    return deepcopy(_safe_dict(beat))
