"""The market-data provider adapter interface (WP-8.3).

Every provider the registry builds implements ``MarketDataProvider``: its
identity and policy, binding resolution, and how it reports itself (display
name, whether it is configured, and its runtime status). Bars, quotes and
execution observations are dispatched by binding; the registry no longer
branches on provider names to describe them. ``ProviderAdapter`` supplies
the defaults for providers that call their upstream through
``ProviderHttpRuntime``.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.apps.trading.models import ProviderBinding, ProviderPolicy


@runtime_checkable
class MarketDataProvider(Protocol):
    provider_id: str
    policy: ProviderPolicy

    @property
    def display_name(self) -> str: ...

    def get_binding(self, instrument_id: str) -> ProviderBinding: ...

    def configured(self) -> bool: ...

    def runtime_status(self) -> tuple[str, dict[str, Any]]:
        """``(status, runtime diagnostics)``; the status ignores configuration."""
        ...


class ProviderAdapter:
    """Defaults for HTTP providers: configured, with the HTTP runtime's status."""

    provider_id: str
    runtime: Any = None

    @property
    def display_name(self) -> str:
        return self.provider_id.title()

    def configured(self) -> bool:
        return True

    def runtime_status(self) -> tuple[str, dict[str, Any]]:
        snapshot_method = getattr(self.runtime, "snapshot", None)
        snapshot = snapshot_method() if callable(snapshot_method) else None
        if snapshot is None:
            return "ready", {}
        return snapshot.status, {
            "request_count": snapshot.request_count,
            "success_count": snapshot.success_count,
            "failure_count": snapshot.failure_count,
            "consecutive_failures": snapshot.consecutive_failures,
            "rate_limit_count": snapshot.rate_limit_count,
            "in_flight": snapshot.in_flight,
            "max_concurrency": snapshot.max_concurrency,
            "circuit_open_count": getattr(snapshot, "circuit_open_count", 0),
            "circuit_suppression_count": getattr(snapshot, "circuit_suppression_count", 0),
            "circuit_open_until": getattr(snapshot, "circuit_open_until", None),
            "last_success_at": snapshot.last_success_at,
            "last_failure_at": snapshot.last_failure_at,
            "last_error": snapshot.last_error,
        }
