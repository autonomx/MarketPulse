"""Email and Web Push senders for alert notifications.

Kept apart from ``alerts_notify`` (settings and subscriptions) so smtplib and the Web Push cryptography load with
the first notification sent, not at startup.
"""

from __future__ import annotations

import json
import logging
import smtplib
import socket
import ssl
import time
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import httpx

from app.config.env import env_str
from app.config.product import product_name
from app.security.url_policy import Resolver, UrlPolicyError, check_outbound_url, outbound_addresses, resolve_hostname

from .alerts_delivery import (
    DNS_TIMEOUT_SECONDS,
    SEND_DEADLINE_SECONDS,
    USER_AGENT,
    ClaimedDelivery,
    DeliveryResult,
    _ascii_url,
    _Deadline,
    _host_header,
    _pinned_url,
    bounded,
    post_within,
)
from .webpush import MAX_PLAINTEXT, encrypt_push, load_vapid_key, new_vapid_key, vapid_authorization

from .alerts_notify import (
    SMTP_TIMEOUT_SECONDS,
    PUSH_TTL_SECONDS,
    VAPID_SECRET,
    EmailSettings,
    NotificationSecretStore,
    NotificationSettingsRepository,
    ProtectedNotificationSecretStore,
    StoredSubscription,
    sender_settings_repository,
    smtp_password_secret,
)

logger = logging.getLogger(__name__)


# --- Email -------------------------------------------------------------------------------------------------------


class _PinnedSMTP(smtplib.SMTP):
    """SMTP to a checked address, keeping the hostname for STARTTLS certificate checks."""

    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        self._pinned = address
        super().__init__(timeout=timeout)
        self._host = hostname
        self.connect(hostname, port)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        return socket.create_connection((self._pinned, port), timeout)


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, hostname: str, address: str, port: int, timeout: float, context: ssl.SSLContext) -> None:
        self._pinned = address
        super().__init__(timeout=timeout, context=context)
        self._host = hostname
        self.connect(hostname, port)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        raw = socket.create_connection((self._pinned, port), timeout)
        return self.context.wrap_socket(raw, server_hostname=self._host)


SmtpFactory = Callable[[EmailSettings, str], smtplib.SMTP]


def default_smtp_factory(settings: EmailSettings, address: str) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if settings.security == "tls":
        return _PinnedSMTPSSL(settings.host, address, settings.port, SMTP_TIMEOUT_SECONDS, context)
    client = _PinnedSMTP(settings.host, address, settings.port, SMTP_TIMEOUT_SECONDS)
    if settings.security == "starttls":
        client.starttls(context=context)
    return client


def alert_email(settings: EmailSettings, message: str, *, alert_id: str = "", test: bool = False, event_kind: str = "alert") -> EmailMessage:
    first_line = (message.strip().splitlines() or [""])[0][:120]
    margin_call = event_kind == "margin_call"
    email = EmailMessage()
    product = product_name()
    email["Subject"] = f"{product} test notification" if test else f"{product} {'margin call' if margin_call else 'alert'}: {first_line or alert_id}"
    email["From"] = settings.from_address
    email["To"] = ", ".join(settings.to_addresses)
    if margin_call:
        # A paper margin call (TVP-7.2b): no alert behind it.
        email["X-Omnix-Event"] = "margin-call"
        email.set_content(f"{message.strip()}\n\n\u2014 {product} paper trading\n")
        return email
    email["X-Omnix-Alert"] = alert_id or "test"
    email.set_content(f"{message.strip()}\n\n— {product} alerts{f' ({alert_id})' if alert_id else ''}\n")
    return email


