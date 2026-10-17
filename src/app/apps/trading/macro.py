"""The US Treasury yield curve (TVP-10.5, decision D-3) from the Treasury's daily par yield curve rates.

A day's curve (1 month to 30 years) with the curves a week, a month and a year earlier, and the 10-year less 2-year
and 10-year less 3-month spreads. The Treasury publishes each year as one CSV (no key needed); the current year is
re-read hourly, earlier years daily. Other countries' curves need a vendor.
"""

from __future__ import annotations

import csv
import io
import logging
import threading
import time as clock_time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

TREASURY_CSV_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"
CURRENT_YEAR_SECONDS = 3_600
PAST_YEAR_SECONDS = 86_400
COMPARISONS = (("1 week earlier", timedelta(days=7)), ("1 month earlier", timedelta(days=30)), ("1 year earlier", timedelta(days=365)))


def tenor_years(label: str) -> float | None:
    """'1 Mo' -> 1/12, '1.5 Month' -> 0.125, '10 Yr' -> 10."""
    parts = label.split()
    if len(parts) != 2:
        return None
    try:
        amount = float(parts[0])
    except ValueError:
        return None
    unit = parts[1].lower()
    if unit.startswith("mo"):
        return amount / 12
    if unit.startswith("yr") or unit.startswith("year"):
        return amount
    if unit.startswith("wk") or unit.startswith("week"):
        return amount / 52
    return None


Day = dict[str, float | None]


def parse_curve_csv(text: str) -> tuple[list[str], dict[date, Day]]:
    """The tenors (short to long) and each day's yields in percent by tenor (None where not published)."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, [])
    columns = [(index, label) for index, label in enumerate(header[1:], start=1) if tenor_years(label) is not None]
    columns.sort(key=lambda item: tenor_years(item[1]) or 0)
    days: dict[date, Day] = {}
    for row in reader:
        try:
            day = datetime.strptime(row[0], "%m/%d/%Y").date()
        except (ValueError, IndexError):
            continue
        values: Day = {}
        for index, label in columns:
            try:
                values[label] = float(row[index]) if index < len(row) and row[index] != "" else None
            except ValueError:
                values[label] = None
        days[day] = values
    return [label for _index, label in columns], days


class YieldCurveLine(BaseModel):
    label: str
    date: date
    # Percent, one per tenor (None where not published that day).
    yields: list[float | None]


class YieldCurve(BaseModel):
    tenors: list[str]
    # Each tenor's maturity in years, for a chart's axis.
    years: list[float]
    curves: list[YieldCurveLine]
    spreads: dict[str, float | None]
    source: str = "U.S. Department of the Treasury, daily par yield curve rates"


class TreasuryYieldCurves:
    def __init__(self, fetch: Callable[[int], str] | None = None, clock: Callable[[], float] = clock_time.monotonic) -> None:
        self.fetch = fetch or self._fetch
        self.clock = clock
        self._years: dict[int, tuple[float, tuple[list[str], dict[date, Day]]]] = {}
        self._lock = threading.Lock()
        self._runtime: Any = None

    def _fetch(self, year: int) -> str:
        if self._runtime is None:
            from .providers.http_runtime import ProviderHttpRuntime

            self._runtime = ProviderHttpRuntime("us_treasury_yield_curve", max_concurrency=1)
        response = self._runtime.get(
            TREASURY_CSV_URL.format(year=year),
            params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": str(year), "page": "", "_format": "csv"},
            headers={"User-Agent": "OmnixTradingResearch/1.0"}, timeout=30,
        )
        return str(response.text)

    def year(self, year: int, today: date) -> tuple[list[str], dict[date, Day]]:
        with self._lock:
            cached = self._years.get(year)
        age = CURRENT_YEAR_SECONDS if year >= today.year else PAST_YEAR_SECONDS
        if cached and self.clock() - cached[0] < age:
            return cached[1]
        parsed = parse_curve_csv(self.fetch(year))
        with self._lock:
            self._years[year] = (self.clock(), parsed)
        return parsed

    def curve(self, on: date | None, today: date) -> YieldCurve:
        target = on or today
        # The year before too: early in a year, and for the comparison a year earlier.
        tenors, days = self.year(target.year, today)
        previous_tenors, previous_days = self.year(target.year - 1, today)
        days = {**previous_days, **days}
        tenors = sorted(set(tenors) | set(previous_tenors), key=lambda label: tenor_years(label) or 0)
        if not days or not tenors:
            raise LookupError("the Treasury published no yield curve for that date")

        def at_or_before(day: date) -> tuple[date, list[float | None]] | None:
            found = max((item for item in days if item <= day), default=None)
            return (found, [days[found].get(label) for label in tenors]) if found else None

        latest = at_or_before(target)
        if latest is None:
            raise LookupError("the Treasury published no yield curve on or before that date")
        lines = [YieldCurveLine(label="Latest" if on is None else "Selected", date=latest[0], yields=latest[1])]
        for label, back in COMPARISONS:
            earlier = at_or_before(latest[0] - back)
            if earlier is not None:
                lines.append(YieldCurveLine(label=label, date=earlier[0], yields=earlier[1]))
        by_tenor = dict(zip(tenors, latest[1], strict=False))

        def spread(long: str, short: str) -> float | None:
            a, b = by_tenor.get(long), by_tenor.get(short)
            return round(a - b, 2) if a is not None and b is not None else None

        return YieldCurve(
            tenors=tenors, years=[round(tenor_years(label) or 0, 4) for label in tenors], curves=lines,
            spreads={"10Y-2Y": spread("10 Yr", "2 Yr"), "10Y-3M": spread("10 Yr", "3 Mo")},
        )


_curves: TreasuryYieldCurves | None = None


def default_yield_curves() -> TreasuryYieldCurves:
    global _curves
    if _curves is None:
        _curves = TreasuryYieldCurves()
    return _curves


def create_trading_macro_router(
    curves_factory: Callable[[], TreasuryYieldCurves] = default_yield_curves, today: Callable[[], date] = date.today,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/macro", tags=["trading-macro"])

    @router.get("/yield-curve", response_model=YieldCurve)
    def yield_curve(on: date | None = None) -> YieldCurve:
        if on is not None and on > today():
            raise HTTPException(status_code=422, detail="Choose today or an earlier date.")
        try:
            return curves_factory().curve(on, today())
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("yield_curve_failed", exc_info=True)
            raise HTTPException(status_code=502, detail={"code": "yield_curve_failed", "message": "The Treasury yield curve could not load."}) from exc

    return router


__all__ = ["TreasuryYieldCurves", "YieldCurve", "create_trading_macro_router", "parse_curve_csv", "tenor_years"]
