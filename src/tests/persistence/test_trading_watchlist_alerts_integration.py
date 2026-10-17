"""Watchlist alerts against PostgreSQL (TVP-1.7): one alert, every symbol, state per symbol."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.apps.trading.alerts import TradingAlertCreate, TradingAlertEvaluation, TradingAlertUpdate
from app.apps.trading.alerts_repository import TradingAlertRepository
from app.apps.trading.alerts_monitor import TradingAlertMonitor
from app.apps.trading.repositories import TradingDocumentRepository
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


@pytest.fixture()
def env():
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=3,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-watchlist-alert-tests",
        )
    )
    try:
        context = ensure_local_identity(database)

        def uow():
            return unit_of_work(database)

        suffix = uuid.uuid4().hex[:8].upper()
        alerts = TradingAlertRepository(context=context, uow_factory=uow)
        documents = TradingDocumentRepository(context=context, uow_factory=uow)
        symbols = [f"crypto:BINANCE:spot:W{suffix}{name}-USDT" for name in ("A", "B", "C")]
        watchlist_id = f"watchlist-{suffix.lower()}"
        documents.create("watchlist", watchlist_id, {"schemaVersion": 2, "name": "Test", "instrumentIds": symbols[:2]})
        yield SimpleNamespace(alerts=alerts, documents=documents, symbols=symbols, watchlist_id=watchlist_id, suffix=suffix, uow=uow, context=context)
        for alert in alerts.list_alerts(500):
            if alert.instrument_id == f"watchlist:{watchlist_id}":
                alerts.archive(alert.alert_id, alert.revision)
    finally:
        database.close()


class Market:
    """The same closes for every symbol, after the alerts were created."""

    def __init__(self) -> None:
        self.base = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
        self.closes: list[float] = []
        self.registry = SimpleNamespace(resolve_binding=lambda symbol: SimpleNamespace(provider="binance"))

    def bars(self, instrument_id, interval, limit, binding_id=None):
        bars = [
            SimpleNamespace(
                start_time=self.base + timedelta(minutes=index), end_time=self.base + timedelta(minutes=index + 1),
                open=Decimal(str(close)), high=Decimal(str(close)), low=Decimal(str(close)), close=Decimal(str(close)),
                volume=Decimal("10"), is_final=True,
            )
            for index, close in enumerate(self.closes)
        ]
        return SimpleNamespace(bars=bars, binding=SimpleNamespace(binding_id="fixture:resolved", provider="fixture"))


def _run(env, market: Market) -> list[str]:
    monitor = TradingAlertMonitor(
        repository_factory=lambda: env.alerts, market_service_factory=lambda: market,
        document_repository_factory=lambda: env.documents, interval_seconds=5,
    )
    before = {trigger.trigger_id for trigger in env.alerts.list_triggers(500)}
    asyncio.run(monitor.run_once())
    assert monitor.last_error is None
    return sorted(
        trigger.instrument_id for trigger in env.alerts.list_triggers(500)
        if trigger.trigger_id not in before and trigger.alert_id.endswith(env.suffix)
    )


def _create(env, alert_id: str, frequency: str):
    return env.alerts.create(
        TradingAlertCreate(
            alert_id=f"{alert_id}-{env.suffix}", instrument_id=f"watchlist:{env.watchlist_id}", frequency=frequency,
            conditions=[{"source": {"kind": "price", "field": "close"}, "operator": "greater_than", "target": {"kind": "value", "value": "100"}}],
            evaluation_policy={"interval": "1m"},
        ),
        webhook_ref=None,
    )


def test_a_once_alert_fires_once_per_symbol_and_follows_the_list(env) -> None:
    created = _create(env, "once", "once")
    assert created.watchlist_id == env.watchlist_id
    market = Market()
    market.closes = [99, 101]
    a, b, c = env.symbols
    assert _run(env, market) == [a, b]
    # Once per symbol: no second trigger, and the alert stays on for the others.
    market.closes = [99, 101, 102]
    assert _run(env, market) == []
    alert = next(item for item in env.alerts.list_alerts(500) if item.alert_id == created.alert_id)
    assert alert.enabled is True and alert.last_triggered_at is not None

    # A symbol added to the watchlist is evaluated on the next pass.
    record = env.documents.get("watchlist", env.watchlist_id)
    env.documents.update("watchlist", env.watchlist_id, {**record["payload"], "instrumentIds": env.symbols}, expected_revision=record["revision"])
    assert _run(env, market) == [c]
    with env.uow() as uow:
        states = uow.connection.execute(
            "SELECT instrument_id, fired_once FROM omnix_trading_alert_symbol_states WHERE workspace_id = %s AND alert_id = %s ORDER BY instrument_id",
            (env.context.workspace_id, created.alert_id),
        ).fetchall()
    assert [(row[0], row[1]) for row in states] == [(a, True), (b, True), (c, True)]


def _edit(env, alert, **changes):
    fields = alert.model_dump(include={"instrument_id", "conditions", "evaluation_policy", "frequency", "enabled", "parameters"})
    fields["parameters"] = {**fields["parameters"], **changes.pop("parameters", {})}
    return env.alerts.update(alert.alert_id, TradingAlertUpdate(**{**fields, **changes}), expected_revision=alert.revision, webhook_ref=None)


def test_a_message_edit_keeps_once_and_re_enabling_re_arms_every_symbol(env) -> None:
    created = _create(env, "rearm", "once")
    market = Market()
    market.closes = [99, 101]
    a, b, _ = env.symbols
    assert _run(env, market) == [a, b]
    edited = _edit(env, created, parameters={"message": "{{ticker}} is up"})
    assert edited.revision > created.revision and edited.definition_revision == created.definition_revision
    assert _run(env, market) == []
    disabled = _edit(env, edited, enabled=False)
    enabled = _edit(env, disabled, enabled=True)
    assert enabled.definition_revision == created.definition_revision
    market.closes = [99, 101, 102]
    assert _run(env, market) == [a, b]


def test_the_ordinary_paths_leave_watchlist_alerts_alone(env) -> None:
    created = _create(env, "pushed", "once")
    triggers = env.alerts.evaluate(TradingAlertEvaluation(
        instrument_id=created.instrument_id, interval="1m", observed_price=Decimal("500"), evaluated_at=datetime.now(timezone.utc),
    ))
    assert triggers == []
    alert = env.alerts.get(created.alert_id)
    assert alert is not None and alert.enabled is True and alert.revision == created.revision


def test_symbol_states_are_row_level_secured(env) -> None:
    with env.uow() as uow:
        row = uow.connection.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'omnix_trading_alert_symbol_states'"
        ).fetchone()
    assert tuple(row) == (True, True)


def test_an_every_time_alert_keeps_each_symbol_history_apart(env) -> None:
    _create(env, "every", "every_time")
    market = Market()
    market.closes = [99, 101]
    a, b, _ = env.symbols
    assert _run(env, market) == [a, b]
    # The same closed bar again: nothing new for either symbol.
    assert _run(env, market) == []
    market.closes = [99, 101, 103]
    assert _run(env, market) == [a, b]
