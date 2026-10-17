"""Writable resources move with OMNIX_RESOURCES_DIR (MarketPulse S-2)."""
from __future__ import annotations

from app.apps.trading.trade_logging import trade_log_dir
from app.observability.agent_logging import agent_debug_log_dir
from app.runtime import paths


def test_writable_resources_default_to_the_bundled_resources(monkeypatch):
    monkeypatch.delenv("OMNIX_RESOURCES_DIR", raising=False)

    assert paths.writable_resources_root() == paths.resources_root()


def test_data_and_logs_follow_the_writable_root(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNIX_RESOURCES_DIR", str(tmp_path))
    monkeypatch.delenv("OMNIX_TRADE_LOG_DIR", raising=False)
    monkeypatch.delenv("OMNIX_AGENT_LOG_DIR", raising=False)

    assert paths.resources_data_root() == tmp_path / "data"
    assert (tmp_path / "data").is_dir()
    assert paths.resources_logs_root() == tmp_path / "logs"
    assert trade_log_dir() == tmp_path / "logs" / "trade"
    assert agent_debug_log_dir() == tmp_path / "logs" / "agent"


def test_bundled_resources_stay_in_the_source_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNIX_RESOURCES_DIR", str(tmp_path))

    assert paths.resources_root() == paths.repo_root() / "resources"
    assert (paths.resources_root() / "trading" / "indicator_goldens").is_dir()
