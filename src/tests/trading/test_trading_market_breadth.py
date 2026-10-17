"""Market breadth (TVP-6.6): advances, declines and their volume per exchange, and the metrics charts read."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.breadth import BreadthDay, BreadthMonitor, breadth_metric, compute_breadth, last_completed_session, session_close
from app.apps.trading.metric_api import create_trading_metric_router


def bar(day: str, close: float, volume: float = 100) -> dict:
    # Alpaca's daily bars start at midnight New York.
    return {"t": f"{day}T04:00:00Z", "c": close, "v": volume}


BARS = {
    "AAA": [bar("2026-08-03", 10), bar("2026-08-04", 11, 500), bar("2026-08-05", 10, 300)],
    "BBB": [bar("2026-08-03", 20), bar("2026-08-04", 19, 200), bar("2026-08-05", 19, 50)],
    "CCC": [bar("2026-08-03", 5), bar("2026-08-04", 6, 50), bar("2026-08-05", 7, 70)],
    "OTC": [bar("2026-08-03", 1), bar("2026-08-04", 2)],
}
EXCHANGE = {"AAA": "NYSE", "BBB": "NYSE", "CCC": "NASDAQ", "OTC": "OTC"}


def test_each_session_counts_issues_against_their_previous_close() -> None:
    days = compute_breadth(BARS, EXCHANGE)
    assert [(day.exchange, str(day.session_date), day.advances, day.declines, day.unchanged, day.advancing_volume, day.declining_volume) for day in days] == [
        ("NASDAQ", "2026-08-04", 1, 0, 0, Decimal(50), Decimal(0)),
        ("NYSE", "2026-08-04", 1, 1, 0, Decimal(500), Decimal(200)),
        ("NASDAQ", "2026-08-05", 1, 0, 0, Decimal(70), Decimal(0)),
        ("NYSE", "2026-08-05", 0, 1, 1, Decimal(0), Decimal(300)),
    ]


def test_a_session_counts_once_its_close_has_settled() -> None:
    # Friday 7 August 2026, before and after 16:30 New York; then the weekend.
    assert last_completed_session(datetime(2026, 8, 7, 19, 0, tzinfo=timezone.utc)) == date(2026, 8, 6)
    assert last_completed_session(datetime(2026, 8, 7, 21, 0, tzinfo=timezone.utc)) == date(2026, 8, 7)
    assert last_completed_session(datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)) == date(2026, 8, 7)
    assert session_close(date(2026, 8, 7)) == datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)


class Repository:
    def __init__(self, days: list[BreadthDay] | None = None) -> None:
        self.stored = list(days or [])

    def latest_session(self):
        return max((day.session_date for day in self.stored), default=None)

    def save(self, days, source):
        days = list(days)
        self.stored.extend(days)
        self.source = source
        return len(days)

    def days(self, exchange, *, until=None, limit=5000):
        return sorted((day for day in self.stored if day.exchange == exchange and (until is None or day.session_date <= until)), key=lambda day: day.session_date)


def test_the_metrics_are_running_sums_and_ratios_at_each_close() -> None:
    repository = Repository(compute_breadth(BARS, EXCHANGE))
    line = breadth_metric("breadth.ad_line", repository, instrument_id="equity:X:Y", interval="1d", limit=500, end_time=None)
    nyse = next(series for series in line.series if series.key == "nyse")
    assert [point.value for point in nyse.points] == [0, -1] and nyse.points[0].time == session_close(date(2026, 8, 4))
    cvi = breadth_metric("breadth.cvi", repository, instrument_id="x", interval="1d", limit=500, end_time=None)
    assert [point.value for point in next(series for series in cvi.series if series.key == "nyse").points] == [300, 0]
    ratio = breadth_metric("breadth.ad_ratio", repository, instrument_id="x", interval="1d", limit=500, end_time=None)
    # No declines on Nasdaq: no ratio that day.
    assert [point.value for point in next(series for series in ratio.series if series.key == "nyse").points] == [1, 0]
    assert next(series for series in ratio.series if series.key == "nasdaq").points == []
    # Up to the chart's end only.
    capped = breadth_metric("breadth.ad_line", repository, instrument_id="x", interval="1d", limit=500, end_time=datetime(2026, 8, 4, 22, tzinfo=timezone.utc))
    assert len(next(series for series in capped.series if series.key == "nyse").points) == 1
    app = FastAPI()
    app.include_router(create_trading_metric_router(breadth_repository_factory=lambda: repository))
    body = TestClient(app).get("/api/trading/metrics", params={"instrument_id": "equity:NASDAQ:AAPL", "metric": "breadth.ad_line", "interval": "1d"}).json()
    assert body["provider"] == "omnix_breadth_alpaca" and [series["key"] for series in body["series"]] == ["nyse", "nasdaq"]


class Source:
    feed = "sip"

    def __init__(self) -> None:
        self.ranges: list[tuple] = []

    def universe(self):
        return EXCHANGE

    def daily_bars(self, symbols, start, end):
        self.ranges.append((symbols, start, end))
        return BARS


def test_the_monitor_backfills_then_adds_only_new_sessions() -> None:
    repository, source = Repository(), Source()
    monitor = BreadthMonitor(repository_factory=lambda: repository, source_factory=lambda: source, clock=lambda: datetime(2026, 8, 5, 22, tzinfo=timezone.utc))
    assert monitor.collect() == 4 and repository.source == "alpaca_sip"
    symbols, start, end = source.ranges[0]
    assert symbols == sorted(EXCHANGE) and start.date() < date(2025, 7, 1) and end > session_close(date(2026, 8, 5))
    # Nothing new until the next session closes.
    assert monitor.collect() == 0 and len(source.ranges) == 1


def test_the_first_run_after_the_close_stays_outside_the_recent_sip_window() -> None:
    # 30 minutes after the 16:00 ET close; Alpaca rejects SIP queries reaching into the last 15 minutes.
    now = session_close(date(2026, 8, 5)) + timedelta(minutes=30)
    repository, source = Repository(), Source()
    monitor = BreadthMonitor(repository_factory=lambda: repository, source_factory=lambda: source, clock=lambda: now)

    assert monitor.collect() == 4
    _, _, end = source.ranges[0]
    assert session_close(date(2026, 8, 5)) < end <= now - timedelta(minutes=15)
