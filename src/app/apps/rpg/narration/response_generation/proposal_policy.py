from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from .truth_lifetime import SoftTruthRecord, TruthClass, TruthLifetime


class ProposalRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProposalDecision(str, Enum):
    ACCEPT_TURN = "accept_turn"
    ACCEPT_SCENE = "accept_scene"
    PROMOTE_PERSISTENT = "promote_persistent"
    REJECT_DUPLICATE = "reject_duplicate"
    REJECT_BUDGET = "reject_budget"
    REJECT_INCONSISTENT = "reject_inconsistent"
    REJECT_RESOLVER_REQUIRED = "reject_resolver_required"
    REJECT_HIDDEN = "reject_hidden"


@dataclass(frozen=True)
class ProposalBudget:
    max_turn: int = 4
    max_scene: int = 12
    max_persistent: int = 64


@dataclass(frozen=True)
class WorldProposal:
    proposal_id: str
    proposal_type: str
    summary: str
    content: Any = None
    risk: ProposalRisk = ProposalRisk.LOW
    requested_lifetime: TruthLifetime = TruthLifetime.TURN
    source: str = "response_generation"
    seed: str = ""
    provenance_refs: tuple[str, ...] = ()
    scene_id: str = ""
    created_turn: int = 0
    created_turn_id: str = ""
    dedupe_key: str = ""
    visibility: str = "player_visible"
    confidence: float = 0.5
    player_interactions: int = 0
    relevance_count: int = 1
    director_approved: bool = False
    resolver_name: str = ""
    resolver_approved: bool = False
    world_consistent: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def normalized_dedupe_key(self) -> str:
        return self.dedupe_key or _stable_key(
            self.proposal_type,
            self.summary,
            self.scene_id,
        )


@dataclass(frozen=True)
class ProposalPromotionEvent:
    event_id: str
    proposal_id: str
    truth_ref: str
    from_lifetime: TruthLifetime
    to_lifetime: TruthLifetime
    turn_id: str
    reason: str
    source: str
    seed: str
    resolver_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "proposal_id": self.proposal_id,
            "truth_ref": self.truth_ref,
            "from_lifetime": self.from_lifetime.value,
            "to_lifetime": self.to_lifetime.value,
            "turn_id": self.turn_id,
            "reason": self.reason,
            "source": self.source,
            "seed": self.seed,
            "resolver_name": self.resolver_name,
        }


@dataclass(frozen=True)
class ProposalPolicyResult:
    decision: ProposalDecision
    proposal: WorldProposal
    truth: SoftTruthRecord | None = None
    event: ProposalPromotionEvent | None = None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.truth is not None

    @property
    def persistent(self) -> bool:
        return self.truth is not None and self.truth.persistent


