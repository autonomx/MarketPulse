"""Alert email (TVP-0.5b) and web-push (TVP-0.5c) delivery: settings, subscriptions and the two senders.

Both run behind the notification outbox (``alerts_delivery.py``): the monitor claims a delivery and hands it to the
sender of its channel, which returns delivered, retry or failed.

- **Email:** a workspace's SMTP server (STARTTLS or TLS; plain only without a login), sender and up to five
  recipients. The password lives in the OS-protected secret store, never in PostgreSQL. The server's address is
  checked like a webhook's (no private, loopback or link-local address unless ``OMNIX_ALLOWED_PRIVATE_NETWORKS``
  allows it) and the connection goes to that checked address.
- **Web push:** each browser that allows notifications subscribes with Omnix's VAPID key (made once, kept in the
  secret store) and stores its subscription per user and device. A push carries the alert's message, encrypted for
  that browser (``webpush.py``); a subscription the push service says is gone (404, 410) is removed.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.persistence.tenant_scope import system_scope
from app.persistence.unit_of_work import unit_of_work
from app.security.provider_secret_store import (
    delete_alert_notification_secret,
    load_alert_notification_secret,
    save_alert_notification_secret,
)
from app.security.tenant_context import RequestTenant, TenantContext

from .alerts_delivery import (
    SYSTEM_OPERATION,
)

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"^[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+$")
SMTP_TIMEOUT_SECONDS = 10.0
VAPID_SECRET = "webpush/vapid_private_key"
# Push messages live this long at the push service while a device is offline.
PUSH_TTL_SECONDS = 24 * 3600
MAX_SUBSCRIPTIONS_PER_USER = 20


def smtp_password_secret(workspace_id: str) -> str:
    return f"smtp/{workspace_id}/password"


class NotificationSecretStore(Protocol):
    def available(self) -> bool: ...
    def load(self, name: str) -> str | None: ...
    def save(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class ProtectedNotificationSecretStore:
    """Delivery credentials in the user's OS-protected provider secret store (DPAPI), like webhooks."""

    def available(self) -> bool:
        import sys

        return sys.platform == "win32"

    def load(self, name: str) -> str | None:
        return load_alert_notification_secret(name)

    def save(self, name: str, value: str) -> None:
        save_alert_notification_secret(name, value)

    def delete(self, name: str) -> None:
        delete_alert_notification_secret(name)


# --- Settings ----------------------------------------------------------------------------------------------------


class EmailSettings(BaseModel):
    """A workspace's SMTP server, as stored and shown (the password is only ``has_password``)."""

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "tls", "none"] = "starttls"
    username: str = Field(default="", max_length=320)
    from_address: str = Field(min_length=3, max_length=320)
    to_addresses: list[str] = Field(min_length=1, max_length=5)
    has_password: bool = False

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        host = value.strip().lower().rstrip(".")
        if not re.fullmatch(r"[a-z0-9.\-]+|\[[0-9a-f:]+\]", host):
            raise ValueError("the SMTP host is a hostname or an IP address")
        return host

    @field_validator("from_address")
    @classmethod
    def _from(cls, value: str) -> str:
        if not EMAIL_PATTERN.match(value.strip()):
            raise ValueError("the sender is an email address")
        return value.strip()

    @field_validator("to_addresses")
    @classmethod
    def _to(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not EMAIL_PATTERN.match(value) for value in cleaned):
            raise ValueError("each recipient is an email address")
        return list(dict.fromkeys(cleaned))


class EmailSettingsWrite(EmailSettings):
    """``password``: None keeps the stored one, an empty string removes it."""

    password: str | None = Field(default=None, max_length=500, exclude=True)
    has_password: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def _login_needs_tls(self) -> EmailSettingsWrite:
        if self.username and self.security == "none":
            raise ValueError("a login needs STARTTLS or TLS; a password is never sent in the clear")
        return self


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=120)
    auth: str = Field(min_length=16, max_length=64)


class PushSubscriptionWrite(BaseModel):
    endpoint: str = Field(min_length=12, max_length=2000)
    keys: PushSubscriptionKeys
    user_agent: str = Field(default="", max_length=300)


class PushSubscription(BaseModel):
    """A subscription as the API shows it: no endpoint or keys, only the push service's host."""

    subscription_id: str
    user_id: str
    service: str
    user_agent: str
    created_at: datetime
    last_success_at: datetime | None = None


@dataclass(frozen=True)
class StoredSubscription:
    subscription_id: str
    user_id: str
    endpoint: str
    p256dh: str
    auth: str


