from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.apps.trading.bounce_model import label_two_r_before_one_r
from app.apps.trading.catalyst_evidence import capture_catalyst_evidence
from app.apps.trading.gapper_dataset import (
    GapperCandidate,
    freeze_gapper_universe,
    time_of_day_relative_volume,
)
from app.apps.trading.models import MarketBar
from app.apps.trading.paper import (
    PaperAccount,
    PaperAccountSnapshot,
    PaperBalance,
    PaperExecutionPolicy,
    PaperLedgerEntry,
    PaperPosition,
)
from app.apps.trading.replay import FrozenBar, dataset_gaps
from app.apps.trading.strategies import evaluate_gap_pullback, session_vwap
from app.apps.trading.strategies.models import GapPullbackConfig, StrategyRiskProfile, StrategySignal
from app.apps.trading.strategy_backtest import freeze_backtest_session, run_gap_pullback_backtest
from app.apps.trading.strategy_risk import size_strategy_entry


INSTRUMENT = "equity:NASDAQ:TEST"
BINDING = "fixture:TEST"
OPEN = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)  # 09:30 ET


def bar(
    index: int,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "100",
    *,
    day_offset: int = 0,
) -> MarketBar:
    start = OPEN + timedelta(days=day_offset, minutes=index)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=1),
    )


def candidate(**overrides) -> GapperCandidate:
    payload = {
        "instrument_id": INSTRUMENT,
        "binding_id": BINDING,
        "previous_close": Decimal("8"),
        "premarket_price": Decimal("10.4"),
        "gap_pct": Decimal("30"),
        "premarket_volume": Decimal("100000"),
        "premarket_dollar_volume": Decimal("1040000"),
        "tod_rvol": Decimal("3"),
        "market_cap": Decimal("50000000"),
        "float_shares": Decimal("5000000"),
        "spread_bps": Decimal("40"),
        "discovery_rank": 1,
    }
    payload.update(overrides)
    return GapperCandidate(**payload)


def pattern_bars() -> list[MarketBar]:
    # Opening impulse -> L1 -> B1 -> higher L2 -> VWAP/B1 breakout -> next bar/target.
    return [
        bar(0, "10", "10.4", "9.9", "10.3", "100"),
        bar(1, "10.3", "11.2", "10.2", "11.0", "120"),
        bar(2, "11.0", "11.05", "10.5", "10.6", "80"),
        bar(3, "10.6", "10.7", "9.8", "10.0", "70"),
        bar(4, "10.0", "10.9", "9.95", "10.8", "80"),
        bar(5, "10.8", "11.1", "10.7", "11.0", "90"),
        bar(6, "10.9", "11.0", "10.4", "10.5", "80"),
        bar(7, "10.5", "10.7", "10.2", "10.3", "70"),
        bar(8, "10.3", "11.0", "10.25", "10.9", "80"),
        bar(9, "10.9", "11.8", "10.85", "11.7", "400"),
        bar(10, "11.75", "12.0", "11.6", "11.9", "1000"),
        bar(11, "11.9", "15.5", "11.8", "15.0", "1000"),
    ]


def causal_config() -> GapPullbackConfig:
    return GapPullbackConfig(
        pivot_left_bars=1,
        pivot_right_bars=1,
        volume_lookback_bars=5,
        breakout_volume_ratio=Decimal("1.25"),
        entry_start_et=time(9, 30),
    )


def test_pivots_are_not_known_before_right_side_confirmation() -> None:
    bars = pattern_bars()
    config = causal_config()

    before_confirmation = evaluate_gap_pullback(candidate(), bars[:4], config)
    assert before_confirmation.state == "first_pullback"
    assert before_confirmation.features.l1 is None

    after_confirmation = evaluate_gap_pullback(candidate(), bars[:5], config)
    assert after_confirmation.state == "first_low_confirmed"
    assert after_confirmation.features.l1 == Decimal("9.8")


def test_future_bars_do_not_change_any_already_evaluated_prefix() -> None:
    bars = pattern_bars()
    config = causal_config()
    baseline = [
        evaluate_gap_pullback(candidate(), bars[:index], config).model_dump(mode="json")
        for index in range(1, 11)
    ]
    future_crash = bar(12, "8", "8.2", "5", "5.5", "5000")
    expanded = [*bars, future_crash]
    replayed = [
        evaluate_gap_pullback(candidate(), expanded[:index], config).model_dump(mode="json")
        for index in range(1, 11)
    ]
    assert replayed == baseline
    assert baseline[-1]["state"] == "entry_ready"


