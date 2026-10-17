"""The economic calendar (TVP-10.5, decision D-3): US data releases, past and upcoming, from FRED.

FRED's release calendar (``fred/releases/dates``) lists every release date, scheduled ones included. Releases that move
markets (employment, inflation, GDP, spending, the Fed, ...) are marked important by name. For those that publish a
headline series, the calendar shows the value as first released and the value before it, read as of the release date
(FRED's real-time "vintage" data), so later revisions don't rewrite history. Global events need a vendor and aren't
covered.

FRED needs a free API key (``OMNIX_FRED_API_KEY`` or the market-data settings); requests are cached.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.env import env_str
from app.security.provider_secret_store import load_trading_provider_secrets

logger = logging.getLogger(__name__)

FRED_URL = "https://api.stlouisfed.org/fred"
CALENDAR_TTL_SECONDS = 6 * 3600
MAX_WINDOW_DAYS = 92
MAX_VALUES_PER_REQUEST = 40

# Release-name fragments that mark an important release, with the headline series and how it's shown.
# (fragment, series, transform: "level" | "change" | "percent_change", unit)
IMPORTANT_RELEASES: tuple[tuple[str, str | None, str, str], ...] = (
    ("Employment Situation", "PAYEMS", "change", "K jobs"),
    ("Consumer Price Index", "CPIAUCSL", "percent_change", "% m/m"),
    ("Gross Domestic Product", "A191RL1Q225SBEA", "level", "% q/q ann."),
    ("Personal Income and Outlays", "PCEPI", "percent_change", "% m/m"),
    ("Producer Price Index", "PPIFIS", "percent_change", "% m/m"),
    ("Advance Monthly Sales for Retail", "RSAFS", "percent_change", "% m/m"),
    ("Industrial Production", "INDPRO", "percent_change", "% m/m"),
    ("Job Openings and Labor Turnover", "JTSJOL", "level", "K openings"),
    ("Unemployment Insurance Weekly Claims", "ICSA", "level", "claims"),
    ("New Residential Construction", "HOUST", "level", "K units ann."),
    ("Surveys of Consumers", "UMCSENT", "level", "index"),
    ("Employment Cost Index", "ECIALLCIV", "percent_change", "% q/q"),
    ("FOMC Press Release", None, "level", ""),
    ("Advance Durable Goods", "DGORDER", "percent_change", "% m/m"),
    ("Productivity and Costs", None, "level", ""),
)


def fred_api_key() -> str:
    value = (env_str("OMNIX_FRED_API_KEY", "") or env_str("FRED_API_KEY", "") or "").strip()
    if value:
        return value
    try:
        return str((load_trading_provider_secrets().get("fred") or {}).get("api_key") or "").strip()
    except Exception:
        return ""


def importance_of(name: str) -> tuple[str, str | None, str, str] | None:
    lowered = name.lower()
    return next((item for item in IMPORTANT_RELEASES if item[0].lower() in lowered), None)


class CalendarEvent(BaseModel):
    date: date
    release_id: int
    name: str
    importance: Literal["high", "normal"]
    link: str
    series: str | None = None
    unit: str = ""
    # As first published on the release date, and the value before it (for important releases with a headline series).
    actual: float | None = None
    previous: float | None = None


class EconomicCalendar(BaseModel):
    configured: bool
    start: date
    end: date
    events: list[CalendarEvent]
    source: str = "FRED release calendar (Federal Reserve Bank of St. Louis); US releases only"


def headline(observations: list[dict[str, Any]], transform: str) -> tuple[float | None, float | None]:
    """(actual, previous) from a vintage's latest observations: the level, its change, or its percent change."""
    values = [float(item["value"]) for item in observations if item.get("value") not in (None, ".", "")]
    if transform == "level":
        return (values[-1] if values else None, values[-2] if len(values) > 1 else None)
    changes = []
    for before, after in zip(values, values[1:], strict=False):
        if transform == "change":
            changes.append(after - before)
        elif before:
            changes.append((after / before - 1) * 100)
    return (changes[-1] if changes else None, changes[-2] if len(changes) > 1 else None)


