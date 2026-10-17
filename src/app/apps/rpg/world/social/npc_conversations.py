from __future__ import annotations

from hashlib import sha1
from typing import Any, Dict, List, Optional
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

_MAX_ACTIVE_CONVERSATIONS = 4
_MAX_RECENT_CONVERSATIONS = 60
_MAX_LINES_PER_CONVERSATION = 12


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(_safe_str(p) for p in parts)
    digest = sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _trim_list(rows: List[Any], max_items: int) -> List[Any]:
    rows = list(rows or [])
    if len(rows) <= max_items:
        return rows
    return rows[-max_items:]


def ensure_conversation_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(simulation_state, dict):
        simulation_state = {}
    social_state = simulation_state.setdefault("social_state", {})
    if not isinstance(social_state, dict):
        social_state = {}
        simulation_state["social_state"] = social_state

    conversations = social_state.setdefault("conversations", {})
    if not isinstance(conversations, dict):
        conversations = {}
        social_state["conversations"] = conversations

    active = conversations.setdefault("active", [])
    recent = conversations.setdefault("recent", [])
    lines_by_conversation = conversations.setdefault("lines_by_conversation", {})

    if not isinstance(active, list):
        conversations["active"] = []
    if not isinstance(recent, list):
        conversations["recent"] = []
    if not isinstance(lines_by_conversation, dict):
        conversations["lines_by_conversation"] = {}

    return simulation_state


def build_conversation_state(
    *,
    kind: str,
    location_id: str,
    participants: List[str],
    initiator_id: str,
    topic: Dict[str, Any],
    max_turns: int,
    player_can_intervene: bool,
    player_present: bool,
    tick: int,
) -> Dict[str, Any]:
    participants = [p for p in (_safe_str(x) for x in participants) if p]
    participants = sorted(dict.fromkeys(participants))
    topic = _safe_dict(topic)
    conversation_id = _stable_id(
        "conv",
        kind,
        location_id,
        ",".join(participants),
        topic.get("type"),
        topic.get("anchor"),
        tick,
    )
    return {
        "conversation_id": conversation_id,
        "kind": _safe_str(kind),
        "status": "active",
        "location_id": _safe_str(location_id),
        "participants": participants,
        "initiator_id": _safe_str(initiator_id),
        "topic": topic,
        "turn_count": 0,
        "max_turns": int(max_turns or 1),
        "player_can_intervene": bool(player_can_intervene),
        "player_present": bool(player_present),
        "created_tick": int(tick or 0),
        "updated_tick": int(tick or 0),
        "last_speaker_id": "",
        "intervention_pending": False,
        "line_source": "template",
        # 4C-A: Thread engine extensions
        "mode": "ambient",           # ambient | directed_to_player | group
        "audience": [],              # IDs of entities overhearing (e.g. player, nearby NPCs)
        "importance": 0,             # 0-100, used for scheduling priority
        "world_effect_budget": 0,    # max world signals this thread can emit
        "world_effects_emitted": 0,  # signals emitted so far
        "expires_at_tick": int(tick or 0) + int(max_turns or 1) + 4,
        "beat_count": 0,             # authoritative beat count
        "pivot_history": [],         # list of mode transitions
    }


def build_conversation_line(
    *,
    conversation_id: str,
    turn: int,
    speaker: str,
    text: str,
    kind: str,
    created_tick: int,
    source: str = "template",
    speaker_name: str = "",
) -> Dict[str, Any]:
    line_id = _stable_id(
        "convline",
        conversation_id,
        int(turn or 0),
        _safe_str(speaker),
        _safe_str(text),
    )
    return {
        "conversation_id": _safe_str(conversation_id),
        "line_id": line_id,
        "turn": int(turn or 0),
        "speaker": _safe_str(speaker),
        "speaker_name": _safe_str(speaker_name),
        "text": _safe_str(text),
        "kind": _safe_str(kind) or "statement",
        "created_tick": int(created_tick or 0),
        "source": _safe_str(source) or "template",
    }


def list_active_conversations(simulation_state: Dict[str, Any], location_id: str = "") -> List[Dict[str, Any]]:
    ensure_conversation_state(simulation_state)
    rows = _safe_list(simulation_state["social_state"]["conversations"]["active"])
    if not location_id:
        return [dict(x) for x in rows if isinstance(x, dict)]
    return [dict(x) for x in rows if isinstance(x, dict) and _safe_str(x.get("location_id")) == _safe_str(location_id)]


