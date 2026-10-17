"""Earnings, dividends and splits (TVP-10.1, decision D-5) for US stocks.

- **Earnings:** a company's 8-K filings with item 2.02 (results of operations) in its SEC submissions. The date and the
  session (before the open, during the session, after the close) come from the filing's acceptance time in New York;
  the fiscal quarter from the company's fiscal year end. A filing is not always the release itself, but it is the
  official record of one. There is no free source of announced future dates: the next one is *estimated* as 52 weeks
  after the matching report a year earlier (when that falls in the next quarter), and marked so.
- **Dividends and splits:** Alpaca corporate actions (cash dividends with their ex, record and pay dates; forward and
  reverse splits), past and declared.

Each ticker's events are cached for a day. A calendar asks for many tickers at once: it fetches at most
``MAX_FETCHES_PER_CALL`` stale ones per call and names the rest as pending, so it fills over a few calls.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from datetime import date as _Date
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config.env import env_str
from app.persistence.unit_of_work import unit_of_work

from .company_profiles import CompanyProfiles, SecCompanySource, default_company_profiles
from .us_equity_calendar import REGULAR_OPEN, after_regular_close

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
CACHE_AGE = timedelta(days=1)
HISTORY_YEARS = 10
MAX_FETCHES_PER_CALL = 15
MAX_CALENDAR_INSTRUMENTS = 200
EARNINGS_ITEM = "2.02"
ALPACA_ACTION_TYPES = "cash_dividend,forward_split,reverse_split"

EventKind = Literal["earnings", "dividend", "split"]
Timing = Literal["before_open", "during_market", "after_close"]


class CorporateEvent(BaseModel):
    kind: EventKind
    date: date
    estimated: bool = False
    timing: Timing | None = None
    fiscal_period: str | None = None
    link: str | None = None
    amount: float | None = None
    special: bool = False
    # The field named date shadows the type inside the class.
    record_date: _Date | None = None
    payable_date: _Date | None = None
    split_from: float | None = None
    split_to: float | None = None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def session_of(accepted: datetime) -> tuple[date, Timing]:
    """The New York date and session of a filing accepted at ``accepted``."""
    local = accepted.astimezone(NEW_YORK)
    clock = local.time()
    timing: Timing = "before_open" if clock < REGULAR_OPEN else "after_close" if after_regular_close(accepted) else "during_market"
    return local.date(), timing


def fiscal_period(report: date, fiscal_year_end: str | None) -> str | None:
    """The fiscal quarter a report on ``report`` covers: the last quarter ended before it, e.g. "Q3 FY2026".

    ``fiscal_year_end`` is SEC's MMDD; a year ending in the first week of a month (52/53-week years) counts as the
    month before.
    """
    if not fiscal_year_end or len(fiscal_year_end) != 4 or not fiscal_year_end.isdigit():
        return None
    end_month, end_day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    if not 1 <= end_month <= 12:
        return None
    if end_day <= 7:
        end_month = 12 if end_month == 1 else end_month - 1
    # The latest quarter-end month whose last day is before the report.
    year, month = report.year, report.month
    for _ in range(15):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        if (month - end_month) % 3 == 0:
            break
    quarter = ((month - end_month - 1) % 12) // 3 + 1
    fiscal_year = year if month <= end_month else year + 1
    return f"Q{quarter} FY{fiscal_year}"


def earnings_from_submissions(submissions: dict[str, Any]) -> list[CorporateEvent]:
    """Each 8-K with item 2.02 as an earnings event (amendments and repeated same-day filings are left out)."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    cik = str(submissions.get("cik") or "").lstrip("0")
    fiscal_year_end = submissions.get("fiscalYearEnd")
    events: dict[date, CorporateEvent] = {}
    for index, form in enumerate(forms):
        try:
            items = str(recent["items"][index] or "")
            accepted_text = str(recent["acceptanceDateTime"][index])
            accession = str(recent["accessionNumber"][index])
            document = str(recent["primaryDocument"][index] or "")
        except (KeyError, IndexError):
            continue
        if form != "8-K" or EARNINGS_ITEM not in [item.strip() for item in items.split(",")]:
            continue
        try:
            accepted = datetime.fromisoformat(accepted_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if accepted.tzinfo is None:
            accepted = accepted.replace(tzinfo=timezone.utc)
        day, timing = session_of(accepted)
        if day in events:
            continue
        link = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}" if cik and document else None
        events[day] = CorporateEvent(kind="earnings", date=day, timing=timing, fiscal_period=fiscal_period(day, fiscal_year_end), link=link)
    return sorted(events.values(), key=lambda event: event.date)


