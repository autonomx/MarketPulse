from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""Durable authority ledger for the prospective-gap experiment.

The experiment reuses Omnix's StrategyEvent persistence instead of inventing a
parallel database. Every persisted record is machine-readable, idempotent, and
bound to a session/cohort/instrument. Markdown activity logs are projections of
this ledger, never execution authority.
"""

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Final, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .strategy_repository import StrategyEvent, TradingStrategyRepository, default_strategy_repository
from app.apps.trading.us_equity_calendar import EASTERN as _ET


PROSPECTIVE_GAP_STRATEGY_ID = "prospective-gap-experiment"
PROSPECTIVE_GAP_LEDGER_VERSION: Final = "prospective-gap-ledger-v1"

ProspectiveRecordKind = Literal[
    "session_manifest",
    "premarket_input",
    "premarket_evidence",
    "premarket_state",
    "v3_forecast",
    "v4_attempt",
    "v4_forecast",
    "confirmation",
    "authorization",
    "formal_outcome",
    "legacy_portfolios",
    "legacy_portfolio_scores",
    "portfolio_e",
    "daily_scorecard",
    "v41_shadow_spec",
    "v42_shadow_spec",
    "v42_action_spec",
    "v42_attempt",
    "v42_forecast",
    "v42_watch",
    "v42_action",
    "v42_authorization",
    "portfolio_f",
    "portfolio_f_score",
    "v43_shadow_spec",
    "v43_action_spec",
    "v43_attempt",
    "v43_cohort_regime",
    "v43_forecast",
    "v43_watch",
    "v43_action",
    "v43_authorization",
    "portfolio_g",
    "portfolio_g_score",
]


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def prospective_session_id(session_date: date) -> str:
    return f"prospective-gap:{session_date.isoformat()}"


def _bounds(session_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(session_date, time(0, 0), tzinfo=_ET).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


class ProspectiveGapRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ledger_version: Literal["prospective-gap-ledger-v1"] = PROSPECTIVE_GAP_LEDGER_VERSION
    session_date: date
    cohort_id: str
    instrument_id: str
    kind: ProspectiveRecordKind
    observed_at: datetime
    payload: dict[str, Any]
    payload_fingerprint: str


class ProspectiveGapSessionLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    session_id: str
    records: tuple[ProspectiveGapRecord, ...] = ()

    def records_of_kind(self, kind: ProspectiveRecordKind) -> tuple[ProspectiveGapRecord, ...]:
        return tuple(record for record in self.records if record.kind == kind)

    def latest(
        self,
        *,
        kind: ProspectiveRecordKind,
        instrument_id: str | None = None,
    ) -> ProspectiveGapRecord | None:
        rows = [
            row
            for row in self.records
            if row.kind == kind and (instrument_id is None or row.instrument_id == instrument_id)
        ]
        return max(rows, key=lambda row: row.observed_at) if rows else None


class ProspectiveGapRepository:
    def __init__(self, repository: TradingStrategyRepository | None = None) -> None:
        self.repository = repository or default_strategy_repository()

    def append(
        self,
        *,
        session_date: date,
        cohort_id: str,
        instrument_id: str,
        kind: ProspectiveRecordKind,
        observed_at: datetime,
        payload: BaseModel | dict[str, Any],
        state: str = "frozen",
        reason_code: str | None = None,
        run_id: str | None = None,
        idempotency_suffix: str | None = None,
    ) -> bool:
        if observed_at.tzinfo is None:
            raise ValueError("prospective record observed_at must be timezone-aware")
        body = (
            payload.model_dump(mode="json")
            if isinstance(payload, BaseModel)
            else dict(payload)
        )
        fingerprint = _hash(body)
        suffix = idempotency_suffix or fingerprint
        key = (
            f"{PROSPECTIVE_GAP_LEDGER_VERSION}:{session_date.isoformat()}:"
            f"{cohort_id}:{instrument_id}:{kind}:{suffix}"
        )
        event = StrategyEvent(
            strategy_id=PROSPECTIVE_GAP_STRATEGY_ID,
            event_id=f"pge-{_hash(key)[:24]}",
            run_id=run_id,
            instrument_id=instrument_id,
            event_type=f"prospective_gap_{kind}",
            state=state,
            reason_code=reason_code,
            observed_at=observed_at.astimezone(timezone.utc),
            idempotency_key=key,
            correlation_version=PROSPECTIVE_GAP_LEDGER_VERSION,
            session_id=prospective_session_id(session_date),
            setup_id=cohort_id,
            payload={
                "ledger_version": PROSPECTIVE_GAP_LEDGER_VERSION,
                "session_date": session_date.isoformat(),
                "cohort_id": cohort_id,
                "kind": kind,
                "payload_fingerprint": fingerprint,
                "data": body,
            },
        )
        return self.repository.append_event(event)

    def session(self, session_date: date) -> ProspectiveGapSessionLedger:
        start, end = _bounds(session_date)
        rows = self.repository.events_between(
            PROSPECTIVE_GAP_STRATEGY_ID,
            start_time=start,
            end_time=end,
            limit=50_000,
        )
        records: list[ProspectiveGapRecord] = []
        for row in rows:
            if row.session_id != prospective_session_id(session_date):
                continue
            payload = dict(row.payload or {})
            raw_kind = str(payload.get("kind") or "")
            if not raw_kind:
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            try:
                records.append(
                    ProspectiveGapRecord(
                        session_date=session_date,
                        cohort_id=str(payload.get("cohort_id") or row.setup_id or ""),
                        instrument_id=row.instrument_id,
                        kind=raw_kind,  # type: ignore[arg-type]
                        observed_at=row.observed_at,
                        payload=data,
                        payload_fingerprint=str(payload.get("payload_fingerprint") or _hash(data)),
                    )
                )
            except Exception:
                logger.debug("suppressed error in %s", "ProspectiveGapRepository.session", exc_info=True)
                continue
        return ProspectiveGapSessionLedger(
            session_date=session_date,
            session_id=prospective_session_id(session_date),
            records=tuple(records),
        )

    def latest_model(
        self,
        session_date: date,
        *,
        kind: ProspectiveRecordKind,
        model: type[BaseModel],
        instrument_id: str | None = None,
    ) -> BaseModel | None:
        record = self.session(session_date).latest(kind=kind, instrument_id=instrument_id)
        return model.model_validate(record.payload) if record is not None else None


def default_prospective_gap_repository() -> ProspectiveGapRepository:
    return ProspectiveGapRepository()


__all__ = [
    "PROSPECTIVE_GAP_LEDGER_VERSION",
    "PROSPECTIVE_GAP_STRATEGY_ID",
    "ProspectiveGapRecord",
    "ProspectiveGapRepository",
    "ProspectiveGapSessionLedger",
    "ProspectiveRecordKind",
    "default_prospective_gap_repository",
    "prospective_session_id",
]
