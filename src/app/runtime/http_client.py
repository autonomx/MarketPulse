"""Pooled HTTP clients for providers and model services (WP-7.2).

Every outbound HTTP call to a provider or model service goes through a
``PooledHttpClient``: one ``httpx.Client`` with keep-alive connection pooling
and explicit connect/read/write/pool timeouts, plus

- retries with exponential backoff and jitter: idempotent calls (GET, HEAD,
  OPTIONS) retry on transport errors and 5xx; every call retries on 429 and
  503, honouring ``Retry-After``;
- a circuit breaker: after ``circuit_failures`` consecutive failures the
  client fails fast for ``circuit_cooldown_seconds``, then lets one probe
  through (half-open); its result closes or re-opens the circuit;
- cancellation: a ``CancellationToken`` stops retries and closes an open
  stream, so a cancelled call returns without waiting for the remote side.
"""
from __future__ import annotations

import email.utils
import random
import socket
import sys
import threading
import time
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import httpx

from app.caching.bounded_cache import bounded_lru_cache
from app.runtime.cancellation import CancellationToken, OperationCancelled

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Told about each attempt; the gateway installs its metrics recorder here
# (WP-10.3). ``status`` is the response status, "transport_error" or
# "circuit_open"; ``seconds`` is the time to the response headers.
_attempt_observer: Callable[[str, int | str, float | None], None] | None = None
_retry_observer: Callable[[str], None] | None = None


def set_attempt_observers(
    on_attempt: Callable[[str, int | str, float | None], None] | None,
    on_retry: Callable[[str], None] | None,
) -> None:
    """Install (or, with ``None``, remove) this process's attempt observers."""
    global _attempt_observer, _retry_observer
    _attempt_observer, _retry_observer = on_attempt, on_retry


def _observe_attempt(client: str, status: int | str, seconds: float | None = None) -> None:
    observer = _attempt_observer
    if observer is not None:
        observer(client, status, seconds)


def _observe_retry(client: str) -> None:
    observer = _retry_observer
    if observer is not None:
        observer(client)
_RETRY_ANY_METHOD = frozenset({429, 503})
_RETRY_IDEMPOTENT = frozenset({500, 502, 504}) | _RETRY_ANY_METHOD


@dataclass(frozen=True)
class HttpPolicy:
    connect_seconds: float = 5.0
    read_seconds: float = 60.0
    write_seconds: float = 10.0
    pool_seconds: float = 5.0
    max_retries: int = 2
    backoff_seconds: float = 0.25
    max_backoff_seconds: float = 8.0
    # A longer Retry-After is not waited out; the response is returned.
    max_retry_after_seconds: float = 30.0
    circuit_failures: int = 5
    circuit_cooldown_seconds: float = 10.0
    max_connections: int = 20


class CircuitOpenError(httpx.TransportError):
    """The provider failed repeatedly; calls fail fast until the cooldown ends."""


class CircuitBreaker:
    def __init__(self, failures: int, cooldown_seconds: float) -> None:
        self._threshold = max(1, failures)
        self._cooldown = max(0.0, cooldown_seconds)
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            if self._open_until is None:
                return "closed"
            return "open" if time.monotonic() < self._open_until else "half_open"

    def before_call(self, name: str) -> None:
        with self._lock:
            if self._open_until is None:
                return
            if time.monotonic() < self._open_until or self._probe_in_flight:
                raise CircuitOpenError(f"{name}: circuit open after repeated failures")
            self._probe_in_flight = True  # half-open: this call is the probe

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = None
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._probe_in_flight or self._consecutive_failures >= self._threshold:
                self._open_until = time.monotonic() + self._cooldown
            self._probe_in_flight = False


def _timeout(policy: HttpPolicy, value: Any) -> httpx.Timeout:
    if isinstance(value, httpx.Timeout):
        return value
    connect, read = policy.connect_seconds, policy.read_seconds
    if isinstance(value, tuple):  # requests-style (connect, read)
        connect, read = float(value[0]), float(value[1])
    elif value is not None:
        read = float(value)
        connect = min(connect, read)
    return httpx.Timeout(connect=connect, read=read, write=policy.write_seconds, pool=policy.pool_seconds)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _abort(response: httpx.Response) -> None:
    """Wake a thread blocked reading ``response`` (called from another thread).

    On Linux, shutting the socket down wakes the blocked ``poll``; closing it
    there could hand the reader a reused descriptor. Windows' ``select`` does
    not wake on a local shutdown, only when the socket is closed. The reading
    thread then closes the response itself.
    """
    stream = response.extensions.get("network_stream")
    sock = stream.get_extra_info("socket") if stream is not None else None
    if sock is None:
        response.close()
        return
    with suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    if sys.platform == "win32":
        with suppress(OSError):
            sock.close()


