"""Financial statements and ratios (TVP-10.2, decision D-5) from SEC XBRL company facts.

A company's facts (``data.sec.gov/api/xbrl/companyfacts``) are normalised to a fixed set of line items: the first
``us-gaap`` concept in each item's list that the company reports for a period. Flows (income statement, cash flow)
come as annual (about a year long) and quarterly periods; quarters a filing reports only year-to-date (most cash
flows, every fourth quarter) are the difference of consecutive year-to-date values. Balance-sheet items are the
values at each period's end. Statements are cached per company for a day.

Ratios use the trailing twelve months (the last four quarters, else the last year), the latest balance sheet, SEC
shares outstanding and the latest daily close. US filers only (non-US issuers file IFRS, which this doesn't map).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.persistence.unit_of_work import unit_of_work

from .company_profiles import CompanyProfiles, SecCompanySource, default_company_profiles

logger = logging.getLogger(__name__)

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
CACHE_AGE = timedelta(days=1)
ANNUAL_PERIODS = 8
QUARTERLY_PERIODS = 12
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT"}

# (key, label, concepts in order of preference, unit)
INCOME = (
    ("revenue", "Revenue", ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"), "USD"),
    ("cost_of_revenue", "Cost of revenue", ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"), "USD"),
    ("gross_profit", "Gross profit", ("GrossProfit",), "USD"),
    ("research_development", "Research and development", ("ResearchAndDevelopmentExpense",), "USD"),
    ("selling_general_admin", "Selling, general and administrative", ("SellingGeneralAndAdministrativeExpense",), "USD"),
    ("operating_income", "Operating income", ("OperatingIncomeLoss",), "USD"),
    ("pretax_income", "Pretax income", ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"), "USD"),
    ("income_tax", "Income tax", ("IncomeTaxExpenseBenefit",), "USD"),
    ("net_income", "Net income", ("NetIncomeLoss", "ProfitLoss"), "USD"),
    ("eps_basic", "EPS (basic)", ("EarningsPerShareBasic",), "USD/shares"),
    ("eps_diluted", "EPS (diluted)", ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"), "USD/shares"),
    ("shares_diluted", "Diluted shares", ("WeightedAverageNumberOfDilutedSharesOutstanding",), "shares"),
)
BALANCE = (
    ("cash", "Cash and equivalents", ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"), "USD"),
    ("inventory", "Inventory", ("InventoryNet",), "USD"),
    ("current_assets", "Current assets", ("AssetsCurrent",), "USD"),
    ("total_assets", "Total assets", ("Assets",), "USD"),
    ("current_liabilities", "Current liabilities", ("LiabilitiesCurrent",), "USD"),
    ("long_term_debt", "Long-term debt", ("LongTermDebtNoncurrent", "LongTermDebt"), "USD"),
    ("total_liabilities", "Total liabilities", ("Liabilities",), "USD"),
    ("equity", "Shareholders' equity", ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"), "USD"),
)
CASH_FLOW = (
    ("operating_cash_flow", "Operating cash flow", ("NetCashProvidedByUsedInOperatingActivities",), "USD"),
    ("investing_cash_flow", "Investing cash flow", ("NetCashProvidedByUsedInInvestingActivities",), "USD"),
    ("financing_cash_flow", "Financing cash flow", ("NetCashProvidedByUsedInFinancingActivities",), "USD"),
    ("capital_expenditure", "Capital expenditure", ("PaymentsToAcquirePropertyPlantAndEquipment",), "USD"),
    ("dividends_paid", "Dividends paid", ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"), "USD"),
    ("share_repurchases", "Share repurchases", ("PaymentsForRepurchaseOfCommonStock",), "USD"),
)
STATEMENTS = {"income": INCOME, "balance": BALANCE, "cash_flow": CASH_FLOW}
LABELS = {key: label for items in STATEMENTS.values() for key, label, _concepts, _unit in items} | {"free_cash_flow": "Free cash flow"}


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(str(text)) if text else None
    except ValueError:
        return None


def _facts(payload: dict[str, Any], concept: str, unit: str) -> list[dict[str, Any]]:
    """A concept's facts in one unit from 10-K/10-Q filings, the latest filed one per period."""
    units = (((payload.get("facts") or {}).get("us-gaap") or {}).get(concept) or {}).get("units") or {}
    latest: dict[tuple[str | None, str], dict[str, Any]] = {}
    for fact in units.get(unit) or []:
        if fact.get("form") not in FORMS or fact.get("val") is None or not fact.get("end"):
            continue
        key = (fact.get("start"), fact["end"])
        if key not in latest or str(fact.get("filed", "")) > str(latest[key].get("filed", "")):
            latest[key] = fact
    return list(latest.values())


