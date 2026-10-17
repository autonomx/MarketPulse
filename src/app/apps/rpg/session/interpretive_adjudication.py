"""World-grounded interpretive adjudication for meaningful non-mechanical RPG input.

This layer sits between first-call safe dialogue and hard deterministic runtime.
It is intentionally non-mutating: it may explain, refuse, doubt, or contextualize
an unsupported/implausible prompt in-world, but it must not change inventory,
currency, quest, combat, travel, or relationship state.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

_INTERPRETIVE_SOURCE = "world_grounded_interpretive_adjudication_v1"
_GENERIC_PARSE_FAILURE_REASONS = {
    "missing_visible_response_text",
    "no_safe_non_stateful_visible_response",
}
_CLIENT_CORRUPTION_MARKERS = ("[object object]", "undefined", "null")
_HARD_MECHANIC_TERMS = (
    " buy ",
    " sell ",
    " attack ",
    " hit ",
    " stab ",
    " shoot ",
    " cast ",
    " equip ",
    " unequip ",
    " travel ",
    " go to ",
    " take ",
    " steal ",
    " pay ",
    " hire ",
    " join me",
)

INTENT_FAMILIES = {
    "observation_request": "observation",
    "npc_capability_request": "npc_request",
    "unverified_player_claim": "claim",
    "unverified_debt_claim": "claim",
    "memory_claim": "claim",
    "lore_conflict_claim": "claim",
    "social_probe": "social",
    "unsupported_mechanic_request": "unsupported_mechanic",
    "unsupported_but_diegetic_action": "diegetic_noop",
    "hypothetical_counterfactual": "hypothetical",
}


def classify_interpretive_intent(
    *,
    player_input: str,
    semantic_advisory: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
) -> str:
    """Return a non-mutating interpretive intent category, or an empty string."""

    text = _norm_words(player_input)
    if not _looks_like_meaningful_player_input(text):
        return ""

    selection = _d(selection)
    if selection.get("consumable"):
        return ""
    if selection.get("reason") == "service_or_commerce_runtime_wins":
        return ""

    from app.apps.rpg.session.hypothetical_world_resolution import (
        HYPOTHETICAL_INTENT,
        looks_like_hypothetical_input,
    )

    if looks_like_hypothetical_input(player_input):
        return HYPOTHETICAL_INTENT

    if re.search(r"\bowe[sd]? me\b", text) or re.search(r"\byou owe\b", text):
        return "unverified_debt_claim"
    if re.search(r"\bremember me\b|\bwe met before\b|\byou know me\b|\byou promised\b", text):
        return "memory_claim"
    if _looks_like_lore_conflict_claim(text):
        return "lore_conflict_claim"
    if re.search(r"\bask\b.+\bto\b", text):
        return "npc_capability_request"
    if re.search(r"\bi (tell|claim|say|said|used to|was|am|were)\b", text):
        return "unverified_player_claim"
    if re.search(r"\bdo you trust me\b|\btrust me\b|\bwhat do you think of me\b", text):
        return "social_probe"
    if re.search(r"\blook(s|ed|ing)? around\b", text) or text in {"look", "look around"}:
        return "observation_request"
    if _looks_like_unsupported_mechanic_request(text):
        return "unsupported_mechanic_request"

    compact = f" {text} "
    if any(term in compact for term in _HARD_MECHANIC_TERMS):
        return ""

    advisory = _d(semantic_advisory)
    action_type = _s(advisory.get("action_type")).lower()
    family = _s(advisory.get("semantic_family")).lower()
    mode = _s(advisory.get("interaction_mode")).lower()
    target = _s(advisory.get("target_id") or advisory.get("target_name"))
    reason = _s(selection.get("reason"))
    if (
        reason in _GENERIC_PARSE_FAILURE_REASONS
        and (mode == "direct" or family == "social" or action_type == "social_activity" or target.startswith("npc:"))
    ):
        return "unsupported_but_diegetic_action"
    return ""


def interpretive_intent_family(intent: str) -> str:
    return INTENT_FAMILIES.get(_s(intent), "")


def build_interpretive_fact_constraints(
    *,
    intent: str,
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    grounding_packet: dict[str, Any],
) -> dict[str, Any]:
    """Return explicit constraints that keep interpretive answers fact-bound."""

    family = interpretive_intent_family(intent)
    authoritative = _d(_d(grounding_packet).get("authoritative_state"))
    return {
        "format_version": "interpretive_fact_constraints_v1",
        "intent": _s(intent),
        "intent_family": family,
        "may_mutate_state": False,
        "may_transfer_currency": False,
        "may_create_or_confirm_debt": False,
        "may_add_inventory": False,
        "may_remove_inventory": False,
        "may_complete_quest": False,
        "may_start_combat": False,
        "may_move_player": False,
        "may_change_relationship": False,
        "may_reveal_private_context": False,
        "may_assert_unverified_player_history": False,
        "must_frame_claims_as_unverified": family == "claim",
        "must_require_proof_for_debt_or_memory": intent in {"unverified_debt_claim", "memory_claim"},
        "must_respect_lore_plausibility": intent == "lore_conflict_claim",
        "must_preserve_current_currency": True,
        "must_preserve_current_inventory": True,
        "verified_facts": {
            "currency": deepcopy(_d(simulation_state).get("currency") or authoritative.get("currency") or {}),
            "inventory": deepcopy(_d(simulation_state).get("inventory") or authoritative.get("inventory") or []),
            "location": deepcopy(_d(simulation_state).get("scene") or authoritative.get("scene") or {}),
            "runtime_tick": _d(runtime_state).get("tick"),
        },
    }


def should_use_interpretive_adjudication(
    *,
    player_input: str,
    semantic_advisory: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
    service_matched: bool = False,
) -> bool:
    if service_matched:
        return False
    return bool(
        classify_interpretive_intent(
            player_input=player_input,
            semantic_advisory=_d(semantic_advisory),
            selection=_d(selection),
        )
    )


def build_interpretive_adjudication_result(
    *,
    session: dict[str, Any],
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    player_input: str,
    action_advisory: dict[str, Any] | None = None,
    semantic_advisory: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a non-stateful, diegetic answer for meaningful unsupported input."""

    action_advisory = _d(action_advisory)
    semantic_advisory = _d(semantic_advisory)
    selection = _d(selection)
    diagnostics = _first_call_diagnostics(action_advisory, semantic_advisory)
    packet = _d(diagnostics.get("turn_grounding_packet"))
    profile = _addressed_profile(packet)
    speaker = _speaker_name(profile, semantic_advisory)
    intent = classify_interpretive_intent(
        player_input=player_input,
        semantic_advisory=semantic_advisory,
        selection=selection,
    ) or "unsupported_but_diegetic_action"
    family = interpretive_intent_family(intent)
    fact_constraints = build_interpretive_fact_constraints(
        intent=intent,
        simulation_state=_d(simulation_state),
        runtime_state=_d(runtime_state),
        grounding_packet=packet,
    )
    if family == "observation":
        narration = _observation_narration(
            session=_d(session),
            simulation_state=_d(simulation_state),
            grounding_packet=packet,
        )
        visible_npc: dict[str, Any] = {}
    else:
        narration = _narration_for_intent(
            intent=intent,
            speaker=speaker,
            player_input=player_input,
        )
        visible_npc = {
            "speaker": speaker,
            "line": _line_for_intent(
                intent=intent,
                speaker=speaker,
                profile=profile,
                player_input=player_input,
            ),
        }
    visible_response = {"narration": narration, "npc": visible_npc}
    grounding_validation = {
        "ok": True,
        "selected_candidate": "interpretive_adjudication",
        "interpretive_intent": intent,
        "interpretive_intent_family": family,
        "interpretive_fact_constraints": deepcopy(fact_constraints),
        "fallback_used": True,
        "fallback_source": _INTERPRETIVE_SOURCE,
        "violations": [],
        "primary_violations": [],
        "state_mutation_allowed": False,
        "state_mutation_applied": False,
        "first_call_grounding_packet_version": _s(packet.get("format_version")),
        "first_call_addressed_npc_ids": _l(_d(packet.get("priority_context")).get("addressed_npc_ids")),
        "first_call_grounding_diagnostics": deepcopy(diagnostics),
        "turn_grounding_packet": deepcopy(packet),
        "source": _INTERPRETIVE_SOURCE,
    }
    resolved_result = {
        "ok": True,
        "action_type": "interpretive_adjudication",
        "semantic_action_type": "interpretive_adjudication",
        "semantic_family": family,
        "interpretive_intent": intent,
        "interpretive_intent_family": family,
        "interpretive_fact_constraints": deepcopy(fact_constraints),
        "stateful": False,
        "needs_runtime_resolution": False,
        "no_state_mutation": True,
        "visible_interaction_reason": "world_grounded_interpretive_adjudication",
        "outcome": "interpretive_adjudication",
        "summary": narration,
        "npc": deepcopy(visible_npc),
        "visible_response": deepcopy(visible_response),
        "first_call_visible_response_selection": deepcopy(selection),
        "first_call_grounding_diagnostics": deepcopy(diagnostics),
        "grounding_validation": deepcopy(grounding_validation),
        "source": _INTERPRETIVE_SOURCE,
    }
    result = {
        "consumed": True,
        "ok": True,
        "result": deepcopy(resolved_result),
        "resolved_result": deepcopy(resolved_result),
        "narration": narration,
        "final_narration": narration,
        "summary": narration,
        "npc": deepcopy(visible_npc),
        "visible_response": deepcopy(visible_response),
        "first_call_visible_response_selection": deepcopy(selection),
        "first_call_action_advisory": deepcopy(action_advisory),
        "first_call_semantic_advisory": deepcopy(semantic_advisory),
        "first_call_grounding_diagnostics": deepcopy(diagnostics),
        "grounding_validation": deepcopy(grounding_validation),
        "interpretive_fact_constraints": deepcopy(fact_constraints),
        "llm_called": True,
        "llm_purpose": "world_grounded_interpretive_adjudication",
        "stateful": False,
        "needs_runtime_resolution": False,
        "no_state_mutation": True,
        "simulation_state": deepcopy(_d(simulation_state)),
        "runtime_state": deepcopy(_d(runtime_state)),
        "session": deepcopy(_d(session)),
        "player_input": _s(player_input),
        "source": _INTERPRETIVE_SOURCE,
    }
    from app.apps.rpg.session.contract_attachment import add_contracts_to_interpretive_result

    return add_contracts_to_interpretive_result(result)


