"""Shared HTTP error mapping for OpenAI-style providers (WP-7.2).

Providers send requests through their pooled client (``BaseProvider.http``)
and map failures to the provider exceptions callers already handle:
transport failures and timeouts become ``ConnectionError``; error statuses
become ``AuthenticationError``, ``ModelNotFoundError``, ``RateLimitError``,
a structured-mode rejection, or ``ConnectionError`` with the response body.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import httpx

from app.runtime.cancellation import OperationCancelled

from .base import AuthenticationError, ConnectionError, ModelNotFoundError, ProviderError
from .structured.transport import raise_if_structured_mode_rejected


@contextmanager
def transport_errors(target: str, *, timeout_target: str | None = None) -> Iterator[None]:
    """Map transport failures while sending a request to ``target``."""
    try:
        yield
    except (OperationCancelled, ProviderError, ConnectionError):
        raise
    except httpx.TimeoutException as exc:
        raise ConnectionError(f"Connection to {timeout_target or target} timed out: {exc}") from exc
    except httpx.TransportError as exc:
        raise ConnectionError(f"Failed to connect to {target}: {exc}") from exc
    except Exception as exc:
        raise ConnectionError(f"Unexpected error: {exc}") from exc


def raise_for_provider_status(response: httpx.Response) -> None:
    """Raise the provider exception for an error status; reads a streamed body."""
    if response.is_success:
        return
    status = response.status_code
    try:
        body = response.read().decode("utf-8", "replace")[:2000]
    except Exception:
        body = ""
    error = httpx.HTTPStatusError(
        f"{status} {response.reason_phrase} for {response.request.url}",
        request=response.request,
        response=response,
    )
    raise_if_structured_mode_rejected(status_code=status, response_body=body, error=error)
    if status in {401, 403}:
        raise AuthenticationError(f"Authentication failed: {error}") from error
    if status == 404:
        raise ModelNotFoundError(f"Resource not found: {error}") from error
    if status == 429:
        from .exceptions import RateLimitError

        raise RateLimitError(f"Rate limit exceeded: {error}") from error
    raise ConnectionError(f"HTTP error {status}: {error}; response_body={body}") from error


__all__ = ["raise_for_provider_status", "transport_errors"]
