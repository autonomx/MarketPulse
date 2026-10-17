"""Data-driven quest transitions from authoritative evidence events."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

QUEST_EVIDENCE_VERSION = "rpg_quest_evidence_v1"

# Template defaults migrate older sessions. Newly generated quests should carry
# equivalent ``transition_rules`` and ``canonical_clues`` in their own data.
QUEST_TEMPLATE_RULES: Dict[str, Dict[str, Any]] = {
    "tavern_rumor": {
        "transition_rules": [
            {
                "event_kind": "dialogue_clue_received",
                "actor_refs_any": ["npc:Bran", "npc:bran"],
                "clue_tags_any": ["quest:tavern_rumor"],
                "next_objective_id": "investigate_old_mill_road",
                "next_objective": "Investigate the strange lights near the old mill road.",
            }
        ],
        "canonical_clues": {
            "bran_rumor": {
                "clue_id": "clue:tavern_rumor:old_mill_lights",
                "summary": "A frightened traveler reported strange lights and armed men near the old mill road.",
                "tags": ["quest:tavern_rumor", "location:old_mill_road", "threat:armed_men"],
            }
        },
    }
}
QUEST_SERVICE_CLUE_RULES: List[Dict[str, str]] = [
    {
        "provider_id": "npc:Bran",
        "service_kind": "paid_information",
        "interaction_kind": "service_inquiry",
        "quest_id": "tavern_rumor",
        "clue_key": "bran_rumor",
    }
]


def authoritative_dialogue_clue(
    *,
    quest_id: str,
    actor_ref: str,
    clue_key: str,
    tick: int,
) -> Dict[str, Any]:
    """Build an evidence event only from registered quest data."""

    definition = _dict(QUEST_TEMPLATE_RULES.get(str(quest_id)))
    clue = _dict(_dict(definition.get("canonical_clues")).get(clue_key))
    if not clue:
        return {}
    return {
        "schema_version": QUEST_EVIDENCE_VERSION,
        "event_kind": "dialogue_clue_received",
        "quest_id": str(quest_id),
        "actor_ref": str(actor_ref),
        "clue_id": str(clue.get("clue_id") or clue_key),
        "clue_summary": str(clue.get("summary") or ""),
        "clue_tags": [str(tag) for tag in _list(clue.get("tags")) if tag],
        "tick": int(tick or 0),
        "source": "registered_quest_clue",
    }


def clue_for_service_interaction(service_result: Dict[str, Any], *, tick: int) -> Dict[str, Any]:
    """Return a registered clue for a provider/service interaction, if any."""

    result = _dict(service_result)
    for rule in QUEST_SERVICE_CLUE_RULES:
        if str(result.get("provider_id") or "") != rule["provider_id"]:
            continue
        if str(result.get("service_kind") or "") != rule["service_kind"]:
            continue
        if str(result.get("kind") or "") != rule["interaction_kind"]:
            continue
        return authoritative_dialogue_clue(
            quest_id=rule["quest_id"],
            actor_ref=rule["provider_id"],
            clue_key=rule["clue_key"],
            tick=tick,
        )
    return {}


def apply_quest_evidence(
    simulation_state: Dict[str, Any],
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply the first matching data-driven transition exactly once."""

    state = simulation_state if isinstance(simulation_state, dict) else {}
    event = _dict(evidence)
    quest_state = _dict(state.get("quest_state"))
    quests = _list(quest_state.get("quests"))
    quest_id = str(event.get("quest_id") or "")
    for quest in quests:
        quest = _dict(quest)
        if str(quest.get("id") or quest.get("quest_id") or "") != quest_id:
            continue
        evidence_log = _list(quest.get("evidence"))
        clue_id = str(event.get("clue_id") or "")
        existing_evidence = next(
            (
                _dict(row)
                for row in evidence_log
                if clue_id and str(_dict(row).get("clue_id") or "") == clue_id
            ),
            {},
        )
        if existing_evidence:
            return {
                "applied": False,
                "reason": "quest_evidence_already_applied",
                "quest_id": quest_id,
                "clue_id": clue_id,
                "objective_id": str(quest.get("objective_id") or ""),
                "objective": str(quest.get("objective") or ""),
                "evidence": deepcopy(existing_evidence),
                "source": "deterministic_quest_evidence_runtime",
            }
        definition = _quest_definition(quest)
        for rule in _list(definition.get("transition_rules")):
            rule = _dict(rule)
            if not _rule_matches(rule, event):
                continue
            before = str(quest.get("objective") or "")
            quest["objective_id"] = str(rule.get("next_objective_id") or "")
            quest["objective"] = str(rule.get("next_objective") or before)
            quest["status"] = "active"
            quest["evidence"] = [*evidence_log, deepcopy(event)][-20:]
            transition = {
                "schema_version": QUEST_EVIDENCE_VERSION,
                "applied": True,
                "quest_id": quest_id,
                "previous_objective": before,
                "objective_id": quest["objective_id"],
                "objective": quest["objective"],
                "evidence": deepcopy(event),
                "source": "deterministic_quest_evidence_runtime",
            }
            quest_state["last_transition"] = deepcopy(transition)
            state["quest_state"] = quest_state
            return transition
    return {
        "applied": False,
        "reason": "no_matching_quest_transition",
        "quest_id": quest_id,
        "source": "deterministic_quest_evidence_runtime",
    }


def _quest_definition(quest: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(quest.get("transition_rules"), list):
        return quest
    quest_id = str(quest.get("id") or quest.get("quest_id") or "")
    return _dict(QUEST_TEMPLATE_RULES.get(quest_id))


def _rule_matches(rule: Dict[str, Any], evidence: Dict[str, Any]) -> bool:
    if str(rule.get("event_kind") or "") != str(evidence.get("event_kind") or ""):
        return False
    actors = {str(value) for value in _list(rule.get("actor_refs_any"))}
    if actors and str(evidence.get("actor_ref") or "") not in actors:
        return False
    required_tags = {str(value) for value in _list(rule.get("clue_tags_any"))}
    evidence_tags = {str(value) for value in _list(evidence.get("clue_tags"))}
    return not required_tags or bool(required_tags & evidence_tags)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


__all__ = [
    "QUEST_EVIDENCE_VERSION",
    "QUEST_TEMPLATE_RULES",
    "apply_quest_evidence",
    "authoritative_dialogue_clue",
    "clue_for_service_interaction",
]
