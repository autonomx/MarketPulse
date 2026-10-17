from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.providers.additional_crypto import AdditionalCryptoProvider
from app.apps.trading.providers.bar_semantics import (
    continuous_bar_end,
    equity_bar_times,
    is_final_bar,
)
from app.apps.trading.providers.equity import YahooEquityProvider
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("GET", "https://provider.test")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def request(self, method: str, url: str, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_interval_and_equity_session_semantics_are_explicit() -> None:
    start = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    assert continuous_bar_end(start, "2h") == start + timedelta(hours=2)
    regular_start, regular_end, session = equity_bar_times(
        datetime(2026, 8, 6, 13, 30, tzinfo=timezone.utc),
        "1d",
        "America/New_York",
    )
    assert session == "regular"
    assert regular_start < regular_end
    assert regular_end.hour in {20, 21}
    assert is_final_bar(regular_end, regular_end + timedelta(seconds=1)) is True
    assert is_final_bar(regular_end, regular_end - timedelta(seconds=1)) is False


def test_additional_crypto_latest_open_candle_is_not_marked_final() -> None:
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    timestamp = int(future.timestamp())
    session = FakeSession(
        [FakeResponse([[timestamp, "99", "101", "100", "100.5", "10"]])]
    )
    runtime = ProviderHttpRuntime(
        "coinbase",
        session=session,
        max_attempts=1,
        initial_backoff_seconds=0,
    )
    provider = AdditionalCryptoProvider("coinbase", runtime=runtime)
    response = provider.get_bars("crypto:COINBASE:spot:BTC-USD", "1m", 1)
    assert response.bars[0].end_time > response.bars[0].start_time
    assert response.bars[0].is_final is False


def test_yahoo_daily_history_keeps_the_full_stock_window() -> None:
    count = 2_005
    timestamps = [1_600_000_000 + index * 86_400 for index in range(count)]
    values = [100 + index for index in range(count)]
    payload = {
        "chart": {
            "result": [{
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "open": values,
                        "high": [value + 1 for value in values],
                        "low": [value - 1 for value in values],
                        "close": values,
                        "volume": [1_000] * count,
                    }],
                },
            }],
        },
    }
    session = FakeSession([FakeResponse(payload)])
    runtime = ProviderHttpRuntime(
        "yahoo",
        session=session,
        max_attempts=1,
        initial_backoff_seconds=0,
    )
    provider = YahooEquityProvider(runtime=runtime, cache=TradingMarketDataCache())

    response = provider.get_bars("equity:NASDAQ:AAPL", "1d", 5_000)

    assert len(response.bars) == 2_000
    assert response.bars[0].provider_event_id == str(timestamps[5])
    assert response.provenance.history_complete is False
    assert session.calls == 1


def test_yahoo_currency_rate_normalizes_stablecoins_and_caches() -> None:
    payload = {
        "chart": {
            "result": [{
                "indicators": {"quote": [{"close": [None, 1.4]}]},
            }],
        },
    }
    session = FakeSession([FakeResponse(payload)])
    runtime = ProviderHttpRuntime(
        "yahoo",
        session=session,
        max_attempts=1,
        initial_backoff_seconds=0,
    )
    provider = YahooEquityProvider(runtime=runtime, cache=TradingMarketDataCache())

    first = provider.get_currency_rate("USDT", "CAD")
    second = provider.get_currency_rate("USD", "CAD")

    assert first["base_currency"] == "USD"
    assert first["quote_currency"] == "CAD"
    assert first["rate"] == 1.4
    assert second["rate"] == 1.4
    assert session.calls == 1


def test_provider_runtime_retries_and_reports_health() -> None:
    session = FakeSession([FakeResponse({}, status_code=503), FakeResponse({"ok": True})])
    runtime = ProviderHttpRuntime(
        "fixture",
        session=session,
        max_attempts=2,
        initial_backoff_seconds=0,
    )
    response = runtime.get("https://example.invalid", timeout=1)
    assert response.json() == {"ok": True}
    snapshot = runtime.snapshot()
    assert session.calls == 2
    assert snapshot.success_count == 1
    assert snapshot.failure_count == 1
    assert snapshot.status == "ready"


def test_market_data_cache_is_bounded_and_writes_no_files(tmp_path: Path, monkeypatch) -> None:
    # Decision data is not kept in local files (WP-8.3).
    monkeypatch.chdir(tmp_path)
    cache = TradingMarketDataCache(max_entries=2)
    cache.put("one", {"value": 1}, ttl_seconds=60, source="fixture")
    cache.put("two", {"value": 2}, ttl_seconds=60, source="fixture")
    cache.put("three", {"value": 3}, ttl_seconds=60, source="fixture")

    assert cache.get("one") is None
    assert cache.get("three").value == {"value": 3}
    assert list(tmp_path.rglob("*")) == []
