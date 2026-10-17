"""Screener fundamentals (TVP-9.1, decision D-5): every US filer's trailing figures from SEC XBRL frames.

A frame is one concept for one calendar period across all filers, so a few dozen requests a week cover the market.
Trailing twelve months are the four latest consecutive calendar quarters a company reported; most companies report
their fourth quarter only inside the annual figure, so without four quarters the latest calendar year is used. The
screener's market cap, P/E, P/S, P/B, EPS, revenue growth and net margin combine these with SEC shares outstanding
(``company_profiles.py``) and each instrument's last close.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.persistence.unit_of_work import unit_of_work
from app.runtime.features import FeatureContext

from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask

if TYPE_CHECKING:
    from .company_profiles import SecCompanySource

logger = logging.getLogger(__name__)

FRAME_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/{unit}/{period}.json"
REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
NET_INCOME = ("NetIncomeLoss",)
EPS = ("EarningsPerShareDiluted",)
EQUITY = ("StockholdersEquity",)
REFRESH_AGE = timedelta(days=7)
FUNDAMENTAL_METRICS = ("market_cap", "pe_ratio", "ps_ratio", "pb_ratio", "eps_ttm", "revenue_growth", "net_margin")

# One company's figures by calendar period: {"CY2026Q1": value, "CY2025": value, "CY2026Q1I": value}
Series = dict[str, Decimal]


def quarters_before(today: date, count: int) -> list[str]:
    """Calendar quarters before the current one, newest first (``CY2026Q2``)."""
    year, quarter = today.year, (today.month - 1) // 3 + 1
    periods = []
    for _ in range(count):
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        periods.append(f"CY{year}Q{quarter}")
    return periods


def _previous_quarter(period: str) -> str:
    year, quarter = int(period[2:6]), int(period[7])
    return f"CY{year - 1}Q4" if quarter == 1 else f"CY{year}Q{quarter - 1}"


def trailing(series: Series, quarters: list[str], years: list[str], *, skip: int = 0) -> Decimal | None:
    """The sum of the newest four consecutive quarters (``skip`` quarters back), else the newest year."""
    available = [period for period in quarters if period in series]
    if available:
        newest = available[0]
        for _ in range(skip):
            newest = _previous_quarter(newest)
        run = [newest]
        while len(run) < 4:
            run.append(_previous_quarter(run[-1]))
        if all(period in series for period in run):
            return sum((series[period] for period in run), Decimal(0))
    known_years = [period for period in years if period in series]
    index = 1 if skip else 0
    return series[known_years[index]] if len(known_years) > index else None


def compute_snapshots(frames: dict[str, dict[str, dict[str, Decimal]]], today: date) -> dict[str, dict[str, Decimal | None]]:
    """Per CIK: revenue (and the year before), net income, EPS over twelve months, and the latest equity.

    ``frames[concept][period][cik]``; concepts of one figure fill each other's gaps in their listed order.
    """
    quarters = quarters_before(today, 9)
    years = [f"CY{today.year - offset}" for offset in (1, 2, 3)]

    def series(concepts: Iterable[str], cik: str) -> Series:
        merged: Series = {}
        for concept in concepts:
            for period, values in frames.get(concept, {}).items():
                if cik in values:
                    merged.setdefault(period, values[cik])
        return merged

    ciks = {cik for concept_frames in frames.values() for values in concept_frames.values() for cik in values}
    snapshots: dict[str, dict[str, Decimal | None]] = {}
    for cik in ciks:
        revenue = series(REVENUE, cik)
        equity = series(EQUITY, cik)
        latest_equity = next((equity[f"{period}I"] for period in quarters if f"{period}I" in equity), None)
        snapshots[cik] = {
            "revenue_ttm": trailing(revenue, quarters, years),
            "revenue_prev_ttm": trailing(revenue, quarters, years, skip=4),
            "net_income_ttm": trailing(series(NET_INCOME, cik), quarters, years),
            "eps_ttm": trailing(series(EPS, cik), quarters, years),
            "equity": latest_equity,
        }
    return snapshots


class FundamentalSnapshotRepository:
    def __init__(self, uow_factory: Callable[[], Any] = unit_of_work) -> None:
        self.uow_factory = uow_factory

    def refreshed_at(self) -> datetime | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute("SELECT max(refreshed_at) FROM omnix_trading_fundamental_snapshots").fetchone()
        return row[0] if row else None

    def save(self, snapshots: dict[str, dict[str, Decimal | None]]) -> int:
        with self.uow_factory() as uow:
            for cik, values in snapshots.items():
                uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_fundamental_snapshots (cik, revenue_ttm, revenue_prev_ttm, net_income_ttm, eps_ttm, equity)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cik) DO UPDATE SET revenue_ttm = EXCLUDED.revenue_ttm, revenue_prev_ttm = EXCLUDED.revenue_prev_ttm,
                        net_income_ttm = EXCLUDED.net_income_ttm, eps_ttm = EXCLUDED.eps_ttm, equity = EXCLUDED.equity,
                        refreshed_at = CURRENT_TIMESTAMP
                    """,
                    (cik, values["revenue_ttm"], values["revenue_prev_ttm"], values["net_income_ttm"], values["eps_ttm"], values["equity"]),
                )
            uow.commit()
        return len(snapshots)

    def for_tickers(self, tickers: Iterable[str]) -> dict[str, dict[str, Decimal | None]]:
        """By ticker: the snapshot with the company's shares outstanding."""
        names = sorted({ticker.upper() for ticker in tickers})
        if not names:
            return {}
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT profile.ticker, profile.shares_outstanding, snapshot.revenue_ttm, snapshot.revenue_prev_ttm,
                       snapshot.net_income_ttm, snapshot.eps_ttm, snapshot.equity
                  FROM omnix_trading_company_profiles AS profile
                  JOIN omnix_trading_fundamental_snapshots AS snapshot ON snapshot.cik = profile.cik
                 WHERE profile.ticker = ANY(%s)
                """,
                (names,),
            ).fetchall()
        keys = ("shares", "revenue_ttm", "revenue_prev_ttm", "net_income_ttm", "eps_ttm", "equity")
        return {str(row[0]): dict(zip(keys, row[1:], strict=True)) for row in rows}


def default_fundamental_snapshot_repository() -> FundamentalSnapshotRepository:
    return FundamentalSnapshotRepository()


def fundamental_metric(metric: str, snapshot: dict[str, Decimal | None] | None, price: Decimal) -> Decimal | None:
    """A screener fundamental at ``price``: None when the company or the figures it needs are missing."""
    if snapshot is None:
        return None
    shares, revenue, previous_revenue = snapshot.get("shares"), snapshot.get("revenue_ttm"), snapshot.get("revenue_prev_ttm")
    income, eps, equity = snapshot.get("net_income_ttm"), snapshot.get("eps_ttm"), snapshot.get("equity")
    market_cap = Decimal(shares) * price if shares else None
    if metric == "market_cap":
        return market_cap
    if metric == "eps_ttm":
        return Decimal(eps) if eps is not None else None
    if metric == "pe_ratio":
        return price / Decimal(eps) if eps and eps > 0 else None
    if metric == "ps_ratio":
        return market_cap / Decimal(revenue) if market_cap is not None and revenue and revenue > 0 else None
    if metric == "pb_ratio":
        return market_cap / Decimal(equity) if market_cap is not None and equity and equity > 0 else None
    if metric == "revenue_growth":
        return (Decimal(revenue) / Decimal(previous_revenue) - 1) * 100 if revenue is not None and previous_revenue and previous_revenue > 0 else None
    if metric == "net_margin":
        return Decimal(income) / Decimal(revenue) * 100 if income is not None and revenue and revenue > 0 else None
    return None


def snapshot_for_instrument(instrument_id: str) -> dict[str, Decimal | None] | None:
    """The screener's fundamentals for an ``equity:`` instrument; None for anything else or an unknown company."""
    if not instrument_id.lower().startswith("equity:"):
        return None
    ticker = instrument_id.split(":")[-1].upper()
    try:
        return default_fundamental_snapshot_repository().for_tickers([ticker]).get(ticker)
    except Exception:  # no fundamentals: the rule has no value for this instrument
        logger.info("fundamental_snapshot_unavailable instrument=%s", instrument_id)
        return None


