"""Alert message placeholders (TVP-1.5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from app.apps.trading.alerts import TradingAlert
from app.apps.trading.alerts_evaluation import AlertConditionOutcome, ConditionObservation
from app.apps.trading.alerts_message import (
    MAX_RENDERED_MESSAGE,
    instrument_parts,
    message_values,
    render_alert_message,
)

START = datetime(2026, 10, 8, 14, 30, tzinfo=timezone.utc)


def _alert(name: str = "Breakout") -> TradingAlert:
    return TradingAlert(
        alert_id="a1",
        instrument_id="crypto:BINANCE:spot:BTC-USDT",
        conditions=[
            {"source": {"kind": "indicator", "indicator_id": "rsi", "inputs": {"period": 14}, "output": "rsi:14"},
             "operator": "greater_than", "target": {"kind": "value", "value": "70"}},
            {"source": {"kind": "price", "field": "close"}, "operator": "greater_than", "target": {"kind": "value", "value": "100"}},
        ],
        parameters={"message": "", "name": name},
    )


def _outcome() -> AlertConditionOutcome:
    return AlertConditionOutcome(
        met=True,
        bar_start=START,
        bar_end=START + timedelta(minutes=1),
        bar_is_final=True,
        close=D("101.50"),
        volume=D("1200"),
        observations=(
            ConditionObservation(0, "greater_than", True, D("71.25")),
            ConditionObservation(1, "greater_than", True, D("101.50")),
        ),
        open=D("100"),
        high=D("102.000"),
        low=D("99.5"),
    )


def _render(template: str, alert: TradingAlert | None = None) -> str:
    names, plots = message_values(alert or _alert(), _outcome(), interval="5m", evaluated_at=START + timedelta(seconds=75))
    return render_alert_message(template, names, plots)


def test_placeholders_take_the_values_at_the_trigger() -> None:
    assert _render("{{ticker}} on {{exchange}} {{interval}}: O {{open}} H {{high}} L {{low}} C {{close}} V {{volume}}") == (
        "BTCUSDT on BINANCE 5m: O 100 H 102 L 99.5 C 101.5 V 1200"
    )
    assert _render("{{time}} / {{timenow}} / {{alert_name}}") == "2026-10-08T14:30:00Z / 2026-10-08T14:31:15Z / Breakout"


def test_plots_by_position_by_output_and_by_indicator() -> None:
    assert _render('{{plot_0}} {{plot_1}} {{plot("rsi:14")}} {{ plot("RSI") }}') == "71.25 101.5 71.25 71.25"


def test_unknown_placeholders_are_left_as_written() -> None:
    assert _render('{{plot_7}} {{strategy.order.action}} {{plot("macd")}} {{alert_name}}', _alert(name="")) == (
        '{{plot_7}} {{strategy.order.action}} {{plot("macd")}} {{alert_name}}'
    )


def test_a_json_template_stays_json_and_the_message_is_capped() -> None:
    assert _render('{"symbol": "{{ticker}}", "price": {{close}}}') == '{"symbol": "BTCUSDT", "price": 101.5}'
    assert len(_render("{{ticker}}" * 600)) == MAX_RENDERED_MESSAGE


def test_instrument_parts() -> None:
    assert instrument_parts("equity:NASDAQ:AAPL") == ("AAPL", "NASDAQ")
    assert instrument_parts("equity:NYSE:BRK-B") == ("BRK-B", "NYSE")
    assert instrument_parts("equity:GOOD") == ("GOOD", "")
    assert instrument_parts("plain") == ("plain", "")


def test_a_legacy_quote_leaves_the_bar_placeholders_it_does_not_have() -> None:
    legacy = AlertConditionOutcome(
        met=True, bar_start=START, bar_end=START, bar_is_final=True, close=D("101"), volume=D("0"),
        observations=(ConditionObservation(0, "greater_than", True, D("101")),), volume_known=False,
    )
    names, plots = message_values(_alert(), legacy, interval="1m", evaluated_at=START)
    assert render_alert_message("{{open}} {{high}} {{low}} {{close}} {{volume}}", names, plots) == "{{open}} {{high}} {{low}} 101 {{volume}}"


def test_the_alert_dialog_learns_which_indicators_the_server_evaluates() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.apps.trading.alerts_api import create_trading_alert_router
    from app.apps.trading.indicators.external import external_indicator_ids
    from app.apps.trading.indicators.intrabar import INTRABAR_OUTPUTS
    from app.apps.trading.indicators.registry import server_indicator_ids

    app = FastAPI()
    app.include_router(create_trading_alert_router(repository_factory=lambda: None, webhook_store=object()))
    body = TestClient(app).get("/api/trading/alerts/indicators").json()
    # Registry indicators, the external-data series (TVP-0.2), and Volume Delta and CVD from lower-timeframe bars (TVP-6.4).
    assert body["indicator_ids"] == sorted([*server_indicator_ids(), *external_indicator_ids(), *INTRABAR_OUTPUTS])
    assert {"rsi", "macd", "bollinger"} <= set(body["indicator_ids"])
