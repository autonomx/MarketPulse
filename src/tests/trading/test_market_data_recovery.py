from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.apps.trading.market_data_recovery import (
    StrategyDataRequirement,
    aggregate_complete_bars,
    assess_data_requirement,
    detect_session_gaps,
    reconcile_recovery,
    recover_market_bars,
)
from app.apps.trading.models import MarketBar
from app.apps.trading.service import TradingMarketDataService


SESSION_DATE = datetime(2026, 9, 16, tzinfo=timezone.utc).date()
OPEN = datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc)  # 09:30 ET


def _bar(
    minute: int,
    *,
    interval: str = "1m",
    provider: str = "yahoo",
    price: str = "10",
    volume: str = "1000",
) -> MarketBar:
    start = OPEN + timedelta(minutes=minute)
    duration = timedelta(minutes=int(interval[:-1]))
    value = Decimal(price)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval=interval,
        start_time=start,
        end_time=start + duration,
        open=value,
        high=value + Decimal("0.10"),
        low=value - Decimal("0.10"),
        close=value + Decimal("0.05"),
        volume=Decimal(volume),
        is_final=True,
        session="regular",
        provider=provider,
        received_at=start + duration + timedelta(seconds=1),
    )


def test_old_unresolved_gap_does_not_poison_independent_rolling_window() -> None:
    bars = [_bar(index) for index in range(10)]
    bars += [_bar(index) for index in range(30, 211)]  # 10:00 through 13:00 ET
    as_of = bars[-1].end_time

    gaps = detect_session_gaps(
        bars,
        session_date=SESSION_DATE,
        interval="1m",
        as_of=as_of,
    )
    assert len(gaps) == 1
    assert gaps[0].missing_bar_count == 20

    rolling = assess_data_requirement(
        bars,
        session_date=SESSION_DATE,
        as_of=as_of,
        requirement=StrategyDataRequirement(
            interval="1m",
            continuity="rolling",
            minimum_clean_bars=60,
        ),
    )
    assert rolling.evaluable is True
    assert rolling.status == "rolling_window_complete"
    assert rolling.reset_required is True
    assert rolling.clean_start == OPEN + timedelta(minutes=30)
    assert rolling.clean_bar_count == 181


def test_same_old_gap_still_blocks_session_anchored_calculation() -> None:
    bars = [_bar(index) for index in range(10)]
    bars += [_bar(index) for index in range(30, 211)]

    result = assess_data_requirement(
        bars,
        session_date=SESSION_DATE,
        as_of=bars[-1].end_time,
        requirement=StrategyDataRequirement(
            interval="1m",
            continuity="session",
            minimum_clean_bars=1,
        ),
    )

    assert result.evaluable is False
    assert result.status == "unresolved_dependency"
    assert "SESSION_DEPENDS_ON_UNRESOLVED_GAP" in result.reason_codes


def test_post_gap_window_must_warm_before_resuming() -> None:
    bars = [_bar(index) for index in range(10)]
    bars += [_bar(index) for index in range(30, 79)]

    result = assess_data_requirement(
        bars,
        session_date=SESSION_DATE,
        as_of=bars[-1].end_time,
        requirement=StrategyDataRequirement(
            interval="1m",
            continuity="rolling",
            minimum_clean_bars=50,
        ),
    )

    assert result.evaluable is False
    assert result.status == "post_gap_warming"
    assert result.clean_bar_count == 49
    assert result.reset_required is True


def test_fallback_fills_only_missing_bucket_and_primary_wins_duplicates() -> None:
    primary = [_bar(index) for index in range(5) if index != 2]
    fallback = [
        _bar(1, provider="alpaca_iex", price="99"),
        _bar(2, provider="alpaca_iex", price="10.2"),
    ]
    result = reconcile_recovery(
        instrument_id="equity:NASDAQ:TEST",
        interval="1m",
        session_date=SESSION_DATE,
        as_of=OPEN + timedelta(minutes=5),
        primary_bars=primary,
        fallback_bars=fallback,
        primary_provider="yahoo",
        fallback_provider="alpaca_iex",
        fallback_attempted=True,
        partial_market_fallback=True,
    )

    assert result.report.recovered_bar_count == 1
    assert result.report.unresolved_gaps == ()
    assert [bar.provider for bar in result.bars] == [
        "yahoo",
        "yahoo",
        "alpaca_iex",
        "yahoo",
        "yahoo",
    ]
    assert result.bars[1].open == Decimal("10")