def list_recent_conversations(simulation_state: Dict[str, Any], location_id: str = "") -> List[Dict[str, Any]]:
    ensure_conversation_state(simulation_state)
    rows = _safe_list(simulation_state["social_state"]["conversations"]["recent"])
    if not location_id:
        return [dict(x) for x in rows if isinstance(x, dict)]
    return [dict(x) for x in rows if isinstance(x, dict) and _safe_str(x.get("location_id")) == _safe_str(location_id)]


def upsert_conversation(simulation_state: Dict[str, Any], conversation: Dict[str, Any]) -> Dict[str, Any]:
    ensure_conversation_state(simulation_state)
    conversation = _safe_dict(conversation)
    cid = _safe_str(conversation.get("conversation_id"))
    if not cid:
        return simulation_state

    conversations = simulation_state["social_state"]["conversations"]
    active = _safe_list(conversations.get("active"))
    replaced = False
    new_active: List[Dict[str, Any]] = []
    for row in active:
        row = _safe_dict(row)
        if _safe_str(row.get("conversation_id")) == cid:
            new_active.append(dict(conversation))
            replaced = True
        else:
            new_active.append(dict(row))
    if not replaced and _safe_str(conversation.get("status")) == "active":
        new_active.append(dict(conversation))

    new_active = sorted(
        new_active,
        key=lambda x: (
            int(_safe_dict(x).get("updated_tick", 0) or 0),
            _safe_str(_safe_dict(x).get("conversation_id")),
        ),
    )
    conversations["active"] = _trim_list(new_active, _MAX_ACTIVE_CONVERSATIONS)
    return simulation_state


def append_conversation_line(simulation_state: Dict[str, Any], conversation_id: str, line: Dict[str, Any]) -> Dict[str, Any]:
    ensure_conversation_state(simulation_state)
    conversation_id = _safe_str(conversation_id)
    line = _safe_dict(line)
    lines_by_conversation = simulation_state["social_state"]["conversations"]["lines_by_conversation"]
    rows = _safe_list(lines_by_conversation.get(conversation_id))
    rows.append(dict(line))
    lines_by_conversation[conversation_id] = _trim_list(rows, _MAX_LINES_PER_CONVERSATION)
    return simulation_state


def get_conversation_lines(simulation_state: Dict[str, Any], conversation_id: str) -> List[Dict[str, Any]]:
    ensure_conversation_state(simulation_state)
    rows = _safe_list(
        simulation_state["social_state"]["conversations"]["lines_by_conversation"].get(_safe_str(conversation_id))
    )
    return [dict(x) for x in rows if isinstance(x, dict)]


def close_conversation(simulation_state: Dict[str, Any], conversation_id: str, reason: str = "") -> Dict[str, Any]:
    ensure_conversation_state(simulation_state)
    conversation_id = _safe_str(conversation_id)
    conversations = simulation_state["social_state"]["conversations"]
    active = _safe_list(conversations.get("active"))
    recent = _safe_list(conversations.get("recent"))

    kept_active: List[Dict[str, Any]] = []
    moved: Optional[Dict[str, Any]] = None
    for row in active:
        row = _safe_dict(row)
        if _safe_str(row.get("conversation_id")) == conversation_id:
            row["status"] = "closed"
            row["close_reason"] = _safe_str(reason)
            moved = dict(row)
        else:
            kept_active.append(dict(row))

    conversations["active"] = kept_active
    if moved:
        recent.append(moved)
        recent = sorted(
            recent,
            key=lambda x: (
                int(_safe_dict(x).get("updated_tick", 0) or 0),
                _safe_str(_safe_dict(x).get("conversation_id")),
            ),
        )
        conversations["recent"] = _trim_list(recent, _MAX_RECENT_CONVERSATIONS)
    return simulation_state


def trim_conversation_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    ensure_conversation_state(simulation_state)
    conversations = simulation_state["social_state"]["conversations"]
    conversations["active"] = _trim_list(_safe_list(conversations.get("active")), _MAX_ACTIVE_CONVERSATIONS)
    conversations["recent"] = _trim_list(_safe_list(conversations.get("recent")), _MAX_RECENT_CONVERSATIONS)

    lines_by_conversation = _safe_dict(conversations.get("lines_by_conversation"))
    normalized: Dict[str, List[Dict[str, Any]]] = {}
    for cid, rows in sorted(lines_by_conversation.items()):
        normalized[_safe_str(cid)] = _trim_list(
            [dict(x) for x in _safe_list(rows) if isinstance(x, dict)],
            _MAX_LINES_PER_CONVERSATION,
        )
    conversations["lines_by_conversation"] = normalized
    return simulation_state
