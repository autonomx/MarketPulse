"""The latest value of indicator lines for a list of symbols (TVP-5.2): the watchlist's indicator columns.

Each symbol's bars on the interval (clock-aligned, as the chart draws them) are read through the market data service,
and each line is computed as alerts and the screener compute it (``alerts_evaluation._BarValues``): the server
registry with the instrument's session hours, compare symbols' bars and external-data series. A value is the line on
the latest bar, which may still be forming, as the watchlist's prices are.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .alert_conditions import IndicatorSource, validate_indicator_source
from .alerts_evaluation import HISTORY_LIMIT_MAX, _BarValues, _source_lookback, history_limit
from .service import TradingMarketDataService, default_market_data_service

if TYPE_CHECKING:
    from .indicators.intrabar import IntrabarLoader

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 200
MAX_LINES = 8
# Symbols read at once: the market data service queues its provider requests within their budgets.
CONCURRENCY = 4


class IndicatorValuesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_ids: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    interval: str = Field(default="1d", min_length=1, max_length=16)
    lines: list[IndicatorSource] = Field(min_length=1, max_length=MAX_LINES)

    @model_validator(mode="after")
    def validate_lines(self) -> IndicatorValuesRequest:
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("instrument_ids must be unique")
        for line in self.lines:
            validate_indicator_source(line)
        return self


class IndicatorValuesResponse(BaseModel):
    interval: str
    # By instrument, one value per requested line in order; None where the line has no value (warming up, no data).
    values: dict[str, list[Decimal | None]]


def _history(lines: Sequence[IndicatorSource], interval: str) -> int:
    required = max(_source_lookback(line, interval) for line in lines) + 1
    return min(HISTORY_LIMIT_MAX, history_limit(required))


def latest_values(
    request: IndicatorValuesRequest,
    bars: Sequence[Any],
    instrument_id: str,
    compare: Callable[..., Any] | None = None,
    intrabar: IntrabarLoader | None = None,
) -> list[Decimal | None]:
    """Each line's value on the latest bar of ``bars``."""
    from .indicators.external import external_indicator

    from .external_series import ExternalSeries
    from .indicator_context import instrument_session

    if not bars:
        return [None] * len(request.lines)
    external = ExternalSeries(instrument_id, request.interval) if any(external_indicator(line.indicator_id) for line in request.lines) else None
    values = _BarValues(bars, external, instrument_session(instrument_id), compare, None, intrabar)
    return [values.value(line, len(bars) - 1) for line in request.lines]


async def indicator_values(
    request: IndicatorValuesRequest,
    service: TradingMarketDataService,
) -> IndicatorValuesResponse:
    limit = _history(request.lines, request.interval)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    def fetch(symbol: str, count: int) -> Sequence[Any]:
        return service.bars(symbol, request.interval, min(count, HISTORY_LIMIT_MAX), None, alignment="clock").bars

    async def one(instrument_id: str) -> list[Decimal | None]:
        from .indicator_context import compare_bars_loader, intrabar_loader

        async with semaphore:
            try:
                bars = await asyncio.to_thread(fetch, instrument_id, limit)
                intrabar = intrabar_loader(service, instrument_id, request.interval)
                return await asyncio.to_thread(latest_values, request, bars, instrument_id, compare_bars_loader(fetch), intrabar)
            except Exception:  # a symbol without data shows empty cells; the others still fill
                logger.info("indicator_values_symbol_failed", extra={"instrument_id": instrument_id}, exc_info=True)
                return [None] * len(request.lines)

    rows = await asyncio.gather(*(one(instrument_id) for instrument_id in request.instrument_ids))
    return IndicatorValuesResponse(interval=request.interval, values=dict(zip(request.instrument_ids, rows, strict=True)))


def create_trading_indicator_values_router(
    service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/indicators", tags=["trading"])

    @router.post("/latest", response_model=IndicatorValuesResponse)
    async def latest(request: IndicatorValuesRequest) -> IndicatorValuesResponse:
        """The latest value of up to 8 indicator lines for up to 200 symbols (the watchlist's indicator columns)."""
        try:
            return await indicator_values(request, service_factory())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


__all__ = ["IndicatorValuesRequest", "IndicatorValuesResponse", "create_trading_indicator_values_router", "indicator_values", "latest_values"]
