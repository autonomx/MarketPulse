from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_knowledge_state import known_facts_for_npc
from app.apps.rpg.world.npc_presence_runtime import present_npcs_at_location
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


REFERRAL_REQUEST_MARKERS = {
    "who should i ask",
    "who do i ask",
    "who can i ask",
    "who knows",
    "who would know",
    "ask about",
    "where should i go",
    "who can tell me",
}


def player_input_requests_referral(player_input: Any) -> bool:
    text = _safe_str(player_input).lower()
    return any(marker in text for marker in REFERRAL_REQUEST_MARKERS)


def _role_score_for_topic(role: str, topic: Dict[str, Any]) -> int:
    role = _safe_str(role).lower()
    topic_text = " ".join(
        _safe_str(topic.get(key)).lower()
        for key in ("topic_type", "title", "summary", "topic_id")
    )

    score = 0
    if "guard" in role and any(word in topic_text for word in ("trouble", "armed", "road", "danger", "report")):
        score += 3
    if "informant" in role and any(word in topic_text for word in ("rumor", "pattern", "whisper", "secret")):
        score += 3
    if "tavern" in role and any(word in topic_text for word in ("traveler", "local", "road", "rumor")):
        score += 2
    if "merchant" in role and any(word in topic_text for word in ("road", "market", "trade", "goods")):
        score += 2
    return score


def suggest_npc_referral(
    simulation_state: Dict[str, Any],
    *,
    speaker_id: str,
    topic: Dict[str, Any],
    access: Dict[str, Any],
    requested_topic_access: Dict[str, Any] | None = None,
    player_input: str = "",
) -> Dict[str, Any]:
    topic = _safe_dict(topic)
    access = _safe_dict(access)
    speaker_id = _safe_str(speaker_id)

    if not topic:
        return {"suggested": False, "reason": "missing_topic"}

    requested_topic_access = _safe_dict(requested_topic_access)
    explicit_referral_request = player_input_requests_referral(player_input)

    access_requested = bool(access.get("requested"))
    requested_topic_requested = bool(requested_topic_access.get("requested"))
    topic_type = _safe_str(topic.get("topic_type"))
    topic_id = _safe_str(topic.get("topic_id"))
    questish_topic = topic_type == "quest" or topic_id.startswith("topic:quest:")

    if not (explicit_referral_request or access_requested or requested_topic_requested or questish_topic):
        return {
            "suggested": False,
            "reason": "referral_not_relevant_for_turn",
            "source": "deterministic_npc_referral_runtime",
        }

    location_id = current_location_id(simulation_state)
    present = present_npcs_at_location(simulation_state, location_id=location_id)

    candidates: List[Dict[str, Any]] = []
    for npc_id in present:
        npc_id = _safe_str(npc_id)
        if not npc_id.startswith("npc:") or npc_id == speaker_id:
            continue
        bio = get_npc_biography(npc_id)
        role = _safe_str(bio.get("role"))
        score = _role_score_for_topic(role, topic)

        for fact in known_facts_for_npc(simulation_state, npc_id=npc_id, limit=8):
            if _safe_str(fact.get("source_topic_id")) == _safe_str(topic.get("topic_id")):
                score += 2

        if score <= 0:
            continue

        candidates.append({
            "npc_id": npc_id,
            "name": _safe_str(bio.get("name")) or npc_id.replace("npc:", ""),
            "role": role,
            "score": score,
        })

    if not candidates:
        return {
            "suggested": False,
            "reason": "no_better_present_npc",
            "source": "deterministic_npc_referral_runtime",
        }

    candidates.sort(key=lambda item: (int(item.get("score") or 0), _safe_str(item.get("npc_id"))), reverse=True)
    selected = candidates[0]

    return {
        "suggested": True,
        "referral_npc_id": selected["npc_id"],
        "referral_name": selected["name"],
        "referral_role": selected["role"],
        "reason": "present_npc_role_or_knowledge_matches_topic",
        "line_hint": f"Ask {selected['name']} if you need a better answer.",
        "source": "deterministic_npc_referral_runtime",
    }
