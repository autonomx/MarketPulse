"""Paper stop-limit and trailing stop orders, time in force, trailing brackets (TVP-7.1)."""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.apps.trading.execution import ExecutionObservation
from app.apps.trading.order_gateway import OrderGateway
from app.apps.trading.paper import (
    PaperAccount,
    PaperAccountSnapshot,
    PaperBalance,
    PaperExecutionPolicy,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderRequest,
    PaperPosition,
    paper_buy_reservation,
    paper_day_expiry,
    paper_fill_decision,
    paper_order_expiry,
    paper_order_request_matches,
    paper_observation_moment,
    paper_order_state_update,
    paper_protection_trigger,
    paper_trailing_protection_update,
)
from app.apps.trading.paper_monitor import TradingPaperMonitor
from app.apps.trading.paper_protection import PaperPositionProtection, PaperProtectionUpsert
from app.apps.trading.paper_risk import PaperRiskOrderRequest, risk_order_request, risk_protection_request
from app.apps.trading.replay_execution import ReplayExecutionBar, place_replay_order

NOW = datetime(2026, 8, 5, 15, tzinfo=timezone.utc)
CRYPTO = "crypto:BINANCE:spot:BTC-USDT"
EQUITY = "equity:NASDAQ:AAPL"
BINDING = "binance:BTCUSDT"
POLICY = PaperExecutionPolicy(slippage_bps=Decimal("0"), stop_slippage_bps=Decimal("25"), latency_ms=0)
APP = Path(__file__).parents[2] / "app"


def order(order_type: str, side: str = "buy", **overrides) -> PaperOrder:
    payload: dict = {
        "account_id": "paper-1",
        "order_id": f"{side}-{order_type}",
        "instrument_id": CRYPTO,
        "binding_id": BINDING,
        "side": side,
        "order_type": order_type,
        "quantity": Decimal("2"),
        "idempotency_key": f"key-{side}-{order_type}",
    }
    payload.update(overrides)
    return PaperOrder(**payload)


def observation(price: str, **overrides) -> PaperMarketObservation:
    payload: dict = {
        "instrument_id": CRYPTO,
        "binding_id": BINDING,
        "provider": "binance",
        "price": Decimal(price),
        "source_time": NOW,
        "evaluated_at": NOW,
    }
    payload.update({key: Decimal(value) if isinstance(value, str) else value for key, value in overrides.items()})
    return PaperMarketObservation(**payload)


def request(order_type: str = "limit", side: str = "sell", **overrides) -> PaperOrderRequest:
    payload: dict = {
        "order_id": "request-1",
        "instrument_id": CRYPTO,
        "side": side,
        "order_type": order_type,
        "quantity": Decimal("1"),
        "idempotency_key": "request-1",
    }
    payload.update(overrides)
    return PaperOrderRequest(**payload)


# Request contract ----------------------------------------------------------


def test_defaults_keep_gtc_and_no_trailing() -> None:
    plain = request("limit", limit_price=Decimal("10"))
    assert plain.time_in_force == "gtc"
    assert plain.expires_at is None and plain.trail_amount is None and plain.trail_percent is None
    assert paper_order_expiry(plain, NOW) is None
    stored = order("limit", limit_price=Decimal("10"))
    assert stored.time_in_force == "gtc" and stored.expires_at is None and stored.trail_water_mark is None


@pytest.mark.parametrize(
    "fields",
    [
        {"order_type": "stop_limit", "stop_price": Decimal("10")},
        {"order_type": "stop_limit", "limit_price": Decimal("10")},
        {"order_type": "trailing_stop"},
        {"order_type": "trailing_stop", "trail_amount": Decimal("1"), "trail_percent": Decimal("1")},
        {"order_type": "trailing_stop", "trail_amount": Decimal("1"), "stop_price": Decimal("9")},
        {"order_type": "trailing_stop", "side": "buy", "trail_amount": Decimal("1")},
        {"order_type": "trailing_stop", "trail_percent": Decimal("100")},
        {"order_type": "limit", "limit_price": Decimal("10"), "trail_amount": Decimal("1")},
        {"order_type": "limit", "limit_price": Decimal("10"), "time_in_force": "gtd"},
        {"order_type": "limit", "limit_price": Decimal("10"), "time_in_force": "gtd", "expires_at": datetime(2026, 9, 1)},
        {"order_type": "limit", "limit_price": Decimal("10"), "time_in_force": "day", "expires_at": NOW},
        {"order_type": "limit", "limit_price": Decimal("10"), "expires_at": NOW},
        {"order_type": "stop", "stop_price": Decimal("10"), "reference_price": Decimal("10")},
    ],
)
def test_invalid_order_requests_are_refused(fields) -> None:
    payload = {"side": "sell", **fields}
    with pytest.raises(ValidationError):
        request(**payload)


