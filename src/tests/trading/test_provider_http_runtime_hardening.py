from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.apps.trading.providers.errors import ProviderUnavailableError
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime


class _SlowSession:
    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def request(self, method, url, **kwargs):
        with self.lock:
            self.calls += 1
        time.sleep(0.05)
        return SimpleNamespace(
            status_code=200,
            headers={},
            raise_for_status=lambda: None,
        )


def test_identical_provider_requests_are_single_flight() -> None:
    session = _SlowSession()
    runtime = ProviderHttpRuntime(
        "test",
        session=session,
        max_attempts=1,
        max_concurrency=4,
    )
    barrier = threading.Barrier(3)
    results = []
    errors = []

    def worker() -> None:
        try:
            barrier.wait()
            results.append(
                runtime.get_coalesced(
                    "same-request",
                    "https://example.test/data",
                )
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert len(results) == 2
    assert session.calls == 1
    assert results[0] is results[1]



class _FailingSession:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            status_code=503,
            headers={},
            raise_for_status=lambda: None,
        )


def test_repeated_provider_failures_open_circuit_and_suppress_upstream_call() -> None:
    session = _FailingSession()
    runtime = ProviderHttpRuntime(
        "test",
        session=session,
        max_attempts=1,
        max_concurrency=1,
        initial_backoff_seconds=0,
        circuit_failure_threshold=2,
        circuit_cooldown_seconds=30,
    )

    with pytest.raises(ProviderUnavailableError):
        runtime.get("https://example.test/data")
    with pytest.raises(ProviderUnavailableError):
        runtime.get("https://example.test/data")

    upstream_calls = session.calls
    with pytest.raises(ProviderUnavailableError, match="circuit open"):
        runtime.get("https://example.test/data")

    assert session.calls == upstream_calls
    snapshot = runtime.snapshot()
    assert snapshot.circuit_open_count >= 1
    assert snapshot.circuit_suppression_count >= 1
    assert snapshot.circuit_open_until is not None



class _BlockingFailSession:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()

    def request(self, method, url, **kwargs):
        with self.lock:
            self.calls += 1
        self.entered.set()
        self.release.wait(timeout=2)
        return SimpleNamespace(
            status_code=503,
            headers={},
            raise_for_status=lambda: None,
        )


def test_queued_request_rechecks_circuit_after_concurrency_wait() -> None:
    session = _BlockingFailSession()
    runtime = ProviderHttpRuntime(
        "test",
        session=session,
        max_attempts=1,
        max_concurrency=1,
        initial_backoff_seconds=0,
        circuit_failure_threshold=1,
        circuit_cooldown_seconds=30,
    )
    errors = []

    def worker() -> None:
        try:
            runtime.get("https://example.test/data")
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker)
    first.start()
    assert session.entered.wait(timeout=1)
    second.start()
    time.sleep(0.02)
    session.release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(errors) == 2
    assert session.calls == 1
    assert runtime.snapshot().circuit_suppression_count >= 1
