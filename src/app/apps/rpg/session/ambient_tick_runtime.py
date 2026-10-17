from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.world.conversation_rumors import expire_conversation_world_signals
from app.apps.rpg.world.conversation_settings import (
    conversation_settings_from_runtime,
    should_attempt_autonomous_conversation,
)
from app.apps.rpg.world.conversation_threads import maybe_advance_conversation_thread
from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.world.npc_presence_runtime import update_present_npcs_for_location
from app.apps.rpg.world.scene_activity_scheduler import maybe_schedule_scene_activity
from app.apps.rpg.world.scene_population_runtime import build_scene_population_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

AMBIENT_TICK_COMMANDS = {
    "__ambient_tick__",
    "__idle_tick__",
    "__world_tick__",
    "__ambient_tick_player_addressed__",
    "__ambient_tick_player_invited__",
    "__ambient_tick_quest__",
    "__ambient_tick_event__",
    "__ambient_tick_rumor__",
}

SCENE_ACTIVITY_TICK_COMMANDS = {
    "__scene_activity_tick__",
}


def is_ambient_tick_command(player_input: str) -> bool:
    command = _safe_str(player_input).strip().lower()
    return command in AMBIENT_TICK_COMMANDS or command in SCENE_ACTIVITY_TICK_COMMANDS


def is_scene_activity_tick_command(player_input: str) -> bool:
    return _safe_str(player_input).strip().lower() in SCENE_ACTIVITY_TICK_COMMANDS


def _is_conversation_trigger_input(player_input: str) -> bool:
    text = _safe_str(player_input).strip().lower()
    if text.startswith("__ambient_tick"):
        return True
    return (
        "wait" in text
        or "listen" in text
        or "observe" in text
    )


def _forced_player_mode(command: str) -> str:
    command = _safe_str(command).strip().lower()
    if command == "__ambient_tick_player_invited__":
        return "player_invited"
    if command == "__ambient_tick_player_addressed__":
        return "player_addressed"
    return ""


def _forced_topic_type(command: str) -> str:
    command = _safe_str(command).strip().lower()
    if command == "__ambient_tick_quest__":
        return "quest"
    if command == "__ambient_tick_event__":
        return "recent_event"
    if command == "__ambient_tick_rumor__":
        return "rumor"
    return ""


