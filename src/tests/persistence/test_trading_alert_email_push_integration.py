"""Alert email and push settings, subscriptions and outbox rows against PostgreSQL (TVP-0.5b/c)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.apps.trading.alerts import TradingAlertCreate, TradingAlertEvaluation
from app.apps.trading.alerts_repository import TradingAlertRepository
from app.apps.trading.alerts_notify import EmailSettings, NotificationSettingsRepository, PushSubscriptionWrite
from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("OMNIX_TEST_DATABASE_URL"),
        reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
    ),
]

KEYS = {"p256dh": "B" + "A" * 86, "auth": "A" * 22}


@pytest.fixture()
def env():
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=3, connect_timeout_seconds=10,
            statement_timeout_ms=30_000, application_name="omnix-alert-email-push-tests",
        )
    )
    try:
        context = ensure_local_identity(database)

        def uow():
            return unit_of_work(database)

        suffix = uuid.uuid4().hex[:10].upper()
        settings = NotificationSettingsRepository(context=context, uow_factory=uow)
        yield SimpleNamespace(
            context=context, uow=uow, suffix=suffix, settings=settings,
            senders=NotificationSettingsRepository(uow_factory=uow, all_workspaces=True),
            alerts=TradingAlertRepository(context=context, uow_factory=uow), instrument=f"crypto:BINANCE:spot:N{suffix}-USDT",
        )
        with uow() as work:
            work.connection.execute("DELETE FROM omnix_trading_push_subscriptions WHERE workspace_id = %s AND endpoint LIKE %s", (context.workspace_id, f"%{suffix}%"))
            work.connection.execute("DELETE FROM omnix_trading_notification_deliveries WHERE workspace_id = %s AND alert_id LIKE %s", (context.workspace_id, f"%-{suffix}"))
            work.commit()
    finally:
        database.close()


def test_email_settings_and_push_subscriptions_are_stored_per_workspace(env) -> None:
    previous = env.settings.email()
    try:
        settings = EmailSettings(host="smtp.example.com", from_address="me@example.com", to_addresses=["you@example.com"], has_password=True)
        env.settings.save_email(settings)
        assert env.settings.email() == settings
        # The senders read any workspace by id.
        assert env.senders.email(env.context.workspace_id) == settings
        endpoint = f"https://push.example/{env.suffix}"
        first = env.settings.save_subscription("u1", PushSubscriptionWrite(endpoint=endpoint, keys=KEYS))
        # The same browser again refreshes its subscription.
        assert env.settings.save_subscription("u1", PushSubscriptionWrite(endpoint=endpoint, keys=KEYS, user_agent="Edge")) == first
        mine = [sub for sub in env.settings.subscriptions(user_id="u1") if env.suffix in sub.endpoint]
        assert [sub.subscription_id for sub in mine] == [first]
        listed = next(item for item in env.settings.listed_subscriptions() if item.subscription_id == first)
        assert listed.service == "push.example" and listed.user_agent == "Edge"
        env.senders.mark_delivered(first, env.context.workspace_id)
        assert next(item for item in env.settings.listed_subscriptions() if item.subscription_id == first).last_success_at is not None
        assert env.senders.delete_subscription(first, env.context.workspace_id)
    finally:
        env.settings.save_email(previous)


def test_a_trigger_queues_email_and_push_deliveries(env) -> None:
    alert = env.alerts.create(
        TradingAlertCreate(
            alert_id=f"notify-{env.suffix}", instrument_id=env.instrument,
            conditions=[{"source": {"kind": "price", "field": "close"}, "operator": "crossing_up", "target": {"kind": "value", "value": "100"}}],
            parameters={"notification_channels": ["app", "email", "push"], "message": "BTC up"},
        ),
        webhook_ref=None,
    )
    try:
        observed = datetime(2089, 12, 31, tzinfo=timezone.utc)
        env.alerts.evaluate(TradingAlertEvaluation(instrument_id=env.instrument, observed_price=Decimal("99"), observed_at=observed))
        env.alerts.evaluate(TradingAlertEvaluation(instrument_id=env.instrument, observed_price=Decimal("101"), observed_at=observed + timedelta(seconds=30)))
        with env.uow() as work:
            rows = work.connection.execute(
                "SELECT channel, message FROM omnix_trading_notification_deliveries WHERE workspace_id = %s AND alert_id = %s ORDER BY channel",
                (env.context.workspace_id, alert.alert_id),
            ).fetchall()
        assert [tuple(row) for row in rows] == [("email", "BTC up"), ("push", "BTC up")]
    finally:
        env.alerts.archive(alert.alert_id, env.alerts.get(alert.alert_id).revision)


def test_the_new_tables_are_row_level_secured(env) -> None:
    with env.uow() as work:
        rows = work.connection.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname IN ('omnix_trading_notification_settings', 'omnix_trading_push_subscriptions') ORDER BY relname"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("omnix_trading_notification_settings", True, True), ("omnix_trading_push_subscriptions", True, True)]
