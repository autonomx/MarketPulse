"""Fake HTTP for code that uses pooled clients (WP-7.2)."""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from app.runtime.http_client import HttpPolicy, PooledHttpClient

Handler = Callable[[httpx.Request], httpx.Response]


def mock_http_client(handler: Handler, name: str = "test", *, retries: int = 0) -> PooledHttpClient:
    """A pooled client whose requests go to ``handler`` instead of the network."""
    return PooledHttpClient(name, HttpPolicy(max_retries=retries, backoff_seconds=0.001), transport=httpx.MockTransport(handler))


def install_provider_http(provider: Any, handler: Handler) -> PooledHttpClient:
    """Route a provider's ``http`` client to ``handler``."""
    client = mock_http_client(handler, getattr(provider, "provider_name", "test"))
    provider.__dict__["_http_client"] = client
    return client


def request_json(request: httpx.Request) -> Any:
    return json.loads(request.content or b"null")


def read_timeout(request: httpx.Request) -> float | None:
    return request.extensions.get("timeout", {}).get("read")
