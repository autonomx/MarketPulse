from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.companion_acceptance import (
    get_pending_companion_offer_debug,
    resolve_pending_companion_offer_response,
)
from app.apps.rpg.world.companion_dialogue import (
    build_companion_join_dialogue,
    build_companion_presence_summary,
)
from app.apps.rpg.world.conversation_settings import normalize_conversation_settings
from app.apps.rpg.rules.location_registry import present_npcs_for_current_location
from app.apps.rpg.world.npc_dialogue_profile import build_npc_dialogue_profile
from app.apps.rpg.world.npc_goal_state import (
    dominant_goal_for_npc,
    goal_topic_bias,
    record_goal_influence,
    seed_default_npc_goals,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_CONVERSATION_THREADS = 32
MAX_BEATS_PER_THREAD = 8
MAX_WORLD_SIGNALS = 64


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _player_party_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(
        _safe_dict(
            _safe_dict(simulation_state.get("player_state")).get("party_state")
        )
    )


def _companion_acceptance_runtime_result(
    simulation_state: Dict[str, Any],
    state: Dict[str, Any],
    settings: Dict[str, Any],
    *,
    player_input: str,
    tick: int,
    source_reason: str,
) -> Dict[str, Any]:
    companion_acceptance_result = resolve_pending_companion_offer_response(
        simulation_state,
        player_input=player_input,
        tick=tick,
    )

    if not companion_acceptance_result.get("resolved"):
        return {
            "triggered": False,
            "reason": _safe_str(companion_acceptance_result.get("reason")) or "companion_offer_not_resolved",
            "companion_acceptance_result": deepcopy(companion_acceptance_result),
            "companion_acceptance_debug": get_pending_companion_offer_debug(
                simulation_state,
                player_input=player_input,
            ),
            "source": "deterministic_conversation_thread_runtime",
        }

    npc_id = _safe_str(companion_acceptance_result.get("npc_id"))
    eligibility = _safe_dict(
        companion_acceptance_result.get("party_join_eligibility_result")
    )
    npc_name = (
        _safe_str(eligibility.get("name"))
        or npc_id.replace("npc:", "")
        or "Companion"
    )

    companion_dialogue_result: Dict[str, Any] = {}
    if (
        settings.get("companion_dialogue_enabled", True)
        and companion_acceptance_result.get("accepted")
    ):
        companion_dialogue_result = build_companion_join_dialogue(
            npc_id=npc_id,
            npc_name=npc_name,
            acceptance_result=companion_acceptance_result,
        )

    npc_response_beat = {}
    if companion_dialogue_result.get("created"):
        npc_response_beat = deepcopy(
            _safe_dict(companion_dialogue_result.get("beat"))
        )

    state["debug"] = {
        "last_triggered": True,
        "reason": "pending_companion_offer_resolved",
        "source_reason": source_reason,
        "participation_mode": "companion_acceptance",
        "npc_id": npc_id,
        "accepted": bool(companion_acceptance_result.get("accepted")),
        "rejected": bool(companion_acceptance_result.get("rejected")),
        "tick": int(tick or 0),
        "source": "deterministic_conversation_thread_runtime",
    }

    return {
        "triggered": True,
        "reason": "pending_companion_offer_resolved",
        "source_reason": source_reason,
        "autonomous": False,
        "participation_mode": "companion_acceptance",
        "companion_acceptance_result": deepcopy(companion_acceptance_result),
        "companion_dialogue_result": deepcopy(companion_dialogue_result),
        "companion_presence_summary": deepcopy(
            build_companion_presence_summary(simulation_state)
        ),
        "companion_acceptance_state": deepcopy(
            _safe_dict(simulation_state.get("companion_acceptance_state"))
        ),
        "companion_acceptance_debug": get_pending_companion_offer_debug(
            simulation_state,
            player_input=player_input,
        ),
        "party_state": _player_party_state(simulation_state),
        "npc_response_beat": npc_response_beat,
        "conversation_thread_state": get_conversation_thread_state(simulation_state),
        "source": "deterministic_conversation_thread_runtime",
    }


def ensure_conversation_thread_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    state = simulation_state.get("conversation_thread_state")
    if not isinstance(state, dict):
        state = {}
        simulation_state["conversation_thread_state"] = state

    if not isinstance(state.get("threads"), list):
        state["threads"] = []
    if not isinstance(state.get("active_thread_ids"), list):
        state["active_thread_ids"] = []
    if not isinstance(state.get("world_signals"), list):
        state["world_signals"] = []
    if not isinstance(state.get("pending_player_response"), dict):
        state["pending_player_response"] = {}
    if not isinstance(state.get("cooldowns"), dict):
        state["cooldowns"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    return state


def get_conversation_thread_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(ensure_conversation_thread_state(simulation_state))





def _participant_key(npc: Dict[str, Any]) -> str:
    return _safe_str(npc.get("id") or npc.get("npc_id") or npc.get("name"))


def _normalize_present_npc(npc: Dict[str, Any]) -> Dict[str, Any]:
    npc = _safe_dict(npc)
    npc_id = _safe_str(npc.get("id") or npc.get("npc_id"))
    name = _safe_str(npc.get("name"))
    if not npc_id and name:
        npc_id = f"npc:{name}"
    return {
        "id": npc_id,
        "name": name or npc_id.replace("npc:", ""),
        "role": _safe_str(npc.get("role")),
    }


def select_conversation_participants(
    simulation_state: Dict[str, Any],
    *,
    limit: int = 2,
) -> List[Dict[str, Any]]:
    """Select participants only from NPCs present at current location."""
    present = [
        _normalize_present_npc(npc)
        for npc in present_npcs_for_current_location(simulation_state)
    ]
    present = [npc for npc in present if _participant_key(npc)]
    present.sort(key=lambda npc: (_safe_str(npc.get("id")), _safe_str(npc.get("name"))))
    return deepcopy(present[:limit])


def _topic_for_location(location_id: str) -> Dict[str, str]:
    if location_id == "loc_tavern":
        return {
            "topic_id": "tavern_evening_rumors",
            "topic": "the mood in the tavern",
            "signal_kind": "rumor_interest",
            "summary": "The tavern staff trade quiet observations about travelers and local rumors.",
        }
    if location_id == "loc_market":
        return {
            "topic_id": "market_trade_pressure",
            "topic": "market traffic",
            "signal_kind": "market_pressure",
            "summary": "Market workers comment on stock, traffic, and the day's trade.",
        }
    return {
        "topic_id": "local_activity",
        "topic": "local activity",
        "signal_kind": "ambient_interest",
        "summary": "Nearby NPCs exchange quiet comments about the area.",
    }


def _line_for_participant(
    participant: Dict[str, Any],
    *,
    location_id: str,
    beat_index: int,
) -> str:
    if location_id == "loc_tavern":
        if beat_index % 2 == 1:
            return "The room has been busier than usual tonight."
        return "Travelers always bring more stories than coin."
    if location_id == "loc_market":
        if beat_index % 2 == 1:
            return "The market crowd is moving quickly today."
        return "Busy stalls mean both opportunity and trouble."
    if beat_index % 2 == 1:
        return "The mood nearby is shifting."
    return "Best keep the exchange grounded in what is happening here."


def _thread_id_for(
    *,
    location_id: str,
    participants: List[Dict[str, Any]],
) -> str:
    participant_ids = [
        _safe_str(participant.get("npc_id") or participant.get("id"))
        for participant in participants
        if _safe_str(participant.get("npc_id") or participant.get("id"))
    ]

    # NPC-to-NPC conversations should have a stable thread identity regardless
    # of who speaks first on a given tick. Bran->Mira and Mira->Bran are the
    # same conversation thread; individual beats still preserve direction via
    # speaker_id/listener_id.
    npc_ids = [value for value in participant_ids if value.startswith("npc:")]
    non_npc_ids = [value for value in participant_ids if not value.startswith("npc:")]
    if len(npc_ids) >= 2 and not non_npc_ids:
        participant_ids = sorted(set(npc_ids))

    return f"conversation:{location_id}:{':'.join(participant_ids)}"


def _find_thread(state: Dict[str, Any], thread_id: str) -> Dict[str, Any]:
    for thread in _safe_list(state.get("threads")):
        thread = _safe_dict(thread)
        if _safe_str(thread.get("thread_id")) == thread_id:
            return thread
    return {}


def _append_world_signal(
    state: Dict[str, Any],
    signal: Dict[str, Any],
) -> Dict[str, Any]:
    signals = _safe_list(state.get("world_signals"))
    signal_id = _safe_str(signal.get("signal_id"))
    if not signal_id:
        return {}
    for existing in signals:
        if _safe_str(_safe_dict(existing).get("signal_id")) == signal_id:
            return deepcopy(_safe_dict(existing))
    signals.append(deepcopy(signal))
    if len(signals) > MAX_WORLD_SIGNALS:
        del signals[:-MAX_WORLD_SIGNALS]
    state["world_signals"] = signals
    return deepcopy(signal)


def _make_beat(
    *,
    thread_id: str,
    participants: List[Dict[str, Any]],
    location_id: str,
    tick: int,
    beat_index: int,
    topic_payload: Dict[str, str],
    participation_mode: str = "overheard",
) -> Dict[str, Any]:
    speaker = participants[(beat_index - 1) % len(participants)]
    listener = participants[beat_index % len(participants)]
    return {
        "beat_id": f"conversation:beat:{tick}:{thread_id}:{beat_index}",
        "thread_id": thread_id,
        "beat_index": beat_index,
        "speaker_id": _safe_str(speaker.get("id")),
        "speaker_name": _safe_str(speaker.get("name")),
        "listener_id": _safe_str(listener.get("id")),
        "listener_name": _safe_str(listener.get("name")),
        "line": _conversation_line_for_topic(
            speaker=speaker,
            location_id=location_id,
            beat_index=beat_index,
            topic=topic_payload,
            participation_mode=participation_mode,
        ),
        "topic_id": _safe_str(topic_payload.get("topic_id")),
        "topic_type": _safe_str(topic_payload.get("topic_type")),
        "topic": _safe_str(topic_payload.get("title") or topic_payload.get("topic")),
        "tick": int(tick or 0),
        "source": "deterministic_conversation_thread_runtime",
    }


def _deterministic_percent(seed: str, tick: int) -> int:
    total = sum(ord(ch) for ch in _safe_str(seed))
    return (total + int(tick or 0) * 37 + 19) % 100


def _select_participation_mode(
    *,
    settings: Dict[str, Any],
    topic: Dict[str, Any],
    tick: int,
    force_player_mode: str = "",
) -> str:
    if force_player_mode in {"overheard", "player_addressed", "player_invited"}:
        return force_player_mode

    settings = normalize_conversation_settings(settings)
    if not settings.get("allow_player_addressed") and not settings.get("allow_player_invited"):
        return "overheard"

    chance = int(settings.get("player_inclusion_chance_percent") or 0)
    if chance <= 0:
        return "overheard"

    bucket = _deterministic_percent(_safe_str(topic.get("topic_id")), tick)
    if bucket >= chance:
        return "overheard"

    if settings.get("allow_player_invited"):
        return "player_invited"
    if settings.get("allow_player_addressed"):
        return "player_addressed"
    return "overheard"


def _apply_goal_bias_to_player_inclusion(
    simulation_state: Dict[str, Any],
    *,
    settings: Dict[str, Any],
    participants: List[Dict[str, Any]],
    tick: int,
    location_id: str,
) -> Dict[str, Any]:
    """Adjust player_inclusion_chance_percent based on the dominant NPC goal."""
    if not settings.get("allow_npc_goal_influence", True):
        return {"settings": settings, "goal_bias": 0, "goal": {}}
    seed_default_npc_goals(simulation_state, tick=tick, location_id=location_id)
    speaker = _safe_dict(participants[0] if participants else {})
    npc_id = _safe_str(speaker.get("id"))
    goal = dominant_goal_for_npc(simulation_state, npc_id, tick=tick, location_id=location_id)
    bias_payload = goal_topic_bias(goal)
    cap = max(0, _safe_int(settings.get("goal_player_invitation_bias_cap"), 20))
    bias = max(-cap, min(cap, _safe_int(bias_payload.get("player_invitation_bias"), 0)))
    if not bias:
        return {"settings": settings, "goal_bias": 0, "goal": goal}
    adjusted = dict(settings)
    adjusted["player_inclusion_chance_percent"] = max(
        0,
        min(100, _safe_int(settings.get("player_inclusion_chance_percent"), 0) + bias),
    )
    record_goal_influence(
        simulation_state,
        tick=tick,
        npc_id=npc_id,
        goal=goal,
        influence_kind="player_invitation_bias",
        details={"bias": bias, "adjusted_chance": adjusted["player_inclusion_chance_percent"]},
    )
    return {"settings": adjusted, "goal_bias": bias, "goal": goal}


def _player_participation_payload(
    *,
    mode: str,
    topic: Dict[str, Any],
    tick: int,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    pending = mode == "player_invited"
    return {
        "included": mode in {"player_addressed", "player_invited", "player_joined"},
        "mode": mode,
        "pending_response": pending,
        "prompt": (
            f"NPCs invite your response about {_safe_str(topic.get('title') or topic.get('topic_id'))}."
            if pending
            else ""
        ),
        "topic_id": _safe_str(topic.get("topic_id")),
        "created_tick": int(tick or 0) if pending else 0,
        "expires_tick": int(tick or 0) + int(settings.get("pending_response_timeout_ticks") or 3) if pending else 0,
    }


def _thread_on_cooldown(
    state: Dict[str, Any],
    *,
    thread_id: str,
    tick: int,
) -> bool:
    cooldowns = _safe_dict(state.get("cooldowns"))
    until = _safe_int(cooldowns.get(thread_id), 0)
    return bool(until and int(tick or 0) < until)


def _apply_forced_player_invite_to_thread(
    *,
    state: Dict[str, Any],
    thread: Dict[str, Any],
    topic_payload: Dict[str, Any],
    tick: int,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    """Force a pending player response onto an active thread.

    This is used only for explicit player-invited ticks. It does not mutate
    inventory, currency, quests, journal, location, stock, rewards, or combat.
    """
    thread = _safe_dict(thread)
    state = _safe_dict(state)
    topic_payload = _safe_dict(topic_payload)

    thread_id = _safe_str(thread.get("thread_id"))
    topic_id = _safe_str(
        topic_payload.get("topic_id")
        or thread.get("topic_id")
    )
    topic_type = _safe_str(
        topic_payload.get("topic_type")
        or thread.get("topic_type")
    )
    prompt = _safe_str(
        topic_payload.get("prompt")
        or topic_payload.get("summary")
        or topic_payload.get("title")
        or thread.get("topic")
        or "The NPC invites your response."
    )

    timeout_ticks = max(
        1,
        _safe_int(settings.get("pending_response_timeout_ticks"), 3),
    )
    created_tick = int(tick or 0)
    expires_tick = created_tick + timeout_ticks

    pending = {
        "thread_id": thread_id,
        "topic_id": topic_id,
        "topic_type": topic_type,
        "prompt": prompt[:280],
        "created_tick": created_tick,
        "expires_tick": expires_tick,
        "source": "deterministic_forced_player_invite_runtime",
    }

    participation = _safe_dict(thread.get("player_participation"))
    participation.update(
        {
            "included": True,
            "mode": "player_invited",
            "pending_response": True,
            "prompt": pending["prompt"],
            "topic_id": topic_id,
            "topic_type": topic_type,
            "created_tick": created_tick,
            "expires_tick": expires_tick,
        }
    )

    thread["participation_mode"] = "player_invited"
    thread["player_participation"] = participation
    thread["updated_tick"] = created_tick
    state["pending_player_response"] = pending

    return {
        "pending_player_response": deepcopy(pending),
        "player_participation": deepcopy(participation),
        "source": "deterministic_forced_player_invite_runtime",
    }


def _set_thread_cooldown(
    state: Dict[str, Any],
    *,
    thread_id: str,
    tick: int,
    settings: Dict[str, Any],
) -> None:
    cooldowns = _safe_dict(state.get("cooldowns"))
    cooldowns[thread_id] = int(tick or 0) + int(settings.get("thread_cooldown_ticks") or 0)
    state["cooldowns"] = cooldowns


def _conversation_line_for_topic(
    *,
    speaker: Dict[str, Any],
    location_id: str,
    beat_index: int,
    topic: Dict[str, Any],
    participation_mode: str,
) -> str:
    topic_type = _safe_str(topic.get("topic_type"))
    facts = _safe_list(topic.get("allowed_facts"))
    fact = _safe_str(facts[0] if facts else topic.get("summary"))
    speaker_id = _safe_str(speaker.get("npc_id") or speaker.get("id"))
    profile = build_npc_dialogue_profile(
        npc_id=speaker_id,
        simulation_state={},
        runtime_state={},
        topic=topic,
        listener_id="",
        response_intent="ambient_comment",
    )
    role = _safe_str(profile.get("role")).lower()

    if participation_mode == "player_invited":
        if "tavern" in role:
            return f"You look like you have ears worth using. What do you make of this: {fact}"
        if "informant" in role:
            return f"You heard that too, didn't you? {fact}"
        if "guard" in role:
            return f"If you know anything useful about this, say it plainly: {fact}"
        return f"What do you make of this: {fact}"
    if participation_mode == "player_addressed":
        if "tavern" in role:
            return f"You heard the room turning that over too: {fact}"
        if "informant" in role:
            return f"You noticed the same thread, I expect: {fact}"
        if "guard" in role:
            return f"You heard the report as well: {fact}"
        return f"You heard the talk about this too: {fact}"

    if topic_type == "quest":
        if "tavern" in role:
            return f"People do not avoid a road for nothing. {fact}"
        if "informant" in role:
            return f"That keeps coming up in whispers: {fact}"
        if "guard" in role:
            return f"Reports agree on this much: {fact}"
        return f"I keep hearing about it: {fact}"
    if topic_type == "recent_event":
        if "tavern" in role:
            return f"It has the room talking into their cups: {fact}"
        if "informant" in role:
            return f"Everyone repeats it differently, but this part stays the same: {fact}"
        if "guard" in role:
            return f"Recent reports mention this: {fact}"
        return f"That recent trouble still has people talking: {fact}"
    if topic_type == "rumor":
        if "tavern" in role:
            return f"Taverns breed rumors, but this one keeps returning: {fact}"
        if "informant" in role:
            return f"The rumor is not proof, but it has a shape: {fact}"
        if "guard" in role:
            return f"I would call that unverified, but worth noting: {fact}"
        return f"Rumor has it: {fact}"
    if topic_type == "memory":
        return f"People remember this clearly: {fact}"

    return _line_for_participant(
        speaker,
        location_id=location_id,
        beat_index=beat_index,
    )