class EmailSender:
    """Sends a delivery by its workspace's SMTP settings."""

    def __init__(
        self,
        repository_factory: Callable[[], NotificationSettingsRepository] = sender_settings_repository,
        store: NotificationSecretStore | None = None,
        *,
        smtp_factory: SmtpFactory = default_smtp_factory,
        resolver: Resolver = resolve_hostname,
    ) -> None:
        self.repository_factory = repository_factory
        self.store = store or ProtectedNotificationSecretStore()
        self.smtp_factory = smtp_factory
        self.resolver = resolver

    def send(self, delivery: ClaimedDelivery) -> DeliveryResult:
        settings = self.repository_factory().email(delivery.workspace_id)
        if settings is None:
            return DeliveryResult("failed", "email_not_configured")
        email = alert_email(settings, delivery.message, alert_id=delivery.alert_id, event_kind=delivery.event_kind)
        return self.deliver(settings, delivery.workspace_id, email)

    def deliver(self, settings: EmailSettings, workspace_id: str, message: EmailMessage) -> DeliveryResult:
        password = None
        if settings.username:
            try:
                password = self.store.load(smtp_password_secret(workspace_id))
            except Exception:
                logger.warning("trading_alert_smtp_secret_unreadable workspace_id=%s", workspace_id)
                return DeliveryResult("retry", "secret_store_unavailable")
            if not password:
                return DeliveryResult("failed", "email_password_missing")
        try:
            host = f"[{settings.host.strip('[]')}]" if ":" in settings.host else settings.host
            addresses = outbound_addresses(f"https://{host}:{settings.port}/", strict=True, resolver=bounded(self.resolver, DNS_TIMEOUT_SECONDS))
        except _Deadline:
            return DeliveryResult("retry", "dns_timeout")
        except UrlPolicyError as exc:
            return DeliveryResult("retry", "dns_failed") if str(exc) == "hostname_resolution_failed" else DeliveryResult("failed", f"url_policy:{exc}")
        error = "connection_failed"
        for address in addresses[:3]:
            try:
                client = self.smtp_factory(settings, str(address))
            except (OSError, smtplib.SMTPException):
                continue
            try:
                if settings.username and password:
                    client.login(settings.username, password)
                client.send_message(message)
                return DeliveryResult("delivered")
            except smtplib.SMTPAuthenticationError:
                return DeliveryResult("failed", "smtp_auth_failed")
            except smtplib.SMTPRecipientsRefused:
                return DeliveryResult("failed", "smtp_recipients_refused")
            except smtplib.SMTPSenderRefused:
                return DeliveryResult("failed", "smtp_sender_refused")
            except smtplib.SMTPResponseException as exc:
                return DeliveryResult("failed" if exc.smtp_code >= 500 else "retry", f"smtp_{exc.smtp_code}")
            except (OSError, smtplib.SMTPException):
                error = "connection_failed"
            finally:
                try:
                    client.quit()
                except (OSError, smtplib.SMTPException):
                    pass
        return DeliveryResult("retry", error)


# --- Web push ----------------------------------------------------------------------------------------------------


def vapid_subject() -> str:
    """The contact push services may use about Omnix's pushes (``OMNIX_VAPID_SUBJECT``: mailto: or https:)."""
    subject = (env_str("OMNIX_VAPID_SUBJECT", "") or "").strip()
    return subject if subject.startswith(("mailto:", "https://")) else "mailto:alerts@omnix.invalid"


def vapid_key(store: NotificationSecretStore, *, create: bool) -> Any:
    """Omnix's VAPID key; made and stored on first use when ``create``. None without one (or without a store)."""
    pem = store.load(VAPID_SECRET)
    if not pem and create:
        if not store.available():
            return None
        pem = new_vapid_key()
        store.save(VAPID_SECRET, pem)
    return load_vapid_key(pem) if pem else None


def push_payload(message: str, *, alert_id: str = "", trigger_id: str = "", title: str | None = None) -> bytes:
    """What a browser shows: a title, the message (cut to fit a push) and where a click goes."""
    title = title or f"{product_name()} alert"
    body = message.strip()
    payload = {"title": title, "body": body, "alert_id": alert_id, "trigger_id": trigger_id, "url": "/trading" + (f"?alert={alert_id}" if alert_id else "")}
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    while len(encoded) > MAX_PLAINTEXT and payload["body"]:
        payload["body"] = payload["body"][: max(0, len(payload["body"]) - (len(encoded) - MAX_PLAINTEXT) - 1)].rstrip() + "…"
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return encoded


