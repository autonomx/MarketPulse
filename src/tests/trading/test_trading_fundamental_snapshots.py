"""Screener fundamentals from SEC frames (TVP-9.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.apps.trading import scanner as scanner_module
from app.apps.trading.fundamental_snapshots import (
    FundamentalSnapshotMonitor,
    compute_snapshots,
    fundamental_metric,
    quarters_before,
    trailing,
)
from app.apps.trading.scanner import TradingScannerDefinition, evaluate_scanner_dataset

TODAY = date(2026, 8, 10)
D = Decimal


def test_trailing_twelve_months_use_four_consecutive_quarters_else_the_year() -> None:
    quarters = quarters_before(TODAY, 9)
    assert quarters[:3] == ["CY2026Q2", "CY2026Q1", "CY2025Q4"]
    years = ["CY2025", "CY2024", "CY2023"]
    full = {"CY2026Q2": D(4), "CY2026Q1": D(3), "CY2025Q4": D(2), "CY2025Q3": D(1), "CY2025Q2": D(1), "CY2025Q1": D(1), "CY2024Q4": D(1), "CY2024Q3": D(1)}
    assert trailing(full, quarters, years) == 10 and trailing(full, quarters, years, skip=4) == 4
    # No fourth quarter reported on its own: the latest year (and the year before for growth).
    gappy = {"CY2026Q2": D(4), "CY2026Q1": D(3), "CY2025Q3": D(1), "CY2025": D(9), "CY2024": D(8)}
    assert trailing(gappy, quarters, years) == 9 and trailing(gappy, quarters, years, skip=4) == 8
    assert trailing({}, quarters, years) is None


def test_snapshots_merge_concepts_per_company() -> None:
    frames = {
        "Revenues": {"CY2025": {"0000000001": D(100)}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"CY2025": {"0000000001": D(999), "0000000002": D(50)}, "CY2024": {"0000000002": D(40)}},
        "NetIncomeLoss": {"CY2025": {"0000000001": D(20)}},
        "EarningsPerShareDiluted": {"CY2025": {"0000000001": D(2)}},
        "StockholdersEquity": {"CY2026Q2I": {"0000000001": D(400)}, "CY2026Q1I": {"0000000002": D(10)}},
    }
    snapshots = compute_snapshots(frames, TODAY)
    assert snapshots["0000000001"] == {"revenue_ttm": D(100), "revenue_prev_ttm": None, "net_income_ttm": D(20), "eps_ttm": D(2), "equity": D(400)}
    assert snapshots["0000000002"]["revenue_ttm"] == 50 and snapshots["0000000002"]["revenue_prev_ttm"] == 40 and snapshots["0000000002"]["equity"] == 10


SNAPSHOT = {"shares": D(1000), "revenue_ttm": D(5000), "revenue_prev_ttm": D(4000), "net_income_ttm": D(500), "eps_ttm": D("0.5"), "equity": D(2500)}


def test_fundamental_metrics_at_a_price() -> None:
    price = D(10)
    assert fundamental_metric("market_cap", SNAPSHOT, price) == 10_000
    assert fundamental_metric("pe_ratio", SNAPSHOT, price) == 20
    assert fundamental_metric("ps_ratio", SNAPSHOT, price) == 2
    assert fundamental_metric("pb_ratio", SNAPSHOT, price) == 4
    assert fundamental_metric("revenue_growth", SNAPSHOT, price) == 25
    assert fundamental_metric("net_margin", SNAPSHOT, price) == 10
    assert fundamental_metric("pe_ratio", {**SNAPSHOT, "eps_ttm": D(-1)}, price) is None
    assert fundamental_metric("market_cap", None, price) is None


def bars(closes):
    return [SimpleNamespace(close=str(close), open=str(close), high=str(close), low=str(close), volume="10", is_final=True) for close in closes]


def test_the_screener_filters_on_fundamentals(monkeypatch) -> None:
    looked_up: list[str] = []

    def snapshot(instrument_id):
        looked_up.append(instrument_id)
        return SNAPSHOT if instrument_id.endswith("AAA") else None

    monkeypatch.setattr(scanner_module, "snapshot_for_instrument", snapshot)
    definition = TradingScannerDefinition(
        scanner_id="s", name="Cheap", instrument_ids=["equity:NYSE:AAA", "equity:NYSE:BBB"], history_limit=2,
        rules=[
            {"rule_id": "pe", "metric": "pe_ratio", "operator": "lt", "threshold": "25"},
            {"rule_id": "cap", "metric": "market_cap", "operator": "gt", "threshold": "0", "role": "column"},
        ],
    )

    def response(instrument):
        return SimpleNamespace(bars=bars([9, 10]), instrument=SimpleNamespace(instrument_id=instrument),
                               binding=SimpleNamespace(binding_id="b", provider="p"), provenance=SimpleNamespace(dataset_fingerprint="f", as_of=datetime(2026, 8, 10, tzinfo=timezone.utc)))

    result = evaluate_scanner_dataset(definition, "run", response("equity:NYSE:AAA"), None)
    assert result is not None and result.metrics["rule:pe"] == 20 and result.metrics["rule:cap"] == 10_000
    # No fundamentals: a filter can't match.
    assert evaluate_scanner_dataset(definition, "run", response("equity:NYSE:BBB"), None) is None
    assert looked_up == ["equity:NYSE:AAA", "equity:NYSE:BBB"]


class Repository:
    def __init__(self, refreshed=None):
        self.refreshed = refreshed
        self.saved = None

    def refreshed_at(self):
        return self.refreshed

    def save(self, snapshots):
        self.saved = snapshots
        return len(snapshots)


class Sec:
    def __init__(self):
        self.urls = []

    def _json(self, url):
        self.urls.append(url)
        if "Revenues/USD/CY2025.json" in url:
            return {"data": [{"cik": 1, "val": 100}]}
        raise ValueError("not published")


def test_the_monitor_refreshes_weekly() -> None:
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    repository, sec = Repository(), Sec()
    monitor = FundamentalSnapshotMonitor(repository_factory=lambda: repository, source_factory=lambda: sec, clock=lambda: now)
    assert monitor.refresh() == 1 and repository.saved["0000000001"]["revenue_ttm"] == 100
    assert any("EarningsPerShareDiluted/USD-per-shares/CY2026Q2.json" in url for url in sec.urls)
    fresh = Repository(refreshed=now)
    assert FundamentalSnapshotMonitor(repository_factory=lambda: fresh, source_factory=lambda: pytest.fail("no SEC request"), clock=lambda: now).refresh() == 0
