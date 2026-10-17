from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from fastapi import FastAPI

from app.apps.trading import strategy_monitor as monitor_module
from app.apps.trading.strategy_managed_finviz_shadow import (
    MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
    MANAGED_FINVIZ_SHADOW_STRATEGY_ID,
    ManagedFinvizShadowProvisionResult,
    managed_finviz_shadow_config,
    provision_managed_finviz_shadow_strategy,
)
from app.runtime.capabilities import RuntimeCapabilities
from app.runtime.config import RuntimeConfig
from app.runtime.features import FeatureContext
from app.apps.trading.strategy_monitor import create_trading_strategy_monitor_task
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument




def _strategy_monitor_worker(app):
    config = RuntimeConfig()
    return create_trading_strategy_monitor_task(
        FeatureContext(
            feature_id="trading",
            config=None,
            runtime=config,
            capabilities=RuntimeCapabilities.from_config(config),
            services=None,
            logger=logging.getLogger("tests.trading"),
            runtime_state=app.state,
        )
    )


class FakePaperRepository:
    def __init__(self, *, accounts=None) -> None:
        self.accounts = list(accounts or [])
        self.created = []

    def list_accounts(self, limit: int = 100):
        return list(self.accounts[:limit])

    def create_account(self, request):
        self.created.append(request)
        account = SimpleNamespace(
            account_id=request.account_id,
            enabled=True,
        )
        self.accounts.append(account)
        return SimpleNamespace(account=account)


class FakeStrategyRepository:
    def __init__(self, document: TradingStrategyConfigDocument | None = None) -> None:
        self.document = document
        self.created: list[TradingStrategyConfigDocument] = []
        self.updated: list[TradingStrategyConfigDocument] = []

    def get_config(self, strategy_id: str) -> TradingStrategyConfigDocument:
        if self.document is None or self.document.strategy_id != strategy_id:
            raise ValueError("strategy_config_not_found")
        return self.document

    def create_config(
        self,
        document: TradingStrategyConfigDocument,
    ) -> TradingStrategyConfigDocument:
        self.created.append(document)
        self.document = document.model_copy(
            update={
                "revision": 1,
                "created_at": datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
            }
        )
        return self.document

    def update_config(
        self,
        strategy_id: str,
        document: TradingStrategyConfigDocument,
        *,
        expected_revision: int,
    ) -> TradingStrategyConfigDocument:
        assert self.document is not None
        assert strategy_id == self.document.strategy_id
        assert expected_revision == self.document.revision
        self.updated.append(document)
        self.document = document.model_copy(
            update={
                "revision": expected_revision + 1,
                "updated_at": datetime(2026, 9, 2, 12, 1, tzinfo=timezone.utc),
            }
        )
        return self.document


def _managed_existing(
    *,
    mode: str = "shadow",
    enabled: bool = True,
    archived_at=None,
) -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id=MANAGED_FINVIZ_SHADOW_STRATEGY_ID,
        account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        strategy_kind="gap_pullback_v1",
        strategy_version="2.0.0",
        mode=mode,
        active_universe_id=None,
        config=managed_finviz_shadow_config(),
        enabled=enabled,
        archived_at=archived_at,
        revision=4,
    )


def test_managed_profile_is_exact_0915_finviz_top5_shadow_research() -> None:
    config = managed_finviz_shadow_config()

    assert config.strategy_version == "2.0.0"
    assert config.universe_scan_time_et == time(9, 15)
    assert config.universe_discovery_source == "finviz"
    assert config.universe_discovery_count == 5
    assert config.auto_archive_daily_universe is True
    assert config.intraday_learning_enabled is True
    assert config.stoch_trend_capture_enabled is True
    assert config.intraday_llm_enabled is True
    assert config.intraday_llm_top_n == 5
    assert config.intraday_llm_interval_minutes == 10
    assert config.entry_start_et == time(9, 35)
    assert config.last_entry_et == time(11, 30)