class OpenStream:
    """An open streamed response; ``close()`` releases it (and its cancel hook)."""

    def __init__(self, response: httpx.Response, stack: ExitStack) -> None:
        self.response = response
        self._stack = stack

    def __getattr__(self, name: str) -> Any:
        return getattr(self.response, name)

    def close(self) -> None:
        self._stack.close()

    def __enter__(self) -> OpenStream:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class PooledHttpClient:
    def __init__(
        self,
        name: str,
        policy: HttpPolicy | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self.policy = policy or HttpPolicy()
        self.circuit = CircuitBreaker(self.policy.circuit_failures, self.policy.circuit_cooldown_seconds)
        self._client = httpx.Client(
            timeout=_timeout(self.policy, None),
            limits=httpx.Limits(
                max_connections=self.policy.max_connections,
                max_keepalive_connections=self.policy.max_connections,
            ),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        timeout: Any = None,
        retry: bool | None = None,
        cancel: CancellationToken | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request and read the whole body. ``retry``: None follows the method, True retries, False never does."""
        with self._open(method, url, timeout=timeout, retry=retry, cancel=cancel, **kwargs) as response:
            response.read()
            return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    @contextmanager
    def stream(
        self,
        method: str,
        url: str,
        *,
        timeout: Any = None,
        retry: bool | None = None,
        cancel: CancellationToken | None = None,
        **kwargs: Any,
    ) -> Iterator[httpx.Response]:
        """Open a streamed response; a cancel closes it, ending iteration at once."""
        with self._open(method, url, timeout=timeout, retry=retry, cancel=cancel, **kwargs) as response:
            unregister = cancel.on_cancel(lambda: _abort(response)) if cancel is not None else (lambda: None)
            try:
                yield response
            except (httpx.HTTPError, RuntimeError) as exc:
                # Reading a response closed by the cancel raises a read or
                # stream-closed error; report the cancel, not the symptom.
                if cancel is not None and cancel.cancelled:
                    raise OperationCancelled() from exc
                raise
            finally:
                unregister()

    def open_stream(self, method: str, url: str, **kwargs: Any) -> OpenStream:
        """``stream`` for callers that close the response themselves (``requests``' ``stream=True``)."""
        stack = ExitStack()
        try:
            response = stack.enter_context(self.stream(method, url, **kwargs))
        except BaseException:
            stack.close()
            raise
        return OpenStream(response, stack)

    @contextmanager
    def _open(
        self,
        method: str,
        url: str,
        *,
        timeout: Any,
        retry: bool | None,
        cancel: CancellationToken | None,
        **kwargs: Any,
    ) -> Iterator[httpx.Response]:
        method = method.upper()
        # retry=None: the method decides; True: retry as if idempotent; False: never.
        idempotent = method in IDEMPOTENT_METHODS if retry is None else retry
        max_retries = 0 if retry is False else self.policy.max_retries
        timeout = _timeout(self.policy, timeout)
        attempt = 0
        while True:
            if cancel is not None:
                cancel.raise_if_cancelled()
            try:
                self.circuit.before_call(self.name)
            except CircuitOpenError:
                _observe_attempt(self.name, "circuit_open")
                raise
            request = self._client.build_request(method, url, timeout=timeout, **kwargs)
            sent = time.perf_counter()
            try:
                response = self._client.send(request, stream=True)
            except httpx.TransportError:
                _observe_attempt(self.name, "transport_error", time.perf_counter() - sent)
                self.circuit.record_failure()
                if not idempotent or attempt >= max_retries:
                    raise
                _observe_retry(self.name)
                self._pause(self._backoff(attempt), cancel)
                attempt += 1
                continue

            status = response.status_code
            _observe_attempt(self.name, status, time.perf_counter() - sent)
            retryable = status in (_RETRY_IDEMPOTENT if idempotent else _RETRY_ANY_METHOD)
            if status >= 500 or status == 429:
                self.circuit.record_failure()
            else:
                self.circuit.record_success()
            if retryable and attempt < max_retries:
                delay = self._backoff(attempt)
                retry_after = _retry_after_seconds(response)
                if retry_after is None or retry_after <= self.policy.max_retry_after_seconds:
                    _observe_retry(self.name)
                    response.close()
                    self._pause(max(delay, retry_after or 0.0), cancel)
                    attempt += 1
                    continue
            try:
                yield response
            finally:
                response.close()
            return

    def _backoff(self, attempt: int) -> float:
        delay = min(self.policy.max_backoff_seconds, self.policy.backoff_seconds * (2**attempt))
        return delay * random.uniform(0.5, 1.0)

    @staticmethod
    def _pause(seconds: float, cancel: CancellationToken | None) -> None:
        if cancel is None:
            time.sleep(seconds)
        elif cancel.wait(seconds):
            raise OperationCancelled()


@bounded_lru_cache(max_entries=64, ttl_seconds=86400.0)
def shared_http_client(name: str, policy: HttpPolicy | None = None) -> PooledHttpClient:
    """One pooled client per service name and policy, shared by the process.

    An evicted client is not closed: a caller may still hold it, and its
    connections are released when it is collected.
    """
    return PooledHttpClient(name, policy)


__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "HttpPolicy",
    "IDEMPOTENT_METHODS",
    "OpenStream",
    "PooledHttpClient",
    "set_attempt_observers",
    "shared_http_client",
]
