from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.world.npc_reputation_state import update_npc_reputation
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def classify_player_conversation_reputation_event(
    *,
    player_input: str,
    topic_pivot: Dict[str, Any],
    conversation_result: Dict[str, Any],
) -> Dict[str, Any]:
    text = _safe_str(player_input).strip().lower()
    pivot = _safe_dict(topic_pivot)
    conversation = _safe_dict(conversation_result)

    if any(word in text for word in ("thank", "thanks", "appreciate", "helpful")):
        return {
            "kind": "polite_cooperation",
            "familiarity_delta": 1,
            "trust_delta": 1,
            "respect_delta": 1,
            "reason": "player_polite_or_cooperative",
        }

    if any(word in text for word in ("liar", "shut up", "idiot", "threaten")):
        return {
            "kind": "hostile_social_reply",
            "familiarity_delta": 1,
            "trust_delta": -1,
            "annoyance_delta": 2,
            "fear_delta": 1,
            "reason": "player_hostile_social_reply",
        }

    if pivot.get("requested") and pivot.get("accepted"):
        return {
            "kind": "backed_topic_cooperation",
            "familiarity_delta": 1,
            "trust_delta": 1,
            "respect_delta": 1,
            "reason": "player_asked_backed_topic",
        }

    if pivot.get("requested") and not pivot.get("accepted"):
        return {
            "kind": "unbacked_topic_pressure",
            "familiarity_delta": 1,
            "trust_delta": 0,
            "annoyance_delta": 1,
            "reason": "player_pressed_unbacked_topic",
        }

    if _safe_str(conversation.get("reason")) == "pending_player_response_expired":
        return {
            "kind": "ignored_invitation",
            "familiarity_delta": 0,
            "trust_delta": -1,
            "annoyance_delta": 1,
            "reason": "player_ignored_invitation",
        }

    return {
        "kind": "ordinary_conversation_reply",
        "familiarity_delta": 1,
        "trust_delta": 0,
        "respect_delta": 0,
        "reason": "ordinary_conversation_reply",
    }


def apply_player_reputation_consequence(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    player_input: str,
    topic_pivot: Dict[str, Any],
    conversation_result: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    event = classify_player_conversation_reputation_event(
        player_input=player_input,
        topic_pivot=topic_pivot,
        conversation_result=conversation_result,
    )

    result = update_npc_reputation(
        simulation_state,
        npc_id=npc_id,
        tick=tick,
        familiarity_delta=int(event.get("familiarity_delta") or 0),
        trust_delta=int(event.get("trust_delta") or 0),
        annoyance_delta=int(event.get("annoyance_delta") or 0),
        fear_delta=int(event.get("fear_delta") or 0),
        respect_delta=int(event.get("respect_delta") or 0),
        reason=_safe_str(event.get("reason")),
    )

    return {
        "applied": bool(result.get("updated")),
        "event": deepcopy(event),
        "reputation_update": deepcopy(result),
        "source": "deterministic_player_reputation_consequence_runtime",
    }