def test_first_start_creates_managed_1000_dollar_account_and_shadow_strategy(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_INITIAL_CASH", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)
    paper = FakePaperRepository()
    strategy = FakeStrategyRepository()

    result = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=paper,
    )

    assert result.action == "created"
    assert result.enabled is True
    assert result.mode == "shadow"
    assert len(paper.created) == 1
    assert paper.created[0].account_id == MANAGED_FINVIZ_SHADOW_ACCOUNT_ID
    assert paper.created[0].initial_cash == Decimal("1000")
    assert len(strategy.created) == 1
    saved = strategy.document
    assert saved is not None
    assert saved.strategy_id == MANAGED_FINVIZ_SHADOW_STRATEGY_ID
    assert saved.account_id == MANAGED_FINVIZ_SHADOW_ACCOUNT_ID
    assert saved.mode == "shadow"
    assert saved.enabled is True
    assert saved.config.universe_scan_time_et == time(9, 15)
    assert saved.config.universe_discovery_count == 5
    assert saved.config.stoch_trend_capture_enabled is True


def test_repeated_start_is_idempotent_and_does_not_rewrite_revision(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)
    existing_account = SimpleNamespace(
        account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        enabled=True,
    )
    paper = FakePaperRepository(accounts=[existing_account])
    strategy = FakeStrategyRepository(_managed_existing())

    first = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=paper,
    )
    second = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=paper,
    )

    assert first.action == "unchanged"
    assert second.action == "unchanged"
    assert paper.created == []
    assert strategy.created == []
    assert strategy.updated == []
    assert strategy.document is not None
    assert strategy.document.revision == 4


def test_startup_restores_managed_strategy_if_operator_only_toggled_it_off(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)
    account = SimpleNamespace(
        account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        enabled=False,
    )
    paper = FakePaperRepository(accounts=[account])
    current = _managed_existing(mode="off", enabled=False).model_copy(
        update={
            "active_universe_id": "stale-manual-universe",
            "config": managed_finviz_shadow_config().model_copy(
                update={
                    "universe_discovery_count": 50,
                    "stoch_trend_capture_enabled": False,
                }
            ),
        }
    )
    strategy = FakeStrategyRepository(current)

    result = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=paper,
    )

    assert result.action == "updated"
    assert len(strategy.updated) == 1
    restored = strategy.document
    assert restored is not None
    assert restored.mode == "shadow"
    assert restored.enabled is True
    assert restored.active_universe_id is None
    assert restored.config.universe_discovery_count == 5
    assert restored.config.stoch_trend_capture_enabled is True
    # SHADOW provisioning must not silently reactivate a disabled paper account.
    assert account.enabled is False
    assert paper.created == []


def test_explicit_archive_is_operator_opt_out_and_is_not_resurrected(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)
    account = SimpleNamespace(
        account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        enabled=True,
    )
    archived = _managed_existing(
        mode="off",
        enabled=False,
        archived_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
    )
    strategy = FakeStrategyRepository(archived)
    paper = FakePaperRepository(accounts=[account])

    result = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=paper,
    )

    assert result.action == "archived_suppressed"
    assert result.enabled is False
    assert strategy.updated == []
    assert paper.created == []


def test_concurrent_account_create_race_converges_on_stable_account(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)

    class RacingPaperRepository(FakePaperRepository):
        def create_account(self, request):
            self.created.append(request)
            self.accounts.append(
                SimpleNamespace(account_id=request.account_id, enabled=True)
            )
            raise RuntimeError("duplicate key")

    paper = RacingPaperRepository()
    strategy = FakeStrategyRepository()

    result = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=paper,
    )

    assert result.action == "created"
    assert result.account_id == MANAGED_FINVIZ_SHADOW_ACCOUNT_ID
    assert len(paper.created) == 1


def test_concurrent_strategy_create_race_converges_without_duplicate_failure(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)
    account = SimpleNamespace(
        account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        enabled=True,
    )

    class RacingStrategyRepository(FakeStrategyRepository):
        def create_config(self, document):
            self.created.append(document)
            self.document = document.model_copy(update={"revision": 1})
            raise RuntimeError("duplicate key")

    strategy = RacingStrategyRepository()
    result = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=FakePaperRepository(accounts=[account]),
    )

    assert result.action == "unchanged"
    assert strategy.document is not None
    assert strategy.document.mode == "shadow"
    assert strategy.document.enabled is True


def test_explicit_account_override_must_already_exist(monkeypatch) -> None:
    monkeypatch.setenv(
        "OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID",
        "operator-account",
    )
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)

    try:
        provision_managed_finviz_shadow_strategy(
            strategy_repository=FakeStrategyRepository(),
            paper_repository=FakePaperRepository(),
        )
    except ValueError as exc:
        assert str(exc) == "managed_finviz_shadow_account_not_found:operator-account"
    else:
        raise AssertionError("missing explicit account should fail closed")


