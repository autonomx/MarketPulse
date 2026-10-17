"""Desktop notifications for alerts and margin calls (MarketPulse S-6).

The desktop shell runs a notification listener on loopback. When the shell
starts the gateway it sets ``OMNIX_TRADING_DESKTOP_NOTIFY_URL`` (that
listener) and ``OMNIX_TRADING_DESKTOP_NOTIFY_SECRET`` (a bearer secret shared
with it). With both set, ``desktop`` is an alert channel and the outbox's
delivery monitor sends ``desktop`` rows to the listener like any other
channel: the listener answers 200 once the notification is shown, and lease,
attempt fencing, backoff and ``max_attempts`` stay the outbox's. Without them
the channel does not exist, so Omnix in a browser is unchanged.

The URL is never checked against the webhook URL policy (which refuses
loopback): it is fixed by the shell, and only an ``http`` URL on a loopback
host is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlsplit

from app.config.env import env_str
from app.config.product import product_name

if TYPE_CHECKING:
    from .alerts_delivery import ClaimedDelivery, DeliveryResult

DESKTOP_NOTIFY_URL_ENV = "OMNIX_TRADING_DESKTOP_NOTIFY_URL"
DESKTOP_NOTIFY_SECRET_ENV = "OMNIX_TRADING_DESKTOP_NOTIFY_SECRET"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SEND_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class DesktopNotifyTarget:
    url: str
    secret: str


def desktop_notify_target() -> DesktopNotifyTarget | None:
    """The shell's listener, or None when desktop notifications are not configured (or not on loopback)."""
    url = (env_str(DESKTOP_NOTIFY_URL_ENV, "") or "").strip()
    secret = (env_str(DESKTOP_NOTIFY_SECRET_ENV, "") or "").strip()
    if not url or not secret:
        return None
    parts = urlsplit(url)
    if parts.scheme != "http" or (parts.hostname or "") not in _LOOPBACK_HOSTS or parts.username or parts.password:
        return None
    return DesktopNotifyTarget(url=url, secret=secret)


def desktop_notifications_configured() -> bool:
    return desktop_notify_target() is not None


def desktop_payload(delivery: ClaimedDelivery) -> dict[str, Any]:
    """What the shell needs to show, place and route one notification without calling back."""
    margin_call = delivery.event_kind == "margin_call"
    url = "/trading"
    if delivery.alert_id:
        url += f"?alert={delivery.alert_id}"
    return {
        "delivery_id": delivery.delivery_id,
        "event_kind": delivery.event_kind,
        "workspace_id": delivery.workspace_id,
        "title": f"{product_name()} {'margin call' if margin_call else 'alert'}",
        "message": delivery.message,
        "alert_id": delivery.alert_id or None,
        "trigger_id": delivery.trigger_id or None,
        "instrument_id": delivery.instrument_id or None,
        "created_at": delivery.created_at.isoformat() if delivery.created_at else None,
        "url": url,
    }


def _post(url: str, payload: dict[str, Any], secret: str) -> int:
    import httpx

    response = httpx.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {secret}"},
        timeout=SEND_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    )
    return response.status_code


class DesktopSender:
    """Sends one delivery to the desktop shell's notification listener."""

    def __init__(
        self,
        *,
        target: Callable[[], DesktopNotifyTarget | None] = desktop_notify_target,
        post: Callable[[str, dict[str, Any], str], int] = _post,
    ) -> None:
        self.target = target
        self.post = post

    def send(self, delivery: ClaimedDelivery) -> DeliveryResult:
        from .alerts_delivery import DeliveryResult

        target = self.target()
        if target is None:
            return DeliveryResult("failed", "desktop_unavailable")
        try:
            status = self.post(target.url, desktop_payload(delivery), target.secret)
        except Exception as exc:
            # The shell is starting, sleeping or busy: try again on the outbox's backoff.
            return DeliveryResult("retry", f"desktop_unreachable:{type(exc).__name__}")
        if 200 <= status < 300:
            return DeliveryResult("delivered", status_code=status)
        if status in {408, 425, 429} or status >= 500:
            return DeliveryResult("retry", f"desktop_status:{status}", status_code=status)
        return DeliveryResult("failed", f"desktop_status:{status}", status_code=status)


__all__ = [
    "DESKTOP_NOTIFY_SECRET_ENV",
    "DESKTOP_NOTIFY_URL_ENV",
    "DesktopNotifyTarget",
    "DesktopSender",
    "desktop_notifications_configured",
    "desktop_notify_target",
    "desktop_payload",
]
