"""Options research (TVP-10.4, decision D-6): US equity option chains from Alpaca options market data.

- **Expirations** come from Alpaca's option contracts (with open interest); **quotes** from its option snapshots on the
  free indicative feed (real-time OPRA needs Alpaca's paid data plan).
- **Implied volatility and Greeks** are Black-Scholes from the quote's mid (Alpaca's indicative Greeks only where the
  model can't price a contract):
  the risk-free rate is the Treasury curve at the option's maturity (``macro.py``), the dividend yield the stock's
  trailing twelve months of dividends (``corporate_events.py``). Alpaca's US equity options are American; the
  European model is the usual approximation for screens like this one.

Research only: nothing here places, sizes or routes an options order.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config.env import env_str

from .us_equity_calendar import EASTERN, regular_close_time

logger = logging.getLogger(__name__)

ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"
EXPIRATIONS_SECONDS = 1_800
CHAIN_SECONDS = 60
DEFAULT_RATE = 0.04
YEAR_SECONDS = 365.0 * 86_400

OptionType = Literal["call", "put"]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(kind: OptionType, spot: float, strike: float, years: float, rate: float, dividend: float, sigma: float) -> dict[str, float]:
    """Price and Greeks per share: delta, gamma, theta (per calendar day), vega and rho (per 1 point, i.e. 1%)."""
    if spot <= 0 or strike <= 0 or years <= 0 or sigma <= 0:
        intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
        delta = (1.0 if spot > strike else 0.0) if kind == "call" else (-1.0 if spot < strike else 0.0)
        return {"price": intrinsic, "delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * sigma * sigma) * years) / (sigma * root)
    d2 = d1 - sigma * root
    carry = math.exp(-dividend * years)
    discount = math.exp(-rate * years)
    gamma = carry * _norm_pdf(d1) / (spot * sigma * root)
    vega = spot * carry * _norm_pdf(d1) * root / 100
    decay = -spot * carry * _norm_pdf(d1) * sigma / (2 * root)
    if kind == "call":
        price = spot * carry * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        delta = carry * _norm_cdf(d1)
        theta = decay - rate * strike * discount * _norm_cdf(d2) + dividend * spot * carry * _norm_cdf(d1)
        rho = strike * years * discount * _norm_cdf(d2) / 100
    else:
        price = strike * discount * _norm_cdf(-d2) - spot * carry * _norm_cdf(-d1)
        delta = -carry * _norm_cdf(-d1)
        theta = decay + rate * strike * discount * _norm_cdf(-d2) - dividend * spot * carry * _norm_cdf(-d1)
        rho = -strike * years * discount * _norm_cdf(-d2) / 100
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta / 365, "vega": vega, "rho": rho}


def implied_volatility(kind: OptionType, price: float, spot: float, strike: float, years: float, rate: float, dividend: float) -> float | None:
    """The volatility at which Black-Scholes gives ``price`` (bisection, 1% to 500%); None below intrinsic or above."""
    if price <= 0 or spot <= 0 or years <= 0:
        return None
    low, high = 0.01, 5.0
    if black_scholes(kind, spot, strike, years, rate, dividend, low)["price"] > price or black_scholes(kind, spot, strike, years, rate, dividend, high)["price"] < price:
        return None
    for _ in range(80):
        middle = (low + high) / 2
        if black_scholes(kind, spot, strike, years, rate, dividend, middle)["price"] < price:
            low = middle
        else:
            high = middle
        if high - low < 1e-6:
            break
    return (low + high) / 2


def years_to_expiry(expiration: date, now: datetime) -> float:
    """Until the expiration day's regular close in New York."""
    close = datetime.combine(expiration, regular_close_time(expiration), tzinfo=EASTERN)
    return max(0.0, (close - now).total_seconds() / YEAR_SECONDS)