def test_valid_new_order_requests() -> None:
    request("stop_limit", stop_price=Decimal("10"), limit_price=Decimal("9.5"), time_in_force="day")
    request("trailing_stop", trail_percent=Decimal("2.5"))
    request("trailing_stop", side="buy", trail_amount=Decimal("1"), reference_price=Decimal("10"))
    gtd = request("limit", limit_price=Decimal("10"), time_in_force="gtd", expires_at=NOW)
    assert paper_order_expiry(gtd, NOW - timedelta(hours=1)) == NOW


def test_idempotent_retry_ignores_server_owned_trailing_and_day_state() -> None:
    trailing = request("trailing_stop", trail_amount=Decimal("1"), time_in_force="day")
    stored = PaperOrder(
        account_id="paper-1",
        **trailing.model_dump(exclude={"expires_at", "stop_price"}),
        expires_at=NOW,
        stop_price=Decimal("41"),
        trail_water_mark=Decimal("42"),
    )
    assert paper_order_request_matches(stored, trailing)
    assert not paper_order_request_matches(stored, trailing.model_copy(update={"trail_amount": Decimal("2")}))
    assert not paper_order_request_matches(stored, trailing.model_copy(update={"time_in_force": "gtc"}))


# Fill semantics -------------------------------------------------------------


def test_limit_and_stop_fill_through_price_gaps() -> None:
    # A buy limit that the market gaps through fills at the better gap price.
    limit = paper_fill_decision(order("limit", limit_price=Decimal("100")), observation("95", ask="95"), POLICY)
    assert limit.should_fill and limit.fill_price == Decimal("95")
    # A sell stop that the market gaps through fills at the worse gap price, with stop slippage.
    stop = paper_fill_decision(order("stop", "sell", stop_price=Decimal("100")), observation("90", bid="90"), POLICY)
    assert stop.should_fill and stop.fill_price == Decimal("89.775")


def test_buy_stop_limit_triggers_then_rests_as_a_limit_order() -> None:
    resting = order("stop_limit", stop_price=Decimal("100"), limit_price=Decimal("101"))

    below = observation("99", ask="99")
    assert paper_fill_decision(resting, below, POLICY).reason == "stop_limit_not_triggered"
    assert paper_order_state_update(resting, below, POLICY) is None

    inside = paper_fill_decision(resting, observation("100.5", ask="100.5"), POLICY)
    assert inside.should_fill and inside.fill_price == Decimal("100.5")
    assert inside.reason == "stop_limit_triggered_within_limit"

    # A gap through both the stop and the limit triggers the order without a fill.
    gap = observation("103", ask="103")
    gapped = paper_fill_decision(resting, gap, POLICY)
    assert not gapped.should_fill and gapped.reason == "stop_limit_price_not_reached"
    state = paper_order_state_update(resting, gap, POLICY)
    assert state is not None and state.stop_triggered_at == NOW

    # Once triggered it is a limit order, even back below the stop.
    triggered = resting.model_copy(update={"stop_triggered_at": state.stop_triggered_at})
    later = NOW + timedelta(minutes=1)
    refill = paper_fill_decision(triggered, observation("99.5", ask="99.5", source_time=later, evaluated_at=later), POLICY)
    assert refill.should_fill and refill.fill_price == Decimal("99.5")
    assert paper_order_state_update(triggered, observation("99.5", source_time=later, evaluated_at=later), POLICY) is None


def test_stop_limit_trigger_by_bar_range_never_fills_better_than_the_stop() -> None:
    resting = order("stop_limit", stop_price=Decimal("100"), limit_price=Decimal("101"))
    bar = observation("99", ask="99", high="100.5", low="98", bar_start_time=NOW - timedelta(minutes=1))
    decision = paper_fill_decision(resting, bar, POLICY)
    assert decision.should_fill and decision.fill_price == Decimal("100")


def test_triggered_stop_limit_uses_a_bar_range_only_after_the_trigger() -> None:
    triggered = order("stop_limit", stop_price=Decimal("100"), limit_price=Decimal("101"), stop_triggered_at=NOW)
    later = NOW + timedelta(seconds=30)
    early_bar = observation(
        "102", ask="102", high="102", low="99", bar_start_time=NOW - timedelta(seconds=30),
        source_time=later, evaluated_at=later,
    )
    assert not paper_fill_decision(triggered, early_bar, POLICY).should_fill
    # The bar that starts at the trigger moment may still hold earlier prices.
    same_moment = early_bar.model_copy(update={"bar_start_time": NOW})
    assert not paper_fill_decision(triggered, same_moment, POLICY).should_fill
    after_bar = early_bar.model_copy(update={"bar_start_time": NOW + timedelta(seconds=1)})
    filled = paper_fill_decision(triggered, after_bar, POLICY)
    assert filled.should_fill and filled.fill_price == Decimal("101")
    assert filled.reason == "stop_limit_range_reached"


