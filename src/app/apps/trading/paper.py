from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.apps.trading.us_equity_calendar import EASTERN, regular_close_time, regular_holidays


PaperSide = Literal["buy", "sell"]
PaperOrderType = Literal["market", "limit", "stop", "stop_limit", "trailing_stop"]
PaperOrderStatus = Literal["open", "filled", "cancelled", "rejected", "expired"]
# GTC is the default and was the only behaviour before TVP-7.1. DAY expires at
# the session close: for US equities the regular close (early closes
# included); every other market trades around the clock, so its day ends at
# midnight UTC. GTD expires at the order's own ``expires_at``.
PaperTimeInForce = Literal["gtc", "day", "gtd"]
ProtectionTrigger = Literal["stop", "target"]
TIME_IN_FORCE_EXPIRED = "time_in_force_expired"


class PaperExecutionPolicy(BaseModel):
    """Deterministic, pessimistic paper-fill assumptions shared with backtests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["paper-execution-v2"] = "paper-execution-v2"
    slippage_bps: Decimal = Field(default=Decimal("10"), ge=0, le=5_000)
    stop_slippage_bps: Decimal = Field(default=Decimal("25"), ge=0, le=10_000)
    max_volume_participation_pct: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    max_observation_age_seconds: Decimal = Field(default=Decimal("5"), gt=0, le=300)
    latency_ms: int = Field(default=250, ge=0, le=60_000)
    require_execution_eligible: bool = True
    reject_halted: bool = True


# The asset classes margin is set for (TVP-7.2b); an instrument's class is its id's prefix (``equity:NASDAQ:AAPL``).
PAPER_ASSET_CLASSES: tuple[str, ...] = ("equity", "crypto", "forex", "commodity")
CommissionType = Literal["percent", "fixed_per_order"]


class PaperMargin(BaseModel):
    """Margin for one asset class, as TradingView's paper account sets it (TVP-7.2b).

    The part of a position's value the account must hold: 100% is no leverage (the default), 50% is 2:1, 0.2% is 500:1.
    The same percentage is the maintenance requirement; equity below it triggers a margin call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    long_pct: Decimal = Field(default=Decimal("100"), ge=Decimal("0.1"), le=100)
    short_pct: Decimal = Field(default=Decimal("100"), ge=Decimal("0.1"), le=100)


def _check_margin_classes(margin: dict[str, PaperMargin] | None) -> None:
    unknown = sorted(set(margin or {}) - set(PAPER_ASSET_CLASSES))
    if unknown:
        raise ValueError(f"margin is set per asset class ({', '.join(PAPER_ASSET_CLASSES)}), not {', '.join(unknown)}")


class PaperAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    base_currency: str = Field(default="USD", min_length=3, max_length=12)
    initial_cash: Decimal = Field(default=Decimal("100000"), ge=0)
    commission_bps: Decimal = Field(default=Decimal("0"), ge=0, le=1000)
    # Off unless asked for: strategies create accounts through this model too.
    # The account form sends it on, as TradingView's paper accounts are (TVP-7.2a).
    allow_short: bool = False
    # TVP-7.2b: margin by asset class (none set: 100%, no leverage) and a fixed commission per order instead of bps.
    margin: dict[str, PaperMargin] = Field(default_factory=dict)
    commission_type: CommissionType = "percent"
    commission_fixed: Decimal = Field(default=Decimal("0"), ge=0, le=10_000)
    # TVP-7.2b: a margin call also notifies by the workspace's email and push channels; off unless asked for.
    notify_margin_calls: bool = False

    @model_validator(mode="after")
    def validate_margin(self) -> PaperAccountCreate:
        _check_margin_classes(self.margin)
        return self


class PaperAccountSettings(BaseModel):
    """Account settings a person changes after creating the account (TVP-7.2a, TVP-7.2b).

    Fields left out keep their value.
    """

    model_config = ConfigDict(extra="forbid")

    allow_short: bool | None = None
    margin: dict[str, PaperMargin] | None = None
    commission_type: CommissionType | None = None
    commission_bps: Decimal | None = Field(default=None, ge=0, le=1000)
    commission_fixed: Decimal | None = Field(default=None, ge=0, le=10_000)
    notify_margin_calls: bool | None = None

    @model_validator(mode="after")
    def validate_margin(self) -> PaperAccountSettings:
        _check_margin_classes(self.margin)
        return self


class PaperAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    name: str
    base_currency: str
    commission_bps: Decimal
    enabled: bool = True
    revision: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Whether a sell may open a short position (still an entry needing risk authority).
    allow_short: bool = False
    margin: dict[str, PaperMargin] = Field(default_factory=dict)
    commission_type: CommissionType = "percent"
    commission_fixed: Decimal = Decimal("0")
    notify_margin_calls: bool = False


class PaperBalance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    available: Decimal
    reserved: Decimal = Decimal("0")


class PaperPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    quantity: Decimal
    reserved_quantity: Decimal = Decimal("0")
    average_cost: Decimal
    realized_pnl: Decimal
    last_price: Decimal | None = None
    unrealized_pnl: Decimal = Decimal("0")


class PaperOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=200)
    instrument_id: str = Field(min_length=3, max_length=200)
    binding_id: str | None = Field(default=None, max_length=240)
    side: PaperSide
    order_type: PaperOrderType
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    reference_price: Decimal | None = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=1, max_length=240)
    time_in_force: PaperTimeInForce = "gtc"
    # Only for GTD. A DAY order's expiry is set by the server when it is placed.
    expires_at: datetime | None = None
    # A trailing stop trails the best price by exactly one of these.
    trail_amount: Decimal | None = Field(default=None, gt=0)
    trail_percent: Decimal | None = Field(default=None, gt=0, lt=100)

    @model_validator(mode="after")
    def validate_prices(self):
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.order_type == "stop" and self.stop_price is None:
            raise ValueError("stop orders require stop_price")
        if self.order_type == "stop_limit" and (self.stop_price is None or self.limit_price is None):
            raise ValueError("stop_limit orders require stop_price and limit_price")
        if self.order_type == "market" and (
            self.limit_price is not None or self.stop_price is not None
        ):
            raise ValueError("market orders cannot include limit_price or stop_price")
        if self.order_type == "trailing_stop":
            if (self.trail_amount is None) == (self.trail_percent is None):
                raise ValueError("trailing_stop orders require exactly one of trail_amount or trail_percent")
            if self.limit_price is not None or self.stop_price is not None:
                raise ValueError("trailing_stop orders cannot include limit_price or stop_price")
            if self.side == "buy" and self.reference_price is None:
                raise ValueError("buy trailing_stop orders require reference_price for the cash reservation")
        elif self.trail_amount is not None or self.trail_percent is not None:
            raise ValueError("trail_amount and trail_percent are only valid for trailing_stop orders")
        if self.order_type not in {"market", "trailing_stop"} and self.reference_price is not None:
            raise ValueError("reference_price is only valid for market and trailing_stop orders")
        if self.time_in_force == "gtd":
            if self.expires_at is None:
                raise ValueError("gtd orders require expires_at")
            if self.expires_at.tzinfo is None:
                raise ValueError("expires_at must be timezone-aware")
        elif self.expires_at is not None:
            raise ValueError("expires_at is only valid for gtd orders")
        return self


OrderAuthorityKind = Literal["reduce_only", "manual_risk", "strategy_entry"]


@dataclass(frozen=True, slots=True)
class OrderAuthority:
    """Why an order may open or add exposure (WP-8.3).

    ``reduce_only`` orders may only sell an unreserved long position.
    ``manual_risk`` orders passed the server's risk preview. ``strategy_entry``
    orders carry the strategy and trade attempt whose authorization the order
    gateway proved before placing them.
    """

    kind: OrderAuthorityKind
    strategy_id: str | None = None
    trade_attempt_id: str | None = None
    # Entries stop for the rest of the Eastern trading day once the account's
    # realized loss reaches this share of its equity, checked in the order's
    # own transaction.
    max_daily_loss_pct: Decimal | None = None

    @property
    def may_add_exposure(self) -> bool:
        return self.kind != "reduce_only"


class PaperOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    order_id: str
    instrument_id: str
    binding_id: str | None = None
    side: PaperSide
    order_type: PaperOrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    reference_price: Decimal | None = None
    status: PaperOrderStatus = "open"
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    idempotency_key: str
    rejection_reason: str | None = None
    reserved_cash: Decimal = Decimal("0")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    time_in_force: PaperTimeInForce = "gtc"
    expires_at: datetime | None = None
    trail_amount: Decimal | None = None
    trail_percent: Decimal | None = None
    # Server-tracked trailing state: the best price since the order was armed
    # (the highest for a sell, the lowest for a buy); ``stop_price`` holds the
    # stop it implies. Both are persisted, so a restart resumes the same trail.
    trail_water_mark: Decimal | None = None
    # When a stop-limit order's stop was reached; from then on it is a limit order.
    stop_triggered_at: datetime | None = None
    # When the trailing stop last moved. A bar's range can trigger the stop
    # only when the whole bar started at or after this moment: an earlier bar
    # may hold a low from before the high that moved the stop.
    trail_moved_at: datetime | None = None


