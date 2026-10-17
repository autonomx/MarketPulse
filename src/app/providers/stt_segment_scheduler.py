"""Provider-scoped fair scheduling for segmented live transcription."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar

T = TypeVar("T")


class SegmentQueueFullError(RuntimeError):
    """Raised when the bounded provider or session queue is full."""


@dataclass(frozen=True)
class SegmentJob(Generic[T]):
    session_id: str
    segment_id: str
    sequence: int
    created_at: float
    run: Callable[[], Awaitable[T]]
    future: asyncio.Future[T]


class ProviderSegmentScheduler(Generic[T]):
    """Serialize provider inference while scheduling sessions fairly."""

    def __init__(
        self,
        *,
        max_queued_jobs: int = 32,
        max_session_jobs: int = 8,
    ) -> None:
        if max_queued_jobs <= 0 or max_session_jobs <= 0:
            raise ValueError("STT scheduler queue limits must be positive")
        self.max_queued_jobs = max_queued_jobs
        self.max_session_jobs = max_session_jobs
        self._pending: dict[str, deque[SegmentJob[T]]] = {}
        self._session_order: deque[str] = deque()
        self._queued_jobs = 0
        self._active_jobs = 0
        self._condition = asyncio.Condition()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def queued_jobs(self) -> int:
        return self._queued_jobs

    @property
    def is_idle(self) -> bool:
        return self._queued_jobs == 0 and self._active_jobs == 0

    def queued_for_session(self, session_id: str) -> int:
        return len(self._pending.get(session_id, ()))

    async def submit(
        self,
        *,
        session_id: str,
        segment_id: str,
        sequence: int,
        run: Callable[[], Awaitable[T]],
    ) -> asyncio.Future[T]:
        async with self._condition:
            if self._closed:
                raise RuntimeError("STT scheduler is closed")
            if self._queued_jobs >= self.max_queued_jobs:
                raise SegmentQueueFullError("provider_queue_full")
            session_queue = self._pending.setdefault(session_id, deque())
            if len(session_queue) >= self.max_session_jobs:
                raise SegmentQueueFullError("session_queue_full")
            loop = asyncio.get_running_loop()
            future: asyncio.Future[T] = loop.create_future()
            job = SegmentJob(
                session_id=session_id,
                segment_id=segment_id,
                sequence=sequence,
                created_at=time.perf_counter(),
                run=run,
                future=future,
            )
            was_empty = not session_queue
            session_queue.append(job)
            self._queued_jobs += 1
            if was_empty:
                self._session_order.append(session_id)
            self._ensure_worker()
            self._condition.notify_all()
            return future

    async def cancel_session(self, session_id: str) -> int:
        async with self._condition:
            jobs = self._pending.pop(session_id, deque())
            self._session_order = deque(item for item in self._session_order if item != session_id)
            cancelled = 0
            while jobs:
                job = jobs.popleft()
                self._queued_jobs -= 1
                if not job.future.done():
                    job.future.cancel()
                    cancelled += 1
            self._condition.notify_all()
            return cancelled

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            for session_id in list(self._pending):
                jobs = self._pending.pop(session_id)
                while jobs:
                    job = jobs.popleft()
                    self._queued_jobs -= 1
                    if not job.future.done():
                        job.future.cancel()
            self._session_order.clear()
            self._condition.notify_all()
        if self._worker is not None:
            await self._worker

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="omnix-stt-segment-scheduler")

    async def _next_job(self) -> SegmentJob[T] | None:
        async with self._condition:
            while not self._closed and self._queued_jobs == 0:
                await self._condition.wait()
            if self._queued_jobs == 0:
                return None
            session_id = self._session_order.popleft()
            session_queue = self._pending[session_id]
            job = session_queue.popleft()
            self._queued_jobs -= 1
            if session_queue:
                self._session_order.append(session_id)
            else:
                self._pending.pop(session_id, None)
            return job

    async def _run(self) -> None:
        while True:
            job = await self._next_job()
            if job is None:
                return
            if job.future.cancelled():
                continue
            self._active_jobs += 1
            try:
                result = await job.run()
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                raise
            except Exception as exc:
                if not job.future.done():
                    job.future.set_exception(exc)
            else:
                if not job.future.done():
                    job.future.set_result(result)
            finally:
                self._active_jobs -= 1