def test_sell_stop_limit_gap_down() -> None:
    resting = order("stop_limit", "sell", stop_price=Decimal("95"), limit_price=Decimal("94.5"))
    gap = observation("93", bid="93")
    assert not paper_fill_decision(resting, gap, POLICY).should_fill
    state = paper_order_state_update(resting, gap, POLICY)
    assert state is not None
    triggered = resting.model_copy(update={"stop_triggered_at": state.stop_triggered_at})
    later = NOW + timedelta(minutes=1)
    filled = paper_fill_decision(triggered, observation("94.8", bid="94.8", source_time=later, evaluated_at=later), POLICY)
    assert filled.should_fill and filled.fill_price == Decimal("94.8")


def test_sell_trailing_stop_arms_ratchets_and_fills() -> None:
    trailing = order("trailing_stop", "sell", trail_amount=Decimal("2"))

    arm = observation("100", bid="100")
    assert paper_fill_decision(trailing, arm, POLICY).reason == "trailing_stop_not_armed"
    armed_state = paper_order_state_update(trailing, arm, POLICY)
    assert armed_state is not None
    assert (armed_state.trail_water_mark, armed_state.stop_price) == (Decimal("100"), Decimal("98"))
    armed = trailing.model_copy(update={"trail_water_mark": Decimal("100"), "stop_price": Decimal("98")})

    rally = observation("103", bid="103", high="104", low="102", bar_start_time=NOW)
    assert not paper_fill_decision(armed, rally, POLICY).should_fill
    raised = paper_order_state_update(armed, rally, POLICY)
    assert raised is not None and (raised.trail_water_mark, raised.stop_price) == (Decimal("104"), Decimal("102"))
    # A lower price never lowers the water mark or the stop.
    higher = armed.model_copy(update={"trail_water_mark": Decimal("104"), "stop_price": Decimal("102")})
    assert paper_order_state_update(higher, observation("103", bid="103"), POLICY) is None

    drop = paper_fill_decision(higher, observation("101.5", bid="101.5"), POLICY)
    assert drop.should_fill and drop.fill_price == Decimal("101.246250")
    assert drop.reason == "trailing_stop_current_market_triggered"


def test_trailing_stop_checks_the_old_stop_before_a_bar_raises_it() -> None:
    armed = order("trailing_stop", "sell", trail_amount=Decimal("2"), trail_water_mark=Decimal("100"), stop_price=Decimal("98"))
    whipsaw = observation("105", bid="105", high="110", low="97", bar_start_time=NOW)
    decision = paper_fill_decision(armed, whipsaw, POLICY)
    assert decision.should_fill and decision.fill_price == Decimal("97.755")


def test_trailing_stop_by_percent_and_buy_side() -> None:
    percent = order("trailing_stop", "sell", trail_percent=Decimal("5"))
    state = paper_order_state_update(percent, observation("200", bid="200"), POLICY)
    assert state is not None and state.stop_price == Decimal("190")

    buy = order("trailing_stop", "buy", trail_amount=Decimal("2"), reference_price=Decimal("100"))
    armed_state = paper_order_state_update(buy, observation("100", ask="100"), POLICY)
    assert armed_state is not None and armed_state.stop_price == Decimal("102")
    armed = buy.model_copy(update={"trail_water_mark": Decimal("100"), "stop_price": Decimal("102")})
    lowered = paper_order_state_update(armed, observation("95", ask="95"), POLICY)
    assert lowered is not None and (lowered.trail_water_mark, lowered.stop_price) == (Decimal("95"), Decimal("97"))
    lower = armed.model_copy(update={"trail_water_mark": Decimal("95"), "stop_price": Decimal("97")})
    filled = paper_fill_decision(lower, observation("97.5", ask="97.5"), POLICY)
    assert filled.should_fill and filled.fill_price == Decimal("97.743750")


def test_buy_reservations_for_the_new_types() -> None:
    stop_limit = request("stop_limit", side="buy", stop_price=Decimal("10"), limit_price=Decimal("10.5"), quantity=Decimal("2"))
    assert paper_buy_reservation(stop_limit, available_cash=Decimal("1000"), commission_bps=Decimal("0")) == Decimal("21")
    trailing = request("trailing_stop", side="buy", trail_percent=Decimal("10"), reference_price=Decimal("10"), quantity=Decimal("2"))
    assert paper_buy_reservation(trailing, available_cash=Decimal("1000"), commission_bps=Decimal("0")) == Decimal("22")


def test_an_order_never_fills_at_or_after_its_expiry() -> None:
    expiring = order("limit", limit_price=Decimal("100"), time_in_force="gtd", expires_at=NOW)
    before = NOW - timedelta(seconds=1)
    assert paper_fill_decision(expiring, observation("99", source_time=before, evaluated_at=before), POLICY).should_fill
    expired = paper_fill_decision(expiring, observation("99"), POLICY)
    assert not expired.should_fill and expired.reason == "order_expired"


