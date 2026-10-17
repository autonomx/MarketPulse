from __future__ import annotations

import os

from app.apps.trading.strategy_research_monitor import strategy_research_monitor_enabled


def test_research_monitor_is_disabled_in_legacy_test_mode(monkeypatch):
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE","legacy_test");monkeypatch.delenv("OMNIX_TRADING_RESEARCH_MONITOR_IN_TESTS",raising=False)
    assert strategy_research_monitor_enabled() is False


def test_research_monitor_can_be_explicitly_enabled_in_tests(monkeypatch):
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE","legacy_test");monkeypatch.setenv("OMNIX_TRADING_RESEARCH_MONITOR_IN_TESTS","1")
    assert strategy_research_monitor_enabled() is True