def _flow_periods(facts: list[dict[str, Any]]) -> tuple[dict[date, float], dict[date, float]]:
    """(annual, quarterly) values by period end: quarters reported directly or as year-to-date differences."""
    annual: dict[date, float] = {}
    quarterly: dict[date, float] = {}
    cumulative: dict[date, list[tuple[date, float]]] = {}
    for fact in facts:
        start, end = _date(fact.get("start")), _date(fact.get("end"))
        if start is None or end is None:
            continue
        days = (end - start).days
        value = float(fact["val"])
        if 330 <= days <= 400:
            annual[end] = value
        if 80 <= days <= 100:
            quarterly[end] = value
        if 80 <= days <= 400:
            cumulative.setdefault(start, []).append((end, value))
    # A year's year-to-date values (3, 6, 9, 12 months from one start): each quarter is the difference.
    for start, values in cumulative.items():
        ordered = sorted(values)
        previous: tuple[date, float] | None = None
        for end, value in ordered:
            if previous is not None and 80 <= (end - previous[0]).days <= 100 and end not in quarterly:
                quarterly[end] = value - previous[1]
            previous = (end, value)
    # A fourth quarter reported only in the year: the year less its first three quarters.
    for start, values in cumulative.items():
        for end, value in values:
            if not 330 <= (end - start).days <= 400 or end in quarterly:
                continue
            inside = [quarterly[day] for day in quarterly if start < day < end]
            if len(inside) == 3:
                quarterly[end] = value - sum(inside)
    return annual, quarterly


def _instant_periods(facts: list[dict[str, Any]]) -> tuple[dict[date, float], dict[date, float]]:
    """(annual, quarterly) balance values by date; annual ones are picked at the fiscal year ends afterwards."""
    quarterly: dict[date, float] = {}
    for fact in facts:
        end = _date(fact.get("end"))
        if end is not None and not fact.get("start"):
            quarterly[end] = float(fact["val"])
    return {}, quarterly


def normalise_company_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """The statements as {"annual"|"quarterly": {"income"|"balance"|"cash_flow": [{"end", "values"}]}}, oldest first."""
    result: dict[str, dict[str, dict[date, dict[str, float]]]] = {"annual": {}, "quarterly": {}}
    for statement, items in STATEMENTS.items():
        for frequency in ("annual", "quarterly"):
            result[frequency].setdefault(statement, {})
        for key, _label, concepts, unit in items:
            for concept in concepts:
                facts = _facts(payload, concept, unit)
                if not facts:
                    continue
                annual, quarterly = _instant_periods(facts) if statement == "balance" else _flow_periods(facts)
                for frequency, values in (("annual", annual), ("quarterly", quarterly)):
                    periods = result[frequency][statement]
                    for end, value in values.items():
                        # A concept earlier in the list wins; later ones only fill periods it lacks.
                        periods.setdefault(end, {}).setdefault(key, value)
    # The balance sheet at each fiscal year end (the end of an annual income or cash-flow period). Later 10-Qs restate
    # year-end balances as comparatives, so the 10-K's own tags can't say which dates are year ends.
    year_ends = set(result["annual"]["income"]) | set(result["annual"]["cash_flow"])
    for end in year_ends:
        if end in result["quarterly"]["balance"]:
            result["annual"]["balance"][end] = dict(result["quarterly"]["balance"][end])
    output: dict[str, Any] = {}
    for frequency, keep in (("annual", ANNUAL_PERIODS), ("quarterly", QUARTERLY_PERIODS)):
        output[frequency] = {}
        for statement, periods in result[frequency].items():
            rows = []
            for end in sorted(periods)[-keep:]:
                period_values = dict(periods[end])
                if statement == "cash_flow" and "operating_cash_flow" in period_values and "capital_expenditure" in period_values:
                    period_values["free_cash_flow"] = period_values["operating_cash_flow"] - period_values["capital_expenditure"]
                rows.append({"end": end.isoformat(), "values": period_values})
            output[frequency][statement] = rows
    return output


