"""Graceful drain for rolling restarts (WP-6.7).

On SIGTERM a serving process enters drain mode: ``/ready`` reports 503 at
once, new requests are refused with 503 + ``Connection: close`` so the
ingress retries them elsewhere, and in-flight requests and streams get up to
``OMNIX_DRAIN_SECONDS`` to finish. Only then does the server stop, and the
normal lifespan shutdown releases leases and advisory locks.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
import threading
import time
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config.env import env_int

logger = logging.getLogger(__name__)

# Probes stay answerable while draining so the ingress can see the change.
PROBE_PATHS = frozenset({"/health", "/api/health", "/ready"})


def drain_seconds() -> int:
    return env_int("OMNIX_DRAIN_SECONDS", 30, minimum=0, maximum=3600)


def drain_min_seconds() -> int:
    """Time to keep answering ``/ready`` 503 before stopping, even when idle.

    Set it to at least the ingress readiness-probe interval in multi-replica
    deployments so the ingress stops routing here before the listener closes.
    """
    return env_int("OMNIX_DRAIN_MIN_SECONDS", 0, minimum=0, maximum=600)


class DrainController:
    """Process-wide drain flag plus an in-flight request counter."""

    def __init__(self) -> None:
        self._draining = threading.Event()
        self._changed = threading.Condition()
        self._inflight = 0
        self.started_at: float | None = None

    @property
    def draining(self) -> bool:
        return self._draining.is_set()

    @property
    def inflight(self) -> int:
        with self._changed:
            return self._inflight

    def begin(self) -> bool:
        """Enter drain mode; returns False when already draining."""
        with self._changed:
            if self._draining.is_set():
                return False
            self.started_at = time.monotonic()
            self._draining.set()
            self._changed.notify_all()
        logger.info("drain_started", extra={"inflight": self.inflight})
        return True

    def enter(self) -> None:
        with self._changed:
            self._inflight += 1

    def exit(self) -> None:
        with self._changed:
            self._inflight = max(0, self._inflight - 1)
            self._changed.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        with self._changed:
            return self._changed.wait_for(lambda: self._inflight == 0, timeout=max(0.0, timeout))

    def reset_for_tests(self) -> None:
        with self._changed:
            self._draining.clear()
            self._inflight = 0
            self.started_at = None


_PROCESS_DRAIN = DrainController()


def process_drain() -> DrainController:
    """The serving process's drain state (signals are process-wide)."""
    return _PROCESS_DRAIN


class DrainMiddleware:
    def __init__(self, app: ASGIApp, *, controller: DrainController | None = None) -> None:
        self.app = app
        self.controller = controller or process_drain()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or scope.get("path") in PROBE_PATHS:
            await self.app(scope, receive, send)
            return
        if self.controller.draining:
            await self._refuse(scope, send)
            return
        self.controller.enter()
        try:
            await self.app(scope, receive, send)
        finally:
            self.controller.exit()

    @staticmethod
    async def _refuse(scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            # 1012 "service restart": clients reconnect to another replica.
            await send({"type": "websocket.close", "code": 1012, "reason": "draining"})
            return
        body = json.dumps({"detail": "draining"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"retry-after", b"1"),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def start_drain_then(
    stop: Callable[[], None],
    controller: DrainController | None = None,
    *,
    timeout: float | None = None,
    minimum: float | None = None,
) -> threading.Thread | None:
    """Begin draining and call ``stop()`` once idle or after the drain window."""
    drain = controller or process_drain()
    if not drain.begin():
        return None
    window = float(drain_seconds() if timeout is None else timeout)
    floor = min(window, float(drain_min_seconds() if minimum is None else minimum))

    def finish() -> None:
        if floor > 0:
            time.sleep(floor)
        idle = drain.wait_idle(window - floor)
        logger.info("drain_finished", extra={"idle": idle, "inflight": drain.inflight})
        stop()

    thread = threading.Thread(target=finish, name="omnix-drain", daemon=True)
    thread.start()
    return thread


def create_draining_server(config: Any) -> Any:
    """A uvicorn server whose first exit signal drains before stopping.

    A second signal falls through to uvicorn's normal (then forced) exit.
    """
    import uvicorn

    class DrainingServer(uvicorn.Server):
        def handle_exit(self, sig: int, frame: Any) -> None:
            if not process_drain().draining and not self.should_exit:
                start_drain_then(self._request_exit)
                return
            super().handle_exit(sig, frame)

        def _request_exit(self) -> None:
            self.should_exit = True

    return DrainingServer(config)


__all__ = [
    "DrainController",
    "DrainMiddleware",
    "PROBE_PATHS",
    "create_draining_server",
    "drain_min_seconds",
    "drain_seconds",
    "process_drain",
    "start_drain_then",
]