class ProposalPolicy:
    """Deterministic policy for bounded soft-truth lifetimes and promotion."""

    def __init__(self, budget: ProposalBudget | None = None) -> None:
        self.budget = budget or ProposalBudget()

    def evaluate(
        self,
        proposal: WorldProposal,
        *,
        existing: Iterable[SoftTruthRecord] = (),
        turn_id: str,
    ) -> ProposalPolicyResult:
        current = tuple(existing)
        rejection = self._preflight(proposal, current)
        if rejection is not None:
            return rejection

        target = self._target_lifetime(proposal)
        if self._budget_exceeded(current, target, proposal.scene_id):
            return ProposalPolicyResult(
                ProposalDecision.REJECT_BUDGET,
                proposal,
                reason=f"{target.value} proposal budget exceeded",
            )

        truth_ref = f"proposal.{proposal.proposal_id}"
        base_lifetime = (
            TruthLifetime.TURN
            if target is TruthLifetime.PERSISTENT
            else target
        )
        base_expires = (
            proposal.created_turn
            if base_lifetime is TruthLifetime.TURN
            else proposal.created_turn + 64
        )
        truth = SoftTruthRecord(
            truth_ref=truth_ref,
            truth_class=TruthClass.GENERATED_PROPOSAL,
            content=(
                proposal.content
                if proposal.content is not None
                else proposal.summary
            ),
            provenance_refs=proposal.provenance_refs,
            visibility=proposal.visibility,
            confidence=proposal.confidence,
            lifetime=base_lifetime,
            created_turn=proposal.created_turn,
            created_turn_id=proposal.created_turn_id or turn_id,
            scene_id=proposal.scene_id,
            expires_turn=base_expires,
            source=proposal.source,
            metadata={
                **dict(proposal.metadata),
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type,
                "risk": proposal.risk.value,
                "seed": proposal.seed,
                "dedupe_key": proposal.normalized_dedupe_key(),
                "acceptance_reason": self._acceptance_reason(proposal, target),
                "resolver_name": proposal.resolver_name,
            },
        )
        if target is TruthLifetime.PERSISTENT:
            event = self._promotion_event(proposal, truth, turn_id=turn_id)
            truth = truth.promote(
                TruthLifetime.PERSISTENT,
                turn_id=turn_id,
                reason=event.reason,
                event_id=event.event_id,
                expires_turn=None,
            )
            return ProposalPolicyResult(
                ProposalDecision.PROMOTE_PERSISTENT,
                proposal,
                truth=truth,
                event=event,
                reason=event.reason,
            )
        decision = (
            ProposalDecision.ACCEPT_SCENE
            if target is TruthLifetime.SCENE
            else ProposalDecision.ACCEPT_TURN
        )
        return ProposalPolicyResult(
            decision,
            proposal,
            truth=truth,
            reason=self._acceptance_reason(proposal, target),
        )

    def _preflight(
        self,
        proposal: WorldProposal,
        current: tuple[SoftTruthRecord, ...],
    ) -> ProposalPolicyResult | None:
        if proposal.visibility == "hidden":
            return ProposalPolicyResult(
                ProposalDecision.REJECT_HIDDEN,
                proposal,
                reason="hidden proposals cannot enter visible recovery truth",
            )
        if not proposal.world_consistent:
            return ProposalPolicyResult(
                ProposalDecision.REJECT_INCONSISTENT,
                proposal,
                reason="proposal conflicts with authoritative world rules",
            )
        dedupe_key = proposal.normalized_dedupe_key()
        for row in current:
            if str(row.metadata.get("dedupe_key") or "") == dedupe_key:
                return ProposalPolicyResult(
                    ProposalDecision.REJECT_DUPLICATE,
                    proposal,
                    reason=f"duplicates existing truth {row.truth_ref}",
                )
        if (
            proposal.risk is ProposalRisk.HIGH
            and proposal.requested_lifetime is TruthLifetime.PERSISTENT
            and (not proposal.resolver_name or not proposal.resolver_approved)
        ):
            # Preserve the idea only as turn-scoped inference; never persist it.
            return None
        return None

    def _target_lifetime(self, proposal: WorldProposal) -> TruthLifetime:
        if proposal.risk is ProposalRisk.HIGH:
            return (
                TruthLifetime.PERSISTENT
                if proposal.resolver_name and proposal.resolver_approved
                else TruthLifetime.TURN
            )
        if proposal.requested_lifetime is TruthLifetime.PERSISTENT:
            promotable = bool(
                proposal.player_interactions >= 1
                or proposal.relevance_count >= 3
                or proposal.director_approved
            )
            return TruthLifetime.PERSISTENT if promotable else TruthLifetime.SCENE
        if proposal.requested_lifetime is TruthLifetime.SCENE:
            return TruthLifetime.SCENE
        if proposal.risk is ProposalRisk.MEDIUM:
            return TruthLifetime.SCENE
        return TruthLifetime.TURN

    def _budget_exceeded(
        self,
        existing: tuple[SoftTruthRecord, ...],
        target: TruthLifetime,
        scene_id: str,
    ) -> bool:
        if target is TruthLifetime.TURN:
            return sum(row.lifetime is TruthLifetime.TURN for row in existing) >= self.budget.max_turn
        if target is TruthLifetime.SCENE:
            count = sum(
                row.lifetime is TruthLifetime.SCENE
                and (not scene_id or row.scene_id == scene_id)
                for row in existing
            )
            return count >= self.budget.max_scene
        return sum(row.lifetime is TruthLifetime.PERSISTENT for row in existing) >= self.budget.max_persistent

    @staticmethod
    def _acceptance_reason(
        proposal: WorldProposal,
        target: TruthLifetime,
    ) -> str:
        if target is TruthLifetime.PERSISTENT:
            if proposal.resolver_approved:
                return f"approved by deterministic resolver {proposal.resolver_name}"
            if proposal.player_interactions:
                return "promoted after player interaction"
            if proposal.director_approved:
                return "promoted by deterministic world-director policy"
            return "promoted after repeated relevance"
        if target is TruthLifetime.SCENE:
            return "accepted as reversible scene-scoped soft truth"
        return "accepted as ephemeral turn-scoped soft truth"

    @staticmethod
    def _promotion_event(
        proposal: WorldProposal,
        truth: SoftTruthRecord,
        *,
        turn_id: str,
    ) -> ProposalPromotionEvent:
        reason = ProposalPolicy._acceptance_reason(
            proposal,
            TruthLifetime.PERSISTENT,
        )
        event_id = _stable_key(
            proposal.seed,
            proposal.proposal_id,
            truth.truth_ref,
            TruthLifetime.PERSISTENT.value,
            reason,
        )
        return ProposalPromotionEvent(
            event_id=f"proposal-promotion:{event_id[:24]}",
            proposal_id=proposal.proposal_id,
            truth_ref=truth.truth_ref,
            from_lifetime=TruthLifetime.TURN,
            to_lifetime=TruthLifetime.PERSISTENT,
            turn_id=turn_id,
            reason=reason,
            source=proposal.source,
            seed=proposal.seed,
            resolver_name=proposal.resolver_name,
        )


