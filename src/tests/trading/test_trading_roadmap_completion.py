from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.apps.trading.catalyst_evidence import capture_catalyst_evidence, dilution_flags
from app.apps.trading.models import MarketBar
from app.apps.trading.paper_analytics import lifecycle_funnel
from app.apps.trading.strategy_monitor import _rsi_crossed_after_activation
from app.apps.trading.strategy_repository import StrategyEvent


def _bar(index: int, close: str) -> MarketBar:
    start = datetime(2026, 8, 24, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=index)
    value = Decimal(close)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST", interval="1m", start_time=start,
        end_time=start + timedelta(minutes=1), open=value, high=value, low=value,
        close=value, volume=Decimal("100"), is_final=True, session="regular",
        provider="fixture", received_at=start + timedelta(minutes=1),
    )


def test_supply_flags_require_resolved_active_state() -> None:
    assert dilution_flags("The company terminated its at-the-market program and it is no longer available.") == ()
    assert dilution_flags("The company signed a distribution sales agreement with a retailer.") == ()
    assert "atm" in dilution_flags("The company entered into an at-the-market offering that remains available.")
    evidence = capture_catalyst_evidence(
        evidence_id="ev-1", instrument_id="equity:NASDAQ:TEST", source_type="sec",
        source_locator="fixture", published_at=datetime.now(timezone.utc),
        raw_text="The prior ATM facility was exhausted and no longer available.",
    )
    facts = evidence.facts["supply_facts"]
    assert isinstance(facts, list)
    assert facts[0]["status"] == "exhausted"
    assert evidence.dilution_flags == ()


def test_funnel_does_not_promote_feature_payload_to_structure() -> None:
    observed = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    event = StrategyEvent(
        strategy_id="s", event_id="e", instrument_id="equity:NASDAQ:TEST",
        event_type="state", state="rejected", reason_code="GAP_BELOW_MINIMUM",
        observed_at=observed, idempotency_key="i",
        payload={"features": {"quality_score": 2}, "transitions": ["discovered"], "universe_id": "u"},
    )
    funnel = {stage.stage: stage.count for stage in lifecycle_funnel([event])}
    assert funnel["DISCOVERED"] == 1
    assert funnel["BASIC MARKET FILTER PASS"] == 0
    assert funnel["STRUCTURE FORMED"] == 0


def test_live_rsi_helper_matches_causal_cross_contract() -> None:
    bars = [_bar(0, "1"), _bar(1, "2"), _bar(2, "3"), _bar(3, "2"), _bar(4, "1")]
    assert _rsi_crossed_after_activation(
        bars, period=2, threshold=Decimal("70"),
        activated_at=bars[2].start_time, observed_at=bars[-1].end_time,
    ) is True


def test_completion_migration_preserves_initial_risk_and_protection_snapshots() -> None:
    migration = Path("src/app/apps/trading/migrations/0044_trading_roadmap_completion.sql").read_text()
    for token in (
        "archived_at", "initial_stop_price", "mae_price", "mfe_price",
        "trg_omnix_trading_strategy_protection_equity",
        "trg_zz_omnix_trading_strategy_trade_metrics",
    ):
        assert token in migration
    monitor = "\n".join(path.read_text(encoding="utf-8") for path in [*sorted(Path("src/app/apps/trading").glob("strategy_monitor*.py")), Path("src/app/apps/trading/strategy_entry_path.py")])
    assert 'trigger = "rsi"' in monitor
    assert "relative_strength_index" in monitor