def test_session_vwap_resets_at_regular_open() -> None:
    prior = bar(0, "100", "101", "99", "100", "1000000", day_offset=-1)
    today = pattern_bars()[:3]
    assert session_vwap([prior, *today]) == session_vwap(today)


def test_frozen_gapper_universe_is_order_independent_and_point_in_time() -> None:
    second = candidate(
        instrument_id="equity:NASDAQ:TEST2",
        binding_id="fixture:TEST2",
        previous_close=Decimal("4"),
        premarket_price=Decimal("5"),
        gap_pct=Decimal("25"),
        discovery_rank=2,
    )
    evaluation = datetime(2026, 8, 18, 13, 20, tzinfo=timezone.utc)
    first = freeze_gapper_universe(
        universe_id="gappers-2026-08-18",
        session_date=date(2026, 8, 18),
        evaluation_time=evaluation,
        discovery_source="import",
        candidates=[candidate(), second],
    )
    second_copy = freeze_gapper_universe(
        universe_id="gappers-2026-08-18",
        session_date=date(2026, 8, 18),
        evaluation_time=evaluation,
        discovery_source="import",
        candidates=[second, candidate()],
    )
    assert first.source_fingerprint == second_copy.source_fingerprint
    assert [item.discovery_rank for item in first.candidates] == [1, 2]



def test_finviz_source_cohort_participates_in_new_fingerprint_without_changing_legacy_fingerprint() -> None:
    evaluation = datetime(2026, 8, 28, 13, 20, tzinfo=timezone.utc)
    legacy = freeze_gapper_universe(
        universe_id="legacy",
        session_date=date(2026, 8, 28),
        evaluation_time=evaluation,
        discovery_source="import",
        candidates=[candidate()],
    )
    legacy_again = freeze_gapper_universe(
        universe_id="legacy",
        session_date=date(2026, 8, 28),
        evaluation_time=evaluation,
        discovery_source="import",
        candidates=[candidate()],
        source_locator=None,
        source_candidate_symbols=(),
    )
    assert legacy.source_fingerprint == legacy_again.source_fingerprint

    finviz = freeze_gapper_universe(
        universe_id="finviz",
        session_date=date(2026, 8, 28),
        evaluation_time=evaluation,
        discovery_source="finviz",
        source_locator="https://finviz.com/screener?v=340&s=ta_topgainers",
        source_candidate_symbols=("TEST", "TEST2"),
        candidates=[candidate(observed_at=evaluation)],
    )
    assert finviz.source_candidate_symbols == ("TEST", "TEST2")
    assert finviz.source_locator and "ta_topgainers" in finviz.source_locator
    assert finviz.source_fingerprint != legacy.source_fingerprint


def test_split_normalized_gap_evidence_and_time_of_day_rvol_are_auditable() -> None:
    adjusted = candidate(
        previous_close=Decimal("5"),
        raw_previous_close=Decimal("10"),
        split_adjustment_factor=Decimal("0.5"),
        corporate_action_evidence_ids=("split-2-for-1",),
        premarket_price=Decimal("6"),
        gap_pct=Decimal("20"),
    )
    assert adjusted.previous_close == Decimal("5")
    assert adjusted.raw_previous_close == Decimal("10")
    assert adjusted.split_adjustment_factor == Decimal("0.5")
    assert adjusted.corporate_action_evidence_ids == ("split-2-for-1",)

    with pytest.raises(ValueError):
        candidate(
            previous_close=Decimal("5"),
            raw_previous_close=Decimal("10"),
            split_adjustment_factor=Decimal("0.5"),
            corporate_action_evidence_ids=(),
            premarket_price=Decimal("6"),
            gap_pct=Decimal("20"),
        )

    assert time_of_day_relative_volume(
        Decimal("300"),
        [Decimal("100"), Decimal("150"), Decimal("200")],
    ) == Decimal("2")


