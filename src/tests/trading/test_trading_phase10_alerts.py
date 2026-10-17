from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.persistence.errors import RevisionConflict
from app.apps.trading.alerts import AlertEvaluationContext, AlertOutcomeRecord, TradingAlert, TradingAlertCreate, TradingAlertEvaluation, TradingAlertEvaluationPolicy, TradingAlertTrigger, TradingAlertUpdate, cooldown_elapsed
from app.apps.trading.alerts_repository import AlertListing, AlertLockState
from app.apps.trading.alerts_api import create_trading_alert_router
from app.apps.trading.alerts_evaluation import evaluate_conditions
from app.apps.trading.alerts_monitor import (
    TradingAlertMonitor,
    _final_only,
    _history_limit,
    trading_alert_monitor_enabled,
)


NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
INSTRUMENT = "crypto:BINANCE:spot:BTC-USDT"


class FakeAlertRepository:
    def __init__(self) -> None:
        self.alerts: dict[str, TradingAlert] = {}
        self.triggers: list[TradingAlertTrigger] = []
        self.evaluations: list[TradingAlertEvaluation] = []
        self.recorded: list[tuple[AlertEvaluationContext, list[AlertOutcomeRecord]]] = []

    context = SimpleNamespace(workspace_id="workspace:test")

    def list_alerts(self, limit: int = 200):
        return list(self.alerts.values())[:limit]

    def list_alerts_report(self, limit: int = 200):
        return AlertListing(self.list_alerts(limit), [])

    @contextmanager
    def alert_transaction(self, alert_id: str):
        yield AlertLockState(exists=alert_id in self.alerts, webhook_ref=None)

    def create(self, request: TradingAlertCreate, *, webhook_ref=None):
        alert = TradingAlert(
            **request.model_dump(),
            enabled=True,
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )
        self.alerts[alert.alert_id] = alert
        return alert

    def get(self, alert_id: str):
        return self.alerts.get(alert_id)

    def update(self, alert_id: str, request: TradingAlertUpdate, expected_revision: int, *, webhook_ref=None):
        current = self.alerts[alert_id]
        if current.revision != expected_revision:
            raise RevisionConflict("stale alert")
        updated = TradingAlert.model_validate(
            {
                **current.model_dump(),
                **request.model_dump(),
                "revision": current.revision + 1,
                "updated_at": NOW + timedelta(minutes=1),
            }
        )
        self.alerts[alert_id] = updated
        return updated

    def archive(self, alert_id: str, expected_revision: int):
        current = self.alerts[alert_id]
        if current.revision != expected_revision:
            raise RevisionConflict("stale alert")
        self.alerts.pop(alert_id)
        archived = current.model_copy(
            update={"enabled": False, "revision": current.revision + 1}
        )
        return archived

    def list_triggers(self, limit: int = 200):
        return self.triggers[:limit]

    def evaluate(self, evaluation: TradingAlertEvaluation):
        self.evaluations.append(evaluation)
        trigger = TradingAlertTrigger(
            trigger_id=f"trigger-{len(self.evaluations)}",
            alert_id="price-alert",
            instrument_id=evaluation.instrument_id,
            binding_id=evaluation.resolved_binding_id,
            provider=evaluation.provider,
            observed_value=evaluation.observed_price,
            observed_price=evaluation.observed_price,
            threshold=Decimal("100"),
            condition_type="price_above",
            observed_at=evaluation.observed_at,
            evaluated_at=evaluation.evaluated_at,
            idempotency_key=f"key-{len(self.evaluations)}",
            payload={
                "requested_binding_id": evaluation.binding_id,
                "resolved_binding_id": evaluation.resolved_binding_id,
            },
        )
        self.triggers = [trigger, *self.triggers]
        return [trigger]


    def record_outcomes(self, context: AlertEvaluationContext, outcomes):
        outcomes = list(outcomes)
        self.recorded.append((context, outcomes))
        first = outcomes[0].outcome
        trigger = TradingAlertTrigger(
            trigger_id=f"trigger-{len(self.recorded)}",
            alert_id=outcomes[0].alert_id,
            instrument_id=context.instrument_id,
            binding_id=context.resolved_binding_id,
            provider=context.provider,
            observed_value=first.close,
            observed_price=first.close,
            threshold=Decimal("100"),
            condition_type="price_above",
            observed_at=first.bar_end,
            evaluated_at=context.evaluated_at,
            idempotency_key=f"recorded-{len(self.recorded)}",
        )
        return [trigger]


class FakeMarketService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, str | None]] = []

    def bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        binding_id: str | None = None,
    ):
        self.calls.append((instrument_id, interval, limit, binding_id))
        bars = [
            SimpleNamespace(
                start_time=NOW + timedelta(minutes=index - 1),
                open=Decimal(90 + index),
                close=Decimal(90 + index),
                high=Decimal(91 + index),
                low=Decimal(89 + index),
                volume=Decimal(1_000 + index),
                is_final=True,
                end_time=NOW + timedelta(minutes=index),
            )
            for index in range(40)
        ]
        return SimpleNamespace(
            bars=bars,
            binding=SimpleNamespace(
                binding_id="fixture:resolved",
                provider="fixture-provider",
            ),
        )