def _looks_like_lore_conflict_claim(text: str) -> bool:
    claim_prefix = re.search(r"\bi (am|was|were|used to|come from|came from|killed|hunt|hunted)\b", text)
    lore_terms = (
        "dragon",
        "dragons",
        "spaceship",
        "alien",
        "aliens",
        "robot",
        "robots",
        "laser",
        "lasers",
        "time traveler",
        "from the future",
    )
    return bool(claim_prefix and any(term in text for term in lore_terms))


def _looks_like_unsupported_mechanic_request(text: str) -> bool:
    impossible_terms = (
        "build a spaceship",
        "craft a spaceship",
        "summon a dragon",
        "fly to the moon",
        "teleport",
        "turn invisible",
        "jump ten feet",
        "jump 10 feet",
    )
    return any(term in text for term in impossible_terms)


def _first_call_diagnostics(action_advisory: dict[str, Any], semantic_advisory: dict[str, Any]) -> dict[str, Any]:
    return _d(
        _d(semantic_advisory).get("first_call_grounding_diagnostics")
        or _d(action_advisory).get("first_call_grounding_diagnostics")
    )


def _addressed_profile(packet: dict[str, Any]) -> dict[str, Any]:
    addressed = _l(_d(packet.get("npc_context")).get("addressed_npcs"))
    return _d(addressed[0]) if addressed else {}


