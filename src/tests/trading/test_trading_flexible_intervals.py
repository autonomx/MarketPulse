from __future__ import annotations

from datetime import datetime, timezone

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import BINANCE_POLICY, INSTRUMENTS
from app.apps.trading.providers.binance import INTERVAL_SECONDS, BinanceMarketDataProvider
from app.apps.trading.streaming.gap_recovery import INTERVAL_DELTAS, recovery_window


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get(self, url, *, params, timeout):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        open_time = 1_700_000_000_000
        return FakeResponse(
            [[open_time, "100", "110", "90", "105", "12", open_time + 7_199_999]]
        )


def test_binance_exposes_real_two_hour_and_weekly_intervals() -> None:
    assert "2h" in BINANCE_POLICY.supported_intervals
    assert "1w" in BINANCE_POLICY.supported_intervals
    assert INTERVAL_SECONDS["2h"] == 7_200
    assert INTERVAL_SECONDS["1w"] == 604_800
    assert INTERVAL_DELTAS["2h"].total_seconds() == 7_200
    assert INTERVAL_DELTAS["1w"].total_seconds() == 604_800
    assert "1mo" in BINANCE_POLICY.supported_intervals
    assert INTERVAL_SECONDS["1mo"] == 2_592_000
    assert INTERVAL_DELTAS["1mo"].total_seconds() == 2_592_000


def test_two_hour_request_is_forwarded_to_binance_and_normalized() -> None:
    session = FakeSession()
    provider = BinanceMarketDataProvider(session=session, cache=TradingMarketDataCache())
    result = provider.get_bars(INSTRUMENTS[0].instrument_id, "2h", 1)

    assert session.calls[0]["params"]["interval"] == "2h"
    assert result.interval == "2h"
    assert result.bars[0].interval == "2h"


def test_two_hour_recovery_window_uses_the_same_interval_contract() -> None:
    last = datetime(2026, 8, 5, 10, tzinfo=timezone.utc)
    first_stream = datetime(2026, 8, 5, 16, tzinfo=timezone.utc)
    assert recovery_window(last, first_stream, interval="2h") == (
        datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
        first_stream,
    )


def test_monthly_request_uses_binance_monthly_wire_interval() -> None:
    session = FakeSession()
    provider = BinanceMarketDataProvider(session=session, cache=TradingMarketDataCache())
    result = provider.get_bars(INSTRUMENTS[0].instrument_id, "1mo", 1)

    assert session.calls[0]["params"]["interval"] == "1M"
    assert result.interval == "1mo"
    assert result.bars[0].interval == "1mo"
