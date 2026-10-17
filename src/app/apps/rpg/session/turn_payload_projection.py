from __future__ import annotations

from copy import deepcopy
from typing import Any

# RPG session runtime responsibility module.
# Phase 4.13: route session travel commands through guarded Phase 4 runtime helpers.
from app.apps.rpg.session.session_runtime_store import (
    load_runtime_session as load_runtime_session, save_runtime_session as save_runtime_session,
)
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _ensure_simulation_state as _ensure_simulation_state, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list,
    _safe_str as _safe_str,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _build_turn_id as _build_turn_id,
)

from .combat_quest_narrative import _apply_combat_quest_narrative as _base_apply_turn_authoritative
from app.apps.rpg.foundation.safe_values import copy_state

_PHASE4_SESSION_TRAVEL_SOURCE = "deterministic_phase4_session_travel_command_integration"
_PHASE4_FRONTEND_MAP_LOCATION_SOURCE = "deterministic_phase4_frontend_map_location_ui_panel"
_PHASE8_PLAYER_HUD_SOURCE = "deterministic_phase8_player_visible_state_objective_hud_gate"
_PHASE8_OBJECTIVE_JOURNAL_SOURCE = "deterministic_phase8_objective_journal_detail_panel_gate"
_PHASE8_COMBAT_PANEL_SOURCE = "deterministic_phase8_combat_state_action_affordance_gate"


def _phase4_session_travel_turn_index(runtime_state: dict[str, Any], simulation_state: dict[str, Any]) -> int:
    return _safe_int(
        runtime_state.get("tick")
        or simulation_state.get("tick")
        or simulation_state.get("current_tick"),
        0,
    )


def _phase4_session_travel_current_location(simulation_state: dict[str, Any]) -> str:
    travel_state = _safe_dict(simulation_state.get("travel_state"))
    location_state = _safe_dict(simulation_state.get("location_state"))
    return _safe_str(
        travel_state.get("current_location_id")
        or location_state.get("current_location_id")
        or simulation_state.get("current_location_id")
        or simulation_state.get("location_id")
    )


def _phase4_session_travel_summary(command_result: dict[str, Any]) -> str:
    command_result = _safe_dict(command_result)
    travel_result = _safe_dict(command_result.get("travel_result"))
    command = _safe_dict(command_result.get("command_result"))
    start = _safe_str(command.get("start_location_id"))
    end = _safe_str(command.get("end_location_id"))
    reason = _safe_str(command_result.get("reason") or travel_result.get("reason"))
    if command_result.get("ok") is True and end:
        return f"Travel resolved deterministically: {start} -> {end}."
    if reason == "insufficient_travel_resources":
        return "Travel denied: required travel resources are missing."
    if reason == "route_blocked":
        return "Travel denied: that route is currently blocked."
    if reason == "destination_undiscovered":
        return "Travel denied: that destination is not yet discovered."
    if reason == "route_undiscovered":
        return "Travel denied: that route is not yet discovered."
    if reason == "unknown_travel_destination":
        return "Travel denied: no canonical destination matches that command."
    return f"Travel command result: {reason or 'not_applied'}."


def _phase4_frontend_map_location_panel_payload(simulation_state: dict[str, Any]) -> dict[str, Any]:
    """Build the player-visible map/location UI payload without mutating state."""
    from app.apps.rpg.world.locations import build_map_location_panel_payload

    panel_payload = build_map_location_panel_payload(deepcopy(_safe_dict(simulation_state)))
    panel_payload["frontend_source"] = _PHASE4_FRONTEND_MAP_LOCATION_SOURCE
    return panel_payload


def _phase8_inventory_summary(player_state: dict[str, Any]) -> dict[str, Any]:
    inventory = _safe_dict(player_state.get("inventory_state") or player_state.get("inventory"))
    items = []
    for raw in _safe_list(inventory.get("items") or player_state.get("items")):
        item = _safe_dict(raw)
        item_id = _safe_str(item.get("item_id") or item.get("id") or item.get("name"))
        if item_id:
            items.append(
                {
                    "item_id": item_id,
                    "name": _safe_str(item.get("name") or item_id),
                    "qty": _safe_int(item.get("qty") or item.get("quantity"), 1),
                    "source": _PHASE8_PLAYER_HUD_SOURCE,
                }
            )
    currency = _safe_dict(inventory.get("currency") or player_state.get("currency"))
    return {
        "items": items[:8],
        "item_count": len(items),
        "currency": {
            "gold": _safe_int(currency.get("gold"), 0),
            "silver": _safe_int(currency.get("silver"), 0),
            "copper": _safe_int(currency.get("copper"), 0),
            "source": _PHASE8_PLAYER_HUD_SOURCE,
        },
        "source": _PHASE8_PLAYER_HUD_SOURCE,
    }