def parse_occ_symbol(symbol: str) -> tuple[str, date, OptionType, float] | None:
    """``AAPL261016C00240000`` -> ("AAPL", 2026-10-16, "call", 240.0)."""
    if len(symbol) < 16:
        return None
    root, body = symbol[:-15], symbol[-15:]
    try:
        expiration = datetime.strptime(body[:6], "%y%m%d").date()
        kind: OptionType = "call" if body[6] == "C" else "put" if body[6] == "P" else None  # type: ignore[assignment]
        strike = int(body[7:]) / 1000
    except (ValueError, IndexError):
        return None
    return (root, expiration, kind, strike) if kind else None


class OptionQuote(BaseModel):
    symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    mark: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    # "alpaca" where the snapshot carried them, "model" when computed here, None without a price.
    greeks_source: Literal["alpaca", "model"] | None = None


class OptionChainRow(BaseModel):
    strike: float
    call: OptionQuote | None = None
    put: OptionQuote | None = None


class OptionChain(BaseModel):
    instrument_id: str
    underlying: str
    expiration: date
    underlying_price: float | None
    years_to_expiry: float
    rate: float
    dividend_yield: float
    rows: list[OptionChainRow]
    as_of: datetime
    source: str = "Alpaca options market data (indicative feed)"


class OptionExpiration(BaseModel):
    date: date
    days: int
    contracts: int
    open_interest: float


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def option_quote(symbol: str, snapshot: dict[str, Any], open_interest: float | None, *, kind: OptionType, spot: float | None, strike: float, years: float, rate: float, dividend: float) -> OptionQuote:
    quote = snapshot.get("latestQuote") or {}
    bid, ask = _number(quote.get("bp")), _number(quote.get("ap"))
    last = _number((snapshot.get("latestTrade") or {}).get("p"))
    mark = (bid + ask) / 2 if bid and ask and ask >= bid else last
    volume = _number((snapshot.get("dailyBar") or {}).get("v"))
    result = OptionQuote(symbol=symbol, bid=bid, ask=ask, last=last, mark=mark, volume=volume, open_interest=open_interest)
    # The model from the current mid first: the indicative feed's own Greeks can be stale and disagree between calls
    # and puts at one strike. Alpaca's are the fallback where the model can't price the contract.
    iv = implied_volatility(kind, mark, spot, strike, years, rate, dividend) if mark is not None and spot is not None else None
    if iv is not None and spot is not None:
        model = black_scholes(kind, spot, strike, years, rate, dividend, iv)
        return result.model_copy(update={
            "iv": iv, "delta": model["delta"], "gamma": model["gamma"], "theta": model["theta"], "vega": model["vega"], "greeks_source": "model",
        })
    greeks = snapshot.get("greeks") or {}
    alpaca_iv = _number(snapshot.get("impliedVolatility"))
    if alpaca_iv and greeks:
        return result.model_copy(update={
            "iv": alpaca_iv, "delta": _number(greeks.get("delta")), "gamma": _number(greeks.get("gamma")), "theta": _number(greeks.get("theta")),
            "vega": _number(greeks.get("vega")), "greeks_source": "alpaca",
        })
    return result