def test_partial_market_price_can_be_opted_in_but_volume_stays_fail_closed() -> None:
    primary = [_bar(index) for index in range(5) if index != 2]
    fallback = [_bar(2, provider="alpaca_iex")]
    recovered = reconcile_recovery(
        instrument_id="equity:NASDAQ:TEST",
        interval="1m",
        session_date=SESSION_DATE,
        as_of=OPEN + timedelta(minutes=5),
        primary_bars=primary,
        fallback_bars=fallback,
        primary_provider="yahoo",
        fallback_provider="alpaca_iex",
        fallback_attempted=True,
        partial_market_fallback=True,
    )

    price_only = assess_data_requirement(
        recovered.bars,
        session_date=SESSION_DATE,
        as_of=OPEN + timedelta(minutes=5),
        recovery_report=recovered.report,
        requirement=StrategyDataRequirement(
            interval="1m",
            continuity="session",
            required_fields=("ohlc",),
            allow_partial_market_price=True,
        ),
    )
    assert price_only.evaluable is True
    assert price_only.status == "recovered_complete"

    volume_sensitive = assess_data_requirement(
        recovered.bars,
        session_date=SESSION_DATE,
        as_of=OPEN + timedelta(minutes=5),
        recovery_report=recovered.report,
        requirement=StrategyDataRequirement(
            interval="1m",
            continuity="session",
            required_fields=("ohlc", "volume"),
            allow_partial_market_price=True,
            allow_partial_market_volume=False,
        ),
    )
    assert volume_sensitive.evaluable is False
    assert "PARTIAL_MARKET_VOLUME_NOT_EQUIVALENT" in volume_sensitive.reason_codes


def test_lower_timeframe_reconstruction_requires_complete_single_provider_bucket() -> None:
    complete = [_bar(index, provider="alpaca_iex") for index in range(5)]
    bars = aggregate_complete_bars(
        complete,
        session_date=SESSION_DATE,
        target_interval="5m",
        as_of=OPEN + timedelta(minutes=5),
    )
    assert len(bars) == 1
    assert bars[0].interval == "5m"
    assert bars[0].provider == "alpaca_iex"
    assert bars[0].volume == Decimal("5000")

    incomplete = complete[:-1]
    assert aggregate_complete_bars(
        incomplete,
        session_date=SESSION_DATE,
        target_interval="5m",
        as_of=OPEN + timedelta(minutes=5),
    ) == []

    mixed = [*complete[:4], _bar(4, provider="yahoo")]
    with pytest.raises(ValueError, match="cannot mix providers"):
        aggregate_complete_bars(
            mixed,
            session_date=SESSION_DATE,
            target_interval="5m",
            as_of=OPEN + timedelta(minutes=5),
        )


class _Registry:
    def __init__(self) -> None:
        self.primary_calls = 0
        self.fallback_calls = 0
        self._primary = [_bar(index) for index in range(5) if index != 2]

    def provider(self, provider_id):
        return SimpleNamespace(provider_id=provider_id)

    def resolve_binding(self, instrument_id, binding_id=None):
        return SimpleNamespace(
            instrument_id=instrument_id,
            binding_id=binding_id or "yahoo:test",
            provider="yahoo",
        )

    def resolve_execution_binding(self, instrument_id, binding_id=None):
        return SimpleNamespace(
            instrument_id=instrument_id,
            binding_id="alpaca_iex:test",
            provider="alpaca_iex",
        )

    def bars(self, instrument_id, interval, limit, binding_id=None, cancellation=None):
        self.primary_calls += 1
        return SimpleNamespace(bars=list(self._primary))

    def execution_indicator_bars(self, instrument_id, binding_id=None, *, as_of, cancellation=None):
        self.fallback_calls += 1
        return [_bar(index, provider="alpaca_iex") for index in range(5)]


