from __future__ import annotations

from .proposal_policy import (
    ProposalDecision,
    ProposalPolicy,
    ProposalPolicyResult,
    ProposalRisk,
    WorldProposal,
)
from .truth_lifetime import SoftTruthRecord


class StrictProposalPolicy(ProposalPolicy):
    """Fail closed for every unresolved high-risk generated world proposal."""

    def _preflight(
        self,
        proposal: WorldProposal,
        current: tuple[SoftTruthRecord, ...],
    ) -> ProposalPolicyResult | None:
        rejection = super()._preflight(proposal, current)
        if rejection is not None:
            return rejection
        if proposal.risk is ProposalRisk.HIGH and (
            not proposal.resolver_name or not proposal.resolver_approved
        ):
            return ProposalPolicyResult(
                ProposalDecision.REJECT_RESOLVER_REQUIRED,
                proposal,
                reason=(
                    "high-risk proposal requires an approved deterministic resolver; "
                    "it cannot enter visible soft truth"
                ),
            )
        return None


__all__ = ["StrictProposalPolicy"]
