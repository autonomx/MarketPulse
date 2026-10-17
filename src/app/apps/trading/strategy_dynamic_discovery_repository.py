from __future__ import annotations

"""Append-only persistence adapter for interday causal discovery.

The parent strategy event ledger is already idempotent, durable and surfaced by
existing strategy APIs. Dynamic discovery therefore persists immutable facts
there instead of introducing a parallel mutable store.
"""

import hashlib
from datetime import date, datetime, time, timedelta, timezone

from .strategy_dynamic_discovery import (
    AttributionEvent,
    DynamicCandidate,
    DiscoveryEvent,
    INTERDAY_TRADING_STRATEGY_ID,
    ShadowQualificationEvidence,
)
from .strategy_repository import StrategyEvent, TradingStrategyRepository
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from typing import Any

EVENT_DISCOVERY = "interday_discovery_event"
EVENT_CANDIDATE = "interday_dynamic_candidate"
EVENT_ATTRIBUTION = "interday_candidate_attribution"
EVENT_DAILY_REPORT = "interday_discovery_daily_report"
EVENT_QUALIFICATION = "interday_discovery_qualification"
EVENT_REPLAY = "interday_discovery_replay"

# 0045/0046 historically mirrored lifecycle correlation envelope fields into
# every StrategyEvent payload. DynamicCandidate is intentionally strict
# (extra="forbid"), so those database-added keys make an otherwise valid typed
# candidate impossible to deserialize. 0082 stops new payload mutation; this
# list is the narrow compatibility boundary for already-persisted candidate
# rows. Candidate domain models have never owned these fields, so stripping them
# here does not weaken their schema or hide arbitrary extras.
_LEGACY_CORRELATION_ENVELOPE_KEYS = frozenset(
    {
        "correlation_version",
        "strategy_revision",
        "session_id",
        "setup_id",
        "trade_attempt_id",
        "trade_intent_id",
        "risk_decision_id",
    }
)


def _candidate_domain_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in _LEGACY_CORRELATION_ENVELOPE_KEYS
    }


