from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast

from .gap_recovery import recovery_window

logger = logging.getLogger(__name__)


StreamKind = Literal["BAR", "QUOTE"]


@dataclass(frozen=True, slots=True)
class StreamingBarUpdate:
    binding_id: str
    instrument_id: str
    interval: str
    start_time: datetime
    end_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_final: bool
    provider_event_id: str | None = None
    provider_sequence: int | None = None
    ingestion_revision: int = 1


@dataclass(frozen=True, slots=True)
class StreamingQuoteUpdate:
    binding_id: str
    instrument_id: str
    provider: str
    source_time: datetime
    received_at: datetime
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    last: Decimal | None = None
    last_size: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    cumulative_volume: Decimal | None = None
    market_data_type: str = "UNKNOWN"
    live_entitled: bool | None = None
    contract_id: str | None = None
    primary_exchange: str | None = None
    local_symbol: str | None = None
    provider_sequence: int | None = None


StreamingUpdate = StreamingBarUpdate | StreamingQuoteUpdate


@dataclass(slots=True)
class SharedSubscription:
    key: str
    stream_kind: StreamKind = "BAR"
    binding_id: str = ""
    instrument_id: str = ""
    interval: str | None = None
    listeners: dict[str, Callable[[StreamingUpdate], None]] = field(default_factory=dict)
    connected: bool = False
    reconnects: int = 0
    last_event_at: datetime | None = None


