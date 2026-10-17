from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work
from app.apps.trading import strategy_monitor as strategy_monitor_module
from app.apps.trading import (
    strategy_monitor_candidates,
    strategy_monitor_config_run,
    strategy_monitor_diagnostics,
    strategy_monitor_intraday_llm,
    strategy_monitor_protections,
)

strategy_monitor_modules = (
    strategy_monitor_module,
    strategy_monitor_candidates,
    strategy_monitor_config_run,
    strategy_monitor_diagnostics,
    strategy_monitor_intraday_llm,
    strategy_monitor_protections,
)
from app.apps.trading import order_gateway as hardening_module
from app.apps.trading.execution import ExecutionObservation
from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.market_evidence import (
    MARKET_EVIDENCE_POLICY_VERSION,
    PremarketLiquidityEvidence,
    SourceMemberDisposition,
)
from app.apps.trading.models import MarketBar
from app.apps.trading.paper import PaperAccountCreate, PaperMarketObservation
from app.apps.trading.paper_repository import TradingPaperRepository
from app.apps.trading.strategies import evaluate_gap_pullback
from app.apps.trading.strategies.models import StrategyRiskProfile
from app.apps.trading.strategy_data_integrity import finviz_atomic_source_locator
from app.apps.trading.strategy_monitor import TradingStrategyMonitor
from app.apps.trading.strategy_repository import (
    StrategyEvent,
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
)
from app.apps.trading.strategy_universe_archiver import _archive_universe_id
from app.apps.trading.strategy_v2_qualification import (
    PROSPECTIVE_ECONOMIC_POLICY_VERSION,
    V2_PROSPECTIVE_START,
    V2_QUALIFICATION_EVENT_TYPES,
    V2_QUALIFICATION_VERSION,
    V2_REPLAY_VERSION,
    evaluate_v2_prospective_qualification,
    managed_finviz_v2_config,
    v2_profile_fingerprint,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("OMNIX_TEST_DATABASE_URL"),
    reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)

FIXTURE_PATH = Path(__file__).parent.parent / "trading" / "fixtures" / "2026-09-03-auto-paper-e2e.json"
REPLAY_SESSION_DATE = date(2026, 10, 1)
REPLAY_CAPTURE_UTC = datetime(2026, 10, 1, 13, 16, 18, tzinfo=timezone.utc)
REPLAY_OPEN_UTC = datetime(2026, 10, 1, 13, 30, tzinfo=timezone.utc)
REPLAY_RUNTIME_NOW = datetime(2026, 10, 1, 13, 41, tzinfo=timezone.utc)
_ET = ZoneInfo("America/New_York")


def _database() -> PostgresDatabase:
    return PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=4,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-auto-paper-e2e-tests",
        )
    )


def _frozen_datetime(now: datetime):
    class FrozenRuntimeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    return FrozenRuntimeDateTime


def _event(*, strategy_id, event_type, instrument_id, observed_at, reason_code, payload, suffix):
    raw = f"{strategy_id}|{event_type}|{instrument_id}|{observed_at.isoformat()}|{suffix}"
    idem = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return StrategyEvent(
        strategy_id=strategy_id,
        event_id=idem[:32],
        instrument_id=instrument_id,
        event_type=event_type,
        state="entry_ready",
        reason_code=reason_code,
        observed_at=observed_at,
        idempotency_key=idem,
        payload=payload,
    )


