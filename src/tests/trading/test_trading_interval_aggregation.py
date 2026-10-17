from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.catalog import INSTRUMENTS, default_binding, instrument_by_id
from app.apps.trading.models import BarsResponse, DatasetProvenance, MarketBar
from app.apps.trading.providers.aggregation import (
    aggregate_market_bars,
    aggregation_plan,
)
from app.apps.trading.providers.registry import ProviderRegistry


def make_bar(index: int, *, session: str = "24x7") -> MarketBar:
    start = datetime(2026, 8, 13, tzinfo=timezone.utc) + timedelta(hours=index)
    return MarketBar(
        instrument_id=INSTRUMENTS[0].instrument_id,
        interval="1h",
        start_time=start,
        end_time=start + timedelta(hours=1),
        open=Decimal(str(100 + index)),
        high=Decimal(str(105 + index)),
        low=Decimal(str(95 + index)),
        close=Decimal(str(104 + index)),
        volume=Decimal("10"),
        is_final=True,
        session=session,
        provider="binance",
        provider_event_id=f"bar-{index}",
        received_at=start + timedelta(hours=1),
    )


def test_aggregation_plan_chooses_the_closest_supported_base() -> None:
    assert aggregation_plan("2h", ("5m", "1h", "1d")) == ("1h", 2)
    assert aggregation_plan("45m", ("5m", "15m", "1h")) == ("15m", 3)
    assert aggregation_plan("1s", ("1m", "5m")) is None


def test_aggregate_market_bars_preserves_ohlcv_semantics() -> None:
    result = aggregate_market_bars(
        [make_bar(index) for index in range(4)],
        target_interval="2h",
        base_interval="1h",
        factor=2,
    )

    assert len(result) == 2
    assert result[0].interval == "2h"
    assert result[0].open == Decimal("100")
    assert result[0].high == Decimal("106")
    assert result[0].low == Decimal("95")
    assert result[0].close == Decimal("105")
    assert result[0].volume == Decimal("20")
    assert result[0].end_time - result[0].start_time == timedelta(hours=2)


def test_equity_intraday_aggregation_does_not_cross_sessions() -> None:
    first = make_bar(0, session="regular")
    second_day = make_bar(24, session="regular")
    result = aggregate_market_bars(
        [first, second_day],
        target_interval="2h",
        base_interval="1h",
        factor=2,
    )

    assert len(result) == 1
    assert result[0].start_time == second_day.start_time


def test_registry_fetches_a_supported_base_for_a_derived_interval() -> None:
    instrument_id = INSTRUMENTS[0].instrument_id
    binding = default_binding(instrument_id)
    instrument = instrument_by_id(instrument_id)
    assert binding is not None
    assert instrument is not None
    calls: list[tuple[str, int]] = []

    class FixtureProvider:
        provider_id = "binance"

        def get_bars(self, requested_id: str, interval: str, limit: int = 500) -> BarsResponse:
            calls.append((interval, limit))
            base_bars = [make_bar(index) for index in range(7)]
            base_bars = [bar.model_copy(update={"instrument_id": requested_id}) for bar in base_bars]
            now = datetime.now(timezone.utc)
            return BarsResponse(
                instrument=instrument,
                binding=binding,
                provenance=DatasetProvenance(
                    instrument_id=requested_id,
                    requested_binding=binding.binding_id,
                    resolved_binding=binding.binding_id,
                    dataset_fingerprint="fixture-base",
                    freshness_mode="polled",
                    as_of=base_bars[-1].end_time,
                    received_at=now,
                    history_complete=False,
                ),
                interval=interval,
                bars=base_bars,
            )

    result = ProviderRegistry(factories={"binance": lambda: FixtureProvider()}).bars(
        instrument_id,
        "7h",
        1,
        binding.binding_id,
    )

    assert calls == [("1h", 13)]
    assert result.interval == "7h"
    assert len(result.bars) == 1
    assert result.bars[0].volume == Decimal("70")