# DAY expiry -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("placed_at", "expires_at"),
    [
        # Wednesday 7 October 2026, regular session -> that day's 16:00 ET close (EDT).
        (datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc), datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)),
        # Pre-market -> the same day's close.
        (datetime(2026, 10, 7, 11, 0, tzinfo=timezone.utc), datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)),
        # Exactly at the close -> the next session's close.
        (datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc), datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)),
        # Friday after hours -> Monday's close.
        (datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc), datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)),
        # Thanksgiving (holiday) -> the 13:00 ET early close the next day (EST).
        (datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc), datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)),
        # Daylight saving time ends on 1 November 2026: Monday's close is 21:00 UTC.
        (datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc), datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc)),
    ],
)
def test_us_equity_day_orders_expire_at_the_session_close(placed_at, expires_at) -> None:
    assert paper_day_expiry(EQUITY, placed_at) == expires_at
    day = request("limit", limit_price=Decimal("10"), instrument_id=EQUITY, time_in_force="day")
    assert paper_order_expiry(day, placed_at) == expires_at


def test_around_the_clock_day_orders_expire_at_the_end_of_the_utc_day() -> None:
    assert paper_day_expiry(CRYPTO, datetime(2026, 10, 8, 15, 30, tzinfo=timezone.utc)) == datetime(
        2026, 10, 9, tzinfo=timezone.utc
    )
    assert paper_day_expiry(CRYPTO, datetime(2026, 10, 8, 23, 59, tzinfo=timezone(timedelta(hours=-5)))) == datetime(
        2026, 10, 10, tzinfo=timezone.utc
    )


# Trailing stop-loss bracket legs ---------------------------------------------


def test_trailing_protection_follows_the_best_price_and_never_loosens() -> None:
    first = paper_trailing_protection_update(
        is_long=True, stop_loss=Decimal("95"), trail_amount=Decimal("5"), trail_percent=None,
        water_mark=None, observation=observation("101"),
    )
    assert first == (Decimal("101"), Decimal("96"))
    # The user's stop is the floor while the trail is below it.
    assert paper_trailing_protection_update(
        is_long=True, stop_loss=Decimal("95"), trail_amount=Decimal("10"), trail_percent=None,
        water_mark=None, observation=observation("101"),
    ) == (Decimal("101"), Decimal("95"))
    assert paper_trailing_protection_update(
        is_long=True, stop_loss=Decimal("96"), trail_amount=Decimal("5"), trail_percent=None,
        water_mark=Decimal("101"), observation=observation("99"),
    ) is None
    # A bar range counts only when the bar started at or after activation.
    bar = observation("103", high="110", low="102", bar_start_time=NOW - timedelta(minutes=1))
    assert paper_trailing_protection_update(
        is_long=True, stop_loss=Decimal("96"), trail_amount=Decimal("5"), trail_percent=None,
        water_mark=Decimal("101"), observation=bar, activated_at=NOW,
    ) == (Decimal("103"), Decimal("98"))
    short = paper_trailing_protection_update(
        is_long=False, stop_loss=Decimal("110"), trail_amount=None, trail_percent=Decimal("5"),
        water_mark=Decimal("100"), observation=observation("90"),
    )
    assert short == (Decimal("90"), Decimal("94.5"))


def test_trailing_protection_requests() -> None:
    with pytest.raises(ValidationError):
        PaperProtectionUpsert(instrument_id=CRYPTO, take_profit=Decimal("110"), trail_amount=Decimal("1"))
    with pytest.raises(ValidationError):
        PaperProtectionUpsert(instrument_id=CRYPTO, stop_loss=Decimal("90"), trail_amount=Decimal("1"), trail_percent=Decimal("1"))
    assert PaperProtectionUpsert(instrument_id=CRYPTO, stop_loss=Decimal("90"), trail_percent=Decimal("1")).trailing


def _risk_intent(**overrides) -> PaperRiskOrderRequest:
    payload: dict = {
        "order_id": "risk-1",
        "instrument_id": CRYPTO,
        "order_type": "stop_limit",
        "trigger_price": Decimal("100"),
        "limit_price": Decimal("101"),
        "stop_loss": Decimal("95"),
        "idempotency_key": "risk-1",
    }
    payload.update(overrides)
    return PaperRiskOrderRequest(**payload)


