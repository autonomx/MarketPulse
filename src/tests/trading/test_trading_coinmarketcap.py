from __future__ import annotations

from decimal import Decimal

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading import market_data_api
from app.apps.trading.catalog import bindings_for_instrument, instrument_by_id
from app.apps.trading.providers.coinmarketcap import CoinMarketCapProvider


class JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def json(self) -> dict[str, object]:
        return self.payload


class HttpErrorResponse(JsonResponse):
    status_code = 400


class FixtureRuntime:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.session = object()

    def get(self, url: str, **kwargs: object) -> JsonResponse:
        params = kwargs.get("params")
        assert isinstance(params, dict)
        self.calls.append((url, params))
        if "global-metrics" in url:
            return JsonResponse(self.payloads["global"])
        return JsonResponse(self.payloads[str(params["id"])])


def _global_payload() -> dict[str, object]:
    return {
        "status": {"error_code": 0},
        "data": {
            "quotes": [
                {
                    "timestamp": "2026-08-20T00:00:00.000Z",
                    "quote": {"USD": {"total_market_cap": 1000, "total_volume_24h": 80}},
                },
                {
                    "timestamp": "2026-08-21T00:00:00.000Z",
                    "quote": {"USD": {"total_market_cap": 1100, "total_volume_24h": 90}},
                },
            ]
        },
    }


def _asset_payload(first: int, second: int) -> dict[str, object]:
    return {
        "status": {"error_code": 0},
        "data": [{
            "quotes": [
                {
                    "timestamp": "2026-08-20T00:00:00.000Z",
                    "quote": {"USD": {"market_cap": first, "volume_24h": 10}},
                },
                {
                    "timestamp": "2026-08-21T00:00:00.000Z",
                    "quote": {"USD": {"market_cap": second, "volume_24h": 11}},
                },
            ]
        }],
    }


def test_crypto_cap_catalog_includes_symbols_and_historical_binding() -> None:
    instrument_id = "index:CRYPTOCAP:USDT.D"
    instrument = instrument_by_id(instrument_id)
    assert instrument is not None
    assert instrument.display_symbol == "USDT.D"
    binding = next(item for item in bindings_for_instrument(instrument_id) if item.provider == "coinmarketcap")
    assert binding.feed_type.value == "historical_daily"
    assert binding.supported_intervals == ("1d",)


def test_coinmarketcap_provider_derives_total3_and_dominance(monkeypatch) -> None:
    monkeypatch.setenv("COINMARKETCAP_API_KEY", "test-key")
    runtime = FixtureRuntime(
        {
            "global": _global_payload(),
            "1": _asset_payload(100, 120),
            "1027": _asset_payload(50, 60),
            "825": _asset_payload(70, 80),
        }
    )
    provider = CoinMarketCapProvider(runtime=runtime)

    total3 = provider.get_bars("index:CRYPTOCAP:TOTAL3", "1d", 2)
    assert [bar.close for bar in total3.bars] == [Decimal("850"), Decimal("920")]
    assert total3.bars[0].open == Decimal("850")
    assert total3.bars[1].open == Decimal("850")
    assert total3.bars[1].high == Decimal("920")
    assert total3.bars[1].low == Decimal("850")
    assert total3.bars[-1].provider == "coinmarketcap"

    dominance = provider.get_bars("index:CRYPTOCAP:USDT.D", "1d", 2)
    assert [bar.close for bar in dominance.bars] == [
        Decimal("7"),
        Decimal("80") / Decimal("1100") * Decimal("100"),
    ]
    assert len(runtime.calls) == 5


def test_coinmarketcap_provider_retries_basic_plan_daily_limit(monkeypatch) -> None:
    monkeypatch.setenv("COINMARKETCAP_API_KEY", "test-key")
    runtime = FixtureRuntime({"global": _global_payload(), "825": _asset_payload(70, 80)})
    original_get = runtime.get

    def get(url: str, **kwargs: object) -> JsonResponse:
        params = kwargs.get("params")
        assert isinstance(params, dict)
        if int(params.get("count") or 0) > 365:
            response = HttpErrorResponse(
                {
                    "status": {
                        "error_code": 1006,
                        "error_message": "Your plan only supports one year of daily history",
                    }
                }
            )
            raise httpx.HTTPStatusError("400 Client Error", request=httpx.Request("GET", url), response=response)
        return original_get(url, **kwargs)

    runtime.get = get  # type: ignore[method-assign]
    provider = CoinMarketCapProvider(runtime=runtime)

    result = provider.get_bars("index:CRYPTOCAP:USDT.D", "1d", 1000)

    assert len(result.bars) == 2
    assert result.provenance.history_complete is True
    assert [params["count"] for _, params in runtime.calls] == [365, 365]


def test_coinmarketcap_provider_uses_extended_history_before_basic_fallback(monkeypatch) -> None:
    monkeypatch.setenv("COINMARKETCAP_API_KEY", "test-key")
    runtime = FixtureRuntime({"global": _global_payload(), "825": _asset_payload(70, 80)})
    original_get = runtime.get

    def get(url: str, **kwargs: object) -> JsonResponse:
        params = kwargs.get("params")
        assert isinstance(params, dict)
        if int(params.get("count") or 0) > 1_095:
            response = HttpErrorResponse(
                {
                    "status": {
                        "error_code": 1006,
                        "error_message": "Your plan only supports three years of daily history",
                    }
                }
            )
            raise httpx.HTTPStatusError("400 Client Error", request=httpx.Request("GET", url), response=response)
        return original_get(url, **kwargs)

    runtime.get = get  # type: ignore[method-assign]
    provider = CoinMarketCapProvider(runtime=runtime)

    result = provider.get_bars("index:CRYPTOCAP:USDT.D", "1d", 5_000)

    assert len(result.bars) == 2
    assert result.provenance.history_complete is True
    assert [params["count"] for _, params in runtime.calls] == [1_095, 1_095]


def test_coinmarketcap_credential_status_masks_key_and_update_does_not_return_it(monkeypatch) -> None:
    state = {"api_key": "old-key"}

    monkeypatch.setattr(market_data_api, "load_trading_provider_secrets", lambda: {"coinmarketcap": dict(state)})
    monkeypatch.setattr(
        market_data_api,
        "trading_provider_credential_sources",
        lambda provider: {"api_key": "os_protected_store" if state.get("api_key") else "missing"},
    )
    monkeypatch.setattr(market_data_api.sys, "platform", "win32")

    def save(provider: str, updates: dict[str, str | None]) -> None:
        assert provider == "coinmarketcap"
        for key, value in updates.items():
            if value:
                state[key] = value
            else:
                state.pop(key, None)

    monkeypatch.setattr(market_data_api, "save_trading_provider_secrets", save)
    app = FastAPI()
    app.include_router(market_data_api.create_trading_market_data_router())
    client = TestClient(app)

    response = client.put(
        "/api/trading/market-data/providers/coinmarketcap/credentials",
        json={"api_key": "new-secret-key"},
    )

    assert response.status_code == 200
    assert state == {"api_key": "new-secret-key"}
    assert response.json()["api_key_masked"] == "***-key"
    assert "new-secret-key" not in response.text