class NotificationSettingsRepository:
    """Email settings and push subscriptions; ``all_workspaces`` (the senders) reads any workspace by id."""

    context = RequestTenant()

    def __init__(self, *, context: TenantContext | None = None, uow_factory: Callable[[], Any] = unit_of_work, all_workspaces: bool = False) -> None:
        self.context = context
        self.uow_factory = uow_factory
        self.all_workspaces = all_workspaces

    @contextmanager
    def _work(self) -> Iterator[Any]:
        with system_scope(SYSTEM_OPERATION) if self.all_workspaces else nullcontext(), self.uow_factory() as uow:
            yield uow

    def _workspace(self, workspace_id: str | None) -> str:
        return workspace_id if self.all_workspaces and workspace_id else self.context.workspace_id

    def email(self, workspace_id: str | None = None) -> EmailSettings | None:
        with self._work() as uow:
            row = uow.connection.execute(
                "SELECT email FROM omnix_trading_notification_settings WHERE workspace_id = %s", (self._workspace(workspace_id),),
            ).fetchone()
        return EmailSettings.model_validate(row[0]) if row and row[0] else None

    def save_email(self, settings: EmailSettings | None) -> None:
        payload = json.dumps(settings.model_dump(mode="json")) if settings is not None else None
        with self._work() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_notification_settings (workspace_id, email) VALUES (%s, %s::jsonb)
                ON CONFLICT (workspace_id) DO UPDATE SET email = EXCLUDED.email, updated_at = CURRENT_TIMESTAMP
                """,
                (self._workspace(None), payload),
            )
            uow.commit()

    def subscriptions(self, workspace_id: str | None = None, *, user_id: str | None = None) -> list[StoredSubscription]:
        with self._work() as uow:
            rows = uow.connection.execute(
                """
                SELECT subscription_id, user_id, endpoint, p256dh, auth FROM omnix_trading_push_subscriptions
                 WHERE workspace_id = %s AND (%s::TEXT IS NULL OR user_id = %s)
                 ORDER BY created_at, subscription_id
                """,
                (self._workspace(workspace_id), user_id, user_id),
            ).fetchall()
        return [StoredSubscription(str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4])) for row in rows]

    def listed_subscriptions(self) -> list[PushSubscription]:
        with self._work() as uow:
            rows = uow.connection.execute(
                """
                SELECT subscription_id, user_id, endpoint, user_agent, created_at, last_success_at FROM omnix_trading_push_subscriptions
                 WHERE workspace_id = %s ORDER BY created_at, subscription_id
                """,
                (self._workspace(None),),
            ).fetchall()
        return [
            PushSubscription(subscription_id=str(row[0]), user_id=str(row[1]), service=urlsplit(str(row[2])).hostname or "",
                             user_agent=str(row[3]), created_at=row[4], last_success_at=row[5])
            for row in rows
        ]

    def save_subscription(self, user_id: str, subscription: PushSubscriptionWrite) -> str:
        """Add or refresh this browser's subscription (by endpoint); returns its id."""
        with self._work() as uow:
            count = uow.connection.execute(
                "SELECT count(*) FROM omnix_trading_push_subscriptions WHERE workspace_id = %s AND user_id = %s AND endpoint <> %s",
                (self._workspace(None), user_id, subscription.endpoint),
            ).fetchone()
            if count and int(count[0]) >= MAX_SUBSCRIPTIONS_PER_USER:
                raise ValueError(f"at most {MAX_SUBSCRIPTIONS_PER_USER} devices per person get notifications")
            row = uow.connection.execute(
                """
                INSERT INTO omnix_trading_push_subscriptions (workspace_id, subscription_id, user_id, endpoint, p256dh, auth, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, endpoint) DO UPDATE
                   SET user_id = EXCLUDED.user_id, p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, user_agent = EXCLUDED.user_agent
                RETURNING subscription_id
                """,
                (self._workspace(None), uuid.uuid4().hex, user_id, subscription.endpoint, subscription.keys.p256dh,
                 subscription.keys.auth, subscription.user_agent),
            ).fetchone()
            uow.commit()
        return str(row[0])

    def delete_subscription(self, subscription_id: str, workspace_id: str | None = None) -> bool:
        with self._work() as uow:
            row = uow.connection.execute(
                "DELETE FROM omnix_trading_push_subscriptions WHERE workspace_id = %s AND subscription_id = %s RETURNING subscription_id",
                (self._workspace(workspace_id), subscription_id),
            ).fetchone()
            uow.commit()
        return row is not None

    def mark_delivered(self, subscription_id: str, workspace_id: str | None = None) -> None:
        with self._work() as uow:
            uow.connection.execute(
                "UPDATE omnix_trading_push_subscriptions SET last_success_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND subscription_id = %s",
                (self._workspace(workspace_id), subscription_id),
            )
            uow.commit()


def default_notification_settings_repository() -> NotificationSettingsRepository:
    return NotificationSettingsRepository()


def sender_settings_repository() -> NotificationSettingsRepository:
    return NotificationSettingsRepository(all_workspaces=True)


__all__ = [
    "EmailSettings",
    "EmailSettingsWrite",
    "NotificationSettingsRepository",
    "ProtectedNotificationSecretStore",
    "PushSubscriptionWrite",
    "smtp_password_secret",
]