def test_risk_entries_size_stop_limits_at_their_limit_and_can_trail() -> None:
    intent = _risk_intent(time_in_force="day", trailing_stop_loss=True)
    assert intent.worst_entry_price == Decimal("101")
    placed = risk_order_request(intent, entry_price=Decimal("101"), quantity=Decimal("3"))
    assert (placed.order_type, placed.stop_price, placed.limit_price) == ("stop_limit", Decimal("100"), Decimal("101"))
    assert placed.time_in_force == "day" and placed.side == "buy"
    protection = risk_protection_request(intent, entry_price=Decimal("101"))
    assert protection.trail_amount == Decimal("6") and protection.stop_loss == Decimal("95")
    assert risk_protection_request(_risk_intent(), entry_price=Decimal("101")).trail_amount is None
    with pytest.raises(ValueError, match="paper_trailing_stop_requires_stop_below_entry"):
        risk_protection_request(intent, entry_price=Decimal("95"))
    with pytest.raises(ValidationError):
        _risk_intent(limit_price=None)
    with pytest.raises(ValidationError):
        _risk_intent(order_type="limit")
    with pytest.raises(ValidationError):
        _risk_intent(time_in_force="gtd")


def test_replay_refuses_the_live_only_order_types() -> None:
    snapshot = PaperAccountSnapshot(
        account=PaperAccount(account_id="paper-1", name="Paper", base_currency="USD", commission_bps=Decimal("0")),
        balances=[PaperBalance(currency="USD", available=Decimal("10000"))],
        positions=[PaperPosition(instrument_id=CRYPTO, quantity=Decimal("5"), average_cost=Decimal("100"), realized_pnl=Decimal("0"))],
        open_orders=[],
        recent_fills=[],
        recent_ledger=[],
    )
    bar = ReplayExecutionBar(
        instrument_id=CRYPTO, binding_id=BINDING, start_time=NOW, end_time=NOW + timedelta(minutes=1),
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"), volume=Decimal("1000"),
    )
    trailing = place_replay_order(snapshot, request("trailing_stop", trail_amount=Decimal("1")), bar)
    assert (trailing.order.status, trailing.order.rejection_reason) == ("rejected", "replay_order_type_unsupported")
    day = place_replay_order(snapshot, request("limit", limit_price=Decimal("120"), time_in_force="day"), bar)
    assert (day.order.status, day.order.rejection_reason) == ("rejected", "replay_time_in_force_unsupported")


# Monitor ----------------------------------------------------------------------


class _MonitorRepository:
    def __init__(self, orders: list[PaperOrder], positions: list[PaperPosition] | None = None) -> None:
        self.orders = orders
        self.positions = positions or []
        self.expired_at: list[datetime] = []
        self.placed: list[PaperOrderRequest] = []

    def list_accounts(self, limit=100):
        return [PaperAccount(account_id="paper-1", name="Paper", base_currency="USD", commission_bps=Decimal("0"))]

    def snapshot(self, account_id):
        return PaperAccountSnapshot(
            account=self.list_accounts()[0],
            balances=[PaperBalance(currency="USD", available=Decimal("10000"))],
            positions=self.positions,
            open_orders=[item for item in self.orders if item.status == "open"],
            order_history=self.orders,
            recent_fills=[],
            recent_ledger=[],
        )

    def expire_orders(self, account_id, *, now):
        self.expired_at.append(now)
        self.orders = [
            item.model_copy(update={"status": "expired"}) if item.expires_at and item.expires_at <= now else item
            for item in self.orders
        ]
        return []

    def process_observation(self, account_id, value):
        return []

    def place_order(self, account_id, request, *, authority):
        self.placed.append(request)
        return PaperOrder(account_id=account_id, **request.model_dump())


class _Protections:
    def __init__(self, protection: PaperPositionProtection | None = None) -> None:
        self.protection = protection
        self.trails: list[dict] = []
        self.transitions: list[dict] = []

    def list(self, account_id, *, active_only=True):
        return [self.protection] if self.protection else []

    def get(self, account_id, instrument_id, *, include_inactive=True):
        if self.protection is None:
            raise ValueError("paper_protection_not_found")
        return self.protection

    def trail_stop(self, account_id, instrument_id, **kwargs):
        self.trails.append(kwargs)

    def transition(self, account_id, instrument_id, **kwargs):
        self.transitions.append(kwargs)


class _Market:
    def __init__(self, last: str, *, high: str | None = None, low: str | None = None, bar_start: datetime | None = None) -> None:
        self.last = Decimal(last)
        self.high = Decimal(high) if high else None
        self.low = Decimal(low) if low else None
        self.bar_start = bar_start

    def execution_observation(self, instrument_id, binding_id=None):
        now = datetime.now(timezone.utc)
        return ExecutionObservation(
            instrument_id=instrument_id, binding_id=binding_id or BINDING, provider="fixture",
            bid=self.last, ask=self.last, last=self.last, high=self.high, low=self.low,
            bar_start_time=self.bar_start, source_time=now, received_at=now,
            session="regular", freshness_mode="polled", execution_eligible=True,
        )


def _monitor(repository, protections, market) -> TradingPaperMonitor:
    return TradingPaperMonitor(
        repository_factory=lambda: repository,
        protection_repository_factory=lambda: protections,
        market_service_factory=lambda: market,
        interval_seconds=5,
    )