@dataclass
class ProposalStore:
    truths: dict[str, SoftTruthRecord] = field(default_factory=dict)
    promotion_events: dict[str, ProposalPromotionEvent] = field(default_factory=dict)

    def apply(self, result: ProposalPolicyResult) -> bool:
        if result.truth is None:
            return False
        existing = self.truths.get(result.truth.truth_ref)
        if existing == result.truth:
            if result.event is not None:
                self.promotion_events.setdefault(result.event.event_id, result.event)
            return False
        self.truths[result.truth.truth_ref] = result.truth
        if result.event is not None:
            self.promotion_events.setdefault(result.event.event_id, result.event)
        return True

    def garbage_collect(
        self,
        *,
        current_turn: int,
        scene_id: str = "",
    ) -> tuple[str, ...]:
        removed: list[str] = []
        for truth_ref, truth in tuple(self.truths.items()):
            if truth.is_expired(current_turn=current_turn, scene_id=scene_id):
                removed.append(truth_ref)
                del self.truths[truth_ref]
        return tuple(sorted(removed))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "rpg_proposal_store_v1",
            "truths": [
                self.truths[key].as_dict() for key in sorted(self.truths)
            ],
            "promotion_events": [
                self.promotion_events[key].as_dict()
                for key in sorted(self.promotion_events)
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProposalStore":
        store = cls()
        for row in payload.get("truths", ()):
            if isinstance(row, Mapping):
                truth = SoftTruthRecord.from_dict(row)
                store.truths.setdefault(truth.truth_ref, truth)
        for row in payload.get("promotion_events", ()):
            if not isinstance(row, Mapping):
                continue
            event = ProposalPromotionEvent(
                event_id=str(row.get("event_id") or ""),
                proposal_id=str(row.get("proposal_id") or ""),
                truth_ref=str(row.get("truth_ref") or ""),
                from_lifetime=TruthLifetime(
                    str(row.get("from_lifetime") or "turn")
                ),
                to_lifetime=TruthLifetime(
                    str(row.get("to_lifetime") or "persistent")
                ),
                turn_id=str(row.get("turn_id") or ""),
                reason=str(row.get("reason") or ""),
                source=str(row.get("source") or ""),
                seed=str(row.get("seed") or ""),
                resolver_name=str(row.get("resolver_name") or ""),
            )
            store.promotion_events.setdefault(event.event_id, event)
        return store


def _stable_key(*values: Any) -> str:
    payload = json.dumps(
        [str(value or "").strip().casefold() for value in values],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
