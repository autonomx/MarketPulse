from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from app.apps.trading.models import MarketBar
from app.apps.trading import strategy_leader_momentum_research as research


START = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)


def _bars(count: int = 26) -> list[MarketBar]:
    values: list[MarketBar] = []
    for index in range(count):
        open_value = Decimal("10") + Decimal(index) * Decimal("0.10")
        close = open_value + Decimal("0.08")
        values.append(
            MarketBar(
                instrument_id="equity:US:TEST",
                interval="3m",
                start_time=START + timedelta(minutes=3 * index),
                end_time=START + timedelta(minutes=3 * (index + 1)),
                open=open_value,
                high=close + Decimal("0.01"),
                low=open_value - Decimal("0.01"),
                close=close,
                volume=Decimal("100000"),
                is_final=True,
                session="regular",
                provider="test",
            )
        )
    return values


def _install_deterministic_signals(monkeypatch, bars: list[MarketBar]) -> None:
    monkeypatch.setattr(
        research.leader,
        "resample_final_bars",
        lambda values, interval: list(values),
    )
    monkeypatch.setattr(
        research.leader,
        "_leader_score",
        lambda *_args, **_kwargs: Decimal("90"),
    )

    def setup(_regular, _sampled, _ema9, _atr14, *, index):
        if index == 9:
            return "momentum_compression", Decimal("10"), Decimal("2"), Decimal("0.20")
        if index >= 16:
            return "controlled_pullback", Decimal("10"), Decimal("2"), Decimal("0.20")
        return None

    monkeypatch.setattr(research.leader, "_setup_at", setup)

    def trade(sampled, _ema9, _atr14, *, signal_index, mode, stop_reference, entry_atr, force_flat_et):
        del stop_reference, entry_atr, force_flat_et
        entry = sampled[signal_index + 1]
        return research.leader.LeaderMomentumTrade(
            mode=mode,
            signal_time=sampled[signal_index].end_time,
            entry_time=entry.start_time,
            entry_price=entry.open,
            initial_stop_price=entry.open - Decimal("0.50"),
            exit_time=entry.end_time,
            exit_price=entry.close,
            exit_reason_code="LEADER_MOMENTUM_STRUCTURE_BREAK",
            return_pct=Decimal("-5") if mode == "momentum_compression" else Decimal("10"),
            mfe_pct=Decimal("12"),
            mae_pct=Decimal("-4"),
        )

    monkeypatch.setattr(research.leader, "_trade_from_signal", trade)


def test_research_baseline_reproduces_frozen_evaluator(monkeypatch) -> None:
    bars = _bars()
    _install_deterministic_signals(monkeypatch, bars)

    research.assert_baseline_parity(bars)


def test_controlled_pullback_only_ignores_mode_b_and_waits_for_mode_a(monkeypatch) -> None:
    bars = _bars()
    _install_deterministic_signals(monkeypatch, bars)

    result = research.evaluate_leader_momentum_research_variant(
        bars,
        variant=research.CONTROLLED_PULLBACK_ONLY,
    )

    assert result.snapshot.trades
    assert all(trade.mode == "controlled_pullback" for trade in result.snapshot.trades)
    assert result.snapshot.trades[0].signal_time == bars[16].end_time
    assert result.execution_authority is False


def test_single_trade_ablation_stops_before_reentry(monkeypatch) -> None:
    bars = _bars()
    _install_deterministic_signals(monkeypatch, bars)

    baseline = research.evaluate_leader_momentum_research_variant(
        bars,
        variant=research.BASELINE_V1_2,
    ).snapshot
    single = research.evaluate_leader_momentum_research_variant(
        bars,
        variant=research.SINGLE_TRADE_ONLY,
    ).snapshot

    assert len(baseline.trades) == 2
    assert len(single.trades) == 1
    assert single.trades[0] == baseline.trades[0]


def test_combined_ablation_is_mode_a_and_one_trade(monkeypatch) -> None:
    bars = _bars()
    _install_deterministic_signals(monkeypatch, bars)

    snapshot = research.evaluate_leader_momentum_research_variant(
        bars,
        variant=research.CONTROLLED_PULLBACK_SINGLE_TRADE,
    ).snapshot

    assert len(snapshot.trades) == 1
    assert snapshot.trades[0].mode == "controlled_pullback"


def test_discovery_time_delays_research_authority(monkeypatch) -> None:
    bars = _bars()
    _install_deterministic_signals(monkeypatch, bars)
    discovered_at = bars[14].end_time

    snapshot = research.evaluate_leader_momentum_research_variant(
        bars,
        variant=research.BASELINE_V1_2,
        discovered_at=discovered_at,
    ).snapshot

    assert snapshot.trades
    assert snapshot.trades[0].signal_time >= discovered_at
    assert snapshot.trades[0].mode == "controlled_pullback"


def test_research_variants_do_not_expose_execution_authority() -> None:
    result = research.evaluate_leader_momentum_research_variant(
        [],
        variant=research.CONTROLLED_PULLBACK_SINGLE_TRADE,
    )
    assert result.execution_authority is False
    assert result.snapshot.execution_authority is False
    assert result.source_policy_version == "leader-momentum-continuation-v1.2"
