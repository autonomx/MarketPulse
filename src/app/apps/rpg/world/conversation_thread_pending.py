from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.world.companion_acceptance import record_companion_join_offer
from app.apps.rpg.world.companion_dialogue import build_companion_presence_summary
from app.apps.rpg.world.consequence_signals import emit_consequence_signals
from app.apps.rpg.world.conversation_effects import (
    strip_forbidden_conversation_effects,
    validate_conversation_effects,
)
from app.apps.rpg.world.conversation_pivots import detect_conversation_topic_pivot
from app.apps.rpg.world.conversation_settings import normalize_conversation_settings
from app.apps.rpg.world.conversation_social_state import (
    choose_npc_response_style,
    record_npc_response_beat,
    record_player_joined_conversation,
)
from app.apps.rpg.world.conversation_topics import topic_is_backed_by_state
from app.apps.rpg.world.npc_arc_continuity import update_npc_arc_continuity
from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_evolution_triggers import evolve_npc_from_reputation_thresholds
from app.apps.rpg.world.npc_goal_state import (
    dominant_goal_for_npc,
    record_goal_influence,
    response_style_from_goal,
)
from app.apps.rpg.world.npc_history_state import add_npc_history_entry
from app.apps.rpg.world.npc_knowledge_state import (
    add_npc_knowledge_from_topic,
    prune_npc_knowledge_state,
)
from app.apps.rpg.world.npc_party_eligibility import evaluate_npc_party_join_eligibility
from app.apps.rpg.world.npc_referrals import suggest_npc_referral
from app.apps.rpg.world.npc_reputation_state import (
    get_npc_reputation,
    response_style_from_reputation,
    update_npc_reputation,
)
from app.apps.rpg.world.player_reputation_consequences import apply_player_reputation_consequence
from app.apps.rpg.world.quest_conversation_access import (
    evaluate_quest_conversation_access,
    filter_allowed_topic_facts_for_access,
    requested_topic_access_from_pivot,
)
from app.apps.rpg.world.quest_rumor_propagation import (
    maybe_seed_quest_rumor_from_conversation,
    prune_quest_rumors,
)
from app.apps.rpg.world.scene_continuity_state import update_scene_continuity_from_conversation
from app.apps.rpg.world.world_event_log import add_world_event

from .conversation_thread_base import (
    MAX_BEATS_PER_THREAD,
    _find_thread,
    _player_party_state,
    _safe_int,
    ensure_conversation_thread_state,
    get_conversation_thread_state,
)
from app.apps.rpg.foundation.safe_values import (
    safe_dict as _safe_dict,
    safe_list as _safe_list,
    safe_str as _safe_str,
)
from .conversation_thread_responses import (
    _biography_grounded_npc_response,
    _make_npc_response_beat,
)
from app.apps.rpg.world.companion_join_intent import maybe_create_companion_join_intent

def has_pending_player_conversation_response(
    simulation_state: Dict[str, Any],
    *,
    tick: int = 0,
) -> bool:
    state = ensure_conversation_thread_state(simulation_state)
    pending = _safe_dict(state.get("pending_player_response"))
    if not pending:
        return False
    # Return True even after expiry so the next player turn can clear the
    # stale pending response deterministically instead of leaving it stuck.
    return bool(_safe_str(pending.get("thread_id")))


