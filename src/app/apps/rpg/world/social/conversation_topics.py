from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _topic_sort_key(topic: Dict[str, Any]):
    return (
        -_safe_float(topic.get("priority"), 0.0),
        _safe_str(topic.get("type")),
        _safe_str(topic.get("anchor")),
    )


def _recent_events_for_location(simulation_state: Dict[str, Any], location_id: str) -> List[Dict[str, Any]]:
    rows = []
    for raw in _safe_list(simulation_state.get("events"))[-24:]:
        row = _safe_dict(raw)
        if _safe_str(row.get("location_id")) == _safe_str(location_id):
            rows.append(row)
    return rows


def _participant_roles(simulation_state: Dict[str, Any], participant_ids: List[str]) -> Dict[str, str]:
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    out: Dict[str, str] = {}
    for npc_id in participant_ids:
        row = _safe_dict(npc_index.get(npc_id))
        out[npc_id] = _safe_str(row.get("role")).lower()
    return out


def _build_event_commentary_topic(event: Dict[str, Any]) -> Dict[str, Any]:
    event = _safe_dict(event)
    event_id = _safe_str(event.get("event_id"))
    event_type = _safe_str(event.get("type")).lower()
    summary = _safe_str(event.get("summary") or event.get("description"))
    return {
        "type": "event_commentary",
        "anchor": event_id,
        "summary": summary or f"NPCs discuss a recent {event_type}.",
        "stance": "comment",
        "priority": 0.60,
        "reason": "recent event",
    }


def _build_local_incident_topic(event: Dict[str, Any]) -> Dict[str, Any]:
    event = _safe_dict(event)
    event_id = _safe_str(event.get("event_id"))
    return {
        "type": "local_incident",
        "anchor": event_id,
        "summary": _safe_str(event.get("summary") or "NPCs react to a nearby disturbance."),
        "stance": "concern",
        "priority": 0.72,
        "reason": "local disturbance",
    }


def _build_plan_reaction_topic(player_action: Dict[str, Any]) -> Dict[str, Any]:
    player_action = _safe_dict(player_action)
    action_text = _safe_str(player_action.get("text") or player_action.get("summary") or player_action.get("action"))
    return {
        "type": "plan_reaction",
        "anchor": _safe_str(player_action.get("action_id") or action_text),
        "summary": f"NPCs react to the player's plan: {action_text}".strip(),
        "stance": "react",
        "priority": 0.82,
        "reason": "player plan",
    }


def _build_moral_conflict_topic(npc_a: str, npc_b: str) -> Dict[str, Any]:
    return {
        "type": "moral_conflict",
        "anchor": f"{npc_a}:{npc_b}:morality",
        "summary": f"{npc_a} questions {npc_b}'s morality.",
        "stance": "disagree",
        "priority": 0.78,
        "reason": "personality conflict",
    }


def _build_risk_conflict_topic(anchor: str) -> Dict[str, Any]:
    return {
        "type": "risk_conflict",
        "anchor": anchor,
        "summary": "NPCs disagree about a risky course of action.",
        "stance": "disagree",
        "priority": 0.76,
        "reason": "risk tolerance conflict",
    }


def _dedupe_topics(topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for topic in sorted(topics, key=_topic_sort_key):
        key = (_safe_str(topic.get("type")), _safe_str(topic.get("anchor")))
        if key in seen:
            continue
        seen.add(key)
        out.append(topic)
    return out


def build_conversation_topic_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    location_id: str,
    participant_ids: List[str],
    tick: int,
) -> List[Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    participant_ids = [x for x in (_safe_str(p) for p in participant_ids) if x]
    topics: List[Dict[str, Any]] = []

    recent_events = _recent_events_for_location(simulation_state, location_id)
    for event in recent_events[-8:]:
        event_type = _safe_str(event.get("type")).lower()
        if event_type in {"attack", "threaten", "retaliate", "incident", "destabilize", "sabotage"}:
            topics.append(_build_local_incident_topic(event))
        elif event_type:
            topics.append(_build_event_commentary_topic(event))

    last_player_action = _safe_dict(runtime_state.get("last_player_action"))
    if last_player_action:
        topics.append(_build_plan_reaction_topic(last_player_action))

    roles = _participant_roles(simulation_state, participant_ids)
    role_values = sorted(set(v for v in roles.values() if v))
    if "thief" in role_values and any(v in {"guard", "innkeeper", "priest", "paladin"} for v in role_values):
        a = participant_ids[0] if participant_ids else ""
        b = participant_ids[1] if len(participant_ids) > 1 else ""
        if a and b:
            topics.append(_build_moral_conflict_topic(a, b))

    if last_player_action and _safe_str(last_player_action.get("text")).lower().find("cave") >= 0:
        topics.append(_build_risk_conflict_topic("player_plan:cave"))

    # HARD FALLBACK:
    # If multiple NPCs are together and nothing else triggered, still allow
    # ambient conversation so the world does not feel frozen.
    if not topics and len(participant_ids) >= 2:
        a = participant_ids[0]
        b = participant_ids[1]
        topics.append({
            "type": "ambient_chat",
            "anchor": f"{a}:{b}:ambient_chat",
            "summary": "NPCs casually talk among themselves.",
            "stance": "comment",
            "priority": 0.20,
            "reason": "ambient fallback",
        })

    return _dedupe_topics(topics)[:8]
