"""Alert conditions, frequencies and channels against PostgreSQL (TVP-1.1, TVP-1.2)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.alert_conditions import legacy_conditions
from app.apps.trading.alerts import TradingAlertCreate, TradingAlertEvaluation, TradingAlertParameters, TradingAlertUpdate
from app.apps.trading.alerts_repository import TradingAlertRepository
from app.apps.trading.alerts_api import create_trading_alert_router
from app.apps.trading.alerts_channels import ProtectedAlertWebhookStore, alert_webhook_prefix
from app.apps.trading.alerts_monitor import TradingAlertMonitor
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

MIGRATION = Path("src/app/apps/trading/migrations/0141_trading_alert_conditions.sql")
CLOSE = {"kind": "price", "field": "close"}


@pytest.fixture()
def alerts():
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=3,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-alert-condition-tests",
        )
    )
    try:
        context = ensure_local_identity(database)

        def uow():
            return unit_of_work(database)

        repository = TradingAlertRepository(context=context, uow_factory=uow)
        suffix = uuid.uuid4().hex[:10].upper()
        instrument = f"crypto:BINANCE:spot:T{suffix}-USDT"
        yield SimpleNamespace(repository=repository, instrument=instrument, uow=uow, context=context, suffix=suffix)
        for alert in repository.list_alerts(500):
            if alert.instrument_id == instrument:
                repository.archive(alert.alert_id, alert.revision)
    finally:
        database.close()


def _owner_connection() -> psycopg.Connection:
    """The migration's DDL needs the table owner (the migrator); the runtime role has DML only."""
    url = (
        os.environ.get("OMNIX_TEST_ADMIN_DATABASE_URL")
        or os.environ.get("OMNIX_MIGRATION_DATABASE_URL")
        or os.environ["OMNIX_TEST_DATABASE_URL"]
    )
    return psycopg.connect(url)


def _create(env, alert_id: str, **data):
    data.setdefault("conditions", [{"source": CLOSE, "operator": "crossing_up", "target": {"kind": "value", "value": "100"}}])
    return env.repository.create(
        TradingAlertCreate(alert_id=f"{alert_id}-{env.suffix}", instrument_id=env.instrument, **data), webhook_ref=None
    )


def _alert(env, alert_id: str):
    return next(alert for alert in env.repository.list_alerts(500) if alert.alert_id == f"{alert_id}-{env.suffix}")


def _child_rows(env, alert_id: str):
    with env.uow() as uow:
        return uow.connection.execute(
            "SELECT position, source, operator, target FROM omnix_trading_alert_conditions"
            " WHERE workspace_id = %s AND alert_id = %s ORDER BY position",
            (env.context.workspace_id, alert_id),
        ).fetchall()


class Market:
    """Bars whose times lie after the alerts were created, so none of them is history."""

    def __init__(self) -> None:
        self.base = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
        self.bars: list[SimpleNamespace] = []

    def set(self, closes, forming=None) -> None:
        values = [(close, True) for close in closes] + ([(forming, False)] if forming is not None else [])
        self.bars = [
            SimpleNamespace(
                start_time=self.base + timedelta(minutes=index),
                end_time=self.base + timedelta(minutes=index + 1),
                open=Decimal(str(close)), high=Decimal(str(close)), low=Decimal(str(close)), close=Decimal(str(close)),
                volume=Decimal("10"), is_final=final,
            )
            for index, (close, final) in enumerate(values)
        ]

    def bars_for(self, *args, **kwargs):
        return SimpleNamespace(bars=list(self.bars), binding=SimpleNamespace(binding_id="fixture:resolved", provider="fixture"))


def _run(env, market: Market, repository=None) -> list[str]:
    target = repository or env.repository
    service = SimpleNamespace(bars=market.bars_for)
    monitor = TradingAlertMonitor(repository_factory=lambda: target, market_service_factory=lambda: service, interval_seconds=5)
    before = {trigger.trigger_id for trigger in env.repository.list_triggers(500)}
    asyncio.run(monitor.run_once())
    assert monitor.last_error is None
    return sorted(
        trigger.alert_id.removesuffix(f"-{env.suffix}")
        for trigger in env.repository.list_triggers(500)
        if trigger.trigger_id not in before and trigger.instrument_id == env.instrument
    )


PARTIAL = {"interval": "1m", "allow_partial_bars": True}


