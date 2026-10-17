from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_dialogue_profile import (
    build_npc_dialogue_profile,
    deterministic_biography_line,
)
from app.apps.rpg.world.npc_dialogue_recall import (
    find_recall_capable_npc,
    player_input_requests_recall,
)
from app.apps.rpg.world.npc_presence_runtime import present_npcs_at_location

from .conversation_thread_base import (
    MAX_BEATS_PER_THREAD,
    MAX_CONVERSATION_THREADS,
    _find_thread,
    get_conversation_thread_state,
)
from app.apps.rpg.foundation.safe_values import (
    safe_dict as _safe_dict,
    safe_list as _safe_list,
    safe_str as _safe_str,
)

def _biography_grounded_npc_response(
    *,
    speaker_id: str,
    listener_id: str,
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
    player_input: str = "",
    topic: Dict[str, Any] | None = None,
    pivot: Dict[str, Any] | None = None,
    response_style: str = "",
    response_intent: str = "answer",
) -> Dict[str, Any]:
    profile_runtime_state = dict(_safe_dict(runtime_state))
    profile_runtime_state["player_input"] = _safe_str(player_input)
    profile_runtime_state["latest_player_input"] = _safe_str(player_input)
    profile_runtime_state.setdefault("enable_dialogue_recall", True)

    profile = build_npc_dialogue_profile(
        npc_id=speaker_id,
        simulation_state=simulation_state,
        runtime_state=profile_runtime_state,
        topic=topic or {},
        listener_id=listener_id,
        response_intent=response_intent,
    )
    line_payload = deterministic_biography_line(
        profile=profile,
        topic=topic or {},
        pivot=pivot or {},
        response_style=response_style,
    )
    return {
        "profile": profile,
        "line_payload": line_payload,
        "line": _safe_str(line_payload.get("line")),
        "biography_role": _safe_str(line_payload.get("biography_role")),
        "roleplay_source": _safe_str(line_payload.get("roleplay_source") or "deterministic_template"),
        "used_fact_ids": _safe_list(line_payload.get("used_fact_ids")),
        "dialogue_recall": _safe_dict(profile.get("dialogue_recall")),
        "recalled_history_ids": [
            _safe_str(recall.get("source_history_id"))
            for recall in _safe_list(_safe_dict(profile.get("dialogue_recall")).get("recalls"))
            if _safe_str(recall.get("source_history_id"))
        ],
        "recalled_knowledge_ids": [
            _safe_str(recall.get("source_knowledge_id"))
            for recall in _safe_list(_safe_dict(profile.get("dialogue_recall")).get("recalls"))
            if _safe_str(recall.get("source_knowledge_id"))
        ],
        "response_style": _safe_str(line_payload.get("response_style") or response_style),
        "source": "deterministic_biography_grounded_npc_response",
    }


