"""Market breadth (TVP-6.6, decision D-3): advancing and declining issues per exchange, computed in-house.

The collector reads Alpaca's active NYSE and Nasdaq stocks and their daily bars (the SIP feed, IEX without it) and
counts, for each session, the issues that closed above, below or at their previous close and the volume of the
rising and falling ones. Sessions are stored once complete (after the close). From them:

- **Advance/Decline Line:** the running sum of advances minus declines;
- **Advance/Decline Ratio:** advances divided by declines;
- **Cumulative Volume Index:** the running sum of advancing minus declining volume.

The universe is today's listed stocks, so earlier sessions miss issues delisted since (survivorship); TradingView's
exchange breadth series count every issue that traded. Values are placed at each session's close (16:00 New York, earlier on
early closes), so an intraday chart shows a session's breadth only from its close on.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.config.env import env_str
from app.persistence.unit_of_work import unit_of_work
from app.runtime.features import FeatureContext

from .metric_data import MarketMetricPoint, MarketMetricResponse, MarketMetricSeries
from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .us_equity_calendar import EASTERN, regular_close_time, regular_holidays

logger = logging.getLogger(__name__)

EXCHANGES = ("NYSE", "NASDAQ")
BACKFILL_SESSIONS = 400
CHUNK_SYMBOLS = 200
SIP_RECENT_DATA_DELAY = timedelta(minutes=16)
BREADTH_METRICS = {"breadth.ad_line", "breadth.ad_ratio", "breadth.cvi"}


@dataclass(frozen=True)
class BreadthDay:
    exchange: str
    session_date: date
    advances: int
    declines: int
    unchanged: int
    advancing_volume: Decimal
    declining_volume: Decimal


def session_date_of(timestamp: str | datetime) -> date:
    moment = timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    return moment.astimezone(EASTERN).date()


def compute_breadth(bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]], exchange_of: Mapping[str, str]) -> list[BreadthDay]:
    """Each session's breadth per exchange from daily bars (``{"t", "c", "v"}``, any order). The first bar of a symbol
    has no previous close and counts nowhere."""
    counts: dict[tuple[str, date], list[Any]] = defaultdict(lambda: [0, 0, 0, Decimal(0), Decimal(0)])
    for symbol, bars in bars_by_symbol.items():
        exchange = exchange_of.get(symbol)
        if exchange not in EXCHANGES:
            continue
        ordered = sorted((bar for bar in bars if bar.get("t") and bar.get("c") is not None), key=lambda bar: str(bar["t"]))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            change = Decimal(str(current["c"])) - Decimal(str(previous["c"]))
            volume = Decimal(str(current.get("v") or 0))
            row = counts[(exchange, session_date_of(current["t"]))]
            if change > 0:
                row[0] += 1
                row[3] += volume
            elif change < 0:
                row[1] += 1
                row[4] += volume
            else:
                row[2] += 1
    return [
        BreadthDay(exchange, day, row[0], row[1], row[2], row[3], row[4])
        for (exchange, day), row in sorted(counts.items(), key=lambda item: (item[0][1], item[0][0]))
    ]


def session_close(day: date) -> datetime:
    """The session's regular close (13:00 New York on an early close), in UTC."""
    return datetime.combine(day, regular_close_time(day), tzinfo=EASTERN).astimezone(timezone.utc)


def last_completed_session(now: datetime) -> date:
    """The latest US session whose close, plus 30 minutes for the bars to settle, has passed."""
    day = now.astimezone(EASTERN).date()
    while day.weekday() >= 5 or day in regular_holidays(day.year) or now < session_close(day) + timedelta(minutes=30):
        day -= timedelta(days=1)
    return day


