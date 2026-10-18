from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from threading import Lock
from time import perf_counter
from typing import Any, Literal

from .cache import TradingMarketDataCache
from .execution import ExecutionEligibilityPolicy, ExecutionObservation
from .ibkr_evidence import IbkrEvidenceStore, default_ibkr_evidence_store
from .market_data_recovery import (
    KnowledgeMode,
    RecoveredBars,
    aggregate_complete_bars,
    deduplicate_bars,
    detect_session_gaps,
    reconcile_recovery,
)
from .models import FeedType, MarketBar
from .market_data_window import (
    MarketDataWindow,
    RecoveredWindowBars,
    WindowRecoveryReport,
    detect_window_gaps,
    expected_window_starts,
    finalized_window_bars,
    window_dataset_fingerprint,
)
from .providers.binance import BinanceMarketDataProvider
from .providers.alpaca_iex_status import default_alpaca_iex_status_cache
from .providers.bar_semantics import interval_duration
from .providers.errors import ProviderDataUnavailableError
from .providers.registry import ProviderRegistry
from .streaming.binance_stream import BinanceWebSocketStream
from .streaming.ibkr_stream import IbkrBarStream, ibkr_streams_interval
from .streaming.manager import (
    BarStreamItem,
    SharedBarStreamHub,
    SharedSubscriptionManager,
    StreamingBarUpdate,
)
from .yahoo_evidence import YahooEvidenceStore, default_yahoo_evidence_store
from app.apps.trading.us_equity_calendar import EASTERN
from app.apps.trading.us_equity_calendar import regular_close_time


RECOVERY_STAGES = ("total", "reused", "primary", "yahoo_evidence", "yahoo_exact", "ibkr", "iex")