def _consume_recall_request_as_conversation_reply(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    tick: int,
    settings: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Handle direct player recall questions even when no pending invite exists.

    This creates a presentation/conversation beat only. It does not mutate
    quest/reward/journal/inventory/currency/location/combat state.
    """
    if not player_input_requests_recall(player_input):
        return {
            "triggered": False,
            "reason": "not_recall_request",
        }

    location_id = current_location_id(simulation_state)
    candidate_npcs = present_npcs_at_location(simulation_state, location_id=location_id)
    if not candidate_npcs:
        candidate_npcs = ["npc:Bran", "npc:Mira"]

    # Prefer a current/recent topic when available, but recall selection can
    # work without topic overlap because the player explicitly asked to recall.
    conversation_state = _safe_dict(simulation_state.get("conversation_thread_state"))
    topic_payload: Dict[str, Any] = {}
    threads = _safe_list(conversation_state.get("threads"))
    if threads:
        latest_thread = _safe_dict(threads[-1])
        topic_payload = _safe_dict(latest_thread.get("topic_payload"))

    recall_choice = find_recall_capable_npc(
        simulation_state,
        candidate_npc_ids=candidate_npcs,
        player_input=player_input,
        topic=topic_payload,
        tick=tick,
    )
    if not recall_choice.get("selected"):
        return {
            "triggered": False,
            "reason": _safe_str(recall_choice.get("reason")) or "no_recall_available",
            "recall_choice": recall_choice,
        }

    responder_id = _safe_str(recall_choice.get("npc_id"))
    responder_bio = get_npc_biography(responder_id)
    responder_name = _safe_str(responder_bio.get("name")) or responder_id.replace("npc:", "")

    # Force the profile to see the recall request text.
    profile_runtime_state = dict(_safe_dict(runtime_state))
    profile_runtime_state["player_input"] = _safe_str(player_input)
    profile_runtime_state["latest_player_input"] = _safe_str(player_input)
    profile_runtime_state["tick"] = int(tick or 0)
    profile_runtime_state["enable_dialogue_recall"] = True

    biography_response = _biography_grounded_npc_response(
        speaker_id=responder_id,
        listener_id="player",
        simulation_state=simulation_state,
        runtime_state=profile_runtime_state,
        player_input=player_input,
        topic=topic_payload,
        pivot={},
        response_style="helpful",
        response_intent="recall",
    )

    dialogue_recall = _safe_dict(biography_response.get("dialogue_recall"))
    recalled_history_ids = _safe_list(biography_response.get("recalled_history_ids"))
    recalled_knowledge_ids = _safe_list(biography_response.get("recalled_knowledge_ids"))
    line = _safe_str(biography_response.get("line"))
    if not line:
        recalls = _safe_list(dialogue_recall.get("recalls"))
        summary = _safe_str(_safe_dict(recalls[0]).get("summary")) if recalls else ""
        line = f"I remember this: {summary}" if summary else "I remember you asking, but not enough to add more."

    thread_id = f"conversation:{location_id}:{responder_id}:player:recall"
    thread = _find_thread(conversation_state, thread_id)
    if not thread:
        thread = {
            "thread_id": thread_id,
            "participants": [
                {"npc_id": responder_id, "name": responder_name},
                {"npc_id": "player", "name": "Player"},
            ],
            "location_id": location_id,
            "topic_id": _safe_str(topic_payload.get("topic_id")),
            "topic_type": _safe_str(topic_payload.get("topic_type")),
            "topic": _safe_str(topic_payload.get("title") or topic_payload.get("summary") or "Recall"),
            "topic_payload": deepcopy(topic_payload),
            "participation_mode": "player_joined",
            "player_participation": {
                "included": True,
                "mode": "player_joined",
                "pending_response": False,
                "topic_id": _safe_str(topic_payload.get("topic_id")),
            },
            "beats": [],
            "status": "active",
            "created_tick": int(tick or 0),
            "updated_tick": int(tick or 0),
            "source": "deterministic_recall_request_runtime",
        }
        threads.append(thread)
        conversation_state["threads"] = threads[-MAX_CONVERSATION_THREADS:]

    player_response_beat = {
        "beat_id": f"conversation:beat:{int(tick or 0)}:{thread_id}:player_recall_request",
        "thread_id": thread_id,
        "speaker_id": "player",
        "speaker_name": "Player",
        "listener_id": responder_id,
        "listener_name": responder_name,
        "line": _safe_str(player_input),
        "topic_id": _safe_str(topic_payload.get("topic_id")),
        "topic_type": _safe_str(topic_payload.get("topic_type")),
        "topic": _safe_str(topic_payload.get("title") or topic_payload.get("summary") or "Recall"),
        "tick": int(tick or 0),
        "participation_mode": "player_joined",
        "source": "deterministic_recall_request_runtime",
    }

    npc_response_beat = {
        "beat_id": f"conversation:beat:{int(tick or 0)}:{thread_id}:npc_recall_response",
        "thread_id": thread_id,
        "speaker_id": responder_id,
        "speaker_name": responder_name,
        "listener_id": "player",
        "listener_name": "Player",
        "line": line,
        "topic_id": _safe_str(topic_payload.get("topic_id")),
        "topic_type": _safe_str(topic_payload.get("topic_type")),
        "topic": _safe_str(topic_payload.get("title") or topic_payload.get("summary") or "Recall"),
        "tick": int(tick or 0),
        "participation_mode": "player_joined",
        "response_style": _safe_str(biography_response.get("response_style") or "helpful"),
        "biography_role": _safe_str(biography_response.get("biography_role")),
        "roleplay_source": _safe_str(biography_response.get("roleplay_source") or "deterministic_template"),
        "used_fact_ids": _safe_list(biography_response.get("used_fact_ids")),
        "dialogue_profile": _safe_dict(biography_response.get("profile")),
        "dialogue_recall": dialogue_recall,
        "recalled_history_ids": recalled_history_ids,
        "recalled_knowledge_ids": recalled_knowledge_ids,
        "source": "deterministic_recall_request_runtime",
    }

    beats = _safe_list(thread.get("beats"))
    beats.extend([player_response_beat, npc_response_beat])
    thread["beats"] = beats[-MAX_BEATS_PER_THREAD:]
    thread["updated_tick"] = int(tick or 0)
    thread["participation_mode"] = "player_joined"
    thread["player_participation"] = {
        "included": True,
        "mode": "player_joined",
        "pending_response": False,
        "topic_id": _safe_str(topic_payload.get("topic_id")),
    }

    conversation_state["pending_player_response"] = {}
    simulation_state["conversation_thread_state"] = conversation_state

    return {
        "triggered": True,
        "reason": "recall_request_consumed",
        "participation_mode": "player_joined",
        "thread": deepcopy(thread),
        "beat": deepcopy(player_response_beat),
        "npc_response_beat": deepcopy(npc_response_beat),
        "player_participation": deepcopy(thread["player_participation"]),
        "dialogue_profile": deepcopy(_safe_dict(npc_response_beat.get("dialogue_profile"))),
        "dialogue_recall": deepcopy(dialogue_recall),
        "recalled_history_ids": recalled_history_ids,
        "recalled_knowledge_ids": recalled_knowledge_ids,
        "conversation_thread_state": get_conversation_thread_state(simulation_state),
        "source": "deterministic_recall_request_runtime",
    }


# ── Bundle H: NPC response beat after player joins ───────────────────────────


def _npc_response_line_for_player_join(
    *,
    topic: Dict[str, Any],
    topic_pivot: Dict[str, Any],
    response_style: str,
    recent_lines: List[str] | None = None,
) -> str:
    """Deterministic NPC reply line after player joins conversation.

    Picks from 3 candidates per style, avoiding lines already in recent_lines.
    Hard constraint: never creates quests, rewards, or inventory changes.
    """
    topic = _safe_dict(topic)
    facts = _safe_list(topic.get("allowed_facts"))
    fact = _safe_str(facts[0] if facts else topic.get("summary"))
    title = _safe_str(topic.get("title") or topic.get("topic") or topic.get("topic_id"))
    recent = {_safe_str(line).strip().lower() for line in (recent_lines or []) if _safe_str(line).strip()}
    candidates: List[str]
    if topic_pivot.get("requested") and not topic_pivot.get("accepted"):
        candidates = [
            "I have no reliable word of that. I will not dress guesses up as fact.",
            "That is not something I can ground in anything known here.",
            "If that tale is true, it has not reached this room as more than smoke.",
        ]
    elif response_style == "helpful":
        candidates = [
            f"Aye. What I know is this: {fact}",
            f"The useful part is this: {fact}",
            f"If you need something solid, start here: {fact}",
        ]
    elif response_style == "friendly":
        candidates = [
            f"I can tell you this much about {title}: {fact}",
            f"Since you ask plain, here it is: {fact}",
            f"Between us, the talk around {title} is simple enough: {fact}",
        ]
    elif response_style == "annoyed":
        candidates = [
            f"Mind your tone. Still, the fact of it is: {fact}",
            f"You ask like you expect trouble. The answer is: {fact}",
            f"Fine. But do not make a scene of it: {fact}",
        ]
    elif response_style == "evasive":
        candidates = [
            f"People are careful when speaking about {title}.",
            f"I would not say more than this: {fact}",
            f"That is the sort of matter best spoken of quietly: {fact}",
        ]
    else:
        candidates = [
            f"That's what folk are saying about {title}: {fact}",
            f"The talk comes back to this: {fact}",
            f"What reaches my ears is this: {fact}",
        ]
    for line in candidates:
        if line.strip().lower() not in recent:
            return line
    return candidates[0]


def _make_npc_response_beat(
    *,
    npc: Dict[str, Any],
    thread: Dict[str, Any],
    topic: Dict[str, Any],
    topic_pivot: Dict[str, Any],
    response_style: str,
    tick: int,
    thread_id: str,
    beat_index: int,
) -> Dict[str, Any]:
    """Build an NPC response beat dict with avoid-repeat logic."""
    recent_lines = [
        _safe_str(beat.get("line"))
        for beat in _safe_list(_safe_dict(thread).get("beats"))
        if _safe_str(beat.get("speaker_id")).startswith("npc:")
    ]
    return {
        "beat_id": f"conversation:npc_response:{int(tick or 0)}:{thread_id}:{beat_index}",
        "thread_id": thread_id,
        "beat_index": beat_index,
        "speaker_id": _safe_str(npc.get("id")),
        "speaker_name": _safe_str(npc.get("name")),
        "listener_id": "player",
        "listener_name": "Player",
        "line": _npc_response_line_for_player_join(
            topic=topic,
            topic_pivot=topic_pivot,
            response_style=response_style,
            recent_lines=recent_lines,
        ),
        "topic_id": _safe_str(topic.get("topic_id")),
        "topic_type": _safe_str(topic.get("topic_type")),
        "topic": _safe_str(topic.get("title") or topic.get("topic")),
        "tick": int(tick or 0),
        "response_style": response_style,
        "participation_mode": "player_joined",
        "source": "deterministic_conversation_thread_runtime",
    }
