from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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
from app.apps.trading.paper import (
    PaperAccount,
    PaperAccountSnapshot,
    PaperBalance,
    PaperFill,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderRequest,
    PaperPosition,
    paper_buy_reservation,
    paper_fill_decision,
    paper_fill_is_fundable,
    paper_fill_key,
)
from app.apps.trading.strategies import evaluate_gap_pullback
from app.apps.trading.strategies.models import StrategyRiskProfile
from app.apps.trading.strategy_data_integrity import finviz_atomic_source_locator
from app.apps.trading.strategy_monitor import TradingStrategyMonitor
from app.apps.trading.strategies.runner import StrategyRunner
from app.apps.trading.strategy_runner_parity import (
    MONITOR_PARITY_EVENT,
    PARITY_EVENT_TYPES,
    RUNNER_PARITY_EVENT,
    parity_report,
)
from app.apps.trading.strategy_repository import (
    StrategyEvent,
    StrategyProtection,
    TradingStrategyConfigDocument,
)
from app.apps.trading.strategy_universe_archiver import _archive_universe_id
from app.apps.trading.strategy_v2_qualification import (
    PROSPECTIVE_ECONOMIC_POLICY_VERSION,
    V2_PROSPECTIVE_START,
    V2_QUALIFICATION_VERSION,
    V2_REPLAY_VERSION,
    evaluate_v2_prospective_qualification,
    managed_finviz_v2_config,
    v2_profile_fingerprint,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "2026-09-03-auto-paper-e2e.json"
SOURCE_SESSION_DATE = date(2026, 9, 3)
REPLAY_SESSION_DATE = date(2026, 10, 1)
REPLAY_CAPTURE_UTC = datetime(2026, 10, 1, 13, 16, 18, tzinfo=timezone.utc)
REPLAY_OPEN_UTC = datetime(2026, 10, 1, 13, 30, tzinfo=timezone.utc)
# The fixture contains finalized bars 09:30-09:40 ET, so 09:41 ET is the
# latest causal runtime clock at which bar coverage is complete and current.
REPLAY_RUNTIME_NOW = datetime(2026, 10, 1, 13, 41, tzinfo=timezone.utc)
_ET = ZoneInfo("America/New_York")


def _frozen_datetime(now: datetime):
    class FrozenRuntimeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    return FrozenRuntimeDateTime


class InMemoryStrategyRepository:
    def __init__(self, config: TradingStrategyConfigDocument, universe) -> None:
        self.config = config
        self.universe = universe
        self.events: list[StrategyEvent] = []
        self._event_keys: set[str] = set()
        self.protections: dict[str, StrategyProtection] = {}
        self.archive_requests: list[str] = []

    def list_configs(self, active_only=True):
        if active_only and (not self.config.enabled or self.config.mode == "off"):
            return []
        return [self.config]

    def get_universe(self, universe_id):
        self.archive_requests.append(universe_id)
        if universe_id != self.universe.universe_id:
            raise ValueError("gapper_universe_not_found")
        return self.universe

    def events_by_types_between(self, strategy_id, *, event_types, start_time, end_time, limit):
        allowed = set(event_types)
        return [
            event
            for event in self.events
            if event.strategy_id == strategy_id
            and event.event_type in allowed
            and start_time <= event.observed_at < end_time
        ][:limit]

    def append_event(self, event: StrategyEvent):
        if event.idempotency_key in self._event_keys:
            return False
        self._event_keys.add(event.idempotency_key)
        self.events.append(event)
        return True

    def entry_events_between(self, strategy_id, *, start_time, end_time):
        return [
            event
            for event in self.events
            if event.strategy_id == strategy_id
            and event.event_type == "entry_order_submitted"
            and start_time <= event.observed_at < end_time
        ]

    def daily_paper_pnl(self, account_id, *, start_time, end_time):
        return Decimal("0")

    def recent_events(self, strategy_id, limit):
        values = [event for event in self.events if event.strategy_id == strategy_id]
        return values[-limit:]

    def list_protections(self, strategy_id, *, active_only=True):
        values = [
            item.model_copy(deep=True)
            for item in self.protections.values()
            if item.strategy_id == strategy_id
        ]
        if active_only:
            values = [
                item
                for item in values
                if item.status in {"pending_entry", "active", "exit_submitted"}
            ]
        return values

    def save_protection(self, protection: StrategyProtection):
        previous = self.protections.get(protection.protection_id)
        revision = 1 if previous is None else previous.revision + 1
        saved = protection.model_copy(deep=True, update={"revision": revision})
        self.protections[saved.protection_id] = saved
        return saved.model_copy(deep=True)


class InMemoryPaperRepository:
    def __init__(self, account_id: str, *, now: datetime) -> None:
        self.now = now
        self.account = PaperAccount(
            account_id=account_id,
            name="Market evidence V2 E2E Paper",
            base_currency="USD",
            commission_bps=Decimal("0"),
            enabled=True,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        self.balance = PaperBalance(currency="USD", available=Decimal("10000"), reserved=Decimal("0"))
        self.orders: dict[str, PaperOrder] = {}
        self.positions: dict[str, PaperPosition] = {}
        self.fills: list[PaperFill] = []

    def snapshot(self, account_id):
        assert account_id == self.account.account_id
        history = list(self.orders.values())
        return PaperAccountSnapshot(
            account=self.account.model_copy(deep=True),
            balances=[self.balance.model_copy(deep=True)],
            positions=[item.model_copy(deep=True) for item in self.positions.values() if item.quantity != 0],
            open_orders=[item.model_copy(deep=True) for item in history if item.status == "open"],
            order_history=[item.model_copy(deep=True) for item in history],
            recent_fills=[item.model_copy(deep=True) for item in self.fills],
            recent_ledger=[],
        )

    def place_order(self, account_id, request: PaperOrderRequest, *, authority):
        assert account_id == self.account.account_id
        existing = self.orders.get(request.order_id)
        if existing is not None:
            if existing.idempotency_key == request.idempotency_key:
                return existing.model_copy(deep=True)
            raise ValueError("paper_order_id_conflict")
        reserved_cash = paper_buy_reservation(
            request,
            available_cash=self.balance.available,
            commission_bps=self.account.commission_bps,
        )
        if request.side == "buy" and reserved_cash > self.balance.available:
            raise ValueError("insufficient_paper_cash")
        if request.side == "buy":
            self.balance.available -= reserved_cash
            self.balance.reserved += reserved_cash
        order = PaperOrder(
            account_id=account_id,
            **request.model_dump(),
            reserved_cash=reserved_cash,
            created_at=self.now,
            updated_at=self.now,
        )
        self.orders[order.order_id] = order
        return order.model_copy(deep=True)

    def process_observation(self, account_id: str, observation: PaperMarketObservation) -> list[PaperFill]:
        from app.apps.trading.paper import PaperExecutionPolicy

        policy = PaperExecutionPolicy(
            latency_ms=0,
            max_volume_participation_pct=Decimal("1"),
            max_observation_age_seconds=Decimal("60"),
        )
        fills: list[PaperFill] = []
        for order_id, order in list(self.orders.items()):
            if order.status != "open" or order.instrument_id != observation.instrument_id:
                continue
            decision = paper_fill_decision(order, observation, policy)
            if not decision.should_fill:
                continue
            assert decision.fill_price is not None
            assert decision.fill_quantity is not None
            quantity = decision.fill_quantity
            price = decision.fill_price
            notional = quantity * price
            if not paper_fill_is_fundable(order, total_cost=notional, available_cash=self.balance.available):
                raise ValueError("insufficient_paper_cash")
            key = paper_fill_key(account_id, order.order_id, observation)
            fill = PaperFill(
                fill_id=f"fill-{key[:32]}",
                order_id=order.order_id,
                instrument_id=order.instrument_id,
                side=order.side,
                quantity=quantity,
                price=price,
                commission=Decimal("0"),
                source_time=observation.source_time,
                evaluated_at=observation.evaluated_at,
                idempotency_key=key,
            )
            fills.append(fill)
            self.fills.append(fill)
            self.orders[order_id] = order.model_copy(
                update={
                    "filled_quantity": order.filled_quantity + quantity,
                    "average_fill_price": price,
                    "status": "filled",
                    "updated_at": observation.evaluated_at,
                }
            )
            if order.side == "buy":
                self.balance.reserved -= order.reserved_cash
                self.balance.available += order.reserved_cash - notional
                self.positions[order.instrument_id] = PaperPosition(
                    instrument_id=order.instrument_id,
                    quantity=quantity,
                    average_cost=price,
                    realized_pnl=Decimal("0"),
                    last_price=price,
                )
        return fills


class ReplayMarketService:
    def __init__(self, bars: list[MarketBar], fixture: dict[str, object], *, now: datetime) -> None:
        self._bars = bars
        assumptions = fixture["execution_assumptions"]
        selected = fixture["selected"]
        assert isinstance(assumptions, dict)
        assert isinstance(selected, dict)
        self.instrument_id = str(selected["instrument_id"])
        self.binding_id = str(selected["binding_id"])
        self.execution = ExecutionObservation(
            instrument_id=self.instrument_id,
            binding_id=self.binding_id,
            provider="alpaca_iex",
            bid=Decimal(str(assumptions["execution_bid"])),
            ask=Decimal(str(assumptions["execution_ask"])),
            bid_size=Decimal(str(assumptions["displayed_size"])),
            ask_size=Decimal(str(assumptions["displayed_size"])),
            last=Decimal(str(assumptions["execution_last"])),
            high=Decimal(str(assumptions["execution_ask"])),
            low=Decimal(str(assumptions["execution_bid"])),
            bar_volume=Decimal("100000"),
            bar_start_time=now - timedelta(minutes=1),
            cumulative_volume=Decimal("2500000"),
            source_time=now,
            received_at=now,
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


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _build_bars(fixture: dict[str, object]) -> list[MarketBar]:
    selected = fixture["selected"]
    assert isinstance(selected, dict)
    bars = []
    for row in fixture["replay_bars"]:
        start = REPLAY_OPEN_UTC + timedelta(minutes=int(row["minute"]))
        bars.append(
            MarketBar(
                instrument_id=str(selected["instrument_id"]),
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
    return bars


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


def _seed_reviewed_qualification(repository: InMemoryStrategyRepository, config: TradingStrategyConfigDocument) -> None:
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

    review_at = max(event.observed_at for event in repository.events) + timedelta(minutes=1)
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
                "approved": True,
                "execution_authority": False,
            },
        )
    )
    before = evaluate_v2_prospective_qualification(config, repository.events)
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
    assert evaluate_v2_prospective_qualification(config, repository.events).auto_paper_authorized is True


def _replay_session(monkeypatch, *, execution_owner: str = "monitor") -> SimpleNamespace:
    """The Sep 3 TLYS replay at 09:41 ET: one configuration, its frozen universe,
    reviewed qualification, an empty paper account and the monitor."""
    fixture = _load_fixture()
    selected = fixture["selected"]
    assumptions = fixture["execution_assumptions"]
    assert isinstance(selected, dict)
    assert isinstance(assumptions, dict)

    value = managed_finviz_v2_config().model_copy(update={"execution_owner": execution_owner})
    config = TradingStrategyConfigDocument(
        strategy_id="sep3-tlys-auto-paper-e2e",
        account_id="paper-sep3-e2e",
        strategy_kind="gap_pullback_v1",
        strategy_version="2.0.0",
        mode="auto_paper",
        active_universe_id=None,
        config=value,
        risk=StrategyRiskProfile(),
        enabled=True,
    )
    assert date.fromisoformat(str(fixture["session_date"])) == SOURCE_SESSION_DATE

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
        instrument_id=str(selected["instrument_id"]),
        binding_id=str(selected["binding_id"]),
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

    marker = datetime.combine(REPLAY_SESSION_DATE, config.config.universe_scan_time_et, tzinfo=_ET)
    universe_id = _archive_universe_id(config, marker)
    symbols = [str(item) for item in fixture["source_candidate_symbols"]]
    dispositions = [
        SourceMemberDisposition(
            symbol=symbols[0],
            source_rank=1,
            status="materialized",
            instrument_id=candidate.instrument_id,
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
        universe_id=universe_id,
        session_date=REPLAY_SESSION_DATE,
        evaluation_time=REPLAY_CAPTURE_UTC,
        discovery_source="finviz",
        source_locator=finviz_atomic_source_locator(str(fixture["source_url"])),
        source_candidate_symbols=symbols,
        source_member_dispositions=dispositions,
        candidates=[candidate],
    )
    bars = _build_bars(fixture)
    causal = evaluate_gap_pullback(candidate, bars, config.config)
    assert causal.state == "entry_ready"
    assert causal.reason_code == "FAILED_SELLOFF_V2_TIMING_BREAK"

    strategy_repository = InMemoryStrategyRepository(config, universe)
    _seed_reviewed_qualification(strategy_repository, config)
    paper_repository = InMemoryPaperRepository(config.account_id, now=REPLAY_RUNTIME_NOW)
    market_service = ReplayMarketService(bars, fixture, now=REPLAY_RUNTIME_NOW)

    frozen_clock = _frozen_datetime(REPLAY_RUNTIME_NOW)
    # The monitor spans several modules; each reads the clock.
    for module in strategy_monitor_modules:
        monkeypatch.setattr(module, "datetime", frozen_clock)
    monkeypatch.setattr(hardening_module, "datetime", frozen_clock)

    monitor = TradingStrategyMonitor(
        strategy_repository_factory=lambda: strategy_repository,
        paper_repository_factory=lambda: paper_repository,
        market_service_factory=lambda: market_service,
        interval_seconds=5,
    )
    return SimpleNamespace(
        selected=selected,
        config=config,
        candidate=candidate,
        strategy_repository=strategy_repository,
        paper_repository=paper_repository,
        market_service=market_service,
        monitor=monitor,
        clock=lambda: REPLAY_RUNTIME_NOW,
    )


def _fill_observation(session: SimpleNamespace) -> PaperMarketObservation:
    return PaperMarketObservation(
        instrument_id=session.candidate.instrument_id,
        binding_id=session.candidate.binding_id,
        provider="alpaca_iex",
        price=session.market_service.execution.last,
        bid=session.market_service.execution.bid,
        ask=session.market_service.execution.ask,
        bid_size=session.market_service.execution.bid_size,
        ask_size=session.market_service.execution.ask_size,
        high=session.market_service.execution.high,
        low=session.market_service.execution.low,
        volume=session.market_service.execution.bar_volume,
        bar_start_time=session.market_service.execution.bar_start_time,
        source_time=REPLAY_RUNTIME_NOW + timedelta(seconds=1),
        evaluated_at=REPLAY_RUNTIME_NOW + timedelta(seconds=1),
        execution_eligible=True,
        freshness_mode="live",
        halted=False,
    )


def test_sep3_tlys_auto_paper_runtime_places_fills_and_protects_trade(monkeypatch) -> None:
    session = _replay_session(monkeypatch)
    selected, config = session.selected, session.config
    strategy_repository, paper_repository = session.strategy_repository, session.paper_repository
    monitor = session.monitor
    submitted = asyncio.run(monitor.run_once())
    assert submitted == 1
    assert monitor.paper_order_count == 1

    authorization_events = [
        event for event in strategy_repository.events
        if event.event_type == "trade_authorization"
    ]
    assert len(authorization_events) == 1
    authorization = authorization_events[0]
    assert authorization.state == "authorized"
    assert authorization.payload["assessment"]["authorized"] is True
    assert authorization.payload["assessment"]["reason_codes"] == []

    snapshot = paper_repository.snapshot(config.account_id)
    assert len(snapshot.open_orders) == 1
    order = snapshot.open_orders[0]
    observation = _fill_observation(session)
    fills = paper_repository.process_observation(config.account_id, observation)
    assert len(fills) == 1

    submitted_again = asyncio.run(monitor.run_once())
    assert submitted_again == 0
    assert monitor.paper_order_count == 1
    protections = strategy_repository.list_protections(config.strategy_id, active_only=True)
    assert len(protections) == 1
    assert protections[0].status == "active"

    entry_events = [event for event in strategy_repository.events if event.event_type == "entry_order_submitted"]
    assert len(entry_events) == 1
    assert authorization.observed_at <= paper_repository.orders[order.order_id].created_at

    print(
        "AUTO PAPER E2E PASS "
        f"symbol={selected['symbol']} order={order.order_id} qty={fills[0].quantity} "
        f"fill={fills[0].price} authorization={authorization.state} protection={protections[0].status}"
    )


def _without_run_ids(value):
    if isinstance(value, dict):
        return {key: _without_run_ids(item) for key, item in value.items() if key != "run_id"}
    if isinstance(value, list):
        return [_without_run_ids(item) for item in value]
    return value


def _trade_record(session: SimpleNamespace, *, ignore_event_types: tuple[str, ...] = ()) -> dict[str, object]:
    """Everything the session's trade left behind, minus the run that made it."""
    config = session.config
    return {
        "orders": [order.model_dump(mode="json") for order in session.paper_repository.orders.values()],
        "protections": [
            protection.model_dump(mode="json")
            for protection in session.strategy_repository.list_protections(config.strategy_id, active_only=False)
        ],
        "events": [
            _without_run_ids(event.model_dump(mode="json"))
            for event in session.strategy_repository.events
            if event.event_type not in ignore_event_types
        ],
    }


def _runner(session: SimpleNamespace) -> StrategyRunner:
    return StrategyRunner(
        strategy_repository_factory=lambda: session.strategy_repository,
        market_service_factory=lambda: session.market_service,
        paper_repository_factory=lambda: session.paper_repository,
        clock=session.clock,
        owned_configs_enabled=lambda: True,
    )


def _monitor_owned_trade(monkeypatch) -> dict[str, object]:
    session = _replay_session(monkeypatch)
    assert asyncio.run(session.monitor.run_once()) == 1
    session.paper_repository.process_observation(session.config.account_id, _fill_observation(session))
    assert asyncio.run(session.monitor.run_once()) == 0
    return _trade_record(session)


def test_the_runner_as_owner_places_the_identical_auto_paper_trade(monkeypatch) -> None:
    expected = _monitor_owned_trade(monkeypatch)
    assert len(expected["orders"]) == len(expected["protections"]) == 1
    assert {"trade_authorization", "risk_decision", "entry_order_submitted"} <= {
        event["event_type"] for event in expected["events"]
    }

    session = _replay_session(monkeypatch, execution_owner="runner")
    runner = _runner(session)
    # The monitor leaves a runner-owned configuration alone.
    seeded = list(session.strategy_repository.events)
    assert asyncio.run(session.monitor.run_once()) == 0
    assert session.strategy_repository.events == seeded
    assert session.paper_repository.orders == {}

    assert asyncio.run(runner.run_cycle()) == 1
    fills = session.paper_repository.process_observation(session.config.account_id, _fill_observation(session))
    assert len(fills) == 1
    assert asyncio.run(runner.run_cycle()) == 0
    assert asyncio.run(session.monitor.run_once()) == 0

    record = _trade_record(session)
    assert len(record["orders"]) == 1
    [protection] = session.strategy_repository.list_protections(session.config.strategy_id, active_only=True)
    assert protection.status == "active"
    assert [event.event_type for event in session.strategy_repository.events].count("trade_authorization") == 1
    assert all(event.run_id.startswith("runner-") for event in session.strategy_repository.events if event.run_id)
    # Same order, protection and strategy events as the monitor's trade.
    assert record == expected


def test_a_runner_shadow_records_parity_without_touching_the_trade(monkeypatch) -> None:
    expected = _monitor_owned_trade(monkeypatch)

    session = _replay_session(monkeypatch, execution_owner="runner_shadow")
    runner = _runner(session)
    assert asyncio.run(session.monitor.run_once()) == 1
    before = _trade_record(session)

    # The runner's pass ends at its proposals: no order, no protection, no other event.
    assert asyncio.run(runner.run_cycle()) == 0
    after = _trade_record(session, ignore_event_types=(RUNNER_PARITY_EVENT,))
    assert after == before

    report = parity_report(session.strategy_repository.events)
    assert report["parity"] is True
    assert report["matched"] == report["monitor_proposals"] == report["runner_proposals"] == 1

    session.paper_repository.process_observation(session.config.account_id, _fill_observation(session))
    assert asyncio.run(session.monitor.run_once()) == 0
    assert asyncio.run(runner.run_cycle()) == 0
    # Apart from the parity evidence, the monitor's trade is the one it places alone.
    assert _trade_record(session, ignore_event_types=PARITY_EVENT_TYPES) == expected


def test_parity_reports_proposals_only_one_side_made() -> None:
    def proposal(event_type: str, attempt: str, signal: dict[str, str]) -> StrategyEvent:
        return StrategyEvent(
            strategy_id="s", event_id=attempt + event_type[:1], instrument_id="i", event_type=event_type,
            state="entry_ready", observed_at=REPLAY_RUNTIME_NOW, idempotency_key=attempt + event_type,
            payload={"trade_attempt_id": attempt, "signal": signal},
        )

    report = parity_report([
        proposal(MONITOR_PARITY_EVENT, "a", {"entry": "1"}),
        proposal(RUNNER_PARITY_EVENT, "a", {"entry": "1"}),
        proposal(MONITOR_PARITY_EVENT, "b", {"entry": "1"}),
        proposal(RUNNER_PARITY_EVENT, "c", {"entry": "1"}),
        proposal(MONITOR_PARITY_EVENT, "d", {"entry": "1"}),
        proposal(RUNNER_PARITY_EVENT, "d", {"entry": "2"}),
    ])
    assert report["parity"] is False
    assert report["matched"] == 1
    assert (report["monitor_only"], report["runner_only"], report["signal_mismatch"]) == (["b"], ["c"], ["d"])