def test_monitor_expires_due_orders_without_market_data() -> None:
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    repository = _MonitorRepository([order("limit", limit_price=Decimal("100"), time_in_force="gtd", expires_at=past)])
    monitor = _monitor(repository, _Protections(), _Market("101"))
    asyncio.run(monitor.run_once())
    assert len(repository.expired_at) == 1
    assert monitor.active_order_count == 0


def test_monitor_leaves_gtc_orders_alone() -> None:
    repository = _MonitorRepository([order("limit", limit_price=Decimal("100"))])
    asyncio.run(_monitor(repository, _Protections(), _Market("101")).run_once())
    assert repository.expired_at == []


def test_monitor_cancels_a_pending_bracket_when_its_entry_expires() -> None:
    entry = order("limit", limit_price=Decimal("100"), status="expired", time_in_force="day", expires_at=NOW)
    protections = _Protections(
        PaperPositionProtection(
            account_id="paper-1", instrument_id=CRYPTO, entry_order_id=entry.order_id,
            stop_loss=Decimal("95"), status="pending_entry",
        )
    )
    asyncio.run(_monitor(_MonitorRepository([entry]), protections, _Market("101")).run_once())
    assert protections.transitions == [
        {"status": "cancelled", "exit_order_id": None, "trigger_reason": "entry_expired", "expected_revision": 1}
    ]


def test_monitor_trails_a_stop_loss_leg_then_exits_at_the_trailed_stop() -> None:
    position = PaperPosition(instrument_id=CRYPTO, quantity=Decimal("2"), average_cost=Decimal("100"), realized_pnl=Decimal("0"))
    trailing = PaperPositionProtection(
        account_id="paper-1", instrument_id=CRYPTO, stop_loss=Decimal("95"), trail_amount=Decimal("5"),
        trail_water_mark=Decimal("100"), status="active", revision=3,
    )
    protections = _Protections(trailing)
    repository = _MonitorRepository([], [position])
    asyncio.run(_monitor(repository, protections, _Market("108")).run_once())
    assert len(protections.trails) == 1
    moved = protections.trails[0]
    assert (moved["water_mark"], moved["stop_loss"], moved["expected_revision"]) == (Decimal("108"), Decimal("103"), 3)
    assert moved["moved_at"] is not None
    assert repository.placed == []

    protections.protection = trailing.model_copy(update={"stop_loss": Decimal("103"), "trail_water_mark": Decimal("108")})
    asyncio.run(_monitor(repository, protections, _Market("102.5")).run_once())
    assert [(item.side, item.order_type, item.quantity) for item in repository.placed] == [("sell", "market", Decimal("2"))]
    assert protections.transitions[-1]["trigger_reason"] == "stop_loss"


# Authority: the live order gateway path is unchanged ---------------------------


def test_new_order_types_take_the_same_gateway_authority_as_before() -> None:
    calls: list[tuple[object, object]] = []
    repository = SimpleNamespace(place_order=lambda account_id, value, *, authority: calls.append((value, authority)) or value)
    gateway = OrderGateway(repository, entry_authorizer=SimpleNamespace(authorize=lambda *a, **k: None))
    stop_limit = request("stop_limit", side="buy", stop_price=Decimal("10"), limit_price=Decimal("10.5"), time_in_force="day")
    trailing = request("trailing_stop", trail_amount=Decimal("1"), time_in_force="gtd", expires_at=NOW)

    gateway.place_reducing("paper-1", trailing)
    gateway.place_manual_entry("paper-1", stop_limit)
    gateway.place_strategy_entry("paper-1", stop_limit, strategy_id="s-1", trade_attempt_id="a-1")

    assert [value for value, _ in calls] == [trailing, stop_limit, stop_limit]
    assert [authority.kind for _, authority in calls] == ["reduce_only", "manual_risk", "strategy_entry"]


def test_the_order_gateway_and_execution_modules_do_not_branch_on_paper_order_types() -> None:
    """TVP-7.1 is paper-only: the gateway and execution layers never name the new types."""
    modules = [APP / "apps/trading/order_gateway.py", *sorted((APP / "apps/trading").glob("execution*.py"))]
    assert len(modules) > 1
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
        } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for term in ("stop_limit", "trailing_stop", "time_in_force", "expires_at", "trail_amount", "trail_percent"):
            assert term not in names, f"{path.name} mentions {term}"


# The trail moves only forward in time ------------------------------------------


