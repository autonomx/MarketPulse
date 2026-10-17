"""Proactive request budgets and priority lanes for market-data providers (WP-8.3).

Every upstream with a published limit (Binance, Alpaca, CoinMarketCap, SEC
EDGAR) has one token bucket per process, shared by all runtimes that call it,
sized below that limit and measured in request weight (Binance counts weight,
not requests).
A request waits for its tokens instead of being sent and refused; one that
would wait too long fails locally with ``ProviderRateLimitedError``.

Requests run in a lane, set with ``provider_lane``:

- ``protective``: exits and protection checks. They may spend the reserved
  slice of every bucket that other lanes may not touch, and wait longest.
- ``standard``: everything else by default.
- ``research``: research polling, the first to give way.

Each lane has its own circuit breaker in ``ProviderHttpRuntime``, so research
polling that trips a breaker does not block a protective exit.
"""
from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Literal

from app.caching.bounded_cache import bounded_lru_cache

from .errors import ProviderCancelledError, ProviderRateLimitedError

ProviderLane = Literal["protective", "standard", "research"]
LANES: tuple[ProviderLane, ...] = ("protective", "standard", "research")

_LANE: ContextVar[ProviderLane] = ContextVar("omnix_provider_lane", default="standard")

# Share of each bucket only the protective lane may spend.
PROTECTIVE_RESERVE = 0.1
# Longest a request waits for budget before failing locally, by lane.
MAX_WAIT_SECONDS: dict[ProviderLane, float] = {"protective": 10.0, "standard": 5.0, "research": 2.0}


@contextmanager
def provider_lane(lane: ProviderLane) -> Iterator[None]:
    if lane not in LANES:
        raise ValueError(f"unknown provider lane: {lane}")
    token = _LANE.set(lane)
    try:
        yield
    finally:
        _LANE.reset(token)


def current_lane() -> ProviderLane:
    return _LANE.get()


def in_provider_lane(lane: ProviderLane):
    """Run a function (sync or async) with its provider requests in ``lane``.

    Worker threads started with ``asyncio.to_thread`` inherit the lane.
    """

    def decorate(function):
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def run_async(*args, **kwargs):
                with provider_lane(lane):
                    return await function(*args, **kwargs)

            return run_async

        @wraps(function)
        def run(*args, **kwargs):
            with provider_lane(lane):
                return function(*args, **kwargs)

        return run

    return decorate


@dataclass(frozen=True, slots=True)
class BudgetLimit:
    """``weight`` per ``per_seconds``, refilled continuously."""

    weight: float
    per_seconds: float


# Per process, below each upstream's published limit. Upstreams without a
# published limit (Yahoo, Finviz) have no proactive budget; their 429s and
# failures still open the lane's circuit.
DEFAULT_LIMITS: dict[str, BudgetLimit] = {
    "binance": BudgetLimit(4800, 60),  # spot: 6,000 request weight per minute
    "binance_futures": BudgetLimit(2000, 60),  # futures: 2,400 request weight per minute
    "alpaca": BudgetLimit(180, 60),  # 200 requests per minute
    "coinmarketcap": BudgetLimit(25, 60),  # 30 requests per minute
    "sec_edgar": BudgetLimit(8, 1),  # 10 requests per second
}


def upstream_of(provider_id: str) -> str:
    """The upstream a runtime's requests count against."""
    normalized = provider_id.lower().replace("-", "_")
    for upstream in ("binance_futures", "binance", "alpaca", "coinmarketcap", "sec_edgar", "finviz", "yahoo"):
        if normalized.startswith(upstream) or f"_{upstream}" in normalized:
            # ``finviz_yahoo_enrichment`` calls Yahoo, not Finviz.
            if upstream == "finviz" and "yahoo" in normalized:
                return "yahoo"
            return upstream
    return normalized


class RequestBudget:
    """A token bucket with a reserve only the protective lane may spend."""

    def __init__(self, upstream: str, limit: BudgetLimit, *, clock=time.monotonic, sleep=time.sleep) -> None:
        self.upstream = upstream
        self.capacity = float(limit.weight)
        self.refill_per_second = float(limit.weight) / float(limit.per_seconds)
        self.reserve = self.capacity * PROTECTIVE_RESERVE
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.capacity
        self._updated = clock()
        self._guard = threading.Lock()
        self.exhausted_count = 0

    def _refill(self, now: float) -> None:
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.refill_per_second)
        self._updated = now

    def _wait_for(self, weight: float, lane: ProviderLane) -> float:
        """Seconds until ``weight`` can be spent in ``lane`` (0 spends it now)."""
        now = self._clock()
        with self._guard:
            self._refill(now)
            floor = 0.0 if lane == "protective" else self.reserve
            needed = weight + floor - self._tokens
            if needed <= 0:
                self._tokens -= weight
                return 0.0
            return needed / self.refill_per_second

    def acquire(
        self,
        weight: float = 1.0,
        *,
        lane: ProviderLane | None = None,
        cancellation: threading.Event | None = None,
    ) -> None:
        lane = lane or current_lane()
        weight = max(0.0, float(weight))
        if weight > self.capacity:
            raise ValueError(f"request weight {weight} exceeds the {self.upstream} budget")
        deadline = self._clock() + MAX_WAIT_SECONDS[lane]
        while True:
            wait = self._wait_for(weight, lane)
            if wait == 0.0:
                return
            if self._clock() + wait > deadline:
                with self._guard:
                    self.exhausted_count += 1
                raise ProviderRateLimitedError(
                    f"{self.upstream} request budget exhausted for the {lane} lane; "
                    f"retry_after_seconds={wait:.3f}"
                )
            if cancellation is not None and cancellation.is_set():
                raise ProviderCancelledError(f"{self.upstream} request cancelled")
            self._sleep(min(wait, 0.05))


@bounded_lru_cache(max_entries=16, ttl_seconds=86400.0)
def request_budget(upstream: str) -> RequestBudget | None:
    """The process-wide budget for ``upstream``, or None when it has no limit.

    One bucket per upstream, so every runtime that calls it spends the same
    budget; an entry that expires is recreated full, as a day-old bucket is.
    """
    limit = DEFAULT_LIMITS.get(upstream)
    return RequestBudget(upstream, limit) if limit is not None else None