class PaperOrderStateUpdate(BaseModel):
    """The trigger and trailing state an observation moves an open order to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stop_price: Decimal | None
    trail_water_mark: Decimal | None
    stop_triggered_at: datetime | None
    trail_moved_at: datetime | None = None


class PaperMarketObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    binding_id: str | None = None
    provider: str | None = None
    price: Decimal = Field(gt=0)
    bid: Decimal | None = Field(default=None, gt=0)
    ask: Decimal | None = Field(default=None, gt=0)
    bid_size: Decimal | None = Field(default=None, ge=0)
    ask_size: Decimal | None = Field(default=None, ge=0)
    high: Decimal | None = Field(default=None, gt=0)
    low: Decimal | None = Field(default=None, gt=0)
    volume: Decimal | None = Field(default=None, ge=0)
    bar_start_time: datetime | None = None
    source_time: datetime
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    execution_eligible: bool = True
    freshness_mode: str = "unknown"
    provider_sequence: int | None = None
    rejection_reasons: tuple[str, ...] = ()
    halted: bool = False

    @model_validator(mode="after")
    def validate_range(self):
        if self.source_time.tzinfo is None or self.evaluated_at.tzinfo is None:
            raise ValueError("paper observation timestamps must be timezone-aware")
        if self.bar_start_time is not None and self.bar_start_time.tzinfo is None:
            raise ValueError("paper bar_start_time must be timezone-aware")
        if self.high is not None and self.low is not None and self.low > self.high:
            raise ValueError("paper observation low cannot exceed high")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("paper observation bid cannot exceed ask")
        return self

    @property
    def age_seconds(self) -> Decimal:
        seconds = (self.evaluated_at - self.source_time).total_seconds()
        return max(Decimal("0"), Decimal(str(seconds)))


class PaperFillDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_fill: bool
    fill_price: Decimal | None = None
    fill_quantity: Decimal | None = None
    reason: str
    policy_version: str = "paper-execution-v2"


class PaperFill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fill_id: str
    order_id: str
    instrument_id: str
    side: PaperSide
    quantity: Decimal
    price: Decimal
    commission: Decimal
    source_time: datetime
    evaluated_at: datetime
    idempotency_key: str


class PaperLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ledger_id: str
    entry_type: Literal[
        "deposit", "withdrawal", "trade_cash", "commission", "realized_pnl"
    ]
    currency: str
    amount: Decimal
    order_id: str | None = None
    fill_id: str | None = None
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class PaperAccountSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: PaperAccount
    balances: list[PaperBalance]
    positions: list[PaperPosition]
    open_orders: list[PaperOrder]
    order_history: list[PaperOrder] = Field(default_factory=list)
    recent_fills: list[PaperFill]
    recent_ledger: list[PaperLedgerEntry]
    # TVP-7.2b: equity against the margin the positions need, at their last prices.
    margin_status: PaperMarginStatus | None = None


class PaperMarginStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equity: Decimal
    # The margin the open positions need (the maintenance requirement).
    maintenance: Decimal
    # Equity beyond what positions and working entries hold: what a new entry can use.
    buying_power: Decimal
    margin_call: bool


PaperAccountSnapshot.model_rebuild()


def paper_asset_class(instrument_id: str) -> str:
    """An instrument's asset class: its id's prefix, ``other`` when it isn't one margin is set for."""
    prefix = instrument_id.split(":", 1)[0].lower()
    return prefix if prefix in PAPER_ASSET_CLASSES else "other"


def paper_margin_fraction(account: PaperAccount, instrument_id: str, *, short: bool) -> Decimal:
    """The part of a position's value the account holds (1 is no leverage)."""
    margin = account.margin.get(paper_asset_class(instrument_id))
    if margin is None:
        return Decimal("1")
    return (margin.short_pct if short else margin.long_pct) / Decimal("100")


def paper_order_commission(account: PaperAccount, notional: Decimal, *, first_fill: bool) -> Decimal:
    """An order fill's commission: bps of its notional, or the fixed amount once per order (on its first fill)."""
    if account.commission_type == "fixed_per_order":
        return account.commission_fixed if first_fill else Decimal("0")
    return paper_commission(notional, account.commission_bps)


@dataclass(frozen=True)
class PaperMarginPosition:
    instrument_id: str
    quantity: Decimal
    average_cost: Decimal
    last_price: Decimal | None = None


def paper_buying_power(account: PaperAccount, available_cash: Decimal, positions: list[PaperMarginPosition]) -> Decimal:
    """Cash a new entry can hold (TVP-7.2b): equity less the margin positions need, less working holds.

    At each position's last price (its cost until one is known): a short holds what buying it back costs (its proceeds
    sit in the cash) and its margin; a long holds its margin share, the rest borrowed (the cash went below zero when it
    was bought). For unleveraged longs this is the available cash, as before; a short at 100% holds twice its value.
    """
    excess = available_cash
    for position in positions:
        price = position.last_price if position.last_price is not None else position.average_cost
        if position.quantity < 0:
            excess -= -position.quantity * price * (1 + paper_margin_fraction(account, position.instrument_id, short=True))
        elif position.quantity > 0:
            excess += position.quantity * price * (1 - paper_margin_fraction(account, position.instrument_id, short=False))
    return excess


def paper_margin_status(
    account: PaperAccount, available_cash: Decimal, reserved_cash: Decimal, positions: list[PaperMarginPosition]
) -> PaperMarginStatus:
    """Equity (cash plus longs less shorts at their last prices) against the maintenance the positions need."""
    equity = available_cash + reserved_cash
    maintenance = Decimal("0")
    for position in positions:
        price = position.last_price if position.last_price is not None else position.average_cost
        equity += position.quantity * price
        maintenance += abs(position.quantity) * price * paper_margin_fraction(account, position.instrument_id, short=position.quantity < 0)
    return PaperMarginStatus(
        equity=equity,
        maintenance=maintenance,
        buying_power=paper_buying_power(account, available_cash, positions),
        margin_call=equity < maintenance,
    )