class BreadthRepository:
    """Breadth rows; market data shared by every workspace."""

    def __init__(self, uow_factory: Callable[[], Any] = unit_of_work) -> None:
        self.uow_factory = uow_factory

    def latest_session(self) -> date | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute("SELECT max(session_date) FROM omnix_trading_market_breadth").fetchone()
        return row[0] if row else None

    def save(self, days: Iterable[BreadthDay], source: str) -> int:
        saved = 0
        with self.uow_factory() as uow:
            for day in days:
                uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_market_breadth
                        (exchange, session_date, advances, declines, unchanged, advancing_volume, declining_volume, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (exchange, session_date) DO UPDATE SET
                        advances = EXCLUDED.advances, declines = EXCLUDED.declines, unchanged = EXCLUDED.unchanged,
                        advancing_volume = EXCLUDED.advancing_volume, declining_volume = EXCLUDED.declining_volume,
                        source = EXCLUDED.source, computed_at = CURRENT_TIMESTAMP
                    """,
                    (day.exchange, day.session_date, day.advances, day.declines, day.unchanged, day.advancing_volume, day.declining_volume, source),
                )
                saved += 1
            uow.commit()
        return saved

    def days(self, exchange: str, *, until: date | None = None, limit: int = 5_000) -> list[BreadthDay]:
        """Oldest first, at most ``limit`` of the latest sessions up to ``until``."""
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT exchange, session_date, advances, declines, unchanged, advancing_volume, declining_volume
                  FROM (SELECT * FROM omnix_trading_market_breadth
                         WHERE exchange = %s AND (%s::DATE IS NULL OR session_date <= %s)
                         ORDER BY session_date DESC LIMIT %s) AS recent
                 ORDER BY session_date
                """,
                (exchange, until, until, limit),
            ).fetchall()
        return [BreadthDay(str(row[0]), row[1], int(row[2]), int(row[3]), int(row[4]), Decimal(row[5]), Decimal(row[6])) for row in rows]


def default_breadth_repository() -> BreadthRepository:
    return BreadthRepository()


def breadth_metric(metric: str, repository: BreadthRepository, *, instrument_id: str, interval: str, limit: int, end_time: datetime | None) -> MarketMetricResponse:
    """A breadth metric for any chart: one line per exchange, a value at each session's close."""
    if metric not in BREADTH_METRICS:
        raise ValueError(f"unknown breadth metric {metric}")
    until = end_time.astimezone(EASTERN).date() if end_time else None
    titles = {"breadth.ad_line": "A/D Line", "breadth.ad_ratio": "A/D Ratio", "breadth.cvi": "CVI"}
    series: list[MarketMetricSeries] = []
    for exchange in EXCHANGES:
        # Running sums start from the first stored session, so read them all, then keep the latest ``limit``.
        days = repository.days(exchange, until=until)
        total = Decimal(0)
        points: list[MarketMetricPoint] = []
        for day in days:
            if metric == "breadth.ad_line":
                total += day.advances - day.declines
                value = total
            elif metric == "breadth.cvi":
                total += day.advancing_volume - day.declining_volume
                value = total
            else:
                if day.declines == 0:
                    continue
                value = Decimal(day.advances) / Decimal(day.declines)
            points.append(MarketMetricPoint(time=session_close(day.session_date), value=value))
        series.append(MarketMetricSeries(key=exchange.lower(), title=f"{titles[metric]} {'NYSE' if exchange == 'NYSE' else 'Nasdaq'}", points=points[-limit:]))
    return MarketMetricResponse(
        instrument_id=instrument_id, metric=metric, provider="omnix_breadth_alpaca", interval=interval, series=series,
        received_at=datetime.now(timezone.utc), freshness_mode="cached", history_complete=False,
    )


# --- Collecting -------------------------------------------------------------------------------------------------


