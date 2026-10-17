"""Alert notification delivery: the outbox, the webhook sender and the delivery monitor (TVP-0.5a).

A trigger that selects a delivery channel gets one outbox row per channel, in
the transaction that records the trigger (``enqueue_alert_deliveries``). The
delivery monitor claims due rows one at a time with a lease, sends outside any
transaction through a ``NotificationSender`` and records the result: delivered,
another attempt after an exponential backoff, or failed. Triggers stay
authoritative and delivery is at-least-once: a pass that dies between sending
and recording sends again when its lease expires, with the same delivery id
(``X-Omnix-Delivery``), so receivers can drop the duplicate. A lease that
expires after the last attempt fails the delivery instead.

One monitor serves every workspace: it claims and records in the
``notifications.delivery`` system scope. The API lists a workspace's own rows.

Webhook rules:

- HTTPS only; the URL and signing secret come from the protected store by the
  alert's current reference (never stored in PostgreSQL). If the alert's
  reference changed between claim and send, the delivery is retried, not failed;
- the hostname (IDNA form) is resolved once, within a bounded time, and every
  address checked with the strict outbound URL policy (nothing that is not
  globally routable unless ``OMNIX_ALLOWED_PRIVATE_NETWORKS`` names it); the
  request goes to a checked address, in the system's preference order with
  fallback to the next one, with the hostname for TLS (SNI and certificate
  verification) and ``Host``, so DNS cannot rebind it;
- one deadline bounds the whole send (resolution, connection, response
  headers): a watchdog shuts the socket down (never closes it from another
  thread); the response body is never read; no redirects, no proxies; nothing
  logs the URL (the HTTP client's request log is not used);
- throughput: at most ``MAX_SENDS_PER_PASS`` sends per pass of the monitor
  (every 10 s), about 24 a minute: enough for one trader's alerts, not for
  fan-out to many receivers;
- the body is the trigger's message, ``application/json`` when it parses as
  JSON, else ``text/plain``;
- with a secret, ``X-Omnix-Signature: sha256=<hex>`` is the HMAC-SHA256 of
  ``<X-Omnix-Timestamp>.<X-Omnix-Delivery>.<body>``.

``last_error`` holds a reason code, never a URL, secret or response body.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import hmac
import ipaddress
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel

from app.config.env import environment
from app.persistence.tenant_scope import system_scope
from app.persistence.unit_of_work import unit_of_work
from app.runtime.features import FeatureContext
from app.security.tenant_context import RequestTenant, TenantContext
from app.security.url_policy import Resolver, UrlPolicyError, check_outbound_url, outbound_addresses, resolve_hostname

from .alerts_channels import AlertWebhookStore, ProtectedAlertWebhookStore
from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask

if TYPE_CHECKING:
    from .alerts import TradingAlert, TradingAlertTrigger, UnitOfWorkFactory

logger = logging.getLogger(__name__)

DeliveryChannel = Literal["webhook", "email", "push", "desktop"]
DeliveryStatus = Literal["pending", "sending", "delivered", "failed"]
DeliveryEventKind = Literal["alert", "margin_call"]

# Channels the outbox delivers; TVP-0.5b (email), TVP-0.5c (push) and MarketPulse S-6 (desktop) add theirs.
OUTBOX_CHANNELS: tuple[DeliveryChannel, ...] = ("webhook", "email", "push", "desktop")

MAX_WEBHOOK_ATTEMPTS = 8
FIRST_RETRY_SECONDS = 30.0
MAX_RETRY_SECONDS = 3_600.0
LEASE_SECONDS = 120.0
# One send, from resolution to response headers; a pass sends at most MAX_SENDS_PER_PASS, well inside the
# scheduler's 60 s pass timeout and the lease.
SEND_DEADLINE_SECONDS = 12.0
DNS_TIMEOUT_SECONDS = 3.0
MAX_SENDS_PER_PASS = 4
MAX_ADDRESSES_TRIED = 3
SYSTEM_OPERATION = "notifications.delivery"
USER_AGENT = "Omnix-Alerts/1"


def retry_delay_seconds(attempts: int) -> float:
    """The wait before the next attempt after ``attempts`` failed ones: 30 s, 1 min, 2 min, ... at most 1 h."""
    return min(MAX_RETRY_SECONDS, FIRST_RETRY_SECONDS * 2 ** max(0, attempts - 1))


def default_alert_message(alert: TradingAlert, trigger: TradingAlertTrigger) -> str:
    return f"{alert.instrument_id}: alert {alert.alert_id} triggered at {trigger.observed_value}"


def delivery_idempotency_key(trigger_key: str, channel: str) -> str:
    return hashlib.sha256(f"{trigger_key}|{channel}".encode()).hexdigest()


def enqueue_alert_deliveries(connection: Any, workspace_id: str, alert: TradingAlert, trigger: TradingAlertTrigger) -> int:
    """Outbox rows for a new trigger, in the caller's transaction; returns how many were added.

    A webhook row is added only when the alert selects the channel and has a
    webhook. A trigger written again (same idempotency key) adds nothing.
    """
    channels = [channel for channel in OUTBOX_CHANNELS if channel in alert.parameters.notification_channels]
    if "webhook" in channels and not alert.webhook_ref:
        channels.remove("webhook")
    # The trigger carries the message with its placeholders filled in (TVP-1.5).
    rendered = str(trigger.payload.get("message") or "").strip()
    message = rendered or alert.parameters.message.strip() or default_alert_message(alert, trigger)
    added = 0
    for channel in channels:
        row = connection.execute(
            """
            INSERT INTO omnix_trading_notification_deliveries (
                workspace_id, delivery_id, trigger_id, alert_id, channel,
                message, max_attempts, idempotency_key
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
            RETURNING delivery_id
            """,
            (
                workspace_id,
                uuid.uuid4().hex,
                trigger.trigger_id,
                alert.alert_id,
                channel,
                message,
                MAX_WEBHOOK_ATTEMPTS,
                delivery_idempotency_key(trigger.idempotency_key, channel),
            ),
        ).fetchone()
        added += row is not None
    return added


class NotificationDelivery(BaseModel):
    """A delivery as the API shows it: status only, never a destination.

    ``event_kind``: an alert trigger, or a paper margin call (TVP-7.2b), which has no trigger or alert."""

    delivery_id: str
    trigger_id: str | None
    alert_id: str | None
    event_kind: DeliveryEventKind = "alert"
    channel: DeliveryChannel
    status: DeliveryStatus
    attempts: int
    max_attempts: int
    next_attempt_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    last_status_code: int | None
    delivered_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class ClaimedDelivery:
    """A delivery leased to one send. ``attempt`` fences the result: a stale sender's result is ignored."""

    delivery_id: str
    trigger_id: str
    alert_id: str
    channel: str
    message: str
    attempt: int
    max_attempts: int
    webhook_ref: str | None
    workspace_id: str = ""
    # 'alert', or 'margin_call' (no trigger or alert: their ids are empty, and the channel is email, push or desktop).
    event_kind: str = "alert"
    # The alert's instrument (empty for a margin call) and when the delivery was queued, for the desktop shell.
    instrument_id: str = ""
    created_at: datetime | None = None


@dataclass(frozen=True)
class DeliveryResult:
    outcome: Literal["delivered", "retry", "failed"]
    error: str | None = None
    status_code: int | None = None
    # Seconds the destination asked to wait (Retry-After), honoured up to the backoff ceiling.
    retry_after: float | None = None


class NotificationSender(Protocol):
    """Sends one claimed delivery; the port lets delivery move to the platform tier later."""

    def send(self, delivery: ClaimedDelivery) -> DeliveryResult: ...


_DELIVERY_COLUMNS = """
    delivery_id, trigger_id, alert_id, channel, status, attempts, max_attempts,
    next_attempt_at, last_attempt_at, last_error, last_status_code, delivered_at, created_at, event_kind
"""


def _delivery(row: Any) -> NotificationDelivery:
    status = str(row[4])
    return NotificationDelivery(
        delivery_id=str(row[0]),
        trigger_id=str(row[1]) if row[1] is not None else None,
        alert_id=str(row[2]) if row[2] is not None else None,
        event_kind=cast(Any, str(row[13])) if len(row) > 13 and row[13] else "alert",
        channel=row[3],
        status=row[4],
        attempts=int(row[5]),
        max_attempts=int(row[6]),
        next_attempt_at=row[7] if status == "pending" else None,
        last_attempt_at=row[8],
        last_error=row[9],
        last_status_code=row[10],
        delivered_at=row[11],
        created_at=row[12],
    )


class NotificationDeliveryRepository:
    """The outbox. ``all_workspaces`` (the monitor) claims and records in every workspace; otherwise one tenant's rows."""

    context = RequestTenant()

    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory: UnitOfWorkFactory = unit_of_work,
        all_workspaces: bool = False,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory
        self.all_workspaces = all_workspaces

    @contextmanager
    def _work(self) -> Iterator[Any]:
        with system_scope(SYSTEM_OPERATION) if self.all_workspaces else nullcontext(), self.uow_factory() as uow:
            yield uow

    def _workspace_filter(self) -> str | None:
        return None if self.all_workspaces else self.context.workspace_id

    def claim_due(self, now: datetime, *, limit: int = 1, lease_seconds: float = LEASE_SECONDS) -> list[ClaimedDelivery]:
        """Leases due deliveries: pending ones whose time has come and sends whose lease ran out.

        A lease that ran out after the last attempt fails its delivery here instead of sending again.
        Claimed rows move their ``next_attempt_at`` to the lease's end, so a reclaimed row queues behind older work.
        """
        workspace = self._workspace_filter()
        lease_end = now + timedelta(seconds=lease_seconds)
        with self._work() as uow:
            uow.connection.execute(
                """
                UPDATE omnix_trading_notification_deliveries
                   SET status = 'failed', lease_expires_at = NULL,
                       last_error = 'lease_expired:attempts_exhausted', updated_at = %s
                 WHERE (%s::TEXT IS NULL OR workspace_id = %s)
                   AND status = 'sending' AND lease_expires_at <= %s AND attempts >= max_attempts
                """,
                (now, workspace, workspace, now),
            )
            rows = uow.connection.execute(
                """
                WITH due AS (
                    SELECT workspace_id, delivery_id
                      FROM omnix_trading_notification_deliveries
                     WHERE (%s::TEXT IS NULL OR workspace_id = %s)
                       AND ((status = 'pending' AND next_attempt_at <= %s)
                            OR (status = 'sending' AND lease_expires_at <= %s))
                     ORDER BY CASE WHEN status = 'sending' THEN lease_expires_at ELSE next_attempt_at END, delivery_id
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE omnix_trading_notification_deliveries AS delivery
                   SET status = 'sending',
                       attempts = delivery.attempts + 1,
                       lease_expires_at = %s,
                       next_attempt_at = %s,
                       last_attempt_at = %s,
                       updated_at = %s
                  FROM due
                 WHERE delivery.workspace_id = due.workspace_id AND delivery.delivery_id = due.delivery_id
                RETURNING delivery.delivery_id, delivery.trigger_id, delivery.alert_id, delivery.channel,
                          delivery.message, delivery.attempts, delivery.max_attempts,
                          (SELECT alert.notification_settings->>'webhook_ref'
                             FROM omnix_trading_alerts AS alert
                            WHERE alert.workspace_id = delivery.workspace_id AND alert.alert_id = delivery.alert_id),
                          delivery.workspace_id, delivery.event_kind, delivery.created_at,
                          (SELECT alert.instrument_id
                             FROM omnix_trading_alerts AS alert
                            WHERE alert.workspace_id = delivery.workspace_id AND alert.alert_id = delivery.alert_id)
                """,
                (workspace, workspace, now, now, limit, lease_end, lease_end, now, now),
            ).fetchall()
            uow.commit()
        claimed = [
            ClaimedDelivery(
                delivery_id=str(row[0]),
                trigger_id=str(row[1] or ""),
                alert_id=str(row[2] or ""),
                channel=str(row[3]),
                message=str(row[4]),
                attempt=int(row[5]),
                max_attempts=int(row[6]),
                webhook_ref=str(row[7]) if row[7] else None,
                workspace_id=str(row[8]),
                event_kind=str(row[9] or "alert"),
                created_at=row[10],
                instrument_id=str(row[11] or ""),
            )
            for row in rows
        ]
        return sorted(claimed, key=lambda item: item.delivery_id)

    def record(self, delivery: ClaimedDelivery, result: DeliveryResult, now: datetime) -> DeliveryStatus | None:
        """Records one send. Returns the new status, or None when the lease was lost (another pass owns the row)."""
        workspace = delivery.workspace_id or self.context.workspace_id
        with self._work() as uow:
            if result.outcome == "failed" and result.error == "webhook_missing" and delivery.webhook_ref:
                current = uow.connection.execute(
                    """
                    SELECT notification_settings->>'webhook_ref' FROM omnix_trading_alerts
                     WHERE workspace_id = %s AND alert_id = %s
                    """,
                    (workspace, delivery.alert_id),
                ).fetchone()
                if current is not None and current[0] and current[0] != delivery.webhook_ref:
                    # The webhook was replaced between claim and send: the new one is tried, not given up.
                    result = replace(result, outcome="retry", error="webhook_changed", retry_after=0.0)
            if result.outcome == "delivered":
                status: DeliveryStatus = "delivered"
            elif result.outcome == "retry" and delivery.attempt < delivery.max_attempts:
                status = "pending"
            else:
                status = "failed"
            if result.error == "webhook_changed":
                delay = 0.0
            else:
                delay = retry_delay_seconds(delivery.attempt)
                if result.retry_after is not None:
                    delay = min(MAX_RETRY_SECONDS, max(delay, result.retry_after))
            error = result.error if status != "delivered" else None
            if status == "failed" and result.outcome == "retry":
                error = f"{result.error or 'retry'}:attempts_exhausted"
            row = uow.connection.execute(
                """
                UPDATE omnix_trading_notification_deliveries
                   SET status = %s,
                       lease_expires_at = NULL,
                       next_attempt_at = CASE WHEN %s = 'pending' THEN %s ELSE next_attempt_at END,
                       last_error = %s,
                       last_status_code = %s,
                       delivered_at = CASE WHEN %s = 'delivered' THEN %s ELSE NULL END,
                       updated_at = %s
                 WHERE workspace_id = %s AND delivery_id = %s
                   AND status = 'sending' AND attempts = %s
                RETURNING status
                """,
                (
                    status,
                    status,
                    now + timedelta(seconds=delay),
                    error,
                    result.status_code,
                    status,
                    now,
                    now,
                    workspace,
                    delivery.delivery_id,
                    delivery.attempt,
                ),
            ).fetchone()
            uow.commit()
        return status if row is not None else None

    def list_deliveries(self, *, alert_id: str | None = None, limit: int = 100) -> list[NotificationDelivery]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_DELIVERY_COLUMNS}
                  FROM omnix_trading_notification_deliveries
                 WHERE workspace_id = %s AND (%s::TEXT IS NULL OR alert_id = %s)
                 ORDER BY created_at DESC, delivery_id
                 LIMIT %s
                """,
                (self.context.workspace_id, alert_id, alert_id, limit),
            ).fetchall()
            return [_delivery(row) for row in rows]


def default_delivery_repository() -> NotificationDeliveryRepository:
    return NotificationDeliveryRepository()


def monitor_delivery_repository() -> NotificationDeliveryRepository:
    return NotificationDeliveryRepository(all_workspaces=True)


def signature(secret: str, timestamp: str, delivery_id: str, body: bytes) -> str:
    """``sha256=<hex>`` of HMAC-SHA256 over ``<timestamp>.<delivery id>.<body>``."""
    signed = timestamp.encode("ascii") + b"." + delivery_id.encode("ascii") + b"." + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def _content_type(message: str) -> str:
    try:
        json.loads(message)
    except ValueError:
        return "text/plain; charset=utf-8"
    return "application/json"


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "").strip()
    return float(value) if value.isdigit() else None


def _ascii_url(url: str) -> tuple[str, str]:
    """The URL with its hostname in IDNA (ASCII) form, and that hostname."""
    parts = urlsplit(url)
    hostname = (parts.hostname or "").rstrip(".")
    ascii_host = hostname.encode("idna").decode("ascii").lower()
    host = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, "")), ascii_host


def _pinned_url(url: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    parts = urlsplit(url)
    host = f"[{address}]" if address.version == 6 else str(address)
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def _host_header(url: str) -> str:
    parts = urlsplit(url)
    return parts.netloc


_RESOLVER_THREADS = 4
_RESOLVERS = concurrent.futures.ThreadPoolExecutor(max_workers=_RESOLVER_THREADS, thread_name_prefix="omnix-webhook-dns")
_RESOLVER_SLOTS = threading.BoundedSemaphore(_RESOLVER_THREADS)


class _Deadline(Exception):
    pass


def bounded(resolver: Resolver, seconds: float) -> Resolver:
    """A resolver that gives up after ``seconds``.

    A lookup that times out keeps its thread until the system resolver returns, so lookups never queue: when every
    lookup thread is busy (a DNS outage), this gives up at once instead of adding to a backlog.
    """

    def resolve(hostname: str, port: int) -> list[str]:
        if not _RESOLVER_SLOTS.acquire(blocking=False):
            raise _Deadline

        def lookup() -> list[str]:
            try:
                return list(resolver(hostname, port))
            finally:
                _RESOLVER_SLOTS.release()

        future = _RESOLVERS.submit(lookup)
        try:
            return future.result(timeout=max(0.1, seconds))
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise _Deadline from exc

    return resolve


def post_within(request: httpx.Request, seconds: float) -> httpx.Response:
    """POSTs ``request`` within ``seconds``; see ``alerts_delivery_http.post_within``."""
    from .alerts_delivery_http import post_within as post

    return post(request, seconds)


Exchange = Callable[[httpx.Request, float], httpx.Response]


class WebhookSender:
    """Sends a delivery to its alert's webhook."""

    def __init__(
        self,
        store: AlertWebhookStore | None = None,
        *,
        exchange: Exchange = post_within,
        resolver: Resolver = resolve_hostname,
        deadline: float = SEND_DEADLINE_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store or ProtectedAlertWebhookStore()
        self.exchange = exchange
        self.resolver = resolver
        self.deadline = deadline
        self.clock = clock

    def send(self, delivery: ClaimedDelivery) -> DeliveryResult:
        started = time.monotonic()
        if not delivery.webhook_ref:
            return DeliveryResult("failed", "webhook_missing")
        try:
            stored = self.store.load(delivery.webhook_ref)
        except Exception:
            # Never mistake an unreadable store for a removed webhook.
            logger.warning("trading_alert_webhook_store_unreadable delivery_id=%s", delivery.delivery_id)
            return DeliveryResult("retry", "webhook_store_unavailable")
        if not stored or not stored.get("url"):
            return DeliveryResult("failed", "webhook_missing")
        try:
            url, hostname = _ascii_url(stored["url"])
        except (UnicodeError, ValueError):
            return DeliveryResult("failed", "url_policy:invalid_hostname")
        try:
            check_outbound_url(url, strict=True, https_only=True)
            resolver = bounded(self.resolver, min(DNS_TIMEOUT_SECONDS, self.deadline))
            addresses = outbound_addresses(url, strict=True, resolver=resolver)
        except _Deadline:
            return DeliveryResult("retry", "dns_timeout")
        except UrlPolicyError as exc:
            reason = str(exc)
            if reason == "hostname_resolution_failed":
                return DeliveryResult("retry", "dns_failed")
            return DeliveryResult("failed", f"url_policy:{reason}")
        body = delivery.message.encode("utf-8")
        timestamp = str(int(self.clock().timestamp()))
        headers = {
            "Host": _host_header(url),
            "Content-Type": _content_type(delivery.message),
            "User-Agent": USER_AGENT,
            "X-Omnix-Delivery": delivery.delivery_id,
            "X-Omnix-Alert": delivery.alert_id,
            "X-Omnix-Timestamp": timestamp,
        }
        secret = stored.get("secret", "")
        if secret:
            headers["X-Omnix-Signature"] = signature(secret, timestamp, delivery.delivery_id, body)
        error = "connection_failed"
        for address in addresses[:MAX_ADDRESSES_TRIED]:
            remaining = self.deadline - (time.monotonic() - started)
            if remaining <= 0:
                return DeliveryResult("retry", "timeout")
            request = httpx.Request(
                "POST",
                _pinned_url(url, address),
                content=body,
                headers=headers,
                extensions={"timeout": httpx.Timeout(remaining).as_dict(), "sni_hostname": hostname},
            )
            try:
                response = self.exchange(request, remaining)
            except httpx.ConnectError:
                error = "connection_failed"
                continue  # the next checked address
            except httpx.ConnectTimeout:
                error = "timeout"
                continue
            except httpx.TimeoutException:
                return DeliveryResult("retry", "timeout")
            except httpx.HTTPError:
                return DeliveryResult("retry", "connection_failed")
            return self._result(response)
        return DeliveryResult("retry", error)

    @staticmethod
    def _result(response: httpx.Response) -> DeliveryResult:
        code = response.status_code
        if 200 <= code < 300:
            return DeliveryResult("delivered", status_code=code)
        if 300 <= code < 400:
            return DeliveryResult("failed", "redirect_refused", code)
        if code in {408, 425, 429} or code >= 500:
            return DeliveryResult("retry", f"http_{code}", code, _retry_after(response))
        return DeliveryResult("failed", f"http_{code}", code)


def _env_flag(name: str, default: str) -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def notification_delivery_monitor_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _env_flag("OMNIX_TRADING_NOTIFICATION_MONITOR_IN_TESTS", "0")
    return _env_flag("OMNIX_TRADING_NOTIFICATION_MONITOR", "1")


class NotificationDeliveryMonitor(ScheduledTradingMonitor):
    """Sends due deliveries of every workspace, one claim at a time, at most ``MAX_SENDS_PER_PASS`` per pass."""

    def __init__(
        self,
        *,
        repository_factory: Callable[[], NotificationDeliveryRepository] = monitor_delivery_repository,
        senders: Mapping[str, NotificationSender] | None = None,
        interval_seconds: float = 10.0,
        max_sends: int = MAX_SENDS_PER_PASS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository_factory = repository_factory
        # None: the default senders, built with the first send (email and web push load smtplib and cryptography).
        self._senders = senders
        self.interval_seconds = interval_seconds
        self.max_sends = max_sends
        self.clock = clock
        self.last_error: str | None = None
        self.last_run_at: datetime | None = None
        self.counts: dict[str, int] = {"delivered": 0, "pending": 0, "failed": 0, "lease_lost": 0}

    @property
    def senders(self) -> Mapping[str, NotificationSender]:
        if self._senders is None:
            # Email and web push (TVP-0.5b/c) read their workspace's settings in alerts_notify.py.
            from .alerts_desktop import DesktopSender, desktop_notifications_configured
            from .alerts_notify_senders import EmailSender, PushSender

            senders: dict[str, NotificationSender] = {"webhook": WebhookSender(), "email": EmailSender(), "push": PushSender()}
            if desktop_notifications_configured():
                # MarketPulse S-6: only when the desktop shell gave its listener; otherwise desktop rows are unavailable.
                senders["desktop"] = DesktopSender()
            self._senders = senders
        return self._senders

    async def run_once(self) -> int:
        repository = self.repository_factory()
        error: str | None = None
        delivered = 0
        for _ in range(self.max_sends):
            # One row per claim: a cancelled pass leaves at most the row in flight to its lease.
            try:
                claimed = await asyncio.to_thread(repository.claim_due, self.clock(), limit=1)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                break
            if not claimed:
                break
            delivery = claimed[0]
            sender = self.senders.get(delivery.channel)
            try:
                if sender is None:
                    result = DeliveryResult("failed", "channel_unavailable")
                else:
                    result = await asyncio.to_thread(sender.send, delivery)
            except Exception as exc:
                logger.exception("trading_notification_send_failed delivery_id=%s", delivery.delivery_id)
                result = DeliveryResult("retry", f"sender_error:{type(exc).__name__}")
            try:
                status = await asyncio.to_thread(repository.record, delivery, result, self.clock())
            except Exception as exc:
                # The lease runs out and the delivery is sent again: at-least-once.
                error = f"{type(exc).__name__}: {exc}"
                continue
            key = status or "lease_lost"
            self.counts[key] = self.counts.get(key, 0) + 1
            delivered += status == "delivered"
        self.last_error = error
        self.last_run_at = datetime.now(timezone.utc)
        return delivered

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": notification_delivery_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "counts": dict(self.counts),
        }


_MONITOR_STATE_KEY = "_omnix_trading_notification_delivery_monitor"


def create_notification_delivery_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    if isinstance(getattr(state, _MONITOR_STATE_KEY, None), NotificationDeliveryMonitor):
        return None
    monitor = NotificationDeliveryMonitor()
    setattr(state, _MONITOR_STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=notification_delivery_monitor_enabled)


__all__ = [
    "ClaimedDelivery",
    "DeliveryResult",
    "NotificationDelivery",
    "NotificationDeliveryMonitor",
    "NotificationDeliveryRepository",
    "NotificationSender",
    "OUTBOX_CHANNELS",
    "WebhookSender",
    "create_notification_delivery_monitor_task",
    "delivery_idempotency_key",
    "enqueue_alert_deliveries",
    "post_within",
    "retry_delay_seconds",
    "signature",
]
