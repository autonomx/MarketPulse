from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


_RUMOR_ELIGIBLE_TOPIC_TYPES = frozenset({
    "moral_conflict", "plan_reaction", "event_commentary", "faction_tension",
    "local_incident", "risk_conflict",
})


def conversation_can_generate_rumor(conversation: Dict[str, Any]) -> bool:
    """Determine if a closed conversation should generate a rumor.

    Only meaningful topic types generate rumors, and the conversation
    must have had at least 2 turns of actual dialogue.
    """
    conversation = _safe_dict(conversation)
    topic = _safe_dict(conversation.get("topic"))
    topic_type = _safe_str(topic.get("type"))
    turn_count = int(conversation.get("turn_count", 0) or 0)
    return topic_type in _RUMOR_ELIGIBLE_TOPIC_TYPES and turn_count >= 2


def build_rumor_from_conversation(conversation: Dict[str, Any]) -> Dict[str, Any]:
    """Build a rumor entry from a completed conversation.

    Rumor text is generated from the conversation topic and participants.
    These feed back into the simulation as social_state rumors and can
    influence NPC topic generation on future ticks.
    """
    conversation = _safe_dict(conversation)
    topic = _safe_dict(conversation.get("topic"))
    participants = [_safe_str(x) for x in _safe_list(conversation.get("participants")) if _safe_str(x)]
    topic_type = _safe_str(topic.get("type"))
    anchor = _safe_str(topic.get("anchor"))
    summary = _safe_str(topic.get("summary"))

    # Build spread-worthy rumor text based on topic type
    if topic_type == "moral_conflict" and len(participants) >= 2:
        rumor_text = f"People say {participants[0]} and {participants[1]} had a heated argument about right and wrong."
    elif topic_type == "plan_reaction":
        rumor_text = "Word is spreading that NPCs are reacting to the player's recent plan."
    elif topic_type == "faction_tension":
        rumor_text = "Tensions are rising within the faction. People are talking."
    elif topic_type == "risk_conflict":
        rumor_text = "There is disagreement about whether a risky course of action is wise."
    elif topic_type == "local_incident":
        rumor_text = summary or "People are worried about a disturbance nearby."
    else:
        rumor_text = summary or "People are talking."

    return {
        "type": "rumor",
        "anchor": anchor,
        "summary": rumor_text,
        "source_conversation_id": _safe_str(conversation.get("conversation_id")),
        "participants": participants[:4],
        "topic_type": topic_type,
        "created_tick": int(conversation.get("updated_tick", 0) or 0),
    }


def collect_rumors_from_recent_conversations(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Scan recent (closed) conversations and build rumors from eligible ones.

    Only conversations that haven't already produced a rumor are processed.
    """
    from .npc_conversations import ensure_conversation_state
    ensure_conversation_state(simulation_state)
    conversations = _safe_dict(_safe_dict(simulation_state.get("social_state")).get("conversations"))
    recent = _safe_list(conversations.get("recent"))

    rumors: List[Dict[str, Any]] = []
    seen_ids = set()
    for conv in recent:
        conv = _safe_dict(conv)
        cid = _safe_str(conv.get("conversation_id"))
        if not cid or cid in seen_ids:
            continue
        seen_ids.add(cid)
        if conversation_can_generate_rumor(conv):
            rumors.append(build_rumor_from_conversation(conv))
    return rumors[:10]
