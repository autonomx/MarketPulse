"""US company profiles from SEC EDGAR (TVP-9.3, decisions D-3 and D-5): sector, industry and shares outstanding.

- **Ticker to company:** SEC's ``company_tickers.json``.
- **Sector and industry:** the SIC code in each company's submissions (``data.sec.gov/submissions``), mapped to a
  sector by SIC ranges. Coarser than GICS, as D-3 accepted. One request per company, so profiles are fetched once, a
  bounded number at a time, and kept in PostgreSQL.
- **Shares outstanding:** ``dei:EntityCommonStockSharesOutstanding`` for every filer from one XBRL frames request
  per quarter, for market capitalisation (shares times the last price).

SEC asks for at most 10 requests a second and a User-Agent with a contact (``OMNIX_SEC_USER_AGENT``); requests spend
the ``sec_edgar`` request budget.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.config.env import env_str
from app.persistence.unit_of_work import unit_of_work

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/{period}.json"
PROFILE_MAX_AGE = timedelta(days=90)
SHARES_MAX_AGE = timedelta(days=7)

# SIC ranges (inclusive) to (sector, industry); the narrowest match wins, and the SEC's divisions fill the rest.
_SIC_SECTORS: tuple[tuple[int, int, str, str], ...] = (
    (2830, 2836, "Health Care", "Pharmaceuticals & Biotech"),
    (3841, 3851, "Health Care", "Medical Devices"),
    (8000, 8099, "Health Care", "Health Care Services"),
    (6324, 6324, "Health Care", "Managed Care"),
    (3570, 3579, "Technology", "Computer Hardware"),
    (3661, 3679, "Technology", "Communications Equipment & Semiconductors"),
    (3674, 3674, "Technology", "Semiconductors"),
    (7370, 7379, "Technology", "Software & IT Services"),
    (3820, 3829, "Technology", "Instruments"),
    (4800, 4899, "Communication Services", "Telecommunications & Media"),
    (2710, 2799, "Communication Services", "Publishing"),
    (7810, 7819, "Communication Services", "Entertainment"),
    (4900, 4949, "Utilities", "Electric & Gas Utilities"),
    (4950, 4991, "Utilities", "Water & Other Utilities"),
    (1300, 1399, "Energy", "Oil & Gas"),
    (2900, 2999, "Energy", "Petroleum Refining"),
    (1200, 1299, "Energy", "Coal"),
    (6798, 6798, "Real Estate", "REITs"),
    (6500, 6599, "Real Estate", "Real Estate"),
    (6000, 6299, "Financials", "Banks & Capital Markets"),
    (6300, 6411, "Financials", "Insurance"),
    (6700, 6799, "Financials", "Holding & Investment Offices"),
    (2800, 2829, "Materials", "Chemicals"),
    (2840, 2899, "Materials", "Chemicals"),
    (1000, 1099, "Materials", "Metal Mining"),
    (1400, 1499, "Materials", "Mining"),
    (3300, 3399, "Materials", "Metals"),
    (2600, 2699, "Materials", "Paper & Packaging"),
    (3710, 3716, "Consumer Discretionary", "Automobiles"),
    (5200, 5999, "Consumer Discretionary", "Retail"),
    (7000, 7099, "Consumer Discretionary", "Hotels & Leisure"),
    (5800, 5899, "Consumer Discretionary", "Restaurants"),
    (2000, 2199, "Consumer Staples", "Food, Beverages & Tobacco"),
    (5400, 5499, "Consumer Staples", "Food Retail"),
    (2840, 2844, "Consumer Staples", "Household Products"),
    (3720, 3729, "Industrials", "Aerospace"),
    (3760, 3769, "Industrials", "Aerospace & Defense"),
    (4000, 4799, "Industrials", "Transportation"),
    (3500, 3569, "Industrials", "Machinery"),
    (1500, 1799, "Industrials", "Construction"),
    (7300, 7399, "Industrials", "Business Services"),
)
_SIC_DIVISIONS: tuple[tuple[int, int, str], ...] = (
    (100, 999, "Consumer Staples"), (1000, 1499, "Materials"), (1500, 1799, "Industrials"), (2000, 3999, "Industrials"),
    (4000, 4999, "Industrials"), (5000, 5199, "Industrials"), (5200, 5999, "Consumer Discretionary"),
    (6000, 6799, "Financials"), (7000, 8999, "Consumer Discretionary"), (9100, 9999, "Other"),
)


def sector_of(sic: str | int | None) -> tuple[str, str]:
    """(sector, industry) for a SIC code; ('Other', '') when unknown."""
    try:
        code = int(str(sic))
    except (TypeError, ValueError):
        return "Other", ""
    # The narrowest range that holds the code: semiconductors (3674) inside communications equipment, and so on.
    for low, high, sector, industry in sorted(_SIC_SECTORS, key=lambda item: item[1] - item[0]):
        if low <= code <= high:
            return sector, industry
    for low, high, sector in _SIC_DIVISIONS:
        if low <= code <= high:
            return sector, ""
    return "Other", ""


@dataclass(frozen=True)
class CompanyProfile:
    ticker: str
    cik: str
    name: str
    sic: str | None
    sector: str | None
    industry: str | None
    shares_outstanding: Decimal | None
    shares_as_of: date | None
    profile_fetched_at: datetime | None


def latest_frame_periods(today: date) -> list[str]:
    """The instant frames to try, newest first (``CY2026Q2I``): a quarter's frame fills in after its filings."""
    quarter = (today.month - 1) // 3 + 1
    year = today.year
    periods = []
    for _ in range(4):
        quarter -= 1
        if quarter == 0:
            quarter, year = 4, year - 1
        periods.append(f"CY{year}Q{quarter}I")
    return periods


