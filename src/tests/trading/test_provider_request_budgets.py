"""Provider request budgets and priority lanes (WP-8.3)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.apps.trading.providers.binance import _request_weight
from app.apps.trading.providers.errors import ProviderRateLimitedError, ProviderUnavailableError
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime
from app.apps.trading.providers.request_budget import (
    BudgetLimit,
    RequestBudget,
    current_lane,
    in_provider_lane,
    provider_lane,
    upstream_of,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _budget(weight: float = 10, per_seconds: float = 10) -> tuple[RequestBudget, _Clock]:
    clock = _Clock()
    return RequestBudget("test", BudgetLimit(weight, per_seconds), clock=clock, sleep=clock.sleep), clock


def test_other_lanes_cannot_spend_the_protective_reserve() -> None:
    budget, _ = _budget(weight=10, per_seconds=100)
    for _ in range(9):
        budget.acquire(1, lane="standard")

    with pytest.raises(ProviderRateLimitedError, match="research lane"):
        budget.acquire(1, lane="research")
    budget.acquire(1, lane="protective")


def test_a_request_waits_for_refill_within_its_lanes_limit() -> None:
    budget, clock = _budget()
    for _ in range(9):
        budget.acquire(1, lane="standard")

    budget.acquire(1, lane="standard")  # waits about one second for one token
    assert 0.9 <= clock.now <= 1.1


def test_an_exhausted_budget_fails_locally_without_waiting_past_the_lane_limit() -> None:
    budget, clock = _budget(weight=10, per_seconds=100)
    for _ in range(9):
        budget.acquire(1, lane="standard")

    with pytest.raises(ProviderRateLimitedError, match="retry_after_seconds"):
        budget.acquire(1, lane="research")
    assert clock.now == 0.0
    assert budget.exhausted_count == 1


def test_weight_counts_against_the_budget() -> None:
    budget, _ = _budget(weight=100, per_seconds=1000)
    budget.acquire(80, lane="standard")
    with pytest.raises(ProviderRateLimitedError):
        budget.acquire(20, lane="research")
    with pytest.raises(ValueError, match="exceeds"):
        budget.acquire(101, lane="protective")


def test_runtimes_of_one_upstream_share_its_budget() -> None:
    assert upstream_of("finviz_yahoo_enrichment") == "yahoo"
    assert upstream_of("binance-futures") == "binance_futures"
    first = ProviderHttpRuntime("alpaca_iex")
    second = ProviderHttpRuntime("alpaca_historical_gapper_reconstruction")
    assert first.budget is not None and first.budget is second.budget
    # No published limit, no proactive budget.
    assert ProviderHttpRuntime("yahoo-metrics").budget is None
    # A runtime on an injected session (tests) spends no shared budget.
    assert ProviderHttpRuntime("alpaca_iex", session=object()).budget is None


def test_binance_request_weights() -> None:
    assert _request_weight("/api/v3/klines", {"symbol": "BTCUSDT"}) == 2
    assert _request_weight("/api/v3/ticker/24hr", {"symbol": "BTCUSDT"}) == 2
    assert _request_weight("/api/v3/ticker/24hr", {}) == 80


class _Session:
    def __init__(self) -> None:
        self.fail = True

    def request(self, method, url, **kwargs):
        if self.fail and "research" in url:
            return httpx.Response(503, request=httpx.Request(method, url))
        return httpx.Response(200, request=httpx.Request(method, url))


def test_research_failures_do_not_open_the_protective_circuit() -> None:
    runtime = ProviderHttpRuntime(
        "alpaca_iex",
        session=_Session(),
        max_attempts=1,
        circuit_failure_threshold=1,
        circuit_cooldown_seconds=30,
    )
    with provider_lane("research"):
        with pytest.raises(ProviderUnavailableError):
            runtime.get("https://example.test/research")
        with pytest.raises(ProviderUnavailableError, match="circuit open"):
            runtime.get("https://example.test/research")

    with provider_lane("protective"):
        assert runtime.get("https://example.test/quote").status_code == 200
    snapshot = runtime.snapshot()
    assert snapshot.circuit_open_count == 1
    assert snapshot.circuit_open_until is not None


def test_requests_spend_the_runtime_budget_in_their_lane() -> None:
    budget, _ = _budget(weight=10, per_seconds=100)
    runtime = ProviderHttpRuntime("alpaca_iex", session=_Session(), budget=budget)
    for _ in range(9):
        runtime.get("https://example.test/quote", weight=1)
    with pytest.raises(ProviderRateLimitedError):
        runtime.get("https://example.test/quote")
    with provider_lane("protective"):
        runtime.get("https://example.test/quote")


def test_the_lane_follows_work_into_worker_threads() -> None:
    @in_provider_lane("protective")
    async def protective_check():
        return await asyncio.to_thread(current_lane)

    assert asyncio.run(protective_check()) == "protective"
    assert current_lane() == "standard"
