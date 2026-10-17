from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.strategy_dynamic_discovery_monitor import InterdayDynamicDiscoveryMonitor
from app.apps.trading.strategy_interday_learning_monitor import InterdayLearningMonitor
from app.apps.trading.strategy_operations_api import create_trading_strategy_operations_router


def _core_status(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if key != "details"}


def test_interday_operations_status_exposes_registered_monitors(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.setenv("OMNIX_TRADING_DYNAMIC_DISCOVERY_IN_TESTS", "1")
    monkeypatch.setenv("OMNIX_TRADING_INTERDAY_LEARNING_IN_TESTS", "1")

    app = FastAPI()
    discovery = InterdayDynamicDiscoveryMonitor(interval_seconds=71)
    discovery.candidate_count = 7
    learning = InterdayLearningMonitor(interval_seconds=83)
    learning.bridged_count = 11
    learning.report_count = 2
    learning.outcome_count = 5
    learning.qualification_count = 1
    app.state._omnix_interday_dynamic_discovery_monitor = discovery
    app.state._omnix_interday_learning_monitor = learning
    app.include_router(create_trading_strategy_operations_router())

    response = TestClient(app).get("/api/trading/strategy-operations/interday-status")

    assert response.status_code == 200
    payload = response.json()
    assert _core_status(payload["dynamic_discovery_monitor"]) == {
        "configured_enabled": True,
        "registered": True,
        "running": False,
        "interval_seconds": 71.0,
        "last_run_at": None,
        "last_error": None,
        "counters": {"candidate_count": 7},
    }
    assert _core_status(payload["interday_learning_monitor"]) == {
        "configured_enabled": True,
        "registered": True,
        "running": False,
        "interval_seconds": 83.0,
        "last_run_at": None,
        "last_error": None,
        "counters": {
            "bridged_count": 11,
            "report_count": 2,
            "outcome_count": 5,
            "qualification_count": 1,
        },
    }
    assert payload["dynamic_discovery_monitor"]["details"] == {}
    assert payload["interday_learning_monitor"]["details"] == {}
    assert payload["execution_authority"] is False


def test_interday_operations_status_marks_missing_monitors_unregistered(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.setenv("OMNIX_TRADING_DYNAMIC_DISCOVERY_IN_TESTS", "1")
    monkeypatch.setenv("OMNIX_TRADING_INTERDAY_LEARNING_IN_TESTS", "1")

    app = FastAPI()
    app.include_router(create_trading_strategy_operations_router())

    payload = TestClient(app).get(
        "/api/trading/strategy-operations/interday-status"
    ).json()
    for key in ("dynamic_discovery_monitor", "interday_learning_monitor"):
        assert payload[key]["configured_enabled"] is True
        assert payload[key]["registered"] is False
        assert payload[key]["running"] is False