class AlpacaOptionsSource:
    """Alpaca option contracts (trading API) and snapshots (market data API), within the Alpaca request budget."""

    def __init__(self, runtime: Any = None) -> None:
        if runtime is None:
            from .providers.http_runtime import ProviderHttpRuntime

            runtime = ProviderHttpRuntime("alpaca_options", max_concurrency=2)
        self.runtime = runtime

    @staticmethod
    def _headers() -> dict[str, str]:
        from .providers.alpaca_iex import alpaca_iex_auth_headers

        return alpaca_iex_auth_headers()

    def _pages(self, url: str, params: dict[str, Any], key: str) -> list[Any] | dict[str, Any]:
        collected: list[Any] | dict[str, Any] | None = None
        token: str | None = None
        for _ in range(40):
            page = self.runtime.get(url, params={**params, **({"page_token": token} if token else {})}, headers=self._headers(), timeout=30).json()
            items = page.get(key) if isinstance(page, dict) else None
            if isinstance(items, list):
                collected = [*(collected or []), *items]
            elif isinstance(items, dict):
                collected = {**(collected or {}), **items}  # type: ignore[dict-item]
            token = page.get("next_page_token") if isinstance(page, dict) else None
            if not token:
                break
        return collected if collected is not None else []

    def contracts(self, underlying: str, since: date) -> list[dict[str, Any]]:
        url = f"{(env_str('OMNIX_ALPACA_TRADING_URL') or ALPACA_TRADING_URL).rstrip('/')}/v2/options/contracts"
        found = self._pages(url, {"underlying_symbols": underlying, "expiration_date_gte": since.isoformat(), "status": "active", "limit": 10_000}, "option_contracts")
        return found if isinstance(found, list) else []

    def snapshots(self, underlying: str, expiration: date) -> dict[str, Any]:
        from .providers.alpaca_iex import ALPACA_DATA_URL

        url = f"{(env_str('OMNIX_ALPACA_DATA_URL') or ALPACA_DATA_URL).rstrip('/')}/v1beta1/options/snapshots/{underlying}"
        found = self._pages(url, {"feed": "indicative", "expiration_date": expiration.isoformat(), "limit": 1_000}, "snapshots")
        return found if isinstance(found, dict) else {}


def default_spot(instrument_id: str) -> float | None:
    try:
        from .service import default_market_data_service

        bars = default_market_data_service().bars(instrument_id, "1m", 1).bars
        if bars:
            return float(bars[-1].close)
    except Exception:  # a closed market or a feed without minute bars: the daily close
        logger.debug("suppressed error in %s", "default_spot", exc_info=True)
    from .fundamentals import default_last_close

    return default_last_close(instrument_id)


def default_rate(years: float) -> float:
    """The Treasury yield at the option's maturity (interpolated on the latest curve), as a fraction."""
    try:
        from .macro import default_yield_curves

        curve = default_yield_curves().curve(None, date.today())
        points = [(span, value) for span, value in zip(curve.years, curve.curves[0].yields, strict=False) if value is not None]
    except Exception:
        return DEFAULT_RATE
    if not points:
        return DEFAULT_RATE
    if years <= points[0][0]:
        return points[0][1] / 100
    for (left_years, left), (right_years, right) in zip(points, points[1:], strict=False):
        if years <= right_years:
            return (left + (right - left) * (years - left_years) / (right_years - left_years)) / 100
    return points[-1][1] / 100


def default_dividend_yield(instrument_id: str, spot: float | None) -> float:
    if not spot:
        return 0.0
    try:
        from .corporate_events import default_corporate_events_service

        today = date.today()
        events = default_corporate_events_service().company(instrument_id).events
        trailing = sum(event.amount or 0.0 for event in events if event.kind == "dividend" and today - timedelta(days=365) < event.date <= today)
    except Exception:
        return 0.0
    return trailing / spot


