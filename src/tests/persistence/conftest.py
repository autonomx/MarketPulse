from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def register_feature_repositories_for_persistence_gates():
    """Use the same lazy feature repository registrations as app composition."""
    from app.persistence.repository_registry import (
        install_repository_specs,
        reset_repository_specs,
    )
    from app.persistence.shared_repository_specs import shared_repository_specs
    from app.runtime.config import RuntimeConfig
    from app.runtime.feature_catalog import enabled_feature_ids, load_feature

    reset_repository_specs()
    install_repository_specs(shared_repository_specs())
    from app.composition.runtime_composition import shared_service_repository_specs

    install_repository_specs(shared_service_repository_specs())
    for feature_id in enabled_feature_ids(RuntimeConfig()):
        install_repository_specs(tuple(load_feature(feature_id).repositories))
    yield
    reset_repository_specs()

from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work
from app.apps.trading.strategy_intraday_llm import IntradayLLMAnalyzer, IntradayLLMResult
from app.apps.trading.strategy_repository import TradingStrategyRepository


@pytest.fixture(scope="session", autouse=True)
def prepare_postgresql_test_runtime():
    """Initialize the disposable PostgreSQL authority explicitly for this test estate."""
    database_url = os.environ.get("OMNIX_TEST_DATABASE_URL")
    if not database_url:
        yield
        return
    from app.persistence.identity_service import ensure_local_identity
    from app.persistence.runtime import ensure_postgresql_runtime_ready
    from app.security.tenant_context import install_process_tenant

    database = PostgresDatabase(DatabaseSettings(url=database_url))
    try:
        ensure_postgresql_runtime_ready(
            database,
            auto_initialize_fresh_install=True,
            apply_schema_changes=False,
        )
        context = ensure_local_identity(database)
        install_process_tenant(context)
        yield
    finally:
        database.close()


_E2E_TEST_NAME = "test_postgres_auto_paper_monitor_persists_order_fill_and_position"
_E2E_STRATEGY_PREFIX = "sep3-postgres-e2e-"


def _disable_stale_auto_paper_e2e_strategies(database_url: str) -> None:
    """Keep repeated local E2E runs isolated inside a reusable test database.

    The PostgreSQL AUTO PAPER E2E intentionally persists production-shaped
    strategy/order/fill/protection state. A developer may run it repeatedly
    against the same disposable database, so prior test strategies must not be
    eligible for the next monitor cycle.
    """

    database = PostgresDatabase(
        DatabaseSettings(
            url=database_url,
            pool_min=1,
            pool_max=2,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-auto-paper-e2e-isolation",
        )
    )
    try:
        context = ensure_local_identity(database)
        repository = TradingStrategyRepository(
            context=context,
            uow_factory=lambda: unit_of_work(database),
        )
        for config in repository.list_configs(active_only=True):
            if not config.strategy_id.startswith(_E2E_STRATEGY_PREFIX):
                continue
            stopped = config.model_copy(
                update={
                    "mode": "off",
                    "enabled": False,
                    "active_universe_id": None,
                }
            )
            repository.update_config(
                config.strategy_id,
                stopped,
                expected_revision=config.revision,
            )
    finally:
        database.close()


def _offline_intraday_llm_assess(self, rows, **kwargs) -> IntradayLLMResult:
    """Deterministic research-only stub for the paper-execution E2E.

    The E2E is proving deterministic qualification -> signal -> risk -> paper
    order/fill/position/protection. Intraday LLM research is non-authoritative
    and has its own tests, so this path must not require LM Studio or network.
    """

    return IntradayLLMResult(
        assessments=(),
        provider="postgres-auto-paper-e2e",
        model="offline-research-stub",
    )


@pytest.fixture(autouse=True)
def isolate_postgres_auto_paper_e2e(request, monkeypatch):
    if request.node.name != _E2E_TEST_NAME:
        yield
        return

    database_url = os.environ.get("OMNIX_TEST_DATABASE_URL")
    if not database_url:
        yield
        return

    _disable_stale_auto_paper_e2e_strategies(database_url)
    monkeypatch.setattr(
        IntradayLLMAnalyzer,
        "assess",
        _offline_intraday_llm_assess,
    )
    try:
        yield
    finally:
        _disable_stale_auto_paper_e2e_strategies(database_url)
