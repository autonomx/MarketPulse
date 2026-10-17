from __future__ import annotations

"""Continuously refreshed causal execution-observation plane.

AI decisions consume this cache after they become actionable.  A fill is never
backfilled from a quote that existed before the model completed merely because a
later price move makes that counterfactual attractive.
"""

from collections import OrderedDict, deque
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .binding_authority import BindingPurpose
from .execution import ExecutionObservation

_DEFAULT_MAX_INSTRUMENTS = 512
_DEFAULT_RETENTION_SECONDS = 3600.0


class ExecutionObservationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation: ExecutionObservation
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("execution-plane recorded_at must be timezone-aware")
        return value.astimezone(timezone.utc)


class CausalExecutionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    binding_id: str
    provider: str
    market_snapshot_as_of: datetime
    decision_completed_at: datetime
    actionable_at: datetime
    first_post_decision_quote_at: datetime
    quote_source_at: datetime
    quote_received_at: datetime
    capture_lag_seconds: Decimal = Field(ge=0)
    observation: ExecutionObservation

    @field_validator(
        "market_snapshot_as_of",
        "decision_completed_at",
        "actionable_at",
        "first_post_decision_quote_at",
        "quote_source_at",
        "quote_received_at",
    )
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("causal execution timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class ExecutionObservationPlane:
    def __init__(
        self,
        *,
        max_observations_per_instrument: int = 600,
        max_instruments: int = _DEFAULT_MAX_INSTRUMENTS,
        retention_seconds: float = _DEFAULT_RETENTION_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_observations_per_instrument < 2:
            raise ValueError("execution plane requires at least two observations")
        if max_instruments < 1 or retention_seconds <= 0:
            raise ValueError("execution plane bounds must be positive")
        self.max_observations_per_instrument = max_observations_per_instrument
        self.max_instruments = max_instruments
        self.retention_seconds = retention_seconds
        self._clock = clock
        self._lock = RLock()
        self._rows: OrderedDict[str, deque[ExecutionObservationEnvelope]] = OrderedDict()
        self._seen: dict[str, set[tuple[object, ...]]] = {}
        self._last_touched: OrderedDict[str, float] = OrderedDict()

    @staticmethod
    def _identity(observation: ExecutionObservation) -> tuple[object, ...]:
        return (
            observation.binding_id,
            observation.binding_purpose,
            observation.provider,
            observation.source_time.astimezone(timezone.utc),
            observation.provider_sequence,
            observation.bid,
            observation.ask,
            observation.last,
            observation.market_data_type,
            observation.live_entitled,
            observation.contract_id,
        )

    def record(
        self,
        observation: ExecutionObservation,
        *,
        recorded_at: datetime | None = None,
    ) -> bool:
        recorded = recorded_at or datetime.now(timezone.utc)
        if recorded.tzinfo is None:
            raise ValueError("execution plane record clock must be timezone-aware")
        envelope = ExecutionObservationEnvelope(
            observation=observation,
            recorded_at=recorded,
        )
        key = self._identity(observation)
        instrument_id = observation.instrument_id
        now = self._clock()
        with self._lock:
            self._prune_expired_locked(now)
            rows = self._rows.get(instrument_id)
            if rows is not None:
                self._touch_instrument_locked(instrument_id, now)
            else:
                self._ensure_capacity_locked()
                rows = deque(maxlen=self.max_observations_per_instrument)
                self._rows[instrument_id] = rows
                self._seen[instrument_id] = set()
                self._touch_instrument_locked(instrument_id, now)
            seen = self._seen[instrument_id]
            if key in seen:
                return False
            if len(rows) == rows.maxlen and rows:
                old = rows[0]
                seen.discard(self._identity(old.observation))
            rows.append(envelope)
            seen.add(key)
        return True

    def observations(self, instrument_id: str) -> tuple[ExecutionObservationEnvelope, ...]:
        with self._lock:
            now = self._clock()
            self._prune_expired_locked(now)
            rows = self._rows.get(instrument_id)
            if rows is None:
                return ()
            self._touch_instrument_locked(instrument_id, now)
            return tuple(rows)

    def latest(
        self,
        instrument_id: str,
        *,
        as_of: datetime | None = None,
    ) -> ExecutionObservationEnvelope | None:
        cutoff = as_of.astimezone(timezone.utc) if as_of is not None else None
        rows = self.observations(instrument_id)
        eligible = [
            row
            for row in rows
            if cutoff is None or row.recorded_at <= cutoff
        ]
        return max(
            eligible,
            key=lambda row: (
                row.recorded_at,
                row.observation.source_time,
                row.observation.provider_sequence or -1,
            ),
            default=None,
        )

    def first_causal_after(
        self,
        instrument_id: str,
        *,
        decision_completed_at: datetime,
        actionable_at: datetime | None = None,
        market_snapshot_as_of: datetime | None = None,
        binding_id: str | None = None,
        purpose: BindingPurpose = "EXECUTION",
    ) -> CausalExecutionSelection | None:
        if decision_completed_at.tzinfo is None:
            raise ValueError("decision_completed_at must be timezone-aware")
        actionable = actionable_at or decision_completed_at
        if actionable.tzinfo is None:
            raise ValueError("actionable_at must be timezone-aware")
        decision_utc = decision_completed_at.astimezone(timezone.utc)
        actionable_utc = actionable.astimezone(timezone.utc)
        rows = self.observations(instrument_id)
        eligible = []
        for row in rows:
            observation = row.observation
            if observation.binding_purpose != purpose:
                continue
            if binding_id is not None and observation.binding_id != binding_id:
                continue
            # Both the market source clock and Omnix receipt/record clock must be
            # after the action became possible. This is intentionally stricter
            # than choosing a pre-decision quote from history.
            if observation.source_time.astimezone(timezone.utc) < actionable_utc:
                continue
            if row.recorded_at < actionable_utc:
                continue
            eligible.append(row)
        if not eligible:
            return None
        selected = min(
            eligible,
            key=lambda row: (
                row.recorded_at,
                row.observation.source_time,
                row.observation.provider_sequence or -1,
            ),
        )
        observation = selected.observation
        snapshot_as_of = market_snapshot_as_of or decision_completed_at
        lag = Decimal(
            str((selected.recorded_at - actionable_utc).total_seconds())
        )
        return CausalExecutionSelection(
            instrument_id=instrument_id,
            binding_id=observation.binding_id,
            provider=observation.provider,
            market_snapshot_as_of=snapshot_as_of,
            decision_completed_at=decision_utc,
            actionable_at=actionable_utc,
            first_post_decision_quote_at=selected.recorded_at,
            quote_source_at=observation.source_time,
            quote_received_at=observation.received_at,
            capture_lag_seconds=max(Decimal("0"), lag),
            observation=observation,
        )

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
            self._seen.clear()
            self._last_touched.clear()

    def _touch_instrument_locked(self, instrument_id: str, now: float) -> None:
        self._last_touched[instrument_id] = now
        self._last_touched.move_to_end(instrument_id)
        self._rows.move_to_end(instrument_id)

    def _prune_expired_locked(self, now: float) -> None:
        while self._last_touched:
            instrument_id, touched = next(iter(self._last_touched.items()))
            if now - touched <= self.retention_seconds:
                break
            self._last_touched.pop(instrument_id, None)
            self._rows.pop(instrument_id, None)
            self._seen.pop(instrument_id, None)

    def _ensure_capacity_locked(self) -> None:
        while len(self._rows) >= self.max_instruments:
            instrument_id, _ = self._last_touched.popitem(last=False)
            self._rows.pop(instrument_id, None)
            self._seen.pop(instrument_id, None)


_DEFAULT_PLANE = ExecutionObservationPlane(
    max_instruments=_DEFAULT_MAX_INSTRUMENTS,
    retention_seconds=_DEFAULT_RETENTION_SECONDS,
)


def default_execution_observation_plane() -> ExecutionObservationPlane:
    return _DEFAULT_PLANE


def clear_default_execution_observation_plane() -> None:
    _DEFAULT_PLANE.clear()


__all__ = [
    "CausalExecutionSelection",
    "clear_default_execution_observation_plane",
    "ExecutionObservationEnvelope",
    "ExecutionObservationPlane",
    "default_execution_observation_plane",
]