def test_a_forming_bar_delivered_again_cannot_fill_below_the_ratcheted_stop() -> None:
    """The reviewer's case: a bar's low from before its high must not hit the stop the high moved."""
    created = NOW - timedelta(minutes=5)
    trailing = order("trailing_stop", "sell", trail_amount=Decimal("2"), created_at=created)
    armed_at = created + timedelta(seconds=5)
    arm = observation("100", source_time=armed_at, evaluated_at=armed_at)
    state = paper_order_state_update(trailing, arm, POLICY)
    assert state is not None and state.trail_moved_at == armed_at
    armed = trailing.model_copy(update=state.model_dump())

    bar = created + timedelta(minutes=1)
    tick1 = observation("104", high="105", low="99", bar_start_time=bar,
                        source_time=bar + timedelta(seconds=20), evaluated_at=bar + timedelta(seconds=20))
    assert not paper_fill_decision(armed, tick1, POLICY).should_fill
    state = paper_order_state_update(armed, tick1, POLICY)
    assert state is not None and (state.stop_price, state.trail_moved_at) == (Decimal("103"), tick1.source_time)
    raised = armed.model_copy(update=state.model_dump())

    tick2 = tick1.model_copy(update={"source_time": bar + timedelta(seconds=22), "evaluated_at": bar + timedelta(seconds=22)})
    again = paper_fill_decision(raised, tick2, POLICY)
    assert not again.should_fill and again.reason == "trailing_stop_range_not_triggered"
    assert paper_order_state_update(raised, tick2, POLICY) is None

    # A bar that starts after the move and trades through the stop does fill.
    later_bar = bar + timedelta(minutes=1)
    later = observation("104", high="104.5", low="102.5", bar_start_time=later_bar,
                        source_time=later_bar + timedelta(seconds=30), evaluated_at=later_bar + timedelta(seconds=30))
    filled = paper_fill_decision(raised, later, POLICY)
    assert filled.should_fill and filled.reason == "trailing_stop_range_triggered_gap_aware"
    # The current price within the old bar still counts.
    drop = tick2.model_copy(update={"price": Decimal("102.9")})
    assert paper_fill_decision(raised, drop, POLICY).should_fill


def test_a_bracket_leg_ignores_a_bar_that_began_before_its_stop_moved() -> None:
    bar = NOW
    tick = observation("104", high="105", low="99", bar_start_time=bar,
                       source_time=bar + timedelta(seconds=22), evaluated_at=bar + timedelta(seconds=22))
    moved_at = bar + timedelta(seconds=20)
    activated = bar - timedelta(minutes=5)
    assert paper_protection_trigger(
        is_long=True, stop_price=Decimal("103"), target_price=None, observation=tick, activated_at=activated,
    ) == "stop"
    assert paper_protection_trigger(
        is_long=True, stop_price=Decimal("103"), target_price=None, observation=tick,
        activated_at=activated, stop_moved_at=moved_at,
    ) is None
    # The take-profit still reads the bar from activation.
    assert paper_protection_trigger(
        is_long=True, stop_price=Decimal("103"), target_price=Decimal("104.5"), observation=tick,
        activated_at=activated, stop_moved_at=moved_at,
    ) == "target"
    later = tick.model_copy(update={"bar_start_time": moved_at + timedelta(seconds=40)})
    assert paper_protection_trigger(
        is_long=True, stop_price=Decimal("103"), target_price=None, observation=later,
        activated_at=activated, stop_moved_at=moved_at,
    ) == "stop"


def test_monitor_checks_a_moved_trailing_leg_against_bars_after_the_move() -> None:
    position = PaperPosition(instrument_id=CRYPTO, quantity=Decimal("2"), average_cost=Decimal("100"), realized_pnl=Decimal("0"))
    bar = datetime.now(timezone.utc) - timedelta(seconds=30)
    leg = PaperPositionProtection(
        account_id="paper-1", instrument_id=CRYPTO, stop_loss=Decimal("103"), trail_amount=Decimal("2"),
        trail_water_mark=Decimal("105"), trail_moved_at=bar + timedelta(seconds=20), status="active", revision=4,
        created_at=bar - timedelta(minutes=10), updated_at=bar - timedelta(minutes=10),
    )
    protections = _Protections(leg)
    repository = _MonitorRepository([], [position])
    asyncio.run(_monitor(repository, protections, _Market("104", high="105", low="99", bar_start=bar)).run_once())
    assert repository.placed == [] and protections.transitions == []


# Tick size ----------------------------------------------------------------------