def _event_id(*values: object) -> str:
    raw = "|".join(str(value) for value in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("dynamic_discovery_repository_timestamp_must_be_aware")
    return value.astimezone(timezone.utc)


def session_bounds(session_date: date) -> tuple[datetime, datetime]:
    start_et = datetime.combine(session_date, time(0, 0), tzinfo=_ET)
    start = start_et.astimezone(timezone.utc)
    end = (start_et + timedelta(days=1)).astimezone(timezone.utc)
    return start, end


class DynamicDiscoveryEventRepository:
    def __init__(self, repository: TradingStrategyRepository) -> None:
        self.repository = repository

    def persist_discovery(self, event: DiscoveryEvent) -> bool:
        row = StrategyEvent(
            strategy_id=INTERDAY_TRADING_STRATEGY_ID,
            event_id=event.event_id,
            run_id=f"interday-discovery:{event.session_date.isoformat()}",
            instrument_id=event.instrument_id,
            event_type=EVENT_DISCOVERY,
            state=event.trigger_type.value,
            reason_code=None,
            observed_at=event.discovered_at,
            idempotency_key=f"{EVENT_DISCOVERY}:{event.event_id}",
            payload=event.model_dump(mode="json"),
        )
        return self.repository.append_event(row)

    def persist_candidate(
        self,
        candidate: DynamicCandidate,
        *,
        snapshot_at: datetime | None = None,
    ) -> bool:
        """Persist one candidate-state snapshot at the time that state became known.

        ``candidate.last_observed_at`` intentionally means the last causal market
        observation. Lifecycle transitions such as cooling, expiry or tier
        demotion can become known later without changing that market timestamp.
        Using ``snapshot_at`` for the ledger row keeps those later transitions
        correctly ordered while preserving the candidate's causal payload.
        """

        recorded_at = _aware_utc(snapshot_at or candidate.last_observed_at)
        if recorded_at < candidate.last_observed_at:
            raise ValueError("candidate_snapshot_cannot_precede_last_observation")
        event_id = _event_id(
            EVENT_CANDIDATE,
            candidate.instrument_id,
            candidate.session_date,
            recorded_at.isoformat(),
            candidate.last_observed_at.isoformat(),
            candidate.lifecycle.value,
            candidate.tier.value,
            candidate.common_priority,
        )
        row = StrategyEvent(
            strategy_id=INTERDAY_TRADING_STRATEGY_ID,
            event_id=event_id,
            run_id=f"interday-discovery:{candidate.session_date.isoformat()}",
            instrument_id=candidate.instrument_id,
            event_type=EVENT_CANDIDATE,
            state=candidate.lifecycle.value,
            reason_code=None,
            observed_at=recorded_at,
            idempotency_key=f"{EVENT_CANDIDATE}:{event_id}",
            payload=candidate.model_dump(mode="json"),
        )
        return self.repository.append_event(row)

    def persist_attribution(self, event: AttributionEvent) -> bool:
        event_id = _event_id(
            EVENT_ATTRIBUTION,
            event.instrument_id,
            event.stage.value,
            event.sub_strategy,
            event.observed_at.isoformat(),
            event.passed,
            event.reason,
        )
        row = StrategyEvent(
            strategy_id=INTERDAY_TRADING_STRATEGY_ID,
            event_id=event_id,
            run_id=f"interday-discovery:{event.session_date.isoformat()}",
            instrument_id=event.instrument_id,
            event_type=EVENT_ATTRIBUTION,
            state=event.stage.value,
            reason_code=event.reason,
            observed_at=event.observed_at,
            idempotency_key=f"{EVENT_ATTRIBUTION}:{event_id}",
            payload=event.model_dump(mode="json"),
        )
        return self.repository.append_event(row)

    def persist_report(
        self,
        *,
        session_date: date,
        observed_at: datetime,
        payload: dict[str, Any],
    ) -> bool:
        event_id = _event_id(EVENT_DAILY_REPORT, session_date)
        return self.repository.append_event(
            StrategyEvent(
                strategy_id=INTERDAY_TRADING_STRATEGY_ID,
                event_id=event_id,
                run_id=f"interday-discovery:{session_date.isoformat()}",
                instrument_id="portfolio:interday-discovery",
                event_type=EVENT_DAILY_REPORT,
                state="complete",
                observed_at=observed_at,
                idempotency_key=f"{EVENT_DAILY_REPORT}:{session_date.isoformat()}",
                payload=payload,
            )
        )

    def persist_qualification(
        self,
        *,
        session_date: date,
        observed_at: datetime,
        evidence: ShadowQualificationEvidence,
    ) -> bool:
        # One immutable evidence snapshot per session. If a process dies after
        # the report is written but before this append, a later monitor pass can
        # safely retry the same idempotency key.
        event_id = _event_id(EVENT_QUALIFICATION, session_date)
        return self.repository.append_event(
            StrategyEvent(
                strategy_id=INTERDAY_TRADING_STRATEGY_ID,
                event_id=event_id,
                run_id=f"interday-discovery:{session_date.isoformat()}",
                instrument_id="portfolio:interday-discovery",
                event_type=EVENT_QUALIFICATION,
                state="eligible_for_review" if evidence.eligible_for_review else "not_qualified",
                reason_code=None if evidence.eligible_for_review else "SHADOW_EVIDENCE_GATE_NOT_MET",
                observed_at=observed_at,
                idempotency_key=f"{EVENT_QUALIFICATION}:{session_date.isoformat()}",
                payload=evidence.model_dump(mode="json"),
            )
        )

    def persist_replay(
        self,
        *,
        session_date: date,
        observed_at: datetime,
        payload: dict[str, Any],
    ) -> bool:
        event_id = _event_id(EVENT_REPLAY, session_date, payload.get("fingerprint"))
        return self.repository.append_event(
            StrategyEvent(
                strategy_id=INTERDAY_TRADING_STRATEGY_ID,
                event_id=event_id,
                run_id=f"interday-discovery-replay:{session_date.isoformat()}",
                instrument_id="portfolio:interday-discovery",
                event_type=EVENT_REPLAY,
                state="complete",
                observed_at=observed_at,
                idempotency_key=f"{EVENT_REPLAY}:{event_id}",
                payload=payload,
            )
        )

    def session_events(self, session_date: date, *, limit: int = 50_000) -> list[StrategyEvent]:
        start, end = session_bounds(session_date)
        return self.repository.events_by_types_between(
            INTERDAY_TRADING_STRATEGY_ID,
            event_types=(
                EVENT_DISCOVERY,
                EVENT_CANDIDATE,
                EVENT_ATTRIBUTION,
                EVENT_DAILY_REPORT,
                EVENT_QUALIFICATION,
                EVENT_REPLAY,
            ),
            start_time=start,
            end_time=end,
            limit=limit,
        )

    def latest_candidates(self, session_date: date) -> dict[str, DynamicCandidate]:
        rows = self.session_events(session_date)
        latest: dict[str, StrategyEvent] = {}
        for row in rows:
            if row.event_type != EVENT_CANDIDATE:
                continue
            current = latest.get(row.instrument_id)
            if current is None or (row.observed_at, row.event_id) > (
                current.observed_at,
                current.event_id,
            ):
                latest[row.instrument_id] = row
        return {
            instrument_id: DynamicCandidate.model_validate(
                _candidate_domain_payload(row.payload)
            )
            for instrument_id, row in latest.items()
        }


__all__ = [
    "DynamicDiscoveryEventRepository",
    "EVENT_ATTRIBUTION",
    "EVENT_CANDIDATE",
    "EVENT_DAILY_REPORT",
    "EVENT_DISCOVERY",
    "EVENT_QUALIFICATION",
    "EVENT_REPLAY",
    "session_bounds",
]
