"""Cooperative cancellation for provider calls (WP-7.2).

A ``CancellationToken`` is passed to a provider call. Streaming loops check
it between items; resources that block (an HTTP stream waiting for the next
byte) register a callback that closes them, so a cancel takes effect without
waiting for the remote side.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

_LOGGER = logging.getLogger(__name__)


class OperationCancelled(Exception):
    """Raised by ``raise_if_cancelled`` once the token is cancelled."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - one failing closer must not skip the rest
                _LOGGER.warning("cancellation callback failed", exc_info=True)

    # ``threading.Event`` spelling, so a token can replace an event in place.
    def is_set(self) -> bool:
        return self.cancelled

    def set(self) -> None:
        self.cancel()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise OperationCancelled()

    def wait(self, timeout: float | None = None) -> bool:
        """Sleep up to ``timeout``; True if cancelled meanwhile."""
        return self._event.wait(timeout)

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Run ``callback`` on cancel (now, if already cancelled); returns an unregister."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)

                def unregister() -> None:
                    with self._lock:
                        if callback in self._callbacks:
                            self._callbacks.remove(callback)

                return unregister
        callback()
        return lambda: None


__all__ = ["CancellationToken", "OperationCancelled"]