def _speaker_name(profile: dict[str, Any], semantic_advisory: dict[str, Any]) -> str:
    return _s(profile.get("name") or semantic_advisory.get("target_name") or "NPC").strip() or "NPC"


def _line_for_intent(*, intent: str, speaker: str, profile: dict[str, Any], player_input: str) -> str:
    role = _s(profile.get("role") or profile.get("occupation") or profile.get("title")).strip()
    role_phrase = f" as {role}" if role else ""
    if intent == "npc_capability_request":
        return (
            f"{_first_person(speaker)} considers the request{role_phrase}, then answers from what is plausible here: "
            "that is not something I can honestly promise or perform just because you asked. "
            "If you mean something practical, say what outcome you want."
        )
    if intent == "unverified_debt_claim":
        return (
            "I do not accept debts just because someone names a number. "
            "Show proof, a witness, or a ledger entry, and I will answer that. Until then, that claim stays unproven."
        )
    if intent == "memory_claim":
        return (
            "Memory is not a blank check. I will not pretend to remember a promise, debt, or meeting without proof. "
            "Give me a name, place, or witness, and I will answer what I can verify."
        )
    if intent == "lore_conflict_claim":
        return (
            "That claim does not fit what I know of this world. I will treat it as a story until you bring proof, "
            "and even then I will weigh it against what can actually be seen or known here."
        )
    if intent == "unverified_player_claim":
        return (
            "That may be a story, a boast, or the truth, but I cannot treat it as fact without proof. "
            "I will judge what you do here and now before I rewrite the past around your words."
        )
    if intent == "social_probe":
        return (
            "Trust is not granted by a question. "
            "I can answer you honestly, but I will weigh your words against what I have seen you do."
        )
    if intent == "observation_request":
        return (
            "Look with your own eyes first: the place, the people, and the mood all matter. "
            "Name what you want to inspect, and I will answer from what can be seen or known here."
        )
    if intent == "unsupported_mechanic_request":
        return (
            "That is not something this moment can honestly resolve as an accomplished action. "
            "I can react to the attempt or explain the obstacle, but I will not pretend the impossible simply worked."
        )
    if intent == "hypothetical_counterfactual":
        return (
            "I can answer that as a possibility, not as a fact that has happened. "
            "I will keep it separate from what is known here and now."
        )
    return (
        "I can respond to that in-world, but I will not turn an unsupported request into a fact. "
        "Say what you want to accomplish, and I will answer from what is possible here."
    )


