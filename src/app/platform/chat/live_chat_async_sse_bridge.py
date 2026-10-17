"""Bridge blocking chat iterators into eagerly produced async SSE streams."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from app.observability.tts_stream_diagnostics import stream_log

_DEFAULT_QUEUE_ITEMS = 32
_MAX_QUEUE_ITEMS = 256
_DIAGNOSTIC_ITEM_LIMIT = 4


@dataclass(frozen=True)
class _BridgeItem:
    kind: str
    payload: Any = None


class _EagerAsyncSseBridge:
    """Run one blocking iterator on a dedicated thread with bounded backpressure."""

    def __init__(
        self,
        factory: Callable[[], Iterable[Any]],
        *,
        queue_items: int,
        diagnostic_context: dict[str, Any] | None,
    ) -> None:
        self._factory = factory
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[_BridgeItem] = asyncio.Queue(maxsize=queue_items)
        self._cancelled = threading.Event()
        self._future_lock = threading.Lock()
        self._pending_put: concurrent.futures.Future[Any] | None = None
        self._started = time.perf_counter()
        self._consumer_attached = False
        self._first_item_enqueued = False
        self._diagnostic_context = dict(diagnostic_context or {})
        self._thread = threading.Thread(
            target=self._run,
            name="omnix-live-chat-accepted-stream",
            daemon=True,
        )
        stream_log(
            "gateway-live-chat-async-sse",
            "runtime",
            "live_chat_async_sse_producer_started",
            queue_items=queue_items,
            producer_thread_name=self._thread.name,
            **self._diagnostic_context,
        )
        self._thread.start()

    def _enqueue(self, item: _BridgeItem) -> bool:
        if self._cancelled.is_set():
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(self._queue.put(item), self._loop)
        except RuntimeError:
            return False
        with self._future_lock:
            self._pending_put = future
        try:
            while not self._cancelled.is_set():
                try:
                    future.result(timeout=0.1)
                    return True
                except concurrent.futures.TimeoutError:
                    continue
                except (concurrent.futures.CancelledError, RuntimeError):
                    return False
            future.cancel()
            return False
        finally:
            with self._future_lock:
                if self._pending_put is future:
                    self._pending_put = None

    def _log_source_advance(
        self,
        event: str,
        *,
        item_index: int,
        **details: Any,
    ) -> None:
        if item_index >= _DIAGNOSTIC_ITEM_LIMIT:
            return
        stream_log(
            "gateway-live-chat-async-sse",
            "runtime",
            event,
            item_index=item_index,
            elapsed_ms=round((time.perf_counter() - self._started) * 1000.0, 3),
            **details,
            **self._diagnostic_context,
        )

    def _run(self) -> None:
        iterator: Iterator[Any] | None = None
        item_count = 0
        failed = False
        try:
            iterator = iter(self._factory())
            while not self._cancelled.is_set():
                source_started = time.perf_counter()
                self._log_source_advance(
                    "live_chat_async_sse_source_next_started",
                    item_index=item_count,
                )
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                source_next_ms = (time.perf_counter() - source_started) * 1000.0
                self._log_source_advance(
                    "live_chat_async_sse_source_next_completed",
                    item_index=item_count,
                    source_next_ms=round(source_next_ms, 3),
                    chunk_type=type(chunk).__name__,
                    chunk_bytes=(
                        len(chunk)
                        if isinstance(chunk, bytes)
                        else len(str(chunk).encode("utf-8"))
                    ),
                )

                enqueue_started = time.perf_counter()
                if not self._enqueue(_BridgeItem("chunk", chunk)):
                    break
                enqueue_wait_ms = (time.perf_counter() - enqueue_started) * 1000.0
                self._log_source_advance(
                    "live_chat_async_sse_source_item_enqueued",
                    item_index=item_count,
                    source_next_ms=round(source_next_ms, 3),
                    enqueue_wait_ms=round(enqueue_wait_ms, 3),
                    buffered_item_count=self._queue.qsize(),
                )
                item_count += 1
                if not self._first_item_enqueued:
                    self._first_item_enqueued = True
                    stream_log(
                        "gateway-live-chat-async-sse",
                        "runtime",
                        "live_chat_async_sse_first_item_enqueued",
                        startup_ms=round((time.perf_counter() - self._started) * 1000.0, 3),
                        buffered_item_count=self._queue.qsize(),
                        **self._diagnostic_context,
                    )
        except Exception as exc:  # noqa: BLE001 - preserve arbitrary route failures
            failed = True
            self._enqueue(_BridgeItem("error", exc))
        finally:
            if self._cancelled.is_set() and iterator is not None:
                close = getattr(iterator, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:  # noqa: BLE001 - cleanup must not mask cancellation
                        stream_log(
                            "gateway-live-chat-async-sse",
                            "runtime",
                            "live_chat_async_sse_producer_close_failed",
                            error_type=type(exc).__name__,
                            **self._diagnostic_context,
                        )
            if not self._cancelled.is_set():
                self._enqueue(_BridgeItem("done"))
            stream_log(
                "gateway-live-chat-async-sse",
                "runtime",
                "live_chat_async_sse_producer_finished",
                elapsed_ms=round((time.perf_counter() - self._started) * 1000.0, 3),
                item_count=item_count,
                cancelled=self._cancelled.is_set(),
                failed=failed,
                **self._diagnostic_context,
            )

    def cancel(self) -> None:
        if self._cancelled.is_set():
            return
        self._cancelled.set()
        with self._future_lock:
            pending = self._pending_put
        if pending is not None:
            pending.cancel()
        stream_log(
            "gateway-live-chat-async-sse",
            "runtime",
            "live_chat_async_sse_cancel_requested",
            elapsed_ms=round((time.perf_counter() - self._started) * 1000.0, 3),
            producer_alive=self._thread.is_alive(),
            **self._diagnostic_context,
        )

    async def stream(self) -> AsyncIterator[Any]:
        if not self._consumer_attached:
            self._consumer_attached = True
            stream_log(
                "gateway-live-chat-async-sse",
                "runtime",
                "live_chat_async_sse_consumer_attached",
                attach_delay_ms=round((time.perf_counter() - self._started) * 1000.0, 3),
                buffered_item_count=self._queue.qsize(),
                producer_alive=self._thread.is_alive(),
                **self._diagnostic_context,
            )
        completed = False
        try:
            while True:
                item = await self._queue.get()
                if item.kind == "chunk":
                    yield item.payload
                    continue
                if item.kind == "error":
                    error = item.payload
                    if isinstance(error, BaseException):
                        raise error
                    raise RuntimeError("live chat stream producer failed")
                completed = True
                stream_log(
                    "gateway-live-chat-async-sse",
                    "runtime",
                    "live_chat_async_sse_consumer_completed",
                    elapsed_ms=round((time.perf_counter() - self._started) * 1000.0, 3),
                    **self._diagnostic_context,
                )
                return
        finally:
            if not completed:
                self.cancel()


def eager_async_sse_stream(
    factory: Callable[[], Iterable[Any]],
    *,
    queue_items: int = _DEFAULT_QUEUE_ITEMS,
    diagnostic_context: dict[str, Any] | None = None,
) -> AsyncIterator[Any]:
    """Start ``factory`` immediately and expose its ordered output asynchronously."""

    bounded_items = max(1, min(_MAX_QUEUE_ITEMS, int(queue_items)))
    bridge = _EagerAsyncSseBridge(
        factory,
        queue_items=bounded_items,
        diagnostic_context=diagnostic_context,
    )
    return bridge.stream()


__all__ = ["eager_async_sse_stream"]
