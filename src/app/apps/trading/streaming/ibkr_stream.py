"""Live IBKR chart bars from the Gateway's keepUpToDate 1m TRADES stream.

The Gateway first sends recent 1m bars, then re-sends the forming bar as trades
arrive and starts a new one when the minute turns. Each bar becomes the 1m bar
the chart's IBKR history holds (``IbkrEquityProvider.get_bars``), and longer
intraday intervals are rebuilt with the clock aggregation the chart's /bars
request uses, so the live bar continues that history instead of disagreeing
with it. Only the newest bucket and later ones are sent: the chart already
holds older history and can revise only its last bar.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from app.apps.trading.catalog import instrument_by_id
from app.apps.trading.market_session_status import us_equity_rules_apply
from app.apps.trading.models import AdjustmentMode, CanonicalInstrument, MarketBar
from app.apps.trading.providers.bar_semantics import interval_duration
from app.apps.trading.providers.clock_aggregation import aggregate_market_bars_clock
from app.apps.trading.providers.ibkr_runtime import (
    BAR_STREAM_NO_DATA,
    IbkrBarStreamEvent,
    IbkrContractIdentity,
    IbkrHistoricalBar,
)
from app.apps.trading.us_equity_calendar import us_equity_session

from .manager import StreamingBarUpdate

logger = logging.getLogger(__name__)

_MINUTE = timedelta(minutes=1)
_DAY = timedelta(days=1)
# The opening history covers the current bucket of any intraday interval
# (and is the longest window IBKR serves 1m bars for).
HISTORY_SECONDS = 86_400
# With no trades in that window (weekends, holidays) the stream reopens this much later,
# instead of spending IBKR's historical-request pacing every backoff cycle.
NO_DATA_RETRY_SECONDS = 600.0


def ibkr_streams_interval(interval: str) -> bool:
    """Whether a live IBKR stream can serve this chart interval: whole minutes, shorter than a day."""
    try:
        duration = interval_duration(interval)
    except ValueError:
        return False
    return _MINUTE <= duration < _DAY and duration % _MINUTE == timedelta(0)


def _valid(row: IbkrHistoricalBar) -> bool:
    prices = (row.open, row.high, row.low, row.close)
    return (
        all(value.is_finite() and value > 0 for value in prices)
        and row.volume.is_finite()
        and row.volume >= 0
        and row.high >= max(row.open, row.close)
        and row.low <= min(row.open, row.close)
    )


class IbkrLiveBars:
    """Turns stream events into chart bar updates for one instrument and interval."""

    def __init__(
        self,
        *,
        instrument: CanonicalInstrument,
        binding_id: str,
        contract: IbkrContractIdentity,
        interval: str,
        extended_hours: bool,
        clock: Callable[[], datetime],
    ) -> None:
        if not ibkr_streams_interval(interval):
            raise ValueError(f"IBKR live bars do not support interval: {interval}")
        self.instrument = instrument
        self.binding_id = binding_id
        self.contract = contract
        self.interval = interval
        self.duration = interval_duration(interval)
        self.extended_hours = extended_hours
        self.clock = clock
        self.calendar = (
            instrument.session_calendar
            if us_equity_rules_apply(instrument.session_calendar, instrument.venue)
            else "24x7"
        )
        self._bars: dict[datetime, MarketBar] = {}
        self._latest: datetime | None = None
        self._revision = 0

    def apply(self, event: IbkrBarStreamEvent) -> list[StreamingBarUpdate]:
        now = self.clock().astimezone(timezone.utc)
        if event.kind == "history":
            for row in event.bars:
                self._store(row, now)
            return self._updates([self._latest] if self._latest is not None else [])
        if event.kind != "update":
            return []
        changed: list[datetime] = []
        for row in event.bars:
            start = row.start_time.astimezone(timezone.utc)
            if self._latest is not None and start < self._latest:
                continue  # a late revision of a minute that has already closed
            previous = self._latest
            if not self._store(row, now):
                continue
            if previous is not None and start > previous:
                # A new minute started, so IBKR has closed the previous one.
                closed = self._bars.get(previous)
                if closed is not None and not closed.is_final:
                    self._bars[previous] = closed.model_copy(update={"is_final": True, "received_at": now})
                changed.append(previous)
            changed.append(start)
        return self._updates(changed)

    def _store(self, row: IbkrHistoricalBar, now: datetime) -> bool:
        if not _valid(row):
            return False
        start = row.start_time.astimezone(timezone.utc)
        end = start + _MINUTE
        latest = start if self._latest is None else max(self._latest, start)
        self._bars[start] = MarketBar(
            instrument_id=self.instrument.instrument_id,
            interval="1m",
            start_time=start,
            end_time=end,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            # A minute is final once a later one exists or its end has passed.
            is_final=start < latest or end <= now,
            adjustment_mode=AdjustmentMode.RAW,
            session=us_equity_session(start),
            provider="ibkr",
            provider_event_id=f"ibkr:{self.contract.con_id}:{start.isoformat()}",
            received_at=now,
        )
        self._latest = latest
        return True

    def _prune(self) -> None:
        if self._latest is None:
            return
        horizon = self._latest - self.duration - _MINUTE
        for start in [item for item in self._bars if item < horizon]:
            del self._bars[start]

    def _updates(self, starts: list[datetime]) -> list[StreamingBarUpdate]:
        bars: dict[datetime, MarketBar] = {}
        for start in starts:
            bar = self._bucket(start)
            if bar is None:
                continue
            if not bar.is_final and self._latest is not None and bar.end_time <= self._latest:
                # A later minute has started, so IBKR closed this bucket, whatever the local clock says.
                bar = bar.model_copy(update={"is_final": True})
            bars[bar.start_time] = bar
        # Only the newest bucket can change from here on.
        self._prune()
        updates = []
        for bar in sorted(bars.values(), key=lambda item: item.start_time):
            self._revision += 1
            updates.append(
                StreamingBarUpdate(
                    binding_id=self.binding_id,
                    instrument_id=bar.instrument_id,
                    interval=self.interval,
                    start_time=bar.start_time,
                    end_time=bar.end_time,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    is_final=bar.is_final,
                    provider_event_id=bar.provider_event_id,
                    ingestion_revision=self._revision,
                    session=bar.session,
                    extended_hours=self.extended_hours,
                )
            )
        return updates

    def _bucket(self, start: datetime) -> MarketBar | None:
        """The chart bar holding the 1m bar at ``start``."""
        if start not in self._bars:
            return None
        if self.interval == "1m":
            # Like get_bars, the 1m chart carries every session the Gateway sent.
            return self._bars[start]
        window = [
            bar
            for bar in self._bars.values()
            if start - self.duration < bar.start_time < start + self.duration
        ]
        aggregated = aggregate_market_bars_clock(
            window,
            target_interval=self.interval,
            base_interval="1m",
            calendar=self.calendar,
            exchange_tz=self.instrument.exchange_timezone,
            include_extended=self.extended_hours,
            # The window holds every 1m bar of the bucket, so its first bucket is whole.
            history_complete=True,
        )
        return next((bar for bar in aggregated if bar.start_time <= start < bar.end_time), None)


class IbkrBarStream:
    """Opens live IBKR bar streams for the shared bar hub."""

    def __init__(
        self,
        provider_factory: Callable[[], Any],
        *,
        clock: Callable[[], datetime] | None = None,
        idle_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.provider_factory = provider_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.idle_sleep = idle_sleep

    async def messages(
        self,
        *,
        provider_symbol: str,
        binding_id: str,
        instrument_id: str,
        interval: str,
        extended_hours: bool = True,
    ) -> AsyncIterator[StreamingBarUpdate]:
        instrument = instrument_by_id(instrument_id)
        if instrument is None:
            raise ValueError(f"unknown instrument: {instrument_id}")
        provider = self.provider_factory()
        loop = asyncio.get_running_loop()
        events: asyncio.Queue[IbkrBarStreamEvent] = asyncio.Queue()

        def deliver(event: IbkrBarStreamEvent) -> None:
            # Runs on the IBKR network thread.
            try:
                loop.call_soon_threadsafe(events.put_nowait, event)
            except RuntimeError:
                logger.debug("IBKR bar event arrived after its stream's loop closed")

        # Qualifying the contract and opening the request can wait on the Gateway.
        opening = asyncio.ensure_future(
            asyncio.to_thread(provider.subscribe_bars, instrument_id, deliver, duration_seconds=HISTORY_SECONDS)
        )
        try:
            token, contract = await asyncio.shield(opening)
        except asyncio.CancelledError:
            # The thread still finishes opening the request; release it when it does.
            def release(done: asyncio.Future[Any]) -> None:
                if not done.cancelled() and done.exception() is None:
                    provider.unsubscribe_bars(done.result()[0])

            opening.add_done_callback(release)
            raise
        try:
            live = IbkrLiveBars(
                instrument=instrument,
                binding_id=binding_id,
                contract=contract,
                interval=interval,
                extended_hours=extended_hours,
                clock=self.clock,
            )
            while True:
                event = await events.get()
                if event.kind == "closed":
                    if (event.reason or "").startswith(BAR_STREAM_NO_DATA):
                        await self.idle_sleep(NO_DATA_RETRY_SECONDS)
                    raise ConnectionError(event.reason or "IBKR bar stream closed")
                for update in live.apply(event):
                    yield update
        finally:
            provider.unsubscribe_bars(token)


__all__ = ["HISTORY_SECONDS", "IbkrBarStream", "IbkrLiveBars", "ibkr_streams_interval"]