class PushSender:
    """Sends a delivery to every browser subscribed in its workspace."""

    def __init__(
        self,
        repository_factory: Callable[[], NotificationSettingsRepository] = sender_settings_repository,
        store: NotificationSecretStore | None = None,
        *,
        exchange: Callable[[httpx.Request, float], httpx.Response] = post_within,
        resolver: Resolver = resolve_hostname,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repository_factory = repository_factory
        self.store = store or ProtectedNotificationSecretStore()
        self.exchange = exchange
        self.resolver = resolver
        self.clock = clock

    def send(self, delivery: ClaimedDelivery) -> DeliveryResult:
        repository = self.repository_factory()
        subscriptions = repository.subscriptions(delivery.workspace_id)
        if not subscriptions:
            return DeliveryResult("failed", "no_push_subscriptions")
        title = f"{product_name()} {'margin call' if delivery.event_kind == 'margin_call' else 'alert'}"
        payload = push_payload(delivery.message, alert_id=delivery.alert_id, trigger_id=delivery.trigger_id, title=title)
        return self.deliver(repository, delivery.workspace_id, subscriptions, payload)

    def deliver(self, repository: NotificationSettingsRepository, workspace_id: str, subscriptions: list[StoredSubscription], payload: bytes) -> DeliveryResult:
        try:
            key = vapid_key(self.store, create=False)
        except Exception:
            logger.warning("trading_alert_vapid_key_unreadable")
            return DeliveryResult("retry", "secret_store_unavailable")
        if key is None:
            return DeliveryResult("failed", "push_not_configured")
        outcomes: list[DeliveryResult] = []
        for subscription in subscriptions:
            result = self._push(key, subscription, payload)
            if result.outcome == "delivered":
                repository.mark_delivered(subscription.subscription_id, workspace_id)
            elif result.error in ("subscription_gone",):
                repository.delete_subscription(subscription.subscription_id, workspace_id)
            outcomes.append(result)
        if any(item.outcome == "delivered" for item in outcomes):
            return DeliveryResult("delivered")
        retry = next((item for item in outcomes if item.outcome == "retry"), None)
        return retry or outcomes[-1]

    def _push(self, key: Any, subscription: StoredSubscription, payload: bytes) -> DeliveryResult:
        started = time.monotonic()
        try:
            url, hostname = _ascii_url(subscription.endpoint)
            check_outbound_url(url, strict=True, https_only=True)
            addresses = outbound_addresses(url, strict=True, resolver=bounded(self.resolver, DNS_TIMEOUT_SECONDS))
        except _Deadline:
            return DeliveryResult("retry", "dns_timeout")
        except (UnicodeError, ValueError):
            return DeliveryResult("failed", "url_policy:invalid_endpoint")
        except UrlPolicyError as exc:
            return DeliveryResult("retry", "dns_failed") if str(exc) == "hostname_resolution_failed" else DeliveryResult("failed", f"url_policy:{exc}")
        try:
            body = encrypt_push(payload, subscription.p256dh, subscription.auth)
        except ValueError:
            return DeliveryResult("failed", "subscription_gone")
        headers = {
            "Host": _host_header(url),
            "TTL": str(PUSH_TTL_SECONDS),
            "Urgency": "high",
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "User-Agent": USER_AGENT,
            "Authorization": vapid_authorization(url, key, vapid_subject(), now=self.clock()),
        }
        for address in addresses[:3]:
            remaining = SEND_DEADLINE_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                return DeliveryResult("retry", "timeout")
            request = httpx.Request(
                "POST", _pinned_url(url, address), content=body, headers=headers,
                extensions={"timeout": httpx.Timeout(remaining).as_dict(), "sni_hostname": hostname},
            )
            try:
                response = self.exchange(request, remaining)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                continue
            except httpx.HTTPError:
                return DeliveryResult("retry", "connection_failed")
            code = response.status_code
            if 200 <= code < 300:
                return DeliveryResult("delivered", status_code=code)
            if code in (404, 410):
                return DeliveryResult("failed", "subscription_gone", code)
            if code in (408, 429) or code >= 500:
                return DeliveryResult("retry", f"http_{code}", code)
            return DeliveryResult("failed", f"http_{code}", code)
        return DeliveryResult("retry", "connection_failed")


__all__ = [
    "EmailSender",
    "PushSender",
    "alert_email",
    "push_payload",
    "vapid_key",
]
