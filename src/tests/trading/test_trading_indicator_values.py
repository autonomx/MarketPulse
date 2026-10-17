"""The watchlist's indicator columns (TVP-5.2): the latest value of indicator lines for a list of symbols."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.apps.trading.indicator_values import IndicatorValuesRequest, create_trading_indicator_values_router, indicator_values
from app.apps.trading.indicators.registry import BarSeries, IndicatorInputs, compute_indicator

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
RSI = {"kind": "indicator", "indicator_id": "rsi", "inputs": {"period": 14}, "output": "rsi:14"}
SMA = {"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 5}, "output": "sma:5"}


def bars(count: int, seed: float):
    rows = []
    for i in range(count):
        close = 100 + 6 * math.sin(i / 4 + seed) + i * 0.1
        rows.append(SimpleNamespace(
            start_time=START + timedelta(days=i), end_time=START + timedelta(days=i + 1), open=Decimal(str(round(close - 0.3, 6))),
            high=Decimal(str(round(close + 1, 6))), low=Decimal(str(round(close - 1, 6))), close=Decimal(str(round(close, 6))),
            volume=Decimal(1000), is_final=True,
        ))
    return rows


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def bars(self, instrument_id, interval, limit, binding_id=None, *, alignment="count"):
        self.calls.append((instrument_id, interval, limit, alignment))
        if instrument_id.endswith("MISSING"):
            raise ValueError("unknown instrument")
        return SimpleNamespace(bars=bars(120, seed=1.0 if instrument_id.endswith("AAPL") else 2.0))


def expected(indicator_id: str, output: str, seed: float, period: int) -> Decimal:
    series = next(item for item in compute_indicator(indicator_id, BarSeries.from_bars(bars(120, seed)), IndicatorInputs(period=period)) if item.key == output)
    return Decimal(repr(series.points[-1][1]))


def test_each_symbol_gets_each_lines_latest_value() -> None:
    service = _Service()
    request = IndicatorValuesRequest(instrument_ids=["equity:NASDAQ:AAPL", "equity:NASDAQ:MSFT", "equity:NASDAQ:MISSING"], interval="1d", lines=[RSI, SMA])
    response = asyncio.run(indicator_values(request, service))
    assert response.values["equity:NASDAQ:AAPL"] == [expected("rsi", "rsi:14", 1.0, 14), expected("sma", "sma:5", 1.0, 5)]
    assert response.values["equity:NASDAQ:MSFT"][0] == expected("rsi", "rsi:14", 2.0, 14)
    # A symbol without data shows empty cells.
    assert response.values["equity:NASDAQ:MISSING"] == [None, None]
    # Bars as the chart draws them, with enough history for the lines.
    assert {call[3] for call in service.calls} == {"clock"}
    assert all(call[2] >= 15 for call in service.calls)


def test_requests_are_bounded_and_lines_validated() -> None:
    with pytest.raises(ValidationError, match="has no output"):
        IndicatorValuesRequest(instrument_ids=["a:b:c"], lines=[{**RSI, "output": "rsi:99"}])
    with pytest.raises(ValidationError, match="unique"):
        IndicatorValuesRequest(instrument_ids=["a:b:c", "a:b:c"], lines=[RSI])
    with pytest.raises(ValidationError):
        IndicatorValuesRequest(instrument_ids=["a:b:c"], lines=[RSI] * 9)


def test_the_api_answers_for_the_watchlist() -> None:
    app = FastAPI()
    app.include_router(create_trading_indicator_values_router(service_factory=_Service))
    response = TestClient(app).post("/api/trading/indicators/latest", json={"instrument_ids": ["equity:NASDAQ:AAPL"], "interval": "1d", "lines": [RSI]})
    assert response.status_code == 200
    assert Decimal(response.json()["values"]["equity:NASDAQ:AAPL"][0]) == expected("rsi", "rsi:14", 1.0, 14)
    assert TestClient(app).post("/api/trading/indicators/latest", json={"instrument_ids": [], "lines": [RSI]}).status_code == 422