class CompanyProfileRepository:
    def __init__(self, uow_factory: Callable[[], Any] = unit_of_work) -> None:
        self.uow_factory = uow_factory

    def get(self, tickers: Iterable[str]) -> dict[str, CompanyProfile]:
        names = sorted({ticker.upper() for ticker in tickers})
        if not names:
            return {}
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT ticker, cik, name, sic, sector, industry, shares_outstanding, shares_as_of, profile_fetched_at
                  FROM omnix_trading_company_profiles WHERE ticker = ANY(%s)
                """,
                (names,),
            ).fetchall()
        return {str(row[0]): CompanyProfile(str(row[0]), str(row[1]), str(row[2]), row[3], row[4], row[5], row[6], row[7], row[8]) for row in rows}

    def save_profile(self, ticker: str, cik: str, name: str, sic: str | None, sic_description: str | None) -> None:
        sector, industry = sector_of(sic)
        with self.uow_factory() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_company_profiles (ticker, cik, name, sic, sic_description, sector, industry, profile_fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (ticker) DO UPDATE SET cik = EXCLUDED.cik, name = EXCLUDED.name, sic = EXCLUDED.sic,
                    sic_description = EXCLUDED.sic_description, sector = EXCLUDED.sector, industry = EXCLUDED.industry,
                    profile_fetched_at = CURRENT_TIMESTAMP
                """,
                (ticker.upper(), cik, name, sic, sic_description, sector, industry or None),
            )
            uow.commit()

    def save_shares(self, shares_by_cik: dict[str, tuple[Decimal, date]], tickers_by_cik: dict[str, list[tuple[str, str]]]) -> int:
        """Shares outstanding for every ticker of each CIK (adding the ticker's row when it has none yet)."""
        saved = 0
        with self.uow_factory() as uow:
            for cik, (shares, as_of) in shares_by_cik.items():
                for ticker, name in tickers_by_cik.get(cik, []):
                    uow.connection.execute(
                        """
                        INSERT INTO omnix_trading_company_profiles (ticker, cik, name, shares_outstanding, shares_as_of, shares_fetched_at)
                        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (ticker) DO UPDATE SET shares_outstanding = EXCLUDED.shares_outstanding,
                            shares_as_of = EXCLUDED.shares_as_of, shares_fetched_at = CURRENT_TIMESTAMP
                        """,
                        (ticker, cik, name, shares, as_of),
                    )
                    saved += 1
            uow.commit()
        return saved

    def shares_fetched_at(self) -> datetime | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute("SELECT max(shares_fetched_at) FROM omnix_trading_company_profiles").fetchone()
        return row[0] if row else None


def default_company_profile_repository() -> CompanyProfileRepository:
    return CompanyProfileRepository()


