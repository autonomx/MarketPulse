"""Wait for a condition instead of sleeping a fixed time."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def wait_until(probe: Callable[[], T], done: Callable[[T], bool], *, timeout: float = 15.0,
               interval: float = 0.1) -> T:
    """Call ``probe`` until ``done`` accepts its result or ``timeout`` passes; return the last result."""
    deadline = time.monotonic() + timeout
    while True:
        result = probe()
        if done(result) or time.monotonic() > deadline:
            return result
        time.sleep(interval)
