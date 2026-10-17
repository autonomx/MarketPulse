from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.models import MarketBar
from app.apps.trading import strategy_leader_momentum_continuation as leader
from app.apps.trading.strategy_timeframes import resample_final_bars


def _bar(
    start: datetime,
    *,
    interval: str = "1m",
    open_: str = "2.00",
    high: str = "2.03",
    low: str = "1.99",
    close: str = "2.02",
    volume: str = "100000",
) -> MarketBar:
    minutes = 1 if interval == "1m" else 3
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval=interval,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        is_final=True,
        session="regular",
        provider="test",
    )


def _rising_one_minute_bars(count: int = 75) -> list[MarketBar]:
    start = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
    bars: list[MarketBar] = []
    price = Decimal("2.00")
    for index in range(count):
        next_price = price * Decimal("1.0025")
        bars.append(
            _bar(
                start + timedelta(minutes=index),
                open_=str(price),
                high=str(next_price * Decimal("1.004")),
                low=str(price * Decimal("0.997")),
                close=str(next_price),
                volume=str(100000 + index * 2500),
            )
        )
        price = next_price
    return bars


def _three_minute_bar(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str,
) -> MarketBar:
    start = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=3 * index)
    return _bar(
        start,
        interval="3m",
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _warmup_three_minute_bars() -> list[MarketBar]:
    bars: list[MarketBar] = []
    price = Decimal("2.00")
    for index in range(20):
        close = price + Decimal("0.012")
        bars.append(
            _three_minute_bar(
                index,
                open_=str(price),
                high=str(close + Decimal("0.025")),
                low=str(price - Decimal("0.020")),
                close=str(close),
                volume="100000",
            )
        )
        price = close
    return bars


def _controlled_pullback_fixture() -> tuple[list[MarketBar], int]:
    bars = _warmup_three_minute_bars()
    specs = (
        ("2.24", "2.32", "2.22", "2.30", "320000"),
        ("2.30", "2.40", "2.28", "2.38", "360000"),
        ("2.38", "2.48", "2.36", "2.46", "400000"),
        ("2.46", "2.54", "2.44", "2.52", "440000"),
        ("2.52", "2.53", "2.43", "2.47", "145000"),
        ("2.47", "2.49", "2.41", "2.45", "140000"),
        ("2.45", "2.50", "2.43", "2.48", "150000"),
        ("2.48", "2.61", "2.47", "2.59", "520000"),
        ("2.59", "2.66", "2.56", "2.64", "280000"),
        ("2.64", "2.70", "2.60", "2.67", "260000"),
    )
    for offset, spec in enumerate(specs, start=20):
        bars.append(_three_minute_bar(offset, open_=spec[0], high=spec[1], low=spec[2], close=spec[3], volume=spec[4]))
    return bars, 27


def _momentum_compression_fixture() -> tuple[list[MarketBar], int]:
    bars = _warmup_three_minute_bars()
    specs = (
        ("2.24", "2.34", "2.22", "2.32", "330000"),
        ("2.32", "2.44", "2.30", "2.42", "380000"),
        ("2.42", "2.54", "2.40", "2.52", "430000"),
        # Tight compression: deliberately too shallow to satisfy the 15% Mode-A retrace.
        ("2.52", "2.55", "2.50", "2.53", "180000"),
        ("2.53", "2.56", "2.51", "2.54", "175000"),
        ("2.54", "2.57", "2.52", "2.55", "170000"),
        ("2.55", "2.61", "2.54", "2.59", "420000"),
        ("2.59", "2.65", "2.57", "2.63", "260000"),
        ("2.63", "2.68", "2.60", "2.65", "250000"),
    )
    for offset, spec in enumerate(specs, start=20):
        bars.append(_three_minute_bar(offset, open_=spec[0], high=spec[1], low=spec[2], close=spec[3], volume=spec[4]))
    return bars, 26


def test_empty_tape_waits_and_never_has_execution_authority() -> None:
    snapshot = leader.evaluate_leader_momentum_continuation([])

    assert snapshot.state == "waiting_session"
    assert snapshot.reason_code == "LEADER_MOMENTUM_WAITING_SESSION"
    assert snapshot.execution_authority is False
    assert snapshot.policy_version == "leader-momentum-continuation-v1.2"


def test_context_rejects_invalid_negative_market_inputs() -> None:
    with pytest.raises(ValueError):
        leader.LeaderMomentumContext(tod_rvol=Decimal("-1"))
    with pytest.raises(ValueError):
        leader.LeaderMomentumContext(spread_bps=Decimal("-1"))


def test_wide_spread_reduces_deterministic_leader_score() -> None:
    bars = _rising_one_minute_bars()
    sampled = resample_final_bars(bars, "3m")
    index = len(sampled) - 1

    tight = leader._leader_score(
        bars,
        sampled,
        index=index,
        context=leader.LeaderMomentumContext(
            tod_rvol=Decimal("8"),
            relative_strength_pct=Decimal("15"),
            spread_bps=Decimal("80"),
            dollar_volume=Decimal("5000000"),
            volume_acceleration=Decimal("2"),
            hod_frequency_15m=3,
        ),
    )
    wide = leader._leader_score(
        bars,
        sampled,
        index=index,
        context=leader.LeaderMomentumContext(
            tod_rvol=Decimal("8"),
            relative_strength_pct=Decimal("15"),
            spread_bps=Decimal("300"),
            dollar_volume=Decimal("5000000"),
            volume_acceleration=Decimal("2"),
            hod_frequency_15m=3,
        ),
    )

    assert tight > wide


def test_historical_gap_is_recorded_without_invalidating_entire_session() -> None:
    bars = _rising_one_minute_bars()
    del bars[25]

    snapshot = leader.evaluate_leader_momentum_continuation(bars)

    assert snapshot.state != "data_gap"
    assert snapshot.data_gap_start is not None
    assert snapshot.data_gap_resume is not None
    assert snapshot.recovered_gap_count >= 1
    assert snapshot.execution_authority is False


def test_setup_window_must_not_cross_a_three_minute_gap() -> None:
    start = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
    sampled = [
        _bar(start + timedelta(minutes=3 * index), interval="3m")
        for index in range(6)
    ]
    assert leader._is_contiguous(sampled, 0, 5) is True

    sampled[4] = _bar(start + timedelta(minutes=15), interval="3m")
    assert leader._is_contiguous(sampled, 0, 5) is False


def test_old_gap_outside_setup_window_does_not_block_controlled_pullback() -> None:
    bars, signal_index = _controlled_pullback_fixture()
    # A six-minute discontinuity ends well before the impulse starts.
    bars.pop(5)
    signal_index -= 1
    ema9 = leader._ema([bar.close for bar in bars], 9)
    atr14 = leader._atr(bars, 14)

    setup = leader._setup_at(bars, bars, ema9, atr14, index=signal_index)

    assert setup is not None
    assert setup[0] == "controlled_pullback"


def test_three_minute_ema_extension_uses_three_minute_atr(monkeypatch) -> None:
    bars, signal_index = _controlled_pullback_fixture()
    ema9 = leader._ema([bar.close for bar in bars], 9)
    # Large enough 3m ATR for the continuation to be acceptable.
    atr14 = [None] * len(bars)
    atr14[signal_index] = Decimal("0.20")

    # A tiny source-tape ATR would reject this setup if the v1 timeframe bug
    # were reintroduced. In v1.1 it is used only for stop buffering.
    monkeypatch.setattr(
        leader,
        "_atr",
        lambda values, period=14: [Decimal("0.01")] * len(values),
    )

    setup = leader._setup_at(bars, bars, ema9, atr14, index=signal_index)

    assert setup is not None
    assert setup[0] == "controlled_pullback"
    assert setup[3] == Decimal("0.01")


def test_leader_latch_survives_pullback_until_controlled_breakout(monkeypatch) -> None:
    bars, signal_index = _controlled_pullback_fixture()

    def score_once(_regular, _sampled, *, index, context):
        del context
        return Decimal("80") if index == 20 else Decimal("50")

    monkeypatch.setattr(leader, "_leader_score", score_once)
    snapshot = leader.evaluate_leader_momentum_continuation(bars)

    assert snapshot.leader_confirmed_at == bars[20].end_time
    assert snapshot.signal_time == bars[signal_index].end_time
    assert snapshot.trades
    assert snapshot.trades[0].mode == "controlled_pullback"


def test_controlled_pullback_fixture_reaches_trade_end_to_end(monkeypatch) -> None:
    bars, _ = _controlled_pullback_fixture()
    monkeypatch.setattr(
        leader,
        "_leader_score",
        lambda *_args, **_kwargs: Decimal("85"),
    )

    snapshot = leader.evaluate_leader_momentum_continuation(bars)

    assert snapshot.trades
    assert snapshot.trades[0].mode == "controlled_pullback"
    assert snapshot.execution_authority is False


def test_momentum_compression_fixture_reaches_trade_end_to_end(monkeypatch) -> None:
    bars, _ = _momentum_compression_fixture()
    monkeypatch.setattr(leader, "MIN_IMPULSE_PCT", Decimal("999"))
    monkeypatch.setattr(
        leader,
        "_leader_score",
        lambda *_args, **_kwargs: Decimal("85"),
    )

    snapshot = leader.evaluate_leader_momentum_continuation(bars)

    assert snapshot.trades
    assert snapshot.trades[0].mode == "momentum_compression"
    assert snapshot.execution_authority is False


def test_v1_2_promoted_research_parameters_are_pinned() -> None:
    assert leader.MIN_IMPULSE_PCT == Decimal("4")
    assert leader.MIN_RUNAWAY_IMPULSE_PCT == Decimal("5")
    assert leader.MAX_PULLBACK_RETRACE == Decimal("0.80")
    assert leader.MAX_ENTRY_RISK_PCT == Decimal("12")
    assert leader.MAX_ATR_EXTENSION == Decimal("3")
    assert leader.MIN_BREAKOUT_CLOSE_LOCATION == Decimal("0.40")
    assert leader.MAX_COMPRESSION_WIDTH_RATIO == Decimal("0.75")
    assert leader.REQUIRE_COMPRESSION_ABOVE_EMA20 is False
    assert leader.REQUIRE_COMPRESSION_HOD_BREAK is False
    assert leader.STRUCTURAL_BUFFER_ATR == Decimal("1")
    assert leader.LEADER_LATCH_TTL == timedelta(minutes=120)


def test_gap_through_stop_exits_at_resume_open_instead_of_fabricating_stop_fill() -> None:
    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    sampled = [
        _bar(start, interval="3m", open_="10", high="10.2", low="9.95", close="10.15"),
        _bar(start + timedelta(minutes=3), interval="3m", open_="10.20", high="10.35", low="10.10", close="10.30"),
        _bar(start + timedelta(minutes=6), interval="3m", open_="10.30", high="10.40", low="10.05", close="10.15"),
        # Deliberate halt/data discontinuity: next print resumes six minutes later.
        _bar(start + timedelta(minutes=12), interval="3m", open_="9.50", high="9.70", low="9.40", close="9.60"),
    ]
    ema9 = [None, Decimal("10.15"), Decimal("10.18"), Decimal("10.00")]
    atr14 = [None, Decimal("0.20"), Decimal("0.20"), Decimal("0.25")]

    trade = leader._trade_from_signal(
        sampled,
        ema9,
        atr14,
        signal_index=0,
        mode="momentum_compression",
        stop_reference=Decimal("9.90"),
        entry_atr=Decimal("0.20"),
        force_flat_et=time(15, 55),
    )

    assert trade is not None
    assert trade.entry_price == Decimal("10.20")
    assert trade.exit_reason_code == "LEADER_MOMENTUM_GAP_THROUGH_STOP"
    assert trade.exit_price == Decimal("9.50")


def test_too_wide_initial_risk_is_rejected() -> None:
    start = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
    sampled = [
        _bar(start + timedelta(minutes=3 * index), interval="3m", open_="10", high="10.2", low="9.8", close="10.1")
        for index in range(5)
    ]
    ema9 = [None] * 5
    atr14 = [None] * 5

    trade = leader._trade_from_signal(
        sampled,
        ema9,
        atr14,
        signal_index=0,
        mode="controlled_pullback",
        stop_reference=Decimal("8.50"),
        entry_atr=Decimal("0.20"),
        force_flat_et=time(15, 55),
    )

    assert trade is None
