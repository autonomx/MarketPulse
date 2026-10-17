"""The alert indicator contract (TVP-1.3), checked on the server.

web/src/features/trading/indicators/fixtures/alertIndicatorContract.json lists
the indicators the server evaluates and, for each, the inputs a chart's
default instance sends and the lines it draws (written by the web test
alertIndicatorContract.test.ts). Every line the alert dialog offers must be
an output the server computes for those inputs.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.apps.trading.alert_conditions import IndicatorSource, validate_indicator_source
from app.apps.trading.indicators.registry import server_indicator_ids

FIXTURE = Path(__file__).parents[3] / "web/src/features/trading/indicators/fixtures/alertIndicatorContract.json"


def test_the_contract_lists_the_server_indicators() -> None:
    contract = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert contract["ids"] == server_indicator_ids(), "refresh the ids, then regenerate the entries (see the note)"


def test_every_line_a_chart_offers_is_a_server_output() -> None:
    contract = json.loads(FIXTURE.read_text(encoding="utf-8"))
    problems = []
    for entry in [*contract["entries"], *contract.get("variants", [])]:
        for output in entry["outputs"]:
            try:
                validate_indicator_source(
                    IndicatorSource(kind="indicator", indicator_id=entry["id"], inputs=entry["inputs"], output=output)
                )
            except ValueError as exc:
                problems.append(f"{entry['id']} {output}: {exc}")
    assert not problems, "\n".join(problems)


def test_every_line_a_chart_offers_can_be_saved_as_an_alert() -> None:
    """The write-time checks accept every offered line: its warm-up fits one history fetch, and it has values."""
    from app.apps.trading.alert_conditions import AlertConditionSpec
    from app.apps.trading.alerts_evaluation import validate_conditions_can_fire

    contract = json.loads(FIXTURE.read_text(encoding="utf-8"))
    problems = []
    for entry in [*contract["entries"], *contract.get("variants", [])]:
        for output in entry["outputs"]:
            source = {"kind": "indicator", "indicator_id": entry["id"], "inputs": entry["inputs"], "output": output}
            condition = AlertConditionSpec.model_validate({"source": source, "operator": "greater_than", "target": {"kind": "value", "value": "-1e18"}})
            try:
                validate_conditions_can_fire([condition])
            except ValueError as exc:
                problems.append(f"{entry['id']} {output}: {exc}")
    assert not problems, "\n".join(problems)
