from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping

from .contracts import (
    AgencyEffect,
    GateDecision,
    ResponseCandidate,
    ResponseRequest,
    SectionType,
)


_FACTUAL_SECTION_TYPES = {
    SectionType.ACTION,
    SectionType.RESULT,
    SectionType.STATE_CHANGE,
}
_TOKEN = re.compile(r"[a-z0-9']+")
_HARD_FAMILY_TERMS = {
    "currency": {"gold", "silver", "copper", "coin", "coins", "paid", "payment"},
    "inventory": {"inventory", "item", "items", "sword", "torch", "rope", "key"},
    "combat": {
        "damage",
        "wound",
        "wounded",
        "blood",
        "dead",
        "dies",
        "defeated",
        "killed",
        "attack",
    },
    "location": {
        "arrive",
        "arrives",
        "travel",
        "travels",
        "enter",
        "enters",
        "leave",
        "leaves",
        "reach",
        "reaches",
    },
    "quest": {"quest", "objective", "mission", "completed", "complete"},
    "relationship": {"trust", "loyalty", "reputation", "relationship", "faction"},
}
_CURRENCY_DENOMINATIONS = {"gold", "silver", "copper"}
_INVENTORY_NOUNS = {"sword", "torch", "rope", "key"}
_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


class EligibilityPolicy:
    """Evaluate non-negotiable response gates before prose ranking."""

    def evaluate(
        self,
        candidate: ResponseCandidate,
        request: ResponseRequest,
    ) -> ResponseCandidate:
        result = _mapping(request.authoritative_turn_result)
        strict_claim_refs = bool(
            request.feature_flags.get("strict_claim_refs")
            or result.get("strict_claim_refs")
        )
        decisions = (
            self._state_claim_gate(candidate, result, strict_claim_refs=strict_claim_refs),
            self._visibility_gate(candidate, result),
            self._speaker_gate(candidate, result),
            self._proposal_gate(candidate, result),
            self._agency_gate(candidate, result),
            self._semantic_reference_gate(
                candidate,
                strict_claim_refs=strict_claim_refs,
            ),
            self._mutation_gate(candidate),
            self._typed_claim_value_gate(
                candidate,
                result,
                strict_claim_refs=strict_claim_refs,
            ),
        )
        return replace(candidate, gate_decisions=decisions)

    @staticmethod
    def _state_claim_gate(
        candidate: ResponseCandidate,
        result: Mapping[str, Any],
        *,
        strict_claim_refs: bool,
    ) -> GateDecision:
        allowed = _string_set(
            result.get("allowed_claim_refs")
            or result.get("claim_refs")
            or result.get("allowed_claims")
        )
        prohibited = _string_set(
            result.get("prohibited_claim_refs")
            or result.get("forbidden_claim_refs")
        )
        claims = {
            claim
            for section in candidate.plan.sections
            for claim in section.claim_refs
            if claim
        }
        reasons: list[str] = []
        if prohibited & claims:
            reasons.extend(f"prohibited_claim:{claim}" for claim in sorted(prohibited & claims))
        if allowed:
            unsupported = claims - allowed
            reasons.extend(f"unsupported_claim:{claim}" for claim in sorted(unsupported))
        elif strict_claim_refs and claims:
            reasons.extend(f"unverified_claim:{claim}" for claim in sorted(claims))
        return GateDecision("state_claims", not reasons, tuple(reasons))

    @staticmethod
    def _visibility_gate(
        candidate: ResponseCandidate,
        result: Mapping[str, Any],
    ) -> GateDecision:
        hidden_refs = _string_set(
            result.get("hidden_fact_refs")
            or result.get("hidden_claim_refs")
            or result.get("hidden_facts")
        )
        referenced = {
            ref
            for section in candidate.plan.sections
            for ref in (*section.claim_refs, *section.soft_truth_refs)
            if ref
        }
        reasons = [
            f"hidden_reference:{ref}"
            for ref in sorted(hidden_refs & referenced)
        ]
        for section in candidate.plan.sections:
            if bool(section.metadata.get("hidden")):
                reasons.append(f"hidden_section:{section.section_id}")
            if str(section.metadata.get("visibility") or "").casefold() == "hidden":
                reasons.append(f"hidden_section:{section.section_id}")
        return GateDecision("visibility", not reasons, tuple(dict.fromkeys(reasons)))

    @staticmethod
    def _speaker_gate(
        candidate: ResponseCandidate,
        result: Mapping[str, Any],
    ) -> GateDecision:
        allowed_speakers = _string_set(
            result.get("allowed_speakers")
            or result.get("present_npcs")
            or result.get("allowed_npcs")
        )
        knowledge_by_speaker = _mapping(result.get("speaker_knowledge_refs"))
        reasons: list[str] = []
        for section in candidate.plan.sections:
            if section.section_type is not SectionType.NPC_DIALOGUE:
                continue
            if not section.speaker_id:
                reasons.append(f"missing_speaker:{section.section_id}")
                continue
            if allowed_speakers and section.speaker_id not in allowed_speakers:
                reasons.append(f"invalid_speaker:{section.speaker_id}")
            known_refs = _string_set(knowledge_by_speaker.get(section.speaker_id))
            if known_refs:
                unsupported = set(section.claim_refs) - known_refs
                reasons.extend(
                    f"speaker_out_of_scope:{section.speaker_id}:{ref}"
                    for ref in sorted(unsupported)
                )
        return GateDecision("speaker_scope", not reasons, tuple(reasons))

    @staticmethod
    def _proposal_gate(
        candidate: ResponseCandidate,
        result: Mapping[str, Any],
    ) -> GateDecision:
        approved = _string_set(
            result.get("approved_proposal_refs")
            or result.get("approved_proposals")
        )
        proposal_refs = set(candidate.plan.proposal_refs)
        for section in candidate.plan.sections:
            proposal_refs.update(section.proposal_refs)
        reasons: list[str] = []
        if proposal_refs:
            unsupported = proposal_refs - approved
            reasons.extend(
                f"unapproved_proposal:{proposal}"
                for proposal in sorted(unsupported)
            )
        return GateDecision("proposal_permissions", not reasons, tuple(reasons))

    @staticmethod
    def _agency_gate(
        candidate: ResponseCandidate,
        result: Mapping[str, Any],
    ) -> GateDecision:
        metadata = candidate.plan.metadata
        reasons: list[str] = []
        takes_choice = bool(
            metadata.get("takes_player_choice")
            or metadata.get("auto_accepts_lead")
            or metadata.get("auto_starts_investigation")
        )
        clear_intent = bool(
            result.get("clear_player_intent")
            or result.get("mechanic_resolved")
            or candidate.plan.agency_effect
            in {AgencyEffect.RESOLVED_MECHANIC, AgencyEffect.PLAYER_CONFIRMED}
        )
        if takes_choice and not clear_intent:
            reasons.append("player_choice_taken_without_authority")
        irreversible = bool(metadata.get("irreversible_consequence"))
        if irreversible and not clear_intent:
            reasons.append("irreversible_consequence_without_authority")
        return GateDecision("player_agency", not reasons, tuple(reasons))

    @staticmethod
    def _semantic_reference_gate(
        candidate: ResponseCandidate,
        *,
        strict_claim_refs: bool,
    ) -> GateDecision:
        if not strict_claim_refs:
            return GateDecision("semantic_references", True, ())
        reasons: list[str] = []
        for section in candidate.plan.sections:
            factual = (
                section.section_type in _FACTUAL_SECTION_TYPES
                or bool(section.metadata.get("factual"))
            )
            if factual and not section.claim_refs and not section.soft_truth_refs:
                reasons.append(f"missing_claim_reference:{section.section_id}")
        return GateDecision("semantic_references", not reasons, tuple(reasons))

    @staticmethod
    def _mutation_gate(candidate: ResponseCandidate) -> GateDecision:
        metadata = candidate.plan.metadata
        reasons: list[str] = []
        if metadata.get("authoritative_mutation"):
            reasons.append("presentation_attempted_authoritative_mutation")
        if metadata.get("executes_tools"):
            reasons.append("presentation_attempted_tool_execution")
        return GateDecision("no_direct_mutation", not reasons, tuple(reasons))

    @staticmethod
    def _typed_claim_value_gate(
        candidate: ResponseCandidate,
        result: Mapping[str, Any],
        *,
        strict_claim_refs: bool,
    ) -> GateDecision:
        if not strict_claim_refs:
            return GateDecision("typed_claim_values", True, ())
        records = {
            str(row.get("claim_ref") or ""): row
            for row in result.get("claim_records", ())
            if isinstance(row, Mapping) and str(row.get("claim_ref") or "")
        }
        approved_soft_truth = _string_set(
            result.get("approved_soft_truth_refs")
            or result.get("approved_proposal_refs")
        )
        reasons: list[str] = []
        for section in candidate.plan.sections:
            for ref in section.soft_truth_refs:
                if ref.startswith("hermes.") and ref not in approved_soft_truth:
                    reasons.append(f"unapproved_hermes_soft_truth:{section.section_id}:{ref}")
            if not records:
                continue
            tokens = set(_tokens(section.text))
            families = {
                family
                for family, markers in _HARD_FAMILY_TERMS.items()
                if tokens & markers
            }
            for family in sorted(families):
                refs = tuple(
                    ref
                    for ref in section.claim_refs
                    if ref.startswith(f"{family}.")
                )
                if not refs:
                    reasons.append(
                        f"hard_state_text_without_typed_claim:{section.section_id}:{family}"
                    )
                    continue
                typed_rows = tuple(records[ref] for ref in refs if ref in records)
                if not typed_rows:
                    reasons.append(
                        f"typed_claim_record_missing:{section.section_id}:{family}"
                    )
                    continue
                if family == "currency" and not _currency_claim_matches(tokens, refs, typed_rows):
                    reasons.append(f"typed_claim_value_mismatch:{section.section_id}:currency")
                elif family == "inventory" and not _inventory_claim_matches(tokens, refs, typed_rows):
                    reasons.append(f"typed_claim_value_mismatch:{section.section_id}:inventory")
                elif family == "quest" and not _quest_claim_matches(tokens, typed_rows):
                    reasons.append(f"typed_claim_value_mismatch:{section.section_id}:quest")
                elif family == "combat" and not _combat_claim_matches(tokens, typed_rows):
                    reasons.append(f"typed_claim_value_mismatch:{section.section_id}:combat")
        return GateDecision(
            "typed_claim_values",
            not reasons,
            tuple(dict.fromkeys(reasons)),
        )