# A margin call's orders carry this id prefix (the paper monitor places them; the web marks them as margin calls).
MARGIN_CALL_ORDER_PREFIX = "paper-margin-call-"


def paper_margin_call_closes(account: PaperAccount, status: PaperMarginStatus, positions: list[PaperMarginPosition]) -> list[tuple[str, Decimal]]:
    """What a margin call closes (TVP-7.2b): the positions using the most margin first, each only as far as needed.

    Closing part of a position frees its margin share; quantities are whole units where the position is whole.
    Returns (instrument, quantity to close) pairs; empty when equity covers the maintenance.
    """
    shortfall = status.maintenance - status.equity
    if shortfall <= 0:
        return []
    uses: list[tuple[Decimal, PaperMarginPosition, Decimal]] = []
    for position in positions:
        if position.quantity == 0:
            continue
        price = position.last_price if position.last_price is not None else position.average_cost
        per_unit = price * paper_margin_fraction(account, position.instrument_id, short=position.quantity < 0)
        uses.append((abs(position.quantity) * per_unit, position, per_unit))
    closes: list[tuple[str, Decimal]] = []
    for _, position, per_unit in sorted(uses, key=lambda item: (-item[0], item[1].instrument_id)):
        if shortfall <= 0:
            break
        size = abs(position.quantity)
        needed = shortfall / per_unit if per_unit > 0 else size
        # Whole units for whole positions; fractional ones (crypto) to 1e-8, rounded up so the close is enough.
        step = Decimal("1") if size == size.to_integral_value() else Decimal("0.00000001")
        needed = (needed / step).to_integral_value(rounding=ROUND_CEILING) * step
        quantity = min(size, needed)
        closes.append((position.instrument_id, quantity))
        shortfall -= quantity * per_unit
    return closes


def paper_order_request_matches(order: PaperOrder, request: PaperOrderRequest) -> bool:
    # A trailing stop's stop_price is server state that moves with the market,
    # and a DAY order's expiry is set by the server: neither is in the request.
    return (
        order.order_id == request.order_id
        and order.instrument_id == request.instrument_id
        and order.binding_id == request.binding_id
        and order.side == request.side
        and order.order_type == request.order_type
        and order.quantity == request.quantity
        and order.limit_price == request.limit_price
        and (order.order_type == "trailing_stop" or order.stop_price == request.stop_price)
        and order.reference_price == request.reference_price
        and order.time_in_force == request.time_in_force
        and (request.time_in_force != "gtd" or order.expires_at == request.expires_at)
        and order.trail_amount == request.trail_amount
        and order.trail_percent == request.trail_percent
    )


def _us_equity_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in regular_holidays(day.year)


def paper_day_expiry(instrument_id: str, placed_at: datetime) -> datetime:
    """When a DAY order placed at ``placed_at`` expires.

    US equities: the first regular-session close after placement, early closes
    included, so an order placed after the close, on a weekend or on a holiday
    lasts until the next session's close. Every other market trades around the
    clock, so its day is the UTC calendar day.
    """
    if placed_at.tzinfo is None:
        raise ValueError("paper order placement time must be timezone-aware")
    if instrument_id.startswith("equity:"):
        day = placed_at.astimezone(EASTERN).date()
        while True:
            if _us_equity_trading_day(day):
                close = datetime.combine(day, regular_close_time(day), tzinfo=EASTERN)
                if close > placed_at:
                    return close.astimezone(timezone.utc)
            day += timedelta(days=1)
    utc_day = placed_at.astimezone(timezone.utc).date()
    return datetime.combine(utc_day + timedelta(days=1), time(0), tzinfo=timezone.utc)


def paper_order_expiry(request: PaperOrderRequest, placed_at: datetime) -> datetime | None:
    """The expiry an order gets when it is placed; GTC orders never expire."""
    if request.time_in_force == "gtd":
        return request.expires_at
    if request.time_in_force == "day":
        return paper_day_expiry(request.instrument_id, placed_at)
    return None


def paper_order_is_expired(order: PaperOrder, moment: datetime) -> bool:
    return order.expires_at is not None and moment >= order.expires_at


def paper_price_tick(instrument_id: str) -> Decimal | None:
    """The instrument's minimum price increment from the catalog, or None if unknown."""
    from .catalog import instrument_by_id

    instrument = instrument_by_id(instrument_id)
    return instrument.minimum_tick if instrument is not None else None


def paper_round_to_tick(price: Decimal, tick_size: Decimal | None, *, up: bool) -> Decimal:
    """Round a price onto the tick grid, up or down; unchanged without a tick size."""
    if tick_size is None or tick_size <= 0:
        return price
    steps = (price / tick_size).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return steps * tick_size


def paper_trailing_stop_price(
    *,
    side: PaperSide,
    water_mark: Decimal,
    trail_amount: Decimal | None,
    trail_percent: Decimal | None,
    tick_size: Decimal | None = None,
) -> Decimal:
    """The stop a trail implies: below the high for a sell, above the low for a buy.

    With a tick size the stop is rounded away from the market (down for a
    sell, up for a buy), so the trail is never tighter than requested.
    """
    distance = (
        trail_amount
        if trail_amount is not None
        else water_mark * (trail_percent or Decimal("0")) / Decimal("100")
    )
    if side == "sell":
        return paper_round_to_tick(water_mark - distance, tick_size, up=False)
    return paper_round_to_tick(water_mark + distance, tick_size, up=True)


