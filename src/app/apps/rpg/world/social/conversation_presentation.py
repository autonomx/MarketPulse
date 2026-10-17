from __future__ import annotations

from typing import Any, Dict, List, cast

from .npc_conversations import (
    get_conversation_lines,
    list_active_conversations,
    list_recent_conversations,
)
from .player_interventions import build_intervention_options
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def _resolve_speaker_name(simulation_state: Dict[str, Any], line: Dict[str, Any]) -> str:
    line = _safe_dict(line)
    explicit = _safe_str(line.get("speaker_name"))
    if explicit:
        return explicit
    speaker_id = _safe_str(line.get("speaker"))
    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    npc = _safe_dict(npc_index.get(speaker_id))
    return _safe_str(npc.get("name")) or speaker_id


def build_conversation_payload(simulation_state: Dict[str, Any], runtime_state: Dict[str, Any], location_id: str = "") -> Dict[str, Any]:
    active = list_active_conversations(simulation_state, location_id=location_id)
    recent = list_recent_conversations(simulation_state, location_id=location_id)

    active_payload: List[Dict[str, Any]] = []
    for conv in active:
        cid = conv.get("conversation_id")
        lines = []
        for line in get_conversation_lines(simulation_state, cast(str, cid)):
            row = dict(line)
            row["speaker_name"] = _resolve_speaker_name(simulation_state, row)
            lines.append(row)
        active_payload.append({
            "conversation_id": cid,
            "kind": conv.get("kind"),
            "topic": conv.get("topic"),
            "participants": conv.get("participants") or [],
            "turn_count": int(conv.get("turn_count", 0) or 0),
            "max_turns": int(conv.get("max_turns", 0) or 0),
            "player_can_intervene": bool(conv.get("player_can_intervene")),
            "lines": lines,
            "intervention_options": build_intervention_options(conv, simulation_state, runtime_state),
        })

    recent_payload: List[Dict[str, Any]] = []
    for conv in recent[-10:]:
        cid = conv.get("conversation_id")
        lines = []
        for line in get_conversation_lines(simulation_state, cast(str, cid)):
            row = dict(line)
            row["speaker_name"] = _resolve_speaker_name(simulation_state, row)
            lines.append(row)
        recent_payload.append({
            "conversation_id": cid,
            "kind": conv.get("kind"),
            "topic": conv.get("topic"),
            "participants": conv.get("participants") or [],
            "lines": lines,
            "close_reason": conv.get("close_reason", ""),
        })

    return {
        "active_conversations": active_payload,
        "recent_conversations": recent_payload,
    }
