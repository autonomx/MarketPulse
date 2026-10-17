from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.market_data_recovery import StrategyDataRequirement
from scripts.trade.run_interday_winner_shadow_replay import (
    MarketDataCache,
    RawBar,
    _decision_dependency_view,
    _build_5m_history,
    _gap_pullback_outcome_final_before_gap,
    _leader_outcome_final_before_gap,
    _needs_one_minute_recovery,
    _recover_one_minute_sessions,
    _stoch_tolerable_single_minute_gap,
    _stoch_trend_outcome_final_before_gap,
    _yahoo_1m_chunks,
)


UTC = timezone.utc
SESSION = date(2026, 9, 16)
OPEN = datetime(2026, 9, 16, 13, 30, tzinfo=UTC)  # 09:30 ET


def _bar(minute: int, *, interval_minutes: int = 1, price: str = "10") -> RawBar:
    value = Decimal(price)
    return RawBar(
        start=OPEN + timedelta(minutes=minute),
        open=value,
        high=value + Decimal("0.10"),
        low=value - Decimal("0.10"),
        close=value + Decimal("0.05"),
        volume=Decimal("1000"),
        interval_minutes=interval_minutes,
    )


def _five_bar(start: datetime, price: str) -> RawBar:
    value = Decimal(price)
    return RawBar(
        start=start,
        open=value,
        high=value + Decimal("0.10"),
        low=value - Decimal("0.10"),
        close=value + Decimal("0.05"),
        volume=Decimal("1000"),
        interval_minutes=5,
    )


def _session_requirement() -> StrategyDataRequirement:
    return StrategyDataRequirement(
        interval="1m",
        continuity="session",
        minimum_clean_bars=1,
        required_fields=("ohlc", "volume"),
        reset_on_gap=False,
    )


def _rolling_requirement(minimum_clean_bars: int) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        interval="1m",
        continuity="rolling",
        minimum_clean_bars=minimum_clean_bars,
        required_fields=("ohlc", "volume"),
        reset_on_gap=True,
    )


def test_5m_history_carries_prior_sessions_but_not_future_bars() -> None:
    prior = _five_bar(datetime(2026, 9, 15, 13, 30, tzinfo=UTC), "9")
    current = _five_bar(datetime(2026, 9, 16, 13, 30, tzinfo=UTC), "10")
    future = _five_bar(datetime(2026, 9, 17, 13, 30, tzinfo=UTC), "11")

    history = _build_5m_history((prior, current, future), [SESSION])

    assert history[SESSION] == (prior, current)


def test_yahoo_one_minute_chunks_cover_the_requested_current_session() -> None:
    assert _yahoo_1m_chunks(SESSION, SESSION) == (
        (date(2026, 9, 16), date(2026, 9, 17)),
    )


def test_yahoo_one_minute_chunks_are_dynamic_and_bounded() -> None:
    chunks = _yahoo_1m_chunks(date(2026, 9, 1), date(2026, 9, 16))
    assert chunks[0][0] == date(2026, 9, 1)
    assert chunks[-1][1] == date(2026, 9, 17)
    assert all((end - start).days <= 7 for start, end in chunks)
    assert all(left[1] == right[0] for left, right in zip(chunks, chunks[1:]))


def test_cache_reuses_requested_session_from_a_different_manifest_range(tmp_path) -> None:
    cache = MarketDataCache(tmp_path, "yahoo")
    broad_start = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
    broad_end = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)
    target = _bar(0)
    cache.store(
        "MEDS",
        "1m",
        (target,),
        start=broad_start,
        end=broad_end,
        query_profile="regular_session",
    )

    narrow = cache.load(
        "MEDS",
        "1m",
        start=OPEN,
        end=datetime(2026, 9, 16, 20, 0, tzinfo=UTC),
        query_profile="regular_session",
    )

    assert narrow == (target,)


def test_cache_still_rejects_a_missing_requested_session(tmp_path) -> None:
    cache = MarketDataCache(tmp_path, "yahoo")
    cache.store(
        "MEDS",
        "1m",
        (_bar(0),),
        start=OPEN,
        end=datetime(2026, 9, 16, 20, 0, tzinfo=UTC),
        query_profile="regular_session",
    )

    assert cache.load(
        "MEDS",
        "1m",
        start=datetime(2026, 9, 17, 13, 30, tzinfo=UTC),
        end=datetime(2026, 9, 17, 20, 0, tzinfo=UTC),
        query_profile="regular_session",
    ) is None