def estimated_next_earnings(earnings: list[CorporateEvent], today: date, fiscal_year_end: str | None) -> CorporateEvent | None:
    """The next quarterly report, 52 weeks after its match a year earlier: a date from today on, 45 to 120 days after the
    latest report (none when there is no match, or the company is overdue)."""
    reported = [event for event in earnings if not event.estimated]
    if not reported:
        return None
    latest = max(event.date for event in reported)
    candidates = [
        event.date + timedelta(weeks=52) for event in reported
        if event.date + timedelta(weeks=52) >= today and latest + timedelta(days=45) < event.date + timedelta(weeks=52) <= latest + timedelta(days=120)
    ]
    if not candidates:
        return None
    day = min(candidates)
    source = next(event for event in reported if event.date + timedelta(weeks=52) == day)
    return CorporateEvent(kind="earnings", date=day, estimated=True, timing=source.timing, fiscal_period=fiscal_period(day, fiscal_year_end))


def actions_from_alpaca(payload: dict[str, Any]) -> dict[str, list[CorporateEvent]]:
    """Ticker to its dividends and splits from one Alpaca corporate-actions page."""
    actions = payload.get("corporate_actions") or {}
    output: dict[str, list[CorporateEvent]] = {}
    for row in actions.get("cash_dividends") or []:
        day = _day(row.get("ex_date"))
        if day is None or not row.get("symbol"):
            continue
        output.setdefault(str(row["symbol"]).upper(), []).append(CorporateEvent(
            kind="dividend", date=day, amount=float(row["rate"]) if row.get("rate") is not None else None, special=bool(row.get("special")),
            record_date=_day(row.get("record_date")), payable_date=_day(row.get("payable_date")),
        ))
    for key in ("forward_splits", "reverse_splits"):
        for row in actions.get(key) or []:
            day = _day(row.get("ex_date"))
            if day is None or not row.get("symbol"):
                continue
            output.setdefault(str(row["symbol"]).upper(), []).append(CorporateEvent(
                kind="split", date=day, split_from=float(row.get("old_rate") or 1), split_to=float(row.get("new_rate") or 1),
            ))
    return output


class AlpacaCorporateActionsSource:
    """Alpaca corporate actions, within the Alpaca request budget."""

    def __init__(self, runtime: Any = None) -> None:
        if runtime is None:
            from .providers.http_runtime import ProviderHttpRuntime

            runtime = ProviderHttpRuntime("alpaca_corporate_actions", max_concurrency=1)
        self.runtime = runtime

    def actions(self, tickers: list[str], start: date, end: date) -> dict[str, list[CorporateEvent]]:
        from .providers.alpaca_iex import ALPACA_DATA_URL, alpaca_iex_auth_headers

        url = f"{(env_str('OMNIX_ALPACA_DATA_URL') or ALPACA_DATA_URL).rstrip('/')}/v1/corporate-actions"
        output: dict[str, list[CorporateEvent]] = {}
        page_token: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"symbols": ",".join(tickers), "types": ALPACA_ACTION_TYPES, "start": start.isoformat(), "end": end.isoformat(), "limit": 1000}
            if page_token:
                params["page_token"] = page_token
            payload = self.runtime.get(url, params=params, headers=alpaca_iex_auth_headers(), timeout=30).json()
            for ticker, events in actions_from_alpaca(payload if isinstance(payload, dict) else {}).items():
                output.setdefault(ticker, []).extend(events)
            page_token = payload.get("next_page_token") if isinstance(payload, dict) else None
            if not page_token:
                break
        return output