def _narration_for_intent(*, intent: str, speaker: str, player_input: str) -> str:
    if intent == "npc_capability_request":
        return f"{speaker} measures the request against the limits of the moment."
    if intent == "unverified_debt_claim":
        return f"{speaker} treats the claimed debt as something that needs proof."
    if intent == "memory_claim":
        return f"{speaker} checks the claim against memory rather than accepting it."
    if intent == "lore_conflict_claim":
        return f"{speaker} tests your claim against what belongs in this world."
    if intent == "unverified_player_claim":
        return f"{speaker} weighs your claim without accepting it as proven fact."
    if intent == "social_probe":
        return f"{speaker} answers cautiously, judging trust by evidence rather than words."
    if intent == "unsupported_mechanic_request":
        return f"{speaker} treats the request as a constraint to answer in-world, not a completed mechanic."
    if intent == "hypothetical_counterfactual":
        return f"{speaker} treats the question as a possibility, not a change to the world."
    return f"{speaker} keeps the answer grounded in what can be known here."


def _observation_narration(
    *,
    session: dict[str, Any],
    simulation_state: dict[str, Any],
    grounding_packet: dict[str, Any],
) -> str:
    """Describe the current published scene without inventing an NPC speaker."""

    state = _d(session.get("state"))
    location_name = _s(
        state.get("current_location_name")
        or state.get("current_location")
        or state.get("location")
        or _d(simulation_state.get("location_state")).get("location_name")
        or _d(_d(grounding_packet).get("authoritative_state")).get("location")
    ).strip()
    opening_story = _d(_d(state.get("narrative_affordances")).get("opening_story"))
    description = _s(
        opening_story.get("summary")
        or state.get("summary")
        or _d(simulation_state.get("scene")).get("description")
    ).strip()
    if description:
        return description
    if location_name:
        return (
            f"You take in {location_name}, noting its immediate paths, occupants, "
            "and anything that stands out."
        )
    return "You take in the immediate scene, noting its paths, occupants, and visible details."


def _first_person(speaker: str) -> str:
    return "I"


def _looks_like_meaningful_player_input(text: str) -> bool:
    if not text or text in _CLIENT_CORRUPTION_MARKERS:
        return False
    if len(text) < 3:
        return False
    words = text.split()
    if len(words) <= 1 and len(text) < 8:
        return False
    alpha = sum(1 for char in text if char.isalpha())
    return alpha >= max(3, len(text) // 3)


def _norm_words(value: Any) -> str:
    return re.sub(r"[^a-z0-9']+", " ", _s(value).casefold()).strip()


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _s(value: Any) -> str:
    return str(value) if value is not None else ""