def handle_pending_player_conversation_response(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    tick: int = 0,
    settings: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    state = ensure_conversation_thread_state(simulation_state)
    settings = normalize_conversation_settings(settings or {})
    pending = _safe_dict(state.get("pending_player_response"))
    response_text = _safe_str(player_input).strip()
    if not pending:
        return {
            "triggered": False,
            "reason": "no_pending_player_response",
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
            "source": "deterministic_conversation_thread_runtime",
        }
    if not response_text:
        return {
            "triggered": False,
            "reason": "empty_player_response",
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
            "source": "deterministic_conversation_thread_runtime",
        }

    expires_tick = _safe_int(pending.get("expires_tick"), 0)
    if expires_tick and int(tick or 0) > expires_tick:
        state["pending_player_response"] = {}
        state["debug"] = {
            "last_triggered": False,
            "reason": "pending_player_response_expired",
            "expires_tick": expires_tick,
            "tick": int(tick or 0),
        }
        return {
            "triggered": False,
            "reason": "pending_player_response_expired",
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
            "source": "deterministic_conversation_thread_runtime",
        }

    thread_id = _safe_str(pending.get("thread_id"))
    thread = _find_thread(state, thread_id)
    if not thread:
        state["pending_player_response"] = {}
        state["debug"] = {
            "last_triggered": False,
            "reason": "pending_player_response_stale_thread",
            "thread_id": thread_id,
        }
        return {
            "triggered": False,
            "reason": "pending_player_response_stale_thread",
            "conversation_thread_state": get_conversation_thread_state(simulation_state),
            "source": "deterministic_conversation_thread_runtime",
        }

    topic_payload = _safe_dict(thread.get("topic_payload"))
    participants = _safe_list(thread.get("participants"))
    listener = _safe_dict(participants[0] if participants else {})
    beats = _safe_list(thread.get("beats"))
    beat_index = len(beats) + 1
    player_response = {
        "beat_id": f"conversation:player_response:{int(tick or 0)}:{thread_id}:{beat_index}",
        "thread_id": thread_id,
        "beat_index": beat_index,
        "speaker_id": "player",
        "speaker_name": "Player",
        "listener_id": _safe_str(listener.get("id")),
        "listener_name": _safe_str(listener.get("name")),
        "line": response_text[:500],
        "topic_id": _safe_str(pending.get("topic_id") or topic_payload.get("topic_id") or thread.get("topic_id")),
        "topic_type": _safe_str(topic_payload.get("topic_type") or thread.get("topic_type")),
        "topic": _safe_str(topic_payload.get("title") or thread.get("topic")),
        "tick": int(tick or 0),
        "participation_mode": "player_joined",
        "source": "deterministic_conversation_thread_runtime",
    }
    beats.append(player_response)
    thread["beats"] = beats[-MAX_BEATS_PER_THREAD:]
    thread["updated_tick"] = int(tick or 0)
    thread["participation_mode"] = "player_joined"
    thread["player_participation"] = {
        "included": True,
        "mode": "player_joined",
        "pending_response": False,
        "topic_id": _safe_str(player_response.get("topic_id")),
        "responded_tick": int(tick or 0),
        "response_preview": response_text[:220],
        "source": "deterministic_conversation_thread_runtime",
    }
    responses = _safe_list(thread.get("player_responses"))
    responses.append(player_response)
    thread["player_responses"] = responses[-MAX_BEATS_PER_THREAD:]

    # ── Bundle H1: topic pivot detection ────────────────────────────────────
    topic_pivot = detect_conversation_topic_pivot(
        simulation_state,
        response_text,
        current_topic=topic_payload,
        settings=settings,
    )
    pivot_accepted = topic_pivot.get("accepted", False)
    active_topic = _safe_dict(topic_pivot.get("selected_topic")) if pivot_accepted else topic_payload

    if pivot_accepted:
        thread["topic_payload"] = deepcopy(active_topic)
        thread["topic_id"] = _safe_str(active_topic.get("topic_id"))
        thread["topic_type"] = _safe_str(active_topic.get("topic_type"))
        thread["topic"] = _safe_str(active_topic.get("title") or active_topic.get("topic"))

    # ── Bundle H1 + I1: NPC response beat ───────────────────────────────────
    npc_response_beat: Dict[str, Any] = {}
    response_style = ""
    quest_access: Dict[str, Any] = {}
    reputation_consequence: Dict[str, Any] = {}
    if settings.get("allow_npc_response_beats", True) and participants:
        npc = _safe_dict(participants[0])
        npc_id = _safe_str(npc.get("id"))
        forced_speaker_id = _safe_str(settings.get("test_force_conversation_speaker_id"))
        if forced_speaker_id.startswith("npc:"):
            npc_id = forced_speaker_id
            npc["id"] = npc_id
            npc["name"] = _safe_str(get_npc_biography(forced_speaker_id).get("name")) or forced_speaker_id.replace("npc:", "")
        response_style = choose_npc_response_style(
            simulation_state,
            thread=thread,
            player_response=player_response,
            topic_pivot=topic_pivot,
            tick=tick,
            settings=settings,
        )
        # Goal-style override (only when not already in a negative mode)
        if settings.get("allow_npc_goal_influence", True):
            goal = dominant_goal_for_npc(
                simulation_state,
                npc_id,
                tick=tick,
                location_id=_safe_str(thread.get("location_id")),
            )
            goal_style = response_style_from_goal(goal)
            if goal_style and response_style not in {"evasive", "annoyed"}:
                response_style = goal_style
                record_goal_influence(
                    simulation_state,
                    tick=tick,
                    npc_id=npc_id,
                    goal=goal,
                    influence_kind="npc_response_style_override",
                    details={"response_style": response_style},
                )
        npc_beat_index = len(_safe_list(thread.get("beats"))) + 1

        # Z-AA-AB.1: Compute requested_topic_access from pivot.
        requested_topic_access = requested_topic_access_from_pivot(topic_pivot or {})

        # Z-AA-AB: Evaluate quest conversation access gate before biography response.
        quest_access = {}
        effective_topic = active_topic
        if settings.get("quest_conversation_access_enabled", True):
            quest_access = evaluate_quest_conversation_access(
                simulation_state,
                npc_id=_safe_str(npc.get("id")),
                topic=active_topic,
                player_input=player_input,
            )
            if quest_access.get("requested"):
                allowed_facts = filter_allowed_topic_facts_for_access(
                    active_topic,
                    access=quest_access,
                )
                effective_topic = {
                    **active_topic,
                    "allowed_facts": allowed_facts,
                    "quest_conversation_access": quest_access,
                }

        biography_response = _biography_grounded_npc_response(
            speaker_id=_safe_str(npc.get("id")),
            listener_id="player",
            simulation_state=simulation_state,
            runtime_state={},
            player_input=player_input,
            topic=effective_topic,
            pivot=topic_pivot,
            response_style=response_style,
            response_intent="answer" if _safe_dict(topic_pivot).get("accepted") else "deflect",
        )
        npc_response_beat = _make_npc_response_beat(
            npc=npc,
            thread=thread,
            topic=effective_topic,
            topic_pivot=topic_pivot,
            response_style=response_style,
            tick=tick,
            thread_id=thread_id,
            beat_index=npc_beat_index,
        )
        if _safe_str(biography_response.get("line")):
            bio_line = _safe_str(biography_response.get("line"))
            recent_npc_lines = {
                _safe_str(beat.get("line")).strip().lower()
                for beat in _safe_list(thread.get("beats"))
                if _safe_str(beat.get("speaker_id")).startswith("npc:")
            }
            if bio_line.strip().lower() not in recent_npc_lines:
                npc_response_beat["line"] = bio_line
        npc_response_beat["biography_role"] = _safe_str(biography_response.get("biography_role"))
        npc_response_beat["roleplay_source"] = _safe_str(biography_response.get("roleplay_source") or "deterministic_template")
        npc_response_beat["used_fact_ids"] = _safe_list(biography_response.get("used_fact_ids"))
        npc_response_beat["dialogue_profile"] = _safe_dict(biography_response.get("profile"))
        npc_response_beat["dialogue_recall"] = _safe_dict(biography_response.get("dialogue_recall"))
        npc_response_beat["recalled_history_ids"] = _safe_list(biography_response.get("recalled_history_ids"))
        npc_response_beat["recalled_knowledge_ids"] = _safe_list(biography_response.get("recalled_knowledge_ids"))
        npc_response_beat["quest_conversation_access"] = deepcopy(quest_access)

        # QRS: Override response style based on NPC reputation before appending beat.
        if settings.get("npc_reputation_enabled", True):
            reputation = get_npc_reputation(simulation_state, npc_id=npc_id)
            response_style = response_style_from_reputation(reputation, fallback=response_style)

        record_npc_response_beat(simulation_state, beat=npc_response_beat, tick=tick)
        all_beats = _safe_list(thread.get("beats"))
        all_beats.append(npc_response_beat)
        thread["beats"] = all_beats[-MAX_BEATS_PER_THREAD:]
        thread["updated_tick"] = int(tick or 0)

        # QRS: Record NPC history and update reputation from player interaction.
        responder_id = _safe_str(npc.get("id"))
        responder_name = _safe_str(npc.get("name")) or responder_id.replace("npc:", "")
        topic_title = _safe_str(active_topic.get("title") or active_topic.get("topic") or active_topic.get("topic_id"))
        topic_id_str = _safe_str(active_topic.get("topic_id"))
        if settings.get("npc_history_enabled", True):
            add_npc_history_entry(
                simulation_state,
                npc_id=responder_id,
                kind="player_conversation_reply",
                summary=f"The player replied to {responder_name} about {topic_title or topic_id_str}.",
                topic_id=topic_id_str,
                tick=tick,
                importance=2,
                ttl_ticks=int(settings.get("npc_history_ttl_ticks") or 1000),
            )
        if settings.get("npc_reputation_enabled", True):
            update_npc_reputation(
                simulation_state,
                npc_id=responder_id,
                tick=tick,
                familiarity_delta=1,
                trust_delta=1 if topic_pivot.get("accepted") else 0,
                annoyance_delta=1 if topic_pivot.get("requested") and not topic_pivot.get("accepted") else 0,
                reason="player_joined_conversation",
            )

        # Z-AA-AB: Apply richer player reputation consequences.
        reputation_consequence = {}
        if settings.get("player_reputation_consequences_enabled", True):
            reputation_consequence = apply_player_reputation_consequence(
                simulation_state,
                npc_id=responder_id,
                player_input=player_input,
                topic_pivot=topic_pivot or {},
                conversation_result={"reason": "pending_player_response_consumed"},
                tick=tick,
            )
        npc_response_beat["player_reputation_consequence"] = deepcopy(reputation_consequence)
        npc_response_beat["requested_topic_access"] = deepcopy(requested_topic_access)

        # AF-AG-AH: NPC evolution from reputation thresholds.
        npc_evolution_result: Dict[str, Any] = {}
        if settings.get("npc_evolution_enabled", True):
            npc_evolution_result = evolve_npc_from_reputation_thresholds(
                simulation_state,
                npc_id=responder_id,
                tick=tick,
            )
        npc_response_beat["npc_evolution_result"] = deepcopy(npc_evolution_result)
        npc_response_beat["npc_evolution_state"] = deepcopy(_safe_dict(simulation_state.get("npc_evolution_state")))

        # AI: Party eligibility check after evolution.
        party_join_eligibility_result: Dict[str, Any] = {}
        if settings.get("npc_party_eligibility_enabled", True):
            party_join_eligibility_result = evaluate_npc_party_join_eligibility(
                simulation_state,
                npc_id=responder_id,
            )
        npc_response_beat["party_join_eligibility_result"] = deepcopy(party_join_eligibility_result)

        # AJ: Companion join intent from player request.
        companion_join_intent: Dict[str, Any] = {}
        if settings.get("companion_join_intent_enabled", True):
            companion_join_intent = maybe_create_companion_join_intent(
                simulation_state,
                npc_id=responder_id,
                player_input=player_input,
            )
        npc_response_beat["companion_join_intent"] = deepcopy(companion_join_intent)

        companion_offer_record_result: Dict[str, Any] = {}
        if (
            settings.get("companion_acceptance_enabled", True)
            and companion_join_intent.get("offered")
        ):
            companion_offer_record_result = record_companion_join_offer(
                simulation_state,
                npc_id=responder_id,
                join_intent=companion_join_intent,
                tick=tick,
                profile_auto_create=_safe_dict(settings.get("npc_profile_generation")).get(
                    "auto_create_on_introduction",
                    True,
                ),
            )

        npc_response_beat["companion_offer_record_result"] = deepcopy(
            companion_offer_record_result
        )
        npc_response_beat["companion_acceptance_state"] = deepcopy(
            _safe_dict(simulation_state.get("companion_acceptance_state"))
        )
        npc_response_beat["party_state"] = _player_party_state(simulation_state)

        # AL-AM-AN.4:
        # This response turn may create an offer, but it must never accept it.
        # Acceptance is handled only at the top of maybe_advance_conversation_thread
        # on a later player yes/no response.
        companion_acceptance_result: Dict[str, Any] = {
            "resolved": False,
            "accepted": False,
            "rejected": False,
            "reason": "awaiting_later_player_acceptance",
            "source": "deterministic_companion_acceptance",
        }

        npc_response_beat["companion_acceptance_result"] = deepcopy(companion_acceptance_result)
        npc_response_beat["companion_dialogue_result"] = {}
        npc_response_beat["companion_presence_summary"] = deepcopy(
            build_companion_presence_summary(simulation_state)
        )

        # AK: Arc continuity tracking.
        npc_arc_continuity_result: Dict[str, Any] = {}
        if settings.get("npc_arc_continuity_enabled", True):
            npc_arc_continuity_result = update_npc_arc_continuity(
                simulation_state,
                npc_id=responder_id,
                tick=tick,
            )
        npc_response_beat["npc_arc_continuity_result"] = deepcopy(npc_arc_continuity_result)

        # AD: NPC referral suggestion.
        npc_referral: Dict[str, Any] = {}
        if settings.get("npc_referrals_enabled", True):
            npc_referral = suggest_npc_referral(
                simulation_state,
                speaker_id=responder_id,
                topic=effective_topic,
                access=quest_access,
                requested_topic_access=requested_topic_access,
                player_input=player_input,
            )
            if npc_referral.get("suggested") and quest_access.get("access") in {"none", "partial"}:
                npc_response_beat["line"] = f"{npc_response_beat.get('line', '')} {npc_referral.get('line_hint')}".strip()
        npc_response_beat["npc_referral"] = deepcopy(npc_referral)
    else:
        requested_topic_access = requested_topic_access_from_pivot(topic_pivot or {})
        npc_referral = {}

    state["pending_player_response"] = {}

    world_event = {}
    thread_world_event_count = len(_safe_list(thread.get("world_events")))
    if (
        settings.get("allow_world_events", True)
        and thread_world_event_count < int(settings.get("max_world_events_per_thread") or 0)
    ):
        world_event = add_world_event(
            simulation_state,
            {
                "event_id": f"world:event:npc_conversation_player_response:{int(tick or 0)}:{thread_id}:{beat_index}",
                "kind": "npc_conversation_player_response",
                "title": "Player joined NPC conversation",
                "summary": f"The player responded to the conversation about {player_response['topic']}.",
                "thread_id": thread_id,
                "beat_id": player_response["beat_id"],
                "topic_id": _safe_str(player_response.get("topic_id")),
                "location_id": _safe_str(thread.get("location_id")),
                "tick": int(tick or 0),
                "source": "deterministic_conversation_thread_runtime",
            },
        )
        thread_events = _safe_list(thread.get("world_events"))
        if world_event:
            thread_events.append(world_event)
        thread["world_events"] = thread_events[-MAX_BEATS_PER_THREAD:]

    # Y1: update scene continuity after player response NPC beat.
    if settings.get("scene_continuity_enabled", True) and npc_response_beat:
        update_scene_continuity_from_conversation(
            simulation_state,
            location_id=_safe_str(thread.get("location_id")),
            topic_id=_safe_str(active_topic.get("topic_id")),
            topic_type=_safe_str(active_topic.get("topic_type")),
            speaker_id=_safe_str(npc_response_beat.get("speaker_id")),
            listener_id="player",
            tick=tick,
        )

    # W1: knowledge from active_topic for responding NPC.
    if settings.get("npc_knowledge_enabled", True) and npc_response_beat:
        _resp_npc_id = _safe_str(npc_response_beat.get("speaker_id"))
        if _resp_npc_id.startswith("npc:") and topic_is_backed_by_state(active_topic):
            add_npc_knowledge_from_topic(
                simulation_state,
                npc_id=_resp_npc_id,
                topic=active_topic,
                tick=tick,
                confidence=2,
                ttl_ticks=int(settings.get("npc_knowledge_ttl_ticks") or 2000),
            )
        prune_npc_knowledge_state(
            simulation_state,
            current_tick=tick,
            max_known_facts_per_npc=int(settings.get("npc_knowledge_max_facts_per_npc") or 24),
        )

    social_state = record_player_joined_conversation(
        simulation_state,
        tick=tick,
        thread=thread,
        player_response=player_response,
        topic=topic_payload,
    )

    state["debug"] = {
        "last_triggered": True,
        "reason": "pending_player_response_consumed",
        "thread_id": thread_id,
        "beat_id": player_response["beat_id"],
        "tick": int(tick or 0),
        "requested_topic_hint": topic_pivot["requested_topic_hint"],
        "pivot_result": "accepted" if pivot_accepted else "rejected",
        "selected_topic_id": topic_pivot["selected_topic_id"],
        "selected_topic_type": topic_pivot["selected_topic_type"],
        "pivot_rejected_reason": topic_pivot["pivot_rejected_reason"],
        "response_style": response_style,
        "response_style_source": "deterministic_conversation_social_state",
    }

    # Build conversation_result snapshot for AC/AE wiring.
    _partial_conversation_result: Dict[str, Any] = {
        "topic_pivot": topic_pivot,
        "quest_conversation_access": deepcopy(quest_access),
        "requested_topic_access": deepcopy(requested_topic_access),
        "player_reputation_consequence": deepcopy(reputation_consequence),
        "npc_referral": deepcopy(npc_referral),
        "npc_response_beat": deepcopy(npc_response_beat),
        "thread": deepcopy(thread),
    }

    # AC: Quest rumor propagation.
    quest_rumor_result: Dict[str, Any] = {}
    if settings.get("quest_rumor_propagation_enabled", True):
        quest_rumor_result = maybe_seed_quest_rumor_from_conversation(
            simulation_state,
            conversation_result=_partial_conversation_result,
            tick=tick,
            ttl_ticks=int(settings.get("quest_rumor_ttl_ticks") or 120),
        )
        prune_quest_rumors(simulation_state, current_tick=tick)

    # AE: Consequence signals.
    _partial_conversation_result["quest_rumor_result"] = deepcopy(quest_rumor_result)
    consequence_signal_result: Dict[str, Any] = {}
    if settings.get("consequence_signals_enabled", True):
        consequence_signal_result = emit_consequence_signals(
            simulation_state,
            conversation_result=_partial_conversation_result,
            tick=tick,
        )

    result = {
        "triggered": True,
        "reason": "pending_player_response_consumed",
        "autonomous": False,
        "participation_mode": "player_joined",
        "player_participation": deepcopy(_safe_dict(thread.get("player_participation"))),
        "player_response": deepcopy(player_response),
        "topic": deepcopy(active_topic),
        "topic_pivot": topic_pivot,
        "npc_response_beat": deepcopy(npc_response_beat),
        "dialogue_profile": deepcopy(_safe_dict(npc_response_beat.get("dialogue_profile"))),
        "roleplay_source": _safe_str(npc_response_beat.get("roleplay_source")),
        "used_fact_ids": _safe_list(npc_response_beat.get("used_fact_ids")),
        "dialogue_recall": deepcopy(_safe_dict(npc_response_beat.get("dialogue_recall"))),
        "recalled_history_ids": _safe_list(npc_response_beat.get("recalled_history_ids")),
        "recalled_knowledge_ids": _safe_list(npc_response_beat.get("recalled_knowledge_ids")),
        "npc_response_style": response_style,
        "thread": deepcopy(thread),
        "beat": deepcopy(player_response),
        "world_signal": {},
        "world_event": deepcopy(world_event),
        "conversation_social_state": deepcopy(social_state),
        "npc_goal_state": deepcopy(_safe_dict(simulation_state.get("npc_goal_state"))),
        "npc_history_state": deepcopy(_safe_dict(simulation_state.get("npc_history_state"))),
        "npc_reputation_state": deepcopy(_safe_dict(simulation_state.get("npc_reputation_state"))),
        "npc_knowledge_state": deepcopy(_safe_dict(simulation_state.get("npc_knowledge_state"))),
        "npc_evolution_state": deepcopy(_safe_dict(simulation_state.get("npc_evolution_state"))),
        "npc_evolution_result": deepcopy(_safe_dict(npc_response_beat.get("npc_evolution_result"))),
        "party_join_eligibility_result": deepcopy(_safe_dict(npc_response_beat.get("party_join_eligibility_result"))),
        "companion_join_intent": deepcopy(_safe_dict(npc_response_beat.get("companion_join_intent"))),
        "companion_acceptance_result": deepcopy(_safe_dict(npc_response_beat.get("companion_acceptance_result"))),
        "companion_offer_record_result": deepcopy(_safe_dict(npc_response_beat.get("companion_offer_record_result"))),
        "companion_dialogue_result": deepcopy(_safe_dict(npc_response_beat.get("companion_dialogue_result"))),
        "companion_presence_summary": deepcopy(_safe_dict(npc_response_beat.get("companion_presence_summary"))),
        "party_state": deepcopy(_safe_dict(npc_response_beat.get("party_state"))),
        "npc_arc_continuity_result": deepcopy(_safe_dict(npc_response_beat.get("npc_arc_continuity_result"))),
        "npc_arc_continuity_state": deepcopy(
            _safe_dict(simulation_state.get("npc_arc_continuity_state"))
        ),
        "scene_continuity_state": deepcopy(_safe_dict(simulation_state.get("scene_continuity_state"))),
        "quest_conversation_access": deepcopy(quest_access),
        "player_reputation_consequence": deepcopy(reputation_consequence),
        "requested_topic_access": deepcopy(requested_topic_access),
        "npc_referral": deepcopy(npc_referral),
        "quest_rumor_result": deepcopy(quest_rumor_result),
        "quest_rumor_state": deepcopy(_safe_dict(simulation_state.get("quest_rumor_state"))),
        "consequence_signal_result": deepcopy(consequence_signal_result),
        "consequence_signal_state": deepcopy(_safe_dict(simulation_state.get("consequence_signal_state"))),
        "conversation_thread_state": get_conversation_thread_state(simulation_state),
        "source": "deterministic_conversation_thread_runtime",
    }
    validation = validate_conversation_effects(result, settings=settings)
    result["conversation_effect_validation"] = validation
    result = strip_forbidden_conversation_effects(result)
    return result


# Alias for test imports
maybe_consume_pending_player_response = handle_pending_player_conversation_response
