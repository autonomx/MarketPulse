from __future__ import annotations

from typing import Any

from app.apps.trading.catalog import (
    all_bindings,
    binding_by_id,
    bindings_for_instrument,
    default_binding,
    instrument_by_id,
)
from app.apps.trading.instrument_catalog_service import ProviderBackedInstrumentCatalog


class FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeRuntime:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def test_provider_catalog_discovers_binance_spot_symbols_and_registers_binding() -> None:
    binance = FakeRuntime(
        {
            "symbols": [
                {
                    "symbol": "ADAUSDT",
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "baseAsset": "ADA",
                    "quoteAsset": "USDT",
                    "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.0001"}],
                },
                {
                    "symbol": "DELISTEDUSDT",
                    "status": "BREAK",
                    "isSpotTradingAllowed": True,
                    "baseAsset": "DELISTED",
                    "quoteAsset": "USDT",
                },
            ]
        }
    )
    yahoo = FakeRuntime({"quotes": []})
    catalog = ProviderBackedInstrumentCatalog(binance_runtime=binance, yahoo_runtime=yahoo)

    results = catalog.search("ADA")

    assert [item.display_symbol for item in results] == ["ADAUSDT"]
    instrument = results[0]
    assert instrument_by_id(instrument.instrument_id) == instrument
    binding = default_binding(instrument.instrument_id)
    assert binding is not None
    assert binding.provider == "binance"
    assert binding.provider_symbol == "ADAUSDT"
    assert binding in bindings_for_instrument(instrument.instrument_id)


def test_provider_catalog_does_not_duplicate_static_binding_ids() -> None:
    binance = FakeRuntime(
        {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                },
            ],
        },
    )
    catalog = ProviderBackedInstrumentCatalog(
        binance_runtime=binance,
        yahoo_runtime=FakeRuntime({"quotes": []}),
    )

    catalog.search("BTC")

    binding_ids = [binding.binding_id for binding in all_bindings()]
    assert len(binding_ids) == len(set(binding_ids))
    assert binding_ids.count("binance:websocket_and_rest:crypto:BINANCE:spot:BTC-USDT") == 1


def test_provider_catalog_discovers_yahoo_equity_search_results() -> None:
    binance = FakeRuntime({"symbols": []})
    yahoo = FakeRuntime(
        {
            "quotes": [
                {
                    "symbol": "AMD",
                    "quoteType": "EQUITY",
                    "exchange": "NMS",
                    "shortname": "Advanced Micro Devices, Inc.",
                }
            ]
        }
    )
    catalog = ProviderBackedInstrumentCatalog(binance_runtime=binance, yahoo_runtime=yahoo)

    results = catalog.search("AMD")

    assert [item.display_symbol for item in results] == ["AMD"]
    instrument = results[0]
    assert instrument.instrument_id == "equity:NASDAQ:AMD"
    assert instrument.venue == "NASDAQ"
    assert {item.provider for item in bindings_for_instrument(instrument.instrument_id)} == {
        "yahoo",
        "ibkr",
        "alpaca_iex",
        "stooq",
    }


def test_catalog_exposes_common_yahoo_commodity_aliases() -> None:
    catalog = ProviderBackedInstrumentCatalog(
        binance_runtime=FakeRuntime({"symbols": []}),
        yahoo_runtime=FakeRuntime({"quotes": []}),
    )

    results = catalog.search("USOIL")

    assert [item.display_symbol for item in results] == ["USOIL"]
    instrument = results[0]
    assert instrument.asset_class.value == "commodity"
    assert instrument.instrument_id == "commodity:YAHOO:USOIL"
    binding = default_binding(instrument.instrument_id)
    assert binding is not None
    assert binding.provider == "yahoo"
    assert binding.provider_symbol == "CL=F"


def test_provider_catalog_keeps_yahoo_futures_instrument_search() -> None:
    catalog = ProviderBackedInstrumentCatalog(
        binance_runtime=FakeRuntime({"symbols": []}),
        yahoo_runtime=FakeRuntime(
            {
                "quotes": [
                    {"symbol": "ZC=F", "quoteType": "FUTURE", "exchange": "CBT"},
                ],
            },
        ),
    )

    results = catalog.search("ZC=F")

    assert [item.display_symbol for item in results] == ["ZC=F"]
    instrument = results[0]
    assert instrument.asset_class.value == "commodity"
    binding = default_binding(instrument.instrument_id)
    assert binding is not None
    assert binding.provider == "yahoo"


def test_short_queries_do_not_hit_provider_catalogs() -> None:
    binance = FakeRuntime({"symbols": []})
    yahoo = FakeRuntime({"quotes": []})
    catalog = ProviderBackedInstrumentCatalog(binance_runtime=binance, yahoo_runtime=yahoo)

    catalog.search("A")

    assert binance.calls == []
    assert yahoo.calls == []


def test_persisted_dynamic_equity_id_rehydrates_bindings() -> None:
    instrument_id = "equity:BTS:SPYI"

    binding = default_binding(instrument_id)

    assert binding is not None
    assert binding.provider == "yahoo"
    assert binding.provider_symbol == "SPYI"
    assert instrument_by_id(instrument_id) is not None
    assert {item.provider for item in bindings_for_instrument(instrument_id)} == {
        "yahoo",
        "ibkr",
        "alpaca_iex",
        "stooq",
    }
    assert binding_by_id(binding.binding_id) == binding


def test_otc_equity_binding_exposes_only_daily_yahoo_intervals() -> None:
    binding = next(
        item
        for item in bindings_for_instrument("equity:PNK:GMETF")
        if item.provider == "yahoo"
    )

    assert binding.supported_intervals == ("1d", "1w", "1mo")


def test_persisted_dynamic_equity_binding_id_rehydrates_catalog() -> None:
    binding_id = "stooq:historical_daily:equity:BTS:SPYI"

    binding = binding_by_id(binding_id)

    assert binding is not None
    assert binding.binding_id == binding_id
    assert binding.instrument_id == "equity:BTS:SPYI"


def test_persisted_dynamic_binance_crypto_id_rehydrates_bindings() -> None:
    instrument_id = "crypto:BINANCE:spot:GMEB-USDT"

    instrument = instrument_by_id(instrument_id)
    binding = default_binding(instrument_id)

    assert instrument is not None
    assert binding is not None
    assert binding.provider == "binance"
    assert binding.provider_symbol == "GMEBUSDT"
    assert binding_by_id(binding.binding_id) == binding