def _worse_price(price: Decimal, side: PaperSide, bps: Decimal) -> Decimal:
    fraction = bps / Decimal("10000")
    return price * (Decimal("1") + fraction if side == "buy" else Decimal("1") - fraction)


def _order_activation_time(
    order: PaperOrder,
    policy: PaperExecutionPolicy,
) -> datetime | None:
    if order.created_at is None:
        return None
    return order.created_at.astimezone(timezone.utc) + timedelta(milliseconds=policy.latency_ms)


def _bar_evidence_is_causal(
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy,
) -> bool:
    """Whether the whole observed bar happened after this order became executable.

    A bar that started before activation may contain a trigger or traded volume
    that occurred before the order existed. Only point-in-time quote/last-price
    evidence is safe in that case.
    """
    activation = _order_activation_time(order, policy)
    if activation is None:
        return True
    if observation.bar_start_time is None:
        return False
    return observation.bar_start_time.astimezone(timezone.utc) >= activation


def paper_liquidity_scope(
    order: PaperOrder,
    observation: PaperMarketObservation,
) -> str:
    """Return the shared simulation-liquidity bucket for one observation."""
    displayed = observation.ask_size if order.side == "buy" else observation.bid_size
    return f"book:{order.side}" if displayed is not None else "bar"


def _liquidity_capacity(
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy,
) -> Decimal | None:
    """Return side-specific executable capacity without pre-activation volume.

    Live observations prefer displayed top-of-book size. Historical/backtest
    observations do not have a quote book and may fall back to bar volume only
    when the complete bar starts after the order's activation time. Cumulative
    daily volume is intentionally not accepted here.
    """
    displayed = observation.ask_size if order.side == "buy" else observation.bid_size
    if displayed is not None:
        return displayed * policy.max_volume_participation_pct
    if observation.volume is None:
        return None
    if not _bar_evidence_is_causal(order, observation, policy):
        return Decimal("0")
    return observation.volume * policy.max_volume_participation_pct


def paper_liquidity_capacity(
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy | None = None,
) -> Decimal | None:
    """Return the maximum simulation participation for this observation bucket."""
    return _liquidity_capacity(order, observation, policy or PaperExecutionPolicy())


def paper_liquidity_allocation(
    order: PaperOrder,
    observation: PaperMarketObservation,
    requested_quantity: Decimal,
    consumed: dict[str, Decimal],
    policy: PaperExecutionPolicy | None = None,
) -> tuple[Decimal, str | None]:
    """Cap one paper fill by liquidity already consumed from the observation."""
    capacity = paper_liquidity_capacity(order, observation, policy)
    if capacity is None:
        return requested_quantity, None
    scope = paper_liquidity_scope(order, observation)
    available = max(Decimal("0"), capacity - consumed.get(scope, Decimal("0")))
    return min(requested_quantity, available), scope


def paper_protection_trigger(
    *,
    is_long: bool,
    stop_price: Decimal | None,
    target_price: Decimal | None,
    observation: PaperMarketObservation,
    activated_at: datetime | None = None,
    stop_moved_at: datetime | None = None,
) -> ProtectionTrigger | None:
    """Apply the same pessimistic stop-before-target trigger semantics everywhere.

    A live minute bar may contain trades that happened before an entry filled in
    that same minute. In that case only the current executable price is used; a
    whole-bar high/low is trusted only when the bar started at or after activation.
    The stop's range check also needs the bar to start strictly after the stop
    last moved (``stop_moved_at``, see ``paper_observation_moment``): an
    earlier or the same bar may hold a low from before the move.
    """

    def window(since: datetime | None, *, strict: bool) -> tuple[Decimal, Decimal]:
        use_range = observation.high is not None and observation.low is not None
        if since is not None:
            use_range = use_range and (
                _bar_starts_after(observation, since) if strict else _bar_evidence_is_after(observation, since)
            )
        high = observation.high if use_range and observation.high is not None else observation.price
        low = observation.low if use_range and observation.low is not None else observation.price
        return high, low

    high, low = window(activated_at, strict=False)
    stop_high, stop_low = high, low
    if stop_moved_at is not None and (activated_at is None or stop_moved_at >= activated_at):
        stop_high, stop_low = window(stop_moved_at, strict=True)
    if is_long:
        if stop_price is not None and stop_low <= stop_price:
            return "stop"
        if target_price is not None and high >= target_price:
            return "target"
    else:
        if stop_price is not None and stop_high >= stop_price:
            return "stop"
        if target_price is not None and low <= target_price:
            return "target"
    return None


