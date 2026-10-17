from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.persistence.blob_store import BlobIntegrityError, LocalBlobStore
from app.persistence.tenant import local_tenant_context
from app.apps.trading.alerts_api import create_trading_alert_router
from app.apps.trading.api import create_trading_router
from app.apps.trading.backtest import BacktestLogEntry, BacktestRunResult
from app.apps.trading.catalyst_api import create_trading_catalyst_router
from app.apps.trading.execution_api import create_trading_execution_router
from app.apps.trading.model_api import create_trading_model_router
from app.apps.trading.paper_api import create_trading_paper_router
from app.apps.trading.replay_api import create_trading_replay_router
from app.apps.trading.replay_repository import TradingReplayRepository
from app.apps.trading.replay_runtime_repository import TradingReplayRuntimeRepository
from app.apps.trading.research_api import create_trading_research_router
from app.apps.trading.scanner_api import create_trading_scanner_router
from app.apps.trading.strategy_api import create_trading_strategy_router


NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, parameters=()):
        self.statements.append((statement, tuple(parameters)))
        return self


class FakeUnitOfWork:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self) -> None:
        self.committed = True


def minimal_backtest() -> BacktestRunResult:
    return BacktestRunResult(
        run_id="release-artifact-run",
        dataset_id="dataset-1",
        dataset_fingerprint="f" * 64,
        strategy_id="sma_cross",
        strategy_parameters={"fast_period": 2, "slow_period": 3},
        execution_policy={"fill_timing": "next_bar_open"},
        formula_version="omnix-indicators-v2",
        status="completed",
        initial_cash=Decimal("10000"),
        final_equity=Decimal("10100"),
        total_return_percent=Decimal("1"),
        max_drawdown_percent=Decimal("0.5"),
        win_rate_percent=Decimal("50"),
        exposure_percent=Decimal("25"),
        trade_count=0,
        started_at=NOW,
        finished_at=NOW,
        trades=(),
        equity_curve=(),
        logs=(BacktestLogEntry(log_index=0, message="release fixture"),),
    )


def test_all_trading_product_routes_are_registered_in_openapi() -> None:
    app = FastAPI()
    for router in (
        create_trading_router(),
        create_trading_execution_router(),
        create_trading_alert_router(),
        create_trading_scanner_router(),
        create_trading_replay_router(),
        create_trading_paper_router(),
        create_trading_research_router(),
        create_trading_strategy_router(),
        create_trading_catalyst_router(),
        create_trading_model_router(),
    ):
        app.include_router(router)
    paths = set(app.openapi()["paths"])
    required = {
        "/api/trading/instruments/search",
        "/api/trading/providers/status",
        "/api/trading/execution/observation",
        "/api/trading/alerts",
        "/api/trading/scanners",
        "/api/trading/replay/datasets",
        "/api/trading/replay/backtests",
        "/api/trading/paper/accounts",
        "/api/trading/paper/accounts/{account_id}/protections",
        "/api/trading/research",
        "/api/trading/strategies",
        "/api/trading/strategies/universes/freeze",
        "/api/trading/strategies/backtest/gap-pullback",
        "/api/trading/catalysts/evidence",
        "/api/trading/catalysts/classify-shadow",
        "/api/trading/models/bounce/train",
        "/api/trading/models/bounce/score-shadow",
    }
    assert required <= paths

    gateway = Path("src/app/apps/trading/route_registration.py").read_text(encoding="utf-8")
    for registration in (
        "create_trading_router",
        "create_trading_execution_router",
        "create_trading_alert_router",
        "create_trading_scanner_router",
        "create_trading_replay_router",
        "create_trading_paper_router",
        "create_trading_research_router",
        "create_trading_strategy_router",
        "create_trading_catalyst_router",
        "create_trading_model_router",
        "create_trading_alert_monitor_task",
        "create_trading_paper_monitor_task",
        "create_trading_strategy_monitor_task",
    ):
        assert registration in gateway


def test_backtest_artifact_is_checksummed_and_corruption_is_detected(
    monkeypatch,
    tmp_path,
) -> None:
    connection = FakeConnection()
    store = LocalBlobStore(tmp_path / "blobs")

    def relational_save(_repository, result):
        return result

    monkeypatch.setattr(TradingReplayRepository, "save_backtest", relational_save)
    repository = TradingReplayRuntimeRepository(
        context=local_tenant_context(),
        uow_factory=lambda: FakeUnitOfWork(connection),
        blob_store=store,
    )
    saved = repository.save_backtest(minimal_backtest())
    assert saved.artifact is not None
    assert saved.artifact.storage_provider == "local-filesystem"
    assert len(saved.artifact.checksum_sha256) == 64
    payload = store.read_bytes(
        saved.artifact.storage_key,
        expected_checksum=saved.artifact.checksum_sha256,
    )
    decoded = json.loads(payload)
    assert decoded["run_id"] == saved.run_id
    assert decoded["win_rate_percent"] == "50"
    assert decoded["exposure_percent"] == "25"
    assert any("artifact_checksum_sha256" in statement for statement, _ in connection.statements)

    artifact_path = store.root.joinpath(*saved.artifact.storage_key.split("/"))
    artifact_path.write_bytes(b"corrupt")
    with pytest.raises(BlobIntegrityError):
        store.read_bytes(
            saved.artifact.storage_key,
            expected_checksum=saved.artifact.checksum_sha256,
        )


