"""Strategy scripts' broker emulator (TVP-11.5): orders, fills, trades and the strategy report.

A research backtest only: it fills simulated orders on the run's bars and never reaches an order gateway, a paper
account or a broker. It follows TradingView's broker emulator:

- the script runs at each bar's close; orders it places fill from the next bar on (``process_orders_on_close`` fills
  market orders at that close instead, and ``immediately`` closes do too);
- within a bar, price is assumed to move open -> nearer extreme -> farther extreme -> close, and pending orders fill
  where that path first reaches them (a gap past a level fills at the open);
- market orders fill at the open, limits at their price (or the open when it is better), stops at their price (or the
  open when it gapped past); slippage (in ticks) worsens market and stop fills;
- ``strategy.entry`` reverses an opposite position and respects ``pyramiding``; ``strategy.order`` simply buys or
  sells; closes go first in, first out; ``strategy.exit`` brackets each matching trade with a profit target, a stop
  and/or a trailing stop; OCA groups cancel or reduce their other orders.

Prices in ticks use ``syminfo.mintick`` (0.01 in Omnix Scripts).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

MINTICK = 0.01
LONG, SHORT = 1, -1
# Trades a report lists in full (the newest); the summary counts every trade.
MAX_REPORTED_TRADES = 5_000


class StrategyError(ValueError):
    pass


def _na(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _number(value: Any, name: str) -> float | None:
    if _na(value):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StrategyError(f"{name} must be a number")
    return float(value)


def direction_of(value: Any) -> int:
    if value in ("strategy.long", "long", True):
        return LONG
    if value in ("strategy.short", "short", False):
        return SHORT
    raise StrategyError("direction must be strategy.long or strategy.short")


@dataclass
class StrategySettings:
    initial_capital: float = 1_000_000.0
    pyramiding: int = 0
    default_qty_type: str = "strategy.fixed"
    default_qty_value: float = 1.0
    commission_type: str = "strategy.commission.percent"
    commission_value: float = 0.0
    slippage: int = 0
    process_orders_on_close: bool = False
    close_entries_rule: str = "FIFO"
    risk_free_rate: float = 2.0

    @classmethod
    def from_declaration(cls, declaration: dict[str, Any]) -> StrategySettings:
        settings = cls()
        for name in ("initial_capital", "default_qty_value", "commission_value", "risk_free_rate"):
            if name in declaration and not _na(declaration[name]):
                setattr(settings, name, float(declaration[name]))
        for name in ("pyramiding", "slippage"):
            if name in declaration and not _na(declaration[name]):
                setattr(settings, name, max(0, int(declaration[name])))
        for name in ("default_qty_type", "commission_type", "close_entries_rule"):
            if isinstance(declaration.get(name), str):
                setattr(settings, name, declaration[name])
        settings.process_orders_on_close = bool(declaration.get("process_orders_on_close", False))
        if settings.initial_capital <= 0:
            raise StrategyError("initial_capital must be more than 0")
        if settings.default_qty_type not in ("strategy.fixed", "strategy.cash", "strategy.percent_of_equity"):
            raise StrategyError("default_qty_type is strategy.fixed, strategy.cash or strategy.percent_of_equity")
        if settings.commission_type not in ("strategy.commission.percent", "strategy.commission.cash_per_contract", "strategy.commission.cash_per_order"):
            raise StrategyError("commission_type is strategy.commission.percent, cash_per_contract or cash_per_order")
        return settings

    def payload(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Order:
    id: str
    kind: str  # entry, order, close, exit
    direction: int  # the fill's side: +1 buys, -1 sells
    qty: float | None
    limit: float | None = None
    stop: float | None = None
    oca_name: str | None = None
    oca_type: str = "strategy.oca.none"
    comment: str | None = None
    placed_bar: int = 0
    triggered: bool = False  # a stop-limit whose stop was reached
    # close: the entry id it closes (None closes the whole position).
    target: str | None = None
    immediately: bool = False


@dataclass
class Exit:
    """``strategy.exit``: a bracket on each open trade of ``from_entry`` (every trade when it's empty)."""

    id: str
    from_entry: str | None
    qty: float | None
    qty_percent: float | None
    profit: float | None
    limit: float | None
    loss: float | None
    stop: float | None
    trail_price: float | None
    trail_points: float | None
    trail_offset: float | None
    comment: str | None
    oca_name: str | None
    done: set[int] = field(default_factory=set)  # trade numbers it has closed


@dataclass
class Trade:
    number: int
    entry_id: str
    direction: int
    qty: float
    entry_bar: int
    entry_time: int
    entry_price: float
    entry_comment: str | None
    entry_commission: float
    max_runup: float = 0.0
    max_drawdown: float = 0.0
    # Trailing stops: the best price since activation, by exit id.
    trail_best: dict[str, float] = field(default_factory=dict)
    exit_id: str | None = None
    exit_bar: int | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    exit_comment: str | None = None
    exit_commission: float = 0.0
    profit: float = 0.0
    cum_profit: float = 0.0

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.qty * self.direction

    def payload(self) -> dict[str, Any]:
        cost = self.entry_price * self.qty
        return {
            "number": self.number, "entry_id": self.entry_id, "direction": "long" if self.direction == LONG else "short",
            "qty": self.qty, "entry_bar": self.entry_bar, "entry_time": self.entry_time, "entry_price": self.entry_price,
            "entry_comment": self.entry_comment, "exit_id": self.exit_id, "exit_bar": self.exit_bar, "exit_time": self.exit_time,
            "exit_price": self.exit_price, "exit_comment": self.exit_comment, "profit": self.profit,
            "profit_percent": self.profit / cost * 100 if cost else 0.0, "cum_profit": self.cum_profit,
            "runup": self.max_runup, "drawdown": self.max_drawdown,
            "bars": (self.exit_bar if self.exit_bar is not None else self.entry_bar) - self.entry_bar,
            "commission": self.entry_commission + self.exit_commission,
        }


class Broker:
    """One run's simulated account. ``open_bar`` fills pending orders on a bar; the script then runs; ``close_bar``
    fills what fills at the close and marks the account."""

    def __init__(self, run: Any, settings: StrategySettings) -> None:
        self.run = run
        self.settings = settings
        self.orders: list[Order] = []
        self.exits: dict[str, Exit] = {}
        self.open_trades: list[Trade] = []
        self.closed_trades: list[Trade] = []
        self.trade_numbers = 0
        self.netprofit = 0.0
        self.commission_paid = 0.0
        self.fills: list[dict[str, Any]] = []
        self.equity: list[float] = []
        self.max_contracts = 0.0
        self.peak = settings.initial_capital
        self.max_drawdown = 0.0
        self.max_runup = 0.0
        self.trough = settings.initial_capital

    # --- State the script reads -------------------------------------------------------------------------------------

    @property
    def position_size(self) -> float:
        return sum(trade.qty * trade.direction for trade in self.open_trades)

    @property
    def position_avg_price(self) -> float | None:
        qty = sum(trade.qty for trade in self.open_trades)
        return sum(trade.entry_price * trade.qty for trade in self.open_trades) / qty if qty else None

    def openprofit(self, price: float | None = None) -> float:
        price = self.run.close[self.run.t] if price is None else price
        return sum(trade.unrealized(price) for trade in self.open_trades)

    def equity_now(self) -> float:
        return self.settings.initial_capital + self.netprofit + self.openprofit()

    # --- Orders the script places --------------------------------------------------------------------------------

    def _default_qty(self) -> float:
        price = self.run.close[self.run.t]
        kind, value = self.settings.default_qty_type, self.settings.default_qty_value
        if kind == "strategy.cash":
            return value / price if price else 0.0
        if kind == "strategy.percent_of_equity":
            return self.equity_now() * value / 100 / price if price else 0.0
        return value

    def _place(self, order: Order) -> None:
        # A new call with an id replaces the pending order with that id (Pine modifies it).
        self.orders = [item for item in self.orders if not (item.id == order.id and item.kind == order.kind)]
        self.orders.append(order)

    def entry(self, id: str, direction: Any, qty: Any, limit: Any, stop: Any, oca_name: Any, oca_type: Any, comment: Any) -> None:
        amount = _number(qty, "qty")
        self._place(Order(
            id=str(id), kind="entry", direction=direction_of(direction), qty=amount if amount is not None else self._default_qty(),
            limit=_number(limit, "limit"), stop=_number(stop, "stop"), oca_name=None if _na(oca_name) else str(oca_name),
            oca_type=str(oca_type or "strategy.oca.none"), comment=None if _na(comment) else str(comment), placed_bar=self.run.t,
        ))

    def order(self, id: str, direction: Any, qty: Any, limit: Any, stop: Any, oca_name: Any, oca_type: Any, comment: Any) -> None:
        amount = _number(qty, "qty")
        self._place(Order(
            id=str(id), kind="order", direction=direction_of(direction), qty=amount if amount is not None else self._default_qty(),
            limit=_number(limit, "limit"), stop=_number(stop, "stop"), oca_name=None if _na(oca_name) else str(oca_name),
            oca_type=str(oca_type or "strategy.oca.none"), comment=None if _na(comment) else str(comment), placed_bar=self.run.t,
        ))

    def close(self, id: Any, comment: Any, qty: Any, qty_percent: Any, immediately: Any) -> None:
        target = None if id is None else str(id)
        trades = [trade for trade in self.open_trades if target is None or trade.entry_id == target]
        held = sum(trade.qty for trade in trades)
        if not held:
            return
        amount = _number(qty, "qty")
        percent = _number(qty_percent, "qty_percent")
        size = min(held, amount) if amount is not None else held * min(100.0, percent) / 100 if percent is not None else held
        order = Order(
            id=f"close:{target or '*'}", kind="close", direction=-trades[0].direction, qty=size, comment=None if _na(comment) else str(comment),
            placed_bar=self.run.t, target=target, immediately=bool(immediately),
        )
        if order.immediately:
            self._fill(order, self.run.close[self.run.t], self.run.t, slip=False)
        else:
            self._place(order)

    def close_all(self, comment: Any, immediately: Any) -> None:
        self.close(None, comment, None, None, immediately)

    def exit(self, id: str, from_entry: Any, qty: Any, qty_percent: Any, profit: Any, limit: Any, loss: Any, stop: Any,
             trail_price: Any, trail_points: Any, trail_offset: Any, oca_name: Any, comment: Any) -> None:
        levels = [_number(value, name) for value, name in ((profit, "profit"), (limit, "limit"), (loss, "loss"), (stop, "stop"),
                                                              (trail_price, "trail_price"), (trail_points, "trail_points"))]
        if all(value is None for value in levels):
            raise StrategyError("strategy.exit() needs profit, limit, loss, stop or a trailing stop")
        key = str(id)
        previous = self.exits.get(key)
        self.exits[key] = Exit(
            id=key, from_entry=None if _na(from_entry) or from_entry == "" else str(from_entry), qty=_number(qty, "qty"),
            qty_percent=_number(qty_percent, "qty_percent"), profit=levels[0], limit=levels[1], loss=levels[2], stop=levels[3],
            trail_price=levels[4], trail_points=levels[5], trail_offset=_number(trail_offset, "trail_offset"),
            comment=None if _na(comment) else str(comment), oca_name=None if _na(oca_name) else str(oca_name),
            done=previous.done if previous else set(),
        )

    def cancel(self, id: Any) -> None:
        key = str(id)
        self.orders = [order for order in self.orders if order.id != key]
        self.exits.pop(key, None)

    def cancel_all(self) -> None:
        self.orders = []
        self.exits = {}

    # --- Filling -------------------------------------------------------------------------------------------------

    def _commission(self, price: float, qty: float) -> float:
        kind, value = self.settings.commission_type, self.settings.commission_value
        if kind == "strategy.commission.cash_per_contract":
            return value * qty
        if kind == "strategy.commission.cash_per_order":
            return value
        return price * qty * value / 100

    def _open(self, entry_id: str, direction: int, qty: float, price: float, bar: int, comment: str | None, commission: float) -> None:
        self.trade_numbers += 1
        self.open_trades.append(Trade(
            number=self.trade_numbers, entry_id=entry_id, direction=direction, qty=qty, entry_bar=bar, entry_time=self.run.time[bar],
            entry_price=price, entry_comment=comment, entry_commission=commission,
        ))

    def _reduce(self, trades: list[Trade], qty: float, price: float, bar: int, exit_id: str, comment: str | None, commission: float) -> float:
        """Close ``qty`` from ``trades`` in order (whole trades first); the commission is shared by the quantity closed."""
        remaining = qty
        closed_total = 0.0
        for trade in list(trades):
            if remaining <= 1e-12:
                break
            part = min(trade.qty, remaining)
            share = commission * part / qty if qty else 0.0
            entry_share = trade.entry_commission * part / trade.qty
            if part < trade.qty - 1e-12:
                # A partial close: the closed part becomes a trade of its own; the rest stays open, keeping its number
                # (so an exit that closed part of it doesn't fire on it again).
                self.trade_numbers += 1
                closed = Trade(
                    number=self.trade_numbers, entry_id=trade.entry_id, direction=trade.direction, qty=part, entry_bar=trade.entry_bar,
                    entry_time=trade.entry_time, entry_price=trade.entry_price, entry_comment=trade.entry_comment, entry_commission=entry_share,
                    max_runup=trade.max_runup, max_drawdown=trade.max_drawdown,
                )
                trade.qty -= part
                trade.entry_commission -= entry_share
            else:
                closed = trade
                self.open_trades.remove(trade)
            closed.exit_id, closed.exit_bar, closed.exit_time = exit_id, bar, self.run.time[bar]
            closed.exit_price, closed.exit_comment, closed.exit_commission = price, comment, share
            closed.profit = closed.unrealized(price) - closed.entry_commission - share
            self.netprofit += closed.profit
            closed.cum_profit = self.netprofit
            self.closed_trades.append(closed)
            remaining -= part
            closed_total += part
        return closed_total

    def _fill(self, order: Order, price: float, bar: int, *, slip: bool = True) -> bool:
        """Execute an order at ``price`` (with slippage on market and stop fills); False when it no longer applies."""
        if slip and self.settings.slippage and (order.limit is None or order.stop is not None):
            price += order.direction * self.settings.slippage * MINTICK
        position = self.position_size
        qty = order.qty or 0.0
        if qty <= 0:
            return False
        if order.kind == "close":
            trades = [trade for trade in self.open_trades if order.target is None or trade.entry_id == order.target]
            traded = min(qty, sum(trade.qty for trade in trades))
            if traded <= 0:
                return False
            commission = self._commission(price, traded)
            self._reduce(trades, traded, price, bar, order.id if order.target is None else order.target, order.comment, commission)
        elif order.kind == "entry":
            same = [trade for trade in self.open_trades if trade.direction == order.direction]
            if same and len(same) >= max(1, self.settings.pyramiding):
                return False  # pyramiding: no more entries in this direction
            opposite = [trade for trade in self.open_trades if trade.direction != order.direction]
            held = sum(trade.qty for trade in opposite)
            traded = held + qty
            commission = self._commission(price, traded)
            if held:
                # A reversal: the opposite position closes and the new one opens in one order.
                self._reduce(opposite, held, price, bar, order.id, order.comment, commission * held / traded)
            self._open(order.id, order.direction, qty, price, bar, order.comment, commission * qty / traded)
        else:  # strategy.order: buys or sells; against the position it closes first in, first out
            opposite = [trade for trade in self.open_trades if trade.direction != order.direction]
            traded = qty
            commission = self._commission(price, qty)
            closing = min(qty, sum(trade.qty for trade in opposite))
            if closing > 0:
                self._reduce(opposite, closing, price, bar, order.id, order.comment, commission * closing / qty)
            if qty - closing > 1e-12:
                self._open(order.id, order.direction, qty - closing, price, bar, order.comment, commission * (qty - closing) / qty)
        self.commission_paid += commission
        self.fills.append({
            "bar": bar, "time": self.run.time[bar], "price": price, "qty": traded, "side": "buy" if order.direction == LONG else "sell",
            "id": order.id, "comment": order.comment, "position": self.position_size,
        })
        self.max_contracts = max(self.max_contracts, abs(self.position_size), abs(position))
        if order in self.orders:
            self.orders.remove(order)
        self._oca(order.oca_name, order.oca_type, traded, order)
        return True

    def _oca(self, name: str | None, kind: str, qty: float, filled: Any) -> None:
        if not name or kind == "strategy.oca.none":
            return
        for other in [item for item in self.orders if item.oca_name == name and item is not filled]:
            if kind == "strategy.oca.cancel":
                self.orders.remove(other)
            elif kind == "strategy.oca.reduce" and other.qty is not None:
                other.qty -= qty
                if other.qty <= 1e-12:
                    self.orders.remove(other)

    def _exit_levels(self, exit: Exit, trade: Trade) -> tuple[float | None, float | None]:
        """A bracket's (target, stop) for a trade; the trailing stop, once active, tightens the stop."""
        d = trade.direction
        target = exit.limit if exit.limit is not None else trade.entry_price + d * exit.profit * MINTICK if exit.profit is not None else None
        stop = exit.stop if exit.stop is not None else trade.entry_price - d * exit.loss * MINTICK if exit.loss is not None else None
        best = trade.trail_best.get(exit.id)
        if best is not None:
            trail = best - d * (exit.trail_offset or 0) * MINTICK
            stop = trail if stop is None else (max(stop, trail) if d == LONG else min(stop, trail))
        return target, stop

    def _trail_activation(self, exit: Exit, trade: Trade) -> float | None:
        if exit.trail_price is not None:
            return exit.trail_price
        if exit.trail_points is not None:
            return trade.entry_price + trade.direction * exit.trail_points * MINTICK
        return None

    def _candidates(self, bar: int) -> list[tuple[str, Any, Any]]:
        """Pending orders and exit legs: (kind, order or (exit, trade, leg), level info)."""
        # Every pending order was placed on an earlier bar: the script runs after the bar's fills.
        items: list[tuple[str, Any, Any]] = [("order", order, None) for order in self.orders]
        for exit in self.exits.values():
            for trade in self.open_trades:
                if trade.number in exit.done or (exit.from_entry is not None and trade.entry_id != exit.from_entry):
                    continue
                items.append(("exit", (exit, trade), None))
        return items

    def _reach(self, kind: str, item: Any, low: float, high: float, start: float, up: bool) -> float | None:
        """Where on a monotone move from ``start`` (up or down, covering low..high) an order fills; None if it doesn't."""
        if kind == "order":
            order: Order = item
            buy = order.direction == LONG
            if order.limit is None and order.stop is None:
                return start
            if order.stop is not None and not order.triggered:
                stop_hit = (start >= order.stop) if buy else (start <= order.stop)
                stop_hit = stop_hit or (up and buy and high >= order.stop) or (not up and not buy and low <= order.stop)
                if not stop_hit:
                    return None
                at = start if ((start >= order.stop) if buy else (start <= order.stop)) else order.stop
                if order.limit is None:
                    return at
                order.triggered = True
                start = at
            if order.limit is not None:
                if (start <= order.limit) if buy else (start >= order.limit):
                    return start
                if buy and not up and low <= order.limit:
                    return order.limit
                if not buy and up and high >= order.limit:
                    return order.limit
            return None
        exit, trade = item
        target, stop = self._exit_levels(exit, trade)
        long = trade.direction == LONG
        hits: list[float] = []
        if target is not None:
            if (start >= target) if long else (start <= target):
                hits.append(start)
            elif long and up and high >= target:
                hits.append(target)
            elif not long and not up and low <= target:
                hits.append(target)
        if stop is not None:
            if (start <= stop) if long else (start >= stop):
                hits.append(start)
            elif long and not up and low <= stop:
                hits.append(stop)
            elif not long and up and high >= stop:
                hits.append(stop)
        if not hits:
            return None
        return min(hits, key=lambda price: abs(price - start))

    def _fill_exit(self, exit: Exit, trade: Trade, price: float, bar: int) -> None:
        qty = trade.qty
        if exit.qty is not None:
            qty = min(qty, exit.qty)
        elif exit.qty_percent is not None:
            qty = trade.qty * min(100.0, exit.qty_percent) / 100
        if self.settings.slippage:
            target, _ = self._exit_levels(exit, trade)
            if target is None or abs(price - target) > 1e-12:
                price -= trade.direction * self.settings.slippage * MINTICK
        exit.done.add(trade.number)
        commission = self._commission(price, qty)
        self.commission_paid += commission
        self._reduce([trade], qty, price, bar, exit.id, exit.comment, commission)
        self.fills.append({
            "bar": bar, "time": self.run.time[bar], "price": price, "qty": qty, "side": "sell" if trade.direction == LONG else "buy",
            "id": exit.id, "comment": exit.comment, "position": self.position_size,
        })

    def _walk(self, bar: int, points: list[float]) -> None:
        """Fill along the bar's price path: what the open already satisfies, then each move between extremes."""
        self._settle(bar, points[0], None)
        for start, end in zip(points, points[1:], strict=False):
            self._settle(bar, start, end)
            self._update_trails(end)

    def _settle(self, bar: int, start: float, end: float | None) -> None:
        """Fill, nearest first, what a move from ``start`` to ``end`` (or the point ``start``) reaches; each fill can
        enable or cancel others further along."""
        for _ in range(1_000):
            up = end is None or end >= start
            low, high = (start, start) if end is None else (min(start, end), max(start, end))
            best: tuple[float, str, Any, float] | None = None
            for kind, item, _unused in self._candidates(bar):
                at = self._reach(kind, item, low, high, start, up)
                if at is not None and (best is None or abs(at - start) < best[0]):
                    best = (abs(at - start), kind, item, at)
            if best is None:
                return
            _distance, kind, item, at = best
            if kind == "order":
                if not self._fill(item, at, bar) and item in self.orders:
                    self.orders.remove(item)
            else:
                exit, trade = item
                self._fill_exit(exit, trade, at, bar)
            start = at
        raise StrategyError("too many fills on one bar")

    def _update_trails(self, price: float) -> None:
        for exit in self.exits.values():
            for trade in self.open_trades:
                activation = self._trail_activation(exit, trade)
                if activation is None or (exit.from_entry is not None and trade.entry_id != exit.from_entry):
                    continue
                d = trade.direction
                best = trade.trail_best.get(exit.id)
                if best is None and (price - activation) * d >= 0:
                    trade.trail_best[exit.id] = price
                elif best is not None and (price - best) * d > 0:
                    trade.trail_best[exit.id] = price

    def open_bar(self, t: int) -> None:
        """Fill orders placed on earlier bars on bar ``t``'s price path."""
        if t == 0 or (not self.orders and not self.exits):
            return
        o, h, low, c = self.run.open[t], self.run.high[t], self.run.low[t], self.run.close[t]
        first, second = (h, low) if abs(h - o) <= abs(o - low) else (low, h)
        self._walk(t, [o, first, second, c])

    def close_bar(self, t: int) -> None:
        """After the script on bar ``t``: fills at the close, then marks the account at the close."""
        close = self.run.close[t]
        if self.settings.process_orders_on_close:
            for order in [item for item in self.orders if item.placed_bar == t and item.limit is None and item.stop is None]:
                if not self._fill(order, close, t, slip=False) and order in self.orders:
                    self.orders.remove(order)
        high, low = self.run.high[t], self.run.low[t]
        for trade in self.open_trades:
            favourable = (high if trade.direction == LONG else low) if trade.entry_bar < t else close
            adverse = (low if trade.direction == LONG else high) if trade.entry_bar < t else close
            trade.max_runup = max(trade.max_runup, trade.unrealized(favourable))
            trade.max_drawdown = min(trade.max_drawdown, trade.unrealized(adverse))
        # What exits remember of trades that closed is no longer needed.
        open_numbers = {trade.number for trade in self.open_trades}
        for exit in self.exits.values():
            exit.done &= open_numbers
        equity = self.equity_now()
        self.equity.append(equity)
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, self.peak - equity)
        self.trough = min(self.trough, equity)
        self.max_runup = max(self.max_runup, equity - self.trough)

    # --- Report --------------------------------------------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        settings = self.settings
        capital = settings.initial_capital
        closes = self.run.close
        first_open = self.run.open[0] if self.run.open else 0.0
        buy_hold = [capital * close / first_open if first_open else capital for close in closes]
        drawdown: list[float] = []
        peak = capital
        for value in self.equity:
            peak = max(peak, value)
            drawdown.append(value - peak)
        last_close = closes[-1] if closes else 0.0
        open_payload = []
        for trade in self.open_trades:
            item = trade.payload()
            item["profit"] = trade.unrealized(last_close) - trade.entry_commission
            open_payload.append(item)
        summary = {
            "all": _summary(self.closed_trades, self.open_trades, last_close),
            "long": _summary([trade for trade in self.closed_trades if trade.direction == LONG], [trade for trade in self.open_trades if trade.direction == LONG], last_close),
            "short": _summary([trade for trade in self.closed_trades if trade.direction == SHORT], [trade for trade in self.open_trades if trade.direction == SHORT], last_close),
        }
        final = self.equity[-1] if self.equity else capital
        summary["all"].update({
            "initial_capital": capital,
            "final_equity": final,
            "net_profit_percent": self.netprofit / capital * 100,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_percent": max((-value / (value_peak) * 100 for value, value_peak in _drawdown_pairs(self.equity, capital)), default=0.0),
            "max_runup": self.max_runup,
            "buy_hold_return": (buy_hold[-1] - capital) if buy_hold else 0.0,
            "buy_hold_return_percent": ((buy_hold[-1] - capital) / capital * 100) if buy_hold else 0.0,
            "commission_paid": self.commission_paid,
            "max_contracts_held": self.max_contracts,
            **_ratios(self.equity, self.run.time, settings.risk_free_rate),
        })
        return {
            "settings": settings.payload(),
            "summary": summary,
            "trades": [trade.payload() for trade in self.closed_trades[-MAX_REPORTED_TRADES:]],
            "trades_total": len(self.closed_trades),
            "open_trades": open_payload,
            "fills": self.fills[-MAX_REPORTED_TRADES * 2:],
            "equity": self.equity,
            "drawdown": drawdown,
            "buy_hold": buy_hold,
        }