def advance_autonomous_ambient_tick(
    *,
    player_input: str,
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    settings = conversation_settings_from_runtime(runtime_state)
    command = _safe_str(player_input).strip().lower()

    presence_result: Dict[str, Any] = {}
    scene_population: Dict[str, Any] = {}

    if settings.get("npc_presence_enabled", True):
        presence_result = update_present_npcs_for_location(
            simulation_state,
            location_id=current_location_id(simulation_state),
            tick=tick,
        )

    if settings.get("scene_population_enabled", True):
        scene_population = build_scene_population_state(
            simulation_state,
            location_id=current_location_id(simulation_state),
            tick=tick,
        )

    try:
        signal_expiration = expire_conversation_world_signals(
            simulation_state,
            runtime_state=runtime_state,
            current_tick=tick,
            settings=settings,
        )
    except Exception as exc:
        thread_signals = _safe_dict(simulation_state.get("conversation_thread_state")).get("world_signals")
        thread_signals = thread_signals if isinstance(thread_signals, list) else []
        rumor_signals = _safe_dict(simulation_state.get("conversation_rumor_state")).get("conversation_world_signals")
        rumor_signals = rumor_signals if isinstance(rumor_signals, list) else []
        signal_expiration = {
            "expired_count": 0,
            "remaining_thread_signal_count": len(thread_signals),
            "remaining_rumor_signal_count": len(rumor_signals),
            "error": f"{type(exc).__name__}: {exc}",
            "source": "deterministic_ambient_tick_runtime",
        }

    force = is_ambient_tick_command(command)
    force_mode = _forced_player_mode(command)
    force_topic = _forced_topic_type(command)
    force_player_invited = force_mode == "player_invited"

    if is_scene_activity_tick_command(command):
        scene_activity_result = maybe_schedule_scene_activity(
            simulation_state,
            tick=tick,
            settings=settings,
            force=True,
        )
        return {
            "matched": True,
            "applied": False,
            "status": "scene_activity_tick",
            "reason": "scene_activity_tick_command",
            "conversation_result": {},
            "scene_activity_result": scene_activity_result,
            "signal_expiration": signal_expiration,
            "conversation_settings": settings,
            "presence_result": presence_result,
            "scene_population_state": scene_population,
            "source": "deterministic_ambient_tick_runtime",
        }

    if not _is_conversation_trigger_input(player_input):
        return {
            "matched": False,
            "applied": False,
            "status": "skipped_non_conversation_input",
            "reason": "non_conversation_input",
            "conversation_result": {},
            "scene_activity_result": {},
            "signal_expiration": signal_expiration,
            "conversation_settings": settings,
            "presence_result": presence_result,
            "scene_population_state": scene_population,
            "source": "deterministic_ambient_tick_runtime",
        }

    conversation_state = _safe_dict(simulation_state.get("conversation_thread_state"))
    debug = _safe_dict(conversation_state.get("debug"))
    last_tick = int(debug.get("last_autonomous_conversation_tick") or 0)

    should_attempt = should_attempt_autonomous_conversation(
        tick=tick,
        last_conversation_tick=last_tick,
        settings=settings,
        force=force,
    )

    if not should_attempt:
        scene_activity_result = maybe_schedule_scene_activity(
            simulation_state, tick=tick, settings=settings
        )
        return {
            "matched": force,
            "applied": False,
            "status": "ambient_tick_no_conversation",
            "reason": "settings_or_cooldown_prevented_conversation",
            "scene_activity_result": scene_activity_result,
            "signal_expiration": signal_expiration,
            "conversation_settings": settings,
            "presence_result": presence_result,
            "scene_population_state": scene_population,
            "source": "deterministic_ambient_tick_runtime",
        }

    conversation_result = maybe_advance_conversation_thread(
        simulation_state,
        player_input="__autonomous_ambient_tick__",
        tick=tick,
        settings=settings,
        autonomous=True,
        force=True,
        force_player_mode=force_mode,
        forced_topic_type=force_topic,
    )

    forced_invite_failed = False
    forced_invite_failure_reason = ""
    if force_player_invited:
        participation = _safe_dict(conversation_result.get("player_participation"))
        pending = (
            _safe_dict(conversation_result.get("pending_player_response"))
            or _safe_dict(
                _safe_dict(conversation_result.get("conversation_thread_state")).get("pending_player_response")
            )
            or _safe_dict(
                _safe_dict(simulation_state.get("conversation_thread_state")).get("pending_player_response")
            )
        )
        if (
            not conversation_result.get("triggered")
            or _safe_str(conversation_result.get("participation_mode")) != "player_invited"
            or not participation.get("pending_response")
            or not pending
        ):
            forced_invite_failed = True
            if conversation_result.get("triggered") and (not participation.get("pending_response") or not pending):
                forced_invite_failure_reason = "forced_player_invited_missing_pending_response"
            else:
                forced_invite_failure_reason = (
                    _safe_str(conversation_result.get("reason"))
                    or "forced_player_invited_did_not_create_pending_response"
                )

    conversation_state = _safe_dict(simulation_state.get("conversation_thread_state"))
    debug = _safe_dict(conversation_state.get("debug"))
    if conversation_result.get("triggered"):
        debug["last_autonomous_conversation_tick"] = int(tick or 0)
    conversation_state["debug"] = debug
    simulation_state["conversation_thread_state"] = conversation_state

    scene_activity_result = maybe_schedule_scene_activity(
        simulation_state, tick=tick, settings=settings
    )

    return {
        "matched": force,
        "applied": bool(conversation_result.get("triggered")),
        "status": "ambient_tick_conversation" if conversation_result.get("triggered") else "ambient_tick_no_conversation",
        "conversation_result": conversation_result,
        "scene_activity_result": scene_activity_result,
        "signal_expiration": signal_expiration,
        "conversation_settings": settings,
        "forced_player_invited": bool(force_player_invited),
        "forced_player_invited_failed": bool(forced_invite_failed),
        "forced_player_invited_failure_reason": forced_invite_failure_reason,
        "pending_player_response": deepcopy(
            _safe_dict(
                _safe_dict(simulation_state.get("conversation_thread_state")).get("pending_player_response")
            )
        ),
        "npc_history_state": _safe_dict(simulation_state.get("npc_history_state")),
        "npc_reputation_state": _safe_dict(simulation_state.get("npc_reputation_state")),
        "npc_knowledge_state": _safe_dict(simulation_state.get("npc_knowledge_state")),
        "npc_dialogue_recall_state": _safe_dict(simulation_state.get("npc_dialogue_recall_state")),
        "scene_continuity_state": _safe_dict(simulation_state.get("scene_continuity_state")),
        "conversation_director_state": _safe_dict(simulation_state.get("conversation_director_state")),
        "presence_result": presence_result,
        "scene_population_state": scene_population,
        "source": "deterministic_ambient_tick_runtime",
    }