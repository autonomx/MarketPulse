"""Alert emails and notifications use the configured product name (MarketPulse S-3)."""
from __future__ import annotations

import json

from app.apps.trading.alerts_notify_senders import alert_email, push_payload
from app.config.product import product_name


class _Settings:
    from_address = "alerts@example.test"
    to_addresses = ("me@example.test",)


def test_product_name_defaults_to_omnix(monkeypatch):
    monkeypatch.delenv("OMNIX_PRODUCT_NAME", raising=False)

    assert product_name() == "Omnix"
    assert alert_email(_Settings(), "AAPL crossed 200", alert_id="a1")["Subject"] == "Omnix alert: AAPL crossed 200"
    assert json.loads(push_payload("AAPL crossed 200"))["title"] == "Omnix alert"


def test_alert_text_uses_the_configured_product_name(monkeypatch):
    monkeypatch.setenv("OMNIX_PRODUCT_NAME", "MarketPulse")

    email = alert_email(_Settings(), "AAPL crossed 200", alert_id="a1")
    assert email["Subject"] == "MarketPulse alert: AAPL crossed 200"
    assert "— MarketPulse alerts (a1)" in email.get_content()
    margin = alert_email(_Settings(), "Margin call", event_kind="margin_call")
    assert margin["Subject"] == "MarketPulse margin call: Margin call"
    assert "MarketPulse paper trading" in margin.get_content()
    assert alert_email(_Settings(), "x", test=True)["Subject"] == "MarketPulse test notification"
    assert json.loads(push_payload("AAPL crossed 200"))["title"] == "MarketPulse alert"
    assert json.loads(push_payload("x", title="Custom"))["title"] == "Custom"