def _observation_gate(
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy,
) -> str | None:
    """Why this observation cannot act on the order at all, or None."""
    if order.status != "open":
        return "order_not_open"
    if paper_order_is_expired(order, observation.source_time):
        return "order_expired"
    if order.instrument_id != observation.instrument_id:
        return "instrument_mismatch"
    if order.binding_id and order.binding_id != observation.binding_id:
        return "binding_mismatch"
    if policy.require_execution_eligible and not observation.execution_eligible:
        return "execution_data_ineligible"
    if observation.age_seconds > policy.max_observation_age_seconds:
        return "stale_market_data"
    if policy.reject_halted and observation.halted:
        return "market_halted"
    activation = _order_activation_time(order, policy)
    if activation is not None and observation.source_time.astimezone(timezone.utc) < activation:
        return "execution_latency_not_elapsed"
    return None


def _bar_evidence_is_after(observation: PaperMarketObservation, moment: datetime) -> bool:
    if observation.bar_start_time is None:
        return False
    return observation.bar_start_time.astimezone(timezone.utc) >= moment.astimezone(timezone.utc)


def _bar_starts_after(observation: PaperMarketObservation, moment: datetime) -> bool:
    if observation.bar_start_time is None:
        return False
    return observation.bar_start_time.astimezone(timezone.utc) > moment.astimezone(timezone.utc)


def paper_observation_moment(observation: PaperMarketObservation) -> datetime:
    """The moment a stop moved or triggered on this observation, for later range checks.

    The quote's ``source_time`` can be a few seconds older than the forming
    bar it arrives with, so the later of the two is used; only a bar that
    starts strictly after this moment is known to hold no earlier prices.
    """
    if observation.bar_start_time is not None and observation.bar_start_time > observation.source_time:
        return observation.bar_start_time
    return observation.source_time


def _price_window(
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy,
    *,
    since: datetime | None = None,
) -> tuple[Decimal | None, Decimal, Decimal, Decimal, bool]:
    """Return (market side, current, high, low, used bar range) for a resting order.

    The bar's range is used only when the whole bar happened after the order
    became executable and, with ``since`` (a trigger or a stop move), when
    the bar started strictly after that moment.
    """
    use_range = (
        observation.high is not None
        and observation.low is not None
        and _bar_evidence_is_causal(order, observation, policy)
        and (since is None or _bar_starts_after(observation, since))
    )
    market_side = observation.ask if order.side == "buy" else observation.bid
    current_price = market_side if market_side is not None else observation.price
    high = observation.high if use_range and observation.high is not None else current_price
    low = observation.low if use_range and observation.low is not None else current_price
    return market_side, current_price, high, low, use_range


def _stop_reached(side: PaperSide, stop_price: Decimal, high: Decimal, low: Decimal) -> bool:
    return high >= stop_price if side == "buy" else low <= stop_price


def _limit_fill_price(side: PaperSide, limit_price: Decimal, market_side: Decimal | None) -> Decimal:
    if market_side is None:
        return limit_price
    return min(limit_price, market_side) if side == "buy" else max(limit_price, market_side)


def _stop_limit_decision(
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy,
    fill_quantity: Decimal,
) -> PaperFillDecision:
    """A stop-limit order rests until its stop is reached, then is a limit order.

    In the observation that reaches the stop, the order fills only at the
    gap-through price, as a stop order would (the worse of the stop and the
    current executable price), and only when that is within the limit: a bar's
    range does not say whether its extreme came before or after the stop. A
    gap through both the stop and the limit leaves a resting limit order.
    After the trigger, a bar's range counts only when the bar started strictly
    after the trigger.
    """
    assert order.stop_price is not None and order.limit_price is not None
    if order.stop_triggered_at is None:
        _, current_price, high, low, _ = _price_window(order, observation, policy)
        if not _stop_reached(order.side, order.stop_price, high, low):
            return PaperFillDecision(should_fill=False, reason="stop_limit_not_triggered")
        if order.side == "buy":
            gap_through = max(order.stop_price, current_price)
            within_limit = gap_through <= order.limit_price
        else:
            gap_through = min(order.stop_price, current_price)
            within_limit = gap_through >= order.limit_price
        if not within_limit:
            return PaperFillDecision(should_fill=False, reason="stop_limit_price_not_reached")
        return PaperFillDecision(
            should_fill=True,
            fill_price=gap_through,
            fill_quantity=fill_quantity,
            reason="stop_limit_triggered_within_limit",
        )

    market_side, _, high, low, use_range = _price_window(
        order, observation, policy, since=order.stop_triggered_at
    )
    reached = low <= order.limit_price if order.side == "buy" else high >= order.limit_price
    if not reached:
        return PaperFillDecision(should_fill=False, reason="stop_limit_price_not_reached")
    return PaperFillDecision(
        should_fill=True,
        fill_price=_limit_fill_price(order.side, order.limit_price, market_side),
        fill_quantity=fill_quantity,
        reason="stop_limit_range_reached" if use_range else "stop_limit_current_market_reached",
    )


