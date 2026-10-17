from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.apps.trading.providers.alpaca_iex import AlpacaIexExecutionProvider
from app.apps.trading.providers.errors import ProviderContractError


def test_missing_alpaca_indicator_bars_normalizes_to_empty(monkeypatch) -> None:
    def no_bars(self, *args, **kwargs):
        raise ProviderContractError("Alpaca IEX historical-bars response has no bars list")

    monkeypatch.setattr(AlpacaIexExecutionProvider, "_indicator_bars_as_of", no_bars)
    provider = object.__new__(AlpacaIexExecutionProvider)

    result = provider.indicator_bars_as_of(
        "equity:NASDAQ:COLA",
        as_of=datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc),
    )

    assert result == []


def test_other_alpaca_contract_failures_still_propagate(monkeypatch) -> None:
    def malformed(self, *args, **kwargs):
        raise ProviderContractError("Alpaca IEX historical bar returned invalid close")

    monkeypatch.setattr(AlpacaIexExecutionProvider, "_indicator_bars_as_of", malformed)
    provider = object.__new__(AlpacaIexExecutionProvider)

    with pytest.raises(ProviderContractError, match="invalid close"):
        provider.indicator_bars_as_of(
            "equity:NASDAQ:COLA",
            as_of=datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc),
        )
