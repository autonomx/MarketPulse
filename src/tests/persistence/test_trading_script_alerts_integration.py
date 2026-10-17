"""Script alerts against PostgreSQL (TVP-11.4): the monitor runs the saved script version and fires with its message."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.alerts import TradingAlertCreate
from app.apps.trading.alerts_repository import TradingAlertRepository
from app.apps.trading.alerts_api import create_trading_alert_router
from app.apps.trading.alerts_monitor import TradingAlertMonitor
from app.apps.trading.repositories import TradingDocumentRepository
from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work
from app.runtime.tenant_context import pop_tenant, push_tenant

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("OMNIX_TEST_DATABASE_URL"),
        reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
    ),
]

SCRIPT = """//@version=5
indicator("Breakout", overlay=true)
if close > 100
    alert("broke out at " + str.tostring(close), alert.freq_once_per_bar)
"""


@pytest.fixture()
def env():
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=3, connect_timeout_seconds=10,
            statement_timeout_ms=30_000, application_name="omnix-script-alert-tests",
        )
    )
    try:
        context = ensure_local_identity(database)
        token = push_tenant(context)

        def uow():
            return unit_of_work(database)

        suffix = uuid.uuid4().hex[:8]
        alerts = TradingAlertRepository(context=context, uow_factory=uow)
        documents = TradingDocumentRepository(context=context, uow_factory=uow)
        script = documents.create("script", f"s{suffix}", {"name": "Breakout", "source": SCRIPT})
        yield SimpleNamespace(alerts=alerts, documents=documents, script=script, suffix=suffix, uow=uow)
        for alert in alerts.list_alerts(500):
            if alert.alert_id.endswith(suffix):
                alerts.archive(alert.alert_id, alert.revision)
        pop_tenant(token)
    finally:
        database.close()


class Market:
    def __init__(self) -> None:
        self.base = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
        self.closes: list[float] = []
        self.registry = SimpleNamespace(resolve_binding=lambda symbol: SimpleNamespace(provider="fixture"))

    def bars(self, instrument_id, interval, limit, binding_id=None):
        return SimpleNamespace(
            bars=[
                SimpleNamespace(
                    start_time=self.base + timedelta(minutes=index), end_time=self.base + timedelta(minutes=index + 1),
                    open=Decimal(str(close)), high=Decimal(str(close)), low=Decimal(str(close)), close=Decimal(str(close)),
                    volume=Decimal("10"), is_final=True,
                )
                for index, close in enumerate(self.closes)
            ],
            binding=SimpleNamespace(binding_id="fixture:resolved", provider="fixture"),
        )


def _condition(script_id: str, revision: int, output: str = "alert") -> dict:
    source = {"kind": "script", "script_id": script_id, "revision": revision, "output": output}
    return {"source": source, "operator": "greater_than", "target": {"kind": "value", "value": "-1000000000000000000"}}


def test_a_script_alert_fires_with_the_alert_call_message_and_keeps_its_version(env) -> None:
    script = env.script
    created = env.alerts.create(
        TradingAlertCreate(
            alert_id=f"script-{env.suffix}", instrument_id=f"equity:TEST:S{env.suffix.upper()}", frequency="once_per_bar_close",
            conditions=[_condition(script["record_id"], script["revision"])], evaluation_policy={"interval": "1m"},
        ),
        webhook_ref=None,
    )
    # A later edit of the script doesn't change the alert: it reads the version it was made with.
    env.documents.update("script", script["record_id"], {"name": "Breakout", "source": SCRIPT.replace("> 100", "> 1000")}, expected_revision=script["revision"])
    market = Market()
    market.closes = [99, 101.5]
    monitor = TradingAlertMonitor(repository_factory=lambda: env.alerts, market_service_factory=lambda: market, interval_seconds=5)
    asyncio.run(monitor.run_once())
    assert monitor.last_error is None
    [trigger] = [item for item in env.alerts.list_triggers(500) if item.alert_id == created.alert_id]
    assert trigger.payload["message"] == "broke out at 101.5"
    assert trigger.payload["observations"][0]["message"] == "broke out at 101.5"


def test_the_api_rejects_a_script_version_that_does_not_exist(env) -> None:
    app = FastAPI()
    app.include_router(create_trading_alert_router(repository_factory=lambda: env.alerts, document_repository_factory=lambda: env.documents))
    client = TestClient(app)
    body = {
        "alert_id": f"bad-{env.suffix}", "instrument_id": "equity:TEST:X", "condition_type": "conditions",
        "conditions": [_condition(env.script["record_id"], 1, "alertcondition:0")], "evaluation_policy": {"interval": "1m"},
    }
    response = client.post("/api/trading/alerts", json=body)
    assert response.status_code == 422 and "alertcondition" in response.text
    body["conditions"] = [_condition("missing-script", 1)]
    assert "no saved version" in client.post("/api/trading/alerts", json=body).text
