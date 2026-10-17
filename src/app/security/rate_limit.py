"""In-process token-bucket rate limits (WP-4.10).

Each gateway process limits independently; the ingress adds a global limit
(WP-11.3). Buckets live on ``app.state``, are bounded and expire idle keys.

- ``login``: 5 attempts per minute per client address
  (``OMNIX_LOGIN_RATE_LIMIT_PER_MINUTE``);
- ``approvals``: approve, deny and execute calls, 60 per minute per caller
  (``OMNIX_APPROVAL_RATE_LIMIT_PER_MINUTE``);
- ``client_errors``: browser error reports, 30 per minute per caller
  (``OMNIX_CLIENT_ERROR_RATE_LIMIT_PER_MINUTE``).
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Literal

from fastapi import HTTPException, Request

from app.config.env import env_str
from app.observability.metrics import record_rate_limit_rejection

MAX_KEYS = 10_000
_LIMITS: MappingProxyType[str, tuple[str, int]] = MappingProxyType({
    "login": ("OMNIX_LOGIN_RATE_LIMIT_PER_MINUTE", 5),
    "approvals": ("OMNIX_APPROVAL_RATE_LIMIT_PER_MINUTE", 60),
    "client_errors": ("OMNIX_CLIENT_ERROR_RATE_LIMIT_PER_MINUTE", 30),
})


class TokenBucket:
    """``per_minute`` requests per minute per key, up to ``per_minute`` at once."""

    def __init__(self, per_minute: int, *, max_keys: int = MAX_KEYS, clock: Callable[[], float] = time.monotonic) -> None:
        if per_minute < 1:
            raise ValueError("rate limit must be at least 1 per minute")
        self.capacity = float(per_minute)
        self.refill_per_second = per_minute / 60.0
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def take(self, key: str) -> float:
        """0 when allowed, else seconds until the next request is allowed."""
        now = self.clock()
        tokens, stamp = self._buckets.pop(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - stamp) * self.refill_per_second)
        if tokens >= 1:
            tokens -= 1
            wait = 0.0
        else:
            wait = (1 - tokens) / self.refill_per_second
        self._buckets[key] = (tokens, now)
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)
        return wait


def _per_minute(name: str) -> int:
    variable, default = _LIMITS[name]
    raw = (env_str(variable, "") or "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{variable} must be at least 1")
    return value


def _bucket(request: Request, name: str) -> TokenBucket:
    state = request.app.state
    buckets = getattr(state, "rate_limit_buckets", None)
    if buckets is None:
        buckets = {}
        state.rate_limit_buckets = buckets
    if name not in buckets:
        buckets[name] = TokenBucket(_per_minute(name))
    return buckets[name]


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _caller_key(request: Request) -> str:
    from app.runtime.tenant_context import current_tenant

    try:
        context = current_tenant()
        return f"{context.workspace_id}:{context.user_id}"
    except RuntimeError:
        return _client_key(request)


def rate_limited(name: Literal["login", "approvals", "client_errors"]) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency: refuse with 429 and ``Retry-After`` when exhausted."""
    key_of = _client_key if name == "login" else _caller_key

    async def dependency(request: Request) -> None:
        wait = _bucket(request, name).take(key_of(request))
        if wait > 0:
            record_rate_limit_rejection(name)
            raise HTTPException(
                status_code=429,
                detail={"error": "rate_limited", "limit": name},
                headers={"Retry-After": str(math.ceil(wait))},
            )

    dependency.__name__ = f"rate_limited_{name}"
    return dependency


__all__ = ["TokenBucket", "rate_limited"]