def test_conditions_round_trip_update_and_cascade(alerts) -> None:
    sma = {"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 3}, "output": "sma:3"}
    created = _create(
        alerts,
        "round-trip",
        frequency="once_per_bar_close",
        conditions=[
            {"source": CLOSE, "operator": "crossing_up", "target": {"kind": "source", "source": sma}},
            {"source": {"kind": "change_percent", "lookback_bars": 2}, "operator": "moving_up", "amount": "1.5", "bars": 3},
            {"source": CLOSE, "operator": "inside_channel", "target": {"kind": "channel", "upper": {"kind": "value", "value": "120"}, "lower": {"kind": "source", "source": sma}}},
        ],
    )
    listed = _alert(alerts, "round-trip")
    assert listed.condition_type == "conditions" and listed.threshold == 0
    assert listed.frequency == "once_per_bar_close" and listed.parameters.trigger_policy == "once_per_bar_close"
    assert listed.conditions == created.conditions
    assert [row[2] for row in _child_rows(alerts, created.alert_id)] == ["crossing_up", "moving_up", "inside_channel"]

    updated = alerts.repository.update(
        created.alert_id,
        TradingAlertUpdate(instrument_id=alerts.instrument, conditions=[{"source": CLOSE, "operator": "less_than", "target": {"kind": "value", "value": "5"}}]),
        expected_revision=created.revision,
        webhook_ref=None,
    )
    assert updated.frequency == "every_time"
    assert _alert(alerts, "round-trip").conditions == updated.conditions
    assert [row[2] for row in _child_rows(alerts, created.alert_id)] == ["less_than"]

    alerts.repository.archive(created.alert_id, updated.revision)
    assert _child_rows(alerts, created.alert_id) == []


def test_legacy_requests_store_their_derived_condition(alerts) -> None:
    created = _create(alerts, "legacy", conditions=[], condition_type="indicator_cross_below", threshold="30", parameters={"indicator_id": "rsi", "period": 9, "trigger_policy": "once"})
    assert created.frequency == "once"
    rows = _child_rows(alerts, created.alert_id)
    assert len(rows) == 1
    assert rows[0][1]["output"] == "rsi:9" and rows[0][2] == "crossing_down"
    assert _alert(alerts, "legacy").conditions == created.conditions


def test_once_per_bar_close_waits_for_the_close_and_fires_once(alerts) -> None:
    market = Market()
    _create(alerts, "close", frequency="once_per_bar_close", evaluation_policy=PARTIAL)
    _create(alerts, "per-bar", frequency="once_per_bar", evaluation_policy=PARTIAL)
    _create(alerts, "every", frequency="every_time", evaluation_policy=PARTIAL)

    market.set([98, 99], forming=101)  # true intrabar
    assert _run(alerts, market) == ["every", "per-bar"]
    market.set([98, 99], forming=102)  # same bar, new value
    assert _run(alerts, market) == ["every"]
    market.set([98, 99, 99.5], forming=100)  # the bar closed below: no close alert
    assert _run(alerts, market) == ["every", "per-bar"]
    market.set([98, 99, 99.5, 101], forming=101.5)  # this bar closed above
    assert _run(alerts, market) == ["close"]
    assert _run(alerts, market) == []
    # A restarted monitor with a fresh repository does not trigger again.
    restarted = TradingAlertRepository(context=alerts.context, uow_factory=alerts.uow)
    assert _run(alerts, market, restarted) == []
    close_alert = _alert(alerts, "close")
    assert close_alert.last_observed_value == Decimal("101")  # evaluation state is kept
    assert close_alert.last_triggered_at is not None


def test_once_disables_the_alert_in_the_same_transaction(alerts) -> None:
    market = Market()
    _create(alerts, "once", frequency="once", cooldown_seconds=0)
    market.set([99, 101])
    assert _run(alerts, market) == ["once"]
    alert = _alert(alerts, "once")
    assert alert.enabled is False and alert.last_triggered_at is not None
    market.set([99, 101, 99, 102])
    assert _run(alerts, market) == []
    # Re-enabling is a new revision, so it may fire once more.
    enabled = alerts.repository.update(
        alert.alert_id,
        TradingAlertUpdate(**alert.model_dump(include=set(TradingAlertUpdate.model_fields) - {"enabled", "webhook_secret"}), enabled=True),
        expected_revision=alert.revision,
        webhook_ref=None,
    )
    assert enabled.last_triggered_at == alert.last_triggered_at  # lifecycle edit keeps history
    market.base += timedelta(minutes=10)
    market.set([99, 103])
    assert _run(alerts, market) == ["once"]


def test_once_per_minute_rate_limit(alerts) -> None:
    market = Market()
    created = _create(
        alerts,
        "minute",
        frequency="once_per_minute",
        evaluation_policy=PARTIAL,
        conditions=[{"source": CLOSE, "operator": "greater_than", "target": {"kind": "value", "value": "100"}}],
    )
    market.set([99], forming=101)
    assert _run(alerts, market) == ["minute"]
    market.set([99], forming=102)
    assert _run(alerts, market) == []
    with alerts.uow() as uow:
        uow.connection.execute(
            "UPDATE omnix_trading_alerts SET last_triggered_at = last_triggered_at - INTERVAL '61 seconds'"
            " WHERE workspace_id = %s AND alert_id = %s",
            (alerts.context.workspace_id, created.alert_id),
        )
        uow.commit()
    assert _run(alerts, market) == ["minute"]


def test_bars_closed_before_the_alert_existed_are_history(alerts) -> None:
    market = Market()
    _create(alerts, "stale")
    market.base -= timedelta(hours=1)
    market.set([99, 101])
    assert _run(alerts, market) == []
    market.base += timedelta(hours=2)
    market.set([99, 101])
    assert _run(alerts, market) == ["stale"]


def test_trigger_payload_lists_each_condition(alerts) -> None:
    market = Market()
    _create(
        alerts,
        "payload",
        conditions=[
            {"source": CLOSE, "operator": "crossing_up", "target": {"kind": "value", "value": "100"}},
            {"source": {"kind": "price", "field": "volume"}, "operator": "greater_than", "target": {"kind": "value", "value": "5"}},
        ],
    )
    market.set([99, 101])
    assert _run(alerts, market) == ["payload"]
    trigger = next(t for t in alerts.repository.list_triggers(500) if t.instrument_id == alerts.instrument)
    assert trigger.condition_type == "conditions"
    assert trigger.observed_value == Decimal("101") and trigger.threshold == 0
    assert [(item["source"], item.get("target")) for item in trigger.payload["observations"]] == [("101", "100"), ("10", "5")]
    assert trigger.payload["frequency"] == "every_time" and trigger.payload["bar_is_final"] is True


def test_pushed_prices_evaluate_price_alerts_only(alerts) -> None:
    price = _create(alerts, "push", conditions=[], condition_type="price_above", threshold="100")
    _create(alerts, "push-rsi", conditions=[], condition_type="indicator_above", threshold="50", parameters={"indicator_id": "rsi"})
    now = datetime.now(timezone.utc) + timedelta(minutes=1)

    def push(value: str, minute: int):
        return alerts.repository.evaluate(
            TradingAlertEvaluation(instrument_id=alerts.instrument, observed_price=Decimal(value), observed_at=now + timedelta(minutes=minute))
        )

    assert push("99", 0) == []
    triggers = push("101", 1)
    assert [trigger.alert_id for trigger in triggers] == [price.alert_id]
    assert push("101", 1) == []  # same observation: idempotent
    assert _alert(alerts, "push-rsi").last_observed_value is None


def test_pushed_prices_skip_per_bar_frequencies(alerts) -> None:
    for frequency in ("once_per_bar", "once_per_bar_close"):
        _create(alerts, frequency, frequency=frequency)
    now = datetime.now(timezone.utc) + timedelta(minutes=1)
    for minute, price in enumerate(("99", "101")):
        assert alerts.repository.evaluate(
            TradingAlertEvaluation(instrument_id=alerts.instrument, observed_price=Decimal(price), observed_at=now + timedelta(minutes=minute))
        ) == []
    assert _alert(alerts, "once_per_bar").last_observed_value is None


def test_migration_backfills_legacy_alerts(alerts) -> None:
    points = [{"time": "2026-08-05T12:00:00+00:00", "price": "100"}, {"time": "2026-08-05T13:00:00+00:00", "price": "110"}]
    legacy = {
        "price_above": ({}, "101.5"),
        "price_below": ({}, "99"),
        "percent_change_above": ({"lookback_bars": 3}, "2"),
        "percent_change_below": ({"lookback_bars": 1}, "-2"),
        "volume_above": ({}, "1000"),
        "volume_below": ({}, "10"),
        "indicator_above": ({"indicator_id": "bollinger", "period": 20, "component": "lower"}, "1"),
        "indicator_below": ({"indicator_id": "macd", "fast_period": 5, "slow_period": 13, "signal_period": 4, "component": "signal"}, "0"),
        "indicator_cross_above": ({"indicator_id": "stochastic-rsi", "period": 14, "fast_period": 3, "signal_period": 3}, "80"),
        "indicator_cross_below": ({"indicator_id": "vwap", "anchor_bars_ago": 5}, "50"),
        "trendline_crossing": ({"trendline_points": points}, "0"),
        "trendline_crossing_up": ({"trendline_points": points}, "0"),
        "trendline_crossing_down": ({"trendline_points": points}, "0"),
        "trendline_above": ({"trendline_points": points}, "0"),
        "trendline_below": ({"trendline_points": points}, "0"),
    }
    policies = {"price_above": "once", "price_below": "once_per_bar", "volume_above": "every_time"}
    workspace = alerts.context.workspace_id
    # One transaction as the migrator, rolled back at the end: legacy rows, the migration twice, and the checks.
    with _owner_connection() as connection:
        for index, (condition_type, (parameters, threshold)) in enumerate(legacy.items()):
            stored = TradingAlertParameters(**parameters, trigger_policy=policies.get(condition_type, "every_time"))
            connection.execute(
                "INSERT INTO omnix_trading_alerts (workspace_id, alert_id, instrument_id, condition_type, threshold,"
                " condition_parameters, cooldown_seconds, last_triggered_at)"
                " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
                (
                    workspace, f"legacy-{index}-{alerts.suffix}", alerts.instrument, condition_type, Decimal(threshold),
                    stored.model_dump_json(), 31_536_000 if condition_type == "price_above" else 60,
                    datetime.now(timezone.utc) if condition_type == "price_above" else None,
                ),
            )
        connection.execute("DELETE FROM omnix_trading_alert_conditions WHERE workspace_id = %s AND alert_id LIKE %s", (workspace, f"legacy-%-{alerts.suffix}"))
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        connection.execute(MIGRATION.read_text(encoding="utf-8"))  # idempotent
        rows = connection.execute(
            "SELECT alert_id, frequency, cooldown_seconds, enabled, condition_parameters, notification_settings,"
            " evaluation_policy FROM omnix_trading_alerts WHERE workspace_id = %s AND alert_id LIKE %s",
            (workspace, f"legacy-%-{alerts.suffix}"),
        ).fetchall()
        stored_rows = {str(row[0]): row for row in rows}
        for index, (condition_type, (parameters, threshold)) in enumerate(legacy.items()):
            alert_id = f"legacy-{index}-{alerts.suffix}"
            children = connection.execute(
                "SELECT position, source, operator, target, amount, bars FROM omnix_trading_alert_conditions"
                " WHERE workspace_id = %s AND alert_id = %s",
                (workspace, alert_id),
            ).fetchall()
            assert len(children) == 1, condition_type
            from app.apps.trading.alert_conditions import AlertConditionSpec

            stored_condition = AlertConditionSpec.model_validate(
                {"source": children[0][1], "operator": children[0][2], "target": children[0][3]}
            )
            expected = legacy_conditions(condition_type, Decimal(threshold), TradingAlertParameters(**parameters))
            assert [stored_condition] == expected, condition_type
        assert stored_rows[f"legacy-0-{alerts.suffix}"][1:4] == ("once", 0, False)  # already fired: stays stopped
        assert stored_rows[f"legacy-1-{alerts.suffix}"][1:4] == ("once_per_bar", 0, True)
        assert stored_rows[f"legacy-4-{alerts.suffix}"][1:4] == ("every_time", 60, True)
        for row in stored_rows.values():
            # Notification settings moved out of condition_parameters; intrabar evaluation follows the frequency.
            assert not {"message", "notification_channels", "delivery"} & set(row[4])
            assert row[5]["notification_channels"] == ["app", "toast"] and row[5]["message"] == ""
            assert row[6]["allow_partial_bars"] is (row[1] != "once_per_bar_close")
        connection.rollback()


class MemoryWebhooks:
    """An in-memory webhook store; ``fail_save`` simulates the protected store failing."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, str]] = {}
        self.fail_save = False
        self.fail_load = False
        self.fail_cleanup = False

    def available(self) -> bool:
        return True

    def load(self, ref):
        if self.fail_load:
            raise OSError("store unreadable")
        return self.entries.get(ref)

    def save(self, ref, url, secret) -> None:
        if self.fail_save:
            raise OSError("store unavailable")
        self.entries[ref] = {"url": url, "secret": secret}

    def delete(self, ref) -> None:
        self.entries.pop(ref, None)

    def delete_alert(self, workspace_id, alert_id, *, keep=None) -> None:
        if self.fail_cleanup:
            raise OSError("store locked")
        prefix = alert_webhook_prefix(workspace_id, alert_id)
        self.entries = {ref: entry for ref, entry in self.entries.items() if ref == keep or not ref.startswith(prefix)}

    def of(self, workspace_id, alert_id):
        prefix = alert_webhook_prefix(workspace_id, alert_id)
        return [entry for ref, entry in self.entries.items() if ref.startswith(prefix)]


def _client(env, webhooks):
    from app.apps.trading.alerts_delivery import NotificationDeliveryRepository
    from app.errors import install_error_envelope

    app = FastAPI()
    install_error_envelope(app)
    deliveries = NotificationDeliveryRepository(context=env.context, uow_factory=env.uow)
    app.include_router(
        create_trading_alert_router(
            repository_factory=lambda: env.repository,
            webhook_store=webhooks,
            delivery_repository_factory=lambda: deliveries,
        )
    )
    return TestClient(app)


HOOK = "https://hooks.example.com/services/T000/B000/tokenvalue"


def test_api_rejects_unavailable_channels_and_keeps_webhooks_out_of_responses(alerts) -> None:
    webhooks = MemoryWebhooks()
    client = _client(alerts, webhooks)
    alert_id = f"api-{alerts.suffix}"

    def stored():
        return webhooks.of(alerts.context.workspace_id, alert_id)

    body = {
        "alert_id": alert_id,
        "instrument_id": alerts.instrument,
        "conditions": [{"source": CLOSE, "operator": "greater_than", "target": {"kind": "value", "value": "1"}}],
        "parameters": {"notification_channels": ["app", "webhook"]},
    }
    rejected = client.post("/api/trading/alerts", json=body)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "the webhook channel needs parameters.delivery.webhook"
    for channel in ("email", "push"):
        assert client.post("/api/trading/alerts", json={**body, "parameters": {"notification_channels": [channel]}}).status_code == 422

    body["parameters"] = {"notification_channels": ["app", "sound"], "delivery": {"webhook": {"url": HOOK}, "sound": {"name": "chime"}}}
    created = client.post("/api/trading/alerts", json={**body, "webhook_secret": "very-secret-value"})
    assert created.status_code == 201, created.text
    for text in (created.text, client.get("/api/trading/alerts").text):
        assert "very-secret-value" not in text and "tokenvalue" not in text and "webhook_secret" not in text
    webhook = created.json()["parameters"]["delivery"]["webhook"]
    assert webhook == {"display_url": "https://hooks.example.com/…", "has_secret": True}
    assert stored() == [{"url": HOOK, "secret": "very-secret-value"}]
    with alerts.uow() as uow:
        row = uow.connection.execute(
            "SELECT condition_parameters::text || notification_settings::text FROM omnix_trading_alerts"
            " WHERE workspace_id = %s AND alert_id = %s",
            (alerts.context.workspace_id, alert_id),
        ).fetchone()[0]
    assert "very-secret-value" not in row and "tokenvalue" not in row and "hooks.example.com/…" in row

    # Reusing the alert id fails without touching the stored webhook.
    duplicate = client.post("/api/trading/alerts", json={**body, "webhook_secret": "other-secret"})
    assert duplicate.status_code == 409
    assert stored() == [{"url": HOOK, "secret": "very-secret-value"}]

    # An update that sends back what it read (masked URL, no secret) keeps both.
    echoed = {**body, "parameters": {**body["parameters"], "delivery": {"webhook": created.json()["parameters"]["delivery"]["webhook"]}}}
    echoed.pop("alert_id")
    kept = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "1"}, json=echoed)
    assert kept.status_code == 200, kept.text
    assert kept.json()["parameters"]["delivery"]["webhook"]["has_secret"] is True
    assert stored() == [{"url": HOOK, "secret": "very-secret-value"}]
    # A stale update fails before anything is stored.
    stale = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "1"}, json={**echoed, "webhook_secret": "stale"})
    assert stale.status_code == 409
    assert stored() == [{"url": HOOK, "secret": "very-secret-value"}]
    # A store failure changes nothing: no new revision, the old secret still referenced.
    webhooks.fail_save = True
    failed = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "2"}, json={**echoed, "webhook_secret": "rotated"})
    assert failed.status_code == 503
    webhooks.fail_save = False
    assert _alert(alerts, "api").revision == 2
    assert stored() == [{"url": HOOK, "secret": "very-secret-value"}]
    # Rotation replaces the old secret; nothing else for this alert stays stored.
    rotated = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "2"}, json={**echoed, "webhook_secret": "rotated"})
    assert rotated.status_code == 200
    assert stored() == [{"url": HOOK, "secret": "rotated"}]
    cleared = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "3"}, json={**echoed, "webhook_secret": ""})
    assert cleared.json()["parameters"]["delivery"]["webhook"]["has_secret"] is False
    assert stored() == [{"url": HOOK, "secret": ""}]
    # A client cannot claim a secret.
    claim = {**echoed, "parameters": {**echoed["parameters"], "delivery": {"webhook": {"has_secret": True}}}}
    claimed = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "4"}, json=claim)
    assert claimed.json()["parameters"]["delivery"]["webhook"]["has_secret"] is False
    # Removing the webhook removes it from the store.
    removed = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "5"}, json={**echoed, "parameters": {"notification_channels": ["app"]}})
    assert removed.status_code == 200 and stored() == []
    assert client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "6"}, json=echoed).status_code == 422  # url required again
    readd = {name: value for name, value in body.items() if name != "alert_id"}
    assert client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "6"}, json=readd).status_code == 200
    assert stored() == [{"url": HOOK, "secret": ""}]
    assert client.delete(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "7"}).status_code == 200
    assert stored() == []

    # A new alert with an old id and a webhook does not inherit what an earlier one left.
    webhooks.save(alert_webhook_prefix(alerts.context.workspace_id, alert_id) + "leftover", "https://old.example.com/x", "old")
    assert client.post("/api/trading/alerts", json={**body, "webhook_secret": "fresh"}).status_code == 201
    assert stored() == [{"url": HOOK, "secret": "fresh"}]
    assert client.post("/api/trading/alerts", json={**body, "alert_id": f"nohook-{alerts.suffix}", "parameters": {}, "webhook_secret": "x"}).status_code == 422


def test_trigger_payloads_and_422s_never_carry_webhook_credentials(alerts) -> None:
    webhooks = MemoryWebhooks()
    client = _client(alerts, webhooks)
    base = {
        "alert_id": f"leak-{alerts.suffix}",
        "instrument_id": alerts.instrument,
        "parameters": {"delivery": {"webhook": {"url": HOOK}}},
        "webhook_secret": "leaky-secret-value",
    }
    for body in (
        {**base, "conditions": []},  # model-level error: the whole body is the input
        {**base, "conditions": [{"source": CLOSE, "operator": "inside_channel", "target": {"kind": "value", "value": "1"}}]},
        {**base, "condition_type": "conditions", "threshold": "5"},
        {**base, "webhook_secret": "leaky-secret-value" * 40},  # field error on the secret itself
        {**base, "parameters": {"delivery": {"webhook": {"url": HOOK + "x" * 2000}}}},  # field error on the URL
        {**base, "conditions": [{"source": {}, "operator": "greater_than", "target": {"kind": "value", "value": "1"}}]},
    ):
        response = client.post("/api/trading/alerts", json=body)
        assert response.status_code == 422, response.text
        assert "leaky-secret-value" not in response.text and "tokenvalue" not in response.text
    update = {name: value for name, value in base.items() if name != "alert_id"}
    put = client.put(f"/api/trading/alerts/{base['alert_id']}", headers={"If-Match": "1"}, json=update)
    assert put.status_code in {409, 422} and "leaky-secret-value" not in put.text

    created = client.post(
        "/api/trading/alerts",
        json={**base, "conditions": [{"source": CLOSE, "operator": "crossing_up", "target": {"kind": "value", "value": "100"}}]},
    )
    assert created.status_code == 201
    market = Market()
    market.set([99, 101])
    assert _run(alerts, market) == ["leak"]
    triggers = client.get("/api/trading/alerts/triggers").text
    assert "hooks.example.com/…" in triggers
    assert "tokenvalue" not in triggers and "leaky-secret-value" not in triggers


def test_a_missing_kind_is_a_422(alerts) -> None:
    client = _client(alerts, MemoryWebhooks())
    body = {"alert_id": f"kind-{alerts.suffix}", "instrument_id": alerts.instrument}
    for condition in (
        {"source": {}, "operator": "greater_than", "target": {"kind": "value", "value": "1"}},
        {"source": CLOSE, "operator": "greater_than", "target": {"value": "1"}},
    ):
        assert client.post("/api/trading/alerts", json={**body, "conditions": [condition]}).status_code == 422


def test_editing_notification_settings_keeps_trigger_state(alerts) -> None:
    market = Market()
    created = _create(
        alerts, "notify", frequency="once_per_minute",
        conditions=[{"source": CLOSE, "operator": "greater_than", "target": {"kind": "value", "value": "100"}}],
    )
    market.set([99], forming=101)
    assert _run(alerts, market) == ["notify"]
    fired = _alert(alerts, "notify")
    fields = set(TradingAlertUpdate.model_fields) - {"webhook_secret"}
    update = TradingAlertUpdate(**{
        **fired.model_dump(include=fields),
        "parameters": {
            **fired.parameters.model_dump(),
            "message": "new text",
            "notification_channels": ["app", "sound"],
            "delivery": {"sound": {"name": "bell"}},
        },
    })
    edited = alerts.repository.update(created.alert_id, update, expected_revision=fired.revision, webhook_ref=None)
    assert edited.parameters.message == "new text"
    assert edited.definition_revision == fired.definition_revision
    assert _alert(alerts, "notify").last_triggered_at == fired.last_triggered_at
    market.set([99], forming=102)
    assert _run(alerts, market) == []  # still inside the minute
    # A condition change starts a new definition and a new history.
    changed = TradingAlertUpdate(**{
        **edited.model_dump(include=fields),
        "conditions": [{"source": CLOSE, "operator": "greater_than", "target": {"kind": "value", "value": "101"}}],
    })
    alerts.repository.update(created.alert_id, changed, expected_revision=edited.revision, webhook_ref=None)
    after = _alert(alerts, "notify")
    assert after.last_triggered_at is None and after.definition_revision == fired.definition_revision + 1


def test_a_notification_edit_does_not_let_a_bar_fire_twice(alerts) -> None:
    market = Market()
    created = _create(alerts, "per-bar-edit", frequency="once_per_bar")
    market.set([99], forming=101)
    assert _run(alerts, market) == ["per-bar-edit"]
    fired = _alert(alerts, "per-bar-edit")
    fields = set(TradingAlertUpdate.model_fields) - {"webhook_secret"}
    update = TradingAlertUpdate(**{**fired.model_dump(include=fields), "parameters": {**fired.parameters.model_dump(), "message": "edited"}})
    alerts.repository.update(created.alert_id, update, expected_revision=fired.revision, webhook_ref=None)
    market.set([99], forming=102)
    assert _run(alerts, market) == []  # same forming bar
    # A definition edit is a new alert as far as bars go.
    threshold = TradingAlertUpdate(**{
        **_alert(alerts, "per-bar-edit").model_dump(include=fields),
        "conditions": [{"source": CLOSE, "operator": "crossing_up", "target": {"kind": "value", "value": "101.5"}}],
    })
    alerts.repository.update(created.alert_id, threshold, expected_revision=fired.revision + 1, webhook_ref=None)
    assert _run(alerts, market) == ["per-bar-edit"]


def _insert_raw(env, alert_id, condition_type="price_above", notification=None):
    with env.uow() as uow:
        uow.connection.execute(
            "INSERT INTO omnix_trading_alerts (workspace_id, alert_id, instrument_id, condition_type, threshold,"
            " notification_settings) VALUES (%s, %s, %s, %s, 100, %s::jsonb)",
            (env.context.workspace_id, alert_id, env.instrument, condition_type, json.dumps(notification or {})),
        )
        uow.commit()


def test_unreadable_rows_are_reported_skipped_and_archivable(alerts) -> None:
    market = Market()
    _create(alerts, "healthy")
    suffix = alerts.suffix
    # Breaks today's write rules but reads: a blocked webhook URL, a legacy indicator without an id.
    _insert_raw(alerts, f"blocked-url-{suffix}", notification={"delivery": {"webhook": {"url": "http://169.254.169.254/"}}})
    _insert_raw(alerts, f"no-indicator-{suffix}", "indicator_above")
    # Cannot be read: a bad alert row, and a good alert row with a bad condition row.
    _insert_raw(alerts, f"broken-{suffix}", notification={"notification_channels": ["fax"]})
    bad_condition = _create(alerts, "bad-condition")
    with alerts.uow() as uow:
        uow.connection.execute(
            "UPDATE omnix_trading_alert_conditions SET operator = 'inside_channel'"
            " WHERE workspace_id = %s AND alert_id = %s",
            (alerts.context.workspace_id, bad_condition.alert_id),
        )
        uow.commit()

    listing = alerts.repository.list_alerts_report(500)
    listed = {alert.alert_id for alert in listing.alerts if alert.instrument_id == alerts.instrument}
    assert listed == {f"healthy-{suffix}", f"blocked-url-{suffix}", f"no-indicator-{suffix}"}
    unreadable = {item.alert_id: item for item in listing.unreadable if item.instrument_id == alerts.instrument}
    assert set(unreadable) == {f"broken-{suffix}", f"bad-condition-{suffix}"}
    assert "fax" not in unreadable[f"broken-{suffix}"].reason

    client = _client(alerts, MemoryWebhooks())
    body = client.get("/api/trading/alerts").json()
    assert {item["alert_id"] for item in body["unreadable"]} >= set(unreadable)

    market.set([99, 101])
    service = SimpleNamespace(bars=market.bars_for)
    monitor = TradingAlertMonitor(repository_factory=lambda: alerts.repository, market_service_factory=lambda: service, interval_seconds=5)
    asyncio.run(monitor.run_once())
    assert monitor.diagnostics()["unreadable_alert_count"] >= 2
    assert f"broken-{suffix}" in monitor.diagnostics()["unreadable_alert_ids"]
    assert monitor.last_error is None  # unreadable data is not an error of the pass
    assert any(t.alert_id == f"healthy-{suffix}" for t in alerts.repository.list_triggers(500))

    for alert_id, item in unreadable.items():
        archived = client.delete(f"/api/trading/alerts/{alert_id}", headers={"If-Match": str(item.revision)})
        assert archived.status_code == 200, archived.text
        assert archived.json()["alert_id"] == alert_id and archived.json()["enabled"] is False
    assert not [item for item in alerts.repository.list_alerts_report(500).unreadable if item.instrument_id == alerts.instrument]


def test_migrated_legacy_alerts_ignore_bars_closed_before_the_migration(alerts) -> None:
    market = Market()
    workspace = alerts.context.workspace_id
    alert_id = f"stale-legacy-{alerts.suffix}"
    month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    with alerts.uow() as uow:
        uow.connection.execute(
            "INSERT INTO omnix_trading_alerts (workspace_id, alert_id, instrument_id, condition_type, threshold,"
            " condition_parameters, created_at, updated_at)"
            " VALUES (%s, %s, %s, 'price_above', 100, %s::jsonb, %s, %s)",
            (workspace, alert_id, alerts.instrument, json.dumps({"message": "old", "trigger_policy": "every_time"}), month_ago, month_ago),
        )
        uow.commit()
    with _owner_connection() as owner:
        owner.execute(MIGRATION.read_text(encoding="utf-8"))
    alert = next(item for item in alerts.repository.list_alerts(500) if item.alert_id == alert_id)
    assert alert.updated_at > month_ago + timedelta(days=29)
    assert alert.parameters.message == "old" and alert.evaluation_policy.allow_partial_bars is True
    # Bars that closed after the alert's old updated_at but before the migration are history.
    market.base = datetime.now(timezone.utc) - timedelta(hours=2)
    market.set([99, 101])
    assert _run(alerts, market) == []
    market.base = datetime.now(timezone.utc) + timedelta(minutes=1)
    market.set([99, 101])
    assert _run(alerts, market) == ["stale-legacy"]


@pytest.mark.skipif(sys.platform != "win32", reason="the protected store needs DPAPI")
def test_protected_webhook_store_round_trip_and_serialised_writes(tmp_path, monkeypatch) -> None:
    import threading

    monkeypatch.setenv("OMNIX_PROVIDER_SECRETS_PATH", str(tmp_path / "secrets.dpapi"))
    store = ProtectedAlertWebhookStore()
    assert store.load("w/a/1") is None
    store.save("w/a/1", HOOK, "s3cret")
    assert store.load("w/a/1") == {"url": HOOK, "secret": "s3cret"}
    raw = (tmp_path / "secrets.dpapi").read_bytes()
    assert b"s3cret" not in raw and b"tokenvalue" not in raw

    # Concurrent writers must not lose each other's entries.
    threads = [threading.Thread(target=store.save, args=(f"w/b/{index}", HOOK, str(index))) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(store.load(f"w/b/{index}") == {"url": HOOK, "secret": str(index)} for index in range(8))

    store.delete_alert("w", "b", keep="w/b/3")
    assert store.load("w/b/3") is not None and store.load("w/b/4") is None
    store.delete("w/a/1")
    assert store.load("w/a/1") is None and store.load("w/b/3") is not None



def test_an_unreadable_store_never_looks_like_a_missing_webhook(alerts) -> None:
    webhooks = MemoryWebhooks()
    client = _client(alerts, webhooks)
    alert_id = f"unreadable-store-{alerts.suffix}"
    body = {
        "instrument_id": alerts.instrument,
        "conditions": [{"source": CLOSE, "operator": "greater_than", "target": {"kind": "value", "value": "1"}}],
        "parameters": {"delivery": {"webhook": {"url": HOOK}}},
    }
    assert client.post("/api/trading/alerts", json={**body, "alert_id": alert_id, "webhook_secret": "keep-me"}).status_code == 201
    url_only = {**body, "parameters": {"delivery": {"webhook": {"url": HOOK + "/v2"}}}}
    webhooks.fail_load = True
    failed = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "1"}, json=url_only)
    assert failed.status_code == 503
    webhooks.fail_load = False
    assert webhooks.of(alerts.context.workspace_id, alert_id) == [{"url": HOOK, "secret": "keep-me"}]
    assert _alert(alerts, "unreadable-store").revision == 1
    # The secret survives a URL-only change once the store reads again.
    assert client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "1"}, json=url_only).status_code == 200
    assert webhooks.of(alerts.context.workspace_id, alert_id) == [{"url": HOOK + "/v2", "secret": "keep-me"}]
    # A row whose stored webhook vanished does not guess: resend url and secret.
    webhooks.entries.clear()
    assert client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "2"}, json=url_only).status_code == 409
    resent = client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "2"}, json={**url_only, "webhook_secret": "again"})
    assert resent.status_code == 200
    assert webhooks.of(alerts.context.workspace_id, alert_id) == [{"url": HOOK + "/v2", "secret": "again"}]


def test_alert_transactions_are_serialised_per_alert(alerts) -> None:
    import threading
    import time

    _create(alerts, "locked")
    alert_id = f"locked-{alerts.suffix}"
    entered = threading.Event()
    other_done = threading.Event()
    timeline: list[str] = []

    def second_is_waiting() -> bool:
        # The second transaction is queued on this alert's advisory lock.
        with alerts.uow() as uow:
            row = uow.connection.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
            ).fetchone()
        return bool(row and row[0])

    def first() -> None:
        with alerts.repository.alert_transaction(alert_id) as state:
            assert state.exists
            entered.set()
            # Hold the lock until another alert has gone through and this
            # alert's second transaction is waiting for it.
            other_done.wait(5)
            deadline = time.monotonic() + 5
            while not second_is_waiting() and time.monotonic() < deadline:
                other_done.wait(0.02)
            timeline.append("first-done")

    def second() -> None:
        entered.wait(5)
        with alerts.repository.alert_transaction(alert_id):
            timeline.append("second-in")

    def other_alert() -> None:
        entered.wait(5)
        with alerts.repository.alert_transaction(f"other-{alerts.suffix}") as state:
            assert not state.exists
            timeline.append("other-in")
        other_done.set()

    threads = [threading.Thread(target=target) for target in (first, second, other_alert)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert timeline.index("first-done") < timeline.index("second-in")
    assert timeline.index("other-in") < timeline.index("first-done")  # other alerts are not blocked



class BrokenWebhooks(MemoryWebhooks):
    """A store that fails every call: corrupt, or locked by a stalled process."""

    def available(self) -> bool:
        return True

    def load(self, ref):
        raise OSError("store unreadable")

    def save(self, ref, url, secret) -> None:
        raise OSError("store unreadable")

    def delete(self, ref) -> None:
        raise OSError("store unreadable")

    def delete_alert(self, workspace_id, alert_id, *, keep=None) -> None:
        raise OSError("store unreadable")


def test_alerts_without_webhooks_never_wait_for_the_store(alerts) -> None:
    client = _client(alerts, BrokenWebhooks())
    alert_id = f"app-only-{alerts.suffix}"
    body = {
        "alert_id": alert_id,
        "instrument_id": alerts.instrument,
        "conditions": [{"source": CLOSE, "operator": "greater_than", "target": {"kind": "value", "value": "1"}}],
        "parameters": {"notification_channels": ["app"]},
    }
    assert client.post("/api/trading/alerts", json=body).status_code == 201
    update = {name: value for name, value in body.items() if name != "alert_id"}
    assert client.put(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "1"}, json=update).status_code == 200
    assert client.delete(f"/api/trading/alerts/{alert_id}", headers={"If-Match": "2"}).status_code == 200
    # With a webhook the store is needed, and its failure is a 503 that changes nothing.
    hooked = client.post("/api/trading/alerts", json={**body, "parameters": {"delivery": {"webhook": {"url": HOOK}}}})
    assert hooked.status_code == 503
    assert all(alert.alert_id != alert_id for alert in alerts.repository.list_alerts(500))


def test_leftover_cleanup_is_best_effort(alerts) -> None:
    webhooks = MemoryWebhooks()
    webhooks.fail_cleanup = True
    client = _client(alerts, webhooks)
    alert_id = f"cleanup-{alerts.suffix}"
    created = client.post(
        "/api/trading/alerts",
        json={
            "alert_id": alert_id,
            "instrument_id": alerts.instrument,
            "conditions": [{"source": CLOSE, "operator": "greater_than", "target": {"kind": "value", "value": "1"}}],
            "parameters": {"delivery": {"webhook": {"url": HOOK}}},
        },
    )
    assert created.status_code == 201
    assert webhooks.of(alerts.context.workspace_id, alert_id) == [{"url": HOOK, "secret": ""}]


def test_webhook_channel_writes_need_an_https_public_destination(alerts) -> None:
    client = _client(alerts, MemoryWebhooks())
    body = {
        "alert_id": f"hook-{alerts.suffix}",
        "instrument_id": alerts.instrument,
        "conditions": [{"source": CLOSE, "operator": "greater_than", "target": {"kind": "value", "value": "1"}}],
    }

    def channel(url: str):
        return client.post(
            "/api/trading/alerts",
            json={**body, "parameters": {"notification_channels": ["webhook"], "delivery": {"webhook": {"url": url}}}},
        )

    for refused in ("http://hooks.example.com/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "https://169.254.169.254/x"):
        assert channel(refused).status_code == 422, refused
    created = channel(HOOK)
    assert created.status_code == 201, created.text
    assert "tokenvalue" not in created.text
    deliveries = client.get("/api/trading/alerts/deliveries", params={"alert_id": body["alert_id"]})
    assert deliveries.status_code == 200
    assert deliveries.json() == {"deliveries": []}

    # A trigger queues a delivery; the listing shows its status, never the destination.
    observed = datetime(2090, 1, 1, tzinfo=timezone.utc)
    for minute, price in enumerate(("0.5", "2")):
        client.post(
            "/api/trading/alerts/evaluate",
            json={"instrument_id": alerts.instrument, "observed_price": price, "observed_at": (observed + timedelta(minutes=minute)).isoformat()},
        )
    listed = client.get("/api/trading/alerts/deliveries", params={"alert_id": body["alert_id"]})
    [queued] = listed.json()["deliveries"]
    assert (queued["channel"], queued["status"], queued["attempts"]) == ("webhook", "pending", 0)
    assert "tokenvalue" not in listed.text and "hooks.example.com" not in listed.text

    # Updates follow the same rule: the webhook channel needs a webhook.
    revision = created.json()["revision"]
    no_hook = client.put(
        f"/api/trading/alerts/{body['alert_id']}",
        json={**body, "parameters": {"notification_channels": ["webhook"]}},
        headers={"If-Match": str(revision)},
    )
    assert no_hook.status_code == 422
