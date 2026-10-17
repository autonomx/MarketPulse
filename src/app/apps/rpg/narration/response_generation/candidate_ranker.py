from __future__ import annotations

from typing import Sequence

from .contracts import CandidateSource, ResponseCandidate
from .eligibility import eligibility_reasons


_SOURCE_TIE_BREAK = {
    CandidateSource.DETERMINISTIC: 4,
    CandidateSource.RECOVERY: 3,
    CandidateSource.HERMES_ASSISTED: 2,
    CandidateSource.PROVIDER: 2,
    CandidateSource.LEGACY_RUNTIME: 1,
    CandidateSource.LEGACY_WORLD_SCENE: 1,
}


class NoEligibleCandidateError(RuntimeError):
    def __init__(self, candidates: Sequence[ResponseCandidate]) -> None:
        self.candidates = tuple(candidates)
        details = {
            candidate.candidate_id: eligibility_reasons(candidate)
            for candidate in candidates
        }
        super().__init__(f"no eligible response candidates: {details}")


class CandidateRanker:
    """Rank only candidates that have passed every hard eligibility gate."""

    def rank(
        self,
        candidates: Sequence[ResponseCandidate],
    ) -> tuple[ResponseCandidate, ...]:
        eligible = [candidate for candidate in candidates if candidate.eligible]
        return tuple(sorted(eligible, key=self._score, reverse=True))

    def select(
        self,
        candidates: Sequence[ResponseCandidate],
    ) -> ResponseCandidate:
        ranked = self.rank(candidates)
        if not ranked:
            raise NoEligibleCandidateError(candidates)
        return ranked[0]

    @staticmethod
    def _score(candidate: ResponseCandidate) -> tuple[float, ...]:
        issue_count = len(candidate.repetition_issues) + len(candidate.style_issues)
        stale_penalty = 1.0 if candidate.provider_metadata.get("stale_prior_narration") else 0.0
        safe_bonus = 0.25 if candidate.provider_metadata.get("grounded_safe_fallback") else 0.0
        return (
            -stale_penalty,
            float(candidate.current_turn_relevance),
            float(candidate.forward_motion),
            float(candidate.specificity),
            float(candidate.naturalness),
            safe_bonus,
            -float(issue_count),
            -max(0.0, float(candidate.latency_ms)),
            float(_SOURCE_TIE_BREAK.get(candidate.source, 0)),
        )
