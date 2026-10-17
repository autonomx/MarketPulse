from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

"""Durable Yahoo evidence and same-feed relative-volume authority.

Yahoo is useful free market evidence, but it is not represented as consolidated
SIP authority and never becomes execution authority.  This module persists
finalized Yahoo 1-minute observations (in PostgreSQL, WP-8.3) so transient HTTP
failures do not erase evidence that Omnix has already observed.

The store is intentionally provider-scoped.  Yahoo-relative RVOL compares a
Yahoo numerator with Yahoo historical denominators at the same clock minute; it
must never be relabelled as consolidated US-market volume.
"""

import threading
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .evidence_storage import YahooEvidenceBackend, default_evidence_backend
from .models import AdjustmentMode, MarketBar
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


_PREMARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_COUNTERS = (
    "persisted_bar_count",
    "loaded_bar_count",
    "repair_attempt_count",
    "repair_success_count",
    "repaired_bar_count",
    "unresolved_repair_count",
    "rvol_baseline_hit_count",
    "rvol_baseline_miss_count",
    "causal_replay_rejection_count",
    "evaluation_repaired_count",
    "evaluation_unresolved_count",
    "acquisition_attempt_count",
    "acquisition_success_count",
    "acquisition_failure_count",
    "acquisition_symbol_count",
)
_SESSION_COUNTERS = (
    "evaluation_count",
    "repaired_evaluation_count",
    "genuinely_blocked_evaluation_count",
    "passed_evaluation_count",
    "repaired_but_blocked_evaluation_count",
)
_REASON_PREFIX = "blocked_reason:"

YahooVolumeAuthority = Literal["provider_relative"]
KnowledgeMode = Literal["live", "causal_replay", "retroactive_research"]


class YahooRelativeVolumeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    observed_at: datetime
    cutoff_clock: str
    current_volume: Decimal = Field(ge=0)
    current_dollar_volume: Decimal = Field(ge=0)
    baseline_mean_volume: Decimal | None = Field(default=None, ge=0)
    baseline_session_count: int = Field(ge=0)
    rejected_baseline_session_count: int = Field(default=0, ge=0)
    baseline_min_coverage_ratio: Decimal = Field(default=Decimal("0.90"), ge=0, le=1)
    relative_volume: Decimal | None = Field(default=None, ge=0)
    current_bar_count: int = Field(ge=0)
    current_nonzero_bar_count: int = Field(ge=0)
    expected_bar_count: int = Field(ge=0)
    coverage_ratio: Decimal | None = Field(default=None, ge=0)
    volume_authority: YahooVolumeAuthority = "provider_relative"
    provider: Literal["yahoo"] = "yahoo"
    feed: Literal["extended_hours"] = "extended_hours"

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Yahoo evidence timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)