def paper_order_state_update(
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy | None = None,
    *,
    tick_size: Decimal | None = None,
) -> PaperOrderStateUpdate | None:
    """The persisted trigger/trailing state after this observation, or None if unchanged.

    The fill decision for the same observation uses the state from before it,
    which is the pessimistic order: a trailing stop is checked against its
    previous stop before the observation's high (low for a buy) raises
    (lowers) it, because a bar does not say which came first.

    - A stop-limit order records when its stop was first reached.
    - A trailing stop is armed by its first usable observation (water mark =
      current executable price), then follows the best price: a causal bar's
      high for a sell, its low for a buy, otherwise the current price. The stop
      only ever moves toward the market, rounded to ``tick_size`` when known,
      and the moment it moves is kept (``trail_moved_at``) so a bar that began
      before the move cannot trigger it by its range.
    """
    active = policy or PaperExecutionPolicy()
    if order.order_type not in {"stop_limit", "trailing_stop"}:
        return None
    if _observation_gate(order, observation, active) is not None:
        return None
    if order.order_type == "stop_limit":
        if order.stop_triggered_at is not None or order.stop_price is None:
            return None
        _, _, high, low, _ = _price_window(order, observation, active)
        if not _stop_reached(order.side, order.stop_price, high, low):
            return None
        return PaperOrderStateUpdate(
            stop_price=order.stop_price,
            trail_water_mark=None,
            stop_triggered_at=paper_observation_moment(observation),
        )

    _, current_price, high, low, _ = _price_window(order, observation, active)
    if order.trail_water_mark is None:
        water_mark = current_price
    elif order.side == "sell":
        water_mark = max(order.trail_water_mark, high)
    else:
        water_mark = min(order.trail_water_mark, low)
    if water_mark == order.trail_water_mark and order.stop_price is not None:
        return None
    stop_price = paper_trailing_stop_price(
        side=order.side,
        water_mark=water_mark,
        trail_amount=order.trail_amount,
        trail_percent=order.trail_percent,
        tick_size=tick_size,
    )
    return PaperOrderStateUpdate(
        stop_price=stop_price,
        trail_water_mark=water_mark,
        stop_triggered_at=None,
        trail_moved_at=paper_observation_moment(observation),
    )


def paper_trailing_protection_update(
    *,
    is_long: bool,
    stop_loss: Decimal,
    trail_amount: Decimal | None,
    trail_percent: Decimal | None,
    water_mark: Decimal | None,
    observation: PaperMarketObservation,
    activated_at: datetime | None = None,
    tick_size: Decimal | None = None,
) -> tuple[Decimal, Decimal] | None:
    """Move a trailing stop-loss bracket leg; return (water mark, stop) or None if unchanged.

    The water mark is the best price since the leg became active (a long's
    high, a short's low), using a bar's range only when the bar started at or
    after activation, as ``paper_protection_trigger`` does. The stop is the
    trail behind the water mark but never looser than the stop it already has,
    so the user's initial stop is the floor (the ceiling for a short).
    """
    use_range = observation.high is not None and observation.low is not None
    if activated_at is not None:
        use_range = use_range and _bar_evidence_is_after(observation, activated_at)
    high = observation.high if use_range and observation.high is not None else observation.price
    low = observation.low if use_range and observation.low is not None else observation.price
    extreme = high if is_long else low
    if water_mark is None:
        next_mark = extreme
    else:
        next_mark = max(water_mark, extreme) if is_long else min(water_mark, extreme)
    trailed = paper_trailing_stop_price(
        side="sell" if is_long else "buy",
        water_mark=next_mark,
        trail_amount=trail_amount,
        trail_percent=trail_percent,
        tick_size=tick_size,
    )
    next_stop = max(stop_loss, trailed) if is_long else min(stop_loss, trailed)
    if next_mark == water_mark and next_stop == stop_loss:
        return None
    return next_mark, next_stop


