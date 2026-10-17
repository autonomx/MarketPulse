from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.apps.trading import strategy_universe_archive_monitor as archive_monitor


class _Repository:
    def list_configs(self, *, active_only=False):
        assert active_only is False
        return [SimpleNamespace(strategy_id="restart-test")]


def test_startup_reconciliation_forwards_late_recovery(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(archive_monitor, "default_strategy_repository", lambda: _Repository())

    def archive(config, repository, *, now, allow_late_recovery=False):
        seen.append((config.strategy_id, allow_late_recovery))
        return None

    monkeypatch.setattr(archive_monitor, "archive_daily_universe_if_due", archive)
    monitor = archive_monitor.TradingStrategyUniverseArchiveMonitor(interval_seconds=60)

    assert asyncio.run(monitor.run_once(allow_late_recovery=True)) == 0
    assert seen == [("restart-test", True)]
