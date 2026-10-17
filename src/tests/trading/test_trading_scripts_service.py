"""Omnix Scripts on the server (TVP-11.1): worker processes, limits, cache and the HTTP surface."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.scripts import ScriptLimits
from app.apps.trading.scripts_api import create_trading_scripts_router
from app.apps.trading.scripts_service import ScriptRunService, ScriptServiceError, bars_for_script, check_script

START = datetime(2026, 8, 3, 14, tzinfo=timezone.utc)


def market_bars(count: int = 30):
    return [
        SimpleNamespace(
            start_time=START + timedelta(minutes=i), open=Decimal(100 + i), high=Decimal(101 + i), low=Decimal(99 + i),
            close=Decimal(100 + i), volume=Decimal(1000), session="regular",
        )
        for i in range(count)
    ]


SMA = """//@version=5
indicator("SMA", overlay=true)
length = input.int(3, "Length")
plot(ta.sma(close, length), "SMA", color=color.blue)
log.info("bar " + str.tostring(bar_index))
"""


@pytest.fixture()
def service():
    instance = ScriptRunService(workers=1, per_user=1, limits=ScriptLimits(max_seconds=5.0))
    yield instance
    instance.close()


def test_a_script_runs_in_a_worker_and_its_result_is_cached(service) -> None:
    bars = bars_for_script(market_bars())
    result = service.run(SMA, bars, inputs={"Length": 2}, symbol="X", timeframe="1", profile=True)
    [plot] = result["plots"]
    assert plot["title"] == "SMA" and plot["values"][0] is None and plot["values"][1] == pytest.approx(100.5)
    assert result["inputs"][0]["title"] == "Length"
    assert result["logs"][-1] == {"time": int((START + timedelta(minutes=29)).timestamp() * 1000), "bar": 29, "level": "info", "message": "bar 29"}
    assert {item["line"] for item in result["profile"]} >= {3, 4}
    assert service.run(SMA, bars, inputs={"Length": 2}, symbol="X", timeframe="1", profile=True) is result


def test_a_script_error_is_reported_with_its_line(service) -> None:
    with pytest.raises(ScriptServiceError) as caught:
        service.run("//@version=5\nindicator('x')\nplot(nosuch(close))\n", bars_for_script(market_bars()))
    assert caught.value.line == 3 and "nosuch" in caught.value.message


def test_a_stuck_worker_is_killed_and_replaced() -> None:
    stuck = ScriptRunService(workers=1, limits=ScriptLimits(max_seconds=0.2), grace_seconds=0.3,
                             command=lambda: [sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with pytest.raises(ScriptServiceError, match="was stopped"):
            stuck.run(SMA, bars_for_script(market_bars()))
        assert stuck.killed_runs == 1
    finally:
        stuck.close()


def test_one_person_has_a_bounded_number_of_runs(service) -> None:
    slot = service._user_slot("someone")
    assert slot.acquire(blocking=False)
    try:
        with pytest.raises(ScriptServiceError, match="at most 1 scripts run at once"):
            service.run(SMA, bars_for_script(market_bars()), user_id="someone")
    finally:
        slot.release()


def test_check_reports_problems_or_the_inputs() -> None:
    assert check_script(SMA)["diagnostics"] == []
    assert check_script(SMA)["inputs"][0]["default"] == 3
    [problem] = check_script("//@version=5\nindicator('x')\nx = (\n")["diagnostics"]
    assert problem["kind"] == "syntax" and problem["line"] >= 3


def test_the_api_runs_on_the_charts_bars_and_lists_names_for_completion(service) -> None:
    calls = []

    class Market:
        def bars(self, instrument_id, interval, limit, binding_id=None, **kwargs):
            calls.append((instrument_id, interval, limit, binding_id, kwargs))
            return SimpleNamespace(bars=market_bars(10))

    app = FastAPI()
    app.include_router(create_trading_scripts_router(service_factory=lambda: service, market_service_factory=Market))
    client = TestClient(app)
    body = client.post("/api/trading/scripts/run", json={"source": SMA, "instrument_id": "equity:X:Y", "interval": "1m", "limit": 50}).json()
    assert body["error"] is None and len(body["times"]) == 10 and len(body["result"]["plots"][0]["values"]) == 10
    assert calls == [("equity:X:Y", "1m", 50, None, {"alignment": "clock"})]
    broken = client.post("/api/trading/scripts/run", json={"source": "//@version=5\nindicator('x')\nplot(nosuch)\n", "instrument_id": "equity:X:Y", "interval": "1m"}).json()
    assert broken["result"] is None and broken["error"]["line"] == 3
    reference = client.get("/api/trading/scripts/reference").json()
    assert "ta.sma" in reference["functions"] and "close" in reference["variables"] and "color.blue" in reference["constants"]
    assert reference["signatures"]["ta.sma"] == "source, length" and "series" in reference["signatures"]["plot"]
    assert client.post("/api/trading/scripts/check", json={"source": SMA}).json()["diagnostics"] == []


def test_script_routes_come_before_the_script_documents() -> None:
    from app.apps.trading.api import create_trading_router as create_base_router

    app = FastAPI()
    app.include_router(create_trading_scripts_router(service_factory=lambda: None))
    app.include_router(create_base_router())
    client = TestClient(app)
    # /reference is the scripts router's, not a script document named "reference".
    assert "ta.sma" in client.get("/api/trading/scripts/reference").json()["functions"]
    from app.apps.trading import route_registration

    source = open(route_registration.__file__, encoding="utf-8").read()
    assert source.index("        create_trading_scripts_router,") < source.index("        create_trading_base_router,")


def test_a_deep_backtest_runs_a_strategy_on_the_providers_history(service) -> None:
    calls = []

    class Market:
        def bars(self, instrument_id, interval, limit, binding_id=None, **kwargs):
            calls.append(limit)
            return SimpleNamespace(bars=market_bars(30))

    app = FastAPI()
    app.include_router(create_trading_scripts_router(service_factory=lambda: service, market_service_factory=Market))
    client = TestClient(app)
    strategy = '//@version=5\nstrategy("S", overlay=true)\nif bar_index == 2\n    strategy.entry("L", strategy.long, qty=1)\nif bar_index == 10\n    strategy.close("L")\nplot(close)\n'
    body = client.post("/api/trading/scripts/backtest", json={"source": strategy, "instrument_id": "equity:X:Y", "interval": "1m"}).json()
    assert calls == [20_000] and body["error"] is None and len(body["times"]) == 30
    [trade] = body["result"]["strategy"]["trades"]
    assert (trade["entry_price"], trade["exit_price"]) == (103, 111)
    assert "plots" not in body["result"] and len(body["result"]["strategy"]["equity"]) == 30
    refused = client.post("/api/trading/scripts/backtest", json={"source": SMA, "instrument_id": "equity:X:Y", "interval": "1m", "bars": 100}).json()
    assert refused["result"] is None and refused["error"]["kind"] == "strategy"
