from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.ambient_intent import is_ambient_wait_or_listen_intent
from app.apps.rpg.world.companion_acceptance import (
    get_pending_companion_offer_debug,
    hydrate_companion_acceptance_from_pending_offers,
    resolve_pending_companion_offer_response,
)
from app.apps.rpg.world.companion_dialogue import (
    build_companion_join_dialogue,
    build_companion_presence_summary,
)
from app.apps.rpg.world.conversation_director import select_conversation_intent
from app.apps.rpg.world.conversation_effects import (
    build_conversation_world_signal,
    strip_forbidden_conversation_effects,
    validate_conversation_effects,
)
from app.apps.rpg.world.conversation_rumor_propagation import (
    add_rumor_seed,
    expire_stale_signals,
)
from app.apps.rpg.world.conversation_settings import normalize_conversation_settings
from app.apps.rpg.world.conversation_topics import (
    select_conversation_topic,
    topic_is_backed_by_state,
)
from app.apps.rpg.rules.location_registry import (
    current_location_id,
    get_location,
)
from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_dialogue_recall import (
    player_input_requests_recall,
)
from app.apps.rpg.world.npc_goal_state import (
    seed_default_npc_goals,
)
from app.apps.rpg.world.npc_history_state import (
    prune_npc_history_state,
)
from app.apps.rpg.world.npc_knowledge_state import (
    add_npc_knowledge_from_topic,
    prune_npc_knowledge_state,
)
from app.apps.rpg.world.scene_continuity_state import (
    update_scene_continuity_from_conversation,
)
from app.apps.rpg.world.world_event_log import add_world_event

from app.apps.rpg.world.conversation_thread_base import (
    MAX_BEATS_PER_THREAD as MAX_BEATS_PER_THREAD,
    MAX_CONVERSATION_THREADS as MAX_CONVERSATION_THREADS,
    MAX_WORLD_SIGNALS as MAX_WORLD_SIGNALS,
    _append_world_signal as _append_world_signal,
    _apply_forced_player_invite_to_thread as _apply_forced_player_invite_to_thread,
    _apply_goal_bias_to_player_inclusion as _apply_goal_bias_to_player_inclusion,
    _companion_acceptance_runtime_result as _companion_acceptance_runtime_result,
    _conversation_line_for_topic as _conversation_line_for_topic,
    _deterministic_percent as _deterministic_percent,
    _find_thread as _find_thread,
    _line_for_participant as _line_for_participant,
    _make_beat as _make_beat,
    _normalize_present_npc as _normalize_present_npc,
    _participant_key as _participant_key,
    _player_participation_payload as _player_participation_payload,
    _player_party_state as _player_party_state,
    _safe_dict as _safe_dict,
    _safe_int as _safe_int,
    _safe_list as _safe_list,
    _safe_str as _safe_str,
    _select_participation_mode as _select_participation_mode,
    _set_thread_cooldown as _set_thread_cooldown,
    _thread_id_for as _thread_id_for,
    _thread_on_cooldown as _thread_on_cooldown,
    _topic_for_location as _topic_for_location,
    ensure_conversation_thread_state as ensure_conversation_thread_state,
    get_conversation_thread_state as get_conversation_thread_state,
    select_conversation_participants as select_conversation_participants,
)
from app.apps.rpg.world.conversation_thread_responses import (
    _biography_grounded_npc_response as _biography_grounded_npc_response,
    _consume_recall_request_as_conversation_reply as _consume_recall_request_as_conversation_reply,
    _make_npc_response_beat as _make_npc_response_beat,
    _npc_response_line_for_player_join as _npc_response_line_for_player_join,
)
from app.apps.rpg.world.conversation_thread_pending import (
    handle_pending_player_conversation_response as handle_pending_player_conversation_response,
    has_pending_player_conversation_response as has_pending_player_conversation_response,
    maybe_consume_pending_player_response as maybe_consume_pending_player_response,
)

