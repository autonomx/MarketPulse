from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_ai_shadow_v3 import (
    AIShadowV3Decision,
    GeometrySuggestion,
    agreement_cohort,
    build_authoritative_runner_geometry,
    build_v3_feature_snapshot,
    compare_runner_geometry_challenger,
)


ET = ZoneInfo("America/New_York")
INSTRUMENT = "equity:NASDAQ:TEST"


def _bars(count: int = 40):
    start = datetime(2026, 9, 17, 9, 30, tzinfo=ET)
    rows = []
    price = Decimal("10")
    for i in range(count):
        at = start + timedelta(minutes=i)
        close = price + Decimal(i) * Decimal("0.01")
        rows.append(
            MarketBar(
                instrument_id=INSTRUMENT,
                interval="1m",
                start_time=at.astimezone(timezone.utc),
                end_time=(at + timedelta(minutes=1)).astimezone(timezone.utc),
                open=close - Decimal("0.02"),
                high=close + Decimal("0.05"),
                low=close - Decimal("0.05"),
                close=close,
                volume=Decimal("1000") + Decimal(i * 10),
                provider="test",
                session="regular",
            )
        )
    return rows


def test_v3_llm_geometry_is_advisory_not_required_for_enter():
    decision = AIShadowV3Decision(
        instrument_id=INSTRUMENT,
        setup_family="failed_selloff_reclaim",
        state="enter",
        quality_score=88,
        geometry_suggestion=GeometrySuggestion(
            invalidation_price=Decimal("1"),
            target_1=Decimal("100"),
        ),
        thesis="failed selloff reclaimed",
    )
    assert decision.state == "enter"
    assert decision.execution_authority is False


def test_runner_geometry_is_deterministically_built_from_market_structure():
    bars = _bars()
    snapshot = build_v3_feature_snapshot(
        bars,
        instrument_id=INSTRUMENT,
        session_date=datetime(2026, 9, 17).date(),
        observed_at=bars[-1].end_time + timedelta(seconds=5),
    )
    geometry = build_authoritative_runner_geometry(
        setup_family="failed_selloff_reclaim",
        bars=bars,
        feature_snapshot=snapshot,
        estimated_cost_bps=Decimal("110"),
    )
    assert geometry.runner_policy == "structure_trail"
    assert geometry.target_2 is not None
    assert geometry.invalidation_price < geometry.entry_reference
    assert geometry.target_2 > geometry.target_1


def test_challenger_records_disagreement_without_changing_champion():
    bars = _bars()
    snapshot = build_v3_feature_snapshot(
        bars,
        instrument_id=INSTRUMENT,
        session_date=datetime(2026, 9, 17).date(),
        observed_at=bars[-1].end_time + timedelta(seconds=5),
    )
    record = compare_runner_geometry_challenger(
        instrument_id=INSTRUMENT,
        v2_decision={
            "setup_family": "failed_selloff_reclaim",
            "invalidation_price": "9.50",
            "target_1": "10.41",
            "target_2": None,
        },
        bars=bars,
        feature_snapshot=snapshot,
        estimated_cost_bps=Decimal("50"),
        minimum_net_r=Decimal("2"),
    )
    assert record.champion_action == "VETO"
    assert record.challenger_action in {"ENTER", "VETO"}
    assert record.challenger_geometry is not None


def test_agreement_cohorts_do_not_claim_predictor_independence():
    assert agreement_cohort(v1_action="enter", v2_state="enter") == "both_agree"
    assert agreement_cohort(v1_action="skip", v2_state="enter") == "v2_only"
