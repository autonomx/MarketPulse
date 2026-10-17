from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.market_data_recovery import (
    StrategyDataRequirement,
    reconcile_recovery,
)
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_session_evidence import _CurrentSessionMarketDataProxy


OPEN = datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc)  # 09:30 ET
SESSION_DATE = OPEN.date()


def _bar(minute: int, *, interval: str = "1m", provider: str = "yahoo") -> MarketBar:
    start = OPEN + timedelta(minutes=minute)
    width = int(interval[:-1])
    price = Decimal("10") + Decimal(minute) / Decimal("100")
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval=interval,
        start_time=start,
        end_time=start + timedelta(minutes=width),
        open=price,
        high=price + Decimal("0.10"),
        low=price - Decimal("0.10"),
        close=price + Decimal("0.05"),
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider=provider,
        received_at=start + timedelta(minutes=width, seconds=1),
    )


class _RecoveredDelegate:
    def __init__(self, bars, *, interval: str, as_of: datetime) -> None:
        self._bars = list(bars)
        self._interval = interval
        self._as_of = as_of

    def recovered_bars(
        self,
        instrument_id,
        interval,
        limit=500,
        binding_id=None,
        *,
        session_date,
        as_of,
        max_primary_attempts=2,
        cancellation=None,
    ):
        assert interval == self._interval
        return reconcile_recovery(
            instrument_id=instrument_id,
            interval=interval,
            session_date=session_date,
            as_of=as_of,
            primary_bars=self._bars,
            primary_provider="yahoo",
            requested_binding=binding_id or "yahoo:test",
            resolved_binding=binding_id or "yahoo:test",
            primary_attempt_count=1,
            primary_response=SimpleNamespace(bars=list(self._bars), provenance=None),
        )


def test_default_shadow_bars_fail_closed_for_gappy_five_minute_tape() -> None:
    # This is intentionally 5m: the default guard must no longer assume a
    # coarser timeframe is safe merely because it is not 1m.
    bars = [_bar(index * 5, interval="5m") for index in range(3)]
    bars += [_bar(index * 5, interval="5m") for index in range(5, 9)]
    as_of = bars[-1].end_time
    proxy = _CurrentSessionMarketDataProxy(
        _RecoveredDelegate(bars, interval="5m", as_of=as_of),
        session_date=SESSION_DATE,
        observed_at=as_of,
    )

    response = proxy.bars(
        "equity:NASDAQ:TEST",
        "5m",
        500,
        "yahoo:test",
    )

    assert response.bars == []


def test_explicit_rolling_requirement_can_resume_on_clean_suffix() -> None:
    bars = [_bar(index, interval="1m") for index in range(10)]
    bars += [_bar(index, interval="1m") for index in range(30, 100)]
    as_of = bars[-1].end_time
    proxy = _CurrentSessionMarketDataProxy(
        _RecoveredDelegate(bars, interval="1m", as_of=as_of),
        session_date=SESSION_DATE,
        observed_at=as_of,
    )

    response = proxy.bars_for_requirement(
        "equity:NASDAQ:TEST",
        "1m",
        500,
        "yahoo:test",
        requirement=StrategyDataRequirement(
            interval="1m",
            continuity="rolling",
            minimum_clean_bars=60,
            required_fields=("ohlc",),
        ),
    )

    assert len(response.bars) == 70
    assert response.bars[0].start_time == OPEN + timedelta(minutes=30)
    assert response.bars[-1].start_time == OPEN + timedelta(minutes=99)


def test_explicit_rolling_requirement_stays_blocked_during_warmup() -> None:
    bars = [_bar(index, interval="1m") for index in range(10)]
    bars += [_bar(index, interval="1m") for index in range(30, 79)]
    as_of = bars[-1].end_time
    proxy = _CurrentSessionMarketDataProxy(
        _RecoveredDelegate(bars, interval="1m", as_of=as_of),
        session_date=SESSION_DATE,
        observed_at=as_of,
    )

    response = proxy.bars_for_requirement(
        "equity:NASDAQ:TEST",
        "1m",
        500,
        "yahoo:test",
        requirement=StrategyDataRequirement(
            interval="1m",
            continuity="rolling",
            minimum_clean_bars=50,
            required_fields=("ohlc",),
        ),
    )

    assert response.bars == []


def test_requirement_interval_must_match_requested_interval() -> None:
    bars = [_bar(index, interval="1m") for index in range(60)]
    as_of = bars[-1].end_time
    proxy = _CurrentSessionMarketDataProxy(
        _RecoveredDelegate(bars, interval="1m", as_of=as_of),
        session_date=SESSION_DATE,
        observed_at=as_of,
    )

    try:
        proxy.bars_for_requirement(
            "equity:NASDAQ:TEST",
            "1m",
            500,
            "yahoo:test",
            requirement=StrategyDataRequirement(
                interval="5m",
                continuity="rolling",
                minimum_clean_bars=10,
            ),
        )
    except ValueError as exc:
        assert "interval must match" in str(exc)
    else:
        raise AssertionError("mismatched requirement interval should fail closed")
