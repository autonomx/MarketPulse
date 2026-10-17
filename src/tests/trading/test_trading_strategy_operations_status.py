from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.prospective_gap_monitor import ProspectiveGapMonitor
from app.apps.trading.providers.alpaca_iex_status import AlpacaIexStatusMonitor
from app.apps.trading.strategy_deep_recovery_monitor import TradingStrategyDeepRecoveryShadowMonitor
from app.apps.trading.strategy_monitor import TradingStrategyMonitor
from app.apps.trading.strategy_operations_api import create_trading_strategy_operations_router
from app.apps.trading.strategy_universe_archive_monitor import TradingStrategyUniverseArchiveMonitor
from app.apps.trading.strategy_v2_qualification_monitor import TradingStrategyV2QualificationMonitor
from app.apps.trading.yahoo_acquisition_monitor import TradingYahooAcquisitionMonitor


def _core_status(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if key != "details"}


def test_strategy_operations_status_reports_registered_monitor_runtime_without_execution_authority(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.setenv("OMNIX_TRADING_STRATEGY_MONITOR_IN_TESTS", "0")
    monkeypatch.setenv("OMNIX_TRADING_DEEP_RECOVERY_SHADOW_MONITOR_IN_TESTS", "0")
    monkeypatch.setenv("OMNIX_TRADING_UNIVERSE_ARCHIVER_IN_TESTS", "0")
    monkeypatch.setenv("OMNIX_TRADING_V2_QUALIFICATION_IN_TESTS", "0")
    monkeypatch.setenv("OMNIX_ALPACA_API_KEY_ID", "test-key")
    monkeypatch.setenv("OMNIX_ALPACA_API_SECRET_KEY", "test-secret")

    app = FastAPI()
    app.state._omnix_trading_strategy_monitor = TradingStrategyMonitor(interval_seconds=17)
    app.state._omnix_trading_strategy_deep_recovery_shadow_monitor = TradingStrategyDeepRecoveryShadowMonitor(interval_seconds=23)
    app.state._omnix_trading_strategy_universe_archive_monitor = TradingStrategyUniverseArchiveMonitor(interval_seconds=19)
    app.state._omnix_trading_strategy_v2_qualification_monitor = TradingStrategyV2QualificationMonitor(interval_seconds=61)
    app.state._omnix_alpaca_iex_status_monitor = AlpacaIexStatusMonitor()
    app.include_router(create_trading_strategy_operations_router())

    response = TestClient(app).get("/api/trading/strategy-operations/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_authority"] is False
    assert _core_status(payload["strategy_monitor"]) == {
        "configured_enabled": False,
        "registered": True,
        "running": False,
        "interval_seconds": 17.0,
        "last_run_at": None,
        "last_error": None,
        "counters": {
            "evaluation_count": 0,
            "signal_count": 0,
            "paper_order_count": 0,
            "intraday_learning_snapshot_count": 0,
            "intraday_llm_call_count": 0,
            "intraday_llm_assessment_count": 0,
            "intraday_llm_error_count": 0,
            "intraday_llm_input_character_count": 0,
            "intraday_llm_input_token_count": 0,
            "intraday_llm_output_token_count": 0,
            "intraday_llm_total_token_count": 0,
            "intraday_llm_estimated_usage_count": 0,
            "auto_paper_ready_strategy_count": 0,
            "auto_paper_blocked_strategy_count": 0,
            "auto_paper_archive_not_ready_strategy_count": 0,
            "auto_paper_qualification_blocked_strategy_count": 0,
        },
    }
    assert _core_status(payload["deep_recovery_shadow_monitor"]) == {
        "configured_enabled": False,
        "registered": True,
        "running": False,
        "interval_seconds": 23.0,
        "last_run_at": None,
        "last_error": None,
        "counters": {
            "evaluation_count": 0,
            "state_transition_count": 0,
            "signal_count": 0,
            "execution_observation_count": 0,
        },
    }
    assert payload["strategy_monitor"]["details"]["candidate_arbitration"] == "observed_at_quality_score_discovery_rank_instrument"
    assert payload["deep_recovery_shadow_monitor"]["details"]["setup_id"] == "deep_recovery_continuation_v1"
    assert payload["deep_recovery_shadow_monitor"]["details"]["execution_authority"] is False
    assert payload["alpaca_status_monitor"]["details"] == {}
    assert payload["universe_archive_monitor"]["interval_seconds"] == 19.0
    assert payload["universe_archive_monitor"]["counters"] == {"archive_count": 0}
    assert payload["v2_qualification_monitor"]["interval_seconds"] == 61.0
    assert payload["v2_qualification_monitor"]["counters"] == {"replay_count": 0}
    assert _core_status(payload["alpaca_status_monitor"]) == {
        "configured_enabled": True,
        "registered": True,
        "running": False,
        "interval_seconds": None,
        "last_run_at": None,
        "last_error": None,
        "counters": {
            "reconnect_count": 0,
            "disconnect_count": 0,
            "known_halts": 0,
            "history_symbols": 0,
        },
    }


def test_strategy_operations_status_marks_missing_monitor_unregistered(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.setenv("OMNIX_TRADING_STRATEGY_MONITOR_IN_TESTS", "1")
    monkeypatch.setenv("OMNIX_TRADING_DEEP_RECOVERY_SHADOW_MONITOR_IN_TESTS", "1")
    monkeypatch.setenv("OMNIX_TRADING_UNIVERSE_ARCHIVER_IN_TESTS", "1")
    monkeypatch.setenv("OMNIX_TRADING_V2_QUALIFICATION_IN_TESTS", "1")
    monkeypatch.setenv("OMNIX_ALPACA_API_KEY_ID", "test-key")
    monkeypatch.setenv("OMNIX_ALPACA_API_SECRET_KEY", "test-secret")

    app = FastAPI()
    app.include_router(create_trading_strategy_operations_router())
    payload = TestClient(app).get("/api/trading/strategy-operations/status").json()

    for key in (
        "strategy_monitor",
        "deep_recovery_shadow_monitor",
        "universe_archive_monitor",
        "v2_qualification_monitor",
        "alpaca_status_monitor",
    ):
        assert payload[key]["configured_enabled"] is True
        assert payload[key]["registered"] is False
        assert payload[key]["running"] is False



def test_yahoo_acquisition_status_exposes_runtime_health(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.setenv("OMNIX_TRADING_YAHOO_ACQUISITION_IN_TESTS", "1")

    app = FastAPI()
    monitor = TradingYahooAcquisitionMonitor(interval_seconds=31)
    monitor.capture_count = 7
    monitor.capture_error_count = 2
    monitor.active_symbol_count = 10
    app.state._omnix_trading_yahoo_acquisition_monitor = monitor
    app.include_router(create_trading_strategy_operations_router())

    response = TestClient(app).get(
        "/api/trading/strategy-operations/yahoo-acquisition-status"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured_enabled"] is True
    assert payload["registered"] is True
    assert payload["running"] is False
    assert payload["interval_seconds"] == 31.0
    assert payload["counters"] == {
        "capture_count": 7,
        "capture_error_count": 2,
        "active_symbol_count": 10,
    }
    assert payload["details"]["authority"] == "acquisition_only"
    assert payload["details"]["execution_authority"] is False



def test_strategy_operations_status_exposes_prospective_gap_runtime(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.setenv("OMNIX_TRADING_PROSPECTIVE_GAP_MONITOR_IN_TESTS", "1")

    app = FastAPI()
    monitor = ProspectiveGapMonitor(interval_seconds=60)
    monitor.confirmation_run_count = 11
    monitor.postclose_finalize_count = 1
    monitor.no_session_count = 2
    app.state._omnix_prospective_gap_monitor = monitor
    app.include_router(create_trading_strategy_operations_router())

    response = TestClient(app).get("/api/trading/strategy-operations/status")

    assert response.status_code == 200
    status = response.json()["prospective_gap_monitor"]
    assert status["configured_enabled"] is True
    assert status["registered"] is True
    assert status["running"] is False
    assert status["interval_seconds"] == 60.0
    assert status["counters"] == {
        "confirmation_run_count": 11,
        "postclose_finalize_count": 1,
        "no_session_count": 2,
        "scheduler_handoff_ingest_count": 0,
        "scheduler_handoff_error_count": 0,
    }
