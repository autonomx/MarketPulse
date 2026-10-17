from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_strategy_monitor_composes_authorization_on_direct_import():
    source_root = Path(__file__).resolve().parents[2]
    repository_root = source_root.parent
    script = textwrap.dedent(
        """
        import asyncio
        import sys
        from datetime import time
        from types import SimpleNamespace

        assert "app.apps.trading" not in sys.modules
        import app.apps.trading.strategy_monitor as strategy_monitor
        # A configuration's pass (where entries are authorized) lives in its own module.
        import app.apps.trading.strategy_monitor_config_run as config_run
        assert config_run.strategy_paper_access.__module__ == "app.apps.trading.order_gateway"

        class StrategyRepository:
            def __init__(self):
                self.events = []

            def recent_events(self, strategy_id, limit):
                return []

            def append_event(self, event):
                self.events.append(event)
                return True

        class PaperRepository:
            def __init__(self):
                self.place_calls = 0

            def place_order(self, account_id, request, *, authority):
                self.place_calls += 1
                raise AssertionError("unauthorized order reached the delegate")

        strategy_repository = StrategyRepository()
        paper_repository = PaperRepository()
        monitor = object.__new__(strategy_monitor.TradingStrategyMonitor)
        monitor.current_run_id = "direct-import-authorization"
        monitor._should_log_diagnostic = lambda *args, **kwargs: False

        async def exercise_authorization(config, repository, guarded_repository, market_service):
            try:
                guarded_repository.place_order
            except AttributeError:
                pass
            else:
                raise AssertionError("a strategy must not reach the repository's order methods")
            try:
                guarded_repository.place_entry(
                    config.account_id,
                    SimpleNamespace(
                        side="buy",
                        order_id="attempt-1",
                        instrument_id="equity:NASDAQ:TEST",
                    ),
                    trade_attempt_id="attempt-1",
                )
            except ValueError as exc:
                assert str(exc).startswith("trade_authorization_denied:")
            else:
                raise AssertionError("missing evidence must deny an entry")

        monitor._reconcile_protections = exercise_authorization
        config = SimpleNamespace(
            strategy_id="test-strategy",
            strategy_kind="gap_pullback_v1",
            strategy_version="1.0.0",
            account_id="paper-account",
            mode="off",
            enabled=False,
            active_universe_id=None,
            config=SimpleNamespace(
                strategy_version="1.0.0",
                entry_start_et=time(0, 0),
                last_entry_et=time(23, 59),
            ),
            risk=SimpleNamespace(
                max_spread_bps=100,
                max_daily_loss_pct=1.5,
                kill_switch=False,
                entry_start_et=time(0, 0),
                last_entry_et=time(23, 59),
            ),
        )

        asyncio.run(
            monitor._run_config(
                config,
                strategy_repository,
                paper_repository,
                object(),
            )
        )
        assert paper_repository.place_calls == 0
        assert len(strategy_repository.events) == 1
        assert strategy_repository.events[0].state == "denied"
        assert "app.apps.trading" in sys.modules
        """
    )
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(source_root), existing_pythonpath) if value
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