def test_service_uses_two_primary_attempts_plus_one_fallback_at_most() -> None:
    registry = _Registry()
    service = TradingMarketDataService(registry=registry)

    result = service.recovered_bars(
        "equity:NASDAQ:TEST",
        "1m",
        500,
        "yahoo:test",
        session_date=SESSION_DATE,
        as_of=OPEN + timedelta(minutes=5),
    )

    assert registry.primary_calls == 2
    assert registry.fallback_calls == 1
    assert result.report.primary_attempt_count == 2
    assert result.report.recovered_bar_count == 1
    assert result.report.unresolved_gaps == ()
    assert result.report.partial_market_fallback is True


ET = ZoneInfo("America/New_York")
COMPAT_INSTRUMENT = "equity:NASDAQ:COMPAT"


def _compat_bar(start_et: datetime, *, provider: str = "primary") -> MarketBar:
    start = start_et.astimezone(timezone.utc)
    return MarketBar(
        instrument_id=COMPAT_INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal("10"),
        high=Decimal("10.1"),
        low=Decimal("9.9"),
        close=Decimal("10"),
        volume=Decimal("100"),
        provider=provider,
        session="regular",
    )


def test_general_recovery_compat_fills_exact_gap_from_fallback_without_synthetic_bar():
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    primary = [_compat_bar(start), _compat_bar(start + timedelta(minutes=2))]
    fallback = [_compat_bar(start + timedelta(minutes=1), provider="fallback")]
    result = recover_market_bars(
        instrument_id=COMPAT_INSTRUMENT,
        interval="1m",
        session_date=start.date(),
        observed_at=start + timedelta(minutes=3, seconds=10),
        primary_fetch=lambda: primary,
        primary_source="primary",
        fallback_fetch=lambda: fallback,
        fallback_source="fallback",
    )
    assert result.status == "COMPLETE"
    assert len(result.bars) == 3
    assert result.unresolved_starts == ()
    assert set(result.source_providers) == {"primary", "fallback"}


def test_general_recovery_compat_keeps_unresolved_gap_partial():
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    result = recover_market_bars(
        instrument_id=COMPAT_INSTRUMENT,
        interval="1m",
        session_date=start.date(),
        observed_at=start + timedelta(minutes=3),
        primary_fetch=lambda: [_compat_bar(start)],
        primary_source="primary",
    )
    assert result.status == "PARTIAL"
    assert len(result.unresolved_starts) == 2


def test_latest_clean_bars_can_step_over_a_single_missing_bar():
    from datetime import date as _date, datetime as _datetime, timedelta as _timedelta, timezone as _timezone
    from decimal import Decimal as _Decimal

    from app.apps.trading.market_data_recovery import latest_clean_bars
    from app.apps.trading.models import MarketBar as _MarketBar

    session_open = _datetime(2026, 9, 17, 13, 30, tzinfo=_timezone.utc)

    def bar(index):
        start = session_open + _timedelta(minutes=5 * index)
        return _MarketBar(
            instrument_id="equity:NASDAQ:TEST", interval="5m", start_time=start, end_time=start + _timedelta(minutes=5),
            open=_Decimal("10"), high=_Decimal("10"), low=_Decimal("10"), close=_Decimal("10"), volume=_Decimal("1"),
            provider="test", session="regular",
        )

    # A two-bar hole after bar 2 and a one-bar hole after bar 6.
    bars = [bar(index) for index in (0, 1, 2, 5, 6, 8, 9)]
    as_of = session_open + _timedelta(minutes=50)
    kwargs = dict(session_date=_date(2026, 9, 17), interval="5m", as_of=as_of)

    assert [item.start_time for item in latest_clean_bars(bars, **kwargs)] == [bar(8).start_time, bar(9).start_time]
    bridged = latest_clean_bars(bars, bridge_single_missing=True, **kwargs)
    assert [item.start_time for item in bridged] == [bar(index).start_time for index in (5, 6, 8, 9)]
