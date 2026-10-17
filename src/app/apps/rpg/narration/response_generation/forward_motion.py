from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .recovery import LocalRecoveryAnalysis


_SOCIAL_ENTITY_REQUEST_WORDS = {
    "ask",
    "join",
    "convince",
    "persuade",
    "order",
    "invite",
    "tell",
}


@dataclass(frozen=True)
class RecoveryHistoryEntry:
    turn_id: str
    strategy: str
    target: str = ""
    produced_progress: bool = False


@dataclass(frozen=True)
class ForwardMotionPlan:
    strategy: str
    outcome: str
    rationale: str
    options: tuple[str, ...] = ()
    answer_evidence_ids: tuple[str, ...] = ()
    offer_only: bool = True
    starts_path: bool = False
    irreversible: bool = False
    requires_player_confirmation: bool = True
    state_mutation_allowed: bool = False


class ForwardMotionPolicy:
    """Choose useful progress without choosing a path on the player's behalf."""

    def select(
        self,
        analysis: LocalRecoveryAnalysis,
        *,
        history: Iterable[RecoveryHistoryEntry] = (),
        target: str = "",
        clear_player_intent: bool = False,
        mechanic_resolved: bool = False,
    ) -> ForwardMotionPlan:
        selected = analysis.intent.selected
        repeated = _repeat_count(history, selected.affordance, target)

        if selected.ambiguity == "high":
            return ForwardMotionPlan(
                strategy="ask_in_world_clarification",
                outcome="clarification",
                rationale="interpretation confidence is too low to choose an action",
                options=("clarify the intended outcome", "inspect the immediate situation"),
            )
        if (
            selected.affordance == "unverified_player_claim"
            and analysis.intent.unresolved_references
        ):
            return ForwardMotionPlan(
                strategy="clarify_unverified_relationship",
                outcome="clarification",
                rationale="the asserted person or relationship is not established",
                options=("identify who you mean", "ask the present NPC what they know"),
            )
        if (
            selected.affordance == "entity_search"
            and analysis.intent.unresolved_references
            and set(analysis.intent.tokens) & _SOCIAL_ENTITY_REQUEST_WORDS
        ):
            return ForwardMotionPlan(
                strategy="clarify_unknown_entity",
                outcome="clarification",
                rationale="the requested person or relationship is not established in current knowledge",
                options=("identify who you mean", "describe where you heard the name"),
            )
        if mechanic_resolved and selected.affordance in {
            "transaction",
            "combat_attempt",
            "social_check",
            "travel_attempt",
        }:
            return ForwardMotionPlan(
                strategy=f"describe_resolved_{selected.affordance}",
                outcome="resolved_action" if selected.affordance != "combat_attempt" else "mechanic_attempt",
                rationale="the deterministic mechanic resolved the player's clear attempt",
                offer_only=False,
                starts_path=True,
                requires_player_confirmation=False,
                state_mutation_allowed=True,
            )
        if analysis.retrieval.knowledge_status == "known" and analysis.retrieval.evidence:
            return ForwardMotionPlan(
                strategy="answer_with_visible_evidence",
                outcome="answer",
                rationale="local visible evidence is sufficient",
                answer_evidence_ids=tuple(row.evidence_id for row in analysis.retrieval.evidence),
                requires_player_confirmation=False,
            )
        if analysis.retrieval.knowledge_status == "conflicting":
            return ForwardMotionPlan(
                strategy="present_bounded_uncertainty",
                outcome="uncertainty",
                rationale="available sources conflict",
                options=("ask a knowledgeable source", "investigate the contradiction"),
                answer_evidence_ids=tuple(row.evidence_id for row in analysis.retrieval.evidence),
            )
        if repeated >= 2:
            return ForwardMotionPlan(
                strategy="break_recovery_loop",
                outcome="choice",
                rationale="the prior recovery suggestion did not produce progress",
                options=("inspect a different clue", "ask another present character", "leave this thread for now"),
            )

        affordance = selected.affordance
        if affordance in {"world_equivalent", "substitute_action"}:
            return ForwardMotionPlan(
                strategy="offer_world_equivalent",
                outcome="alternative",
                rationale="the requested method is unavailable but its goal is understandable",
                options=(selected.underlying_goal,),
            )
        if affordance in {"analogous_skill", "ritual_research"}:
            return ForwardMotionPlan(
                strategy="offer_supported_analogy",
                outcome="alternative",
                rationale="the requested power is unsupported",
                options=("attempt insight or observation", "research a ritual or specialist"),
            )
        if affordance in {"ask_directions", "entity_search", "lore_search", "offer_investigation"}:
            return ForwardMotionPlan(
                strategy="offer_investigation_lead",
                outcome="lead",
                rationale="the requested fact is not locally confirmed",
                options=("ask a likely knowledgeable NPC", "search the journal or local records"),
            )
        if affordance in {"unverified_player_claim", "memory_check"}:
            return ForwardMotionPlan(
                strategy="treat_as_unverified_claim",
                outcome="reaction",
                rationale="the asserted history is not authoritative",
                options=("present evidence", "ask the NPC whether they remember"),
            )
        if affordance in {"travel_attempt", "travel_failure"}:
            if mechanic_resolved and clear_player_intent:
                return ForwardMotionPlan(
                    strategy="describe_resolved_travel",
                    outcome="resolved_action",
                    rationale="the deterministic travel mechanic resolved the request",
                    offer_only=False,
                    starts_path=True,
                    requires_player_confirmation=False,
                    state_mutation_allowed=True,
                )
            return ForwardMotionPlan(
                strategy="offer_route_or_directions",
                outcome="lead",
                rationale="travel has not been resolved",
                options=("ask for directions", "choose a known route"),
            )
        if affordance in {"transaction_failure", "combat_attempt"}:
            return ForwardMotionPlan(
                strategy="meaningful_failure_with_alternative",
                outcome="failure",
                rationale="the requested consequence has not been resolved by the relevant mechanic",
                options=("attempt the supported mechanic", "choose a safer alternative"),
            )
        return ForwardMotionPlan(
            strategy="meaningful_failure_with_alternative",
            outcome="failure",
            rationale="the request cannot be safely resolved as stated",
            options=("inspect the scene", "try a related supported action"),
        )


def validate_agency(plan: ForwardMotionPlan) -> tuple[str, ...]:
    issues: list[str] = []
    if plan.starts_path and plan.offer_only:
        issues.append("offered_path_cannot_be_marked_started")
    if plan.irreversible and plan.requires_player_confirmation:
        issues.append("irreversible_plan_requires_unresolved_confirmation")
    if plan.state_mutation_allowed and not plan.starts_path:
        issues.append("state_mutation_requires_resolved_path")
    if plan.offer_only and not plan.requires_player_confirmation and plan.outcome not in {"answer", "uncertainty"}:
        issues.append("offer_requires_player_confirmation")
    return tuple(issues)


def _repeat_count(
    history: Iterable[RecoveryHistoryEntry],
    strategy: str,
    target: str,
) -> int:
    return sum(
        1
        for entry in history
        if not entry.produced_progress
        and entry.strategy == strategy
        and (not target or not entry.target or entry.target == target)
    )
