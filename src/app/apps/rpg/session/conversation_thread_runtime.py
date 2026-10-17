from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.rules.ambient_intent import is_ambient_wait_or_listen_intent
from app.apps.rpg.session.ambient_tick_runtime import is_ambient_tick_command
from app.apps.rpg.world.conversation_settings import conversation_settings_from_runtime
from app.apps.rpg.world.conversation_threads import (
    handle_pending_player_conversation_response,
    has_pending_player_conversation_response,
    maybe_advance_conversation_thread,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def _conversation_blocking_reason(
    *,
    player_input: str,
    resolved_result: Dict[str, Any],
    allow_pending_player_response: bool = False,
) -> str:
    """Return a reason if this turn must not trigger NPC conversation.

    Bundle F v1 rule:
      Only passive wait/listen/observe turns or __ambient_tick__ pseudo-turns
      may trigger NPC conversation.

    Service, purchase, travel, social, combat, inventory, and normal command
    turns must not spawn an ambient NPC thread as a side effect.
    """
    resolved_result = _safe_dict(resolved_result)
    service_result = _safe_dict(resolved_result.get("service_result"))
    travel_result = _safe_dict(resolved_result.get("travel_result"))
    action_type = _safe_str(resolved_result.get("action_type"))
    semantic_action_type = _safe_str(resolved_result.get("semantic_action_type"))
    semantic_family = _safe_str(resolved_result.get("semantic_family"))

    # Pending NPC-invited replies have precedence over normal service/social
    # routing. Otherwise questions like "What should I know about the room?"
    # can be swallowed by lodging/info routing before the conversation runtime
    # can consume the pending player response.
    if allow_pending_player_response:
        return ""

    if service_result.get("matched"):
        return "service_turn"
    if travel_result.get("matched"):
        return "travel_turn"

    blocked_action_types = {
        "service_inquiry",
        "service_purchase",
        "service_transaction",
        "travel",
        "travel_move",
        "combat",
        "combat_action",
        "social_activity",
        "attack",
        "inventory",
        "item_use",
        "take",
        "drop",
        "equip",
    }
    if action_type in blocked_action_types or semantic_action_type in blocked_action_types:
        return f"blocked_action_type:{action_type or semantic_action_type}"

    if semantic_family in {"commerce", "travel", "combat", "inventory"}:
        return f"blocked_semantic_family:{semantic_family}"
    if semantic_family == "social" and not allow_pending_player_response:
        return f"blocked_semantic_family:{semantic_family}"

    if is_ambient_tick_command(player_input):
        return ""
    if is_ambient_wait_or_listen_intent(player_input):
        return ""

    return "not_wait_listen_or_ambient_tick"


def advance_conversation_threads_for_turn(
    *,
    player_input: str,
    simulation_state: Dict[str, Any],
    resolved_result: Dict[str, Any],
    tick: int = 0,
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    resolved_result = _safe_dict(resolved_result)
    settings = conversation_settings_from_runtime(runtime_state or {})
    pending_player_response = has_pending_player_conversation_response(
        simulation_state,
        tick=tick,
    )
    block_reason = _conversation_blocking_reason(
        player_input=player_input,
        resolved_result=resolved_result,
        allow_pending_player_response=pending_player_response,
    )
    if block_reason:
        return {
            "triggered": False,
            "reason": block_reason,
            "source": "deterministic_conversation_thread_runtime",
        }

    if pending_player_response and not is_ambient_tick_command(player_input):
        return handle_pending_player_conversation_response(
            simulation_state,
            player_input=player_input,
            tick=tick,
            settings=settings,
        )

    return maybe_advance_conversation_thread(
        simulation_state,
        player_input=player_input,
        tick=tick,
        settings=settings,
        exclude_event_ids=_current_turn_world_event_ids(resolved_result),
    )


def _current_turn_world_event_ids(resolved_result: Dict[str, Any]) -> list[str]:
    resolved_result = _safe_dict(resolved_result)
    ids: list[str] = []
    for key in (
        "world_event",
        "service_world_event",
        "rumor_world_event",
        "travel_world_event",
    ):
        event_id = _safe_str(_safe_dict(resolved_result.get(key)).get("event_id"))
        if event_id:
            ids.append(event_id)

    service_application = _safe_dict(resolved_result.get("service_application"))
    for key in ("service_world_event", "rumor_world_event", "world_event"):
        event_id = _safe_str(_safe_dict(service_application.get(key)).get("event_id"))
        if event_id:
            ids.append(event_id)

    return sorted(set(ids))