class AlpacaBreadthSource:
    """Active NYSE and Nasdaq stocks and their daily bars from Alpaca, within the Alpaca request budget."""

    def __init__(self, runtime: Any = None) -> None:
        if runtime is None:
            from .providers.http_runtime import ProviderHttpRuntime

            runtime = ProviderHttpRuntime("alpaca_market_breadth", max_concurrency=2)
        self.runtime = runtime
        self.feed = (env_str("OMNIX_TRADING_BREADTH_FEED", "sip") or "sip").strip().lower()

    def universe(self) -> dict[str, str]:
        from .historical_gapper_reconstruction import _alpaca_assets
        from .providers.alpaca_iex import alpaca_iex_auth_headers

        assets = _alpaca_assets(self.runtime, alpaca_iex_auth_headers())
        return {str(asset["symbol"]).upper(): str(asset["exchange"]).upper() for asset in assets if str(asset.get("exchange", "")).upper() in EXCHANGES}

    def daily_bars(self, symbols: list[str], start: datetime, end: datetime) -> dict[str, list[dict[str, Any]]]:
        from .historical_gapper_reconstruction import _alpaca_bars
        from .providers.alpaca_iex import alpaca_iex_auth_headers

        return _alpaca_bars(self.runtime, alpaca_iex_auth_headers(), symbols, timeframe="1Day", start=start, end=end, chunk_size=CHUNK_SYMBOLS, feed=self.feed)  # type: ignore[arg-type]


def breadth_monitor_enabled() -> bool:
    from app.config.env import environment

    values = environment()
    if values.get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return False
    return values.get("OMNIX_TRADING_BREADTH_MONITOR", "1").strip().lower() in {"1", "true", "yes", "on"}


class BreadthMonitor(ScheduledTradingMonitor):
    """Adds each completed session's breadth (after a first backfill of ``BACKFILL_SESSIONS`` sessions)."""

    def __init__(
        self,
        *,
        repository_factory: Callable[[], BreadthRepository] = default_breadth_repository,
        source_factory: Callable[[], Any] = AlpacaBreadthSource,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        interval_seconds: float = 3_600.0,
    ) -> None:
        self.repository_factory = repository_factory
        self.source_factory = source_factory
        self.clock = clock
        self.interval_seconds = interval_seconds
        self.last_error: str | None = None
        self.last_run_at: datetime | None = None
        self.last_saved = 0

    def collect(self) -> int:
        repository = self.repository_factory()
        target = last_completed_session(self.clock())
        latest = repository.latest_session()
        if latest is not None and latest >= target:
            return 0
        first = (latest - timedelta(days=10)) if latest else target - timedelta(days=int(BACKFILL_SESSIONS * 1.45) + 10)
        source = self.source_factory()
        universe = source.universe()
        # Alpaca refuses SIP queries reaching into the last 15 minutes without a paid plan.
        end = min(session_close(target) + timedelta(hours=8), self.clock() - SIP_RECENT_DATA_DELAY)
        bars = source.daily_bars(sorted(universe), datetime.combine(first, time(0), tzinfo=timezone.utc), end)
        days = [day for day in compute_breadth(bars, universe) if day.session_date <= target and (latest is None or day.session_date > latest)]
        return repository.save(days, f"alpaca_{getattr(source, 'feed', 'sip')}")

    async def run_once(self) -> int:
        import asyncio

        try:
            self.last_saved = await asyncio.to_thread(self.collect)
            self.last_error = None
        except Exception as exc:  # credentials missing, provider down: tried again next hour
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("market_breadth_collect_failed: %s", self.last_error)
        self.last_run_at = datetime.now(timezone.utc)
        return self.last_saved

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": breadth_monitor_enabled(),
            "running": self.scheduled,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "last_saved": self.last_saved,
        }


_MONITOR_STATE_KEY = "_omnix_trading_breadth_monitor"


def create_trading_breadth_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    if isinstance(getattr(state, _MONITOR_STATE_KEY, None), BreadthMonitor):
        return None
    monitor = BreadthMonitor()
    setattr(state, _MONITOR_STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=breadth_monitor_enabled)


__all__ = [
    "BREADTH_METRICS",
    "BreadthDay",
    "BreadthMonitor",
    "BreadthRepository",
    "breadth_metric",
    "compute_breadth",
    "create_trading_breadth_monitor_task",
    "last_completed_session",
]