def alert(
    alert_id: str,
    condition_type: str,
    threshold: Decimal = Decimal("100"),
    **parameters,
) -> TradingAlert:
    return TradingAlert(
        alert_id=alert_id,
        instrument_id=INSTRUMENT,
        binding_id="fixture:requested",
        condition_type=condition_type,
        threshold=threshold,
        parameters=parameters,
        evaluation_policy={"interval": "1m", "allow_partial_bars": False},
        enabled=True,
        cooldown_seconds=0,
        revision=1,
    )


def test_alert_migration_uses_dedicated_complete_authority_tables() -> None:
    migration = Path("src/app/apps/trading/migrations/0020_trading_alerts.sql").read_text()
    trendline_migration = Path(
        "src/app/apps/trading/migrations/0036_trading_trendline_alerts.sql"
    ).read_text()
    assert "CREATE TABLE IF NOT EXISTS omnix_trading_alerts" in migration
    assert "CREATE TABLE IF NOT EXISTS omnix_trading_alert_triggers" in migration
    assert "percent_change_above" in migration
    assert "indicator_cross_below" in migration
    assert "volume_above" in migration
    assert "evaluation_policy JSONB" in migration
    assert "evaluated_at TIMESTAMPTZ" in migration
    assert "UNIQUE (workspace_id, idempotency_key)" in migration
    assert "omnix_module_records" not in migration
    assert "trendline_crossing_up" in trendline_migration
    assert "trendline_below" in trendline_migration


def test_all_legacy_alert_families_fire_on_a_crossing() -> None:
    for condition_type, parameters in (
        ("price_above", {}),
        ("percent_change_above", {}),
        ("indicator_above", {"indicator_id": "rsi"}),
        ("indicator_cross_above", {"indicator_id": "rsi"}),
        ("volume_above", {}),
    ):
        assert alert("a", condition_type, **parameters).conditions[0].operator == "crossing_up"
    for condition_type, parameters in (
        ("price_below", {}),
        ("percent_change_below", {}),
        ("indicator_below", {"indicator_id": "rsi"}),
        ("indicator_cross_below", {"indicator_id": "rsi"}),
        ("volume_below", {}),
    ):
        assert alert("a", condition_type, **parameters).conditions[0].operator == "crossing_down"


def test_finalized_bar_policy_is_explicit() -> None:
    assert TradingAlertEvaluationPolicy().allow_partial_bars is False
    assert TradingAlertEvaluationPolicy(allow_partial_bars=True).allow_partial_bars is True
    closed_only = alert("price", "price_above")
    assert _final_only(closed_only)
    partial = closed_only.model_copy(update={"evaluation_policy": TradingAlertEvaluationPolicy(allow_partial_bars=True)})
    assert not _final_only(partial)
    assert _final_only(partial.model_copy(update={"frequency": "once_per_bar_close"}))


def test_trendline_alerts_compare_price_with_extrapolated_line() -> None:
    trendline = alert(
        "trendline",
        "trendline_crossing_up",
        threshold=Decimal("0"),
        trendline_points=[
            {"time": NOW.isoformat(), "price": "100"},
            {"time": (NOW + timedelta(minutes=10)).isoformat(), "price": "110"},
        ],
        trendline_mode="crossing_up",
    )
    bars = [
        SimpleNamespace(
            start_time=NOW + timedelta(minutes=minute - 1),
            end_time=NOW + timedelta(minutes=minute),
            open=Decimal(close), high=Decimal(close), low=Decimal(close), close=Decimal(close),
            volume=Decimal("1"), is_final=True,
        )
        for minute, close in ((14, "113"), (15, "116"))
    ]
    outcome = evaluate_conditions(trendline.conditions, bars, final_only=True)
    assert outcome is not None and outcome.met
    assert outcome.observations[0].target == Decimal("115")


def test_cooldown_boundaries_are_deterministic() -> None:
    assert not cooldown_elapsed(NOW, NOW + timedelta(seconds=59), 60)
    assert cooldown_elapsed(NOW, NOW + timedelta(seconds=60), 60)


def test_alert_monitor_groups_targets_and_calculates_all_condition_inputs() -> None:
    repository = FakeAlertRepository()
    repository.alerts = {
        item.alert_id: item
        for item in (
            alert("price-alert", "price_above"),
            alert("percent-alert", "percent_change_above", lookback_bars=5),
            alert("indicator-alert", "indicator_cross_above", indicator_id="rsi", period=14),
            alert("volume-alert", "volume_above"),
        )
    }
    market = FakeMarketService()
    monitor = TradingAlertMonitor(
        repository_factory=lambda: repository,
        market_service_factory=lambda: market,
        interval_seconds=5,
    )

    assert asyncio.run(monitor.run_once()) == 1
    assert len(market.calls) == 1
    assert market.calls[0][0:2] == (INSTRUMENT, "1m")
    assert market.calls[0][2] == _history_limit(list(repository.alerts.values()))
    assert repository.evaluations == []
    context, outcomes = repository.recorded[0]
    assert context.binding_id == "fixture:requested"
    assert context.resolved_binding_id == "fixture:resolved"
    assert context.provider == "fixture-provider"
    assert {record.alert_id for record in outcomes} == set(repository.alerts)
    by_alert = {record.alert_id: record.outcome for record in outcomes}
    assert by_alert["volume-alert"].observations[0].source == Decimal("1039")
    assert by_alert["percent-alert"].observations[0].source == (Decimal(129) / Decimal(124) - 1) * 100
    assert by_alert["indicator-alert"].observations[0].source == Decimal("100")  # RSI of a steady climb
    assert all(outcome.bar_is_final for outcome in by_alert.values())
    assert monitor.diagnostics()["evaluation_count"] == 1