class OptionsService:
    def __init__(
        self,
        *,
        source_factory: Callable[[], AlpacaOptionsSource] = AlpacaOptionsSource,
        spot: Callable[[str], float | None] = default_spot,
        rate: Callable[[float], float] = default_rate,
        dividend_yield: Callable[[str, float | None], float] = default_dividend_yield,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source_factory = source_factory
        self.spot = spot
        self.rate = rate
        self.dividend_yield = dividend_yield
        self.clock = clock
        self.monotonic = monotonic
        self._contracts: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._chains: dict[tuple[str, date], tuple[float, OptionChain]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def underlying_of(instrument_id: str) -> str:
        if not instrument_id.lower().startswith("equity:"):
            raise ValueError("option chains are for US stocks and ETFs")
        return instrument_id.split(":")[-1].upper()

    def _contract_list(self, underlying: str) -> list[dict[str, Any]]:
        with self._lock:
            cached = self._contracts.get(underlying)
        if cached and self.monotonic() - cached[0] < EXPIRATIONS_SECONDS:
            return cached[1]
        contracts = self.source_factory().contracts(underlying, self.clock().astimezone(EASTERN).date())
        with self._lock:
            self._contracts[underlying] = (self.monotonic(), contracts)
        return contracts

    def expirations(self, instrument_id: str) -> list[OptionExpiration]:
        today = self.clock().astimezone(EASTERN).date()
        by_date: dict[date, list[dict[str, Any]]] = {}
        for contract in self._contract_list(self.underlying_of(instrument_id)):
            try:
                by_date.setdefault(date.fromisoformat(str(contract["expiration_date"])), []).append(contract)
            except (KeyError, ValueError):
                continue
        return [
            OptionExpiration(date=day, days=(day - today).days, contracts=len(items), open_interest=sum(_number(item.get("open_interest")) or 0.0 for item in items))
            for day, items in sorted(by_date.items()) if day >= today
        ]

    def chain(self, instrument_id: str, expiration: date) -> OptionChain:
        underlying = self.underlying_of(instrument_id)
        key = (underlying, expiration)
        with self._lock:
            cached = self._chains.get(key)
        if cached and self.monotonic() - cached[0] < CHAIN_SECONDS:
            return cached[1]
        now = self.clock()
        years = years_to_expiry(expiration, now)
        spot = self.spot(instrument_id)
        rate = self.rate(years)
        dividend = self.dividend_yield(instrument_id, spot)
        open_interest = {str(item.get("symbol")): _number(item.get("open_interest")) for item in self._contract_list(underlying)}
        rows: dict[float, OptionChainRow] = {}
        for symbol, snapshot in self.source_factory().snapshots(underlying, expiration).items():
            parsed = parse_occ_symbol(symbol)
            if parsed is None or parsed[1] != expiration or not isinstance(snapshot, dict):
                continue
            _root, _expiration, kind, strike = parsed
            quote = option_quote(symbol, snapshot, open_interest.get(symbol), kind=kind, spot=spot, strike=strike, years=years, rate=rate, dividend=dividend)
            row = rows.setdefault(strike, OptionChainRow(strike=strike))
            rows[strike] = row.model_copy(update={kind: quote})
        chain = OptionChain(
            instrument_id=instrument_id, underlying=underlying, expiration=expiration, underlying_price=spot, years_to_expiry=years,
            rate=rate, dividend_yield=dividend, rows=[rows[strike] for strike in sorted(rows)], as_of=now,
        )
        with self._lock:
            self._chains[key] = (self.monotonic(), chain)
        return chain


_service: OptionsService | None = None


def default_options_service() -> OptionsService:
    global _service
    if _service is None:
        _service = OptionsService()
    return _service


def create_trading_options_router(service_factory: Callable[[], OptionsService] = default_options_service) -> APIRouter:
    router = APIRouter(prefix="/api/trading/options", tags=["trading-options"])

    def failed(exc: Exception, what: str) -> HTTPException:
        if isinstance(exc, ValueError):
            return HTTPException(status_code=422, detail=str(exc))
        logger.warning("options_%s_failed", what, exc_info=True)
        return HTTPException(status_code=502, detail={"code": "options_failed", "message": "Alpaca option data could not load."})

    @router.get("/expirations", response_model=list[OptionExpiration])
    def expirations(instrument_id: str = Query(min_length=3, max_length=200)) -> list[OptionExpiration]:
        try:
            return service_factory().expirations(instrument_id)
        except Exception as exc:
            raise failed(exc, "expirations") from exc

    @router.get("/chain", response_model=OptionChain)
    def chain(expiration: date, instrument_id: str = Query(min_length=3, max_length=200)) -> OptionChain:
        try:
            return service_factory().chain(instrument_id, expiration)
        except Exception as exc:
            raise failed(exc, "chain") from exc

    return router


__all__ = [
    "OptionChain", "OptionsService", "black_scholes", "create_trading_options_router", "implied_volatility", "option_quote",
    "parse_occ_symbol", "years_to_expiry",
]