def _drawdown_pairs(equity: list[float], capital: float) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    peak = capital
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            pairs.append((value - peak, peak))
    return pairs


def _summary(closed: list[Trade], open_trades: list[Trade], last_close: float) -> dict[str, Any]:
    profits = [trade.profit for trade in closed]
    wins = [trade for trade in closed if trade.profit > 0]
    losses = [trade for trade in closed if trade.profit < 0]
    gross_profit = sum(trade.profit for trade in wins)
    gross_loss = -sum(trade.profit for trade in losses)
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0

    def bars(trades: list[Trade]) -> float:
        return sum((trade.exit_bar or trade.entry_bar) - trade.entry_bar for trade in trades) / len(trades) if trades else 0.0

    return {
        "net_profit": sum(profits),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "open_pl": sum(trade.unrealized(last_close) for trade in open_trades),
        "total_closed_trades": len(closed),
        "total_open_trades": len(open_trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "even_trades": len(closed) - len(wins) - len(losses),
        "percent_profitable": len(wins) / len(closed) * 100 if closed else None,
        "avg_trade": sum(profits) / len(closed) if closed else None,
        "avg_trade_percent": statistics.fmean(trade.payload()["profit_percent"] for trade in closed) if closed else None,
        "avg_winning_trade": avg_win if wins else None,
        "avg_losing_trade": avg_loss if losses else None,
        "ratio_avg_win_loss": avg_win / avg_loss if wins and losses and avg_loss else None,
        "largest_winning_trade": max((trade.profit for trade in wins), default=None),
        "largest_losing_trade": -max((-trade.profit for trade in losses), default=0.0) if losses else None,
        "avg_bars_in_trades": bars(closed) if closed else None,
        "avg_bars_in_winning_trades": bars(wins) if wins else None,
        "avg_bars_in_losing_trades": bars(losses) if losses else None,
    }


def _ratios(equity: list[float], times: list[int], risk_free_rate: float) -> dict[str, float | None]:
    """Sharpe and Sortino from monthly returns of the equity (like TradingView; risk-free rate per year, in percent)."""
    months: dict[tuple[int, int], float] = {}
    for value, time in zip(equity, times, strict=False):
        moment = datetime.fromtimestamp(time / 1000, tz=timezone.utc)
        months[(moment.year, moment.month)] = value
    values = list(months.values())
    returns = [(after - before) / before for before, after in zip(values, values[1:], strict=False) if before]
    if len(returns) < 2:
        return {"sharpe_ratio": None, "sortino_ratio": None}
    excess = risk_free_rate / 100 / 12
    mean = statistics.fmean(returns) - excess
    deviation = statistics.pstdev(returns)
    downside = math.sqrt(sum(min(0.0, value - excess) ** 2 for value in returns) / len(returns))
    return {
        "sharpe_ratio": mean / deviation if deviation else None,
        "sortino_ratio": mean / downside if downside else None,
    }
