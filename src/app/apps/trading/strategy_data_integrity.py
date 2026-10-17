from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .gapper_dataset import GapperUniverseSnapshot
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_REGULAR_OPEN = time(9, 30)
FINVIZ_ATOMIC_FIRST_PAGE_MAX = 20
FINVIZ_ATOMIC_FIRST_PAGE_TAG = "omnix-atomic-first-page-v1"


class UniverseIntegrityAssessment(BaseModel):
    """Derived prospective-integrity state for one frozen strategy universe."""

    model_config = ConfigDict(frozen=True)

    capture_on_time: bool
    cohort_complete: bool
    cohort_integrity: Literal["valid", "invalid"]
    source_members_accounted: bool = True
    source_member_failure_count: int = 0
    market_data_complete: bool
    prospective_eligible: bool
    reason_codes: tuple[str, ...] = ()


def finviz_atomic_source_locator(source_url: str) -> str:
    clean = str(source_url or "").split("#", 1)[0]
    return f"{clean}#{FINVIZ_ATOMIC_FIRST_PAGE_TAG}"


def assess_universe_integrity(snapshot: GapperUniverseSnapshot) -> UniverseIntegrityAssessment:
    """Derive fail-closed cohort validity without mutating the frozen snapshot.

    Cohort integrity and candidate/session evaluability are deliberately separate.
    A source member may have failed enrichment while the atomic Finviz capture is
    still real and useful for research. New archives must nevertheless account for
    every source symbol so a provider outage cannot silently shrink the cohort.
    """

    reasons: list[str] = []
    evaluation_et = snapshot.evaluation_time.astimezone(_ET)
    is_finviz = snapshot.discovery_source == "finviz"

    capture_on_time = True
    cohort_complete = True
    cohort_integrity: Literal["valid", "invalid"] = "valid"
    source_members_accounted = True
    source_member_failure_count = 0

    if is_finviz:
        capture_on_time = (
            evaluation_et.date() == snapshot.session_date
            and evaluation_et.time() < _REGULAR_OPEN
        )
        if not capture_on_time:
            reasons.append("FINVIZ_CAPTURE_NOT_PREOPEN")

        locator = str(snapshot.source_locator or "")
        atomic = f"#{FINVIZ_ATOMIC_FIRST_PAGE_TAG}" in locator
        cohort_size = len(snapshot.source_candidate_symbols)
        cohort_complete = atomic and 0 < cohort_size <= FINVIZ_ATOMIC_FIRST_PAGE_MAX
        if not cohort_complete:
            reasons.append("FINVIZ_ATOMIC_COHORT_UNPROVEN")
        cohort_integrity = "valid" if cohort_complete else "invalid"

        dispositions = tuple(getattr(snapshot, "source_member_dispositions", ()))
        if dispositions:
            source_members_accounted = (
                len(dispositions) == cohort_size
                and tuple(item.symbol for item in dispositions)
                == tuple(snapshot.source_candidate_symbols)
            )
            source_member_failure_count = sum(
                1
                for item in dispositions
                if item.status in {"enrichment_failed", "provider_unavailable"}
            )
            if not source_members_accounted:
                reasons.append("SOURCE_MEMBER_DISPOSITIONS_INCOMPLETE")
            if source_member_failure_count:
                reasons.append("SOURCE_MEMBER_DATA_FAILURE")
        elif cohort_size:
            # Legacy archives predate disposition persistence. Keep their cohort
            # provenance readable, but they are not eligible for the new truthful
            # source-accounting contract.
            source_members_accounted = False
            reasons.append("SOURCE_MEMBER_DISPOSITIONS_MISSING")

    market_data_complete = all(
        candidate.market_data_complete for candidate in snapshot.candidates
    )
    if snapshot.candidates and not market_data_complete:
        reasons.append("CANDIDATE_MARKET_DATA_INCOMPLETE")

    prospective_eligible = (
        capture_on_time
        and cohort_complete
        and cohort_integrity == "valid"
        and source_members_accounted
    )
    return UniverseIntegrityAssessment(
        capture_on_time=capture_on_time,
        cohort_complete=cohort_complete,
        cohort_integrity=cohort_integrity,
        source_members_accounted=source_members_accounted,
        source_member_failure_count=source_member_failure_count,
        market_data_complete=market_data_complete,
        prospective_eligible=prospective_eligible,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "FINVIZ_ATOMIC_FIRST_PAGE_MAX",
    "FINVIZ_ATOMIC_FIRST_PAGE_TAG",
    "UniverseIntegrityAssessment",
    "assess_universe_integrity",
    "finviz_atomic_source_locator",
]