def maybe_advance_conversation_thread(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    tick: int = 0,
    settings: Dict[str, Any] | None = None,
    autonomous: bool = False,
    force: bool = False,
    force_player_mode: str = "",
    forced_topic_type: str = "",
    exclude_event_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """Create/advance one bounded NPC-to-NPC conversation thread.

    Deterministic v1 trigger:
    - player waits/listens/idles/observes
    - current location has at least two present NPCs

    This function intentionally mutates only:
    - conversation_thread_state
    - world_event_state via bounded npc_conversation event
    """
    simulation_state = _safe_dict(simulation_state)
    state = ensure_conversation_thread_state(simulation_state)
    settings = normalize_conversation_settings(settings or {})

    # AL-AM-AN.3:
    # This must happen at the absolute top, before recall, ambient, wait/listen,
    # NPC-count, topic, cooldown, or pending-player-response gates.
    if settings.get("companion_acceptance_enabled", True):
        companion_acceptance_attempt = _companion_acceptance_runtime_result(
            simulation_state,
            state,
            settings,
            player_input=player_input,
            tick=tick,
            source_reason="top_of_maybe_advance_conversation_thread",
        )
        if companion_acceptance_attempt.get("triggered"):
            return companion_acceptance_attempt

    location_id = current_location_id(simulation_state)
    location = get_location(location_id)

    forced_invite_payload = {}

    # J2: expire stale rumor seeds before advancing the conversation.
    expire_stale_signals(simulation_state, current_tick=tick, settings=settings)

    if not settings.get("enabled", True):
        return {
            "triggered": False,
            "reason": "conversation_settings_disabled",
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
        }

    # AL-AM-AN.2:
    # A pending companion offer is not an ambient conversation trigger.
    # It is a deterministic simulation state waiting for the player's explicit
    # yes/no response. Therefore it must be resolved before the normal
    # wait/listen/ambient gate returns "not_wait_or_listen_turn".
    if settings.get("companion_acceptance_enabled", True):
        companion_acceptance_result = resolve_pending_companion_offer_response(
            simulation_state,
            player_input=player_input,
            tick=tick,
        )

        if companion_acceptance_result.get("resolved"):
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

            if companion_dialogue_result.get("created"):
                npc_response_beat = deepcopy(
                    _safe_dict(companion_dialogue_result.get("beat"))
                )
            else:
                npc_response_beat = {}

            state["debug"] = {
                "last_triggered": True,
                "reason": "pending_companion_offer_resolved",
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
                "party_state": _player_party_state(simulation_state),
                "npc_response_beat": npc_response_beat,
                "conversation_thread_state": get_conversation_thread_state(simulation_state),
                "source": "deterministic_conversation_thread_runtime",
            }

    if (
        settings.get("npc_dialogue_recall_enabled", True)
        and not _safe_dict(state.get("pending_player_response"))
        and player_input_requests_recall(player_input)
    ):
        recall_result = _consume_recall_request_as_conversation_reply(
            simulation_state,
            player_input=player_input,
            tick=tick,
            settings=settings,
            runtime_state={"tick": int(tick or 0), "player_input": player_input},
        )
        if recall_result.get("triggered"):
            return recall_result

    if not force and not autonomous and not is_ambient_wait_or_listen_intent(player_input):
        companion_debug = get_pending_companion_offer_debug(
            simulation_state,
            player_input=player_input,
        )
        fallback_attempt: Dict[str, Any] = {}

        if (
            settings.get("companion_acceptance_enabled", True)
            and companion_debug.get("has_any_pending_offer")
            and (companion_debug.get("accepts") or companion_debug.get("rejects"))
        ):
            thread_pending = _safe_dict(
                _safe_dict(simulation_state.get("conversation_thread_state")).get(
                    "pending_companion_offers"
                )
            )
            if thread_pending:
                hydrate_companion_acceptance_from_pending_offers(
                    simulation_state,
                    thread_pending,
                )

            fallback_attempt = _companion_acceptance_runtime_result(
                simulation_state,
                state,
                settings,
                player_input=player_input,
                tick=tick,
                source_reason="fallback_before_not_wait_listen_or_ambient_tick",
            )
            if fallback_attempt.get("triggered"):
                return fallback_attempt

        state["debug"] = {
            "last_triggered": False,
            "reason": "not_wait_listen_or_ambient_tick",
            "location_id": location_id,
        }
        return {
            "triggered": False,
            "reason": "not_wait_listen_or_ambient_tick",
            "companion_acceptance_debug": companion_debug,
            "companion_acceptance_result": deepcopy(
                _safe_dict(fallback_attempt.get("companion_acceptance_result"))
            ),
            "source": "deterministic_conversation_thread_runtime",
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
        }

    participants = select_conversation_participants(simulation_state, limit=2)
    if len(participants) < 2:
        state["debug"] = {
            "last_triggered": False,
            "reason": "not_enough_present_npcs",
            "location_id": location_id,
            "present_npc_count": len(participants),
        }
        return {
            "triggered": False,
            "reason": "not_enough_present_npcs",
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
        }

    seed_default_npc_goals(simulation_state, tick=tick, location_id=location_id)

    # QRS: Prune NPC history before starting a new conversation beat.
    prune_npc_history_state(
        simulation_state,
        current_tick=tick,
        max_entries_per_npc=int(settings.get("npc_history_max_entries_per_npc") or 20),
    )

    # QRS: Ask the conversation director for a preferred intent.
    director_intent: Dict[str, Any] = {}
    if settings.get("conversation_director_enabled", True):
        director_intent = select_conversation_intent(
            simulation_state,
            settings=settings,
            tick=tick,
        )

    if director_intent.get("selected"):
        speaker_npc_id = _safe_str(director_intent.get("speaker_id"))
        listener_npc_id = _safe_str(director_intent.get("listener_id"))
        speaker_bio = get_npc_biography(speaker_npc_id)
        listener_bio = get_npc_biography(listener_npc_id)
        participants = [
            {
                "id": speaker_npc_id,
                "name": _safe_str(speaker_bio.get("name")) or speaker_npc_id.replace("npc:", ""),
                "role": "",
            },
            {
                "id": listener_npc_id,
                "name": _safe_str(listener_bio.get("name")) or listener_npc_id.replace("npc:", ""),
                "role": "",
            },
        ]
        topic_payload = _safe_dict(director_intent.get("topic")) or {}
    else:
        topic_payload = select_conversation_topic(
            simulation_state,
            settings=settings,
            forced_topic_type=forced_topic_type,
            exclude_event_ids=exclude_event_ids or [],
        )
        if not topic_payload:
            topic_payload = _topic_for_location(location_id)
    if not topic_is_backed_by_state(topic_payload):
        return {
            "triggered": False,
            "reason": "conversation_topic_not_backed_by_state",
            "topic": topic_payload,
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
        }
    participation_mode = _select_participation_mode(
        settings=_safe_dict(
            _apply_goal_bias_to_player_inclusion(
                simulation_state,
                settings=settings,
                participants=participants,
                tick=tick,
                location_id=location_id,
            ).get("settings")
        ),
        topic=topic_payload,
        tick=tick,
        force_player_mode=force_player_mode,
    )

    if force_player_mode == "player_invited":
        if not settings.get("allow_player_invited", False):
            return {
                "triggered": False,
                "reason": "forced_player_invited_disabled_by_settings",
                "participation_mode": "overheard",
                "player_participation": {
                    "included": False,
                    "mode": "overheard",
                    "pending_response": False,
                },
                "conversation_thread_state": get_conversation_thread_state(simulation_state),
            }
        participation_mode = "player_invited"
    thread_id = _thread_id_for(location_id=location_id, participants=participants)
    if _thread_on_cooldown(state, thread_id=thread_id, tick=tick) and force_player_mode != "player_invited":
        return {
            "triggered": False,
            "reason": "thread_on_cooldown",
            "thread_id": thread_id,
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
        }
    existing = _find_thread(state, thread_id)

    if existing:
        thread = existing
        thread["last_participants"] = deepcopy(participants)
        participation_mode = _safe_str(thread.get("participation_mode") or participation_mode or "overheard")

        if force_player_mode == "player_invited":
            participation_mode = "player_invited"
    else:
        # Enforce max_active_threads: don't open a new thread when the cap is reached.
        active_ids = _safe_list(state.get("active_thread_ids"))
        max_threads = max(1, _safe_int(settings.get("max_active_threads"), 2))
        if len(active_ids) >= max_threads:
            state["debug"] = {
                "last_triggered": False,
                "reason": "max_active_threads_reached",
                "location_id": location_id,
                "active_thread_count": len(active_ids),
                "max_active_threads": max_threads,
            }
            return {
                "triggered": False,
                "reason": "max_active_threads_reached",
                "active_thread_count": len(active_ids),
                "max_active_threads": max_threads,
                "conversation_thread_state": get_conversation_thread_state(simulation_state),
            }
        canonical_participants = participants
        npc_participants = [
            participant
            for participant in participants
            if _safe_str(participant.get("npc_id") or participant.get("id")).startswith("npc:")
        ]
        if len(npc_participants) >= 2 and len(npc_participants) == len(participants):
            canonical_participants = sorted(
                participants,
                key=lambda participant: _safe_str(participant.get("npc_id") or participant.get("id")),
            )

        thread = {
            "thread_id": thread_id,
            "location_id": location_id,
            "location_name": _safe_str(location.get("name")),
            "participants": canonical_participants,
            "last_participants": participants,
            "topic_id": _safe_str(topic_payload.get("topic_id")),
            "topic_type": _safe_str(topic_payload.get("topic_type")),
            "topic": _safe_str(topic_payload.get("title") or topic_payload.get("topic")),
            "topic_payload": deepcopy(topic_payload),
            "participation_mode": participation_mode,
            "player_participation": _player_participation_payload(
                mode=participation_mode,
                topic=topic_payload,
                tick=tick,
                settings=settings,
            ),
            "status": "active",
            "beats": [],
            "world_signals": [],
            "world_events": [],
            "player_responses": [],
            "created_tick": int(tick or 0),
            "updated_tick": int(tick or 0),
            "source": "deterministic_conversation_thread_runtime",
        }
        threads = _safe_list(state.get("threads"))
        threads.append(thread)
        if len(threads) > MAX_CONVERSATION_THREADS:
            del threads[:-MAX_CONVERSATION_THREADS]
        state["threads"] = threads

    if force_player_mode == "player_invited":
        forced_invite_payload = _apply_forced_player_invite_to_thread(
            state=state,
            thread=thread,
            topic_payload=topic_payload,
            tick=tick,
            settings=settings,
        )

    beats = _safe_list(thread.get("beats"))
    beat_index = len(beats) + 1
    max_beats = int(settings.get("max_beats_per_thread") or MAX_BEATS_PER_THREAD)
    if beat_index > max_beats:
        thread["status"] = "paused"
        _set_thread_cooldown(state, thread_id=thread_id, tick=tick, settings=settings)
        state["debug"] = {
            "last_triggered": False,
            "reason": "thread_beat_limit_reached",
            "thread_id": thread_id,
            "location_id": location_id,
        }
        return {
            "triggered": False,
            "reason": "thread_beat_limit_reached",
            "thread": deepcopy(thread),
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
        }

    beat = _make_beat(
        thread_id=thread_id,
        participants=participants,
        location_id=location_id,
        tick=tick,
        beat_index=beat_index,
        topic_payload=topic_payload,
        participation_mode=participation_mode,
    )
    beats.append(beat)
    thread["beats"] = beats
    thread["updated_tick"] = int(tick or 0)

    signal = {}
    if settings.get("allow_world_signals", True):
        signal = build_conversation_world_signal(
            tick=tick,
            thread_id=thread_id,
            beat_id=_safe_str(beat.get("beat_id")),
            topic=topic_payload,
            settings=settings,
        )
        # Enforce max signals per thread.
        if len(_safe_list(thread.get("world_signals"))) < int(settings.get("max_world_signals_per_thread") or 0):
            signal = _append_world_signal(state, signal)
        else:
            signal = {}
    thread_signals = _safe_list(thread.get("world_signals"))
    if signal:
        thread_signals.append(signal)
    if len(thread_signals) > MAX_BEATS_PER_THREAD:
        del thread_signals[:-MAX_BEATS_PER_THREAD]
    thread["world_signals"] = thread_signals

    # J1: seed a rumor from eligible signals.
    rumor_seed = {}
    if signal:
        rumor_seed = add_rumor_seed(
            simulation_state,
            signal=signal,
            topic=topic_payload,
            tick=tick,
            location_id=location_id,
            settings=settings,
        )

    # W1: record NPC knowledge from backed topic for speaker and listener.
    if settings.get("npc_knowledge_enabled", True) and topic_is_backed_by_state(topic_payload):
        for _npc_id in {_safe_str(beat.get("speaker_id")), _safe_str(beat.get("listener_id"))}:
            if _npc_id.startswith("npc:"):
                add_npc_knowledge_from_topic(
                    simulation_state,
                    npc_id=_npc_id,
                    topic=topic_payload,
                    tick=tick,
                    confidence=2,
                    ttl_ticks=int(settings.get("npc_knowledge_ttl_ticks") or 2000),
                )

    active_ids = _safe_list(state.get("active_thread_ids"))
    if thread_id not in active_ids:
        active_ids.append(thread_id)
    state["active_thread_ids"] = active_ids[-MAX_CONVERSATION_THREADS:]

    player_participation = _safe_dict(thread.get("player_participation"))
    if player_participation.get("pending_response"):
        state["pending_player_response"] = {
            "thread_id": thread_id,
            "topic_id": _safe_str(topic_payload.get("topic_id")),
            "prompt": _safe_str(player_participation.get("prompt")),
            "created_tick": int(tick or 0),
            "expires_tick": _safe_int(player_participation.get("expires_tick"), 0),
            "source": "deterministic_conversation_thread_runtime",
        }

    world_event = {}
    thread_world_event_count = len(_safe_list(thread.get("world_events")))
    if (
        settings.get("allow_world_events", True)
        and thread_world_event_count < int(settings.get("max_world_events_per_thread") or 0)
    ):
        world_event = add_world_event(
            simulation_state,
            {
                "event_id": f"world:event:npc_conversation:{int(tick or 0)}:{thread_id}:{beat_index}",
                "kind": "npc_conversation",
                "title": "NPC conversation",
                "summary": f"{beat['speaker_name']} speaks with {beat['listener_name']} about {beat['topic']}.",
                "thread_id": thread_id,
                "beat_id": beat["beat_id"],
                "location_id": location_id,
                "tick": int(tick or 0),
                "source": "deterministic_conversation_thread_runtime",
            },
        )
        thread_events = _safe_list(thread.get("world_events"))
        thread_events.append(world_event)
        thread["world_events"] = thread_events[-MAX_BEATS_PER_THREAD:]

    # Y1: update scene continuity from this NPC-to-NPC beat.
    if settings.get("scene_continuity_enabled", True):
        update_scene_continuity_from_conversation(
            simulation_state,
            location_id=location_id,
            topic_id=_safe_str(beat.get("topic_id")),
            topic_type=_safe_str(beat.get("topic_type")),
            speaker_id=_safe_str(beat.get("speaker_id")),
            listener_id=_safe_str(beat.get("listener_id")),
            tick=tick,
        )

    # W1: prune expired NPC knowledge.
    if settings.get("npc_knowledge_enabled", True):
        prune_npc_knowledge_state(
            simulation_state,
            current_tick=tick,
            max_known_facts_per_npc=int(settings.get("npc_knowledge_max_facts_per_npc") or 24),
        )

    state["debug"] = {
        "last_triggered": True,
        "reason": "wait_or_listen_turn",
        "thread_id": thread_id,
        "beat_id": beat["beat_id"],
        "participant_ids": [_safe_str(p.get("id")) for p in participants],
        "location_id": location_id,
    }

    result = {
        "triggered": True,
        "reason": "wait_or_listen_turn",
        "autonomous": bool(autonomous),
        "participation_mode": _safe_str(thread.get("participation_mode") or participation_mode),
        "player_participation": deepcopy(_safe_dict(thread.get("player_participation"))),
        "pending_player_response": deepcopy(_safe_dict(state.get("pending_player_response"))),
        "topic": deepcopy(topic_payload),
        "thread": deepcopy(thread),
        "beat": deepcopy(beat),
        "world_signal": deepcopy(signal),
        "world_event": deepcopy(world_event),
        "rumor_seed": deepcopy(rumor_seed),
        "director_intent": deepcopy(director_intent),
        "present_npcs": _safe_list(
            director_intent.get("present_npcs")
            or _safe_dict(_safe_dict(simulation_state.get("conversation_director_state")).get("debug")).get("present_npcs")
        ),
        "npc_history_state": deepcopy(_safe_dict(simulation_state.get("npc_history_state"))),
        "npc_reputation_state": deepcopy(_safe_dict(simulation_state.get("npc_reputation_state"))),
        "npc_knowledge_state": deepcopy(_safe_dict(simulation_state.get("npc_knowledge_state"))),
        "scene_continuity_state": deepcopy(_safe_dict(simulation_state.get("scene_continuity_state"))),
        "conversation_thread_state": get_conversation_thread_state(simulation_state),
        "forced_player_invite": deepcopy(forced_invite_payload),
        "source": "deterministic_conversation_thread_runtime",
    }
    validation = validate_conversation_effects(result, settings=settings)
    result["conversation_effect_validation"] = validation
    result = strip_forbidden_conversation_effects(result)
    return result
