from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""Post-close outcome labeling and evidence-only qualification for interday discovery."""

import hashlib
from datetime import date, datetime, time, timedelta, timezone

from .strategy_dynamic_discovery import (
    DynamicCandidate,
    ShadowQualificationEvidence,
    TrendDurabilityOutcome,
)
from .strategy_dynamic_discovery_learning import DiscoveryDailyReport
from .strategy_repository import StrategyEvent, TradingStrategyRepository
from app.apps.trading.us_equity_calendar import EASTERN as _ET

EVENT_OUTCOME = "interday_discovery_outcome"


def _event_id(*values: object) -> str:
    return hashlib.sha256("|".join(str(value) for value in values).encode("utf-8")).hexdigest()[:32]


def _session_bounds(session_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(session_date, time(0, 0), tzinfo=_ET).astimezone(timezone.utc)
    end = (datetime.combine(session_date, time(0, 0), tzinfo=_ET) + timedelta(days=1)).astimezone(timezone.utc)
    return start, end


def _regular_session_bars(response, *, session_date: date, observed_at: datetime):
    cutoff = observed_at.astimezone(timezone.utc)
    return sorted(
        [
            bar
            for bar in list(getattr(response, "bars", ()) or ())
            if bool(getattr(bar, "is_final", False))
            and getattr(bar, "session", None) == "regular"
            and bar.end_time.astimezone(timezone.utc) <= cutoff
            and bar.start_time.astimezone(_ET).date() == session_date
        ],
        key=lambda bar: bar.end_time,
    )


def label_candidate_outcome(
    market_service,
    candidate: DynamicCandidate,
    *,
    observed_at: datetime,
) -> TrendDurabilityOutcome | None:
    """Produce the causal OHLC discovery-path label for one candidate."""

    from .strategy_dynamic_discovery_runtime import _label_candidate_outcome_complete

    return _label_candidate_outcome_complete(
        market_service,
        candidate,
        observed_at=observed_at,
    )


def persist_candidate_outcome(
    repository: TradingStrategyRepository,
    outcome: TrendDurabilityOutcome,
    *,
    session_date: date,
    observed_at: datetime,
) -> bool:
    event_id = _event_id(EVENT_OUTCOME, session_date, outcome.instrument_id)
    return repository.append_event(
        StrategyEvent(
            strategy_id="interday-trading-strategy-shadow",
            event_id=event_id,
            run_id=f"interday-discovery:{session_date.isoformat()}",
            instrument_id=outcome.instrument_id,
            event_type=EVENT_OUTCOME,
            state="labeled",
            reason_code=None,
            observed_at=observed_at,
            idempotency_key=f"{EVENT_OUTCOME}:{session_date.isoformat()}:{outcome.instrument_id}",
            payload=outcome.model_dump(mode="json"),
        )
    )


def session_outcomes(
    repository: TradingStrategyRepository,
    *,
    session_date: date,
) -> tuple[TrendDurabilityOutcome, ...]:
    start, end = _session_bounds(session_date)
    rows = repository.events_by_types_between(
        "interday-trading-strategy-shadow",
        event_types=(EVENT_OUTCOME,),
        start_time=start,
        end_time=end,
        limit=10_000,
    )
    values: list[TrendDurabilityOutcome] = []
    for row in rows:
        try:
            values.append(TrendDurabilityOutcome.model_validate(row.payload))
        except Exception:
            logger.debug("suppressed error in %s", "session_outcomes", exc_info=True)
            continue
    return tuple(values)


def qualification_from_persisted_evidence(
    repository: TradingStrategyRepository,
    *,
    current_report: DiscoveryDailyReport | None = None,
    data_reliability_fraction: float = 1.0,
    causality_violations: int = 0,
) -> ShadowQualificationEvidence:
    """Build the durable review gate with execution and holdout criteria."""

    from .strategy_dynamic_discovery_runtime import (
        _qualification_from_persisted_evidence_complete,
    )

    return _qualification_from_persisted_evidence_complete(
        repository,
        current_report=current_report,
        data_reliability_fraction=data_reliability_fraction,
        causality_violations=causality_violations,
    )


__all__ = [
    "EVENT_OUTCOME",
    "label_candidate_outcome",
    "persist_candidate_outcome",
    "qualification_from_persisted_evidence",
    "session_outcomes",
]