def _phase8_active_objective_summary(simulation_state: dict[str, Any]) -> dict[str, Any]:
    panel = _phase8_objective_journal_panel_payload(simulation_state, {})
    active_objective = _safe_dict(panel.get("active_objective"))
    return {
        "title": _safe_str(active_objective.get("title") or "No active objective recorded"),
        "quest_id": _safe_str(active_objective.get("quest_id") or active_objective.get("id")),
        "status": _safe_str(active_objective.get("status") or "none"),
        "source": _PHASE8_PLAYER_HUD_SOURCE,
    }


def _phase8_party_summary(simulation_state: dict[str, Any]) -> dict[str, Any]:
    party_state = _safe_dict(simulation_state.get("party_state") or simulation_state.get("party"))
    members = []
    for raw in _safe_list(party_state.get("members") or party_state.get("companions")):
        member = _safe_dict(raw)
        member_id = _safe_str(member.get("npc_id") or member.get("id") or member.get("name"))
        if member_id:
            members.append(
                {
                    "id": member_id,
                    "name": _safe_str(member.get("name") or member_id),
                    "role": _safe_str(member.get("role") or "companion"),
                    "source": _PHASE8_PLAYER_HUD_SOURCE,
                }
            )
    return {"members": members[:6], "member_count": len(members), "source": _PHASE8_PLAYER_HUD_SOURCE}


