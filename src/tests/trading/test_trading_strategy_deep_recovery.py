from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar
from app.apps.trading.strategies.models import GapPullbackConfig
from app.apps.trading.strategy_deep_recovery import evaluate_deep_recovery_shadow


INSTRUMENT = "equity:NASDAQ:RECOV"
OPEN = datetime(2026, 8, 24, 13, 30, tzinfo=timezone.utc)


def _bar(index: int, open_: str, high: str, low: str, close: str, volume: str = "1000") -> MarketBar:
    start = OPEN + timedelta(minutes=index)
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


def _candidate(**overrides) -> GapperCandidate:
    payload = {
        "instrument_id": INSTRUMENT,
        "binding_id": "fixture:RECOV",
        "previous_close": Decimal("7.50"),
        "premarket_price": Decimal("10.00"),
        "gap_pct": Decimal("33.3333"),
        "premarket_volume": Decimal("30000"),
        "premarket_dollar_volume": Decimal("300000"),
        "tod_rvol": Decimal("8"),
        "market_cap": Decimal("50000000"),
        "float_shares": Decimal("5000000"),
        "spread_bps": Decimal("40"),
        "discovery_rank": 1,
    }
    payload.update(overrides)
    return GapperCandidate(**payload)


def _config() -> GapPullbackConfig:
    return GapPullbackConfig(
        strategy_version="2.0.0",
        structure_interval="1m",
        execution_interval="1m",
        minimum_gap_pct=Decimal("20"),
        minimum_price=Decimal("0.50"),
        maximum_price=Decimal("20"),
        minimum_premarket_dollar_volume=Decimal("100000"),
        minimum_tod_rvol=Decimal("3"),
        maximum_spread_bps=Decimal("150"),
        require_catalyst_evidence=False,
        stop_buffer_bps=Decimal("15"),
        entry_start_et=time(9, 35),
        last_entry_et=time(11, 30),
    )


def _recovery_prefix(final_close: str = "9.80") -> list[MarketBar]:
    # First attained opening high is 10.00. The path then sells to 7.50 and
    # recovers. The final bar closes above both VWAP and the prior three highs.
    rows = [
        ("9.90", "10.00", "9.80", "9.90"),
        ("9.90", "9.95", "9.40", "9.50"),
        ("9.50", "9.55", "8.90", "9.00"),
        ("9.00", "9.05", "8.40", "8.50"),
        ("8.50", "8.55", "7.90", "8.00"),
        ("8.00", "8.10", "7.50", "7.70"),
        ("7.70", "7.90", "7.60", "7.85"),
        ("7.85", "8.10", "7.80", "8.05"),
        ("8.05", "8.30", "8.00", "8.25"),
        ("8.25", "8.45", "8.20", "8.40"),
        ("8.40", "8.60", "8.35", "8.55"),
        ("8.55", "8.75", "8.50", "8.70"),
        ("8.70", "8.90", "8.65", "8.85"),
        ("8.85", "9.05", "8.80", "9.00"),
        ("9.00", "9.15", "8.95", "9.10"),
        ("9.10", "9.20", "9.00", "9.15"),
        ("9.15", "9.30", "9.10", "9.25"),
        ("9.25", "9.40", "9.20", "9.35"),
        ("9.35", "9.50", "9.30", "9.45"),
        ("9.45", "9.60", "9.40", "9.55"),
        ("9.55", "9.90", "9.50", final_close),
    ]
    return [_bar(index, *row) for index, row in enumerate(rows)]


def test_deep_recovery_reuses_frozen_v2_hard_candidate_gates() -> None:
    result = evaluate_deep_recovery_shadow(
        _candidate(premarket_dollar_volume=Decimal("50000")),
        _recovery_prefix(),
        _config(),
    )

    assert result.state == "hard_gate_rejected"
    assert result.reason_code == "PREMARKET_DOLLAR_VOLUME_LOW"
    assert result.execution_authority is False


def test_deep_recovery_emits_shadow_signal_only_after_observed_30pct_recovery_breakout() -> None:
    result = evaluate_deep_recovery_shadow(_candidate(), _recovery_prefix(), _config())

    assert result.state == "signal_ready"
    assert result.reason_code == "DEEP_RECOVERY_30PCT_CONTINUATION_SHADOW"
    assert result.recovery_pct is not None and result.recovery_pct >= Decimal("30")
    assert result.selloff_pct is not None and result.selloff_pct >= Decimal("5")
    assert result.breakout_confirmed is True
    assert result.session_vwap is not None
    assert result.vwap_distance_pct is not None and result.vwap_distance_pct > 0
    assert result.research_stop_price is not None
    assert result.research_risk_pct is not None and result.research_risk_pct > 0
    assert result.execution_authority is False


def test_deep_recovery_does_not_anticipate_future_recovery() -> None:
    bars = _recovery_prefix(final_close="9.20")
    # Keep the final bar internally valid while leaving the observed close below
    # 30% above the known 7.50 trough.
    bars[-1] = _bar(20, "9.10", "9.25", "9.05", "9.20")
    result = evaluate_deep_recovery_shadow(_candidate(), bars, _config())

    assert result.state == "waiting_recovery"
    assert result.reason_code == "WAITING_FOR_30PCT_RECOVERY"
    assert result.recovery_pct is not None and result.recovery_pct < Decimal("30")
    assert result.execution_authority is False
