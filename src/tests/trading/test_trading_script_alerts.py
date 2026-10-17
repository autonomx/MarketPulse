"""Alerts on Omnix Scripts (TVP-11.4): plots, alertcondition() and alert() calls as alert sources."""

from __future__ import annotations

import functools
import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.apps.trading import alerts_scripts
from app.apps.trading.alert_conditions import AlertConditionSpec, ScriptSource
from app.apps.trading.alerts import TradingAlertCreate
from app.apps.trading.alerts_evaluation import evaluate_conditions, required_bars
from app.apps.trading.alerts_scripts import script_alert_series, series_from_result, validate_script_sources
from app.apps.trading.scripts import ScriptLimits
from app.apps.trading.scripts_service import ScriptRunService

START = datetime(2026, 8, 3, 14, tzinfo=timezone.utc)
SCRIPT = """//@version=5
indicator("Breakout", overlay=true)
level = input.float(104, "Level")
plot(ta.sma(close, 2), "Avg")
alertcondition(close > level, "Above", "{{ticker}} closed above the level")
if close > level
    alert("broke " + str.tostring(close), alert.freq_once_per_bar)
"""


def bars(closes: list[float]):
    return [
        SimpleNamespace(
            start_time=START + timedelta(minutes=index), end_time=START + timedelta(minutes=index + 1), open=Decimal(str(close)),
            high=Decimal(str(close + 1)), low=Decimal(str(close - 1)), close=Decimal(str(close)), volume=Decimal(10), is_final=True,
        )
        for index, close in enumerate(closes)
    ]


_workspaces = itertools.count()


class Versions:
    def __init__(self, sources: dict[int, str]) -> None:
        self.sources = sources
        # Versions are cached by workspace: each fake is a workspace of its own.
        self.context = SimpleNamespace(workspace_id=f"ws-{next(_workspaces)}")

    def script_version_at(self, script_id: str, revision: int):
        found = [saved for saved in self.sources if saved <= revision]
        return {"revision": max(found), "source": self.sources[max(found)]} if found else None


@pytest.fixture()
def service():
    instance = ScriptRunService(workers=1, limits=ScriptLimits(max_seconds=5.0))
    yield instance
    instance.close()


def source(output: str, **fields) -> ScriptSource:
    return ScriptSource(kind="script", script_id="s1", revision=fields.pop("revision", 2), output=output, **fields)


def test_each_output_reads_the_run_result() -> None:
    result = {
        "plots": [
            {"index": 0, "kind": "plot", "values": [None, 1.5, 2.5], "options": {}},
            {"index": 1, "kind": "plotshape", "values": [False, True, False], "options": {}},
            {"index": 2, "kind": "alertcondition", "values": [False, False, True], "options": {"message": "up {{close}}"}},
        ],
        "alerts": [{"kind": "alertcondition", "bar": 2}, {"kind": "alert", "bar": 1, "message": "now"}],
    }
    assert series_from_result(result, "plot:0").values == {1: 1.5, 2: 2.5}
    assert series_from_result(result, "plot:1").values == {1: 1.0}
    assert series_from_result(result, "alertcondition:2").values == {2: 1.0}
    assert series_from_result(result, "alertcondition:2").messages == {2: "up {{close}}"}
    assert series_from_result(result, "alert").messages == {1: "now"}
    # The wrong kind at a position is no output.
    assert series_from_result(result, "alertcondition:0").error and not series_from_result(result, "plot:2").values


def test_the_alert_runs_the_script_as_it_was_saved(service) -> None:
    versions = Versions({1: SCRIPT.replace("104", "200"), 2: SCRIPT, 5: SCRIPT.replace("104", "100")})
    run = functools.partial(script_alert_series, repository_factory=lambda: versions, service_factory=lambda: service)
    series = run(source("alert", revision=3), bars([100, 103, 105, 106]))
    assert series.values == {2: 1.0, 3: 1.0} and series.messages[2] == "broke 105"
    # Inputs reach the script.
    assert run(source("alertcondition:1", revision=3, inputs={"Level": 105.5}), bars([100, 103, 105, 106])).values == {3: 1.0}
    assert run(source("plot:0", revision=2), bars([100, 102])).values == {1: 101.0}
    # Revision 1's level is 200: the same bars don't fire there.
    assert run(source("alert", revision=1), bars([150])).values == {}
    assert run(source("alert", revision=2), bars([150])).values == {0: 1.0}
    # No version that old: no values, with the reason.
    assert Versions({4: SCRIPT}).script_version_at("s1", 2) is None
    missing = functools.partial(script_alert_series, repository_factory=lambda: Versions({9: SCRIPT}), service_factory=lambda: service)
    assert "no version" in (missing(source("alert"), bars([100])).error or "")


def test_conditions_on_a_script_fire_with_its_message(service, monkeypatch) -> None:
    versions = Versions({2: SCRIPT})
    monkeypatch.setattr(alerts_scripts, "script_alert_series", functools.partial(
        script_alert_series, repository_factory=lambda: versions, service_factory=lambda: service,
    ))
    appears = AlertConditionSpec(source=source("alert"), operator="greater_than", target={"kind": "value", "value": "-1000000000000000000"})
    outcome = evaluate_conditions([appears], bars([100, 103, 105]), final_only=True)
    assert outcome is not None and outcome.met and outcome.observations[0].message == "broke 105"
    assert outcome.observation_payload()[0]["message"] == "broke 105"
    quiet = evaluate_conditions([appears], bars([100, 103, 104]), final_only=True)
    assert quiet is not None and not quiet.met and quiet.observations[0].message is None
    crossing = AlertConditionSpec(source=source("plot:0"), operator="crossing_up", target={"kind": "value", "value": "103"})
    assert evaluate_conditions([crossing], bars([100, 102, 106]), final_only=True).met
    assert required_bars([crossing]) == 301


def test_write_rules(service) -> None:
    with pytest.raises(ValidationError):
        source("plot:x")
    base = {"alert_id": "a", "conditions": [{"source": source("alert").model_dump(), "operator": "greater_than", "target": {"kind": "value", "value": "0"}}],
            "evaluation_policy": {"interval": "1m"}}
    TradingAlertCreate(instrument_id="equity:NASDAQ:AAPL", **base)
    with pytest.raises(ValidationError, match="cannot use a script"):
        TradingAlertCreate(instrument_id="watchlist:tech", **base)
    conditions = TradingAlertCreate(instrument_id="equity:NASDAQ:AAPL", **base).conditions
    validate_script_sources(conditions, Versions({2: SCRIPT}))
    with pytest.raises(ValueError, match="no saved version"):
        validate_script_sources(conditions, Versions({3: SCRIPT}))
    with pytest.raises(ValueError, match="never calls alert"):
        validate_script_sources(conditions, Versions({2: "//@version=5\nindicator('x')\nplot(close)\n"}))
    with pytest.raises(ValueError, match="problem on line"):
        validate_script_sources(conditions, Versions({2: "//@version=5\nindicator('x')\nalert(\n"}))
