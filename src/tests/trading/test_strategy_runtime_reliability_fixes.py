from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.apps.trading import strategy_ai_shadow_provider as provider
from app.apps.trading import strategy_evaluability
from app.apps.trading.providers import alpaca_iex
from app.apps.trading.strategy_ai_shadow_monitor import TradingAIShadowMonitor
from app.apps.trading.strategy_ai_shadow_v2_monitor import TradingAIShadowV2Monitor
from app.apps.trading.strategy_outcome_quality import outcome_is_valid as _outcome_is_valid
from app.apps.trading.strategy_session_evidence import _CurrentSessionMarketDataProxy


class _Response:
    def __init__(self, payload=None, *, bars=None):
        self._payload = payload
        self.bars = list(bars or [])

    def json(self):
        return self._payload


class _Runtime:
    def __init__(self, payload):
        self.payload = payload
        self.session = object()

    def get(self, *_args, **_kwargs):
        return _Response(self.payload)


class _Repository:
    def __init__(self):
        self.events = []

    def append_event(self, event):
        self.events.append(event)
        return True


def _bar(start: datetime, *, price: str = "10"):
    return SimpleNamespace(
        instrument_id="equity:NASDAQ:ACVA",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        is_final=True,
        session="regular",
        provider="test",
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal("100"),
    )


def _snapshot(*, quote_time: datetime, trade_time: datetime, bid: str, ask: str):
    return {
        "latestQuote": {
            "t": quote_time.isoformat().replace("+00:00", "Z"),
            "bp": bid,
            "ap": ask,
            "bs": 5,
            "as": 5,
        },
        "latestTrade": {
            "t": trade_time.isoformat().replace("+00:00", "Z"),
            "p": str((Decimal(bid) + Decimal(ask)) / Decimal("2")),
        },
        "minuteBar": {
            "t": quote_time.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z"),
            "h": ask,
            "l": bid,
            "v": 1000,
        },
        "dailyBar": {"v": 100000},
    }


def _execution_provider(monkeypatch, payload, *, now: datetime):
    monkeypatch.setattr(alpaca_iex, "alpaca_iex_auth_headers", lambda: {})
    monkeypatch.setattr(
        alpaca_iex,
        "default_alpaca_iex_status_cache",
        lambda: SimpleNamespace(halted=lambda _symbol: False),
    )
    provider = alpaca_iex.AlpacaIexExecutionProvider(
        runtime=_Runtime(payload),
        clock=lambda: now,
    )
    provider.get_binding = lambda _instrument_id: SimpleNamespace(
        binding_id="alpaca-iex-acva",
        provider_symbol="ACVA",
    )
    return provider


def test_execution_freshness_uses_fresh_quote_not_older_last_trade(monkeypatch) -> None:
    quote_time = datetime(2026, 9, 11, 14, 51, 24, tzinfo=timezone.utc)
    trade_time = datetime(2026, 9, 11, 14, 49, 0, tzinfo=timezone.utc)
    provider = _execution_provider(
        monkeypatch,
        _snapshot(quote_time=quote_time, trade_time=trade_time, bid="10.41", ask="10.42"),
        now=quote_time + timedelta(seconds=1),
    )

    observation = provider.execution_observation("equity:NASDAQ:ACVA")

    assert observation.source_time == quote_time
    assert observation.execution_eligible is True
    assert "STALE_MARKET_DATA" not in observation.rejection_reasons


def test_execution_fix_does_not_weaken_wide_spread_veto(monkeypatch) -> None:
    quote_time = datetime(2026, 9, 11, 13, 47, 19, tzinfo=timezone.utc)
    provider = _execution_provider(
        monkeypatch,
        _snapshot(
            quote_time=quote_time,
            trade_time=quote_time - timedelta(seconds=30),
            bid="6.90",
            ask="9.00",
        ),
        now=quote_time + timedelta(seconds=1),
    )

    observation = provider.execution_observation("equity:NASDAQ:TNON")

    assert observation.execution_eligible is False
    assert "SPREAD_TOO_WIDE" in observation.rejection_reasons


def test_opening_bar_is_not_required_until_it_is_complete() -> None:
    assessment = strategy_evaluability.assess_bar_coverage(
        [],
        session_date=datetime(2026, 9, 11, tzinfo=timezone.utc).date(),
        observed_at=datetime(2026, 9, 11, 13, 30, 30, tzinfo=timezone.utc),
        provider="test",
    )

    assert assessment.ready is False
    assert assessment.reason_codes == ("CURRENT_SESSION_NOT_STARTED",)