class SharedSubscriptionManager:
    """Own one upstream logical stream for each unique provider demand.

    Historical bar callers retain the original BAR key shape for compatibility.
    Quote subscriptions use a distinct QUOTE key and are deduplicated across all
    strategy consumers of the same binding/instrument.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, SharedSubscription] = {}
        self._lock = threading.RLock()

    @staticmethod
    def key(
        binding_id: str,
        instrument_id: str,
        interval: str | None,
        *,
        stream_kind: StreamKind = "BAR",
    ) -> str:
        if stream_kind == "QUOTE":
            return f"QUOTE|{binding_id}|{instrument_id}"
        return f"{binding_id}|{instrument_id}|{interval}"

    def _subscribe(
        self,
        *,
        listener_id: str,
        binding_id: str,
        instrument_id: str,
        interval: str | None,
        stream_kind: StreamKind,
        listener: Callable[[StreamingUpdate], None],
    ) -> tuple[str, bool]:
        key = self.key(
            binding_id,
            instrument_id,
            interval,
            stream_kind=stream_kind,
        )
        with self._lock:
            created = key not in self._subscriptions
            subscription = self._subscriptions.setdefault(
                key,
                SharedSubscription(
                    key=key,
                    stream_kind=stream_kind,
                    binding_id=binding_id,
                    instrument_id=instrument_id,
                    interval=interval,
                ),
            )
            subscription.listeners[listener_id] = listener
            return key, created

    def subscribe(
        self,
        *,
        listener_id: str,
        binding_id: str,
        instrument_id: str,
        interval: str,
        listener: Callable[[StreamingBarUpdate], None],
    ) -> tuple[str, bool]:
        return self._subscribe(
            listener_id=listener_id,
            binding_id=binding_id,
            instrument_id=instrument_id,
            interval=interval,
            stream_kind="BAR",
            listener=cast(Callable[[StreamingUpdate], None], listener),
        )

    def subscribe_quote(
        self,
        *,
        listener_id: str,
        binding_id: str,
        instrument_id: str,
        listener: Callable[[StreamingQuoteUpdate], None],
    ) -> tuple[str, bool]:
        return self._subscribe(
            listener_id=listener_id,
            binding_id=binding_id,
            instrument_id=instrument_id,
            interval=None,
            stream_kind="QUOTE",
            listener=cast(Callable[[StreamingUpdate], None], listener),
        )

    def unsubscribe(self, key: str, listener_id: str) -> bool:
        """Remove a listener and return True when the upstream can be closed."""
        with self._lock:
            subscription = self._subscriptions.get(key)
            if subscription is None:
                return False
            subscription.listeners.pop(listener_id, None)
            if subscription.listeners:
                return False
            self._subscriptions.pop(key, None)
            return True

    def publish(self, update: StreamingBarUpdate) -> int:
        key = self.key(
            update.binding_id,
            update.instrument_id,
            update.interval,
            stream_kind="BAR",
        )
        return self._publish(key, update, update.end_time)

    def publish_quote(self, update: StreamingQuoteUpdate) -> int:
        key = self.key(
            update.binding_id,
            update.instrument_id,
            None,
            stream_kind="QUOTE",
        )
        return self._publish(key, update, update.received_at)

    def _publish(self, key: str, update: StreamingUpdate, observed_at: datetime) -> int:
        with self._lock:
            subscription = self._subscriptions.get(key)
            listeners = tuple(subscription.listeners.values()) if subscription else ()
            if subscription:
                subscription.last_event_at = observed_at
        for listener in listeners:
            listener(update)
        return len(listeners)

    def mark_connected(self, key: str) -> None:
        with self._lock:
            subscription = self._subscriptions.get(key)
            if subscription is not None:
                subscription.connected = True

    def mark_disconnected(self, key: str) -> None:
        with self._lock:
            subscription = self._subscriptions.get(key)
            if subscription is not None:
                subscription.connected = False
                subscription.reconnects += 1

    def active_keys(self, *, stream_kind: StreamKind | None = None) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                item.key
                for item in self._subscriptions.values()
                if stream_kind is None or item.stream_kind == stream_kind
            )

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "key": item.key,
                    "stream_kind": item.stream_kind,
                    "binding_id": item.binding_id,
                    "instrument_id": item.instrument_id,
                    "interval": item.interval,
                    "listeners": len(item.listeners),
                    "connected": item.connected,
                    "reconnects": item.reconnects,
                    "last_event_at": item.last_event_at.isoformat() if item.last_event_at else None,
                }
                for item in self._subscriptions.values()
            ]

    @property
    def upstream_subscription_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)


UpstreamFactory = Callable[..., AsyncIterator[StreamingBarUpdate]]
# (binding_id, instrument_id, interval, start, end) -> finalized bars in [start, end).
GapRecovery = Callable[[str, str, str, datetime, datetime], Awaitable[list[StreamingBarUpdate]]]


class SharedBarStreamHub:
    """One upstream socket per binding and interval, shared by every browser client (WP-8.3).

    The first client opens the upstream; later clients join it and the last
    one to leave closes it. A dropped upstream reconnects with backoff, and the
    finalized bars that closed while it was down are fetched over REST and
    published before the live stream resumes. A slow client loses its oldest
    queued updates instead of holding the others back.
    """

    def __init__(
        self,
        subscriptions: SharedSubscriptionManager,
        *,
        open_upstream: UpstreamFactory,
        recover_gap: GapRecovery,
        queue_size: int = 256,
        max_backoff_seconds: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.subscriptions = subscriptions
        self.open_upstream = open_upstream
        self.recover_gap = recover_gap
        self.queue_size = queue_size
        self.max_backoff_seconds = max_backoff_seconds
        self.sleep = sleep
        self._upstreams: dict[str, asyncio.Task[None]] = {}

    @asynccontextmanager
    async def listen(
        self,
        *,
        provider_symbol: str,
        binding_id: str,
        instrument_id: str,
        interval: str,
    ) -> AsyncIterator[asyncio.Queue[StreamingBarUpdate]]:
        queue: asyncio.Queue[StreamingBarUpdate] = asyncio.Queue(maxsize=self.queue_size)

        def offer(update: StreamingBarUpdate) -> None:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(update)

        listener_id = uuid.uuid4().hex
        key, _created = self.subscriptions.subscribe(
            listener_id=listener_id,
            binding_id=binding_id,
            instrument_id=instrument_id,
            interval=interval,
            listener=offer,
        )
        upstream = self._upstreams.get(key)
        if upstream is None or upstream.done():
            self._upstreams[key] = asyncio.create_task(
                self._run_upstream(key, provider_symbol, binding_id, instrument_id, interval)
            )
        try:
            yield queue
        finally:
            if self.subscriptions.unsubscribe(key, listener_id):
                task = self._upstreams.pop(key, None)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    def upstream_count(self) -> int:
        return sum(1 for task in self._upstreams.values() if not task.done())

    async def _run_upstream(
        self,
        key: str,
        provider_symbol: str,
        binding_id: str,
        instrument_id: str,
        interval: str,
    ) -> None:
        backoff = 1.0
        last_final_start: datetime | None = None
        while True:
            try:
                first = True
                async for update in self.open_upstream(
                    provider_symbol=provider_symbol,
                    binding_id=binding_id,
                    instrument_id=instrument_id,
                    interval=interval,
                ):
                    if first:
                        first = False
                        backoff = 1.0
                        self.subscriptions.mark_connected(key)
                        window = recovery_window(last_final_start, update.start_time, interval=interval)
                        if window is not None:
                            for bar in await self.recover_gap(binding_id, instrument_id, interval, *window):
                                self.subscriptions.publish(bar)
                                last_final_start = bar.start_time
                    self.subscriptions.publish(update)
                    if update.is_final and (last_final_start is None or update.start_time > last_final_start):
                        last_final_start = update.start_time
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("market stream %s dropped: %s: %s", key, type(exc).__name__, exc)
            self.subscriptions.mark_disconnected(key)
            await self.sleep(backoff)
            backoff = min(self.max_backoff_seconds, backoff * 2)


__all__ = [
    "SharedBarStreamHub",
    "SharedSubscriptionManager",
    "StreamKind",
    "StreamingBarUpdate",
    "StreamingQuoteUpdate",
    "StreamingUpdate",
]
