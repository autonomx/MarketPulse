from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.apps.trading.strategy_evolving_top_gainers import (
    EvolvingTopGainersConfig,
    TopGainerObservation,
)
from app.apps.trading.strategy_evolving_top_gainers_research import (
    HistoricalPopulationManifest,
    assess_population_integrity,
    leaderboard_trajectory_at,
    replay_evolving_top_gainers_strict,
)


SESSION = date(2026, 9, 9)


def _obs(symbol: str, minute: int, price: str, *, previous: str = "10") -> TopGainerObservation:
    return TopGainerObservation(
        instrument_id=f"equity:US:{symbol}",
        observed_at=datetime(2026, 9, 9, 13, minute, tzinfo=timezone.utc),
        price=Decimal(price),
        previous_close=Decimal(previous),
        cumulative_dollar_volume=Decimal("1000000"),
    )


def _manifest(*symbols: str, point_in_time: bool = True) -> HistoricalPopulationManifest:
    return HistoricalPopulationManifest(
        session_date=SESSION,
        authority="test-point-in-time-listings",
        instrument_ids=tuple(f"equity:US:{symbol}" for symbol in symbols),
        point_in_time=point_in_time,
        captured_as_of=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
    )


def test_strict_replay_rejects_present_day_population_proxy() -> None:
    manifest = _manifest("AAA", "BBB", point_in_time=False)
    with pytest.raises(ValueError, match="population_not_point_in_time"):
        replay_evolving_top_gainers_strict(
            manifest=manifest,
            observations=[_obs("AAA", 35, "12")],
        )


def test_strict_replay_rejects_observation_outside_frozen_population() -> None:
    manifest = _manifest("AAA")
    with pytest.raises(ValueError, match="observations_outside_population"):
        replay_evolving_top_gainers_strict(
            manifest=manifest,
            observations=[_obs("AAA", 35, "12"), _obs("BBB", 35, "13")],
        )


def test_valid_point_in_time_population_produces_integrity_evidence() -> None:
    observations = [_obs("AAA", 35, "12"), _obs("BBB", 35, "11")]
    replay, report = replay_evolving_top_gainers_strict(
        manifest=_manifest("AAA", "BBB"),
        observations=observations,
        config=EvolvingTopGainersConfig(top_n=5),
    )

    assert report.valid_for_inference is True
    assert report.population_size == 2
    assert report.leaderboard_symbol_count == 2
    assert report.reason_codes == ()
    assert set(replay.union_instrument_ids) == {"equity:US:AAA", "equity:US:BBB"}


def test_integrity_report_detects_outcome_conditioned_population() -> None:
    manifest = HistoricalPopulationManifest(
        session_date=SESSION,
        authority="winner-list",
        instrument_ids=("equity:US:AAA",),
        point_in_time=True,
        outcome_conditioned=True,
    )
    report = assess_population_integrity(
        manifest=manifest,
        observations=[_obs("AAA", 35, "12")],
    )
    assert report.valid_for_inference is False
    assert "population_outcome_conditioned" in report.reason_codes


def test_trajectory_features_are_causal_and_measure_rank_acceleration() -> None:
    # Five-symbol snapshots at 09:35, 09:40, 09:45 and 09:50 ET.  TARGET
    # improves from rank 5 -> 3 -> 1, then a future 09:50 snapshot drops it.
    observations = [
        _obs("A", 35, "15"), _obs("B", 35, "14"), _obs("C", 35, "13"),
        _obs("D", 35, "12"), _obs("TARGET", 35, "11"),
        _obs("TARGET", 40, "13.5"),
        _obs("TARGET", 45, "16"),
        _obs("TARGET", 50, "10.5"),
    ]
    replay, _ = replay_evolving_top_gainers_strict(
        manifest=_manifest("A", "B", "C", "D", "TARGET"),
        observations=observations,
        config=EvolvingTopGainersConfig(top_n=5, cadence_minutes=5),
    )

    features = leaderboard_trajectory_at(
        replay,
        instrument_id="equity:US:TARGET",
        observed_at=datetime(2026, 9, 9, 13, 45, tzinfo=timezone.utc),
    )

    assert features.current_rank == 1
    assert features.best_rank_so_far == 1
    assert features.rank_improvement_5m == 2
    assert features.current_gain_pct == Decimal("60.0")
    assert features.gain_change_5m_pct_points == Decimal("25.00")
    assert features.minutes_since_first_top_n == Decimal("10.0")
    # The 09:50 deterioration is in the future and must not affect the feature.
    assert features.currently_top_n is True


def test_trajectory_reports_dropout_at_signal_time_without_erasing_history() -> None:
    observations = [
        _obs("A", 35, "15"), _obs("B", 35, "14"), _obs("C", 35, "13"),
        _obs("D", 35, "12"), _obs("TARGET", 35, "11"),
        _obs("TARGET", 40, "9"),
    ]
    replay, _ = replay_evolving_top_gainers_strict(
        manifest=_manifest("A", "B", "C", "D", "TARGET"),
        observations=observations,
        config=EvolvingTopGainersConfig(top_n=5, cadence_minutes=5, minimum_gain_pct=Decimal("0")),
    )
    features = leaderboard_trajectory_at(
        replay,
        instrument_id="equity:US:TARGET",
        observed_at=datetime(2026, 9, 9, 13, 40, tzinfo=timezone.utc),
    )
    assert features.currently_top_n is False
    assert features.current_rank is None
    assert features.best_rank_so_far == 5
