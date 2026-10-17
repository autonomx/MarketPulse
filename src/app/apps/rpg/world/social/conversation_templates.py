from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str

_MAX_LINE_LEN = 220


def _truncate_line(text: str) -> str:
    text = _safe_str(text).strip()
    if len(text) <= _MAX_LINE_LEN:
        return text
    return text[: _MAX_LINE_LEN - 1].rstrip() + "\u2026"


def _speaker_role(simulation_state: Dict[str, Any], speaker_id: str) -> str:
    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    row = _safe_dict(npc_index.get(speaker_id))
    return _safe_str(row.get("role")).lower()


def _speaker_name(simulation_state: Dict[str, Any], speaker_id: str) -> str:
    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    row = _safe_dict(npc_index.get(speaker_id))
    return _safe_str(row.get("name")) or _safe_str(speaker_id)


def build_template_line(conversation: Dict[str, Any], speaker_id: str, simulation_state: Dict[str, Any], runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    conversation = _safe_dict(conversation)
    topic = _safe_dict(conversation.get("topic"))
    topic_type = _safe_str(topic.get("type")).lower()
    stance = _safe_str(topic.get("stance")).lower()
    summary = _safe_str(topic.get("summary"))
    role = _speaker_role(simulation_state, speaker_id)
    name = _speaker_name(simulation_state, speaker_id)

    text = ""
    kind = "statement"

    if topic_type == "plan_reaction":
        if role in {"guard", "innkeeper", "priest"}:
            text = "You are certain this plan is wise?"
            kind = "question"
        elif role in {"thief", "mercenary", "adventurer"}:
            text = "Why not? We will learn more inside than standing here."
            kind = "challenge"
        else:
            text = "If we are doing this, we should do it with open eyes."
            kind = "statement"
    elif topic_type == "moral_conflict":
        if role in {"innkeeper", "guard", "priest"}:
            text = "You speak lightly of theft, but towns are not held together by excuses."
            kind = "challenge"
        elif role == "thief":
            text = "Morality is easy to praise when your stomach is full."
            kind = "challenge"
        else:
            text = "There is more at stake here than easy judgment."
            kind = "statement"
    elif topic_type == "risk_conflict":
        if stance == "disagree":
            text = "Rushing into danger without a better plan is a fine way to get buried."
            kind = "warning"
        else:
            text = "Danger does not lessen because we stare at it from afar."
            kind = "challenge"
    elif topic_type in {"local_incident", "event_commentary"}:
        text = summary or "Something about this does not sit right."
        kind = "statement"
    elif topic_type == "ambient_chat":
        if role in {"innkeeper", "merchant", "bartender", "shopkeeper"}:
            text = "Business has its own rhythm, but the mood around here has changed."
            kind = "statement"
        elif role in {"guard", "watchman", "soldier"}:
            text = "Keep your eyes open. Trouble has a way of arriving quietly."
            kind = "warning"
        elif role in {"thief", "mercenary", "adventurer"}:
            text = "Quiet places make me nervous. Something usually follows."
            kind = "statement"
        else:
            text = "There is always something worth noticing if you listen closely."
            kind = "statement"
    else:
        text = "There is something we should discuss before we move on."
        kind = "statement"

    return {
        "speaker": _safe_str(speaker_id),
        "text": _truncate_line(text),
        "kind": kind,
        "speaker_name": name,
    }