def _ttm(statements: dict[str, Any], statement: str, key: str, *, skip: int = 0) -> float | None:
    """The sum of the last four quarters (``skip`` quarters back), else the last year's value."""
    quarters = [row for row in statements["quarterly"][statement] if key in row["values"]]
    if skip:
        quarters = quarters[:-skip]
    last_four = quarters[-4:]
    if len(last_four) == 4 and (_date(last_four[-1]["end"]) - _date(last_four[0]["end"])).days <= 300:  # type: ignore[operator]
        return sum(row["values"][key] for row in last_four)
    if skip:
        return None
    years = [row for row in statements["annual"][statement] if key in row["values"]]
    return years[-1]["values"][key] if years else None


def _latest(statements: dict[str, Any], key: str) -> float | None:
    rows = [row for row in statements["quarterly"]["balance"] if key in row["values"]]
    return rows[-1]["values"][key] if rows else None


def ratios(statements: dict[str, Any], *, price: float | None, shares: float | None) -> dict[str, float | None]:
    revenue = _ttm(statements, "income", "revenue")
    net_income = _ttm(statements, "income", "net_income")
    gross = _ttm(statements, "income", "gross_profit")
    operating = _ttm(statements, "income", "operating_income")
    eps = _ttm(statements, "income", "eps_diluted")
    previous_revenue = _ttm(statements, "income", "revenue", skip=4)
    previous_eps = _ttm(statements, "income", "eps_diluted", skip=4)
    equity = _latest(statements, "equity")
    assets = _latest(statements, "total_assets")
    liabilities = _latest(statements, "total_liabilities")
    current_assets, current_liabilities = _latest(statements, "current_assets"), _latest(statements, "current_liabilities")
    market_cap = price * shares if price and shares else None

    def divide(top: float | None, bottom: float | None) -> float | None:
        return top / bottom if top is not None and bottom not in (None, 0) else None

    return {
        "market_cap": market_cap,
        "revenue_ttm": revenue,
        "net_income_ttm": net_income,
        "eps_ttm": eps,
        "pe_ratio": divide(price, eps) if eps and eps > 0 else None,
        "ps_ratio": divide(market_cap, revenue),
        "pb_ratio": divide(market_cap, equity) if equity and equity > 0 else None,
        "gross_margin": divide(gross, revenue),
        "operating_margin": divide(operating, revenue),
        "net_margin": divide(net_income, revenue),
        "return_on_equity": divide(net_income, equity) if equity and equity > 0 else None,
        "return_on_assets": divide(net_income, assets),
        "current_ratio": divide(current_assets, current_liabilities),
        "debt_to_equity": divide(liabilities, equity) if equity and equity > 0 else None,
        "revenue_growth": growth - 1 if (growth := divide(revenue, previous_revenue)) is not None else None,
        "eps_growth": (eps / previous_eps - 1) if eps is not None and previous_eps and previous_eps > 0 else None,
    }