@pytest.mark.anyio
async def test_monitor_startup_provisions_before_runner_start(monkeypatch) -> None:
    app = FastAPI()
    worker = _strategy_monitor_worker(app)
    assert worker is not None
    monitor = worker.monitor
    strategy_repo = object()
    paper_repo = object()
    monitor.strategy_repository_factory = lambda: strategy_repo
    monitor.paper_repository_factory = lambda: paper_repo

    calls: list[tuple[object, object]] = []

    monkeypatch.setattr(
        monitor_module,
        "managed_finviz_shadow_autoprovision_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        monitor_module,
        "trading_strategy_monitor_enabled",
        lambda: False,
    )

    def fake_provision(*, strategy_repository, paper_repository):
        calls.append((strategy_repository, paper_repository))
        return ManagedFinvizShadowProvisionResult(
            account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
            action="created",
            enabled=True,
        )

    monkeypatch.setattr(
        monitor_module,
        "provision_managed_finviz_shadow_strategy",
        fake_provision,
    )

    startup = worker.startup[0]
    await startup()

    assert calls == [(strategy_repo, paper_repo)]
    assert monitor.managed_finviz_shadow_provision == {
        "strategy_id": MANAGED_FINVIZ_SHADOW_STRATEGY_ID,
        "account_id": MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        "action": "created",
        "enabled": True,
        "mode": "shadow",
        "detail": None,
    }
    assert monitor.managed_finviz_shadow_provision_error is None
    assert monitor.scheduled is False


@pytest.mark.anyio
async def test_monitor_startup_surfaces_provision_failure_in_health(monkeypatch) -> None:
    app = FastAPI()
    worker = _strategy_monitor_worker(app)
    assert worker is not None
    monitor = worker.monitor
    monitor.strategy_repository_factory = lambda: object()
    monitor.paper_repository_factory = lambda: object()

    monkeypatch.setattr(
        monitor_module,
        "managed_finviz_shadow_autoprovision_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        monitor_module,
        "trading_strategy_monitor_enabled",
        lambda: False,
    )

    def fail_provision(**kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        monitor_module,
        "provision_managed_finviz_shadow_strategy",
        fail_provision,
    )

    startup = worker.startup[0]
    await startup()

    assert monitor.managed_finviz_shadow_provision is None
    assert (
        monitor.managed_finviz_shadow_provision_error
        == "RuntimeError: database unavailable"
    )
    assert monitor.last_error == (
        "managed_finviz_shadow_provision: RuntimeError: database unavailable"
    )
    assert monitor.scheduled is False


def test_legacy_test_mode_does_not_autoprovision_without_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.delenv(
        "OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION_IN_TESTS",
        raising=False,
    )
    paper = FakePaperRepository()
    strategy = FakeStrategyRepository()

    result = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=paper,
    )

    assert result.action == "disabled"
    assert result.enabled is False
    assert paper.created == []
    assert strategy.created == []


def test_startup_preserves_existing_auto_paper_promotion_and_clears_stale_universe(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)
    account = SimpleNamespace(
        account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        enabled=True,
    )
    current = _managed_existing(mode="auto_paper", enabled=True).model_copy(
        update={"active_universe_id": "yesterdays-universe"}
    )
    strategy = FakeStrategyRepository(current)

    result = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=FakePaperRepository(accounts=[account]),
    )

    assert result.action == "updated"
    assert result.mode == "auto_paper"
    assert strategy.document is not None
    assert strategy.document.mode == "auto_paper"
    assert strategy.document.enabled is True
    assert strategy.document.active_universe_id is None


def test_startup_leaves_already_canonical_auto_paper_mode_unchanged(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", raising=False)
    monkeypatch.delenv("OMNIX_PERSISTENCE_MODE", raising=False)
    account = SimpleNamespace(
        account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        enabled=True,
    )
    strategy = FakeStrategyRepository(
        _managed_existing(mode="auto_paper", enabled=True)
    )

    result = provision_managed_finviz_shadow_strategy(
        strategy_repository=strategy,
        paper_repository=FakePaperRepository(accounts=[account]),
    )

    assert result.action == "unchanged"
    assert result.mode == "auto_paper"
    assert strategy.updated == []
