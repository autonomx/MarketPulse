from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import AgencyEffect, CandidateSource, ResponseCandidate, ResponseMode, Reversibility, SectionType, SemanticResponsePlan, SemanticSection
from .forward_motion import ForwardMotionPlan

@dataclass(frozen=True)
class FallbackInput:
    turn_id: str
    player_input: str
    mode: ResponseMode
    forward_plan: ForwardMotionPlan
    speaker_id: str = ""
    visible_facts: Mapping[str, Any] | None = None
    claim_refs: tuple[str, ...] = ()
    soft_truth_refs: tuple[str, ...] = ()

class DeterministicFallbackLibrary:
    """Specific in-world fallbacks that remain useful without a provider."""

    def candidate(self, fallback_input: FallbackInput) -> ResponseCandidate:
        text, section_type = self._render_text(fallback_input)
        agency = AgencyEffect.RESOLVED_MECHANIC if fallback_input.forward_plan.state_mutation_allowed else AgencyEffect.OFFER_ONLY
        plan = SemanticResponsePlan(
            mode=fallback_input.mode,
            sections=(SemanticSection(
                section_id=f"fallback.{fallback_input.turn_id}",
                section_type=section_type,
                text=text,
                speaker_id=fallback_input.speaker_id if section_type is SectionType.NPC_DIALOGUE else "",
                claim_refs=fallback_input.claim_refs,
                soft_truth_refs=fallback_input.soft_truth_refs,
                metadata={"fallback": True},
            ),),
            forward_strategy=fallback_input.forward_plan.strategy,
            agency_effect=agency,
            reversibility=Reversibility.PERSISTENT if fallback_input.forward_plan.state_mutation_allowed else Reversibility.FULLY_REVERSIBLE,
            metadata={"takes_player_choice": False, "fallback_reason": fallback_input.forward_plan.rationale},
        )
        return ResponseCandidate(
            candidate_id=f"{fallback_input.turn_id}:deterministic-fallback",
            plan=plan,
            source=CandidateSource.DETERMINISTIC,
            current_turn_relevance=0.8,
            forward_motion=0.8,
            specificity=0.55,
            naturalness=0.55,
            provider_metadata={"grounded_safe_fallback": True},
        )

    def _render_text(self, value: FallbackInput) -> tuple[str, SectionType]:
        typed_fact = _first_visible_fact(value.visible_facts)
        if typed_fact and value.claim_refs:
            return typed_fact, SectionType.NARRATION
        strategy = value.forward_plan.strategy
        options = value.forward_plan.options
        choice = _pick(value.turn_id, options) if options else "try another approach"
        if strategy == "ask_in_world_clarification":
            return f"Your intent is not clear from the situation. Do you want to {choice}?", SectionType.CLARIFICATION
        if strategy == "answer_with_visible_evidence":
            return typed_fact or "What is known here does not confirm more than that.", SectionType.NARRATION
        if strategy == "present_bounded_uncertainty":
            return f"The available accounts disagree. You could {choice}, but nothing is confirmed yet.", SectionType.NARRATION
        if strategy == "offer_world_equivalent":
            return f"That method does not exist here. You could instead {choice}.", SectionType.CHOICE
        if strategy == "offer_supported_analogy":
            return f"The power does not answer you directly. You could {choice}.", SectionType.CHOICE
        if strategy == "offer_investigation_lead":
            return f"Nothing nearby confirms it. A practical next step is to {choice}.", SectionType.CHOICE
        if strategy == "treat_as_unverified_claim":
            return f"No one here treats that claim as established. You could {choice}.", SectionType.CHOICE
        if strategy == "offer_route_or_directions":
            return f"There is no confirmed route from here. You could {choice}.", SectionType.CHOICE
        if strategy == "break_recovery_loop":
            return f"That approach has gone in circles. Change course: {choice}.", SectionType.CHOICE
        if strategy == "describe_resolved_travel":
            return typed_fact or "You follow the resolved route and reach the destination.", SectionType.RESULT
        return f"The attempt does not succeed as stated. You can still {choice}.", SectionType.RESULT

def _pick(seed: str, options: tuple[str, ...]) -> str:
    if not options:
        return "try another approach"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return options[int.from_bytes(digest[:4], "big") % len(options)]

def _first_visible_fact(value: Mapping[str, Any] | None) -> str:
    if not isinstance(value, Mapping):
        return ""
    for key in sorted(value, key=str):
        item = value[key]
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""
