from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_dialogue_recall import (
    player_input_requests_recall,
    select_dialogue_recall,
)
from app.apps.rpg.world.npc_evolution_state import get_npc_evolution, merged_npc_identity
from app.apps.rpg.world.npc_history_state import recent_npc_history
from app.apps.rpg.world.npc_knowledge_state import known_facts_for_npc
from app.apps.rpg.world.npc_reputation_state import (
    get_npc_reputation,
    response_style_from_reputation,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

FORBIDDEN_NPC_DIALOGUE_CLAIMS = [
    "Do not create or complete quests.",
    "Do not grant rewards.",
    "Do not create journal entries.",
    "Do not create items.",
    "Do not change inventory, currency, shop stock, location, or combat state.",
    "Do not claim hidden facts unless they are present in deterministic allowed facts.",
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _npc_social_entry(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    social_state = _safe_dict(simulation_state.get("conversation_social_state"))
    by_npc = _safe_dict(social_state.get("npc_state") or social_state.get("npcs"))
    return _safe_dict(by_npc.get(npc_id))


def _npc_goal_entry(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    goal_state = _safe_dict(simulation_state.get("npc_goal_state"))
    goals_by_npc = _safe_dict(goal_state.get("goals"))
    goals = _safe_list(goals_by_npc.get(npc_id))
    active_goals = [
        _safe_dict(goal)
        for goal in goals
        if _safe_str(_safe_dict(goal).get("status") or "active") == "active"
    ]
    if not active_goals:
        return {}
    active_goals.sort(
        key=lambda goal: (
            _safe_int(goal.get("priority"), 0),
            -_safe_int(goal.get("created_tick"), 0),
        ),
        reverse=True,
    )
    return deepcopy(active_goals[0])


def _allowed_facts_from_topic(topic: Dict[str, Any]) -> List[str]:
    topic = _safe_dict(topic)
    facts = []
    for value in _safe_list(topic.get("allowed_facts")):
        text = _safe_str(value).strip()
        if text:
            facts.append(text)
    summary = _safe_str(topic.get("summary")).strip()
    if summary and summary not in facts:
        facts.append(summary)
    title = _safe_str(topic.get("title")).strip()
    if title and title not in facts:
        facts.append(title)
    return facts[:8]


def build_npc_dialogue_profile(
    *,
    npc_id: str,
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
    topic: Dict[str, Any] | None = None,
    listener_id: str = "",
    response_intent: str = "comment",
) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    topic = _safe_dict(topic)

    biography = get_npc_biography(npc_id)
    evolution = get_npc_evolution(simulation_state, npc_id=_safe_str(biography.get("npc_id")))
    base_biography = biography
    biography = merged_npc_identity(base_profile=biography, evolution=evolution)
    social = _npc_social_entry(simulation_state, _safe_str(biography.get("npc_id")))
    active_goal = _npc_goal_entry(simulation_state, _safe_str(biography.get("npc_id")))
    allowed_facts = _allowed_facts_from_topic(topic)
    history = recent_npc_history(simulation_state, npc_id=_safe_str(biography.get("npc_id")), limit=5)
    reputation = get_npc_reputation(simulation_state, npc_id=_safe_str(biography.get("npc_id")))
    known_facts = known_facts_for_npc(simulation_state, npc_id=_safe_str(biography.get("npc_id")), limit=6)
    player_input = _safe_str(runtime_state.get("player_input") or runtime_state.get("latest_player_input"))
    dialogue_recall = select_dialogue_recall(
        simulation_state,
        npc_id=_safe_str(biography.get("npc_id")),
        topic=topic,
        tick=_safe_int(runtime_state.get("tick"), 0),
        player_input=player_input,
    ) if runtime_state.get("enable_dialogue_recall", True) else {"selected": False}

    profile = {
        "npc_id": _safe_str(biography.get("npc_id")),
        "name": _safe_str(biography.get("name")),
        "role": _safe_str(
            biography.get("current_role")
            or biography.get("role")
            or biography.get("starting_role")
        ),
        "base_role": _safe_str(biography.get("base_role")),
        "starting_role": _safe_str(biography.get("starting_role")),
        "current_role": _safe_str(biography.get("current_role") or biography.get("role")),
        "identity_arc": _safe_str(biography.get("identity_arc")),
        "personality_modifiers": deepcopy(_safe_list(biography.get("personality_modifiers"))),
        "active_motivations": deepcopy(_safe_list(biography.get("active_motivations"))),
        "party_join_eligibility": deepcopy(_safe_dict(biography.get("party_join_eligibility"))),
        "npc_evolution": deepcopy(evolution),
        "short_bio": _safe_str(biography.get("short_bio")),
        "personality_traits": _safe_list(biography.get("personality_traits"))[:8],
        "speaking_style": deepcopy(_safe_dict(biography.get("speaking_style"))),
        "values": _safe_list(biography.get("values"))[:8],
        "fears": _safe_list(biography.get("fears"))[:8],
        "relationships": deepcopy(_safe_dict(biography.get("relationships"))),
        "knowledge_boundaries": deepcopy(_safe_dict(biography.get("knowledge_boundaries"))),
        "relationship_to_listener": _safe_str(_safe_dict(biography.get("relationships")).get(listener_id)),
        "conversation_social_state": deepcopy(social),
        "active_goal": deepcopy(active_goal),
        "topic_id": _safe_str(topic.get("topic_id")),
        "topic_type": _safe_str(topic.get("topic_type")),
        "topic_title": _safe_str(topic.get("title")),
        "allowed_facts": allowed_facts,
        "used_fact_ids": [_safe_str(topic.get("topic_id"))] if _safe_str(topic.get("topic_id")) else [],
        "response_intent": response_intent,
        "forbidden_claims": list(FORBIDDEN_NPC_DIALOGUE_CLAIMS),
        "recent_history": deepcopy(history),
        "npc_reputation": deepcopy(reputation),
        "reputation_response_style": response_style_from_reputation(reputation, fallback="guarded"),
        "known_facts": deepcopy(known_facts),
        "dialogue_recall": deepcopy(dialogue_recall),
        "recall_requested": player_input_requests_recall(player_input),
        "quest_conversation_access": deepcopy(_safe_dict(topic.get("quest_conversation_access"))),
        "profile_source": _safe_str(biography.get("source")),
        "biography_source": _safe_str(base_biography.get("source")),
        "source": _safe_str(biography.get("source")) or "deterministic_npc_dialogue_profile",
    }
    return profile


def deterministic_biography_line(
    *,
    profile: Dict[str, Any],
    topic: Dict[str, Any] | None = None,
    pivot: Dict[str, Any] | None = None,
    response_style: str = "",
) -> Dict[str, Any]:
    profile = _safe_dict(profile)
    topic = _safe_dict(topic)
    pivot = _safe_dict(pivot)
    role = _safe_str(profile.get("current_role") or profile.get("role")) or "local"
    npc_id = _safe_str(profile.get("npc_id"))
    name = _safe_str(profile.get("name")) or npc_id.replace("npc:", "")
    style = _safe_str(response_style or profile.get("reputation_response_style") or profile.get("response_intent") or "guarded")
    traits = [_safe_str(v) for v in _safe_list(profile.get("personality_traits"))]
    facts = [_safe_str(v) for v in _safe_list(profile.get("allowed_facts")) if _safe_str(v)]
    fact = facts[0] if facts else ""

    pivot_requested = bool(pivot.get("requested"))
    pivot_accepted = bool(pivot.get("accepted"))
    pivot_reason = _safe_str(pivot.get("pivot_rejected_reason") or pivot.get("reason"))
    hint = _safe_str(pivot.get("requested_topic_hint"))

    quest_access = _safe_dict(profile.get("quest_conversation_access"))
    if quest_access.get("requested") and _safe_str(quest_access.get("access")) == "none":
        line = _safe_str(quest_access.get("safe_deflection")) or "I cannot say more about that."
        return {
            "line": line[:280],
            "roleplay_source": "deterministic_template",
            "biography_role": role,
            "biography_traits": traits[:5],
            "used_fact_ids": [],
            "response_style": style,
            "quest_conversation_access": quest_access,
            "source": "deterministic_biography_dialogue",
        }

    if quest_access.get("requested") and _safe_str(quest_access.get("access")) == "partial":
        prefix = _safe_str(quest_access.get("safe_deflection"))
        if prefix and fact:
            fact = f"{prefix} {fact}"

    if pivot_requested and not pivot_accepted:
        if "plainspoken" in traits or role.lower().startswith("tavern"):
            line = "I have no reliable word of that. I will not dress guesses up as fact."
        elif "curious" in traits:
            line = "I have heard no solid proof of that, only the shape of a question."
        elif "disciplined" in traits:
            line = "I cannot confirm that without a report or witness."
        else:
            line = "I do not know enough to call that true."
        return {
            "line": line,
            "roleplay_source": "deterministic_template",
            "biography_role": role,
            "biography_traits": traits[:5],
            "used_fact_ids": [],
            "response_style": style,
            "unbacked_hint": hint,
            "pivot_rejected_reason": pivot_reason or "no_backed_topic_found",
            "quest_conversation_access": quest_access,
            "source": "deterministic_biography_dialogue",
        }

    recall = _safe_dict(profile.get("dialogue_recall"))
    recalls = _safe_list(recall.get("recalls"))
    recall_prefix = ""
    if recall.get("selected") and recalls:
        first = _safe_dict(recalls[0])
        recall_summary = _safe_str(first.get("summary"))
        if recall_summary:
            if "tavern" in _safe_str(profile.get("role")).lower():
                recall_prefix = f"I remember you asking about this before: {recall_summary} "
            elif "informant" in _safe_str(profile.get("role")).lower():
                recall_prefix = f"I remember the thread you pulled earlier: {recall_summary} "
            elif "guard" in _safe_str(profile.get("role")).lower():
                recall_prefix = f"I remember the earlier report: {recall_summary} "
            else:
                recall_prefix = f"I remember this: {recall_summary} "

    if fact:
        if role.lower().startswith("tavern"):
            line = f"I keep a tavern, not a hero's ledger. But I can tell you this much: {fact}"
        elif "informant" in role.lower() or "curious" in traits:
            line = f"That lines up with what people keep circling around: {fact}"
        elif "guard" in role.lower():
            line = f"Keep to what can be verified: {fact}"
        elif "merchant" in role.lower():
            line = f"If it affects the roads or trade, it matters. What I know is this: {fact}"
        else:
            line = fact
    else:
        if "guarded" in traits:
            line = "I would rather not build a story out of smoke."
        elif "curious" in traits:
            line = "There is something there, but not enough to name it yet."
        else:
            line = "I do not know enough to say more."

    identity_arc = _safe_str(profile.get("identity_arc"))
    motivations = _safe_list(profile.get("active_motivations"))
    modifiers = _safe_list(profile.get("personality_modifiers"))

    evolution_prefix = ""
    if identity_arc:
        evolution_prefix = f"As a {profile.get('current_role')}, "
    elif motivations:
        first_motivation = _safe_dict(motivations[0])
        summary = _safe_str(first_motivation.get("summary"))
        if summary:
            evolution_prefix = f"With {summary.lower()}, "

    if evolution_prefix and line:
        line = evolution_prefix + line[0].lower() + line[1:]

    line = (recall_prefix + line)[:280]

    return {
        "line": line,
        "roleplay_source": "deterministic_template",
        "biography_role": role,
        "biography_traits": traits[:5],
        "used_fact_ids": _safe_list(profile.get("used_fact_ids"))[:8],
        "response_style": style,
        "quest_conversation_access": quest_access,
        "source": "deterministic_biography_dialogue",
    }
