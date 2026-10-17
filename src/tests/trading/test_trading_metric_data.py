from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.metric_data import (
    BinanceDerivativesMetricAdapter,
    BinanceLiquidationBuffer,
    BlockchainMetricAdapter,
    LiquidationEvent,
    TradingMetricDataService,
    YahooFundamentalMetricAdapter,
)
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.headers = {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class RoutingSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        for suffix, payload in self.routes.items():
            if suffix in url:
                return FakeResponse(payload)
        raise AssertionError(f"unexpected URL: {url}")


def runtime(session, provider="fixture"):
    return ProviderHttpRuntime(provider, session=session, max_attempts=1, initial_backoff_seconds=0)


def test_binance_derivatives_open_interest_funding_and_ratios_are_typed() -> None:
    session = RoutingSession(
        {
            "openInterestHist": [
                {"symbol": "BTCUSDT", "sumOpenInterest": "10.5", "sumOpenInterestValue": "700000", "timestamp": 1_700_000_000_000},
                {"symbol": "BTCUSDT", "sumOpenInterest": "11.0", "sumOpenInterestValue": "720000", "timestamp": 1_700_000_300_000},
            ],
            "/fapi/v1/fundingRate": [
                {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1_700_000_000_000},
            ],
            "globalLongShortAccountRatio": [
                {"symbol": "BTCUSDT", "longShortRatio": "1.5", "longAccount": "0.6", "shortAccount": "0.4", "timestamp": 1_700_000_000_000},
            ],
        }
    )
    adapter = BinanceDerivativesMetricAdapter(
        cache=TradingMarketDataCache(),
        runtime=runtime(session, "binance-futures-test"),
    )
    instrument = "crypto:BINANCE:spot:BTC-USDT"

    oi = adapter.open_interest(instrument, "1m", 20)
    funding = adapter.funding_rate(instrument, "1h", 20)
    ratio = adapter.long_short_ratio(instrument, "1h", 20, scope="global_accounts")

    assert oi.series[0].points[-1].value == Decimal("11.0")
    assert oi.interval == "5m"
    assert funding.series[0].points[0].value == Decimal("0.0100")
    assert {series.key for series in ratio.series} == {"ratio", "long-percent", "short-percent"}
    assert next(series for series in ratio.series if series.key == "long-percent").points[0].value == Decimal("60.0")


def test_binance_force_order_parser_and_runtime_aggregation_do_not_invent_history() -> None:
    parsed = BinanceLiquidationBuffer.parse(
        {
            "e": "forceOrder",
            "E": 1_700_000_001_000,
            "o": {
                "S": "SELL",
                "ap": "70000",
                "z": "0.5",
                "T": 1_700_000_000_000,
            },
        }
    )
    assert parsed.liquidation_side == "long"
    assert parsed.notional == Decimal("35000.0")

    class StaticBuffer:
        def snapshot(self, symbol):
            assert symbol == "BTCUSDT"
            return [
                LiquidationEvent(datetime(2026, 8, 24, 12, 1, tzinfo=timezone.utc), "long", Decimal("100")),
                LiquidationEvent(datetime(2026, 8, 24, 12, 2, tzinfo=timezone.utc), "short", Decimal("40")),
                LiquidationEvent(datetime(2026, 8, 24, 12, 4, tzinfo=timezone.utc), "long", Decimal("60")),
            ]

        def is_collecting(self, symbol):
            return True

    adapter = BinanceDerivativesMetricAdapter(
        cache=TradingMarketDataCache(),
        runtime=runtime(RoutingSession({}), "binance-futures-test"),
        liquidation_buffer=StaticBuffer(),
    )
    result = adapter.liquidations("crypto:BINANCE:spot:BTC-USDT", "5m", 20)

    longs = next(series for series in result.series if series.key == "long-liquidations")
    shorts = next(series for series in result.series if series.key == "short-liquidations")
    assert [point.value for point in longs.points] == [Decimal("160")]
    assert [point.value for point in shorts.points] == [Decimal("40")]
    assert result.metadata["history_scope"].startswith("runtime-only")
    assert result.history_complete is False


def test_yahoo_analyst_targets_and_ttm_dividend_yield_are_real_provider_fields() -> None:
    session = RoutingSession(
        {
            "quoteSummary/NVDA": {
                "quoteSummary": {
                    "result": [
                        {
                            "financialData": {
                                "targetLowPrice": {"raw": 180.0},
                                "targetMeanPrice": {"raw": 240.0},
                                "targetHighPrice": {"raw": 310.0},
                                "numberOfAnalystOpinions": {"raw": 50},
                            }
                        }
                    ]
                }
            },
            "chart/NVDA": {
                "chart": {
                    "result": [
                        {
                            "meta": {"regularMarketPrice": 200.0},
                            "events": {
                                "dividends": {
                                    "a": {"amount": 1.0},
                                    "b": {"amount": 1.5},
                                }
                            },
                            "indicators": {"quote": [{"close": [198.0, 200.0]}]},
                        }
                    ]
                }
            },
        }
    )
    adapter = YahooFundamentalMetricAdapter(
        cache=TradingMarketDataCache(),
        runtime=runtime(session, "yahoo-metrics-test"),
    )

    targets = adapter.analyst_targets("equity:NASDAQ:NVDA", "1d", 20)
    dividend = adapter.dividend_yield("equity:NASDAQ:NVDA", "1d", 20)

    assert [series.points[0].value for series in targets.series] == [
        Decimal("180.0"),
        Decimal("240.0"),
        Decimal("310.0"),
    ]
    assert targets.metadata["analyst_opinions"] == 50
    assert dividend.series[0].points[0].value == Decimal("1.2500")
    assert dividend.metadata["method"] == "trailing_12_month_dividends/current_price"


def test_blockchain_chart_adapter_is_bitcoin_only_and_converts_megabytes_to_bytes() -> None:
    session = RoutingSession(
        {
            "/charts/avg-block-size": {
                "values": [
                    {"x": 1_700_000_000, "y": 1.25},
                    {"x": 1_700_086_400, "y": 1.50},
                ]
            }
        }
    )
    adapter = BlockchainMetricAdapter(
        cache=TradingMarketDataCache(),
        runtime=runtime(session, "blockchain-test"),
    )
    response = adapter.metric(
        "crypto:BINANCE:spot:BTC-USDT",
        "blockchain.mean_block_size_bytes",
        "1d",
        20,
    )
    assert [point.value for point in response.series[0].points] == [
        Decimal("1250000.00"),
        Decimal("1500000.00"),
    ]
    assert response.metadata["bitcoin_only"] is True


def test_metric_service_routes_each_external_data_family() -> None:
    class Stub:
        def __init__(self, label):
            self.label = label
            self.calls = []

        def open_interest(self, *args, **kwargs):
            self.calls.append(("open_interest", args, kwargs))
            return self.label

        def analyst_targets(self, *args, **kwargs):
            self.calls.append(("analyst_targets", args, kwargs))
            return self.label

        def metric(self, *args, **kwargs):
            self.calls.append(("metric", args, kwargs))
            return self.label

    binance = Stub("binance")
    yahoo = Stub("yahoo")
    blockchain = Stub("blockchain")
    service = TradingMetricDataService(
        cache=TradingMarketDataCache(),
        binance=binance,
        yahoo=yahoo,
        blockchain=blockchain,
    )

    assert service.metric("crypto:BINANCE:spot:BTC-USDT", "binance.open_interest", "1h") == "binance"
    assert service.metric("equity:NASDAQ:NVDA", "yahoo.analyst_price_forecast", "1d") == "yahoo"
    assert service.metric("crypto:BINANCE:spot:BTC-USDT", "blockchain.hash_rate", "1d") == "blockchain"


def test_the_default_metric_service_constructs_without_an_injected_cache() -> None:
    """The metric API and monitor build the service with no arguments."""
    from app.apps.trading.cache import TradingMarketDataCache
    from app.apps.trading.metric_data import TradingMetricDataService

    service = TradingMetricDataService()
    assert isinstance(service.cache, TradingMarketDataCache)
    assert service.binance.cache is service.cache