def _sec_source() -> SecCompanySource:
    from .company_profiles import SecCompanySource

    return SecCompanySource()


class FundamentalSnapshotMonitor(ScheduledTradingMonitor):
    """Refreshes every filer's snapshot weekly from the SEC frames (about forty requests)."""

    def __init__(
        self,
        *,
        repository_factory: Callable[[], FundamentalSnapshotRepository] = default_fundamental_snapshot_repository,
        source_factory: Callable[[], SecCompanySource] = _sec_source,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        interval_seconds: float = 6 * 3600.0,
    ) -> None:
        self.repository_factory = repository_factory
        self.source_factory = source_factory
        self.clock = clock
        self.interval_seconds = interval_seconds
        self.last_error: str | None = None
        self.last_saved = 0

    def frames(self, source: SecCompanySource, today: date) -> dict[str, dict[str, dict[str, Decimal]]]:
        periods = quarters_before(today, 9)
        years = [f"CY{today.year - offset}" for offset in (1, 2, 3)]
        plan: list[tuple[str, str, list[str]]] = [(concept, "USD", periods + years) for concept in (*REVENUE, *NET_INCOME)]
        plan += [(concept, "USD-per-shares", periods + years) for concept in EPS]
        plan += [(concept, "USD", [f"{period}I" for period in periods[:2]]) for concept in EQUITY]
        frames: dict[str, dict[str, dict[str, Decimal]]] = {}
        for concept, unit, wanted in plan:
            for period in wanted:
                try:
                    payload = source._json(FRAME_URL.format(concept=concept, unit=unit, period=period))
                except Exception:  # a period not published yet
                    logger.debug("suppressed error in %s", "frames", exc_info=True)
                    continue
                values = frames.setdefault(concept, {}).setdefault(period, {})
                for row in (payload.get("data") or []) if isinstance(payload, dict) else []:
                    try:
                        values[str(row["cik"]).zfill(10)] = Decimal(str(row["val"]))
                    except (KeyError, ArithmeticError, ValueError):
                        continue
        return frames

    def refresh(self) -> int:
        repository = self.repository_factory()
        refreshed = repository.refreshed_at()
        now = self.clock()
        if refreshed is not None and now - refreshed < REFRESH_AGE:
            return 0
        frames = self.frames(self.source_factory(), now.date())
        return repository.save(compute_snapshots(frames, now.date()))

    async def run_once(self) -> int:
        import asyncio

        try:
            self.last_saved = await asyncio.to_thread(self.refresh)
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("fundamental_snapshots_failed: %s", self.last_error)
        return self.last_saved

    def diagnostics(self) -> dict[str, Any]:
        return {"enabled": fundamentals_monitor_enabled(), "running": self.scheduled, "last_error": self.last_error, "last_saved": self.last_saved}


def fundamentals_monitor_enabled() -> bool:
    from app.config.env import environment

    values = environment()
    if values.get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return False
    return values.get("OMNIX_TRADING_FUNDAMENTALS_MONITOR", "1").strip().lower() in {"1", "true", "yes", "on"}


_MONITOR_STATE_KEY = "_omnix_trading_fundamentals_monitor"


def create_trading_fundamentals_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    if isinstance(getattr(state, _MONITOR_STATE_KEY, None), FundamentalSnapshotMonitor):
        return None
    monitor = FundamentalSnapshotMonitor()
    setattr(state, _MONITOR_STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=fundamentals_monitor_enabled)


__all__ = ["FUNDAMENTAL_METRICS", "FundamentalSnapshotMonitor", "compute_snapshots", "fundamental_metric", "snapshot_for_instrument", "trailing"]