class SecCompanySource:
    """SEC EDGAR requests, within the ``sec_edgar`` request budget."""

    def __init__(self, runtime: Any = None) -> None:
        if runtime is None:
            from .providers.http_runtime import ProviderHttpRuntime

            runtime = ProviderHttpRuntime("sec_edgar_company_profiles", max_concurrency=1)
        self.runtime = runtime

    @staticmethod
    def headers() -> dict[str, str]:
        return {"User-Agent": env_str("OMNIX_SEC_USER_AGENT", "OmnixTradingResearch/1.0 local-research contact=local@localhost") or "", "Accept-Encoding": "gzip, deflate"}

    def _json(self, url: str) -> Any:
        return self.runtime.get(url, headers=self.headers(), timeout=30).json()

    def tickers(self) -> dict[str, tuple[str, str]]:
        """Ticker to (CIK as 10 digits, company name)."""
        payload = self._json(TICKERS_URL)
        rows = payload.values() if isinstance(payload, dict) else []
        return {
            str(row.get("ticker", "")).upper(): (str(row.get("cik_str", "")).zfill(10), str(row.get("title", "")))
            for row in rows if isinstance(row, dict) and row.get("ticker") and row.get("cik_str") is not None
        }

    def submissions(self, cik: str) -> dict[str, Any]:
        payload = self._json(SUBMISSIONS_URL.format(cik=cik))
        return payload if isinstance(payload, dict) else {}

    def shares_frame(self, period: str) -> dict[str, tuple[Decimal, date]]:
        """CIK (10 digits) to (shares outstanding, as-of date) for one instant frame; empty when the frame has none yet."""
        try:
            payload = self._json(FRAMES_URL.format(period=period))
        except Exception:  # a frame not published yet answers 404
            return {}
        frame: dict[str, tuple[Decimal, date]] = {}
        for row in (payload.get("data") or []) if isinstance(payload, dict) else []:
            try:
                frame[str(row["cik"]).zfill(10)] = (Decimal(str(row["val"])), date.fromisoformat(str(row["end"])))
            except (KeyError, ValueError, ArithmeticError):
                continue
        return frame


class CompanyProfiles:
    """Profiles for tickers: what's stored, and the missing or stale ones fetched, at most ``max_new`` per call."""

    def __init__(
        self,
        repository_factory: Callable[[], CompanyProfileRepository] = default_company_profile_repository,
        source_factory: Callable[[], SecCompanySource] = SecCompanySource,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository_factory = repository_factory
        self.source_factory = source_factory
        self.clock = clock
        self._tickers: dict[str, tuple[str, str]] | None = None

    def _directory(self, source: SecCompanySource) -> dict[str, tuple[str, str]]:
        if self._tickers is None:
            self._tickers = source.tickers()
        return self._tickers

    def ensure(self, tickers: Iterable[str], *, max_new: int = 40) -> dict[str, CompanyProfile]:
        repository = self.repository_factory()
        wanted = [ticker.upper() for ticker in tickers]
        stored = repository.get(wanted)
        now = self.clock()
        stale = [
        ticker for ticker in wanted
        if ticker not in stored or (fetched := stored[ticker].profile_fetched_at) is None or now - fetched > PROFILE_MAX_AGE
    ]
        source = None
        if stale[:max_new]:
            source = self.source_factory()
            directory = self._directory(source)
            for ticker in stale[:max_new]:
                entry = directory.get(ticker.replace(".", "-")) or directory.get(ticker)
                if entry is None:
                    continue
                cik, name = entry
                try:
                    submissions = source.submissions(cik)
                except Exception:
                    logger.info("sec_submissions_failed ticker=%s", ticker)
                    continue
                repository.save_profile(ticker, cik, str(submissions.get("name") or name), submissions.get("sic") or None, submissions.get("sicDescription") or None)
        fetched = repository.shares_fetched_at()
        if fetched is None or now - fetched > SHARES_MAX_AGE:
            source = source or self.source_factory()
            self.refresh_shares(source, repository)
        return repository.get(wanted)

    def refresh_shares(self, source: SecCompanySource, repository: CompanyProfileRepository) -> int:
        """Shares outstanding for every filer, from the newest instant frames (older frames fill companies the newest lacks)."""
        shares: dict[str, tuple[Decimal, date]] = {}
        for period in reversed(latest_frame_periods(self.clock().date())):
            shares.update(source.shares_frame(period))
        if not shares:
            return 0
        by_cik: dict[str, list[tuple[str, str]]] = {}
        for ticker, (cik, name) in self._directory(source).items():
            by_cik.setdefault(cik, []).append((ticker, name))
        return repository.save_shares(shares, by_cik)


_profiles: CompanyProfiles | None = None


def default_company_profiles() -> CompanyProfiles:
    global _profiles
    if _profiles is None:
        _profiles = CompanyProfiles()
    return _profiles


__all__ = ["CompanyProfile", "CompanyProfileRepository", "CompanyProfiles", "SecCompanySource", "default_company_profiles", "latest_frame_periods", "sector_of"]