class FinancialsRepository:
    def __init__(self, uow_factory: Callable[[], Any] = unit_of_work) -> None:
        self.uow_factory = uow_factory

    def get(self, cik: str) -> tuple[dict[str, Any], datetime] | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute("SELECT statements, fetched_at FROM omnix_trading_company_financials WHERE cik = %s", (cik,)).fetchone()
        return (dict(row[0]), row[1]) if row else None

    def save(self, cik: str, statements: dict[str, Any]) -> None:
        with self.uow_factory() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_company_financials (cik, statements) VALUES (%s, %s::jsonb)
                ON CONFLICT (cik) DO UPDATE SET statements = EXCLUDED.statements, fetched_at = CURRENT_TIMESTAMP
                """,
                (cik, json.dumps(statements)),
            )
            uow.commit()


class Financials(BaseModel):
    instrument_id: str
    ticker: str
    cik: str
    name: str = ""
    sector: str | None = None
    industry: str | None = None
    currency: str = "USD"
    price: float | None = None
    shares_outstanding: float | None = None
    annual: dict[str, list[dict[str, Any]]]
    quarterly: dict[str, list[dict[str, Any]]]
    ratios: dict[str, float | None]
    labels: dict[str, str] = Field(default_factory=lambda: dict(LABELS))
    fetched_at: datetime
    source: str = "SEC XBRL company facts"


class FinancialsService:
    def __init__(
        self,
        *,
        repository_factory: Callable[[], FinancialsRepository] = FinancialsRepository,
        source_factory: Callable[[], SecCompanySource] = SecCompanySource,
        profiles: Callable[[], CompanyProfiles] = default_company_profiles,
        price_of: Callable[[str], float | None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository_factory = repository_factory
        self.source_factory = source_factory
        self.profiles = profiles
        self.price_of = price_of or default_last_close
        self.clock = clock

    def statements(self, cik: str) -> tuple[dict[str, Any], datetime]:
        repository = self.repository_factory()
        cached = repository.get(cik)
        if cached and self.clock() - cached[1] < CACHE_AGE:
            return cached
        payload = self.source_factory()._json(COMPANY_FACTS_URL.format(cik=cik))
        statements = normalise_company_facts(payload if isinstance(payload, dict) else {})
        repository.save(cik, statements)
        return statements, self.clock()

    def company(self, instrument_id: str) -> Financials:
        if not instrument_id.lower().startswith("equity:"):
            raise ValueError("financial statements are for US stocks")
        ticker = instrument_id.split(":")[-1].upper()
        profile = self.profiles().ensure([ticker], max_new=1).get(ticker)
        if profile is None:
            raise LookupError(f"{ticker} is not a company filing with the SEC")
        statements, fetched_at = self.statements(profile.cik)
        price = self.price_of(instrument_id)
        shares = float(profile.shares_outstanding) if profile.shares_outstanding else None
        return Financials(
            instrument_id=instrument_id, ticker=ticker, cik=profile.cik, name=profile.name, sector=profile.sector, industry=profile.industry,
            price=price, shares_outstanding=shares, annual=statements["annual"], quarterly=statements["quarterly"],
            ratios=ratios(statements, price=price, shares=shares), fetched_at=fetched_at,
        )


def default_last_close(instrument_id: str) -> float | None:
    try:
        from .service import default_market_data_service

        bars = default_market_data_service().bars(instrument_id, "1d", 2).bars
        return float(bars[-1].close) if bars else None
    except Exception:  # valuation ratios need a price; statements don't
        logger.debug("suppressed error in %s", "default_last_close", exc_info=True)
        return None


_service: FinancialsService | None = None


def default_financials_service() -> FinancialsService:
    global _service
    if _service is None:
        _service = FinancialsService()
    return _service


def create_trading_fundamentals_router(service_factory: Callable[[], FinancialsService] = default_financials_service) -> APIRouter:
    router = APIRouter(prefix="/api/trading/fundamentals", tags=["trading-fundamentals"])

    @router.get("", response_model=Financials)
    def company(instrument_id: str = Query(min_length=3, max_length=200)) -> Financials:
        try:
            return service_factory().company(instrument_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("fundamentals_failed", exc_info=True)
            raise HTTPException(status_code=502, detail={"code": "fundamentals_failed", "message": "SEC financial data could not load."}) from exc

    return router


__all__ = ["Financials", "FinancialsService", "create_trading_fundamentals_router", "normalise_company_facts", "ratios"]