def _seed_qualification(repository: TradingStrategyRepository, config: TradingStrategyConfigDocument) -> None:
    profile = v2_profile_fingerprint(config.config)
    sessions: list[date] = []
    cursor = V2_PROSPECTIVE_START
    while len(sessions) < 20:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)

    for index, session in enumerate(sessions):
        instrument = f"equity:QUAL{index % 10}"
        live_at = datetime.combine(session, time(14, 0), tzinfo=timezone.utc)
        entry_at = live_at + timedelta(minutes=1)
        universe_id = f"qualification-{session.isoformat()}-{index}"
        repository.append_event(
            _event(
                strategy_id=config.strategy_id,
                event_type="shadow_execution",
                instrument_id=instrument,
                observed_at=live_at,
                reason_code="SHADOW_EXECUTION_OBSERVED",
                suffix=f"live-{index}",
                payload={
                    "universe_source": "auto_archive_shadow",
                    "profile_fingerprint": profile,
                    "execution_authority": False,
                    "execution": {"execution_eligible": True},
                },
            )
        )
        repository.append_event(
            _event(
                strategy_id=config.strategy_id,
                event_type="v2_shadow_replay_trade",
                instrument_id=instrument,
                observed_at=entry_at + timedelta(hours=2),
                reason_code="V2_SHADOW_REPLAY_TRADE",
                suffix=f"replay-{index}",
                payload={
                    "qualification_version": V2_QUALIFICATION_VERSION,
                    "replay_version": V2_REPLAY_VERSION,
                    "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
                    "session_date": session.isoformat(),
                    "universe_id": universe_id,
                    "universe_source": "auto_archive_shadow",
                    "profile_fingerprint": profile,
                    "entry_time": entry_at.isoformat(),
                    "r_result": "0.50",
                    "execution_authority": False,
                },
            )
        )
        repository.append_event(
            _event(
                strategy_id=config.strategy_id,
                event_type="v2_shadow_replay_session",
                instrument_id=f"strategy:{config.strategy_id}",
                observed_at=entry_at + timedelta(hours=2, minutes=1),
                reason_code="V2_SHADOW_REPLAY_COMPLETED",
                suffix=f"session-{index}",
                payload={
                    "qualification_version": V2_QUALIFICATION_VERSION,
                    "replay_version": V2_REPLAY_VERSION,
                    "market_evidence_policy_version": MARKET_EVIDENCE_POLICY_VERSION,
                    "session_date": session.isoformat(),
                    "status": "completed",
                    "qualification_eligible": True,
                    "profile_fingerprint": profile,
                    "execution_authority": False,
                },
            )
        )

    review_at = max(
        event.observed_at
        for event in repository.events_by_types_between(
            config.strategy_id,
            event_types=V2_QUALIFICATION_EVENT_TYPES,
            start_time=datetime.combine(V2_PROSPECTIVE_START, time.min, tzinfo=timezone.utc),
            end_time=REPLAY_RUNTIME_NOW,
            limit=20_000,
        )
    ) + timedelta(minutes=1)
    repository.append_event(
        _event(
            strategy_id=config.strategy_id,
            event_type="prospective_economic_auto_paper_review",
            instrument_id=f"strategy:{config.strategy_id}",
            observed_at=review_at,
            reason_code="PROSPECTIVE_ECONOMIC_AUTO_PAPER_REVIEW_APPROVED",
            suffix="economic-review",
            payload={
                "policy_version": PROSPECTIVE_ECONOMIC_POLICY_VERSION,
                "v2_profile_fingerprint": profile,
                "pipeline_evidence_fingerprint": "postgres-market-evidence-v2",
                "approved": True,
                "execution_authority": False,
            },
        )
    )
    events = repository.events_by_types_between(
        config.strategy_id,
        event_types=V2_QUALIFICATION_EVENT_TYPES,
        start_time=datetime.combine(V2_PROSPECTIVE_START, time.min, tzinfo=timezone.utc),
        end_time=REPLAY_RUNTIME_NOW,
        limit=20_000,
    )
    before = evaluate_v2_prospective_qualification(config, events)
    assert before.qualified is True
    assert before.reviewed is False
    repository.append_event(
        _event(
            strategy_id=config.strategy_id,
            event_type="v2_promotion_review",
            instrument_id=f"strategy:{config.strategy_id}",
            observed_at=review_at + timedelta(minutes=1),
            reason_code="V2_PROMOTION_REVIEW_APPROVED",
            suffix="operator-review",
            payload={
                "qualification_version": V2_QUALIFICATION_VERSION,
                "profile_fingerprint": profile,
                "evidence_fingerprint": before.evidence_fingerprint,
                "approved": True,
                "execution_authority": False,
            },
        )
    )