def test_alert_monitor_clears_its_error_after_a_clean_pass() -> None:
    repository = FakeAlertRepository()
    repository.alerts = {"price-alert": alert("price-alert", "price_above")}
    market = FakeMarketService()
    failing = SimpleNamespace(bars=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("feed down")))
    services = iter([failing, market])
    monitor = TradingAlertMonitor(
        repository_factory=lambda: repository,
        market_service_factory=lambda: next(services),
        interval_seconds=5,
    )
    asyncio.run(monitor.run_once())
    assert monitor.diagnostics()["last_error"] == "RuntimeError: feed down"
    asyncio.run(monitor.run_once())
    assert monitor.diagnostics()["last_error"] is None
    assert monitor.diagnostics()["unreadable_alert_count"] == 0


def test_alert_monitor_is_disabled_in_legacy_tests_by_default(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.delenv("OMNIX_TRADING_ALERT_MONITOR_IN_TESTS", raising=False)
    assert trading_alert_monitor_enabled() is False


def test_alert_routes_support_revisioned_policy_and_trigger_history() -> None:
    repository = FakeAlertRepository()
    app = FastAPI()
    webhooks = SimpleNamespace(
        available=lambda: True, load=lambda ref: None, save=lambda *args: None,
        delete=lambda ref: None, delete_alert=lambda *args, **kwargs: None,
    )
    app.include_router(create_trading_alert_router(repository_factory=lambda: repository, webhook_store=webhooks))
    client = TestClient(app)

    created = client.post(
        "/api/trading/alerts",
        json={
            "alert_id": "alert-1",
            "instrument_id": INSTRUMENT,
            "binding_id": "fixture:requested",
            "condition_type": "indicator_cross_above",
            "threshold": "70",
            "parameters": {"indicator_id": "rsi", "period": 14},
            "evaluation_policy": {
                "interval": "5m",
                "allow_partial_bars": False,
                "formula_version": "omnix-indicators-v2",
            },
            "cooldown_seconds": 60,
        },
    )
    assert created.status_code == 201
    assert created.json()["parameters"]["indicator_id"] == "rsi"
    assert created.json()["evaluation_policy"]["interval"] == "5m"

    listed = client.get("/api/trading/alerts")
    assert listed.status_code == 200
    assert [item["alert_id"] for item in listed.json()["alerts"]] == ["alert-1"]

    current = created.json()
    updated = client.put(
        "/api/trading/alerts/alert-1",
        headers={"If-Match": "1"},
        json={
            "instrument_id": INSTRUMENT,
            "binding_id": "fixture:requested",
            "condition_type": "percent_change_below",
            "threshold": "-5",
            "parameters": {"lookback_bars": 10},
            "evaluation_policy": current["evaluation_policy"],
            "enabled": True,
            "cooldown_seconds": 120,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2

    stale = client.put(
        "/api/trading/alerts/alert-1",
        headers={"If-Match": "1"},
        json={
            "instrument_id": INSTRUMENT,
            "condition_type": "price_below",
            "threshold": "80",
            "enabled": True,
            "cooldown_seconds": 0,
        },
    )
    assert stale.status_code == 409

    evaluated = client.post(
        "/api/trading/alerts/evaluate",
        json={
            "instrument_id": INSTRUMENT,
            "binding_id": "fixture:requested",
            "resolved_binding_id": "fixture:resolved",
            "provider": "fixture-provider",
            "interval": "5m",
            "observed_price": "101",
            "observed_volume": "5000",
            "observed_at": NOW.isoformat(),
            "evaluated_at": (NOW + timedelta(seconds=1)).isoformat(),
        },
    )
    assert evaluated.status_code == 200
    trigger = evaluated.json()["triggers"][0]
    assert trigger["binding_id"] == "fixture:resolved"
    assert trigger["provider"] == "fixture-provider"
    assert trigger["evaluated_at"]
    assert client.get("/api/trading/alerts/triggers").json()["triggers"][0]["idempotency_key"] == "key-1"

    archived = client.delete(
        "/api/trading/alerts/alert-1",
        headers={"If-Match": "2"},
    )
    assert archived.status_code == 200
    assert archived.json()["enabled"] is False
    assert client.get("/api/trading/alerts").json()["alerts"] == []