def test_trailing_stops_and_fills_round_to_the_tick_against_the_trader() -> None:
    tick = Decimal("0.01")
    percent = order("trailing_stop", "sell", trail_percent=Decimal("2.5"))
    state = paper_order_state_update(percent, observation("104.37", bid="104.37"), POLICY, tick_size=tick)
    assert state is not None and state.stop_price == Decimal("101.76")  # 101.76075 rounded down
    buy = order("trailing_stop", "buy", trail_percent=Decimal("2.5"), reference_price=Decimal("100"))
    state = paper_order_state_update(buy, observation("100.37", ask="100.37"), POLICY, tick_size=tick)
    assert state is not None and state.stop_price == Decimal("102.88")  # 102.87925 rounded up

    armed = percent.model_copy(update={"trail_water_mark": Decimal("104.37"), "stop_price": Decimal("101.76")})
    fill = paper_fill_decision(armed, observation("101.5", bid="101.5"), POLICY, tick_size=tick)
    assert fill.should_fill and fill.fill_price == Decimal("101.24")  # 101.24625 rounded down
    assert paper_fill_decision(armed, observation("101.5", bid="101.5"), POLICY).fill_price == Decimal("101.24625")
    # Other order types are priced as before.
    stop = order("stop", "sell", stop_price=Decimal("100"))
    assert paper_fill_decision(stop, observation("90", bid="90"), POLICY, tick_size=tick).fill_price == Decimal("89.775")

    leg = paper_trailing_protection_update(
        is_long=True, stop_loss=Decimal("95"), trail_amount=None, trail_percent=Decimal("2.5"),
        water_mark=None, observation=observation("104.37"), tick_size=tick,
    )
    assert leg == (Decimal("104.37"), Decimal("101.76"))


def test_a_stale_quote_time_cannot_reopen_the_bar_that_moved_the_stop() -> None:
    """The quote can be older than the forming bar it arrives with (probe_trailing2)."""
    created = NOW - timedelta(minutes=5)
    trailing = order("trailing_stop", "sell", trail_amount=Decimal("2"), created_at=created)
    armed_at = created + timedelta(seconds=5)
    state = paper_order_state_update(trailing, observation("100", source_time=armed_at, evaluated_at=armed_at), POLICY)
    assert state is not None
    armed = trailing.model_copy(update=state.model_dump())

    bar = created + timedelta(minutes=1)
    quote = bar - timedelta(seconds=2)  # quote time before the bar start, still fresh
    tick1 = observation("104", high="105", low="99", bar_start_time=bar,
                        source_time=quote, evaluated_at=bar + timedelta(seconds=3))
    assert paper_observation_moment(tick1) == bar
    state = paper_order_state_update(armed, tick1, POLICY)
    assert state is not None and (state.stop_price, state.trail_moved_at) == (Decimal("103"), bar)
    raised = armed.model_copy(update=state.model_dump())
    tick2 = tick1.model_copy(update={"source_time": quote + timedelta(milliseconds=1)})
    assert not paper_fill_decision(raised, tick2, POLICY).should_fill

    # The bracket leg: the monitor stamps the same moment, and the re-polled bar cannot trigger it.
    update = paper_trailing_protection_update(
        is_long=True, stop_loss=Decimal("98"), trail_amount=Decimal("2"), trail_percent=None,
        water_mark=None, observation=tick1, activated_at=created,
    )
    assert update == (Decimal("105"), Decimal("103"))
    assert paper_protection_trigger(
        is_long=True, stop_price=Decimal("103"), target_price=None, observation=tick2,
        activated_at=created, stop_moved_at=paper_observation_moment(tick1),
    ) is None
    next_bar = tick2.model_copy(update={"bar_start_time": bar + timedelta(minutes=1), "low": Decimal("102.5")})
    assert paper_protection_trigger(
        is_long=True, stop_price=Decimal("103"), target_price=None, observation=next_bar,
        activated_at=created, stop_moved_at=paper_observation_moment(tick1),
    ) == "stop"


def test_monitor_stamps_the_bar_start_when_a_stale_quote_moves_a_leg() -> None:
    position = PaperPosition(instrument_id=CRYPTO, quantity=Decimal("2"), average_cost=Decimal("100"), realized_pnl=Decimal("0"))
    leg = PaperPositionProtection(
        account_id="paper-1", instrument_id=CRYPTO, stop_loss=Decimal("95"), trail_amount=Decimal("2"),
        trail_water_mark=Decimal("100"), status="active", revision=2,
        created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(days=1),
    )
    protections = _Protections(leg)
    future_bar = datetime.now(timezone.utc) + timedelta(seconds=2)
    asyncio.run(_monitor(_MonitorRepository([], [position]), protections, _Market("104", high="105", low="101", bar_start=future_bar)).run_once())
    assert protections.trails[0]["moved_at"] == future_bar


def test_monitor_checks_an_edited_plain_stop_from_its_edit() -> None:
    position = PaperPosition(instrument_id=CRYPTO, quantity=Decimal("2"), average_cost=Decimal("100"), realized_pnl=Decimal("0"))
    bar = datetime.now(timezone.utc) - timedelta(seconds=30)
    plain = PaperPositionProtection(
        account_id="paper-1", instrument_id=CRYPTO, stop_loss=Decimal("103"), status="active", revision=5,
        trail_moved_at=bar + timedelta(seconds=10),
        created_at=bar - timedelta(minutes=10), updated_at=bar - timedelta(minutes=10),
    )
    protections = _Protections(plain)
    repository = _MonitorRepository([], [position])
    asyncio.run(_monitor(repository, protections, _Market("104", high="105", low="99", bar_start=bar)).run_once())
    assert repository.placed == [] and protections.trails == []