def test_shadow_proxy_drops_previous_session_bars_before_open() -> None:
    previous = _bar(datetime(2026, 9, 10, 19, 59, tzinfo=timezone.utc), price="7.23")

    class Delegate:
        def bars(self, *_args, **_kwargs):
            return _Response(bars=[previous])

        def execution_indicator_bars(self, *_args, **_kwargs):
            raise AssertionError("pre-open current-session filter must not request regular fallback")

    observed = datetime(2026, 9, 11, 13, 20, 35, tzinfo=timezone.utc)  # 09:20 ET
    proxy = _CurrentSessionMarketDataProxy(
        Delegate(),
        session_date=observed.astimezone(timezone(timedelta(hours=-4))).date(),
        observed_at=observed,
    )

    response = proxy.bars("equity:NASDAQ:ACVA", "1m", 500, "binding")

    assert response.bars == []


def test_shadow_proxy_recovers_primary_history_exception_with_complete_iex_prefix() -> None:
    opening = datetime(2026, 9, 11, 13, 30, tzinfo=timezone.utc)
    current_session = [
        _bar(opening + timedelta(minutes=offset), price="10.42")
        for offset in range(81)
    ]

    class Delegate:
        def bars(self, *_args, **_kwargs):
            raise RuntimeError("Yahoo returned no bars")

        def execution_indicator_bars(self, *_args, **_kwargs):
            return current_session

    observed = datetime(2026, 9, 11, 14, 51, 30, tzinfo=timezone.utc)
    proxy = _CurrentSessionMarketDataProxy(
        Delegate(),
        session_date=datetime(2026, 9, 11, tzinfo=timezone.utc).date(),
        observed_at=observed,
    )

    response = proxy.bars("equity:NASDAQ:ACVA", "1m", 500, "binding")

    assert response.bars == current_session


def test_shadow_proxy_keeps_incomplete_fallback_non_actionable() -> None:
    partial = [
        _bar(datetime(2026, 9, 11, 13, 30, tzinfo=timezone.utc)),
        _bar(datetime(2026, 9, 11, 13, 32, tzinfo=timezone.utc)),
    ]

    class Delegate:
        def bars(self, *_args, **_kwargs):
            raise RuntimeError("Yahoo returned no bars")

        def execution_indicator_bars(self, *_args, **_kwargs):
            return partial

    observed = datetime(2026, 9, 11, 13, 33, 30, tzinfo=timezone.utc)
    proxy = _CurrentSessionMarketDataProxy(
        Delegate(),
        session_date=datetime(2026, 9, 11, tzinfo=timezone.utc).date(),
        observed_at=observed,
    )

    response = proxy.bars("equity:NASDAQ:ACVA", "1m", 500, "binding")

    assert response.bars == []


def test_preopen_opportunity_outcome_is_excluded_from_metrics() -> None:
    invalid = {
        "started_at": "2026-09-11T13:20:35.735745+00:00",
        "ended_at": "2026-09-11T14:20:35.735745+00:00",
        "entry_price": "7.23",
    }
    valid = {
        "started_at": "2026-09-11T14:00:00+00:00",
        "ended_at": "2026-09-11T15:00:00+00:00",
        "entry_price": "10.42",
    }

    assert _outcome_is_valid(invalid) is False
    assert _outcome_is_valid(valid) is True


@pytest.mark.anyio
async def test_v2_monitor_does_not_reenter_provider_path_while_circuit_open() -> None:
    async def scenario() -> None:
        circuit = provider._CIRCUIT
        circuit.success()
        circuit.failure_count = 1
        circuit.open_until_monotonic = time.monotonic() + 60
        try:
            monitor = TradingAIShadowV2Monitor()
            result = await monitor._run_arm(
                arm="full_session_control",
                rows=[],
                config=None,
                repository=None,
                events=[],
            )
            assert result is None
            assert monitor.last_error is not None
            assert "ai_shadow_v2_provider_circuit_open" in monitor.last_error
        finally:
            circuit.success()

    await scenario()


@pytest.mark.anyio
async def test_identical_gap_events_are_heartbeat_throttled() -> None:
    async def scenario() -> None:
        repository = _Repository()
        monitor = TradingAIShadowMonitor()
        config = SimpleNamespace(strategy_id="finviz-learning-v2-shadow")
        at = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
        kwargs = {
            "repository": repository,
            "config": config,
            "instrument_id": "equity:NASDAQ:BTCT",
            "event_type": "ai_shadow_input_gap",
            "state": "unavailable",
            "reason_code": "AI_SHADOW_BAR_COVERAGE_GAP",
            "payload": {"policy": "minute"},
            "identity": ("minute", "gap"),
        }

        first = await monitor._append(observed_at=at, **kwargs)
        duplicate = await monitor._append(observed_at=at + timedelta(minutes=1), **kwargs)
        heartbeat = await monitor._append(observed_at=at + timedelta(minutes=16), **kwargs)

        assert first is True
        assert duplicate is False
        assert heartbeat is True
        assert len(repository.events) == 2
        assert repository.events[0].payload["gap_sampling"] == "state_or_periodic_heartbeat"

    await scenario()
