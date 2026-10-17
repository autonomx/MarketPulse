from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.historical_gapper_reconstruction import HistoricalUniverseReconstruction
from app.apps.trading.strategies.models import GapPullbackConfig, StrategyRiskProfile
from app.apps.trading.strategy_range_backtest import (
    StrategyRangeBacktestRequest,
    choose_causal_universe,
    run_strategy_range_backtest,
)
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument


def candidate(symbol: str = "ABC", observed_at: datetime | None = None) -> GapperCandidate:
    return GapperCandidate(
        instrument_id=f"equity:NASDAQ:{symbol}",
        observed_at=observed_at,
        previous_close=Decimal("8"),
        premarket_price=Decimal("10.4"),
        gap_pct=Decimal("30"),
        premarket_volume=Decimal("1000000"),
        premarket_dollar_volume=Decimal("10400000"),
        tod_rvol=Decimal("6"),
        float_shares=Decimal("5000000"),
        spread_bps=Decimal("40"),
        discovery_rank=1,
    )


def universe(universe_id: str, observed: datetime):
    return freeze_gapper_universe(
        universe_id=universe_id,
        session_date=observed.astimezone(timezone.utc).date(),
        evaluation_time=observed,
        discovery_source="provider",
        candidates=[candidate(observed_at=observed)],
    )


def strategy() -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id="range-test",
        account_id="paper-1",
        strategy_version="1.1.0",
        mode="off",
        config=GapPullbackConfig(
            strategy_version="1.1.0",
            universe_scan_time_et=time(9, 20),
            universe_archive_grace_minutes=10,
            entry_start_et=time(9, 35),
        ),
        risk=StrategyRiskProfile(),
    )


def test_choose_causal_universe_uses_latest_snapshot_before_scan_time_and_excludes_selected() -> None:
    early = universe("gappers-early", datetime(2026, 8, 18, 13, 10, tzinfo=timezone.utc))
    research = universe("gappers-early-research-091900", datetime(2026, 8, 18, 13, 19, tzinfo=timezone.utc))
    selected = universe("gappers-early-selected-092000", datetime(2026, 8, 18, 13, 20, tzinfo=timezone.utc))
    late = universe("gappers-late", datetime(2026, 8, 18, 13, 40, tzinfo=timezone.utc))

    chosen = choose_causal_universe(
        [early, research, selected, late],
        session_date=date(2026, 8, 18),
        cutoff_time=time(9, 20),
    )

    assert chosen is not None
    assert chosen.universe_id == research.universe_id


def test_choose_causal_universe_accepts_archive_inside_configured_grace_window() -> None:
    archive = universe("auto-archive-2026-08-18-0920-test", datetime(2026, 8, 18, 13, 24, tzinfo=timezone.utc))

    assert choose_causal_universe(
        [archive],
        session_date=date(2026, 8, 18),
        cutoff_time=time(9, 20),
        grace_minutes=10,
    ) is archive
    assert choose_causal_universe(
        [archive],
        session_date=date(2026, 8, 18),
        cutoff_time=time(9, 20),
        grace_minutes=0,
    ) is None


def test_range_backtest_reports_missing_universes_as_unavailable_not_zero_performance() -> None:
    request = StrategyRangeBacktestRequest(
        start_date=date(2026, 8, 17),
        end_date=date(2026, 8, 18),
        initial_cash=Decimal("100000"),
        universe_mode="captured_only",
    )

    progress: list[tuple[int, int, date]] = []
    result = run_strategy_range_backtest(
        strategy(),
        [],
        request,
        progress_callback=lambda completed, total, session_date: progress.append(
            (completed, total, session_date)
        ),
    )

    assert result.requested_trading_sessions == 2
    assert result.covered_sessions == 0
    assert result.missing_universe_sessions == 2
    assert result.trade_count == 0
    assert result.ending_cash == Decimal("100000")
    assert result.pnl is None
    assert result.return_pct is None
    assert result.expectancy_r is None
    assert result.result_quality == "unavailable"
    assert all(day.status == "missing_universe" for day in result.days)
    assert result.point_in_time_universes_required is True
    assert [(completed, total) for completed, total, _ in progress] == [(1, 2), (2, 2)]


def test_reconstructed_no_candidate_day_counts_as_covered_approximate_session() -> None:
    request = StrategyRangeBacktestRequest(
        start_date=date(2026, 8, 18),
        end_date=date(2026, 8, 18),
        initial_cash=Decimal("100000"),
        universe_mode="captured_or_reconstructed",
    )

    def no_candidates(**kwargs):
        assert kwargs["scan_time"] == time(9, 20)
        return HistoricalUniverseReconstruction(
            snapshot=None,
            fidelity="reconstructed_current_listings_iex",
            warnings=("approximate",),
            candidate_seed_count=10,
            active_asset_count=5000,
            detail="Historical scan completed but no candidate qualified.",
        )

    result = run_strategy_range_backtest(strategy(), [], request, reconstructor=no_candidates)

    assert result.covered_sessions == 1
    assert result.reconstructed_sessions == 1
    assert result.no_candidate_sessions == 1
    assert result.result_quality == "approximate"
    assert result.pnl == Decimal("0")
    assert result.return_pct == Decimal("0")
    assert result.expectancy_r is None
    assert result.days[0].status == "no_candidates"
    assert result.days[0].universe_origin == "reconstructed"


def test_scan_time_is_separate_from_entry_start_and_legacy_cutoff_alias_conflicts() -> None:
    request = StrategyRangeBacktestRequest(
        start_date=date(2026, 8, 18),
        end_date=date(2026, 8, 18),
        universe_mode="captured_only",
    )
    result = run_strategy_range_backtest(strategy(), [], request)
    assert result.universe_scan_time_et == time(9, 20)
    assert result.universe_cutoff_et == time(9, 20)