class CorporateEventRepository:
    def __init__(self, uow_factory: Callable[[], Any] = unit_of_work) -> None:
        self.uow_factory = uow_factory

    def get(self, tickers: Iterable[str]) -> dict[str, tuple[dict[str, Any], datetime]]:
        wanted = sorted(set(tickers))
        if not wanted:
            return {}
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                "SELECT ticker, events, fetched_at FROM omnix_trading_corporate_events WHERE ticker = ANY(%s)", (wanted,),
            ).fetchall()
        return {str(row[0]): (dict(row[1]), row[2]) for row in rows}

    def save(self, ticker: str, events: dict[str, Any]) -> None:
        with self.uow_factory() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_corporate_events (ticker, events) VALUES (%s, %s::jsonb)
                ON CONFLICT (ticker) DO UPDATE SET events = EXCLUDED.events, fetched_at = CURRENT_TIMESTAMP
                """,
                (ticker, json.dumps(events)),
            )
            uow.commit()


class CompanyEvents(BaseModel):
    instrument_id: str
    ticker: str
    events: list[CorporateEvent]
    fetched_at: datetime | None = None
    sources: list[str] = ["SEC 8-K item 2.02 filings", "Alpaca corporate actions"]


class CorporateCalendarEvent(CorporateEvent):
    instrument_id: str
    ticker: str


class CorporateEventCalendar(BaseModel):
    start: date
    end: date
    events: list[CorporateCalendarEvent]
    pending: list[str]


def ticker_of(instrument_id: str) -> str:
    if not instrument_id.lower().startswith("equity:"):
        raise ValueError("earnings, dividends and splits are for US stocks")
    return instrument_id.split(":")[-1].upper()


class CorporateEventsService:
    def __init__(
        self,
        *,
        repository_factory: Callable[[], CorporateEventRepository] = CorporateEventRepository,
        sec_factory: Callable[[], SecCompanySource] = SecCompanySource,
        alpaca_factory: Callable[[], AlpacaCorporateActionsSource] = AlpacaCorporateActionsSource,
        profiles: Callable[[], CompanyProfiles] = default_company_profiles,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository_factory = repository_factory
        self.sec_factory = sec_factory
        self.alpaca_factory = alpaca_factory
        self.profiles = profiles
        self.clock = clock

    def _fetch(self, tickers: list[str]) -> dict[str, tuple[dict[str, Any], bool]]:
        """Fresh events for ``tickers``: each one's earnings (when it files with the SEC), dividends and splits, and
        whether every source answered (what a source failed to give is served, not cached)."""
        today = self.clock().astimezone(NEW_YORK).date()
        actions: dict[str, list[CorporateEvent]] = {}
        actions_ok = True
        try:
            actions = self.alpaca_factory().actions(tickers, today - timedelta(days=365 * HISTORY_YEARS), today + timedelta(days=365))
        except Exception:
            actions_ok = False
            logger.warning("alpaca_corporate_actions_failed tickers=%s", len(tickers), exc_info=True)
        profiles = self.profiles().ensure(tickers, max_new=len(tickers))
        sec = self.sec_factory() if profiles else None
        output: dict[str, tuple[dict[str, Any], bool]] = {}
        for ticker in tickers:
            earnings: list[CorporateEvent] = []
            earnings_ok = True
            profile = profiles.get(ticker)
            if profile is not None and sec is not None:
                try:
                    submissions = sec.submissions(profile.cik)
                    earnings = earnings_from_submissions(submissions)
                    estimate = estimated_next_earnings(earnings, today, submissions.get("fiscalYearEnd"))
                    if estimate is not None:
                        earnings.append(estimate)
                except Exception:
                    earnings_ok = False
                    logger.info("sec_earnings_failed ticker=%s", ticker)
            events = sorted([*earnings, *actions.get(ticker, [])], key=lambda event: (event.date, event.kind))
            output[ticker] = ({"events": [event.model_dump(mode="json") for event in events]}, actions_ok and earnings_ok)
        return output

    def events(self, tickers: list[str], *, max_fetches: int = MAX_FETCHES_PER_CALL) -> tuple[dict[str, tuple[list[CorporateEvent], datetime]], list[str]]:
        """Cached or fresh events per ticker, and the tickers left pending (stale beyond ``max_fetches``)."""
        repository = self.repository_factory()
        stored = repository.get(tickers)
        now = self.clock()
        stale = [ticker for ticker in dict.fromkeys(tickers) if ticker not in stored or now - stored[ticker][1] >= CACHE_AGE]
        fetch, pending = stale[:max_fetches], stale[max_fetches:]
        if fetch:
            for ticker, (value, complete) in self._fetch(fetch).items():
                if complete:
                    repository.save(ticker, value)
                stored[ticker] = (value, now)
        result = {
            ticker: ([CorporateEvent.model_validate(event) for event in value.get("events", [])], fetched_at)
            for ticker, (value, fetched_at) in stored.items() if ticker not in pending
        }
        return result, pending

    def company(self, instrument_id: str) -> CompanyEvents:
        ticker = ticker_of(instrument_id)
        found, _pending = self.events([ticker], max_fetches=1)
        events, fetched_at = found.get(ticker, ([], None))
        return CompanyEvents(instrument_id=instrument_id, ticker=ticker, events=events, fetched_at=fetched_at)

    def calendar(self, instrument_ids: list[str], start: date, end: date, kinds: set[str]) -> CorporateEventCalendar:
        by_ticker: dict[str, list[str]] = {}
        for instrument_id in instrument_ids:
            try:
                by_ticker.setdefault(ticker_of(instrument_id), []).append(instrument_id)
            except ValueError:
                continue
        found, pending = self.events(list(by_ticker))
        events = [
            CorporateCalendarEvent(**event.model_dump(), instrument_id=instrument_id, ticker=ticker)
            for ticker, (items, _fetched) in found.items()
            for event in items if start <= event.date <= end and event.kind in kinds
            for instrument_id in by_ticker.get(ticker, [])
        ]
        events.sort(key=lambda event: (event.date, event.kind, event.ticker))
        return CorporateEventCalendar(start=start, end=end, events=events, pending=[instrument for ticker in pending for instrument in by_ticker[ticker]])


_service: CorporateEventsService | None = None


def default_corporate_events_service() -> CorporateEventsService:
    global _service
    if _service is None:
        _service = CorporateEventsService()
    return _service


def create_trading_corporate_events_router(service_factory: Callable[[], CorporateEventsService] = default_corporate_events_service) -> APIRouter:
    router = APIRouter(prefix="/api/trading/corporate-events", tags=["trading-corporate-events"])

    @router.get("", response_model=CompanyEvents)
    def company(instrument_id: str = Query(min_length=3, max_length=200)) -> CompanyEvents:
        try:
            return service_factory().company(instrument_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("corporate_events_failed", exc_info=True)
            raise HTTPException(status_code=502, detail={"code": "corporate_events_failed", "message": "Earnings, dividends and splits could not load."}) from exc

    @router.get("/calendar", response_model=CorporateEventCalendar)
    def calendar(
        start: date,
        end: date,
        instrument_id: Annotated[list[str] | None, Query()] = None,
        kind: Annotated[list[EventKind] | None, Query()] = None,
    ) -> CorporateEventCalendar:
        instrument_id = instrument_id or []
        if end < start or (end - start).days > 92:
            raise HTTPException(status_code=422, detail="The calendar spans at most three months.")
        if len(instrument_id) > MAX_CALENDAR_INSTRUMENTS:
            raise HTTPException(status_code=422, detail=f"The calendar takes at most {MAX_CALENDAR_INSTRUMENTS} instruments.")
        try:
            return service_factory().calendar(instrument_id, start, end, set(kind or ("earnings", "dividend", "split")))
        except Exception as exc:
            logger.warning("corporate_events_calendar_failed", exc_info=True)
            raise HTTPException(status_code=502, detail={"code": "corporate_events_failed", "message": "The earnings and dividends calendar could not load."}) from exc

    return router


__all__ = [
    "CorporateEvent",
    "CorporateEventsService",
    "actions_from_alpaca",
    "create_trading_corporate_events_router",
    "earnings_from_submissions",
    "estimated_next_earnings",
    "fiscal_period",
    "session_of",
]