def test_no_live_broker_or_ai_mutation_surface_exists() -> None:
    trading_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/app/apps/trading").glob("*.py")
    ).lower()
    for forbidden in (
        "submit_live_order",
        "broker_credentials",
        "interactive_brokers",
        "alpaca_trade_api",
        "/api/trading/live",
    ):
        assert forbidden not in trading_source

    research = Path(
        "src/app/apps/trading/research/market_research.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden in (
        "place_order",
        "create_alert",
        "process_observation",
        "run_backtest",
        "lmstudio",
        "openrouter",
        "cerebras",
    ):
        assert forbidden not in research

    strategy_monitor = "\n".join(path.read_text(encoding="utf-8") for path in [*sorted(Path("src/app/apps/trading").glob("strategy_monitor*.py")), Path("src/app/apps/trading/strategy_entry_path.py")])
    assert '"live_broker_enabled": False' in strategy_monitor
    assert '"ai_order_placement_enabled": False' in strategy_monitor
    assert "bounce_model" not in strategy_monitor.lower()
    assert "catalyst_shadow" not in strategy_monitor.lower()


def test_ui_controls_accessibility_and_attribution_are_structural_invariants() -> None:
    side_panel = Path(
        "web/src/features/trading/TradingSidePanel.tsx"
    ).read_text(encoding="utf-8")
    assert 'role="tablist"' in side_panel
    assert 'role="tab"' in side_panel
    assert 'aria-selected=' in side_panel
    assert "onClick={() => setActiveTab" in side_panel
    assert "TradingResearchPanel" in side_panel

    workspace = Path(
        "web/src/features/trading/TradingWorkspace.tsx"
    ).read_text(encoding="utf-8")
    assert "TradingComplianceFooter" not in workspace
    assert "TradingScannerPanel" in workspace
    assert "TradingReplayPanel" in workspace
    assert "TradingPaperPanel" in workspace
    assert "TradingStrategiesPanel" in workspace

    strategy_panel = Path(
        "web/src/features/trading/TradingStrategiesPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "Freeze point-in-time gapper universe" in strategy_panel
    assert "AI and model scores are shadow-only" in strategy_panel
    assert "No live broker route" in strategy_panel

    styles = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("web/src/features/trading").glob("*.css")
    )
    assert "prefers-reduced-motion" in styles
    assert ":focus-visible" in styles

    assert not Path(
        "web/src/features/trading/TradingComplianceFooter.tsx"
    ).exists()


def test_legal_operator_and_release_records_are_present() -> None:
    notices = Path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "TradingView Lightweight Charts" in notices
    assert "Apache License 2.0" in notices
    assert "tradingview-mcp" in notices
    assert "Copyright (c) 2025 Ahmet Taner Atila" in notices

    certification = Path(
        "docs/architecture/OMNIX_TRADING_RELEASE_CERTIFICATION.md"
    ).read_text(encoding="utf-8")
    assert "Pending environment run" in certification
    assert "PR #1488 remains draft" in certification

    operations = Path(
        "docs/architecture/OMNIX_TRADING_OPERATIONS.md"
    ).read_text(encoding="utf-8")
    assert "Provider outage procedure" in operations
    assert "Corrupt or missing artifact" in operations
    assert "Rollback" in operations
    assert "Gap-pullback automation" in operations
    assert "execution-grade" in operations.lower()
    assert "AUTO PAPER" in operations

    security = Path(
        "docs/architecture/OMNIX_TRADING_SECURITY_LEGAL.md"
    ).read_text(encoding="utf-8")
    assert "does not provide live brokerage execution" in security
    assert "Market-data rights" in security


def test_release_migrations_preserve_integrity_evidence() -> None:
    sequencing = Path(
        "src/app/apps/trading/migrations/0024_trading_backtest_bar_indices.sql"
    ).read_text(encoding="utf-8")
    assert "signal_bar_index IS NULL AND fill_bar_index IS NULL" in sequencing
    assert "fill_bar_index = signal_bar_index + 1" in sequencing
    assert "trade_index * 2" not in sequencing

    artifacts = Path(
        "src/app/apps/trading/migrations/0025_trading_backtest_artifacts.sql"
    ).read_text(encoding="utf-8")
    for column in (
        "win_rate_percent",
        "exposure_percent",
        "artifact_storage_provider",
        "artifact_storage_key",
        "artifact_checksum_sha256",
        "artifact_byte_size",
    ):
        assert column in artifacts
    assert "length(artifact_checksum_sha256) = 64" in artifacts

    strategy = Path(
        "src/app/apps/trading/migrations/0038_trading_strategy_automation.sql"
    ).read_text(encoding="utf-8")
    for table in (
        "omnix_trading_strategy_configs",
        "omnix_trading_strategy_events",
        "omnix_trading_gapper_universes",
        "omnix_trading_catalyst_evidence",
        "omnix_trading_model_scores",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in strategy

    paper_protection = Path(
        "src/app/apps/trading/migrations/0039_trading_paper_protections.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS omnix_trading_paper_protections" in paper_protection

    model_artifacts = Path(
        "src/app/apps/trading/migrations/0040_trading_model_artifacts.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS omnix_trading_model_artifacts" in model_artifacts
    assert "CHECK (shadow_only = TRUE)" in model_artifacts