def test_replay_gap_detection_ignores_exchange_closures_but_not_missing_minutes() -> None:
    def frozen(start: datetime) -> FrozenBar:
        return FrozenBar(
            instrument_id=INSTRUMENT,
            interval="1m",
            start_time=start,
            end_time=start + timedelta(minutes=1),
            open=Decimal("10"),
            high=Decimal("10"),
            low=Decimal("10"),
            close=Decimal("10"),
            volume=Decimal("1"),
            is_final=True,
            adjustment_mode="raw",
            session="regular",
            provider="fixture",
            received_at=start + timedelta(minutes=1),
        )

    friday = frozen(datetime(2026, 8, 14, 19, 59, tzinfo=timezone.utc))
    monday_open = frozen(datetime(2026, 8, 17, 13, 30, tzinfo=timezone.utc))
    monday_missing = frozen(datetime(2026, 8, 17, 13, 32, tzinfo=timezone.utc))
    gaps = dataset_gaps(
        [friday, monday_open, monday_missing],
        "1m",
        session_calendar="XNYS",
        exchange_timezone="America/New_York",
    )
    assert gaps == [(monday_open.end_time, monday_missing.start_time)]


def test_strategy_backtest_uses_shared_paper_execution_policy_and_next_bar_entry() -> None:
    universe = freeze_gapper_universe(
        universe_id="gappers-2026-08-18",
        session_date=date(2026, 8, 18),
        evaluation_time=datetime(2026, 8, 18, 13, 20, tzinfo=timezone.utc),
        discovery_source="import",
        candidates=[candidate()],
    )
    bars = pattern_bars()
    dataset = freeze_backtest_session(
        session_date=date(2026, 8, 18),
        universe=universe,
        bars_by_instrument={INSTRUMENT: bars},
    )
    policy = PaperExecutionPolicy(
        slippage_bps=Decimal("10"),
        max_volume_participation_pct=Decimal("1"),
        latency_ms=0,
    )
    result = run_gap_pullback_backtest(
        dataset,
        causal_config(),
        policy,
        assumed_spread_bps=Decimal("40"),
        max_hold_minutes=90,
    )
    assert result.execution_policy_version == "paper-execution-v2"
    assert result.summary.trigger_count == 1
    assert result.summary.trade_count == 1
    assert result.summary.entry_execution_rejection_count == 0
    assert result.summary.exit_execution_rejection_count == 0
    assert result.summary.portfolio_capacity_rejection_count == 0
    assert result.summary.trigger_to_trade_rate == Decimal("1")
    assert result.summary.average_entry_slippage_bps > 0
    trade = result.trades[0]
    assert trade.trigger_bar_index == 9
    assert trade.entry_bar_index == 10
    assert trade.entry_time == bars[10].start_time
    assert trade.entry_price > bars[10].open
    assert trade.entry_slippage_bps > 0
    assert trade.exit_reason == "target"
    assert trade.r_multiple > Decimal("1.9")


def test_strategy_backtest_exits_on_rsi_cross_instead_of_elapsed_time() -> None:
    universe = freeze_gapper_universe(
        universe_id="gappers-2026-08-18-rsi",
        session_date=date(2026, 8, 18),
        evaluation_time=datetime(2026, 8, 18, 13, 20, tzinfo=timezone.utc),
        discovery_source="import",
        candidates=[candidate()],
    )
    bars = pattern_bars()
    bars.extend(
        [
            bar(12, "15.0", "15.2", "14.8", "15.1", "100000"),
            bar(13, "15.1", "15.2", "14.5", "14.7", "100000"),
            bar(14, "14.7", "14.8", "13.8", "13.9", "100000"),
            bar(15, "13.9", "14.0", "13.5", "13.6", "100000"),
        ]
    )
    dataset = freeze_backtest_session(
        session_date=date(2026, 8, 18),
        universe=universe,
        bars_by_instrument={INSTRUMENT: bars},
    )
    config = causal_config().model_copy(
        update={
            "reward_multiple": Decimal("10"),
            "exit_rsi_period": 2,
            "exit_rsi_threshold": Decimal("70"),
        }
    )

    result = run_gap_pullback_backtest(
        dataset,
        config,
        PaperExecutionPolicy(max_volume_participation_pct=Decimal("1")),
        max_hold_minutes=1,
    )

    assert result.summary.trade_count == 1
    assert result.summary.indicator_exit_count == 1
    assert result.trades[0].exit_reason == "rsi"