def test_replay_recovery_fills_twenty_minute_hole_from_factual_fallback() -> None:
    primary = tuple(_bar(index) for index in range(390) if not 10 <= index < 30)
    fallback = tuple(_bar(index, price="10.01") for index in range(10, 30))

    assert _needs_one_minute_recovery(
        "MEDS", [SESSION], primary, source="yahoo"
    ) is True

    recovered, unresolved = _recover_one_minute_sessions(
        "MEDS",
        [SESSION],
        primary_raw=primary,
        fallback_raw=fallback,
        primary_source="yahoo",
        fallback_source="alpaca-sip",
    )

    assert unresolved == {}
    assert len(recovered[SESSION]) == 390
    assert recovered[SESSION][9].open == Decimal("10")
    assert recovered[SESSION][10].open == Decimal("10.01")
    assert recovered[SESSION][29].open == Decimal("10.01")
    assert recovered[SESSION][30].open == Decimal("10")


def test_unrecoverable_hole_retains_factual_tape_for_dependency_analysis() -> None:
    primary = tuple(_bar(index) for index in range(390) if not 10 <= index < 30)

    recovered, unresolved = _recover_one_minute_sessions(
        "MEDS",
        [SESSION],
        primary_raw=primary,
        fallback_raw=(),
        primary_source="yahoo",
        fallback_source=None,
    )

    assert len(recovered[SESSION]) == 370
    assert recovered[SESSION][9].start == OPEN + timedelta(minutes=9)
    assert recovered[SESSION][10].start == OPEN + timedelta(minutes=30)
    assert SESSION in unresolved
    assert "UNRESOLVED_1M_GAPS" in unresolved[SESSION]


def test_session_dependency_exposes_only_safe_prefix_before_gap() -> None:
    primary = tuple(_bar(index) for index in range(390) if not 10 <= index < 30)
    recovered, _ = _recover_one_minute_sessions(
        "MEDS", [SESSION], primary_raw=primary, fallback_raw=(),
        primary_source="yahoo", fallback_source=None,
    )

    view = _decision_dependency_view(
        "MEDS", SESSION, recovered[SESSION], requirement=_session_requirement()
    )

    assert view.decision_evaluable is False
    assert view.blocked_after == OPEN + timedelta(minutes=10)
    assert len(view.bars) == 10
    assert view.bars[-1].end_time == view.blocked_after
    assert "SESSION_DEPENDS_ON_UNRESOLVED_GAP" in view.reason_codes


def test_rolling_dependency_resumes_after_clean_post_gap_warmup() -> None:
    primary = tuple(_bar(index) for index in range(390) if not 10 <= index < 30)
    recovered, _ = _recover_one_minute_sessions(
        "MEDS", [SESSION], primary_raw=primary, fallback_raw=(),
        primary_source="yahoo", fallback_source=None,
    )

    view = _decision_dependency_view(
        "MEDS",
        SESSION,
        recovered[SESSION],
        requirement=_rolling_requirement(60),
    )

    assert view.decision_evaluable is True
    assert view.reset_required is True
    assert len(view.bars) == 360
    assert view.bars[0].start_time == OPEN + timedelta(minutes=30)


def test_rolling_dependency_stays_blocked_until_warmup_completes() -> None:
    primary = tuple(_bar(index) for index in range(390) if not 320 <= index < 370)
    recovered, _ = _recover_one_minute_sessions(
        "MEDS", [SESSION], primary_raw=primary, fallback_raw=(),
        primary_source="yahoo", fallback_source=None,
    )

    view = _decision_dependency_view(
        "MEDS",
        SESSION,
        recovered[SESSION],
        requirement=_rolling_requirement(50),
    )

    assert view.decision_evaluable is False
    assert view.reset_required is True
    assert len(view.bars) == 20
    assert "POST_GAP_WARMUP_INCOMPLETE" in view.reason_codes


def test_stoch_trend_preserves_explicit_single_missing_minute_policy() -> None:
    primary = tuple(_bar(index) for index in range(390) if index != 42)
    _recover_one_minute_sessions(
        "MEDS", [SESSION], primary_raw=primary, fallback_raw=(),
        primary_source="yahoo", fallback_source=None,
    )

    assert _stoch_tolerable_single_minute_gap("MEDS", SESSION) is True


