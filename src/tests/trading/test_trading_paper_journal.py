from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.persistence.tenant import TenantContext
from app.apps.trading.paper_journal import TradingPaperJournal, automatic_trade_observations


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""
        self.params = ()

    def execute(self, query, params=()):
        self.query = str(query)
        self.params = params
        return _Result(self.rows)


class _Uow:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_automatic_trade_observations_are_factual_and_deterministic() -> None:
    observations = automatic_trade_observations(
        realized_pnl=Decimal("125.50"),
        r_result=Decimal("1.25"),
        mae_r=Decimal("-0.40"),
        mfe_r=Decimal("1.80"),
        signal_to_executable_bps=Decimal("12.3"),
        fill_slippage_bps=Decimal("4.5"),
        implementation_shortfall_bps=Decimal("16.8"),
        initial_stop=Decimal("0.54"),
        initial_target=Decimal("0.72"),
        initial_risk_dollars=Decimal("100"),
        holding_seconds=960,
        exit_reason="take_profit",
        setup_features={"quality_score": 8, "gap_pct": "42", "l1": "0.51", "b1": "0.61", "l2": "0.55"},
    )

    assert observations == [
        "Outcome: win; realized P&L +125.50; +1.250R",
        "Holding time: 960 seconds; exit reason: take_profit",
        "Excursion: MAE -0.400R; MFE +1.800R",
        "Execution: signal→executable +12.30 bps; fill slippage +4.50 bps; implementation shortfall +16.80 bps",
        "Initial plan: stop 0.5400; target 0.7200; risk $100.00",
        "Setup snapshot: quality 8; gap 42%; L1 0.51; B1 0.61; L2 0.55",
    ]


def test_journal_reads_existing_canonical_trade_and_lifecycle_events() -> None:
    entry_time = datetime(2026, 8, 24, 13, 50, tzinfo=timezone.utc)
    exit_time = datetime(2026, 8, 24, 14, 6, tzinfo=timezone.utc)
    rows = [(
        "trade-1", "paper-1", "epoch-0001", "gap-v2", "2.0.0", 12, "run-1",
        "profile-1", "universe-1", "equity:NASDAQ:OSRH", date(2026, 8, 24),
        entry_time, exit_time, 960, "signal-event-1", "entry-order-1", "exit-order-1",
        ["entry-fill-1"], ["exit-fill-1"], "session-1", "setup-1", "intent-1", "risk-1",
        "protection-1", "closed", "pending", Decimal("0.60"), Decimal("0.72"), Decimal("1000"),
        Decimal("100"), Decimal("0.54"), Decimal("0.72"), Decimal("120"), Decimal("1.2"),
        Decimal("-0.3"), Decimal("1.4"), Decimal("10"), Decimal("4"), Decimal("14"),
        "take_profit", {"quality_score": 8, "gap_pct": "42"}, {"provider": "alpaca_iex"},
        [{
            "event_id": "signal-event-1",
            "run_id": "run-1",
            "event_type": "entry_order_submitted",
            "state": "entry_ready",
            "reason_code": "RISK_ACCEPTED",
            "observed_at": entry_time.isoformat(),
        }],
    )]
    connection = _Connection(rows)
    context = TenantContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="membership-1",
        roles=frozenset({"owner"}),
    )
    journal = TradingPaperJournal(context=context, uow_factory=lambda: _Uow(connection))

    response = journal.list_entries(
        "paper-1",
        strategy_id="gap-v2",
        start_date=date(2026, 8, 24),
        end_date=date(2026, 8, 24),
        limit=25,
    )

    assert response.account_id == "paper-1"
    assert len(response.entries) == 1
    entry = response.entries[0]
    assert entry.trade_id == "trade-1"
    assert entry.setup_id == "setup-1"
    assert entry.trade_intent_id == "intent-1"
    assert entry.risk_decision_id == "risk-1"
    assert entry.entry_fill_ids == ["entry-fill-1"]
    assert entry.exit_fill_ids == ["exit-fill-1"]
    assert entry.outcome == "win"
    assert entry.review_state == "pending"
    assert entry.events[0].event_id == "signal-event-1"
    assert entry.automatic_observations[0].startswith("Outcome: win")
    assert "omnix_trading_paper_trade_records AS trade" in connection.query
    assert "omnix_trading_strategy_events AS event" in connection.query
    assert connection.params[-1] == 25
