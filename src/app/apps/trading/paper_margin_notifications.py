"""Margin-call notifications through the outbox (TVP-7.2b).

When a paper account with ``notify_margin_calls`` is margin-called, each margin-call order queues an email and a push
delivery, on the channels the workspace has set up (its SMTP settings, its browsers' push subscriptions). The
delivery monitor sends them like alert notifications, with retries. A delivery's idempotency key follows from the
order, so queuing again (every monitor pass, until it is queued) adds nothing: a margin call notified once is never
notified twice, and one placed just before a crash is notified on the next pass.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.persistence.unit_of_work import unit_of_work
from app.security.tenant_context import RequestTenant, TenantContext

from .alerts_delivery import MAX_WEBHOOK_ATTEMPTS
from .paper import MARGIN_CALL_ORDER_PREFIX, PaperAccount, PaperOrder

if TYPE_CHECKING:
    from .alerts_notify import NotificationSettingsRepository

# Margin-call orders this recent are (re)queued; older ones were queued on an earlier pass or predate the setting.
RECENT = timedelta(hours=24)


def margin_call_orders(orders: Sequence[PaperOrder], now: datetime) -> list[PaperOrder]:
    """The account's margin-call orders placed in the last day."""
    return [
        order for order in orders
        if order.order_id.startswith(MARGIN_CALL_ORDER_PREFIX) and order.created_at is not None and now - order.created_at <= RECENT
    ]


def margin_call_message(account: PaperAccount, order: PaperOrder) -> str:
    action = "Sold" if order.side == "sell" else "Bought back"
    symbol = order.instrument_id.split(":")[-1].replace("-", "/")
    return (
        f"Margin call on {account.name}: {action} {order.quantity} {symbol} at market. "
        "The account's equity fell below the margin its positions need."
    )


def _idempotency_key(account_id: str, order_id: str, channel: str) -> str:
    return hashlib.sha256(f"margin-call|{account_id}|{order_id}|{channel}".encode()).hexdigest()


def _notification_settings() -> NotificationSettingsRepository:
    from .alerts_notify import default_notification_settings_repository

    return default_notification_settings_repository()


class PaperMarginCallNotifier:
    """Queues margin-call deliveries in the current workspace's outbox."""

    context = RequestTenant()

    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory: Callable[[], AbstractContextManager[Any]] = unit_of_work,
        settings_factory: Callable[[], NotificationSettingsRepository] = _notification_settings,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory
        self.settings_factory = settings_factory

    def channels(self) -> list[str]:
        """The channels the workspace can deliver on now: email with SMTP settings, push with a subscribed browser,
        and the desktop app's notifications when it runs inside the desktop shell (MarketPulse S-6)."""
        from .alerts_desktop import desktop_notifications_configured

        settings = self.settings_factory()
        channels = []
        if settings.email() is not None:
            channels.append("email")
        if settings.subscriptions():
            channels.append("push")
        if desktop_notifications_configured():
            channels.append("desktop")
        return channels

    def notify(self, account: PaperAccount, orders: Sequence[PaperOrder]) -> int:
        """Queues each order's deliveries; returns how many were new."""
        if not account.notify_margin_calls or not orders:
            return 0
        channels = self.channels()
        if not channels:
            return 0
        added = 0
        with self.uow_factory() as uow:
            for order in orders:
                message = margin_call_message(account, order)
                for channel in channels:
                    row = uow.connection.execute(
                        """
                        INSERT INTO omnix_trading_notification_deliveries (
                            workspace_id, delivery_id, trigger_id, alert_id, channel,
                            message, max_attempts, idempotency_key, event_kind
                        ) VALUES (%s, %s, NULL, NULL, %s, %s, %s, %s, 'margin_call')
                        ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
                        RETURNING delivery_id
                        """,
                        (
                            self.context.workspace_id,
                            uuid.uuid4().hex,
                            channel,
                            message,
                            MAX_WEBHOOK_ATTEMPTS,
                            _idempotency_key(account.account_id, order.order_id, channel),
                        ),
                    ).fetchone()
                    added += row is not None
            uow.commit()
        return added


def default_margin_call_notifier() -> PaperMarginCallNotifier:
    return PaperMarginCallNotifier()


__all__ = ["PaperMarginCallNotifier", "default_margin_call_notifier", "margin_call_message", "margin_call_orders"]
