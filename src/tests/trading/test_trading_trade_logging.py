from __future__ import annotations

import json
from decimal import Decimal

from app.apps.trading.trade_logging import trade_log, trade_log_path


def test_trade_audit_log_writes_jsonl_and_redacts_secrets(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OMNIX_TRADE_LOG_DIR", str(tmp_path / "trade"))
    monkeypatch.setenv("OMNIX_TRADE_AUDIT_LOGGING", "1")
    monkeypatch.delenv("OMNIX_INSTANCE_NAME", raising=False)
    monkeypatch.setenv("OMNIX_GATEWAY_BACKGROUND_ROLE", "worker")

    trade_log(
        "auto_trading",
        "risk_decision",
        strategy_id="strategy-1",
        quantity=Decimal("12.5"),
        api_key="should-not-leak",
        nested={
            "authorization": "Bearer should-not-leak-either",
            "safe": "visible",
            "credentials": {"username": "also-hidden-by-parent", "secret": "hidden"},
        },
    )

    path = trade_log_path("auto_trading")
    raw = path.read_text(encoding="utf-8").strip()
    payload = json.loads(raw)

    assert path.parent == tmp_path / "trade"
    assert path.name == "auto_trading.worker.jsonl"
    assert payload["channel"] == "auto_trading"
    assert payload["event"] == "risk_decision"
    assert payload["strategy_id"] == "strategy-1"
    assert payload["quantity"] == "12.5"
    assert payload["api_key"] == "<redacted>"
    assert payload["nested"]["authorization"] == "<redacted>"
    assert payload["nested"]["credentials"] == "<redacted>"
    assert payload["nested"]["safe"] == "visible"
    assert "should-not-leak" not in raw


def test_trade_audit_log_can_be_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OMNIX_TRADE_LOG_DIR", str(tmp_path / "trade"))
    monkeypatch.setenv("OMNIX_TRADE_AUDIT_LOGGING", "0")

    trade_log("backtest", "backtest_requested", strategy_id="strategy-1")

    assert not trade_log_path("backtest").exists()


def test_each_process_writes_its_own_file(monkeypatch, tmp_path) -> None:
    from app.apps.trading.trade_logging import trade_log_process_name

    monkeypatch.setenv("OMNIX_TRADE_LOG_DIR", str(tmp_path / "trade"))
    monkeypatch.delenv("OMNIX_INSTANCE_NAME", raising=False)
    monkeypatch.setenv("OMNIX_GATEWAY_BACKGROUND_ROLE", "job-worker")
    job_worker = trade_log_path("backtest")
    monkeypatch.setenv("OMNIX_INSTANCE_NAME", "api 8001/../x")
    replica = trade_log_path("backtest")

    assert job_worker.name == "backtest.job-worker.jsonl"
    assert replica.name == "backtest.api-8001-..-x.jsonl" and replica.parent == tmp_path / "trade"
    assert trade_log_process_name() == "api-8001-..-x"
