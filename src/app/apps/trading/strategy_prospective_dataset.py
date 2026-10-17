from __future__ import annotations

import logging

from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from .strategy_repository import StrategyEvent
from .strategy_v2_qualification import (
    FROZEN_V2_PROFILE_FINGERPRINT,
    V2_LIVE_MATCH_WINDOW_MINUTES,
    V2_PROSPECTIVE_START,
    V2_QUALIFICATION_VERSION,
    V2_REPLAY_VERSION,
)
from typing import Any

logger = logging.getLogger(__name__)


class ProspectiveSignalOutcomeRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    instrument_id: str
    session_date: date
    live_event_id: str
    replay_event_id: str
    signal_observed_at: datetime
    replay_entry_time: datetime
    r_result: Decimal
    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None
    feature_fingerprint: str
    features: dict[str, Any]

    @property
    def won(self) -> bool:
        return self.r_result > 0


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _entry_time(event: StrategyEvent) -> datetime | None:
    raw = event.payload.get("entry_time")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _session_date(event: StrategyEvent) -> date:
    raw = event.payload.get("session_date")
    if raw:
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            pass
    return event.observed_at.astimezone(timezone.utc).date()


def _canonical_replay(event: StrategyEvent, expected_profile: str) -> bool:
    return (
        event.event_type == "v2_shadow_replay_trade"
        and event.payload.get("qualification_version") == V2_QUALIFICATION_VERSION
        and event.payload.get("replay_version") == V2_REPLAY_VERSION
        and event.payload.get("profile_fingerprint") == expected_profile
        and event.payload.get("universe_source") == "auto_archive_shadow"
        and _session_date(event) >= V2_PROSPECTIVE_START
        and _entry_time(event) is not None
        and _decimal(event.payload.get("r_result")) is not None
    )


def _eligible_feature_event(event: StrategyEvent, expected_profile: str) -> bool:
    execution = event.payload.get("execution")
    if not isinstance(execution, dict):
        return False
    features = execution.get("prospective_signal_features")
    return (
        event.event_type == "shadow_execution"
        and event.reason_code == "SHADOW_EXECUTION_OBSERVED"
        and event.payload.get("execution_authority") is False
        and event.payload.get("universe_source") == "auto_archive_shadow"
        and event.payload.get("profile_fingerprint") == expected_profile
        and bool(execution.get("execution_eligible"))
        and isinstance(features, dict)
        and features.get("schema_version") == "v2-prospective-signal-features-1"
        and isinstance(features.get("immutable_fingerprint"), str)
        and event.observed_at.astimezone(timezone.utc).date() >= V2_PROSPECTIVE_START
    )


def _match_delta_minutes(replay: StrategyEvent, live: StrategyEvent) -> Decimal | None:
    if replay.instrument_id != live.instrument_id or _session_date(replay) != _session_date(live):
        return None
    entry = _entry_time(replay)
    if entry is None:
        return None
    observed = live.observed_at.astimezone(timezone.utc)
    return abs(Decimal(str((entry - observed).total_seconds())) / Decimal("60"))


def matched_prospective_signal_outcomes(
    events: list[StrategyEvent] | tuple[StrategyEvent, ...],
    *,
    expected_profile: str = FROZEN_V2_PROFILE_FINGERPRINT,
) -> tuple[ProspectiveSignalOutcomeRow, ...]:
    """Join exact persisted SHADOW feature rows to canonical replay outcomes.

    Matching preserves the V2 qualification boundary: same symbol/session and no
    more than the frozen live-match window. When multiple live observations are
    eligible, the causally closest observation is selected; ties use event id for
    deterministic replay. Each live event can be consumed at most once.
    """

    ordered = sorted(events, key=lambda item: (item.observed_at, item.event_id))
    replay_events = [event for event in ordered if _canonical_replay(event, expected_profile)]
    live_events = [event for event in ordered if _eligible_feature_event(event, expected_profile)]
    used_live: set[str] = set()
    rows: list[ProspectiveSignalOutcomeRow] = []
    for replay in replay_events:
        candidates: list[tuple[Decimal, datetime, str, StrategyEvent]] = []
        for live in live_events:
            if live.event_id in used_live:
                continue
            delta = _match_delta_minutes(replay, live)
            if delta is None or delta > Decimal(V2_LIVE_MATCH_WINDOW_MINUTES):
                continue
            candidates.append((delta, live.observed_at, live.event_id, live))
        if not candidates:
            continue
        _, _, _, live = min(candidates, key=lambda item: (item[0], item[1], item[2]))
        used_live.add(live.event_id)
        execution = live.payload["execution"]
        assert isinstance(execution, dict)
        features = execution["prospective_signal_features"]
        assert isinstance(features, dict)
        entry = _entry_time(replay)
        r_result = _decimal(replay.payload.get("r_result"))
        assert entry is not None and r_result is not None
        rows.append(
            ProspectiveSignalOutcomeRow(
                strategy_id=live.strategy_id,
                instrument_id=live.instrument_id,
                session_date=_session_date(replay),
                live_event_id=live.event_id,
                replay_event_id=replay.event_id,
                signal_observed_at=live.observed_at,
                replay_entry_time=entry,
                r_result=r_result,
                mfe_r=_decimal(replay.payload.get("mfe_r")),
                mae_r=_decimal(replay.payload.get("mae_r")),
                feature_fingerprint=str(features["immutable_fingerprint"]),
                features=features,
            )
        )
    return tuple(rows)


def prospective_dataset_readiness(
    rows: list[ProspectiveSignalOutcomeRow] | tuple[ProspectiveSignalOutcomeRow, ...],
) -> dict[str, Any]:
    total = len(rows)
    winners = sum(row.won for row in rows)

    def complete(row: ProspectiveSignalOutcomeRow, key: str) -> bool:
        completeness = row.features.get("completeness")
        return isinstance(completeness, dict) and completeness.get(key) is True

    return {
        "matched_trade_count": total,
        "winner_count": winners,
        "loser_count": total - winners,
        "win_rate": str(Decimal(winners) / Decimal(total)) if total else None,
        "premarket_available_count": sum(complete(row, "premarket_available") for row in rows),
        "research_available_count": sum(complete(row, "research_available") for row in rows),
        "halt_history_complete_count": sum(complete(row, "halt_history_complete") for row in rows),
        "momentum_full_warmup_count": sum(complete(row, "momentum_full_warmup") for row in rows),
        "all_core_available_count": sum(complete(row, "all_core_available") for row in rows),
        "distinct_sessions": len({row.session_date for row in rows}),
        "distinct_symbols": len({row.instrument_id for row in rows}),
    }


__all__ = [
    "ProspectiveSignalOutcomeRow",
    "matched_prospective_signal_outcomes",
    "prospective_dataset_readiness",
]
