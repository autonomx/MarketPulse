"""Desktop notifications for alerts and margin calls (MarketPulse S-6)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.apps.trading.alerts_channels import AVAILABLE_ALERT_CHANNELS, available_alert_channels
from app.apps.trading.alerts_delivery import ClaimedDelivery, NotificationDeliveryMonitor
from app.apps.trading.alerts_desktop import DesktopNotifyTarget, DesktopSender, desktop_notify_target, desktop_payload

URL = "http://127.0.0.1:47811/notify"


def _delivery(**changes) -> ClaimedDelivery:
    values = dict(
        delivery_id="d1", trigger_id="t1", alert_id="a1", channel="desktop", message="AAPL crossed 200",
        attempt=1, max_attempts=8, webhook_ref=None, workspace_id="workspace:local", event_kind="alert",
        instrument_id="equity:NASDAQ:AAPL", created_at=datetime(2026, 10, 17, 13, 30, tzinfo=timezone.utc),
    )
    values.update(changes)
    return ClaimedDelivery(**values)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("OMNIX_TRADING_DESKTOP_NOTIFY_URL", URL)
    monkeypatch.setenv("OMNIX_TRADING_DESKTOP_NOTIFY_SECRET", "s3cret")


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.delenv("OMNIX_TRADING_DESKTOP_NOTIFY_URL", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_DESKTOP_NOTIFY_SECRET", raising=False)


def test_desktop_channel_exists_only_when_the_shell_configured_it(unconfigured, monkeypatch):
    assert desktop_notify_target() is None
    assert available_alert_channels() == AVAILABLE_ALERT_CHANNELS
    assert "desktop" not in NotificationDeliveryMonitor().senders

    monkeypatch.setenv("OMNIX_TRADING_DESKTOP_NOTIFY_URL", URL)
    monkeypatch.setenv("OMNIX_TRADING_DESKTOP_NOTIFY_SECRET", "s3cret")
    assert desktop_notify_target() == DesktopNotifyTarget(URL, "s3cret")
    assert "desktop" in available_alert_channels()
    assert isinstance(NotificationDeliveryMonitor().senders["desktop"], DesktopSender)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:47811/notify",
    "http://192.168.1.20:47811/notify",
    "http://example.com/notify",
    "http://user:pw@127.0.0.1:47811/notify",
])
def test_only_a_plain_loopback_listener_is_accepted(monkeypatch, url):
    monkeypatch.setenv("OMNIX_TRADING_DESKTOP_NOTIFY_URL", url)
    monkeypatch.setenv("OMNIX_TRADING_DESKTOP_NOTIFY_SECRET", "s3cret")

    assert desktop_notify_target() is None


def test_payload_carries_what_the_shell_shows_without_calling_back(monkeypatch):
    monkeypatch.setenv("OMNIX_PRODUCT_NAME", "MarketPulse")

    payload = desktop_payload(_delivery())

    assert payload == {
        "delivery_id": "d1", "event_kind": "alert", "workspace_id": "workspace:local",
        "title": "MarketPulse alert", "message": "AAPL crossed 200", "alert_id": "a1", "trigger_id": "t1",
        "instrument_id": "equity:NASDAQ:AAPL", "created_at": "2026-10-17T13:30:00+00:00", "url": "/trading?alert=a1",
    }
    margin = desktop_payload(_delivery(event_kind="margin_call", alert_id="", trigger_id="", instrument_id=""))
    assert (margin["title"], margin["alert_id"], margin["url"]) == ("MarketPulse margin call", None, "/trading")


def test_sender_delivers_retries_and_fails_by_the_listener_answer(configured):
    calls = []

    def post(status):
        def call(url, payload, secret):
            calls.append((url, payload, secret))
            if isinstance(status, Exception):
                raise status
            return status
        return call

    assert DesktopSender(post=post(200)).send(_delivery()).outcome == "delivered"
    url, payload, secret = calls[-1]
    assert (url, secret) == (URL, "s3cret")
    assert "s3cret" not in repr(payload)
    assert DesktopSender(post=post(ConnectionRefusedError())).send(_delivery()).outcome == "retry"
    assert DesktopSender(post=post(503)).send(_delivery()).outcome == "retry"
    assert DesktopSender(post=post(429)).send(_delivery()).outcome == "retry"
    failed = DesktopSender(post=post(401)).send(_delivery())
    assert (failed.outcome, failed.error) == ("failed", "desktop_status:401")


def test_sender_fails_when_the_listener_went_away(unconfigured):
    result = DesktopSender().send(_delivery())

    assert (result.outcome, result.error) == ("failed", "desktop_unavailable")


def test_margin_calls_use_the_desktop_channel_inside_the_desktop_app(configured):
    from app.apps.trading.paper_margin_notifications import PaperMarginCallNotifier

    class _Settings:
        def email(self):
            return None

        def subscriptions(self):
            return []

    notifier = PaperMarginCallNotifier(settings_factory=_Settings)
    assert notifier.channels() == ["desktop"]
