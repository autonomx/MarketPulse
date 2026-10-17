from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.paper import PaperOrder
from app.apps.trading.strategies.models import GapPullbackConfig
from app.apps.trading.strategy_repository import StrategyProtection, TradingStrategyConfigDocument
from app.apps.trading.strategy_research_outcome_monitor import (
    capture_closed_paper_outcome,
    strategy_research_outcome_monitor_enabled,
)


class FactRepo:
    def __init__(self, features=None):
        self.features = features
        self.as_of = None
        self.saved = []

    def research_features_as_of(self, instrument_id, decision_at):
        self.as_of = (instrument_id, decision_at)
        return self.features

    def save_outcome(self, outcome):
        self.saved.append(outcome)
        return True


def _order(order_id: str, side: str, price: str, at: datetime) -> PaperOrder:
    return PaperOrder(
        account_id="paper-1",
        order_id=order_id,
        instrument_id="equity:NASDAQ:XYZ",
        binding_id="alpaca_iex:rest:equity:NASDAQ:XYZ",
        side=side,
        order_type="market",
        quantity=Decimal("100"),
        reference_price=Decimal(price),
        status="filled",
        filled_quantity=Decimal("100"),
        average_fill_price=Decimal(price),
        idempotency_key=f"idem-{order_id}",
        created_at=at,
        updated_at=at,
    )


def _config() -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id="strategy-1",
        account_id="paper-1",
        strategy_version="1.1.0",
        config=GapPullbackConfig(strategy_version="1.1.0", reward_multiple=Decimal("2")),
    )


def _protection(reason="profit_target") -> StrategyProtection:
    return StrategyProtection(
        strategy_id="strategy-1",
        protection_id="protect-1",
        account_id="paper-1",
        instrument_id="equity:NASDAQ:XYZ",
        entry_order_id="entry-1",
        exit_order_id="exit-1",
        stop_price=Decimal("9"),
        target_price=Decimal("12"),
        quantity=Decimal("100"),
        status="closed",
        trigger_reason=reason,
    )


def test_closed_paper_trade_uses_research_features_as_of_entry_and_records_r():
    entry_at = datetime(2026, 8, 20, 13, 40, tzinfo=timezone.utc)
    exit_at = entry_at + timedelta(minutes=18)
    repo = FactRepo()
    persisted = capture_closed_paper_outcome(
        config=_config(),
        protection=_protection(),
        entry_order=_order("entry-1", "buy", "10", entry_at),
        exit_order=_order("exit-1", "sell", "12", exit_at),
        fact_repository=repo,
    )
    assert persisted is True
    assert repo.as_of == ("equity:NASDAQ:XYZ", entry_at)
    outcome = repo.saved[0]
    assert outcome.r_result == Decimal("2")
    assert outcome.two_r_before_minus_one_r is True
    assert outcome.mfe_r is None and outcome.mae_r is None
    assert "paper_live_mfe_unavailable" in outcome.data_quality_flags
    assert outcome.research_fidelity == "unavailable"


def test_non_closed_or_unfilled_trade_is_not_captured():
    entry_at = datetime(2026, 8, 20, 13, 40, tzinfo=timezone.utc)
    repo = FactRepo()
    protection = _protection().model_copy(update={"status": "active"})
    assert capture_closed_paper_outcome(
        config=_config(), protection=protection,
        entry_order=_order("entry-1", "buy", "10", entry_at),
        exit_order=_order("exit-1", "sell", "12", entry_at + timedelta(minutes=5)),
        fact_repository=repo,
    ) is False
    assert repo.saved == []


def test_outcome_monitor_is_disabled_in_legacy_test_mode(monkeypatch):
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.delenv("OMNIX_TRADING_RESEARCH_OUTCOME_MONITOR_IN_TESTS", raising=False)
    assert strategy_research_outcome_monitor_enabled() is False
