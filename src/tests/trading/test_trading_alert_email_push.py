"""Alert email (TVP-0.5b) and web push (TVP-0.5c): encryption, senders and settings API."""

from __future__ import annotations

import json
import smtplib
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.alerts_delivery import ClaimedDelivery
from app.apps.trading.alerts_notify import (
    EmailSettings,
    PushSubscriptionWrite,
    StoredSubscription,
    smtp_password_secret,
)
from app.apps.trading.alerts_notify_senders import EmailSender, PushSender, push_payload
from app.apps.trading.alerts_notify_api import create_trading_notification_router
from app.apps.trading.webpush import b64url, b64url_decode, decrypt_push, encrypt_push, new_vapid_key, load_vapid_key, vapid_authorization
from app.runtime.tenant_context import TenantContext, pop_tenant, push_tenant

PUBLIC = lambda host, port: ["93.184.216.34"]  # noqa: E731 - a public address for every host


class Store:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def available(self) -> bool:
        return True

    def load(self, name: str) -> str | None:
        return self.values.get(name)

    def save(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class Settings:
    """NotificationSettingsRepository in memory, for one workspace."""

    def __init__(self) -> None:
        self.email_settings: EmailSettings | None = None
        self.subs: dict[str, StoredSubscription] = {}
        self.delivered: list[str] = []

    def email(self, workspace_id=None):
        return self.email_settings

    def save_email(self, settings):
        self.email_settings = settings

    def subscriptions(self, workspace_id=None, *, user_id=None):
        return [sub for sub in self.subs.values() if user_id is None or sub.user_id == user_id]

    def listed_subscriptions(self):
        from datetime import datetime, timezone

        from app.apps.trading.alerts_notify import PushSubscription

        return [PushSubscription(subscription_id=sub.subscription_id, user_id=sub.user_id, service="push.example", user_agent="", created_at=datetime.now(timezone.utc)) for sub in self.subs.values()]

    def save_subscription(self, user_id: str, subscription: PushSubscriptionWrite) -> str:
        subscription_id = f"sub{len(self.subs) + 1}"
        self.subs[subscription_id] = StoredSubscription(subscription_id, user_id, subscription.endpoint, subscription.keys.p256dh, subscription.keys.auth)
        return subscription_id

    def delete_subscription(self, subscription_id, workspace_id=None):
        return self.subs.pop(subscription_id, None) is not None

    def mark_delivered(self, subscription_id, workspace_id=None):
        self.delivered.append(subscription_id)


def browser():
    """A browser's subscription keys: its private key (to read pushes) and what it sends Omnix."""
    key = ec.generate_private_key(ec.SECP256R1())
    from cryptography.hazmat.primitives import serialization

    point = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, b64url(point), b64url(b"0123456789abcdef")


def delivery(channel: str, message: str = "AAPL crossing 200") -> ClaimedDelivery:
    return ClaimedDelivery("d1", "t1", "a1", channel, message, 1, 8, None, "ws1")


def test_push_messages_are_encrypted_per_rfc_8291() -> None:
    sender = ec.derive_private_key(int.from_bytes(b64url_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = encrypt_push(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg", salt=b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"), sender_key=sender,
    )
    # RFC 8291, Appendix A.
    assert b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6c"
        "VPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_vapid_authorization_is_an_es256_jwt_for_the_endpoint_origin() -> None:
    key = load_vapid_key(new_vapid_key())
    header = vapid_authorization("https://fcm.googleapis.com/fcm/send/abc", key, "mailto:me@example.com", now=1_000)
    token, public = header.removeprefix("vapid t=").split(", k=")
    head, claims, signature = token.split(".")
    assert json.loads(b64url_decode(claims)) == {"aud": "https://fcm.googleapis.com", "exp": 1_000 + 12 * 3600, "sub": "mailto:me@example.com"}
    raw = b64url_decode(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64url_decode(public)).verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


class FakeSmtp:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple] = []

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if isinstance(self.fail, smtplib.SMTPAuthenticationError):
            raise self.fail

    def send_message(self, message):
        self.calls.append(("send", message["Subject"], message["To"], message.get_content()))
        if self.fail and not isinstance(self.fail, smtplib.SMTPAuthenticationError):
            raise self.fail

    def quit(self):
        self.calls.append(("quit",))


def test_email_goes_to_the_checked_server_with_the_stored_password() -> None:
    settings, store, smtp = Settings(), Store(), FakeSmtp()
    connected: list[tuple[str, str]] = []
    settings.email_settings = EmailSettings(host="smtp.example.com", username="me", from_address="me@example.com", to_addresses=["you@example.com"], has_password=True)
    store.save(smtp_password_secret("ws1"), "pw")

    def factory(email, address):
        connected.append((email.host, address))
        return smtp

    sender = EmailSender(lambda: settings, store, smtp_factory=factory, resolver=PUBLIC)
    assert sender.send(delivery("email")).outcome == "delivered"
    assert connected == [("smtp.example.com", "93.184.216.34")]
    login, send, _quit = smtp.calls
    assert login == ("login", "me", "pw") and send[1] == "Omnix alert: AAPL crossing 200" and "AAPL crossing 200" in send[3]
    # Refused or misconfigured: failed; unreachable: retried.
    assert EmailSender(lambda: settings, store, smtp_factory=lambda *_: FakeSmtp(smtplib.SMTPAuthenticationError(535, b"no")), resolver=PUBLIC).send(delivery("email")).error == "smtp_auth_failed"
    assert EmailSender(lambda: settings, store, smtp_factory=lambda *_: FakeSmtp(smtplib.SMTPResponseException(451, b"later")), resolver=PUBLIC).send(delivery("email")).outcome == "retry"
    def unreachable(*_):
        raise OSError("refused")
    assert EmailSender(lambda: settings, store, smtp_factory=unreachable, resolver=PUBLIC).send(delivery("email")).outcome == "retry"
    private = EmailSender(lambda: settings, store, smtp_factory=factory, resolver=lambda host, port: ["10.0.0.5"]).send(delivery("email"))
    assert private.outcome == "failed" and private.error.startswith("url_policy:")
    store.delete(smtp_password_secret("ws1"))
    assert sender.send(delivery("email")).error == "email_password_missing"
    settings.email_settings = None
    assert sender.send(delivery("email")).error == "email_not_configured"


def test_push_reaches_each_browser_and_drops_gone_subscriptions() -> None:
    settings, store = Settings(), Store()
    store.save("webpush/vapid_private_key", new_vapid_key())
    reader, p256dh, auth = browser()
    for endpoint in ("https://push.example/live", "https://push.example/gone"):
        settings.save_subscription("u1", PushSubscriptionWrite(endpoint=endpoint, keys={"p256dh": p256dh, "auth": auth}))
    sent: list[httpx.Request] = []

    def exchange(request: httpx.Request, seconds: float) -> httpx.Response:
        sent.append(request)
        return httpx.Response(410 if request.url.path == "/gone" else 201)

    result = PushSender(lambda: settings, store, exchange=exchange, resolver=PUBLIC).send(delivery("push", "AAPL crossing 200"))
    assert result.outcome == "delivered" and list(settings.subs) == ["sub1"] and settings.delivered == ["sub1"]
    first = sent[0]
    assert first.url.host == "93.184.216.34" and first.headers["Host"] == "push.example" and first.headers["Content-Encoding"] == "aes128gcm"
    assert first.headers["Authorization"].startswith("vapid t=")
    shown = json.loads(decrypt_push(first.content, reader, auth))
    assert shown == {"title": "Omnix alert", "body": "AAPL crossing 200", "alert_id": "a1", "trigger_id": "t1", "url": "/trading?alert=a1"}
    # Every browser failing temporarily: retried; none subscribed: failed.
    busy = PushSender(lambda: settings, store, exchange=lambda request, seconds: httpx.Response(503), resolver=PUBLIC).send(delivery("push"))
    assert busy.outcome == "retry"
    assert PushSender(lambda: Settings(), store, exchange=exchange, resolver=PUBLIC).send(delivery("push")).error == "no_push_subscriptions"
    encoded = push_payload("x" * 10_000)
    assert len(encoded) <= 3993 and json.loads(encoded)["body"].endswith("…")


def test_the_settings_api_keeps_secrets_out_of_responses() -> None:
    settings, store = Settings(), Store()
    token = push_tenant(TenantContext(user_id="u1", workspace_id="ws1", membership_id="m1", roles=frozenset({"owner"})))
    try:
        sent: list[httpx.Request] = []
        push = PushSender(lambda: settings, store, exchange=lambda request, seconds: (sent.append(request), httpx.Response(201))[1], resolver=PUBLIC)
        smtp = FakeSmtp()
        email = EmailSender(lambda: settings, store, smtp_factory=lambda *_: smtp, resolver=PUBLIC)
        app = FastAPI()
        app.include_router(create_trading_notification_router(lambda: settings, store, email_sender=email, push_sender=push))
        client = TestClient(app)
        body = {"host": "SMTP.Example.com", "port": 587, "username": "me", "password": "pw", "from_address": "me@example.com", "to_addresses": ["you@example.com"]}
        saved = client.put("/api/trading/notifications/email", json=body).json()
        assert saved["settings"]["host"] == "smtp.example.com" and saved["settings"]["has_password"] is True and "pw" not in json.dumps(saved)
        assert store.values == {"smtp/ws1/password": "pw"}
        # Without a new password the stored one stays.
        assert client.put("/api/trading/notifications/email", json={**body, "password": None}).json()["settings"]["has_password"] is True
        assert client.put("/api/trading/notifications/email", json={**body, "security": "none"}).status_code == 422
        assert client.post("/api/trading/notifications/email/test").json() == {"outcome": "delivered", "error": None}
        assert smtp.calls[1][1] == "Omnix test notification"

        key = client.get("/api/trading/notifications/push").json()
        assert key["available"] and len(b64url_decode(key["public_key"])) == 65 and "webpush/vapid_private_key" in store.values
        assert client.post("/api/trading/notifications/push/test").status_code == 422
        _reader, p256dh, auth = browser()
        created = client.post("/api/trading/notifications/push/subscriptions", json={"endpoint": "https://push.example/x", "keys": {"p256dh": p256dh, "auth": auth}})
        assert created.status_code == 201
        listed = client.get("/api/trading/notifications/push").json()["subscriptions"]
        assert listed[0]["service"] == "push.example" and "endpoint" not in listed[0]
        assert client.post("/api/trading/notifications/push/test").json()["outcome"] == "delivered" and len(sent) == 1
        assert client.delete(f"/api/trading/notifications/push/subscriptions/{created.json()['subscription_id']}").status_code == 204
        assert client.post("/api/trading/notifications/push/subscriptions", json={"endpoint": "http://push.example/x", "keys": {"p256dh": p256dh, "auth": auth}}).status_code == 422
        assert client.delete("/api/trading/notifications/email").status_code == 204 and store.values.get("smtp/ws1/password") is None
    finally:
        pop_tenant(token)


def test_alerts_can_choose_email_and_push_only_once_they_are_set_up() -> None:
    from app.apps.trading.alerts_api import create_trading_alert_router

    settings = Settings()
    repository = SimpleNamespace()
    app = FastAPI()
    app.include_router(create_trading_alert_router(lambda: repository, notification_settings_factory=lambda: settings))
    client = TestClient(app)
    body = {"alert_id": "a", "instrument_id": "equity:X:Y", "condition_type": "price_above", "threshold": "1",
            "parameters": {"notification_channels": ["app", "email"]}, "evaluation_policy": {"interval": "1m"}}
    assert "set up email" in client.post("/api/trading/alerts", json=body).text
    body["parameters"]["notification_channels"] = ["push"]
    assert "turn on notifications" in client.post("/api/trading/alerts", json=body).text


@pytest.fixture(autouse=True)
def _no_private_networks(monkeypatch):
    monkeypatch.delenv("OMNIX_ALLOWED_PRIVATE_NETWORKS", raising=False)
