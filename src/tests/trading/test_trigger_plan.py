from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import app.apps.trading.trigger_plan as trigger_plan_module

from app.apps.trading.trigger_plan import (
    AuthoritativeTradeGeometry,
    TriggerCondition,
    TriggerMarketSnapshot,
    TriggerPlan,
    TriggerPlanOrigin,
    evaluate_armed_trigger,
    transition_trigger_plan,
)


NOW = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)


def _plan() -> TriggerPlan:
    return TriggerPlan(
        trigger_plan_id="plan-1",
        strategy_id="strategy",
        arm_id="ai-v3",
        instrument_id="equity:NASDAQ:TEST",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
        trigger=TriggerCondition(
            trigger_type="bar_close_above",
            price=Decimal("10.20"),
        ),
        geometry=AuthoritativeTradeGeometry(
            entry_reference=Decimal("10.20"),
            invalidation_price=Decimal("9.90"),
            target_1=Decimal("10.80"),
            target_2=Decimal("11.10"),
            risk_per_share=Decimal("0.30"),
            net_r_target_1=Decimal("2"),
            net_r_target_2=Decimal("3"),
            runner_policy="structure_trail",
        ),
        max_spread_bps=Decimal("300"),
        origin=TriggerPlanOrigin(
            decision_id="decision-1",
            setup_family="failed_selloff_reclaim",
            thesis="reclaim",
            policy_version="ai-shadow-v3",
        ),
    )


def test_trigger_plan_transitions_without_another_llm_call():
    plan = _plan()
    snapshot = TriggerMarketSnapshot(
        observed_at=NOW + timedelta(minutes=1),
        current_price=Decimal("10.25"),
        session_high=Decimal("10.40"),
        bar_high=Decimal("10.30"),
        bar_low=Decimal("10.05"),
        bar_close=Decimal("10.25"),
    )
    triggered = evaluate_armed_trigger(plan, snapshot)
    assert triggered.status == "TRIGGERED"
    checking = transition_trigger_plan(
        triggered,
        status="EXECUTION_CHECK",
        reason="quote_required",
        observed_at=snapshot.observed_at,
    )
    assert checking.status == "EXECUTION_CHECK"


def test_same_ohlc_bar_trigger_and_stop_is_unresolved_not_hindsight_ordered():
    plan = _plan()
    snapshot = TriggerMarketSnapshot(
        observed_at=NOW + timedelta(minutes=1),
        current_price=Decimal("10.25"),
        session_high=Decimal("10.40"),
        bar_high=Decimal("10.30"),
        bar_low=Decimal("9.80"),
        bar_close=Decimal("10.25"),
    )
    result = evaluate_armed_trigger(plan, snapshot)
    assert result.status == "TRIGGER_ORDER_UNRESOLVED"


def test_terminal_trigger_plan_cannot_be_reopened():
    plan = _plan().model_copy(update={"status": "INVALIDATED"})
    with pytest.raises(ValueError, match="terminal_trigger_plan"):
        transition_trigger_plan(
            plan,
            status="TRIGGERED",
            reason="bad",
            observed_at=NOW + timedelta(minutes=1),
        )


def test_trigger_plan_db_row_roundtrip_preserves_spread_authority():
    plan = _plan()
    row = (
        plan.trigger_plan_id,
        plan.strategy_id,
        plan.arm_id,
        plan.instrument_id,
        plan.status,
        plan.created_at,
        plan.expires_at,
        plan.trigger.model_dump(mode="json"),
        plan.geometry.model_dump(mode="json"),
        list(plan.required_certificate_ids),
        plan.max_spread_bps,
        plan.origin.model_dump(mode="json"),
        plan.transition_reason,
        plan.revision,
        plan.updated_at or NOW,
    )
    restored = trigger_plan_module._row_to_plan(row)
    assert restored.max_spread_bps == Decimal("300")
    assert restored.origin.decision_id == plan.origin.decision_id


def test_bar_close_trigger_does_not_fall_back_to_intrabar_price():
    plan = _plan()
    snapshot = TriggerMarketSnapshot(
        observed_at=NOW + timedelta(minutes=1),
        current_price=Decimal("10.30"),
        session_high=Decimal("10.40"),
        bar_high=Decimal("10.35"),
        bar_low=Decimal("10.05"),
        bar_close=None,
    )

    result = evaluate_armed_trigger(plan, snapshot)

    assert result.status == "ARMED"
    assert result.revision == plan.revision
