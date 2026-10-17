from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.execution import ExecutionObservation
from app.apps.trading.paper import PaperAccount, PaperAccountSnapshot, PaperBalance, PaperPosition
from app.apps.trading.paper_protection import PaperPositionProtection
from app.apps.trading.strategy_operations_health import account_risk_health, execution_health, operational_health


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


def _snapshot(*, protected_cash: str = "98000") -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        account=PaperAccount(
            account_id="paper-1",
            name="Paper",
            base_currency="USD",
            commission_bps=Decimal("0"),
        ),
        balances=[PaperBalance(currency="USD", available=Decimal(protected_cash))],
        positions=[
            PaperPosition(
                instrument_id="equity:NYSE:TEST",
                quantity=Decimal("100"),
                average_cost=Decimal("20"),
                realized_pnl=Decimal("0"),
                last_price=Decimal("20"),
            )
        ],
        open_orders=[],
        order_history=[],
        recent_fills=[],
        recent_ledger=[],
    )


def _protection() -> PaperPositionProtection:
    return PaperPositionProtection(
        account_id="paper-1",
        instrument_id="equity:NYSE:TEST",
        binding_id="alpaca_iex:TEST",
        stop_loss=Decimal("19"),
        status="active",
    )


def test_account_risk_health_uses_stop_risk_and_daily_headroom() -> None:
    result = account_risk_health(
        snapshot=_snapshot(),
        manual_protections=[_protection()],
        strategy_protections=[],
        strategy_configs=[],
        daily_realized_pnl=Decimal("-200"),
    )
    assert result.state == "healthy"
    assert result.policy_source == "paper_default"
    assert result.equity == Decimal("100000")
    assert result.open_risk_dollars == Decimal("100")
    assert result.open_risk_pct == Decimal("0.100")
    assert result.daily_loss_limit_dollars == Decimal("1500")
    assert result.daily_loss_remaining == Decimal("1300")
    assert result.unprotected_exposure_count == 0


def test_account_risk_health_fails_closed_on_unprotected_position() -> None:
    result = account_risk_health(
        snapshot=_snapshot(),
        manual_protections=[],
        strategy_protections=[],
        strategy_configs=[],
        daily_realized_pnl=Decimal("0"),
    )
    assert result.state == "blocked"
    assert result.reason_codes == ("UNPROTECTED_OPEN_EXPOSURE",)
    assert result.unprotected_exposure_count == 1


def test_execution_health_preserves_provider_eligibility_evidence() -> None:
    source = NOW - timedelta(seconds=4, milliseconds=500)
    observation = ExecutionObservation(
        instrument_id="equity:NYSE:TEST",
        binding_id="alpaca_iex:TEST",
        provider="alpaca_iex",
        bid=Decimal("19.99"),
        ask=Decimal("20.01"),
        last=Decimal("20"),
        source_time=source,
        received_at=NOW,
        session="regular",
        freshness_mode="polled",
        execution_eligible=True,
        rejection_reasons=(),
    )
    result = execution_health(
        observation,
        instrument_id=observation.instrument_id,
        requested_binding_id="alpaca_iex:TEST",
        observed_at=NOW,
    )
    assert result.state == "degraded"
    assert result.execution_eligible is True
    assert result.reason_codes == ("EXECUTION_DATA_NEAR_STALE",)
    assert result.observation_age_ms == Decimal("4500.0")
    assert result.provider == "alpaca_iex"


def test_operational_health_is_blocked_if_execution_is_ineligible() -> None:
    risk = account_risk_health(
        snapshot=_snapshot(),
        manual_protections=[_protection()],
        strategy_protections=[],
        strategy_configs=[],
        daily_realized_pnl=Decimal("0"),
    )
    execution = execution_health(
        None,
        instrument_id="equity:NYSE:TEST",
        requested_binding_id="alpaca_iex:TEST",
        error="provider unavailable",
        observed_at=NOW,
    )
    result = operational_health(observed_at=NOW, risk=risk, execution=execution)
    assert result.state == "blocked"
    assert "EXECUTION_DATA_UNAVAILABLE" in result.reason_codes
    assert result.paper_only is True
    assert result.live_broker_enabled is False
    assert result.ai_order_placement_enabled is False