class ReplayMarketService:
    def __init__(self, *, instrument_id: str, binding_id: str, bars: list[MarketBar], assumptions: dict[str, object]) -> None:
        self.instrument_id = instrument_id
        self.binding_id = binding_id
        self._bars = bars
        self.execution = ExecutionObservation(
            instrument_id=instrument_id,
            binding_id=binding_id,
            provider="alpaca_iex",
            bid=Decimal(str(assumptions["execution_bid"])),
            ask=Decimal(str(assumptions["execution_ask"])),
            bid_size=Decimal(str(assumptions["displayed_size"])),
            ask_size=Decimal(str(assumptions["displayed_size"])),
            last=Decimal(str(assumptions["execution_last"])),
            high=Decimal(str(assumptions["execution_ask"])),
            low=Decimal(str(assumptions["execution_bid"])),
            bar_volume=Decimal("100000"),
            bar_start_time=REPLAY_RUNTIME_NOW - timedelta(minutes=1),
            cumulative_volume=Decimal("2500000"),
            source_time=REPLAY_RUNTIME_NOW,
            received_at=REPLAY_RUNTIME_NOW,
            session="regular",
            freshness_mode="polled",
            halted=False,
            execution_eligible=True,
        )

    def bars(self, instrument_id, interval, limit, binding_id):
        assert instrument_id == self.instrument_id
        assert interval == "1m"
        assert binding_id == self.binding_id
        return SimpleNamespace(bars=list(self._bars))

    def execution_observation(self, instrument_id, binding_id=None):
        assert instrument_id == self.instrument_id
        assert binding_id in {None, self.binding_id}
        return self.execution


