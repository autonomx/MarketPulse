"""The script screener (TVP-11.6): a script's outputs across a list of symbols."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.scripts import ScriptLimits
from app.apps.trading.scripts_api import create_trading_scripts_router
from app.apps.trading.scripts_screener import screen_row, screen_script
from app.apps.trading.scripts_service import ScriptRunService

START = datetime(2026, 8, 3, tzinfo=timezone.utc)

SCRIPT = """//@version=5
indicator("Screen")
fast = ta.sma(close, 2)
slow = ta.sma(close, 4)
plot(fast, "Fast")
bgcolor(close > open ? color.green : na)
plot(slow, "Slow")
alertcondition(ta.crossover(fast, slow), "Cross up")
"""


def bars(closes):
    return [
        SimpleNamespace(
            start_time=START + timedelta(days=i), open=Decimal(str(close)), high=Decimal(str(close + 1)), low=Decimal(str(close - 1)),
            close=Decimal(str(close)), volume=Decimal(1000), session="regular",
        )
        for i, close in enumerate(closes)
    ]


class Market:
    calls: list[tuple] = []

    def bars(self, instrument_id, interval, limit, binding_id=None, **kwargs):
        Market.calls.append((instrument_id, interval, limit, kwargs))
        if instrument_id == "equity:X:BAD":
            raise RuntimeError("provider down")
        if instrument_id == "equity:X:NONE":
            return SimpleNamespace(bars=[])
        # UP turns up on its last bar (the fast average crosses the slow one there); FLAT doesn't move.
        closes = [10, 9, 8, 7, 6, 12] if instrument_id == "equity:X:UP" else [5] * 6
        return SimpleNamespace(bars=bars(closes))


@pytest.fixture()
def service():
    instance = ScriptRunService(workers=2, per_user=2, limits=ScriptLimits(max_seconds=5.0))
    yield instance
    instance.close()


def test_each_symbol_gets_every_outputs_last_two_values(service) -> None:
    Market.calls = []
    outputs, rows = screen_script(
        SCRIPT, ["equity:X:UP", "equity:X:FLAT", "equity:X:BAD", "equity:X:NONE"], interval="1d", limit=300, inputs={}, user_id="u1",
        script_service=service, market_service=Market(),
    )
    assert [(output.key, output.title, output.kind) for output in outputs] == [
        ("plot:0", "Fast", "plot"), ("plot:2", "Slow", "plot"), ("alertcondition:3", "Cross up", "alertcondition"),
    ]
    up, flat, bad, none = rows
    assert up.instrument_id == "equity:X:UP" and up.bar_time == (START + timedelta(days=5)).isoformat()
    assert up.last["plot:0"] == pytest.approx(9.0) and up.previous["plot:0"] == pytest.approx(6.5)
    assert up.last["plot:2"] == pytest.approx(8.25) and up.last["alertcondition:3"] == 1.0 and up.previous["alertcondition:3"] == 0.0
    assert flat.last["alertcondition:3"] == 0.0 and flat.last["plot:0"] == pytest.approx(5.0)
    assert bad.error and "provider down" in bad.error and none.error == "no bars"
    assert {call[3]["alignment"] for call in Market.calls} == {"clock"}


def test_a_screen_out_of_time_says_which_symbols_it_left() -> None:
    ticks = iter([0.0, 100.0, 100.0])
    _outputs, rows = screen_script(
        SCRIPT, ["equity:X:UP"], interval="1d", limit=300, inputs={}, user_id="u1",
        script_service=None, market_service=Market(), deadline_seconds=10, clock=lambda: next(ticks),  # type: ignore[arg-type]
    )
    assert rows[0].error == "not screened: the screen's time ran out"


def test_rows_read_numbers_and_conditions() -> None:
    result = {"plots": [
        {"index": 0, "kind": "plot", "title": "", "values": [1, None]},
        {"index": 1, "kind": "alertcondition", "title": "Go", "values": [None, True]},
        {"index": 2, "kind": "barcolor", "title": "", "values": ["#fff", "#000"]},
    ]}
    row = screen_row("equity:X:Y", result, ["a", "b"])
    assert row.last == {"plot:0": None, "alertcondition:1": 1.0} and row.previous == {"plot:0": 1.0, "alertcondition:1": 0.0}


def test_the_api_screens_a_list_and_reports_a_script_that_does_not_compile(service) -> None:
    app = FastAPI()
    app.include_router(create_trading_scripts_router(service_factory=lambda: service, market_service_factory=Market))
    client = TestClient(app)
    body = client.post("/api/trading/scripts/screen", json={"source": SCRIPT, "instrument_ids": ["equity:X:UP", "equity:X:UP", "equity:X:FLAT"]}).json()
    assert body["error"] is None and [row["instrument_id"] for row in body["rows"]] == ["equity:X:UP", "equity:X:FLAT"]
    assert body["rows"][0]["last"]["alertcondition:3"] == 1.0
    broken = client.post("/api/trading/scripts/screen", json={"source": "//@version=5\nindicator('x')\nplot(nosuch)\n", "instrument_ids": ["equity:X:UP"]}).json()
    assert broken["rows"] == [] and broken["error"]["line"] == 3
    too_many = client.post("/api/trading/scripts/screen", json={"source": SCRIPT, "instrument_ids": [f"equity:X:S{i}" for i in range(51)]})
    assert too_many.status_code == 422