def risk_snapshot(*, positions: int = 0, daily_loss: str = "0") -> PaperAccountSnapshot:
    ledger = []
    if Decimal(daily_loss) != 0:
        ledger.append(
            PaperLedgerEntry(
                ledger_id="daily-loss",
                entry_type="realized_pnl",
                currency="USD",
                amount=Decimal(daily_loss),
                idempotency_key="daily-loss",
                created_at=datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc),
            )
        )
    return PaperAccountSnapshot(
        account=PaperAccount(
            account_id="paper-1",
            name="Paper",
            base_currency="USD",
            commission_bps=Decimal("0"),
        ),
        balances=[PaperBalance(currency="USD", available=Decimal("10000"))],
        positions=[
            PaperPosition(
                instrument_id=f"equity:NASDAQ:P{index}",
                quantity=Decimal("1"),
                average_cost=Decimal("100"),
                realized_pnl=Decimal("0"),
                last_price=Decimal("100"),
            )
            for index in range(positions)
        ],
        open_orders=[],
        order_history=[],
        recent_fills=[],
        recent_ledger=ledger,
    )


def risk_signal() -> StrategySignal:
    return StrategySignal(
        instrument_id=INSTRUMENT,
        state="entry_ready",
        entry_price=Decimal("10"),
        stop_price=Decimal("9.5"),
        target_price=Decimal("11"),
        risk_per_share=Decimal("0.5"),
        reason_code="FAILED_SELL_OFF_CONFIRMED",
    )


def test_server_strategy_risk_enforces_kill_daily_loss_position_and_time_gates() -> None:
    observed = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)  # 10:00 ET
    base = StrategyRiskProfile()

    assert size_strategy_entry(
        risk_snapshot(),
        risk_signal(),
        base.model_copy(update={"kill_switch": True}),
        spread_bps=Decimal("40"),
        observed_at=observed,
    ).reason_code == "KILL_SWITCH"
    assert size_strategy_entry(
        risk_snapshot(daily_loss="-200"),
        risk_signal(),
        base,
        spread_bps=Decimal("40"),
        observed_at=observed,
    ).reason_code == "MAX_DAILY_LOSS"
    assert size_strategy_entry(
        risk_snapshot(positions=3),
        risk_signal(),
        base,
        spread_bps=Decimal("40"),
        observed_at=observed,
    ).reason_code == "MAX_POSITIONS"
    assert size_strategy_entry(
        risk_snapshot(),
        risk_signal(),
        base.model_copy(update={"max_positions": 1}),
        spread_bps=Decimal("40"),
        reserved_instruments={"equity:NASDAQ:PENDING"},
        observed_at=observed,
    ).reason_code == "MAX_POSITIONS"
    assert size_strategy_entry(
        risk_snapshot(),
        risk_signal(),
        base,
        spread_bps=Decimal("40"),
        observed_at=datetime(2026, 8, 18, 13, 31, tzinfo=timezone.utc),
    ).reason_code == "ENTRY_WINDOW_NOT_OPEN"


def test_catalyst_evidence_is_immutable_and_dilution_is_evidence_backed() -> None:
    published = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    captured = datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc)
    kwargs = dict(
        evidence_id="ev-1",
        instrument_id=INSTRUMENT,
        source_type="sec",
        source_locator="sec:fixture:8-k",
        published_at=published,
        captured_at=captured,
        headline="Financing update",
        raw_text="The issuer entered an at-the-market sales agreement and issued warrants.",
    )
    first = capture_catalyst_evidence(**kwargs)
    second = capture_catalyst_evidence(**kwargs)
    assert first.immutable_fingerprint == second.immutable_fingerprint
    assert "atm" in first.dilution_flags
    assert "warrants" in first.dilution_flags


def test_two_r_label_is_pessimistic_when_stop_and_target_hit_same_bar() -> None:
    ambiguous = bar(1, "10", "12.2", "8.9", "11", "1000")
    label = label_two_r_before_one_r(
        [ambiguous],
        entry_time=OPEN,
        entry_price=Decimal("10"),
        risk_per_share=Decimal("1"),
    )
    assert label == 0


def test_strategy_surface_remains_paper_only_and_ai_shadow_only() -> None:
    monitor = "\n".join(path.read_text(encoding="utf-8") for path in [*sorted(Path("src/app/apps/trading").glob("strategy_monitor*.py")), Path("src/app/apps/trading/strategy_entry_path.py")])
    catalyst = Path("src/app/apps/trading/catalyst_evidence.py").read_text().lower()
    gateway = Path("src/app/apps/trading/route_registration.py").read_text()
    strategy_api = Path("src/app/apps/trading/strategy_api.py").read_text()

    assert '"live_broker_enabled": False' in monitor
    assert '"ai_order_placement_enabled": False' in monitor
    assert "shadow_only" in catalyst
    assert "create_trading_strategy_router" in gateway
    assert '"/backtest/gap-pullback"' in strategy_api
