from __future__ import annotations

from pathlib import Path

import pytest

from app.apps.trading.strategy_managed_finviz_shadow import (
    INTERDAY_TRADING_STRATEGY_ID,
    INTERDAY_TRADING_SUBSTRATEGY_KEYS,
    managed_finviz_shadow_document,
)
from app.apps.trading.strategy_dynamic_discovery import INTERDAY_SUBSTRATEGIES
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument


def test_interday_group_has_five_embedded_and_three_linked_substrategies() -> None:
    assert len(INTERDAY_TRADING_SUBSTRATEGY_KEYS) == 8
    assert INTERDAY_TRADING_SUBSTRATEGY_KEYS[:5] == (
        "deterministic-v2",
        "stoch-trend-capture",
        "leader-momentum-continuation",
        "ai-every-minute",
        "ai-event-driven",
    )
    assert INTERDAY_TRADING_SUBSTRATEGY_KEYS[-3:] == (
        "stoch-rsi-5min",
        "stoch-rsi-5min-early-single",
        "gap-pullback-v2-prospective-20260825",
    )
    assert INTERDAY_TRADING_SUBSTRATEGY_KEYS == INTERDAY_SUBSTRATEGIES
    assert managed_finviz_shadow_document("paper-test").strategy_id == INTERDAY_TRADING_STRATEGY_ID


def test_strategy_document_rejects_self_parent() -> None:
    payload = managed_finviz_shadow_document("paper-test").model_dump(mode="python")
    payload["parent_strategy_id"] = INTERDAY_TRADING_STRATEGY_ID
    with pytest.raises(ValueError, match="strategy_cannot_parent_itself"):
        TradingStrategyConfigDocument.model_validate(payload)


def test_interday_migration_renames_and_attaches_children() -> None:
    migration = Path(__file__).parents[2] / "app/apps/trading/migrations/0068_trading_interday_strategy_group.sql"
    sql = migration.read_text(encoding="utf-8")

    assert "finviz-learning-v2-shadow" in sql
    assert "interday-trading-strategy-shadow" in sql
    assert "stoch-rsi-5min" in sql
    assert "gap-pullback-v2-prospective-20260825" in sql
    assert "ON UPDATE CASCADE" in sql
    assert "parent_strategy_id" in sql


def test_early_single_migration_clones_stoch_rsi_child_into_interday_group() -> None:
    migration = (
        Path(__file__).parents[2]
        / "app/apps/trading/migrations/0123_trading_interday_stoch_rsi_early_single.sql"
    )
    sql = migration.read_text(encoding="utf-8")

    assert sql.startswith("-- omnix-migration: phase=contract transactional=true")
    assert "'stoch-rsi-5min-early-single'" in sql
    assert "'interday-trading-strategy-shadow'" in sql
    assert "WHERE source.strategy_id = 'stoch-rsi-5min'" in sql
    assert '"trade_selection": "early_single"' in sql
    assert "'shadow'" in sql
    assert "ON CONFLICT (workspace_id, strategy_id) DO NOTHING" in sql