def paper_fill_decision(
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy | None = None,
    *,
    tick_size: Decimal | None = None,
) -> PaperFillDecision:
    """Whether and at what price the observation fills the order.

    ``tick_size`` rounds trailing stop fills to the instrument's tick, against
    the trader; other order types are priced as before.
    """
    active = policy or PaperExecutionPolicy()
    gate = _observation_gate(order, observation, active)
    if gate is not None:
        return PaperFillDecision(should_fill=False, reason=gate)

    remaining = max(Decimal("0"), order.quantity - order.filled_quantity)
    if remaining <= 0:
        return PaperFillDecision(should_fill=False, reason="order_already_filled")
    fill_quantity = remaining
    participation_capacity = _liquidity_capacity(order, observation, active)
    if participation_capacity is not None:
        if participation_capacity <= 0:
            return PaperFillDecision(should_fill=False, reason="no_causal_executable_volume")
        fill_quantity = min(remaining, participation_capacity)
        if fill_quantity <= 0:
            return PaperFillDecision(should_fill=False, reason="volume_participation_exceeded")

    if order.order_type == "market":
        base = (
            observation.ask
            if order.side == "buy" and observation.ask is not None
            else observation.bid
            if order.side == "sell" and observation.bid is not None
            else observation.price
        )
        return PaperFillDecision(
            should_fill=True,
            fill_price=_worse_price(base, order.side, active.slippage_bps),
            fill_quantity=fill_quantity,
            reason="market_execution_observation",
        )

    if order.order_type == "stop_limit":
        return _stop_limit_decision(order, observation, active, fill_quantity)

    # A trailing stop trusts a bar's range only from when its stop last moved.
    since = order.trail_moved_at if order.order_type == "trailing_stop" else None
    market_side, current_price, high, low, use_range = _price_window(order, observation, active, since=since)
    if order.order_type == "limit":
        assert order.limit_price is not None
        triggered = low <= order.limit_price if order.side == "buy" else high >= order.limit_price
        if not triggered:
            return PaperFillDecision(should_fill=False, reason="limit_range_not_reached")
        return PaperFillDecision(
            should_fill=True,
            fill_price=_limit_fill_price(order.side, order.limit_price, market_side),
            fill_quantity=fill_quantity,
            reason="limit_range_reached" if use_range else "limit_current_market_reached",
        )

    # A stop order, or a trailing stop at its current (pre-observation) stop.
    prefix = "trailing_" if order.order_type == "trailing_stop" else ""
    if order.stop_price is None:
        return PaperFillDecision(should_fill=False, reason="trailing_stop_not_armed")
    if not _stop_reached(order.side, order.stop_price, high, low):
        return PaperFillDecision(should_fill=False, reason=f"{prefix}stop_range_not_triggered")
    observed = current_price
    gap_through = max(order.stop_price, observed) if order.side == "buy" else min(order.stop_price, observed)
    fill_price = _worse_price(gap_through, order.side, active.stop_slippage_bps)
    if order.order_type == "trailing_stop":
        fill_price = paper_round_to_tick(fill_price, tick_size, up=order.side == "buy")
    return PaperFillDecision(
        should_fill=True,
        fill_price=fill_price,
        fill_quantity=fill_quantity,
        reason=(
            f"{prefix}stop_range_triggered_gap_aware"
            if use_range
            else f"{prefix}stop_current_market_triggered"
        ),
    )


def paper_commission(notional: Decimal, commission_bps: Decimal) -> Decimal:
    return notional * commission_bps / Decimal("10000")


def paper_buy_reservation(
    request: PaperOrderRequest,
    *,
    available_cash: Decimal,
    commission_bps: Decimal,
    margin: Decimal = Decimal("1"),
    fixed_commission: Decimal | None = None,
) -> Decimal:
    """Compute the cash hold for an open buy order: its margin share of the notional and its commission.

    reference_price is reservation-only evidence. It never authorizes a fill.
    """

    def hold(notional: Decimal) -> Decimal:
        commission = fixed_commission if fixed_commission is not None else paper_commission(notional, commission_bps)
        return notional * margin + commission

    if request.side != "buy":
        return Decimal("0")
    if request.order_type == "market":
        if request.reference_price is None:
            return available_cash
        return hold(request.quantity * request.reference_price)
    reference_price: Decimal | None
    if request.order_type == "trailing_stop":
        # Held at the stop the trail implies from the reference price; the
        # stop of a buy trail only moves down from where it is armed.
        assert request.reference_price is not None
        reference_price = paper_trailing_stop_price(
            side="buy",
            water_mark=request.reference_price,
            trail_amount=request.trail_amount,
            trail_percent=request.trail_percent,
        )
    elif request.order_type in {"limit", "stop_limit"}:
        # A stop-limit buy never pays more than its limit.
        reference_price = request.limit_price
    else:
        reference_price = request.stop_price
    assert reference_price is not None
    return hold(request.quantity * reference_price)


def paper_fill_is_fundable(
    order: PaperOrder,
    *,
    total_cost: Decimal,
    available_cash: Decimal,
) -> bool:
    if order.side != "buy":
        return True
    if order.order_type in {"market", "trailing_stop"}:
        # Both reserve from a reference price that is not a price bound.
        return order.reserved_cash + available_cash >= total_cost
    return order.reserved_cash >= total_cost


def paper_observation_key(observation: PaperMarketObservation) -> str:
    """Stable identity for one distinct paper-execution market observation."""
    raw = (
        f"{observation.instrument_id}|{observation.binding_id}|{observation.provider}|"
        f"{observation.source_time.isoformat()}|{observation.price}|"
        f"{observation.bid}|{observation.ask}|{observation.bid_size}|{observation.ask_size}|"
        f"{observation.high}|{observation.low}|{observation.volume}|"
        f"{observation.bar_start_time.isoformat() if observation.bar_start_time else None}|"
        f"{observation.execution_eligible}|{observation.freshness_mode}|"
        f"{observation.provider_sequence}|{','.join(observation.rejection_reasons)}|"
        f"{observation.halted}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def paper_fill_key(
    account_id: str,
    order_id: str,
    observation: PaperMarketObservation,
) -> str:
    raw = f"{account_id}|{order_id}|{paper_observation_key(observation)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def paper_unrealized_pnl(
    quantity: Decimal,
    average_cost: Decimal,
    last_price: Decimal,
) -> Decimal:
    return (last_price - average_cost) * quantity


def paper_realized_pnl(
    quantity: Decimal,
    average_cost: Decimal,
    fill_price: Decimal,
) -> Decimal:
    return (fill_price - average_cost) * quantity