class FredCalendar:
    def __init__(self, key: Callable[[], str] = fred_api_key, runtime: Any = None, clock: Callable[[], float] = time.time) -> None:
        self.key = key
        self.clock = clock
        if runtime is None:
            from .providers.http_runtime import ProviderHttpRuntime

            runtime = ProviderHttpRuntime("fred_economic_calendar", max_concurrency=2)
        self.runtime = runtime
        self._calendar: dict[tuple[date, date], tuple[float, list[dict[str, Any]]]] = {}
        self._values: dict[tuple[str, date], tuple[float | None, float | None]] = {}
        self._guard = threading.Lock()

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        payload = self.runtime.get(f"{FRED_URL}/{path}", params={**params, "api_key": self.key(), "file_type": "json"}, timeout=20).json()
        if not isinstance(payload, dict):
            raise ValueError("FRED returned an unexpected answer")
        if payload.get("error_message"):
            raise ValueError(f"FRED: {payload['error_message']}")
        return payload

    def release_dates(self, start: date, end: date) -> list[dict[str, Any]]:
        key = (start, end)
        with self._guard:
            hit = self._calendar.get(key)
            if hit and self.clock() - hit[0] < CALENDAR_TTL_SECONDS:
                return hit[1]
        payload = self._get(
            "releases/dates", realtime_start=start.isoformat(), realtime_end=end.isoformat(),
            include_release_dates_with_no_data="true", order_by="release_date", sort_order="asc", limit=1000,
        )
        rows = [row for row in payload.get("release_dates") or [] if isinstance(row, dict)]
        with self._guard:
            self._calendar[key] = (self.clock(), rows)
        return rows

    def values_on(self, series: str, day: date, transform: str) -> tuple[float | None, float | None]:
        """The headline as known on ``day`` (a past release date); cached for good, since vintages don't change."""
        key = (series, day)
        with self._guard:
            if key in self._values:
                return self._values[key]
        payload = self._get(
            "series/observations", series_id=series, realtime_start=day.isoformat(), realtime_end=day.isoformat(),
            sort_order="desc", limit=3,
        )
        observations = list(reversed([row for row in payload.get("observations") or [] if isinstance(row, dict)]))
        result = headline(observations, transform)
        with self._guard:
            self._values[key] = result
        return result

    def calendar(self, start: date, end: date, *, importance: str, today: date) -> EconomicCalendar:
        if not self.key():
            return EconomicCalendar(configured=False, start=start, end=end, events=[])
        events: list[CalendarEvent] = []
        valued = 0
        for row in self.release_dates(start, end):
            try:
                day = date.fromisoformat(str(row["date"]))
                release_id = int(row["release_id"])
            except (KeyError, ValueError):
                continue
            name = str(row.get("release_name") or "")
            important = importance_of(name)
            if importance == "high" and important is None:
                continue
            event = CalendarEvent(
                date=day, release_id=release_id, name=name, importance="high" if important else "normal",
                link=f"https://fred.stlouisfed.org/release?rid={release_id}",
            )
            if important and important[1]:
                event.series, event.unit = important[1], important[3]
                if day <= today and valued < MAX_VALUES_PER_REQUEST:
                    valued += 1
                    try:
                        event.actual, event.previous = self.values_on(important[1], day, important[2])
                    except Exception:  # a value we can't read is left blank
                        logger.info("fred_vintage_failed series=%s day=%s", important[1], day)
            events.append(event)
        return EconomicCalendar(configured=True, start=start, end=end, events=events)


_calendar: FredCalendar | None = None


def default_fred_calendar() -> FredCalendar:
    global _calendar
    if _calendar is None:
        _calendar = FredCalendar()
    return _calendar


def create_trading_economic_calendar_router(calendar_factory: Callable[[], FredCalendar] = default_fred_calendar, today: Callable[[], date] = date.today) -> APIRouter:
    router = APIRouter(prefix="/api/trading/economic-calendar", tags=["trading-economic-calendar"])

    @router.get("", response_model=EconomicCalendar)
    def calendar(
        start: date | None = None,
        end: date | None = None,
        importance: Literal["high", "all"] = "high",
    ) -> EconomicCalendar:
        current = today()
        first = start or current - timedelta(days=current.weekday())
        last = end or first + timedelta(days=13)
        if last < first or (last - first).days > MAX_WINDOW_DAYS:
            raise HTTPException(status_code=422, detail=f"the window is at most {MAX_WINDOW_DAYS} days")
        try:
            return calendar_factory().calendar(first, last, importance=importance, today=current)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail={"code": "economic_calendar_failed", "message": str(exc)}) from exc
        except Exception as exc:
            logger.warning("economic_calendar_failed", exc_info=True)
            raise HTTPException(status_code=502, detail={"code": "economic_calendar_failed", "message": "FRED could not be reached."}) from exc

    return router


__all__ = ["CalendarEvent", "EconomicCalendar", "FredCalendar", "create_trading_economic_calendar_router", "headline", "importance_of"]