def _phase8_major_warnings(simulation_state: dict[str, Any], runtime_state: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    player_state = _safe_dict(simulation_state.get("player_state"))
    survival = _safe_dict(player_state.get("survival_state") or simulation_state.get("survival_state"))
    hunger = _safe_int(survival.get("hunger"), 0)
    thirst = _safe_int(survival.get("thirst"), 0)
    if hunger >= 80:
        warnings.append({"kind": "high_hunger", "label": "Hunger is high", "severity": "warning", "source": _PHASE8_PLAYER_HUD_SOURCE})
    if thirst >= 80:
        warnings.append({"kind": "high_thirst", "label": "Thirst is high", "severity": "warning", "source": _PHASE8_PLAYER_HUD_SOURCE})
    last_turn = _safe_dict(runtime_state.get("last_turn_result"))
    if last_turn and last_turn.get("ok") is False:
        warnings.append(
            {
                "kind": "last_action_failed",
                "label": _safe_str(last_turn.get("reason") or "Last action failed"),
                "severity": "info",
                "source": _PHASE8_PLAYER_HUD_SOURCE,
            }
        )
    return warnings


def _phase8_objective_status(raw_status: Any) -> str:
    status = _safe_str(raw_status or "available").strip().lower().replace("-", "_").replace(" ", "_")
    status_aliases = {
        "active": "active",
        "in_progress": "active",
        "current": "active",
        "available": "available",
        "offered": "available",
        "open": "available",
        "completed": "completed",
        "complete": "completed",
        "done": "completed",
        "blocked": "blocked",
        "locked": "blocked",
        "failed": "blocked",
        "unavailable": "blocked",
    }
    return status_aliases.get(status, "available")


def _phase8_objective_label(status: str) -> str:
    return {
        "active": "Active",
        "available": "Available",
        "completed": "Completed",
        "blocked": "Blocked",
    }.get(_safe_str(status), "Available")


def _phase8_objective_detail(raw: dict[str, Any], fallback_index: int, source_name: str) -> dict[str, Any]:
    objective = _safe_dict(raw)
    status = _phase8_objective_status(objective.get("status") or objective.get("state"))
    objective_id = _safe_str(objective.get("objective_id") or objective.get("id") or objective.get("quest_id"))
    title = _safe_str(
        objective.get("title")
        or objective.get("objective")
        or objective.get("summary")
        or objective.get("description")
        or objective_id
        or f"Objective {fallback_index + 1}"
    )
    return {
        "id": objective_id or f"objective:{fallback_index + 1}",
        "quest_id": _safe_str(objective.get("quest_id") or objective.get("quest") or objective_id),
        "title": title,
        "description": _safe_str(objective.get("description") or objective.get("details") or title),
        "status": status,
        "status_label": _phase8_objective_label(status),
        "blocking_reason": _safe_str(objective.get("blocking_reason") or objective.get("reason")),
        "source_name": source_name,
        "source": _PHASE8_OBJECTIVE_JOURNAL_SOURCE,
    }


def _phase8_objective_candidates(simulation_state: dict[str, Any]) -> list[dict[str, Any]]:
    journal = _safe_dict(simulation_state.get("journal_state") or simulation_state.get("journal"))
    quest_state = _safe_dict(simulation_state.get("quest_state") or simulation_state.get("quests"))
    candidates: list[dict[str, Any]] = []
    for raw in _safe_list(journal.get("objectives")):
        candidates.append({"raw": _safe_dict(raw), "source_name": "journal_state.objectives"})
    for raw in _safe_list(quest_state.get("objectives")):
        candidates.append({"raw": _safe_dict(raw), "source_name": "quest_state.objectives"})
    for raw in _safe_list(quest_state.get("active_quests") or quest_state.get("quests")):
        candidates.append({"raw": _safe_dict(raw), "source_name": "quest_state.quests"})
    active_text = _safe_str(journal.get("active_objective") or quest_state.get("active_objective"))
    if active_text:
        candidates.insert(0, {"raw": {"title": active_text, "status": "active"}, "source_name": "active_objective"})
    return candidates


def _phase8_grouped_objectives(simulation_state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"active": [], "available": [], "completed": [], "blocked": []}
    for index, candidate in enumerate(_phase8_objective_candidates(simulation_state)):
        detail = _phase8_objective_detail(
            _safe_dict(candidate.get("raw")),
            index,
            _safe_str(candidate.get("source_name") or "unknown"),
        )
        grouped.setdefault(detail["status"], []).append(detail)
    return {key: value[:8] for key, value in grouped.items()}


def _phase8_journal_entries(simulation_state: dict[str, Any]) -> list[dict[str, Any]]:
    journal = _safe_dict(simulation_state.get("journal_state") or simulation_state.get("journal"))
    entries = []
    for index, raw in enumerate(_safe_list(journal.get("entries") or journal.get("journal_entries"))):
        entry = _safe_dict(raw)
        title = _safe_str(entry.get("title") or entry.get("heading") or f"Journal entry {index + 1}")
        body = _safe_str(entry.get("body") or entry.get("text") or entry.get("summary") or entry.get("description"))
        entries.append(
            {
                "id": _safe_str(entry.get("id") or entry.get("entry_id") or f"journal:{index + 1}"),
                "title": title,
                "body": body,
                "turn_index": _safe_int(entry.get("turn_index") or entry.get("turn"), 0),
                "source_name": "journal_state.entries",
                "source": _PHASE8_OBJECTIVE_JOURNAL_SOURCE,
            }
        )
    learned = _safe_list(journal.get("what_i_learned") or journal.get("learned"))
    for offset, raw in enumerate(learned):
        value = _safe_str(raw)
        if value:
            entries.append(
                {
                    "id": f"learned:{offset + 1}",
                    "title": "What I learned",
                    "body": value,
                    "turn_index": 0,
                    "source_name": "journal_state.what_i_learned",
                    "source": _PHASE8_OBJECTIVE_JOURNAL_SOURCE,
                }
            )
    return entries[-8:]


def _phase8_recent_action_state(runtime_state: dict[str, Any]) -> dict[str, Any]:
    last_turn = _safe_dict(runtime_state.get("last_turn_result") or runtime_state.get("last_result"))
    last_action = _safe_dict(runtime_state.get("last_player_action") or runtime_state.get("last_action"))
    return {
        "ok": last_turn.get("ok") is True,
        "action_id": _safe_str(last_action.get("action_id") or last_turn.get("turn_id")),
        "action_type": _safe_str(last_action.get("action_type") or last_turn.get("action_type")),
        "target_id": _safe_str(last_action.get("target_id")),
        "reason": _safe_str(last_turn.get("reason")),
        "summary": _safe_str(last_turn.get("summary") or last_turn.get("label")),
        "source": _PHASE8_OBJECTIVE_JOURNAL_SOURCE,
    }


def _phase8_objective_journal_panel_payload(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the player-visible objective/journal panel without mutating state."""

    simulation_copy = copy_state(_safe_dict(simulation_state))
    runtime_copy = copy_state(_safe_dict(runtime_state))
    objectives = _phase8_grouped_objectives(simulation_copy)
    active_objective = (objectives.get("active") or objectives.get("available") or [{}])[0]
    if not active_objective:
        active_objective = {
            "id": "",
            "quest_id": "",
            "title": "No active objective recorded",
            "description": "",
            "status": "none",
            "status_label": "None",
            "source_name": "none",
            "source": _PHASE8_OBJECTIVE_JOURNAL_SOURCE,
        }
    return {
        "source": _PHASE8_OBJECTIVE_JOURNAL_SOURCE,
        "frontend_source": _PHASE8_OBJECTIVE_JOURNAL_SOURCE,
        "active_objective": deepcopy(active_objective),
        "objectives": objectives,
        "journal_entries": _phase8_journal_entries(simulation_copy),
        "recent_action_state": _phase8_recent_action_state(runtime_copy),
        "major_warnings": _phase8_major_warnings(simulation_copy, runtime_copy),
        "non_mutating": True,
    }


def _phase8_combat_source_state(simulation_state: dict[str, Any], runtime_state: dict[str, Any]) -> dict[str, Any]:
    return _safe_dict(
        runtime_state.get("combat_state")
        or simulation_state.get("combat_state")
        or _safe_dict(simulation_state.get("player_state")).get("combat_state")
    )


def _phase8_actor_is_player(actor_id: str, actor: dict[str, Any]) -> bool:
    side = _safe_str(actor.get("side") or actor.get("team") or actor.get("faction")).lower()
    actor_type = _safe_str(actor.get("actor_type") or actor.get("type") or actor.get("kind")).lower()
    return actor_id == "player" or side == "player" or actor_type == "player"


def _phase8_combat_participant_detail(actor_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    actor = _safe_dict(raw)
    hp = _safe_int(actor.get("hp") or actor.get("current_hp") or actor.get("health"), 0)
    max_hp = _safe_int(actor.get("max_hp") or actor.get("maximum_hp") or actor.get("hp_max"), hp)
    side = _safe_str(actor.get("side") or actor.get("team") or ("player" if _phase8_actor_is_player(actor_id, actor) else "enemy"))
    return {
        "actor_id": actor_id,
        "name": _safe_str(actor.get("name") or actor.get("label") or actor_id),
        "side": side,
        "is_player": _phase8_actor_is_player(actor_id, actor),
        "hp": hp,
        "max_hp": max_hp,
        "defeated": bool(actor.get("defeated") or actor.get("is_defeated") or (max_hp > 0 and hp <= 0)),
        "source": _PHASE8_COMBAT_PANEL_SOURCE,
    }


def _phase8_combat_participants(combat_state: dict[str, Any]) -> list[dict[str, Any]]:
    participants = _safe_dict(combat_state.get("participants"))
    rows: list[dict[str, Any]] = []
    if participants:
        for actor_id in sorted(participants):
            rows.append(_phase8_combat_participant_detail(_safe_str(actor_id), _safe_dict(participants.get(actor_id))))
    else:
        for index, raw in enumerate(_safe_list(combat_state.get("actors") or combat_state.get("combatants"))):
            actor = _safe_dict(raw)
            actor_id = _safe_str(actor.get("actor_id") or actor.get("id") or f"actor:{index + 1}")
            rows.append(_phase8_combat_participant_detail(actor_id, actor))
    return rows[:12]


def _phase8_player_actor_id(combat_state: dict[str, Any], participants: list[dict[str, Any]]) -> str:
    configured = _safe_str(combat_state.get("player_actor_id") or combat_state.get("player_id"))
    if configured:
        return configured
    for participant in participants:
        if participant.get("is_player"):
            return _safe_str(participant.get("actor_id"))
    return "player"


def _phase8_combat_legal_actions(combat_state: dict[str, Any], participants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if combat_state.get("active") is not True:
        return []
    current_actor_id = _safe_str(combat_state.get("current_actor_id") or combat_state.get("turn_actor_id"))
    player_actor_id = _phase8_player_actor_id(combat_state, participants)
    if current_actor_id and current_actor_id != player_actor_id:
        return []
    targets = [row for row in participants if not row.get("is_player") and not row.get("defeated")]
    actions: list[dict[str, Any]] = []
    for target in targets[:6]:
        actions.append(
            {
                "action_type": "attack",
                "target_id": _safe_str(target.get("actor_id")),
                "label": f"Attack {_safe_str(target.get('name') or target.get('actor_id'))}",
                "enabled": True,
                "source": _PHASE8_COMBAT_PANEL_SOURCE,
            }
        )
    actions.append({"action_type": "defend", "target_id": player_actor_id, "label": "Defend", "enabled": True, "source": _PHASE8_COMBAT_PANEL_SOURCE})
    return actions


def _phase8_combat_panel_warnings(combat_state: dict[str, Any], runtime_state: dict[str, Any], participants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if combat_state.get("active") is True:
        current_actor_id = _safe_str(combat_state.get("current_actor_id") or combat_state.get("turn_actor_id"))
        player_actor_id = _phase8_player_actor_id(combat_state, participants)
        if current_actor_id and current_actor_id != player_actor_id:
            warnings.append(
                {
                    "kind": "waiting_for_npc_turn",
                    "label": "Waiting for a non-player combat turn",
                    "severity": "info",
                    "source": _PHASE8_COMBAT_PANEL_SOURCE,
                }
            )
    last_turn = _safe_dict(runtime_state.get("last_turn_result") or runtime_state.get("last_result"))
    if last_turn and last_turn.get("ok") is False:
        warnings.append(
            {
                "kind": "last_combat_action_rejected",
                "label": _safe_str(last_turn.get("reason") or "Last combat action was rejected"),
                "severity": "warning",
                "source": _PHASE8_COMBAT_PANEL_SOURCE,
            }
        )
    return warnings


def _phase8_combat_panel_payload(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build player-visible combat state/action affordances without mutating state."""

    simulation_copy = copy_state(_safe_dict(simulation_state))
    runtime_copy = copy_state(_safe_dict(runtime_state))
    combat_state = _phase8_combat_source_state(simulation_copy, runtime_copy)
    participants = _phase8_combat_participants(combat_state)
    current_actor_id = _safe_str(combat_state.get("current_actor_id") or combat_state.get("turn_actor_id"))
    player_actor_id = _phase8_player_actor_id(combat_state, participants)
    legal_actions = _phase8_combat_legal_actions(combat_state, participants)
    active = combat_state.get("active") is True
    return {
        "source": _PHASE8_COMBAT_PANEL_SOURCE,
        "frontend_source": _PHASE8_COMBAT_PANEL_SOURCE,
        "active": active,
        "combat_id": _safe_str(combat_state.get("combat_id") or combat_state.get("id")),
        "status_label": "In combat" if active else "Not in combat",
        "current_actor_id": current_actor_id,
        "player_actor_id": player_actor_id,
        "is_player_turn": bool(active and (not current_actor_id or current_actor_id == player_actor_id)),
        "participants": participants,
        "legal_actions": legal_actions,
        "target_summary": [row for row in participants if not row.get("is_player")][:6],
        "recent_action_state": _phase8_recent_action_state(runtime_copy),
        "major_warnings": _phase8_combat_panel_warnings(combat_state, runtime_copy, participants),
        "non_mutating": True,
    }


def _phase8_player_visible_hud_payload(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a read-only player HUD payload from deterministic runtime state."""

    simulation_copy = copy_state(_safe_dict(simulation_state))
    runtime_copy = copy_state(_safe_dict(runtime_state))
    player_state = _safe_dict(simulation_copy.get("player_state"))
    map_panel = _phase4_frontend_map_location_panel_payload(simulation_copy)
    time_state = _safe_dict(map_panel.get("time_state") or simulation_copy.get("time_state"))
    weather_state = _safe_dict(map_panel.get("weather_state") or simulation_copy.get("weather_state"))
    return {
        "source": _PHASE8_PLAYER_HUD_SOURCE,
        "frontend_source": _PHASE8_PLAYER_HUD_SOURCE,
        "current_location": _safe_dict(map_panel.get("current_location")),
        "current_location_id": _safe_str(map_panel.get("current_location_id")),
        "active_objective": _phase8_active_objective_summary(simulation_copy),
        "player_resources": _phase8_inventory_summary(player_state),
        "party_summary": _phase8_party_summary(simulation_copy),
        "major_warnings": _phase8_major_warnings(simulation_copy, runtime_copy),
        "time_state": {
            "day_count": _safe_int(time_state.get("day_count"), 1),
            "clock_time": _safe_str(time_state.get("clock_time")),
            "time_of_day_label": _safe_str(time_state.get("time_of_day_label")),
            "season": _safe_str(time_state.get("season") or weather_state.get("season")),
            "weather_label": _safe_str(time_state.get("weather_label") or weather_state.get("weather_label") or weather_state.get("label")),
            "source": _PHASE8_PLAYER_HUD_SOURCE,
        },
        "map_location_panel": map_panel,
        "non_mutating": True,
    }


def _phase4_session_travel_payload(
    *,
    session_id: str,
    player_input: str,
    session: dict[str, Any],
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    command_result: dict[str, Any],
) -> dict[str, Any]:
    from app.apps.rpg.world.locations import build_runtime_travel_command_narration_contract

    command_result = _safe_dict(command_result)
    turn_index = _phase4_session_travel_turn_index(runtime_state, simulation_state)
    turn_id = _build_turn_id(runtime_state)
    summary = _phase4_session_travel_summary(command_result)
    contract = build_runtime_travel_command_narration_contract(command_result)
    travel_result = _safe_dict(command_result.get("travel_result"))
    encounter_result = _safe_dict(command_result.get("encounter_result"))
    encounter_runtime_result = _safe_dict(command_result.get("encounter_runtime_result"))
    map_location_panel = _phase4_frontend_map_location_panel_payload(simulation_state)
    player_hud = _phase8_player_visible_hud_payload(simulation_state, runtime_state)
    objective_journal_panel = _phase8_objective_journal_panel_payload(simulation_state, runtime_state)
    combat_action_panel = _phase8_combat_panel_payload(simulation_state, runtime_state)

    resolved_result: dict[str, Any] = {
        "ok": command_result.get("ok") is True,
        "action_type": "travel",
        "semantic_action_type": "travel",
        "summary": summary,
        "outcome": "success" if command_result.get("ok") is True else "failure",
        "travel_command_result": command_result,
        "travel_result": travel_result,
        "encounter_result": encounter_result,
        "encounter_runtime_result": encounter_runtime_result,
        "runtime_travel_command_narration_contract": contract,
        "map_location_panel": map_location_panel,
        "player_hud": player_hud,
        "objective_journal_panel": objective_journal_panel,
        "combat_action_panel": combat_action_panel,
        "meaningful_progress": command_result.get("ok") is True,
        "progress_category": "location_progression" if command_result.get("ok") is True else "blocked_travel",
        "source": _PHASE4_SESSION_TRAVEL_SOURCE,
    }
    if travel_result:
        resolved_result["travel_resource_result"] = _safe_dict(travel_result.get("resource_result"))
    if encounter_runtime_result.get("combat_candidate"):
        resolved_result["combat_candidate"] = encounter_runtime_result.get("combat_candidate")

    runtime_state["last_turn_result"] = {
        "action_type": "travel",
        "reason": _safe_str(command_result.get("reason")),
        "ok": command_result.get("ok") is True,
        "source": _PHASE4_SESSION_TRAVEL_SOURCE,
    }
    runtime_state["last_player_action"] = {
        "action_id": f"player_action:{turn_index + 1}",
        "action_type": "travel",
        "target_id": _safe_str(_safe_dict(command_result.get("command_result")).get("end_location_id")),
        "source": _PHASE4_SESSION_TRAVEL_SOURCE,
    }
    runtime_state["tick"] = turn_index + 1

    session = _safe_dict(session)
    session["simulation_state"] = simulation_state
    session["runtime_state"] = runtime_state
    manifest = _safe_dict(session.get("manifest"))
    manifest.setdefault("session_id", session_id)
    manifest.setdefault("id", session_id)
    session["manifest"] = manifest
    save_runtime_session(session)

    narration_context = {
        "player_input": player_input,
        "action_type": "travel",
        "resolved_result": resolved_result,
        "simulation_state": simulation_state,
        "runtime_state": runtime_state,
        "travel_command_result": command_result,
        "travel_result": travel_result,
        "encounter_result": encounter_result,
        "encounter_runtime_result": encounter_runtime_result,
        "runtime_travel_command_narration_contract": contract,
        "map_location_panel": map_location_panel,
        "player_hud": player_hud,
        "objective_journal_panel": objective_journal_panel,
        "combat_action_panel": combat_action_panel,
        "forbidden_narration": list(_safe_list(contract.get("forbidden_runtime_travel_command_claims"))),
        "settings": runtime_state.get("runtime_settings", {}),
        "conversation_threads": [],
    }

    return {
        "ok": True,
        "simulation_state": simulation_state,
        "runtime_state": runtime_state,
        "session": session,
        "result": resolved_result,
        "resolved_result": resolved_result,
        "travel_command_result": command_result,
        "travel_result": travel_result,
        "encounter_result": encounter_result,
        "encounter_runtime_result": encounter_runtime_result,
        "runtime_travel_command_narration_contract": contract,
        "map_location_panel": map_location_panel,
        "player_hud": player_hud,
        "objective_journal_panel": objective_journal_panel,
        "combat_action_panel": combat_action_panel,
        "narration_context": narration_context,
        "narration": summary,
        "final_narration": summary,
        "summary": summary,
        "turn_id": turn_id,
        "tick": turn_index,
        "source": _PHASE4_SESSION_TRAVEL_SOURCE,
    }


def _apply_phase4_session_travel_command(
    session_id: str,
    player_input: str,
    *,
    session: dict[str, Any],
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    from app.apps.rpg.world.locations import apply_runtime_travel_command, resolve_travel_command

    turn_index = _phase4_session_travel_turn_index(runtime_state, simulation_state)
    current_location_id = _phase4_session_travel_current_location(simulation_state)
    command_probe = resolve_travel_command(player_input, current_location_id=current_location_id or "location:rusty_flagon")
    if command_probe.get("reason") == "not_travel_command":
        return {}

    command_result = apply_runtime_travel_command(
        simulation_state,
        player_input,
        turn_index=turn_index,
        encounter_seed=f"phase4.13:{session_id}",
        current_location_id=current_location_id or None,
    )
    return _phase4_session_travel_payload(
        session_id=session_id,
        player_input=player_input,
        session=session,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
        command_result=command_result,
    )


def _apply_phase4_travel_and_panels(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = load_runtime_session(session_id)
    if session is None:
        return _base_apply_turn_authoritative(session_id, player_input, action, performance_override=performance_override)
    session = _copy_dict(session)
    simulation_state = _ensure_simulation_state(_safe_dict(session.get("simulation_state")))
    runtime_state = _copy_dict(session.get("runtime_state"))
    travel_payload = _apply_phase4_session_travel_command(
        session_id,
        _safe_str(player_input).strip(),
        session=session,
        simulation_state=simulation_state,
        runtime_state=runtime_state,
    )
    if travel_payload:
        return travel_payload
    base_payload = _base_apply_turn_authoritative(session_id, player_input, action, performance_override=performance_override)
    if isinstance(base_payload, dict):
        base_payload.setdefault("player_hud", _phase8_player_visible_hud_payload(simulation_state, runtime_state))
        base_payload.setdefault(
            "objective_journal_panel",
            _phase8_objective_journal_panel_payload(simulation_state, runtime_state),
        )
        base_payload.setdefault("combat_action_panel", _phase8_combat_panel_payload(simulation_state, runtime_state))
    return base_payload


__all__ = ['_PHASE4_FRONTEND_MAP_LOCATION_SOURCE', '_PHASE4_SESSION_TRAVEL_SOURCE', '_PHASE8_COMBAT_PANEL_SOURCE', '_PHASE8_OBJECTIVE_JOURNAL_SOURCE', '_PHASE8_PLAYER_HUD_SOURCE', '_apply_phase4_session_travel_command', '_apply_phase4_travel_and_panels', '_phase4_frontend_map_location_panel_payload', '_phase4_session_travel_current_location', '_phase4_session_travel_payload', '_phase4_session_travel_summary', '_phase4_session_travel_turn_index', '_phase8_active_objective_summary', '_phase8_actor_is_player', '_phase8_combat_legal_actions', '_phase8_combat_panel_payload', '_phase8_combat_panel_warnings', '_phase8_combat_participant_detail', '_phase8_combat_participants', '_phase8_combat_source_state', '_phase8_grouped_objectives', '_phase8_inventory_summary', '_phase8_journal_entries', '_phase8_major_warnings', '_phase8_objective_candidates', '_phase8_objective_detail', '_phase8_objective_journal_panel_payload', '_phase8_objective_label', '_phase8_objective_status', '_phase8_party_summary', '_phase8_player_actor_id', '_phase8_player_visible_hud_payload', '_phase8_recent_action_state']