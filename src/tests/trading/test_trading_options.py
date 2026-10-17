"""Options research (TVP-10.4): Black-Scholes, implied volatility and Alpaca option chains."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.options import (
    OptionsService,
    black_scholes,
    create_trading_options_router,
    implied_volatility,
    option_quote,
    parse_occ_symbol,
    years_to_expiry,
)

NOW = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)  # 10:00 in New York


def test_black_scholes_matches_the_textbook_values_and_put_call_parity() -> None:
    # Hull's example: S=42, K=40, r=10%, sigma=20%, T=0.5 -> call 4.76, put 0.81.
    call = black_scholes("call", 42, 40, 0.5, 0.10, 0.0, 0.20)
    put = black_scholes("put", 42, 40, 0.5, 0.10, 0.0, 0.20)
    assert call["price"] == pytest.approx(4.76, abs=0.01) and put["price"] == pytest.approx(0.81, abs=0.01)
    assert call["price"] - put["price"] == pytest.approx(42 - 40 * 2.718281828 ** (-0.05), abs=1e-6)
    assert call["delta"] == pytest.approx(0.7791, abs=1e-3) and put["delta"] == pytest.approx(call["delta"] - 1, abs=1e-9)
    assert call["gamma"] == pytest.approx(put["gamma"]) and call["vega"] == pytest.approx(put["vega"]) and call["theta"] < 0
    expired = black_scholes("put", 38, 40, 0.0, 0.05, 0.0, 0.3)
    assert expired["price"] == 2 and expired["delta"] == -1


def test_implied_volatility_inverts_the_price() -> None:
    price = black_scholes("call", 100, 105, 0.25, 0.04, 0.01, 0.37)["price"]
    assert implied_volatility("call", price, 100, 105, 0.25, 0.04, 0.01) == pytest.approx(0.37, abs=1e-4)
    assert implied_volatility("put", 0.0001, 100, 150, 0.25, 0.04, 0.0) is None  # below intrinsic
    assert implied_volatility("call", 0, 100, 105, 0.25, 0.04, 0.0) is None


def test_symbols_and_time_to_expiry() -> None:
    assert parse_occ_symbol("AAPL261016C00240000") == ("AAPL", date(2026, 10, 16), "call", 240.0)
    assert parse_occ_symbol("BRKB261016P00412500") == ("BRKB", date(2026, 10, 16), "put", 412.5)
    assert parse_occ_symbol("AAPL261016X00240000") is None and parse_occ_symbol("short") is None
    # Expiry is the close in New York: 6 hours from 10:00 on the day, a week and 6 hours from the week before.
    assert years_to_expiry(date(2026, 10, 9), NOW) == pytest.approx(6 / (365 * 24))
    assert years_to_expiry(date(2026, 10, 16), NOW) == pytest.approx((7 * 24 + 6) / (365 * 24))
    assert years_to_expiry(date(2026, 10, 8), NOW) == 0


def test_quotes_use_alpacas_greeks_else_the_model_from_the_mid() -> None:
    # Without a spot the model can't price it: Alpaca's own Greeks.
    with_greeks = option_quote("X", {"latestQuote": {"bp": 1.0, "ap": 1.2}, "impliedVolatility": 0.3, "greeks": {"delta": 0.5, "gamma": 0.1, "theta": -0.02, "vega": 0.1}}, 10.0, kind="call", spot=None, strike=100, years=0.1, rate=0.04, dividend=0.0)
    assert with_greeks.greeks_source == "alpaca" and with_greeks.iv == 0.3 and with_greeks.mark == pytest.approx(1.1) and with_greeks.open_interest == 10
    # With one, the model from the mid wins over the feed's (possibly stale) Greeks.
    preferred = option_quote("X", {"latestQuote": {"bp": 1.0, "ap": 1.2}, "impliedVolatility": 0.9, "greeks": {"delta": 0.9}}, None, kind="call", spot=100, strike=100, years=0.1, rate=0.04, dividend=0.0)
    assert preferred.greeks_source == "model" and preferred.iv != 0.9
    mid = black_scholes("put", 100, 95, 0.1, 0.04, 0.0, 0.25)["price"]
    modelled = option_quote("Y", {"latestQuote": {"bp": mid - 0.01, "ap": mid + 0.01}, "dailyBar": {"v": 42}}, None, kind="put", spot=100, strike=95, years=0.1, rate=0.04, dividend=0.0)
    assert modelled.greeks_source == "model" and modelled.iv == pytest.approx(0.25, abs=1e-3) and modelled.delta < 0 and modelled.volume == 42
    no_price = option_quote("Z", {}, None, kind="call", spot=100, strike=100, years=0.1, rate=0.04, dividend=0.0)
    assert no_price.mark is None and no_price.iv is None and no_price.greeks_source is None


class Source:
    calls: list[str] = []

    def contracts(self, underlying, since):
        Source.calls.append(f"contracts {underlying} {since}")
        return [
            {"symbol": "AAPL261016C00100000", "expiration_date": "2026-10-16", "open_interest": "120"},
            {"symbol": "AAPL261016P00100000", "expiration_date": "2026-10-16", "open_interest": "80"},
            {"symbol": "AAPL261023C00100000", "expiration_date": "2026-10-23", "open_interest": None},
            {"symbol": "AAPL261002C00100000", "expiration_date": "2026-10-02", "open_interest": "5"},
        ]

    def snapshots(self, underlying, expiration):
        Source.calls.append(f"snapshots {underlying} {expiration}")
        price = black_scholes("call", 101, 100, 7.25 / 365, 0.04, 0.0, 0.3)["price"]
        return {
            "AAPL261016C00100000": {"latestQuote": {"bp": price - 0.05, "ap": price + 0.05}},
            "AAPL261016P00100000": {"latestQuote": {"bp": 1.0, "ap": 1.1}},
            "AAPL261016C00105000": {"latestTrade": {"p": 0.4}},
            "NOTASYMBOL": {},
        }


def service() -> OptionsService:
    Source.calls = []
    return OptionsService(source_factory=Source, spot=lambda instrument: 101.0, rate=lambda years: 0.04, dividend_yield=lambda instrument, spot: 0.0, clock=lambda: NOW, monotonic=lambda: 0.0)


def test_the_api_lists_expirations_and_a_chain_by_strike() -> None:
    options = service()
    app = FastAPI()
    app.include_router(create_trading_options_router(lambda: options))
    client = TestClient(app)
    expirations = client.get("/api/trading/options/expirations", params={"instrument_id": "equity:NASDAQ:AAPL"}).json()
    assert [(item["date"], item["days"], item["contracts"], item["open_interest"]) for item in expirations] == [("2026-10-16", 7, 2, 200.0), ("2026-10-23", 14, 1, 0.0)]
    chain = client.get("/api/trading/options/chain", params={"instrument_id": "equity:NASDAQ:AAPL", "expiration": "2026-10-16"}).json()
    assert [row["strike"] for row in chain["rows"]] == [100.0, 105.0]
    call = chain["rows"][0]["call"]
    assert call["open_interest"] == 120 and call["iv"] == pytest.approx(0.3, abs=0.01) and call["greeks_source"] == "model"
    assert chain["rows"][0]["put"]["mark"] == pytest.approx(1.05) and chain["rows"][1]["put"] is None
    assert chain["underlying_price"] == 101 and chain["rate"] == 0.04
    client.get("/api/trading/options/chain", params={"instrument_id": "equity:NASDAQ:AAPL", "expiration": "2026-10-16"})
    assert Source.calls.count("contracts AAPL 2026-10-09") == 1 and Source.calls.count("snapshots AAPL 2026-10-16") == 1  # cached
    assert client.get("/api/trading/options/expirations", params={"instrument_id": "crypto:BINANCE:spot:BTC-USDT"}).status_code == 422
