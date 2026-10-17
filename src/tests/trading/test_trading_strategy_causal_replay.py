from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_causal_replay import recover_causal_1m_bars
from app.apps.trading.strategy_repository import StrategyEvent


INSTRUMENT = "equity:NASDAQ:TEST"
OPEN = datetime(2026, 9, 2, 13, 30, tzinfo=timezone.utc)


def _bar(index: int) -> MarketBar:
    start = OPEN + timedelta(minutes=index)
    price = Decimal("10") + Decimal(index) / Decimal("10")
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=price,
        high=price + Decimal("0.10"),
        low=price - Decimal("0.10"),
        close=price,
        volume=Decimal("100000"),
        provider="fixture",
        session="regular",
    )


def _event(index: int, bars: list[MarketBar]) -> StrategyEvent:
    observed_at = bars[-1].end_time
    return StrategyEvent(
        strategy_id="strategy-1",
        event_id=f"event-{index}",
        run_id="run-1",
        instrument_id=INSTRUMENT,
        event_type="state",
        state="qualified_gap",
        reason_code="WAITING",
        observed_at=observed_at,
        idempotency_key=f"idem-{index}",
        payload={
            "causal_1m_bar_window": [bar.model_dump(mode="json") for bar in bars],
            "latest_execution_bar": bars[-1].model_dump(mode="json"),
        },
    )


def test_recover_causal_archive_deduplicates_overlapping_windows():
    bars = [_bar(index) for index in range(5)]
    events = [
        _event(1, bars[:3]),
        _event(2, bars[1:4]),
        _event(3, bars[2:5]),
    ]

    archive = recover_causal_1m_bars(events, instrument_id=INSTRUMENT)

    assert archive.complete is True
    assert len(archive.bars) == 5
    assert archive.missing_intervals == ()
    assert [bar.start_time for bar in archive.bars] == [bar.start_time for bar in bars]


def test_recover_causal_archive_detects_missing_minutes():
    bars = [_bar(0), _bar(1), _bar(3)]

    archive = recover_causal_1m_bars(
        [_event(1, bars)],
        instrument_id=INSTRUMENT,
    )

    assert archive.complete is False
    assert len(archive.missing_intervals) == 1
    assert archive.bars[-1].start_time == OPEN + timedelta(minutes=3)


def test_recover_causal_archive_falls_back_to_legacy_latest_bar_payload():
    bar = _bar(0)
    event = StrategyEvent(
        strategy_id="strategy-1",
        event_id="legacy",
        instrument_id=INSTRUMENT,
        event_type="state",
        state="qualified_gap",
        observed_at=bar.end_time,
        idempotency_key="legacy-idem",
        payload={"latest_execution_bar": bar.model_dump(mode="json")},
    )

    archive = recover_causal_1m_bars([event], instrument_id=INSTRUMENT)

    assert archive.complete is True
    assert archive.bars == (bar,)
