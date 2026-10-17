"""A dedicated thread pool for model-service work (WP-7.1).

Model calls (synthesis, cloning) are synchronous and long. Running them on
the event loop stalls every other request; running them on the server's
shared thread pool lets a burst of model calls starve health checks and
cheap reads. A model service runs them here instead.

Device admission stays with the device permit service: it queues waiters by
priority, so this pool must be large enough to hold the waiters too. Sizing
it to the device's capacity would let a queued batch call keep a realtime
call from reaching the permit queue at all.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class ModelExecutor:
    def __init__(self, name: str, max_workers: int) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.name = name
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"omnix-{name}",
        )

    async def run(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """Run ``fn`` on the pool; context variables carry over, as with to_thread."""
        context = contextvars.copy_context()
        call = functools.partial(context.run, fn, *args, **kwargs)
        return await asyncio.get_running_loop().run_in_executor(self._executor, call)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


__all__ = ["ModelExecutor"]
