from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.backtest import (
    BACKTEST_MARK_TO_MARKET_POLICY,
    BacktestRequest,
    MovingAverageCrossStrategy,
    run_backtest,
)
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
from app.apps.trading.replay import freeze_bars_response


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
INSTRUMENT = "crypto:FIXTURE:spot:ECON-USD"


def _snapshot():
    closes = [10, 10, 10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 9, 10, 11]
    instrument = CanonicalInstrument(
        instrument_id=INSTRUMENT,
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.SPOT,
        venue="FIXTURE",
        venue_symbol="ECON-USD",
        display_symbol="ECONUSD",
        base_currency="ECON",
        quote_currency="USD",
        exchange_timezone="UTC",
        session_calendar="24x7",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
    )
    binding = ProviderBinding(
        binding_id="fixture:econ",
        instrument_id=INSTRUMENT,
        provider="fixture",
        provider_symbol="ECON-USD",
        feed_type=FeedType.HISTORICAL_POLLING,
        realtime_scope="fixture history",
        supported_intervals=("1d",),
        usage_scope=UsageScope.PERSONAL_LOCAL,
        is_official_api=True,
    )
    bars = []
    for index, close in enumerate(closes):
        start = NOW + timedelta(days=index)
        bars.append(
            MarketBar(
                instrument_id=INSTRUMENT,
                interval="1d",
                start_time=start,
                end_time=start + timedelta(days=1),
                open=Decimal(close),
                high=Decimal(close + 1),
                low=Decimal(close - 1),
                close=Decimal(close),
                volume=Decimal(1000 + index),
                is_final=True,
                provider="fixture",
                received_at=start + timedelta(days=1),
            )
        )
    response = BarsResponse(
        instrument=instrument,
        binding=binding,
        provenance=DatasetProvenance(
            instrument_id=INSTRUMENT,
            requested_binding=binding.binding_id,
            resolved_binding=binding.binding_id,
            dataset_fingerprint="provider-economic-fixture",
            freshness_mode="polled",
            as_of=bars[-1].end_time,
            received_at=bars[-1].received_at,
            cached=False,
            history_complete=True,
        ),
        interval="1d",
        bars=bars,
    )
    return freeze_bars_response(
        dataset_id="dataset-economic",
        response=response,
        requested_binding_id=binding.binding_id,
        gap_policy="fail",
    )


def _request():
    return BacktestRequest(
        strategy=MovingAverageCrossStrategy(fast_period=2, slow_period=3),
        execution_policy={
            "fill_timing": "next_bar_open",
            "commission_bps": "10",
            "slippage_bps": "5",
            "position_size_fraction": "1",
            "allow_short": False,
            "use_finalized_bars_only": True,
        },
        initial_cash=Decimal("10000"),
    )


def test_backtest_discloses_ending_position_and_mark_to_market_economics() -> None:
    result = run_backtest(_snapshot(), _request(), run_id="economics-1", now=NOW)
    assert result.status == "completed"
    assert result.ending_cash == result.equity_curve[-1].cash
    assert result.ending_position == result.equity_curve[-1].position
    assert result.ending_mark_price == _snapshot().bars[-1].close
    assert result.mark_to_market_policy == BACKTEST_MARK_TO_MARKET_POLICY
    assert result.realized_pnl + result.unrealized_pnl == result.final_equity - result.initial_cash
    assert len(result.economic_result_fingerprint) == 64


def test_economic_fingerprint_ignores_run_identity_and_wall_clock_timestamps() -> None:
    first = run_backtest(_snapshot(), _request(), run_id="economics-a", now=NOW)
    second = run_backtest(
        _snapshot(),
        _request(),
        run_id="economics-b",
        now=NOW + timedelta(hours=7),
    )
    assert first.run_id != second.run_id
    assert first.started_at != second.started_at
    assert first.economic_result_fingerprint == second.economic_result_fingerprint


def test_economic_fingerprint_changes_when_execution_economics_change() -> None:
    baseline = run_backtest(_snapshot(), _request(), run_id="economics-a", now=NOW)
    changed = _request().model_copy(
        update={
            "execution_policy": _request().execution_policy.model_copy(
                update={"commission_bps": Decimal("20")}
            )
        }
    )
    other = run_backtest(_snapshot(), changed, run_id="economics-b", now=NOW)
    assert baseline.economic_result_fingerprint != other.economic_result_fingerprint