def test_stoch_completed_trade_before_gap_is_final() -> None:
    cutoff = OPEN + timedelta(minutes=60)
    snapshot = SimpleNamespace(
        return_pct=Decimal("4"),
        runner_exit_time=OPEN + timedelta(minutes=45),
        entry_time=OPEN + timedelta(minutes=20),
        state="range_exited",
    )

    final, reason = _stoch_trend_outcome_final_before_gap(snapshot, cutoff)

    assert final is True
    assert reason == "STOCH_TREND_TRADE_COMPLETED_BEFORE_GAP"


def test_stoch_open_position_across_gap_remains_blocked() -> None:
    cutoff = OPEN + timedelta(minutes=60)
    snapshot = SimpleNamespace(
        return_pct=None,
        runner_exit_time=None,
        entry_time=OPEN + timedelta(minutes=20),
        state="trend_active",
    )

    final, reason = _stoch_trend_outcome_final_before_gap(snapshot, cutoff)

    assert final is False
    assert reason == "STOCH_TREND_OPEN_POSITION_SPANS_GAP"


def test_stoch_gap_after_entry_window_is_irrelevant_without_pending_entry() -> None:
    cutoff = OPEN + timedelta(minutes=150)  # 12:00 ET
    snapshot = SimpleNamespace(
        return_pct=None,
        runner_exit_time=None,
        entry_time=None,
        state="waiting_oversold",
    )

    final, reason = _stoch_trend_outcome_final_before_gap(snapshot, cutoff)

    assert final is True
    assert reason == "STOCH_TREND_GAP_AFTER_ENTRY_WINDOW_NO_OPEN_POSITION"


def test_leader_gap_after_entry_window_is_irrelevant_after_genuine_exit() -> None:
    cutoff = OPEN + timedelta(minutes=361)  # 15:31 ET
    trade = SimpleNamespace(
        exit_time=OPEN + timedelta(minutes=300),
        exit_reason_code="LEADER_MOMENTUM_INITIAL_STOP",
    )
    snapshot = SimpleNamespace(trades=(trade,), state="completed")

    final, reason = _leader_outcome_final_before_gap(snapshot, cutoff)

    assert final is True
    assert reason == "LEADER_MOMENTUM_GAP_AFTER_ENTRY_WINDOW_NO_OPEN_POSITION"


def test_leader_truncated_force_flat_is_not_mistaken_for_a_real_exit() -> None:
    cutoff = OPEN + timedelta(minutes=120)
    trade = SimpleNamespace(
        exit_time=OPEN + timedelta(minutes=117),
        exit_reason_code="LEADER_MOMENTUM_FORCE_FLAT",
    )
    snapshot = SimpleNamespace(trades=(trade,), state="force_flat")

    final, reason = _leader_outcome_final_before_gap(snapshot, cutoff)

    assert final is False
    assert reason == "LEADER_MOMENTUM_OPEN_POSITION_SPANS_GAP"


def test_gap_pullback_real_pre_gap_exit_is_final_but_truncated_eod_is_not() -> None:
    cutoff = OPEN + timedelta(minutes=90)
    config = SimpleNamespace(last_entry_et=time(11, 30))
    stop_trade = SimpleNamespace(
        exit_time=OPEN + timedelta(minutes=75), exit_reason="stop"
    )
    eod_trade = SimpleNamespace(
        exit_time=cutoff, exit_reason="eod"
    )

    assert _gap_pullback_outcome_final_before_gap(
        stop_trade, None, cutoff, config=config
    )[0] is True
    final, reason = _gap_pullback_outcome_final_before_gap(
        eod_trade, None, cutoff, config=config
    )
    assert final is False
    assert reason == "GAP_PULLBACK_OPEN_POSITION_SPANS_GAP"


def test_gap_pullback_terminal_rejection_is_independent_of_later_gap() -> None:
    cutoff = OPEN + timedelta(minutes=20)
    config = SimpleNamespace(last_entry_et=time(11, 30))
    decision = SimpleNamespace(state="rejected", rejection_reason="price_filter")

    final, reason = _gap_pullback_outcome_final_before_gap(
        None, decision, cutoff, config=config
    )

    assert final is True
    assert reason == "GAP_PULLBACK_TERMINAL_DECISION_BEFORE_GAP"
