"""The desktop notification channel through the outbox, against PostgreSQL (MarketPulse S-6)."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.alerts import TradingAlertCreate, TradingAlertEvaluation
from app.apps.trading.alerts_delivery import NotificationDeliveryMonitor, NotificationDeliveryRepository
from app.apps.trading.alerts_desktop import DesktopSender
from app.apps.trading.alerts_repository import TradingAlertRepository
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

CLOSE = {"kind": "price", "field": "close"}
BASE = datetime(2091, 1, 1, tzinfo=timezone.utc)


@pytest.fixture()
def outbox(monkeypatch):
    monkeypatch.setenv("OMNIX_TRADING_DESKTOP_NOTIFY_URL", "http://127.0.0.1:47811/notify")
    monkeypatch.setenv("OMNIX_TRADING_DESKTOP_NOTIFY_SECRET", "s3cret")
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=3, connect_timeout_seconds=10,
            statement_timeout_ms=30_000, application_name="omnix-desktop-notification-tests",
        )
    )
    try:
        context = ensure_local_identity(database)

        def uow():
            return unit_of_work(database)

        suffix = uuid.uuid4().hex[:10].upper()
        alerts = TradingAlertRepository(context=context, uow_factory=uow)
        yield alerts, NotificationDeliveryRepository(context=context, uow_factory=uow), suffix, f"crypto:BINANCE:spot:D{suffix}-USDT", uow, context
        with uow() as work:
            work.connection.execute(
                "DELETE FROM omnix_trading_notification_deliveries WHERE workspace_id = %s AND alert_id LIKE %s",
                (context.workspace_id, f"%-{suffix}"),
            )
            work.commit()
        for alert in alerts.list_alerts(500):
            if alert.alert_id.endswith(suffix):
                alerts.archive(alert.alert_id, alert.revision)
    finally:
        database.close()


def test_a_desktop_alert_is_queued_claimed_with_its_target_and_delivered(outbox) -> None:
    alerts, deliveries, suffix, instrument, uow, context = outbox
    alert = alerts.create(
        TradingAlertCreate(
            alert_id=f"desk-{suffix}",
            instrument_id=instrument,
            conditions=[{"source": CLOSE, "operator": "crossing_up", "target": {"kind": "value", "value": "100"}}],
            parameters={"notification_channels": ["app", "desktop"], "message": "crossed 100"},
        ),
        webhook_ref=None,
    )
    observed = BASE - timedelta(days=1)
    alerts.evaluate(TradingAlertEvaluation(instrument_id=instrument, observed_price=Decimal("99"), observed_at=observed))
    alerts.evaluate(TradingAlertEvaluation(instrument_id=instrument, observed_price=Decimal("101"), observed_at=observed + timedelta(seconds=30)))

    sent: list[dict] = []

    def post(url, payload, secret):
        sent.append(payload)
        return 200

    monitor = NotificationDeliveryMonitor(
        repository_factory=lambda: NotificationDeliveryRepository(context=context, uow_factory=uow),
        senders={"desktop": DesktopSender(post=post)},
        clock=lambda: BASE,
    )
    asyncio.run(monitor.run_once())

    mine = [payload for payload in sent if payload["alert_id"] == alert.alert_id]
    assert len(mine) == 1
    assert mine[0]["instrument_id"] == instrument
    assert mine[0]["message"] == "crossed 100"
    assert mine[0]["created_at"] is not None
    [listed] = deliveries.list_deliveries(alert_id=alert.alert_id)
    assert (listed.channel, listed.status) == ("desktop", "delivered")
