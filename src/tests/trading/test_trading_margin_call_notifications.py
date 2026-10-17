"""Margin calls notified by email and push through the outbox (TVP-7.2b)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.alerts_delivery import ClaimedDelivery
from app.apps.trading.alerts_notify import EmailSettings
from app.apps.trading.alerts_notify_senders import PushSender, alert_email
from app.apps.trading.paper import MARGIN_CALL_ORDER_PREFIX, PaperAccount, PaperAccountCreate, PaperAccountSettings, PaperOrder
from app.apps.trading.paper_margin_notifications import PaperMarginCallNotifier, margin_call_message, margin_call_orders

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
SETTINGS = EmailSettings(host="smtp.example.com", from_address="omnix@example.com", to_addresses=["me@example.com"])


def account(notify: bool = True) -> PaperAccount:
    return PaperAccount(account_id="acct-1", name="Swing", base_currency="USD", commission_bps=Decimal(0), notify_margin_calls=notify)


def order(order_id: str, *, side: str = "sell", age: timedelta = timedelta(minutes=5)) -> PaperOrder:
    return PaperOrder.model_validate({
        "order_id": order_id, "account_id": "acct-1", "instrument_id": "equity:NASDAQ:AAPL", "side": side, "order_type": "market",
        "quantity": "12", "status": "filled", "idempotency_key": f"key-{order_id}", "created_at": NOW - age,
    })


class _Connection:
    def __init__(self, keys: set[str]) -> None:
        self.keys = keys
        self.rows: list[tuple] = []

    def execute(self, sql, params):
        key = params[5]
        if key in self.keys:
            return SimpleNamespace(fetchone=lambda: None)
        self.keys.add(key)
        self.rows.append(params)
        return SimpleNamespace(fetchone=lambda: ("id",))


def notifier(*, email: bool = True, push: bool = True):
    keys: set[str] = set()
    connections: list[_Connection] = []

    @contextmanager
    def uow():
        connection = _Connection(keys)
        connections.append(connection)
        yield SimpleNamespace(connection=connection, commit=lambda: None)

    settings = SimpleNamespace(email=lambda: SETTINGS if email else None, subscriptions=lambda: [object()] if push else [])
    return PaperMarginCallNotifier(context=SimpleNamespace(workspace_id="ws-1"), uow_factory=uow, settings_factory=lambda: settings), connections


def test_recent_margin_call_orders_are_the_ones_notified() -> None:
    orders = [order(f"{MARGIN_CALL_ORDER_PREFIX}a"), order("manual-1"), order(f"{MARGIN_CALL_ORDER_PREFIX}old", age=timedelta(days=2))]
    assert [item.order_id for item in margin_call_orders(orders, NOW)] == [f"{MARGIN_CALL_ORDER_PREFIX}a"]
    assert margin_call_message(account(), order("x")) == (
        "Margin call on Swing: Sold 12 AAPL at market. The account's equity fell below the margin its positions need."
    )
    assert margin_call_message(account(), order("x", side="buy")).startswith("Margin call on Swing: Bought back 12 AAPL")


def test_each_margin_call_is_queued_once_per_channel() -> None:
    queue, connections = notifier()
    calls = [order(f"{MARGIN_CALL_ORDER_PREFIX}a"), order(f"{MARGIN_CALL_ORDER_PREFIX}b")]
    assert queue.notify(account(), calls) == 4
    channels = sorted((row[2], row[0]) for row in connections[0].rows)
    assert channels == [("email", "ws-1"), ("email", "ws-1"), ("push", "ws-1"), ("push", "ws-1")]
    # Every pass queues again until it is queued; the idempotency key makes that a no-op.
    assert queue.notify(account(), calls) == 0


def test_nothing_is_queued_without_the_setting_or_a_channel() -> None:
    calls = [order(f"{MARGIN_CALL_ORDER_PREFIX}a")]
    queue, connections = notifier()
    assert queue.notify(account(notify=False), calls) == 0
    assert connections == []
    quiet, _ = notifier(email=False, push=False)
    assert quiet.notify(account(), calls) == 0
    push_only, connections = notifier(email=False)
    assert push_only.notify(account(), calls) == 1
    assert connections[0].rows[0][2] == "push"


def test_the_setting_is_off_unless_asked_for() -> None:
    assert PaperAccountCreate(account_id="a", name="a").notify_margin_calls is False
    assert PaperAccountSettings().notify_margin_calls is None
    assert PaperAccountSettings(notify_margin_calls=True).notify_margin_calls is True


def test_a_margin_call_email_and_push_say_what_it_is() -> None:
    email = alert_email(SETTINGS, "Margin call on Swing: Sold 12 AAPL at market.", event_kind="margin_call")
    assert email["Subject"] == "Omnix margin call: Margin call on Swing: Sold 12 AAPL at market."
    assert email["X-Omnix-Event"] == "margin-call" and email["X-Omnix-Alert"] is None
    assert "Omnix paper trading" in email.get_content()
    alert = alert_email(SETTINGS, "AAPL crossing 100", alert_id="a-1")
    assert alert["Subject"] == "Omnix alert: AAPL crossing 100" and alert["X-Omnix-Alert"] == "a-1"

    sent = []
    sender = PushSender(repository_factory=lambda: SimpleNamespace(subscriptions=lambda workspace_id: [object()]))
    sender.deliver = lambda repository, workspace_id, subscriptions, payload: sent.append(payload) or "ok"
    delivery = ClaimedDelivery(delivery_id="d", trigger_id="", alert_id="", channel="push", message="Margin call", attempt=1,
                               max_attempts=8, webhook_ref=None, workspace_id="ws-1", event_kind="margin_call")
    sender.send(delivery)
    assert b'"title": "Omnix margin call"' in sent[0]
