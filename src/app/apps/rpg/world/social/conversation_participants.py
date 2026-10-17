from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _speaker_weight(npc_row: Dict[str, Any]) -> float:
    npc_row = _safe_dict(npc_row)
    role = _safe_str(npc_row.get("role")).lower()
    base = 0.10
    if role in {"leader", "guard", "innkeeper", "merchant", "priest"}:
        base += 0.20
    if role in {"bard", "noble", "thief"}:
        base += 0.10
    base += _safe_float(npc_row.get("assertiveness"), 0.0) * 0.20
    return base


def find_candidate_conversation_groups(simulation_state: Dict[str, Any], location_id: str, tick: int) -> List[List[str]]:
    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    present = []
    for npc_id, raw in sorted(npc_index.items()):
        row = _safe_dict(raw)
        if _safe_str(row.get("location_id")) == _safe_str(location_id):
            present.append(_safe_str(npc_id))

    groups: List[List[str]] = []
    for i in range(len(present)):
        for j in range(i + 1, len(present)):
            groups.append([present[i], present[j]])
    return groups[:6]


def select_initiator(simulation_state: Dict[str, Any], participants: List[str]) -> str:
    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    weighted = []
    for npc_id in participants:
        row = _safe_dict(npc_index.get(npc_id))
        weighted.append((-_speaker_weight(row), _safe_str(npc_id)))
    weighted.sort()
    return weighted[0][1] if weighted else ""


def select_next_speaker(conversation: Dict[str, Any], simulation_state: Dict[str, Any]) -> str:
    conversation = _safe_dict(conversation)
    participants = [x for x in (_safe_str(p) for p in conversation.get("participants") or []) if x]
    last_speaker_id = _safe_str(conversation.get("last_speaker_id"))
    if not participants:
        return ""
    if last_speaker_id and last_speaker_id in participants:
        idx = participants.index(last_speaker_id)
        return participants[(idx + 1) % len(participants)]
    initiator = _safe_str(conversation.get("initiator_id"))
    if initiator and initiator in participants:
        return initiator
    return participants[0]