@pytest.mark.anyio
async def test_postgres_auto_paper_monitor_persists_authorization_order_fill_and_position(monkeypatch) -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    selected = fixture["selected"]
    assumptions = fixture["execution_assumptions"]
    assert isinstance(selected, dict)
    assert isinstance(assumptions, dict)

    suffix = uuid.uuid4().hex[:10]
    strategy_id = f"market-evidence-v2-postgres-{suffix}"
    account_id = f"paper-market-evidence-v2-{suffix}"
    instrument_id = f"equity:TLYS:{suffix}"
    binding_id = f"replay:TLYS:{suffix}"
    config_value = managed_finviz_v2_config()
    config = TradingStrategyConfigDocument(
        strategy_id=strategy_id,
        account_id=account_id,
        strategy_kind="gap_pullback_v1",
        strategy_version="2.0.0",
        mode="auto_paper",
        active_universe_id=None,
        config=config_value,
        risk=StrategyRiskProfile(),
        enabled=True,
    )

    liquidity = PremarketLiquidityEvidence(
        policy_version=MARKET_EVIDENCE_POLICY_VERSION,
        provider="alpaca_iex",
        feed="iex",
        observed_at=REPLAY_CAPTURE_UTC,
        current_premarket_volume=Decimal(str(assumptions["premarket_volume"])),
        current_premarket_dollar_volume=Decimal(str(assumptions["premarket_dollar_volume"])),
        tod_rvol=Decimal(str(assumptions["tod_rvol"])),
        tod_rvol_numerator=Decimal(str(assumptions["premarket_volume"])),
        tod_rvol_denominator_mean=Decimal(str(assumptions["premarket_volume"])) / Decimal(str(assumptions["tod_rvol"])),
        baseline_session_count=5,
        premarket_bar_count=8,
        nonzero_volume_bar_count=8,
        coverage_ratio=Decimal("1"),
        ready=True,
    )
    candidate = GapperCandidate(
        instrument_id=instrument_id,
        binding_id=binding_id,
        observed_at=REPLAY_CAPTURE_UTC,
        evidence_observed_at={"premarket_liquidity:alpaca_iex:iex": REPLAY_CAPTURE_UTC},
        previous_close=Decimal(str(selected["previous_close"])),
        premarket_price=Decimal(str(selected["premarket_price"])),
        gap_pct=Decimal(str(selected["gap_pct"])),
        premarket_volume=liquidity.current_premarket_volume,
        premarket_dollar_volume=liquidity.current_premarket_dollar_volume,
        premarket_bar_count=liquidity.premarket_bar_count,
        tod_rvol=liquidity.tod_rvol,
        premarket_liquidity=liquidity,
        market_evidence_policy_version=MARKET_EVIDENCE_POLICY_VERSION,
        market_data_complete=True,
        market_cap=Decimal(str(selected["market_cap"])),
        spread_bps=Decimal(str(assumptions["candidate_spread_bps"])),
        discovery_rank=1,
    )

    bars = []
    for row in fixture["replay_bars"]:
        start = REPLAY_OPEN_UTC + timedelta(minutes=int(row["minute"]))
        bars.append(
            MarketBar(
                instrument_id=instrument_id,
                interval="1m",
                start_time=start,
                end_time=start + timedelta(minutes=1),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                is_final=True,
                session="regular",
                provider="yahoo",
                received_at=start + timedelta(minutes=1),
            )
        )
    assert evaluate_gap_pullback(candidate, bars, config.config).state == "entry_ready"

    marker = datetime.combine(REPLAY_SESSION_DATE, config.config.universe_scan_time_et, tzinfo=_ET)
    symbols = [str(item) for item in fixture["source_candidate_symbols"]]
    dispositions = [
        SourceMemberDisposition(
            symbol=symbols[0],
            source_rank=1,
            status="materialized",
            instrument_id=instrument_id,
        ),
        *[
            SourceMemberDisposition(
                symbol=symbol,
                source_rank=index,
                status="filtered_gap",
                reason_codes=("GAP_BELOW_MINIMUM",),
            )
            for index, symbol in enumerate(symbols[1:], start=2)
        ],
    ]
    universe = freeze_gapper_universe(
        universe_id=_archive_universe_id(config, marker),
        session_date=REPLAY_SESSION_DATE,
        evaluation_time=REPLAY_CAPTURE_UTC,
        discovery_source="finviz",
        source_locator=finviz_atomic_source_locator(str(fixture["source_url"])),
        source_candidate_symbols=symbols,
        source_member_dispositions=dispositions,
        candidates=[candidate],
    )

    database = _database()
    try:
        context = ensure_local_identity(database)

        def uow_factory():
            return unit_of_work(database)

        strategy_repository = TradingStrategyRepository(context=context, uow_factory=uow_factory)
        paper_repository = TradingPaperRepository(context=context, uow_factory=uow_factory)
        paper_repository.create_account(
            PaperAccountCreate(
                account_id=account_id,
                name="Market evidence V2 PostgreSQL E2E",
                initial_cash=Decimal("10000"),
            )
        )
        strategy_repository.create_config(config)
        strategy_repository.save_universe(universe)
        persisted_universe = strategy_repository.get_universe(universe.universe_id)
        assert persisted_universe.source_candidate_symbols == universe.source_candidate_symbols
        assert persisted_universe.source_member_dispositions == universe.source_member_dispositions
        _seed_qualification(strategy_repository, config)
        authorized_events = strategy_repository.events_by_types_between(
            strategy_id,
            event_types=V2_QUALIFICATION_EVENT_TYPES,
            start_time=datetime.combine(V2_PROSPECTIVE_START, time.min, tzinfo=timezone.utc),
            end_time=REPLAY_RUNTIME_NOW,
            limit=20_000,
        )
        assert evaluate_v2_prospective_qualification(config, authorized_events).auto_paper_authorized is True

        market_service = ReplayMarketService(
            instrument_id=instrument_id,
            binding_id=binding_id,
            bars=bars,
            assumptions=assumptions,
        )
        frozen = _frozen_datetime(REPLAY_RUNTIME_NOW)
        # The monitor spans several modules; each reads the clock.
        for module in strategy_monitor_modules:
            monkeypatch.setattr(module, "datetime", frozen)
        monkeypatch.setattr(hardening_module, "datetime", frozen)
        monitor = TradingStrategyMonitor(
            strategy_repository_factory=lambda: strategy_repository,
            paper_repository_factory=lambda: paper_repository,
            market_service_factory=lambda: market_service,
            interval_seconds=5,
        )

        submitted = await monitor.run_once()
        assert submitted == 1
        before_fill = paper_repository.snapshot(account_id)
        assert len(before_fill.open_orders) == 1
        order = before_fill.open_orders[0]
        # PostgreSQL stamps created_at with the real clock, the only clock the
        # replay does not freeze. Align it with the replayed session so the
        # fill below stays causal whenever the test runs.
        with unit_of_work(database) as work:
            work.connection.execute(
                """UPDATE omnix_trading_paper_orders SET created_at = %s
                    WHERE workspace_id = %s AND account_id = %s AND order_id = %s""",
                (REPLAY_RUNTIME_NOW, context.workspace_id, account_id, order.order_id),
            )
            work.commit()

        events = strategy_repository.recent_events(strategy_id, 20_000)
        auth = next(event for event in events if event.event_type == "trade_authorization")
        entry = next(event for event in events if event.event_type == "entry_order_submitted")
        assert auth.state == "authorized"
        assert auth.payload["assessment"]["authorized"] is True
        assert auth.observed_at <= entry.observed_at

        execution = market_service.execution
        fill_time = REPLAY_RUNTIME_NOW + timedelta(seconds=1)
        fills = paper_repository.process_observation(
            account_id,
            PaperMarketObservation(
                instrument_id=instrument_id,
                binding_id=binding_id,
                provider=execution.provider,
                price=execution.last or execution.ask or execution.bid,
                bid=execution.bid,
                ask=execution.ask,
                bid_size=execution.bid_size,
                ask_size=execution.ask_size,
                high=execution.high,
                low=execution.low,
                volume=execution.bar_volume,
                bar_start_time=execution.bar_start_time,
                source_time=fill_time,
                evaluated_at=fill_time,
                execution_eligible=True,
                freshness_mode="live",
                halted=False,
            ),
        )
        assert len(fills) == 1
        persisted = paper_repository.snapshot(account_id)
        filled_order = next(item for item in persisted.order_history if item.order_id == order.order_id)
        assert filled_order.status == "filled"
        position = next(item for item in persisted.positions if item.instrument_id == instrument_id)
        assert position.quantity == fills[0].quantity
        assert position.average_cost == fills[0].price

        print(
            "POSTGRES AUTO PAPER E2E PASS "
            f"strategy={strategy_id} order={order.order_id} authorization={auth.state} "
            f"qty={fills[0].quantity} fill={fills[0].price} position={position.quantity}"
        )
    finally:
        database.close()


def test_one_pass_of_a_strategy_runs_at_a_time() -> None:
    """The monitor and the strategy runner never overlap on one configuration (strategy runner WP)."""
    database = _database()
    try:
        context = ensure_local_identity(database)
        repository = TradingStrategyRepository(context=context, uow_factory=lambda: unit_of_work(database))
        with repository.exclusive_pass("pass-lock-a") as first:
            with repository.exclusive_pass("pass-lock-a") as second, repository.exclusive_pass("pass-lock-b") as other:
                assert first is True
                assert second is False
                assert other is True
        # Closing the pass releases the lock.
        with repository.exclusive_pass("pass-lock-a") as again:
            assert again is True
    finally:
        database.close()
