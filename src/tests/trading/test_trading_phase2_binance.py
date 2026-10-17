from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import INSTRUMENTS
from app.apps.trading.models import MarketBar
from app.apps.trading.providers.binance import BinanceMarketDataProvider
from app.apps.trading.streaming.gap_recovery import (
    missing_finalized_ranges,
    reconcile_market_bars,
    recovery_window,
)
from app.apps.trading.streaming.manager import SharedSubscriptionManager, StreamingBarUpdate


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeBinanceSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def get(self, url, *, params, timeout):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        if url.endswith("/api/v3/ticker/24hr"):
            return FakeResponse(
                {
                    "lastPrice": "70000.1",
                    "bidPrice": "70000.0",
                    "askPrice": "70000.2",
                    "priceChangePercent": "1.25",
                    "volume": "1000",
                }
            )
        limit = int(params["limit"])
        end_time = params.get("endTime")
        eligible = self.rows if end_time is None else [row for row in self.rows if row[0] <= end_time]
        return FakeResponse(eligible[-limit:])


def build_rows(count: int = 1_205):
    start = 1_700_000_000_000
    rows = []
    for index in range(count):
        open_time = start + index * 60_000
        rows.append(
            [
                open_time,
                str(40_000 + index),
                str(40_010 + index),
                str(39_990 + index),
                str(40_005 + index),
                str(100 + index),
                open_time + 59_999,
            ]
        )
    return rows


def market_bar(start: datetime, *, revision: int = 1, final: bool = True, close: str = "101"):
    return MarketBar(
        instrument_id=INSTRUMENTS[0].instrument_id,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=Decimal("10"),
        is_final=final,
        provider="binance",
        ingestion_revision=revision,
    )


def test_binance_pagination_is_ordered_deduplicated_and_cached() -> None:
    session = FakeBinanceSession(build_rows())
    provider = BinanceMarketDataProvider(session=session, cache=TradingMarketDataCache(max_entries=8))
    instrument_id = INSTRUMENTS[0].instrument_id

    first = provider.get_bars(instrument_id, "1m", 1_205)
    second = provider.get_bars(instrument_id, "1m", 1_205)

    assert len(first.bars) == 1_205
    assert [bar.start_time for bar in first.bars] == sorted(bar.start_time for bar in first.bars)
    assert len({bar.start_time for bar in first.bars}) == 1_205
    assert first.provenance.cached is False
    assert second.provenance.cached is True
    assert second.provenance.dataset_fingerprint == first.provenance.dataset_fingerprint
    assert len(session.calls) == 2


def test_binance_quote_retains_canonical_instrument_identity() -> None:
    session = FakeBinanceSession(build_rows(1))
    provider = BinanceMarketDataProvider(session=session, cache=TradingMarketDataCache())
    quote = provider.get_quote(INSTRUMENTS[0].instrument_id)
    assert quote["instrument_id"] == "crypto:BINANCE:spot:BTC-USDT"
    assert quote["provider"] == "binance"
    assert quote["price"] == "70000.1"


def test_cache_coalesces_concurrent_identical_loads() -> None:
    cache = TradingMarketDataCache()
    calls = 0
    calls_lock = threading.Lock()
    results = []

    def loader():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return {"bars": [1, 2, 3]}

    def worker():
        results.append(cache.get_or_load("same", loader, ttl_seconds=60, source="fixture")[0])

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert results == [{"bars": [1, 2, 3]}] * 5


def test_gap_recovery_and_ingestion_revision_are_exact() -> None:
    start = datetime(2026, 8, 5, tzinfo=timezone.utc)
    bars = [market_bar(start), market_bar(start + timedelta(minutes=2))]
    assert missing_finalized_ranges(
        bars,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=3),
    ) == [(start + timedelta(minutes=1), start + timedelta(minutes=2))]
    assert recovery_window(start, start + timedelta(minutes=3), interval="1m") == (
        start + timedelta(minutes=1),
        start + timedelta(minutes=3),
    )

    corrected = market_bar(start, revision=2, close="105")
    stale = market_bar(start, revision=1, close="1")
    reconciled = reconcile_market_bars(bars, [stale, corrected])
    assert reconciled[0].close == Decimal("105")
    assert reconciled[0].ingestion_revision == 2


def test_identical_chart_streams_share_one_upstream_subscription() -> None:
    manager = SharedSubscriptionManager()
    binding_id = "binance:rest-ws:crypto:BINANCE:spot:BTC-USDT"
    instrument_id = INSTRUMENTS[0].instrument_id
    received: list[tuple[str, Decimal]] = []

    first_key, first_created = manager.subscribe(
        listener_id="chart-1",
        binding_id=binding_id,
        instrument_id=instrument_id,
        interval="1m",
        listener=lambda update: received.append(("chart-1", update.close)),
    )
    second_key, second_created = manager.subscribe(
        listener_id="chart-2",
        binding_id=binding_id,
        instrument_id=instrument_id,
        interval="1m",
        listener=lambda update: received.append(("chart-2", update.close)),
    )
    assert first_key == second_key
    assert first_created is True
    assert second_created is False
    assert manager.upstream_subscription_count == 1

    moment = datetime(2026, 8, 5, tzinfo=timezone.utc)
    listeners = manager.publish(
        StreamingBarUpdate(
            binding_id=binding_id,
            instrument_id=instrument_id,
            interval="1m",
            start_time=moment,
            end_time=moment + timedelta(minutes=1),
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
            volume=Decimal("5"),
            is_final=False,
        )
    )
    assert listeners == 2
    assert received == [("chart-1", Decimal("101")), ("chart-2", Decimal("101"))]
    assert manager.unsubscribe(first_key, "chart-1") is False
    assert manager.unsubscribe(first_key, "chart-2") is True
    assert manager.upstream_subscription_count == 0
