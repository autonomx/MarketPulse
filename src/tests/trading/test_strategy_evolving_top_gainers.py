from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from app.apps.trading import strategy_evolving_top_gainers as evolving
from app.apps.trading.strategy_leader_momentum_continuation import LeaderMomentumSnapshot


SESSION = date(2026, 9, 9)


def _obs(symbol: str, minute: int, price: str, previous: str = "10", dollar: str = "1000000"):
    return evolving.TopGainerObservation(
        instrument_id=f"equity:US:{symbol}",
        observed_at=datetime(2026, 9, 9, 13, minute, tzinfo=timezone.utc),
        price=Decimal(price),
        previous_close=Decimal(previous),
        cumulative_dollar_volume=Decimal(dollar),
    )


def test_evolving_ranking_admits_late_runner_and_records_reentry():
    config = evolving.EvolvingTopGainersConfig(top_n=5)
    observations = [
        _obs("AAA", 35, "12"),
        _obs("BBB", 35, "11.5"),
        _obs("CCC", 35, "11"),
        _obs("DDD", 35, "10.8"),
        _obs("EEE", 35, "10.5"),
        _obs("FFF", 35, "10.1"),
        # FFF becomes the leader later; it must be admitted at 09:40 ET even
        # though it was not in the first Top-5 snapshot.
        _obs("FFF", 40, "14"),
        # FFF falls out, then re-enters.
        _obs("FFF", 45, "9.9"),
        _obs("FFF", 50, "15"),
    ]

    result = evolving.replay_evolving_top_gainers(
        session_date=SESSION,
        observations=observations,
        config=config,
    )

    fff = result.membership_for("equity:US:FFF")
    assert fff is not None
    assert fff.first_top_n_at == datetime(2026, 9, 9, 13, 40, tzinfo=timezone.utc)
    assert fff.first_top_5_at == fff.first_top_n_at
    assert fff.best_rank == 1
    assert fff.entry_count == 2
    assert "equity:US:FFF" in result.union_instrument_ids
    assert any(
        item.instrument_id == "equity:US:FFF" and item.kind == "reentered"
        for item in result.transitions
    )


def test_final_winner_is_not_injected_without_causal_observation():
    result = evolving.replay_evolving_top_gainers(
        session_date=SESSION,
        observations=[_obs("AAA", 35, "12"), _obs("BBB", 35, "11")],
    )

    assert result.membership_for("equity:US:FUTURE_WINNER") is None
    assert "equity:US:FUTURE_WINNER" not in result.union_instrument_ids


def test_top_20_top_10_top_5_timestamps_are_recorded_independently():
    config = evolving.EvolvingTopGainersConfig(top_n=20)
    observations = []
    for index in range(1, 21):
        observations.append(
            _obs(f"S{index:02d}", 35, str(10 + Decimal(21 - index) / Decimal("10")))
        )
    # S20 begins at rank 20, moves to rank 8, then rank 2.
    observations.extend(
        [
            _obs("S20", 40, "11.35"),
            _obs("S20", 45, "13"),
        ]
    )

    result = evolving.replay_evolving_top_gainers(
        session_date=SESSION,
        observations=observations,
        config=config,
    )
    summary = result.membership_for("equity:US:S20")
    assert summary is not None
    assert summary.first_top_n_at == datetime(2026, 9, 9, 13, 35, tzinfo=timezone.utc)
    assert summary.first_top_10_at == datetime(2026, 9, 9, 13, 40, tzinfo=timezone.utc)
    assert summary.first_top_5_at == datetime(2026, 9, 9, 13, 45, tzinfo=timezone.utc)
    assert summary.best_rank == 1


def test_rank_ties_are_deterministic_and_use_dollar_volume_then_symbol():
    result = evolving.replay_evolving_top_gainers(
        session_date=SESSION,
        observations=[
            _obs("BBB", 35, "12", dollar="2000000"),
            _obs("AAA", 35, "12", dollar="2000000"),
            _obs("CCC", 35, "12", dollar="3000000"),
        ],
    )

    members = result.snapshots[0].members
    assert [row.instrument_id for row in members] == [
        "equity:US:CCC",
        "equity:US:AAA",
        "equity:US:BBB",
    ]


def test_replay_rejects_observation_from_another_exchange_session():
    with pytest.raises(ValueError, match="outside_exchange_session"):
        evolving.replay_evolving_top_gainers(
            session_date=SESSION,
            observations=[
                evolving.TopGainerObservation(
                    instrument_id="equity:US:AAA",
                    observed_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc),
                    price=Decimal("12"),
                    previous_close=Decimal("10"),
                )
            ],
        )


def test_leader_momentum_wrapper_delays_trade_authority_until_discovery(monkeypatch):
    captured = {}

    def fake_evaluate(bars, *, context, entry_start_et, last_entry_et, force_flat_et):
        captured["entry_start_et"] = entry_start_et
        return LeaderMomentumSnapshot(
            state="waiting_leader",
            reason_code="LEADER_MOMENTUM_LEADER_NOT_CONFIRMED",
        )

    monkeypatch.setattr(
        evolving.leader,
        "evaluate_leader_momentum_continuation",
        fake_evaluate,
    )
    discovered_at = datetime(2026, 9, 9, 15, 7, tzinfo=timezone.utc)  # 11:07 ET

    evolving.evaluate_leader_momentum_after_discovery(
        [],
        discovered_at=discovered_at,
    )

    assert captured["entry_start_et"] == time(11, 7)


def test_leader_momentum_wrapper_never_opens_before_normal_entry_window(monkeypatch):
    captured = {}

    def fake_evaluate(bars, *, context, entry_start_et, last_entry_et, force_flat_et):
        captured["entry_start_et"] = entry_start_et
        return LeaderMomentumSnapshot(
            state="waiting_leader",
            reason_code="LEADER_MOMENTUM_LEADER_NOT_CONFIRMED",
        )

    monkeypatch.setattr(
        evolving.leader,
        "evaluate_leader_momentum_continuation",
        fake_evaluate,
    )

    evolving.evaluate_leader_momentum_after_discovery(
        [],
        discovered_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),  # 08:00 ET
    )

    assert captured["entry_start_et"] == time(9, 35)
