from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading import strategy_historical_bars as historical


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeRuntime:
    def get(self, url, *, params=None, headers=None, timeout=None, cancellation=None):
        assert url.endswith('/v2/stocks/bars')
        assert params['timeframe'] == '1Min'
        assert params['feed'] == 'iex'
        return FakeResponse({
            'bars': {
                'ABC': [
                    {'t': '2026-08-18T13:30:00Z', 'o': 10, 'h': 10.5, 'l': 9.9, 'c': 10.4, 'v': 1000},
                    {'t': '2026-08-18T13:31:00Z', 'o': 10.4, 'h': 10.8, 'l': 10.3, 'c': 10.7, 'v': 1200},
                ]
            },
            'next_page_token': None,
        })


def test_reconstructed_session_replay_uses_batched_alpaca_iex_history(monkeypatch) -> None:
    monkeypatch.setattr(
        historical,
        'alpaca_iex_auth_headers',
        lambda: {
            'APCA-API-KEY-ID': 'test-key',
            'APCA-API-SECRET-KEY': 'test-secret',
        },
    )
    candidate = GapperCandidate(
        instrument_id='equity:NASDAQ:ABC',
        previous_close=Decimal('8'),
        premarket_price=Decimal('10.4'),
        gap_pct=Decimal('30'),
        premarket_dollar_volume=Decimal('12000000'),
        tod_rvol=Decimal('6'),
        spread_bps=Decimal('40'),
    )

    result = historical.alpaca_historical_session_bars(
        [candidate],
        date(2026, 8, 18),
        runtime=FakeRuntime(),
    )

    bars = result[candidate.instrument_id]
    assert len(bars) == 2
    assert bars[0].provider == 'alpaca_iex'
    assert bars[0].interval == '1m'
    assert bars[0].open == Decimal('10')
    assert bars[1].close == Decimal('10.7')
    assert bars[1].volume == Decimal('1200')