def eligibility_reasons(candidate: ResponseCandidate) -> tuple[str, ...]:
    return tuple(
        reason
        for decision in candidate.gate_decisions
        if not decision.passed
        for reason in decision.reasons
    )


def _currency_claim_matches(
    tokens: set[str],
    refs: tuple[str, ...],
    rows: tuple[Mapping[str, Any], ...],
) -> bool:
    denominations = tokens & _CURRENCY_DENOMINATIONS
    if denominations and not any(
        denomination in set(_tokens(ref))
        for denomination in denominations
        for ref in refs
    ):
        return False
    if not denominations:
        return True
    mentioned_numbers = _numbers(tokens)
    if not mentioned_numbers:
        return True
    allowed_numbers = {
        abs(int(value))
        for row in rows
        for value in (row.get("value"),)
        if isinstance(value, (int, float)) and float(value).is_integer()
    }
    return bool(mentioned_numbers & allowed_numbers)


def _inventory_claim_matches(
    tokens: set[str],
    refs: tuple[str, ...],
    rows: tuple[Mapping[str, Any], ...],
) -> bool:
    concrete = tokens & _INVENTORY_NOUNS
    if not concrete:
        return True
    expected = {
        token
        for ref in refs
        for token in _tokens(ref)
        if token not in {"inventory", "item", "changed"}
    }
    for row in rows:
        expected.update(_tokens(row.get("value")))
    return bool(concrete & expected)


def _quest_claim_matches(
    tokens: set[str],
    rows: tuple[Mapping[str, Any], ...],
) -> bool:
    if not tokens & {"complete", "completed"}:
        return True
    values = " ".join(str(row.get("value") or "").casefold() for row in rows)
    return any(marker in values for marker in ("complete", "completed", "true", "success"))


def _combat_claim_matches(
    tokens: set[str],
    rows: tuple[Mapping[str, Any], ...],
) -> bool:
    terminal = tokens & {"dead", "dies", "defeated", "killed"}
    if not terminal:
        return True
    values = " ".join(str(row.get("value") or "").casefold() for row in rows)
    return any(marker in values for marker in terminal | {"defeat", "death", "kill", "true"})


def _numbers(tokens: set[str]) -> set[int]:
    values: set[int] = set()
    for token in tokens:
        if token.isdigit():
            values.add(int(token))
        elif token in _NUMBER_WORDS:
            values.add(_NUMBER_WORDS[token])
    return values


def _tokens(value: Any) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(str(value or "").casefold()))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, Mapping):
        return {str(key) for key, enabled in value.items() if enabled}
    if isinstance(value, str):
        return {value} if value else set()
    try:
        return {str(item) for item in value if str(item)}
    except TypeError:
        return set()