class RecoveryStageMetrics:
    """Process-wide call counts and seconds per bar-recovery stage (diagnostics only)."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._calls = dict.fromkeys(RECOVERY_STAGES, 0)
        self._seconds = dict.fromkeys(RECOVERY_STAGES, 0.0)

    def add(self, stage: str, seconds: float) -> None:
        with self._lock:
            self._calls[stage] += 1
            self._seconds[stage] += max(0.0, seconds)

    @contextmanager
    def timed(self, stage: str) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            self.add(stage, perf_counter() - started)

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                stage: {"calls": self._calls[stage], "seconds": round(self._seconds[stage], 3)}
                for stage in RECOVERY_STAGES
            }


# Strategies evaluate the same stocks moments apart. Within one clock minute a
# live repaired tape can only gain bars, so a copy that already holds the
# latest expected bar (no unresolved gap reaching it) is shared instead of
# running the provider chain again. Replays and explicit knowledge cutoffs are
# never shared.
TAPE_REUSE_INTERVALS = frozenset({"1m", "5m"})
TAPE_REUSE_MAX_ENTRIES = 512
# A copy fetched this far into the minute that still lacks the latest bar
# reflects a minute without trades rather than a bar still in flight.
TAPE_TAIL_GRACE = timedelta(seconds=20)


def _minute_floor(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _tape_reusable(recovered: RecoveredBars, observed: datetime) -> bool:
    if not recovered.bars or recovered.report.primary_error:
        return False
    step = interval_duration(recovered.report.interval)
    epoch = observed.timestamp()
    latest_end = datetime.fromtimestamp(epoch - epoch % step.total_seconds(), tz=timezone.utc)
    tail_gap = any(gap.end >= latest_end for gap in recovered.report.unresolved_gaps)
    return not tail_gap or observed - _minute_floor(observed) >= TAPE_TAIL_GRACE


IBKR_REPAIR_BLOCK = timedelta(minutes=15)
# Yahoo publishes a minute's bar within moments; one still missing this long
# after its bucket closed, after a successful exact repair, is not coming.
YAHOO_REPAIR_SETTLE = timedelta(minutes=5)
YAHOO_UNREPAIRABLE_MAX_KEYS = 4_096


def _gap_bucket_starts(gap, step: timedelta) -> list[datetime]:
    starts = []
    cursor = gap.start.astimezone(timezone.utc)
    end = gap.end.astimezone(timezone.utc)
    while cursor < end:
        starts.append(cursor)
        cursor += step
    return starts


def _aligned_repair_blocks(starts: set[datetime], *, through: datetime) -> list[tuple[datetime, datetime]]:
    """Clock-aligned blocks covering every missing bucket, never past ``through``.

    Merged gap ranges shift whenever a neighbouring minute goes missing, which
    defeats the IBKR history cache; fixed 15-minute blocks keep the same request
    for a finished block all day. ``through`` is the last completed minute, so
    no request can return the still-forming bar of the current minute.
    """

    block_seconds = IBKR_REPAIR_BLOCK.total_seconds()
    block_starts = sorted({
        datetime.fromtimestamp(start.timestamp() - start.timestamp() % block_seconds, tz=timezone.utc)
        for start in starts
    })
    return [
        (block_start, min(block_start + IBKR_REPAIR_BLOCK, through))
        for block_start in block_starts
        if min(block_start + IBKR_REPAIR_BLOCK, through) > block_start
    ]


def _coalesced_gap_ranges(starts: set[datetime], step):
    ordered = sorted(starts)
    if not ordered:
        return []
    ranges: list[tuple[datetime, datetime]] = []
    start = previous = ordered[0]
    for current in ordered[1:]:
        if current == previous + step:
            previous = current
            continue
        ranges.append((start, previous + step))
        start = previous = current
    ranges.append((start, previous + step))
    return ranges


class TradingMarketDataService:
    def __init__(
        self,
        *,
        provider: BinanceMarketDataProvider | None = None,
        registry: ProviderRegistry | None = None,
        cache: TradingMarketDataCache | None = None,
        subscriptions: SharedSubscriptionManager | None = None,
        stream: BinanceWebSocketStream | None = None,
        ibkr_stream: IbkrBarStream | None = None,
        yahoo_evidence_store: YahooEvidenceStore | None = None,
        ibkr_evidence_store: IbkrEvidenceStore | None = None,
    ) -> None:
        self.cache = cache or TradingMarketDataCache(max_entries=256)
        self.registry = registry or ProviderRegistry(cache=self.cache)
        if provider is not None:
            self.registry._providers["binance"] = provider
        self.provider = self.registry.provider("binance")
        self.subscriptions = subscriptions or SharedSubscriptionManager()
        self.stream = stream or BinanceWebSocketStream()
        self.ibkr_stream = ibkr_stream or IbkrBarStream(lambda: self.registry.provider("ibkr"))
        self.bar_streams = SharedBarStreamHub(
            self.subscriptions,
            open_upstream=self._open_bar_upstream,
            recover_gap=self._recover_stream_gap,
        )
        self.yahoo_evidence_store = yahoo_evidence_store or default_yahoo_evidence_store()
        self.ibkr_evidence_store = ibkr_evidence_store or default_ibkr_evidence_store()
        self.recovery_metrics = RecoveryStageMetrics()
        self._tape_lock = Lock()
        self._tapes: OrderedDict[tuple[Any, ...], tuple[datetime, RecoveredBars]] = OrderedDict()
        self._yahoo_unrepairable: OrderedDict[tuple[str, str, date], set[datetime]] = OrderedDict()

    def bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int = 500,
        binding_id: str | None = None,
        cancellation: threading.Event | None = None,
        *,
        alignment: Literal["count", "clock"] = "count",
        include_extended_hours: bool = True,
    ):
        # Only the chart's /bars request asks for clock alignment (TVP-2.5);
        # strategy and evidence callers keep count-mode aggregation.
        return self.registry.bars(
            instrument_id,
            interval,
            limit,
            binding_id,
            cancellation,
            alignment=alignment,
            include_extended_hours=include_extended_hours,
        )

    def recovered_bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int = 500,
        binding_id: str | None = None,
        *,
        session_date: date,
        as_of: datetime,
        max_primary_attempts: int = 2,
        cancellation: threading.Event | None = None,
        knowledge_mode: KnowledgeMode = "live",
        knowledge_cutoff: datetime | None = None,
    ) -> RecoveredBars:
        """The shared causal tape; a same-minute live copy holding the latest bar is reused."""

        if as_of.tzinfo is None:
            raise ValueError("recovered_bars as_of must be timezone-aware")
        observed = as_of.astimezone(timezone.utc)
        shareable = knowledge_mode == "live" and knowledge_cutoff is None and interval in TAPE_REUSE_INTERVALS
        key = (instrument_id, interval, int(limit), binding_id, session_date, int(max_primary_attempts))
        if shareable:
            with self._tape_lock:
                cached = self._tapes.get(key)
            if cached is not None:
                cached_at, tape = cached
                if cached_at <= observed and _minute_floor(cached_at) == _minute_floor(observed):
                    self.recovery_metrics.add("reused", 0.0)
                    return tape
        with self.recovery_metrics.timed("total"):
            recovered = self._recover_bars(
                instrument_id,
                interval,
                limit,
                binding_id,
                session_date=session_date,
                as_of=as_of,
                max_primary_attempts=max_primary_attempts,
                cancellation=cancellation,
                knowledge_mode=knowledge_mode,
                knowledge_cutoff=knowledge_cutoff,
            )
        if shareable and _tape_reusable(recovered, observed):
            with self._tape_lock:
                self._tapes[key] = (observed, recovered)
                self._tapes.move_to_end(key)
                while len(self._tapes) > TAPE_REUSE_MAX_ENTRIES:
                    self._tapes.popitem(last=False)
        return recovered

    def prefetch_recovered_bars(
        self,
        requests: list[tuple[str, str, int, str | None]],
        *,
        session_date: date,
        as_of: datetime,
        workers: int = 4,
    ) -> None:
        """Warm the shared tape for (instrument, interval, limit, binding) requests, a few at a time.

        Failures are ignored here: the consumer's own call repeats the request and
        records the error exactly as before.
        """

        unique = list(dict.fromkeys(requests))
        if not unique:
            return

        def warm(request: tuple[str, str, int, str | None]) -> None:
            instrument_id, interval, limit, binding_id = request
            try:
                self.recovered_bars(
                    instrument_id, interval, limit, binding_id, session_date=session_date, as_of=as_of
                )
            except Exception:
                return

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(unique))), thread_name_prefix="omnix-tape") as pool:
            list(pool.map(warm, unique))

    def _recover_bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int = 500,
        binding_id: str | None = None,
        *,
        session_date: date,
        as_of: datetime,
        max_primary_attempts: int = 2,
        cancellation: threading.Event | None = None,
        knowledge_mode: KnowledgeMode = "live",
        knowledge_cutoff: datetime | None = None,
    ) -> RecoveredBars:
        """Return the shared causal tape for every strategy consumer.

        Yahoo intraday recovery is canonicalized at 1m before any coarser
        aggregation: ordinary response + durable evidence + exact repair are
        deduplicated into one Yahoo tape, then aggregated once. IBKR exact-range
        repair is observed next and is only applied after its explicit rollout
        gate; IEX remains the final partial-market fallback.
        """

        if as_of.tzinfo is None:
            raise ValueError("recovered_bars as_of must be timezone-aware")
        observed = as_of.astimezone(timezone.utc)
        known_by = (
            knowledge_cutoff.astimezone(timezone.utc)
            if knowledge_cutoff is not None
            else observed
        )
        attempts = max(1, min(int(max_primary_attempts), 2))
        requested = self.registry.resolve_binding(instrument_id, binding_id)
        primary_provider = requested.provider
        primary_response = None
        primary_error: str | None = None
        primary_attempt_count = 0
        yahoo_intraday = bool(
            primary_provider == "yahoo"
            and instrument_id.startswith("equity:")
            and (interval.endswith("m") or interval.endswith("h"))
        )

        primary_bars: list[MarketBar] = []
        yahoo_1m: list[MarketBar] = []
        request_interval = "1m" if yahoo_intraday else interval
        request_limit = (
            min(2_000, max(500, limit * 60))
            if yahoo_intraday and interval != "1m"
            else limit
        )
        for _ in range(attempts):
            primary_attempt_count += 1
            try:
                with self.recovery_metrics.timed("primary"):
                    response = self.registry.bars(
                        instrument_id,
                        request_interval,
                        request_limit,
                        requested.binding_id,
                        cancellation,
                    )
            except Exception as exc:
                primary_error = f"{type(exc).__name__}: {exc}"
                continue
            if yahoo_intraday:
                yahoo_1m.extend(list(response.bars))
                primary_response = response if interval == "1m" else None
                current_yahoo = deduplicate_bars(
                    yahoo_1m,
                    preferred_provider="yahoo",
                )
                try:
                    projected = (
                        current_yahoo
                        if interval == "1m"
                        else aggregate_complete_bars(
                            current_yahoo,
                            session_date=session_date,
                            target_interval=interval,
                            as_of=observed,
                            knowledge_mode=knowledge_mode,
                            knowledge_cutoff=known_by,
                        )
                    )
                except ValueError:
                    projected = []
                if not detect_session_gaps(
                    projected,
                    session_date=session_date,
                    interval=interval,
                    as_of=observed,
                    knowledge_mode=knowledge_mode,
                    knowledge_cutoff=known_by,
                ):
                    break
            else:
                primary_bars.extend(list(response.bars))
                primary_response = response
                if not detect_session_gaps(
                    primary_bars,
                    session_date=session_date,
                    interval=interval,
                    as_of=observed,
                    knowledge_mode=knowledge_mode,
                    knowledge_cutoff=known_by,
                ):
                    break

        yahoo_repair_attempted = False
        yahoo_repaired_count = 0
        if yahoo_intraday:
            et = EASTERN
            session_open = datetime.combine(
                session_date, time(9, 30), tzinfo=et
            ).astimezone(timezone.utc)
            session_close = datetime.combine(
                session_date, regular_close_time(session_date), tzinfo=et
            ).astimezone(timezone.utc)
            bounded_end = min(observed, session_close)
            if bounded_end > session_open:
                with self.recovery_metrics.timed("yahoo_evidence"):
                    yahoo_1m.extend(
                        self.yahoo_evidence_store.load_market_bars(
                            instrument_id,
                            start=session_open,
                            end=bounded_end,
                            session="regular",
                            knowledge_mode=knowledge_mode,
                            known_by=known_by,
                        )
                    )

            def project_yahoo() -> list[MarketBar]:
                canonical_1m = deduplicate_bars(
                    yahoo_1m,
                    preferred_provider="yahoo",
                )
                if interval == "1m":
                    return canonical_1m
                return aggregate_complete_bars(
                    canonical_1m,
                    session_date=session_date,
                    target_interval=interval,
                    as_of=observed,
                    knowledge_mode=knowledge_mode,
                    knowledge_cutoff=known_by,
                )

            try:
                primary_bars = project_yahoo()
            except ValueError as exc:
                primary_error = (
                    f"{primary_error}; yahoo_1m_aggregation={exc}"
                    if primary_error
                    else f"yahoo_1m_aggregation={exc}"
                )
                primary_bars = []

            before_gaps = detect_session_gaps(
                primary_bars,
                session_date=session_date,
                interval=interval,
                as_of=observed,
                knowledge_mode=knowledge_mode,
                knowledge_cutoff=known_by,
            )
            before_missing = sum(gap.missing_bar_count for gap in before_gaps)

            # A fresh request observed now cannot repair a historical causal
            # replay decision. Preserve that as explicit retroactive research.
            unrepairable_key = (instrument_id, interval, session_date)
            repair_bucket = interval_duration(interval)
            repair_window: tuple[datetime, datetime] | None = None
            # Buckets an earlier repair proved Yahoo will not fill (a halt or a
            # minute without trades) are not asked for again every cycle.
            with self._tape_lock:
                unrepairable = set(self._yahoo_unrepairable.get(unrepairable_key, ()))
            open_gaps = [
                gap
                for gap in before_gaps
                if not set(_gap_bucket_starts(gap, repair_bucket)) <= unrepairable
            ]
            if open_gaps and knowledge_mode != "causal_replay":
                yahoo_provider = self.registry.provider("yahoo")
                repair = getattr(yahoo_provider, "get_intraday_bars_range", None)
                if callable(repair):
                    yahoo_repair_attempted = True
                    repair_start = min(gap.start for gap in open_gaps)
                    repair_end = max(gap.end for gap in open_gaps)
                    try:
                        with self.recovery_metrics.timed("yahoo_exact"):
                            exact = repair(
                                instrument_id,
                                start=repair_start,
                                end=repair_end,
                                include_extended_hours=False,
                                cancellation=cancellation,
                            )
                        yahoo_1m.extend(list(exact.bars))
                        primary_bars = project_yahoo()
                        repair_window = (repair_start, repair_end)
                    except Exception as exc:
                        detail = f"{type(exc).__name__}: {exc}"
                        primary_error = (
                            f"{primary_error}; yahoo_exact_repair={detail}"
                            if primary_error
                            else f"yahoo_exact_repair={detail}"
                        )

            after_yahoo = detect_session_gaps(
                primary_bars,
                session_date=session_date,
                interval=interval,
                as_of=observed,
                knowledge_mode=knowledge_mode,
                knowledge_cutoff=known_by,
            )
            after_missing = sum(gap.missing_bar_count for gap in after_yahoo)
            yahoo_repaired_count = max(0, before_missing - after_missing)
            if repair_window is not None:
                # Only a successful answer for a bucket closed long enough ago is final.
                settled_by = observed - YAHOO_REPAIR_SETTLE
                proven = {
                    start
                    for gap in after_yahoo
                    for start in _gap_bucket_starts(gap, repair_bucket)
                    if repair_window[0] <= start < repair_window[1]
                    and start + repair_bucket <= settled_by
                }
                if proven:
                    with self._tape_lock:
                        self._yahoo_unrepairable.setdefault(unrepairable_key, set()).update(proven)
                        self._yahoo_unrepairable.move_to_end(unrepairable_key)
                        while len(self._yahoo_unrepairable) > YAHOO_UNREPAIRABLE_MAX_KEYS:
                            self._yahoo_unrepairable.popitem(last=False)

        primary_gaps = detect_session_gaps(
            primary_bars,
            session_date=session_date,
            interval=interval,
            as_of=observed,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=known_by,
        )

        # Missing bars can be classified as confirmed halt/no-trade only by an
        # independent status authority. Unknown market state remains unresolved.
        confirmed_nontrading: list[datetime] = []
        unresolved_market_state: list[datetime] = []
        if instrument_id.startswith("equity:") and primary_gaps:
            symbol = instrument_id.rsplit(":", 1)[-1]
            status_cache = default_alpaca_iex_status_cache()
            duration = interval_duration(interval)
            for gap in primary_gaps:
                cursor = gap.start
                while cursor < gap.end:
                    if status_cache.confirmed_halt_interval(
                        symbol,
                        start=cursor,
                        end=cursor + duration,
                    ):
                        confirmed_nontrading.append(cursor)
                    else:
                        unresolved_market_state.append(cursor)
                    cursor += duration

        fallback_attempted = False
        fallback_provider: str | None = None
        fallback_bars: list[MarketBar] = []
        fallback_error: str | None = None
        fallback_binding_id: str | None = None
        recovery_source_order: list[str] = []
        ibkr_shadow_repair_attempted = False
        ibkr_shadow_recoverable_count = 0
        ibkr_recovery_enabled = False
        ibkr_candidates: list[MarketBar] = []
        ibkr_selected: list[MarketBar] = []

        unconfirmed_gap_starts = set(unresolved_market_state)
        if primary_gaps and not instrument_id.startswith("equity:"):
            duration = interval_duration(interval)
            for gap in primary_gaps:
                cursor = gap.start
                while cursor < gap.end:
                    unconfirmed_gap_starts.add(cursor)
                    cursor += duration

        # IBKR is deliberately observed before it is allowed to alter strategy
        # evidence. Fresh network repair is also forbidden during causal replay.
        if (
            primary_gaps
            and unconfirmed_gap_starts
            and instrument_id.startswith("equity:")
            and knowledge_mode != "causal_replay"
        ):
            try:
                ibkr_provider = self.registry.provider("ibkr")
                ibkr_runtime = getattr(ibkr_provider, "runtime", None)
                if ibkr_runtime is not None and getattr(ibkr_runtime, "enabled", False):
                    ibkr_shadow_repair_attempted = True
                    repair_blocks = _aligned_repair_blocks(
                        unconfirmed_gap_starts,
                        through=_minute_floor(observed),
                    )
                    ibkr_one_minute: list[MarketBar] = []
                    for repair_start, repair_end in repair_blocks:
                        try:
                            with self.recovery_metrics.timed("ibkr"):
                                exact = ibkr_provider.get_intraday_bars_range(
                                    instrument_id,
                                    start=repair_start,
                                    end=repair_end,
                                    include_extended_hours=False,
                                    cancellation=cancellation,
                                )
                        except ProviderDataUnavailableError:
                            continue  # IBKR has no trades in this block either; try the next one
                        ibkr_one_minute.extend(list(exact.bars))
                    if interval == "1m":
                        ibkr_candidates = deduplicate_bars(
                            ibkr_one_minute,
                            preferred_provider="ibkr",
                        )
                    else:
                        ibkr_candidates = aggregate_complete_bars(
                            ibkr_one_minute,
                            session_date=session_date,
                            target_interval=interval,
                            as_of=observed,
                            knowledge_mode=knowledge_mode,
                            knowledge_cutoff=known_by,
                        )
                    candidate_starts = {
                        bar.start_time.astimezone(timezone.utc)
                        for bar in ibkr_candidates
                    }
                    ibkr_shadow_recoverable_count = len(
                        candidate_starts & unconfirmed_gap_starts
                    )
                    ibkr_recovery_enabled = bool(
                        getattr(ibkr_runtime, "recovery_authority_enabled", False)
                    )
                    if ibkr_recovery_enabled:
                        ibkr_selected = [
                            bar
                            for bar in ibkr_candidates
                            if bar.start_time.astimezone(timezone.utc)
                            in unconfirmed_gap_starts
                        ]
                        fallback_bars.extend(ibkr_selected)
                        if ibkr_selected:
                            recovery_source_order.append("ibkr")
                            fallback_attempted = True
            except Exception as exc:
                detail = f"ibkr_exact_repair={type(exc).__name__}: {exc}"
                fallback_error = detail

        already_recovered = {
            bar.start_time.astimezone(timezone.utc)
            for bar in fallback_bars
        }
        remaining_gap_starts = unconfirmed_gap_starts - already_recovered

        # Alpaca IEX remains an independent partial-market fallback. It only
        # supplies buckets IBKR did not already fill when IBKR recovery authority
        # is enabled; in observation mode current behavior is unchanged.
        if primary_gaps and remaining_gap_starts:
            try:
                execution_binding = self.registry.resolve_execution_binding(
                    instrument_id,
                    requested.binding_id,
                )
                if execution_binding.provider != primary_provider:
                    with self.recovery_metrics.timed("iex"):
                        one_minute = self.registry.execution_indicator_bars(
                            instrument_id,
                            requested.binding_id,
                            as_of=observed,
                            cancellation=cancellation,
                        )
                    if interval == "1m":
                        iex_candidates = list(one_minute)
                    else:
                        iex_candidates = aggregate_complete_bars(
                            one_minute,
                            session_date=session_date,
                            target_interval=interval,
                            as_of=observed,
                            knowledge_mode=knowledge_mode,
                            knowledge_cutoff=known_by,
                        )
                    selected_iex = [
                        bar
                        for bar in iex_candidates
                        if bar.start_time.astimezone(timezone.utc)
                        in remaining_gap_starts
                    ]
                    fallback_bars.extend(selected_iex)
                    if selected_iex:
                        recovery_source_order.append(execution_binding.provider)
                        fallback_attempted = True
                        if not ibkr_selected:
                            fallback_binding_id = execution_binding.binding_id
            except Exception as exc:
                detail = f"alpaca_iex_repair={type(exc).__name__}: {exc}"
                fallback_error = (
                    f"{fallback_error}; {detail}"
                    if fallback_error
                    else detail
                )

        if recovery_source_order:
            fallback_provider = "_then_".join(recovery_source_order)
            if len(recovery_source_order) > 1:
                fallback_binding_id = (
                    "recovery:" + "+".join(recovery_source_order) + ":" + instrument_id
                )
            elif recovery_source_order == ["ibkr"]:
                try:
                    fallback_binding_id = self.registry.provider("ibkr").get_binding(
                        instrument_id
                    ).binding_id
                except Exception:
                    fallback_binding_id = requested.binding_id

        recovered = reconcile_recovery(
            instrument_id=instrument_id,
            interval=interval,
            session_date=session_date,
            as_of=observed,
            primary_bars=primary_bars,
            fallback_bars=fallback_bars,
            primary_provider=primary_provider,
            fallback_provider=fallback_provider,
            requested_binding=requested.binding_id,
            resolved_binding=(
                fallback_binding_id
                if fallback_attempted and fallback_bars
                else requested.binding_id
            ),
            primary_attempt_count=primary_attempt_count,
            fallback_attempted=fallback_attempted,
            primary_error=primary_error,
            fallback_error=fallback_error,
            partial_market_fallback=any(
                bar.provider == "alpaca_iex" for bar in fallback_bars
            ),
            primary_response=primary_response,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=known_by,
            confirmed_nontrading_starts=confirmed_nontrading,
            unresolved_market_state_starts=unresolved_market_state,
        )
        recovered_starts_set = set(recovered.report.recovered_starts)
        ibkr_applied_count = len(
            {
                bar.start_time.astimezone(timezone.utc)
                for bar in ibkr_selected
            }
            & recovered_starts_set
        )
        updated_report = recovered.report.model_copy(
            update={
                "ibkr_shadow_repair_attempted": ibkr_shadow_repair_attempted,
                "ibkr_shadow_recoverable_count": ibkr_shadow_recoverable_count,
                "ibkr_repair_applied_count": ibkr_applied_count,
                "recovery_source_order": tuple(recovery_source_order),
            }
        )
        recovered = RecoveredBars(
            recovered.bars,
            updated_report,
            recovered.primary_response,
            recovered.bucket_evidence,
        )
        self.ibkr_evidence_store.record_recovery(
            session_date,
            attempted=ibkr_shadow_repair_attempted,
            recoverable_count=ibkr_shadow_recoverable_count,
            applied_count=ibkr_applied_count,
            before_unresolved_count=len(unconfirmed_gap_starts),
            after_unresolved_count=max(
                0,
                len(unconfirmed_gap_starts) - ibkr_applied_count,
            ),
        )

        if primary_provider == "yahoo":
            unresolved_effective = [
                gap
                for gap in recovered.report.unresolved_gaps
                if gap.start not in set(confirmed_nontrading)
            ]
            self.yahoo_evidence_store.record_repair(
                attempted=yahoo_repair_attempted,
                recovered_bar_count=yahoo_repaired_count,
                unresolved=bool(unresolved_effective),
            )
        return recovered

    def recovered_window_bars(
        self,
        instrument_id: str,
        *,
        start: datetime,
        end: datetime,
        interval: str = "1m",
        session: str = "extended_pre",
        provider: str = "yahoo",
        include_extended_hours: bool = True,
        knowledge_mode: KnowledgeMode = "live",
        knowledge_cutoff: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> RecoveredWindowBars:
        """Recover one explicit intraday window without regular-session assumptions.

        This path is primarily for prospective premarket evidence. It first uses
        durable provider evidence already observed, then performs one bounded exact
        fetch when live/retroactive knowledge permits. Causal replay never performs
        a fresh network repair.
        """

        if provider != "yahoo":
            raise ValueError("window recovery currently supports yahoo authority only")
        if interval != "1m":
            raise ValueError("window recovery currently requires canonical 1m bars")
        sparse_event_window = session in {"extended_pre", "extended_post"}
        window = MarketDataWindow(
            start=start,
            end=end,
            interval=interval,
            session=session,  # type: ignore[arg-type]
            include_extended_hours=include_extended_hours,
            continuity="sparse_event" if sparse_event_window else "bucket_complete",
        )
        known_by = (
            knowledge_cutoff.astimezone(timezone.utc)
            if knowledge_cutoff is not None
            else window.end
        )
        durable = self.yahoo_evidence_store.load_market_bars(
            instrument_id,
            start=window.start,
            end=window.end,
            session=None if session == "custom" else session,
            knowledge_mode=knowledge_mode,
            known_by=known_by,
        )
        durable_filtered = finalized_window_bars(
            durable,
            window=window,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=known_by,
        )
        gaps_before = detect_window_gaps(
            durable_filtered,
            window=window,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=known_by,
        )

        fetched: list[MarketBar] = []
        primary_response = None
        provider_error: str | None = None
        repair_attempted = False
        persisted = 0
        should_refresh = (
            knowledge_mode != "causal_replay"
            and (sparse_event_window or bool(gaps_before))
        )
        if should_refresh:
            yahoo_provider = self.registry.provider("yahoo")
            repair = getattr(yahoo_provider, "get_intraday_bars_range", None)
            if callable(repair):
                repair_attempted = True
                try:
                    if sparse_event_window:
                        repair_start, repair_end = window.start, window.end
                    else:
                        repair_start = min(gap.start for gap in gaps_before)
                        repair_end = max(gap.end for gap in gaps_before)
                    primary_response = repair(
                        instrument_id,
                        start=repair_start,
                        end=repair_end,
                        include_extended_hours=include_extended_hours,
                        cancellation=cancellation,
                    )
                    fetched = list(getattr(primary_response, "bars", ()) or ())
                    if fetched:
                        persisted = self.yahoo_evidence_store.persist_market_bars(fetched)
                except Exception as exc:
                    provider_error = f"{type(exc).__name__}: {exc}"

        canonical = finalized_window_bars(
            [*durable_filtered, *fetched],
            window=window,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=known_by,
        )
        unresolved = detect_window_gaps(
            canonical,
            window=window,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=known_by,
        )
        if sparse_event_window:
            expected = 0
            sparse_verified = bool(canonical) and (
                knowledge_mode == "causal_replay"
                or (repair_attempted and provider_error is None)
            )
            coverage = 1.0 if sparse_verified else (0.5 if canonical else 0.0)
        else:
            expected = len(expected_window_starts(window))
            coverage = (len(canonical) / expected) if expected else 1.0
        latest_bar_lag_seconds = (
            max(0, int((window.end - canonical[-1].end_time).total_seconds()))
            if canonical
            else None
        )
        late_window_bar_count = sum(
            1
            for bar in canonical
            if bar.end_time > window.end - timedelta(minutes=15)
        )
        report = WindowRecoveryReport(
            provider=provider,
            instrument_id=instrument_id,
            window=window,
            knowledge_mode=knowledge_mode,
            knowledge_cutoff=known_by,
            durable_bar_count=len(durable_filtered),
            fetched_bar_count=len(fetched),
            canonical_bar_count=len(canonical),
            expected_bar_count=expected,
            coverage_ratio=min(1.0, max(0.0, coverage)),
            latest_bar_lag_seconds=latest_bar_lag_seconds,
            late_window_bar_count=late_window_bar_count,
            repair_attempted=repair_attempted,
            persisted_repair_bar_count=persisted,
            unresolved_gaps=unresolved,
            provider_error=provider_error,
            dataset_fingerprint=window_dataset_fingerprint(canonical, window=window),
        )
        return RecoveredWindowBars(tuple(canonical), report, primary_response)

    def yahoo_relative_volume(
        self,
        instrument_id: str,
        evaluation_time: datetime,
        *,
        minimum_baseline_sessions: int = 5,
    ):
        return self.yahoo_evidence_store.premarket_relative_volume(
            instrument_id,
            evaluation_time,
            minimum_baseline_sessions=minimum_baseline_sessions,
        )

    def quote(
        self,
        instrument_id: str,
        binding_id: str | None = None,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        return self.registry.quote(instrument_id, binding_id, cancellation)

    def execution_observation(
        self,
        instrument_id: str,
        binding_id: str | None = None,
        *,
        policy: ExecutionEligibilityPolicy | None = None,
        cancellation: threading.Event | None = None,
    ) -> ExecutionObservation:
        return self.registry.execution_observation(
            instrument_id,
            binding_id,
            policy=policy,
            cancellation=cancellation,
        )

    def execution_indicator_bars(
        self,
        instrument_id: str,
        binding_id: str | None = None,
        *,
        as_of: datetime,
        cancellation: threading.Event | None = None,
    ) -> list[MarketBar]:
        return self.registry.execution_indicator_bars(
            instrument_id,
            binding_id,
            as_of=as_of,
            cancellation=cancellation,
        )

    def currency_rate(
        self,
        base_currency: str,
        quote_currency: str,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        return self.registry.currency_rate(base_currency, quote_currency, cancellation)

    async def stream_updates(
        self,
        instrument_id: str,
        interval: str,
        binding_id: str | None = None,
        *,
        extended_hours: bool | None = None,
    ) -> AsyncIterator[BarStreamItem]:
        binding = self.registry.resolve_binding(instrument_id, binding_id)
        if binding.provider == "binance" and binding.feed_type is FeedType.WEBSOCKET_AND_REST:
            scope = None  # 24x7: extended hours do not apply
        elif binding.provider == "ibkr" and ibkr_streams_interval(interval):
            scope = True if extended_hours is None else extended_hours
        else:
            raise ValueError(
                f"binding does not support Omnix live streaming: {binding.binding_id} {interval}"
            )
        async with self.bar_streams.listen(
            provider_symbol=binding.provider_symbol,
            binding_id=binding.binding_id,
            instrument_id=instrument_id,
            interval=interval,
            extended_hours=scope,
        ) as updates:
            while True:
                yield await updates.get()

    def _open_bar_upstream(
        self,
        *,
        provider_symbol: str,
        binding_id: str,
        instrument_id: str,
        interval: str,
        extended_hours: bool | None = None,
    ) -> AsyncIterator[StreamingBarUpdate]:
        binding = self.registry.resolve_binding(instrument_id, binding_id)
        if binding.provider == "ibkr":
            return self.ibkr_stream.messages(
                provider_symbol=provider_symbol,
                binding_id=binding_id,
                instrument_id=instrument_id,
                interval=interval,
                extended_hours=True if extended_hours is None else extended_hours,
            )
        return self.stream.messages(
            provider_symbol=provider_symbol,
            binding_id=binding_id,
            instrument_id=instrument_id,
            interval=interval,
        )

    async def _recover_stream_gap(
        self,
        binding_id: str,
        instrument_id: str,
        interval: str,
        start: datetime,
        end: datetime,
        *,
        extended_hours: bool = True,
    ) -> list[StreamingBarUpdate]:
        """Finalized bars that closed while the upstream was down, from REST.

        They are fetched the way the chart fetches its history (clock-aligned),
        so a recovered derived bar has the chart's bucket edges.
        """
        missing = int((end - start) / interval_duration(interval)) + 2
        response = await asyncio.to_thread(
            lambda: self.bars(
                instrument_id,
                interval,
                min(1_000, missing),
                binding_id,
                alignment="clock",
                include_extended_hours=extended_hours,
            )
        )
        return [
            StreamingBarUpdate(
                binding_id=binding_id,
                instrument_id=instrument_id,
                interval=interval,
                start_time=bar.start_time,
                end_time=bar.end_time,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                is_final=True,
                provider_event_id=bar.provider_event_id,
                provider_sequence=bar.provider_sequence,
                ingestion_revision=bar.ingestion_revision,
                session=bar.session,
            )
            for bar in response.bars
            if bar.is_final and start <= bar.start_time < end
        ]

    def provider_descriptors(self) -> list[dict[str, Any]]:
        return self.registry.descriptors()

    def diagnostics(self) -> dict[str, Any]:
        providers = self.provider_descriptors()
        return {
            "providers": [
                {
                    "provider": item["provider"],
                    "status": item["status"],
                    "binding_count": len(item["bindings"]),
                    "official": item["policy"].is_official_api,
                    "usage_scope": item["policy"].usage_scope,
                    "runtime": item.get("runtime", {}),
                }
                for item in providers
            ],
            "cache": {
                "authority": False,
                "disposable": True,
                "directory": None,
                "max_entries": self.cache.max_entries,
                "disk_bounded": True,
                "atomic_writes": True,
            },
            "execution": {
                "policy_version": ExecutionEligibilityPolicy().policy_version,
                "fail_closed": True,
                "synthetic_reference_prices_allowed": False,
            },
            "yahoo_hardening": self.yahoo_evidence_store.diagnostics(),
            "bar_recovery": {
                "stages": self.recovery_metrics.snapshot(),
                "shared_tapes": len(self._tapes),
            },
            "streams": self.subscriptions.status(),
            "upstream_subscription_count": self.subscriptions.upstream_subscription_count,
            "shared_upstream_streams": self.bar_streams.upstream_count(),
        }


_default_service: TradingMarketDataService | None = None
_default_lock = Lock()


def default_market_data_service() -> TradingMarketDataService:
    global _default_service
    if _default_service is None:
        with _default_lock:
            if _default_service is None:
                _default_service = TradingMarketDataService()
    return _default_service
