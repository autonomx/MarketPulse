from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_knowledge_state import known_facts_for_npc
from app.apps.rpg.world.npc_reputation_state import get_npc_reputation
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

ACCESS_LEVELS = {"none", "partial", "normal", "trusted"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _topic_is_backed(topic: Dict[str, Any]) -> bool:
    topic = _safe_dict(topic)
    topic_id = _safe_str(topic.get("topic_id"))
    topic_type = _safe_str(topic.get("topic_type"))
    source_id = _safe_str(topic.get("source_id"))
    summary = _safe_str(topic.get("summary") or topic.get("title"))
    return bool(topic_id and topic_type and summary and (source_id or topic_id.startswith("topic:")))


def _npc_knows_topic(simulation_state: Dict[str, Any], *, npc_id: str, topic_id: str) -> bool:
    for fact in known_facts_for_npc(simulation_state, npc_id=npc_id, limit=12):
        if _safe_str(fact.get("source_topic_id")) == topic_id:
            return True
    return False


def evaluate_quest_conversation_access(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    topic: Dict[str, Any],
    player_input: str = "",
) -> Dict[str, Any]:
    """Decide how much backed quest detail an NPC may discuss.

    This is a presentation/access gate only. It does not create quests, rewards,
    journal entries, inventory, currency, stock, location changes, or combat.
    """
    npc_id = _safe_str(npc_id)
    topic = _safe_dict(topic)
    topic_id = _safe_str(topic.get("topic_id"))
    topic_type = _safe_str(topic.get("topic_type"))
    bio = get_npc_biography(npc_id)
    role = _safe_str(bio.get("role")).lower()
    reputation = get_npc_reputation(simulation_state, npc_id=npc_id)

    requested = (
        topic_type == "quest"
        or "quest" in topic_id
        or "trouble" in _safe_str(player_input).lower()
        or "quest" in _safe_str(player_input).lower()
    )

    if not requested:
        return {
            "requested": False,
            "access": "normal",
            "reason": "not_quest_conversation",
            "source": "deterministic_quest_conversation_access",
        }

    if not _topic_is_backed(topic):
        return {
            "requested": True,
            "topic_id": topic_id,
            "npc_id": npc_id,
            "access": "none",
            "reason": "unbacked_topic",
            "allowed_detail_level": 0,
            "blocked_detail_level": 3,
            "safe_deflection": "I have no reliable word of that.",
            "source": "deterministic_quest_conversation_access",
        }

    trust = _safe_int(reputation.get("trust"), 0)
    respect = _safe_int(reputation.get("respect"), 0)
    annoyance = _safe_int(reputation.get("annoyance"), 0)
    fear = _safe_int(reputation.get("fear"), 0)
    knows_topic = _npc_knows_topic(simulation_state, npc_id=npc_id, topic_id=topic_id)

    score = 1

    if knows_topic:
        score += 1
    if trust >= 2:
        score += 1
    if respect >= 2:
        score += 1
    if annoyance >= 3:
        score -= 1
    if fear >= 3:
        score -= 1

    if "guard" in role and topic_type == "quest":
        score += 1
    if "informant" in role and topic_type == "quest":
        score += 1
    if "tavern" in role and any(word in _safe_str(topic.get("summary")).lower() for word in ("road", "traveler", "tavern", "local")):
        score += 1
    if "merchant" in role and any(word in _safe_str(topic.get("summary")).lower() for word in ("road", "trade", "market", "goods")):
        score += 1

    if score <= 0:
        access = "none"
        reason = "known_topic_but_npc_unwilling"
        allowed = 0
    elif score == 1:
        access = "partial"
        reason = "backed_topic_low_trust_or_low_knowledge"
        allowed = 1
    elif score <= 3:
        access = "normal"
        reason = "backed_topic_normal_access"
        allowed = 2
    else:
        access = "trusted"
        reason = "backed_topic_trusted_access"
        allowed = 3

    deflection_by_access = {
        "none": "I know enough to be cautious, not enough to say more.",
        "partial": "I will keep this to what is reliable.",
        "normal": "",
        "trusted": "",
    }

    return {
        "requested": True,
        "topic_id": topic_id,
        "topic_type": topic_type,
        "npc_id": npc_id,
        "access": access,
        "reason": reason,
        "allowed_detail_level": allowed,
        "blocked_detail_level": max(0, 3 - allowed),
        "safe_deflection": deflection_by_access.get(access, ""),
        "npc_reputation": deepcopy(reputation),
        "npc_role": _safe_str(bio.get("role")),
        "npc_knows_topic": knows_topic,
        "source": "deterministic_quest_conversation_access",
    }


def requested_topic_access_from_pivot(topic_pivot: Dict[str, Any]) -> Dict[str, Any]:
    pivot = _safe_dict(topic_pivot)
    if not pivot.get("requested"):
        return {
            "requested": False,
            "source": "deterministic_requested_topic_access",
        }

    if pivot.get("accepted"):
        return {
            "requested": True,
            "accepted": True,
            "access": "backed",
            "reason": "requested_topic_backed",
            "requested_topic_hint": _safe_str(pivot.get("requested_topic_hint")),
            "selected_topic_id": _safe_str(
                pivot.get("selected_topic_id")
                or pivot.get("topic_id")
                or _safe_dict(pivot.get("selected_topic")).get("topic_id")
            ),
            "source": "deterministic_requested_topic_access",
        }

    reason = (
        _safe_str(pivot.get("pivot_rejected_reason"))
        or _safe_str(pivot.get("reason"))
        or "no_backed_topic_found"
    )

    return {
        "requested": True,
        "accepted": False,
        "access": "none",
        "reason": reason,
        "requested_topic_hint": _safe_str(pivot.get("requested_topic_hint")),
        "safe_deflection": "I have no reliable word of that.",
        "source": "deterministic_requested_topic_access",
    }


def filter_allowed_topic_facts_for_access(
    topic: Dict[str, Any],
    *,
    access: Dict[str, Any],
) -> List[str]:
    topic = _safe_dict(topic)
    access = _safe_dict(access)
    allowed_level = _safe_int(access.get("allowed_detail_level"), 1)
    facts = [_safe_str(fact) for fact in _safe_list(topic.get("allowed_facts")) if _safe_str(fact)]

    summary = _safe_str(topic.get("summary"))
    title = _safe_str(topic.get("title"))

    if not facts and summary:
        facts = [summary]
    if not facts and title:
        facts = [title]

    if allowed_level <= 0:
        return []
    if allowed_level == 1:
        return facts[:1]
    if allowed_level == 2:
        return facts[:3]
    return facts[:6]