class YahooEvidenceStore:
    """Yahoo 1-minute evidence by revision, with durable evidence counters."""

    def __init__(self, backend: YahooEvidenceBackend | None = None) -> None:
        self.backend = backend if backend is not None else default_evidence_backend()
        self._lock = threading.RLock()

    def _count(self, **deltas: int) -> None:
        self.backend.add_counters("yahoo", {name: value for name, value in deltas.items() if value})

    @staticmethod
    def _symbol(value: str) -> str:
        clean = str(value or "").strip().upper()
        if ":" in clean:
            clean = clean.rsplit(":", 1)[-1]
        return "".join(ch for ch in clean if ch.isalnum() or ch in {".", "-"}) or "UNKNOWN"

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Yahoo evidence timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _empty_session_metrics(session_date: date) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "provider": "yahoo",
            "session_date": session_date.isoformat(),
            "evaluation_count": 0,
            "repaired_evaluation_count": 0,
            "genuinely_blocked_evaluation_count": 0,
            "passed_evaluation_count": 0,
            "repaired_but_blocked_evaluation_count": 0,
            "blocked_reason_counts": {},
        }

    def _read_session_metrics(self, session_date: date) -> dict[str, Any]:
        payload = self._empty_session_metrics(session_date)
        counts, updated_at = self.backend.session_counters("yahoo", session_date)
        for name in _SESSION_COUNTERS:
            payload[name] = max(0, int(counts.get(name, 0)))
        reasons = {
            name.removeprefix(_REASON_PREFIX): int(value)
            for name, value in counts.items()
            if name.startswith(_REASON_PREFIX) and int(value) > 0
        }
        payload["blocked_reason_counts"] = dict(sorted(reasons.items()))
        if updated_at is not None:
            payload["updated_at"] = updated_at.astimezone(timezone.utc).isoformat()
        return payload

    def _record_session_evaluation(
        self,
        session_date: date,
        *,
        repaired: bool,
        unresolved: bool,
        reason: str | None,
    ) -> None:
        deltas = {"evaluation_count": 1}
        if repaired:
            deltas["repaired_evaluation_count"] = 1
        if unresolved:
            deltas["genuinely_blocked_evaluation_count"] = 1
            if repaired:
                deltas["repaired_but_blocked_evaluation_count"] = 1
            reason_key = str(reason or "").strip() or "RECOVERY_UNRESOLVED"
            deltas[_REASON_PREFIX + reason_key] = 1
        else:
            deltas["passed_evaluation_count"] = 1
        self.backend.add_session_counters("yahoo", session_date, deltas)

    @staticmethod
    def _record_from_market_bar(bar: MarketBar) -> dict[str, Any]:
        return {
            "start_time": bar.start_time.astimezone(timezone.utc).isoformat(),
            "end_time": bar.end_time.astimezone(timezone.utc).isoformat(),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": str(bar.volume),
            "session": bar.session,
            "provider_event_id": bar.provider_event_id,
            "received_at": bar.received_at.astimezone(timezone.utc).isoformat(),
        }

    @staticmethod
    def _record_rank(record: dict[str, Any]) -> tuple[str, str]:
        return (
            str(record.get("received_at") or ""),
            str(record.get("provider_event_id") or ""),
        )

    def _persist_records(
        self,
        symbol: str,
        grouped: dict[date, list[dict[str, Any]]],
    ) -> int:
        normalized_symbol = self._symbol(symbol)
        written = sum(
            self.backend.add_revisions(normalized_symbol, session_date, records)
            for session_date, records in grouped.items()
        )
        self._count(persisted_bar_count=written)
        return written

    def persist_market_bars(self, bars: list[MarketBar] | tuple[MarketBar, ...]) -> int:
        grouped: dict[str, dict[date, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for bar in bars:
            if bar.provider != "yahoo" or bar.interval != "1m" or not bar.is_final:
                continue
            symbol = self._symbol(bar.instrument_id)
            local_date = bar.start_time.astimezone(_ET).date()
            grouped[symbol][local_date].append(self._record_from_market_bar(bar))
        total = 0
        for symbol, by_date in grouped.items():
            total += self._persist_records(symbol, by_date)
        return total

    def persist_chart_result(
        self,
        symbol: str,
        result: dict[str, Any],
        *,
        received_at: datetime,
        cutoff: datetime | None = None,
    ) -> int:
        """Persist finalized raw Yahoo 1m chart rows before an instrument is registered."""

        received = self._utc(received_at)
        cutoff_utc = self._utc(cutoff) if cutoff is not None else received
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not isinstance(quote, dict):
            return 0
        timestamps = result.get("timestamp") or []
        arrays = {name: quote.get(name) or [] for name in ("open", "high", "low", "close", "volume")}
        if not isinstance(timestamps, list):
            return 0

        grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for index, raw_timestamp in enumerate(timestamps):
            try:
                values = {name: arrays[name][index] for name in arrays}
            except (IndexError, TypeError):
                continue
            if any(values[name] is None for name in ("open", "high", "low", "close")):
                continue
            try:
                start = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
                end = start + timedelta(minutes=1)
                open_value = Decimal(str(values["open"]))
                high = Decimal(str(values["high"]))
                low = Decimal(str(values["low"]))
                close = Decimal(str(values["close"]))
                volume = Decimal(str(values["volume"] or 0))
            except Exception:
                logger.debug("suppressed error in %s", "YahooEvidenceStore.persist_chart_result", exc_info=True)
                continue
            if end > cutoff_utc:
                continue
            if not all(value.is_finite() for value in (open_value, high, low, close, volume)):
                continue
            if high < max(open_value, close) or low > min(open_value, close) or volume < 0:
                continue
            local = start.astimezone(_ET)
            clock = local.timetz().replace(tzinfo=None)
            if clock < _REGULAR_OPEN:
                session = "extended_pre"
            elif clock >= regular_close_time(local.date()):
                session = "extended_post"
            else:
                session = "regular"
            grouped[local.date()].append(
                {
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "open": str(open_value),
                    "high": str(high),
                    "low": str(low),
                    "close": str(close),
                    "volume": str(volume),
                    "session": session,
                    "provider_event_id": str(raw_timestamp),
                    "received_at": received.isoformat(),
                }
            )
        return self._persist_records(self._symbol(symbol), grouped)

    def _records_for_date(self, symbol: str, session_date: date) -> list[dict[str, Any]]:
        """Every stored revision for the session, oldest receipt first per bar."""

        return sorted(
            self.backend.revisions(self._symbol(symbol), session_date),
            key=lambda row: (
                str(row.get("start_time") or ""),
                self._record_rank(row),
            ),
        )

    def _selected_records_for_date(
        self,
        symbol: str,
        session_date: date,
        *,
        knowledge_mode: KnowledgeMode,
        known_by: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Select the newest revision that was actually knowable at the cutoff."""

        cutoff = self._utc(known_by) if known_by is not None else None
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        rejected = 0
        for row in self._records_for_date(symbol, session_date):
            key = str(row.get("start_time") or "")
            if not key:
                continue
            if knowledge_mode == "causal_replay":
                try:
                    received = datetime.fromisoformat(str(row["received_at"]))
                except (KeyError, TypeError, ValueError):
                    rejected += 1
                    continue
                if received.tzinfo is None:
                    rejected += 1
                    continue
                if cutoff is not None and received.astimezone(timezone.utc) > cutoff:
                    rejected += 1
                    continue
            grouped[key].append(row)

        self._count(causal_replay_rejection_count=rejected)

        selected = [
            max(revisions, key=self._record_rank)
            for revisions in grouped.values()
            if revisions
        ]
        return sorted(selected, key=lambda row: str(row.get("start_time") or ""))

    def load_market_bars(
        self,
        instrument_id: str,
        *,
        start: datetime,
        end: datetime,
        session: str | None = None,
        knowledge_mode: KnowledgeMode = "live",
        known_by: datetime | None = None,
    ) -> list[MarketBar]:
        start_utc = self._utc(start)
        end_utc = self._utc(end)
        known_cutoff = self._utc(known_by) if known_by is not None else end_utc
        if end_utc <= start_utc:
            return []
        symbol = self._symbol(instrument_id)
        first_date = start_utc.astimezone(_ET).date()
        last_date = (end_utc - timedelta(microseconds=1)).astimezone(_ET).date()
        current = first_date
        output: list[MarketBar] = []
        while current <= last_date:
            for row in self._selected_records_for_date(
                symbol,
                current,
                knowledge_mode=knowledge_mode,
                known_by=known_cutoff,
            ):
                try:
                    bar_start = datetime.fromisoformat(str(row["start_time"]))
                    bar_end = datetime.fromisoformat(str(row["end_time"]))
                    received = datetime.fromisoformat(str(row["received_at"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if bar_start.tzinfo is None or bar_end.tzinfo is None or received.tzinfo is None:
                    continue
                bar_start = bar_start.astimezone(timezone.utc)
                bar_end = bar_end.astimezone(timezone.utc)
                received = received.astimezone(timezone.utc)
                if not start_utc <= bar_start < end_utc:
                    continue
                row_session = str(row.get("session") or "regular")
                if session is not None and row_session != session:
                    continue
                try:
                    output.append(
                        MarketBar(
                            instrument_id=instrument_id,
                            interval="1m",
                            start_time=bar_start,
                            end_time=bar_end,
                            open=Decimal(str(row["open"])),
                            high=Decimal(str(row["high"])),
                            low=Decimal(str(row["low"])),
                            close=Decimal(str(row["close"])),
                            volume=Decimal(str(row.get("volume") or "0")),
                            is_final=True,
                            adjustment_mode=AdjustmentMode.RAW,
                            session=row_session,
                            provider="yahoo",
                            provider_event_id=(
                                str(row.get("provider_event_id"))
                                if row.get("provider_event_id") is not None
                                else None
                            ),
                            received_at=received.astimezone(timezone.utc),
                        )
                    )
                except Exception:
                    logger.debug("suppressed error in %s", "YahooEvidenceStore.load_market_bars", exc_info=True)
                    continue
            current += timedelta(days=1)
        output.sort(key=lambda bar: bar.start_time)
        self._count(loaded_bar_count=len(output))
        return output

    @staticmethod
    def _latest_completed_premarket_clock(evaluation_time: datetime) -> time | None:
        local = evaluation_time.astimezone(_ET)
        opening = datetime.combine(local.date(), _PREMARKET_OPEN, tzinfo=_ET)
        if local < opening + timedelta(minutes=1):
            return None
        latest = (local - timedelta(minutes=1)).replace(second=0, microsecond=0)
        if latest.time() >= _REGULAR_OPEN:
            latest = datetime.combine(local.date(), _REGULAR_OPEN, tzinfo=_ET) - timedelta(minutes=1)
        return latest.time()

    def premarket_relative_volume(
        self,
        symbol_or_instrument: str,
        evaluation_time: datetime,
        *,
        minimum_baseline_sessions: int = 5,
        lookback_calendar_days: int = 60,
        minimum_baseline_coverage_ratio: Decimal = Decimal("0.90"),
        knowledge_mode: KnowledgeMode = "live",
    ) -> YahooRelativeVolumeEvidence:
        if evaluation_time.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware")
        observed = evaluation_time.astimezone(timezone.utc)
        local = observed.astimezone(_ET)
        cutoff = self._latest_completed_premarket_clock(observed)
        symbol = self._symbol(symbol_or_instrument)
        expected = 0
        if cutoff is not None:
            expected = (
                cutoff.hour * 60
                + cutoff.minute
                - (_PREMARKET_OPEN.hour * 60 + _PREMARKET_OPEN.minute)
                + 1
            )

        def session_totals(session_date: date) -> tuple[Decimal, Decimal, int, int]:
            volume = Decimal("0")
            dollar = Decimal("0")
            count = 0
            nonzero = 0
            if cutoff is None:
                return volume, dollar, count, nonzero
            for row in self._selected_records_for_date(
                symbol,
                session_date,
                knowledge_mode=knowledge_mode,
                known_by=observed,
            ):
                if str(row.get("session") or "") != "extended_pre":
                    continue
                try:
                    start = datetime.fromisoformat(str(row["start_time"])).astimezone(_ET)
                    value = Decimal(str(row.get("volume") or "0"))
                    close = Decimal(str(row.get("close") or "0"))
                except Exception:
                    logger.debug("suppressed error in %s", "YahooEvidenceStore.premarket_relative_volume.session_totals", exc_info=True)
                    continue
                if start.timetz().replace(tzinfo=None) > cutoff:
                    continue
                count += 1
                volume += max(Decimal("0"), value)
                if value > 0:
                    nonzero += 1
                if close > 0 and value > 0:
                    dollar += value * close
            return volume, dollar, count, nonzero

        current_volume, current_dollar, current_count, current_nonzero = session_totals(
            local.date()
        )
        historical: list[Decimal] = []
        rejected_baseline_sessions = 0
        cursor = local.date() - timedelta(days=1)
        floor = local.date() - timedelta(days=max(1, lookback_calendar_days))
        while cursor >= floor:
            volume, _, count, _ = session_totals(cursor)
            coverage = (
                Decimal(count) / Decimal(expected)
                if expected > 0
                else Decimal("0")
            )
            if (
                count > 0
                and volume > 0
                and coverage >= minimum_baseline_coverage_ratio
            ):
                historical.append(volume)
            elif count > 0:
                rejected_baseline_sessions += 1
            cursor -= timedelta(days=1)
        historical = historical[:30]
        baseline_count = len(historical)
        baseline = (
            sum(historical, Decimal("0")) / Decimal(baseline_count)
            if baseline_count
            else None
        )
        relative = (
            current_volume / baseline
            if baseline is not None
            and baseline > 0
            and baseline_count >= minimum_baseline_sessions
            else None
        )
        current_coverage = (
            Decimal(current_count) / Decimal(expected)
            if expected > 0
            else None
        )
        if relative is None:
            self._count(rvol_baseline_miss_count=1)
        else:
            self._count(rvol_baseline_hit_count=1)
        return YahooRelativeVolumeEvidence(
            symbol=symbol,
            observed_at=observed,
            cutoff_clock=cutoff.isoformat(timespec="minutes") if cutoff is not None else "",
            current_volume=current_volume,
            current_dollar_volume=current_dollar,
            baseline_mean_volume=baseline,
            baseline_session_count=baseline_count,
            rejected_baseline_session_count=rejected_baseline_sessions,
            baseline_min_coverage_ratio=minimum_baseline_coverage_ratio,
            relative_volume=relative,
            current_bar_count=current_count,
            current_nonzero_bar_count=current_nonzero,
            expected_bar_count=expected,
            coverage_ratio=current_coverage,
        )

    def record_repair(
        self,
        *,
        attempted: bool,
        recovered_bar_count: int,
        unresolved: bool,
    ) -> None:
        self._count(
            repair_attempt_count=int(bool(attempted)),
            repair_success_count=int(recovered_bar_count > 0),
            repaired_bar_count=max(0, int(recovered_bar_count)),
            unresolved_repair_count=int(bool(unresolved)),
        )

    def record_acquisition(
        self,
        *,
        attempted: int = 0,
        succeeded: int = 0,
        failed: int = 0,
        symbols: int = 0,
    ) -> None:
        self._count(
            acquisition_attempt_count=max(0, int(attempted)),
            acquisition_success_count=max(0, int(succeeded)),
            acquisition_failure_count=max(0, int(failed)),
            acquisition_symbol_count=max(0, int(symbols)),
        )

    def record_evaluation_outcome(
        self,
        *,
        repaired: bool,
        unresolved: bool,
        session_date: date | None = None,
        reason: str | None = None,
    ) -> None:
        effective_session_date = session_date or datetime.now(timezone.utc).astimezone(_ET).date()
        self._count(
            evaluation_repaired_count=int(bool(repaired)),
            evaluation_unresolved_count=int(bool(unresolved)),
        )
        self._record_session_evaluation(
            effective_session_date,
            repaired=repaired,
            unresolved=unresolved,
            reason=reason,
        )

    def session_diagnostics(self, session_date: date) -> dict[str, Any]:
        return dict(self._read_session_metrics(session_date))

    def diagnostics(self) -> dict[str, Any]:
        counters = self.backend.counters("yahoo")
        current_session_date = datetime.now(timezone.utc).astimezone(_ET).date()
        return {
            "policy": "yahoo-evidence-recovery-v1",
            "provider": "yahoo",
            "volume_authority": "provider_relative",
            "consolidated_volume_authority": False,
            "execution_authority": False,
            "storage": self.backend.name,
            **{name: max(0, int(counters.get(name, 0))) for name in _COUNTERS},
            "current_session_metrics": self._read_session_metrics(current_session_date),
            "metrics_persistent": self.backend.persistent,
            "session_metrics_persistent": self.backend.persistent,
        }


_default_store: YahooEvidenceStore | None = None
_default_lock = threading.Lock()


def default_yahoo_evidence_store() -> YahooEvidenceStore:
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = YahooEvidenceStore()
    return _default_store


__all__ = [
    "YahooEvidenceStore",
    "YahooRelativeVolumeEvidence",
    "YahooVolumeAuthority",
    "KnowledgeMode",
    "default_yahoo_evidence_store",
]
