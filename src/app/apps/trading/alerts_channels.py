"""Alert notification channels: which ones have a sender, and webhook credentials (TVP-1.2).

``notification_channels`` accepts every channel the schema knows, but an alert
may only select a channel whose sender is deployed. TVP-0.5a (webhook),
TVP-0.5b (email) and TVP-0.5c (push) each add theirs to
``AVAILABLE_ALERT_CHANNELS`` without a migration. ``desktop`` (MarketPulse
S-6) is available only when the desktop shell configured its listener.

A webhook's URL and signing secret are credentials (chat webhooks embed their
token in the URL). Both live in the OS-protected secret store under a
versioned reference; PostgreSQL holds only the reference, a masked URL and
whether a secret is set.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Protocol
from urllib.parse import quote, urlsplit

from app.security.provider_secret_store import (
    delete_alert_webhooks,
    load_alert_webhook,
    save_alert_webhook,
)

AVAILABLE_ALERT_CHANNELS: frozenset[str] = frozenset({"app", "toast", "sound", "webhook", "email", "push"})


def available_alert_channels() -> frozenset[str]:
    """The channels an alert may select here: the deployed ones, plus ``desktop`` inside the desktop app."""
    from .alerts_desktop import desktop_notifications_configured

    return AVAILABLE_ALERT_CHANNELS | {"desktop"} if desktop_notifications_configured() else AVAILABLE_ALERT_CHANNELS


def unavailable_channels(channels: Iterable[str], available: Iterable[str] = AVAILABLE_ALERT_CHANNELS) -> list[str]:
    allowed = set(available)
    return [channel for channel in dict.fromkeys(channels) if channel not in allowed]


def mask_webhook_url(url: str) -> str:
    """Scheme and host only: the path and query of a webhook URL may hold its token."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{parts.scheme}://{host}/…" if parts.scheme and host else "…"


def alert_webhook_prefix(workspace_id: str, alert_id: str) -> str:
    """The reference prefix of one alert's webhooks.

    Both parts are percent-encoded, so neither can contain the separator and
    one alert's prefix never matches another alert's references.
    """
    return f"{quote(workspace_id, safe='')}/{quote(alert_id, safe='')}/"


class AlertWebhookStore(Protocol):
    def available(self) -> bool: ...
    def load(self, ref: str) -> dict[str, str] | None: ...
    def save(self, ref: str, url: str, secret: str) -> None: ...
    def delete(self, ref: str) -> None: ...
    def delete_alert(self, workspace_id: str, alert_id: str, *, keep: str | None = None) -> None: ...


class ProtectedAlertWebhookStore:
    """Alert webhooks in the user's OS-protected provider secret store, never in PostgreSQL."""

    def available(self) -> bool:
        # The store is DPAPI-backed; elsewhere saving a webhook fails closed.
        return sys.platform == "win32"

    def load(self, ref: str) -> dict[str, str] | None:
        return load_alert_webhook(ref)

    def save(self, ref: str, url: str, secret: str) -> None:
        save_alert_webhook(ref, url, secret)

    def delete(self, ref: str) -> None:
        delete_alert_webhooks([ref])

    def delete_alert(self, workspace_id: str, alert_id: str, *, keep: str | None = None) -> None:
        delete_alert_webhooks(prefix=alert_webhook_prefix(workspace_id, alert_id), keep=keep)


__all__ = [
    "AVAILABLE_ALERT_CHANNELS",
    "available_alert_channels",
    "AlertWebhookStore",
    "ProtectedAlertWebhookStore",
    "alert_webhook_prefix",
    "mask_webhook_url",
    "unavailable_channels",
]
