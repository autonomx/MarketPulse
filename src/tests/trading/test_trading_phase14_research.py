from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.apps.trading.models import (
    AssetClass,
    BarsResponse,
    CanonicalInstrument,
    DatasetProvenance,
    FeedType,
    InstrumentType,
    MarketBar,
    ProviderBinding,
    UsageScope,
)
from app.apps.trading.research import (
    MAX_RESEARCH_BARS,
    MAX_RESEARCH_PROMPT_CHARS,
    MarketResearchRequest,
    build_research_context,
    generate_market_research,
)
from app.apps.trading.research_api import create_trading_research_router


NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
INSTRUMENT = "crypto:BINANCE:spot:BTC-USDT"
BINDING = "binance:BTCUSDT"


def bars_response(count=120) -> BarsResponse:
    instrument = CanonicalInstrument(
        instrument_id=INSTRUMENT,
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.SPOT,
        venue="BINANCE",
        venue_symbol="BTCUSDT",
        display_symbol="BTC/USDT",
        base_currency="BTC",
        quote_currency="USDT",
        exchange_timezone="UTC",
        session_calendar="24x7",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
    )
    binding = ProviderBinding(
        binding_id=BINDING,
        instrument_id=INSTRUMENT,
        provider="binance",
        provider_symbol="BTCUSDT",
        feed_type=FeedType.HISTORICAL_POLLING,
        realtime_scope="public market data",
        supported_intervals=("1d",),
        usage_scope=UsageScope.PERSONAL_LOCAL,
        is_official_api=True,
    )
    bars = [
        MarketBar(
            instrument_id=INSTRUMENT,
            interval="1d",
            start_time=NOW + timedelta(days=index),
            end_time=NOW + timedelta(days=index + 1),
            open=Decimal(50_000 + index),
            high=Decimal(50_100 + index),
            low=Decimal(49_900 + index),
            close=Decimal(50_050 + index),
            volume=Decimal(1_000 + index),
            is_final=True,
            provider="binance",
            received_at=NOW + timedelta(days=index + 1),
        )
        for index in range(count)
    ]
    return BarsResponse(
        instrument=instrument,
        binding=binding,
        provenance=DatasetProvenance(
            instrument_id=INSTRUMENT,
            requested_binding=BINDING,
            resolved_binding=BINDING,
            dataset_fingerprint="research-fixture-fingerprint",
            freshness_mode="polled",
            as_of=bars[-1].end_time,
            received_at=bars[-1].received_at,
            cached=False,
            history_complete=True,
        ),
        interval="1d",
        bars=bars,
    )


class FakeMarketService:
    def __init__(self, count=120) -> None:
        self.count = count
        self.calls = []

    def bars(self, instrument_id, interval, limit, binding_id=None):
        self.calls.append((instrument_id, interval, limit, binding_id))
        return bars_response(min(limit, self.count))


class FakeProvider:
    provider_name = "fixture-provider"

    def __init__(self, content=None) -> None:
        self.config = SimpleNamespace(model="fixture-model")
        self.content = content or json.dumps({
            "summary": "Momentum is positive but extended relative to the recent average.",
            "observations": ["The latest close remains above the 20-period averages."],
            "risks": ["A reversal below the selected support would weaken the structure."],
            "confidence": 0.72,
        })
        self.messages = None
        self.kwargs = None

    def chat_completion(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return SimpleNamespace(content=self.content)


def request(**overrides) -> MarketResearchRequest:
    payload = {
        "instrument_id": INSTRUMENT,
        "binding_id": BINDING,
        "interval": "1d",
        "bar_limit": 120,
        "question": "Summarize the technical structure and risks.",
        "selected_levels": ["50000", "49000"],
    }
    payload.update(overrides)
    return MarketResearchRequest.model_validate(payload)


def test_research_request_and_context_are_bounded_and_finalized() -> None:
    with pytest.raises(ValidationError):
        request(bar_limit=MAX_RESEARCH_BARS + 1)
    with pytest.raises(ValidationError):
        request(question="x" * 801)
    context, source = build_research_context(request(), bars_response())
    assert source.bar_count == 120
    assert len(context["recent_finalized_bars"]) == 40
    assert source.dataset_fingerprint == "research-fixture-fingerprint"
    assert source.formula_version == "omnix-indicators-v2"
    assert len(json.dumps(context, separators=(",", ":"), sort_keys=True)) < MAX_RESEARCH_PROMPT_CHARS


def test_research_uses_registered_provider_and_attaches_exact_source_metadata() -> None:
    provider = FakeProvider()
    market = FakeMarketService()
    result = generate_market_research(
        request(),
        market_service_factory=lambda: market,
        provider_factory=lambda: provider,
    )
    assert market.calls == [(INSTRUMENT, "1d", 120, BINDING)]
    assert result.read_only is True
    assert result.provider == "fixture-provider"
    assert result.model == "fixture-model"
    assert result.source.instrument_id == INSTRUMENT
    assert result.source.resolved_binding_id == BINDING
    assert result.source.dataset_fingerprint == "research-fixture-fingerprint"
    assert result.source.as_of == bars_response().provenance.as_of
    assert result.disclaimer.endswith("No order was created or executed.")
    assert provider.kwargs["stream"] is False
    assert provider.kwargs["temperature"] == 0
    prompt = provider.messages[1].content
    assert "normalized_context" in prompt
    assert "recent_finalized_bars" in prompt


def test_research_rejects_malformed_or_action_shaped_provider_output() -> None:
    malformed = FakeProvider("not-json")
    with pytest.raises(json.JSONDecodeError):
        generate_market_research(
            request(),
            market_service_factory=lambda: FakeMarketService(),
            provider_factory=lambda: malformed,
        )

    action_output = FakeProvider(json.dumps({
        "summary": "Attempted action",
        "observations": ["Observation"],
        "risks": ["Risk"],
        "confidence": 0.5,
        "order": {"side": "buy"},
    }))
    with pytest.raises(ValidationError):
        generate_market_research(
            request(),
            market_service_factory=lambda: FakeMarketService(),
            provider_factory=lambda: action_output,
        )


def test_research_api_is_read_only_and_maps_invalid_output_to_provider_failure() -> None:
    app = FastAPI()
    app.include_router(
        create_trading_research_router(
            market_service_factory=lambda: FakeMarketService(),
            provider_factory=lambda: FakeProvider(),
        )
    )
    client = TestClient(app)
    response = client.post(
        "/api/trading/research",
        json=request().model_dump(mode="json"),
    )
    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert response.json()["source"]["dataset_fingerprint"] == "research-fixture-fingerprint"

    invalid_app = FastAPI()
    invalid_app.include_router(
        create_trading_research_router(
            market_service_factory=lambda: FakeMarketService(),
            provider_factory=lambda: FakeProvider("not-json"),
        )
    )
    invalid = TestClient(invalid_app).post(
        "/api/trading/research",
        json=request().model_dump(mode="json"),
    )
    assert invalid.status_code == 502
    assert invalid.json()["detail"]["code"] == "invalid_research_output"


def test_research_has_no_direct_provider_or_mutation_dependency() -> None:
    source = Path(
        "src/app/apps/trading/research/market_research.py"
    ).read_text().lower()
    for forbidden in (
        "lmstudio",
        "openai.chat",
        "openrouter",
        "cerebras",
        "create_alert",
        "place_order",
        "run_backtest",
        "process_observation",
    ):
        assert forbidden not in source
    assert "from app import " + "shared" not in source
    assert "app.providers" in source
    gateway = Path("src/app/apps/trading/route_registration.py").read_text()
    assert "create_trading_research_router" in gateway
