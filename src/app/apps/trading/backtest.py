from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Final, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .indicators.engine import CORE_INDICATOR_FORMULA_VERSION, simple_moving_average
from .replay import FrozenDatasetSnapshot


BACKTEST_MARK_TO_MARKET_POLICY: Final = "final_finalized_bar_close"


class MovingAverageCrossStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: Literal["sma_cross"] = "sma_cross"
    fast_period: int = Field(default=10, ge=1, le=500)
    slow_period: int = Field(default=30, ge=2, le=500)

    @model_validator(mode="after")
    def validate_periods(self):
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        return self


class BacktestExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fill_timing: Literal["next_bar_open"] = "next_bar_open"
    commission_bps: Decimal = Field(default=Decimal("0"), ge=0, le=1_000)
    slippage_bps: Decimal = Field(default=Decimal("0"), ge=0, le=1_000)
    position_size_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    allow_short: bool = False
    use_finalized_bars_only: bool = True

    @model_validator(mode="after")
    def validate_policy(self):
        # allow_short (TVP-7.2a): a cross down closes a long and opens a short; a cross up covers it and goes long.
        if not self.use_finalized_bars_only:
            raise ValueError("OTT-12 backtests require finalized bars")
        return self


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: MovingAverageCrossStrategy = Field(default_factory=MovingAverageCrossStrategy)
    execution_policy: BacktestExecutionPolicy = Field(default_factory=BacktestExecutionPolicy)
    initial_cash: Decimal = Field(default=Decimal("10000"), gt=0)
    formula_version: str = CORE_INDICATOR_FORMULA_VERSION

    @model_validator(mode="after")
    def validate_formula_version(self):
        if self.formula_version != CORE_INDICATOR_FORMULA_VERSION:
            raise ValueError(f"unsupported backtest formula version: {self.formula_version}")
        return self


class BacktestTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_index: int = Field(ge=0)
    side: Literal["buy", "sell"]
    signal_bar_index: int = Field(ge=0)
    fill_bar_index: int = Field(ge=1)
    signal_time: datetime
    fill_time: datetime
    quantity: Decimal
    fill_price: Decimal
    commission: Decimal
    cash_after: Decimal
    position_after: Decimal

    @model_validator(mode="after")
    def validate_next_bar_fill(self):
        if self.fill_bar_index != self.signal_bar_index + 1:
            raise ValueError("backtest fills must occur on the bar after the signal")
        if self.fill_time < self.signal_time:
            raise ValueError("fill_time cannot precede signal_time")
        return self


class BacktestEquityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    point_index: int
    bar_time: datetime
    cash: Decimal
    position: Decimal
    equity: Decimal
    drawdown_percent: Decimal


class BacktestLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    log_index: int
    bar_time: datetime | None = None
    level: Literal["info", "warning", "error"] = "info"
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


class BacktestArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    storage_provider: str
    storage_key: str
    checksum_sha256: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=1)


class BacktestRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    dataset_id: str
    dataset_fingerprint: str
    strategy_id: str
    strategy_parameters: dict[str, Any]
    execution_policy: dict[str, Any]
    formula_version: str
    status: Literal["completed", "failed"]
    initial_cash: Decimal
    ending_cash: Decimal
    ending_position: Decimal
    ending_mark_price: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    final_equity: Decimal
    total_return_percent: Decimal
    max_drawdown_percent: Decimal
    win_rate_percent: Decimal = Field(ge=0, le=100)
    exposure_percent: Decimal = Field(ge=0, le=100)
    trade_count: int
    mark_to_market_policy: Literal["final_finalized_bar_close"] = BACKTEST_MARK_TO_MARKET_POLICY
    economic_result_fingerprint: str = Field(min_length=64, max_length=64)
    started_at: datetime
    finished_at: datetime
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[BacktestEquityPoint, ...]
    logs: tuple[BacktestLogEntry, ...]
    artifact: BacktestArtifactReference | None = None
    error_message: str | None = None

    @model_validator(mode="before")
    @classmethod
    def populate_legacy_flat_result_economics(cls, value):
        """Upgrade pre-economics flat result constructors without hiding ambiguity.

        Older internal callers sometimes constructed a completed, flat result
        directly from aggregate fields. That state is deterministic: ending cash
        equals final equity, position/unrealized P&L are zero, and realized P&L is
        final equity minus initial cash. A legacy result that ends with an open
        position cannot be upgraded safely without a mark/cost basis, so it must
        provide the explicit economics fields.
        """
        if not isinstance(value, dict):
            return value
        required = {
            "ending_cash",
            "ending_position",
            "ending_mark_price",
            "realized_pnl",
            "unrealized_pnl",
            "economic_result_fingerprint",
        }
        if required <= set(value):
            return value

        data = dict(value)
        initial_cash = Decimal(str(data["initial_cash"]))
        final_equity = Decimal(str(data["final_equity"]))
        curve = data.get("equity_curve") or ()
        if curve:
            final_point = curve[-1]
            if isinstance(final_point, BacktestEquityPoint):
                ending_cash = final_point.cash
                ending_position = final_point.position
            else:
                ending_cash = Decimal(str(final_point["cash"]))
                ending_position = Decimal(str(final_point["position"]))
        else:
            ending_cash = final_equity
            ending_position = Decimal("0")

        if ending_position != 0 and not required <= set(data):
            raise ValueError(
                "legacy backtest result with an open position requires explicit ending economics"
            )

        data.setdefault("ending_cash", ending_cash)
        data.setdefault("ending_position", Decimal("0"))
        data.setdefault("ending_mark_price", None)
        data.setdefault("realized_pnl", final_equity - initial_cash)
        data.setdefault("unrealized_pnl", Decimal("0"))
        if "economic_result_fingerprint" not in data:
            legacy_payload = {
                "schema": "omnix-backtest-legacy-flat-v1",
                "dataset_fingerprint": data.get("dataset_fingerprint"),
                "strategy_id": data.get("strategy_id"),
                "strategy_parameters": data.get("strategy_parameters"),
                "execution_policy": data.get("execution_policy"),
                "formula_version": data.get("formula_version"),
                "status": data.get("status"),
                "initial_cash": str(initial_cash),
                "ending_cash": str(data["ending_cash"]),
                "ending_position": str(data["ending_position"]),
                "ending_mark_price": data["ending_mark_price"],
                "realized_pnl": str(data["realized_pnl"]),
                "unrealized_pnl": str(data["unrealized_pnl"]),
                "final_equity": str(final_equity),
                "total_return_percent": str(data.get("total_return_percent")),
                "max_drawdown_percent": str(data.get("max_drawdown_percent")),
                "win_rate_percent": str(data.get("win_rate_percent")),
                "exposure_percent": str(data.get("exposure_percent")),
                "trade_count": data.get("trade_count"),
                "mark_to_market_policy": data.get(
                    "mark_to_market_policy", BACKTEST_MARK_TO_MARKET_POLICY
                ),
            }
            canonical = json.dumps(
                legacy_payload,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            data["economic_result_fingerprint"] = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
        return data


class BacktestEconomicBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ending_cash: Decimal
    ending_position: Decimal
    ending_mark_price: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal


def _aligned_sma(closes: list[Decimal], period: int) -> dict[int, Decimal]:
    values = simple_moving_average(closes, period)
    return {index + period - 1: value for index, value in enumerate(values)}


def _commission(notional: Decimal, bps: Decimal) -> Decimal:
    return notional * bps / Decimal("10000")


def _fill_price(open_price: Decimal, side: str, slippage_bps: Decimal) -> Decimal:
    adjustment = slippage_bps / Decimal("10000")
    return open_price * (
        Decimal("1") + adjustment if side == "buy" else Decimal("1") - adjustment
    )


def _round_trips(
    trades: list[BacktestTrade] | tuple[BacktestTrade, ...],
) -> tuple[list[Decimal], Decimal, Decimal]:
    """Closed round trips' results, and the open trip's cash out (a long's cost) or in (a short's credit).

    A trade that starts from flat opens a trip: a buy pays its notional plus commission, a short sale
    (TVP-7.2a) receives its notional less commission. The next trade closes it.
    """
    results: list[Decimal] = []
    open_cost = Decimal("0")
    open_credit = Decimal("0")
    position = Decimal("0")
    for trade in trades:
        notional = trade.quantity * trade.fill_price
        if position == 0:
            if trade.side == "buy":
                open_cost = notional + trade.commission
            else:
                open_credit = notional - trade.commission
        elif position > 0:
            results.append(notional - trade.commission - open_cost)
            open_cost = Decimal("0")
        else:
            results.append(open_credit - notional - trade.commission)
            open_credit = Decimal("0")
        position = trade.position_after
    return results, open_cost, open_credit


def _round_trip_win_rate(trades: list[BacktestTrade]) -> Decimal:
    closed_results, _, _ = _round_trips(trades)
    if not closed_results:
        return Decimal("0")
    wins = sum(1 for value in closed_results if value > 0)
    return Decimal(wins) / Decimal(len(closed_results)) * Decimal("100")


def _exposure_percent(equity_curve: list[BacktestEquityPoint]) -> Decimal:
    if not equity_curve:
        return Decimal("0")
    exposed = sum(1 for point in equity_curve if point.position != 0)
    return Decimal(exposed) / Decimal(len(equity_curve)) * Decimal("100")


def backtest_economic_breakdown(
    *,
    initial_cash: Decimal,
    ending_cash: Decimal,
    ending_position: Decimal,
    ending_mark_price: Decimal | None,
    trades: list[BacktestTrade] | tuple[BacktestTrade, ...],
) -> BacktestEconomicBreakdown:
    closed, open_cost_basis, open_credit = _round_trips(trades)
    realized_pnl = sum(closed, Decimal("0"))
    unrealized_pnl = Decimal("0")
    if ending_position > 0 and ending_mark_price is not None:
        unrealized_pnl = ending_position * ending_mark_price - open_cost_basis
    elif ending_position < 0 and ending_mark_price is not None:
        unrealized_pnl = open_credit - abs(ending_position) * ending_mark_price
    if not trades and ending_position == 0:
        realized_pnl = ending_cash - initial_cash
    return BacktestEconomicBreakdown(
        ending_cash=ending_cash,
        ending_position=ending_position,
        ending_mark_price=ending_mark_price,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
    )


def backtest_economic_result_fingerprint(
    *,
    dataset_fingerprint: str,
    strategy_id: str,
    strategy_parameters: dict[str, Any],
    execution_policy: dict[str, Any],
    formula_version: str,
    status: str,
    initial_cash: Decimal,
    economics: BacktestEconomicBreakdown,
    final_equity: Decimal,
    total_return_percent: Decimal,
    max_drawdown_percent: Decimal,
    win_rate_percent: Decimal,
    exposure_percent: Decimal,
    trades: list[BacktestTrade] | tuple[BacktestTrade, ...],
    equity_curve: list[BacktestEquityPoint] | tuple[BacktestEquityPoint, ...],
    error_message: str | None = None,
) -> str:
    """Hash deterministic economic evidence, excluding runtime/storage identity."""
    payload = {
        "schema": "omnix-backtest-economics-v1",
        "dataset_fingerprint": dataset_fingerprint,
        "strategy_id": strategy_id,
        "strategy_parameters": strategy_parameters,
        "execution_policy": execution_policy,
        "formula_version": formula_version,
        "status": status,
        "initial_cash": str(initial_cash),
        "ending_cash": str(economics.ending_cash),
        "ending_position": str(economics.ending_position),
        "ending_mark_price": (
            str(economics.ending_mark_price)
            if economics.ending_mark_price is not None
            else None
        ),
        "realized_pnl": str(economics.realized_pnl),
        "unrealized_pnl": str(economics.unrealized_pnl),
        "final_equity": str(final_equity),
        "total_return_percent": str(total_return_percent),
        "max_drawdown_percent": str(max_drawdown_percent),
        "win_rate_percent": str(win_rate_percent),
        "exposure_percent": str(exposure_percent),
        "mark_to_market_policy": BACKTEST_MARK_TO_MARKET_POLICY,
        "trades": [
            {
                "trade_index": trade.trade_index,
                "side": trade.side,
                "signal_bar_index": trade.signal_bar_index,
                "fill_bar_index": trade.fill_bar_index,
                "quantity": str(trade.quantity),
                "fill_price": str(trade.fill_price),
                "commission": str(trade.commission),
                "cash_after": str(trade.cash_after),
                "position_after": str(trade.position_after),
            }
            for trade in trades
        ],
        "equity_curve": [
            {
                "point_index": point.point_index,
                "cash": str(point.cash),
                "position": str(point.position),
                "equity": str(point.equity),
                "drawdown_percent": str(point.drawdown_percent),
            }
            for point in equity_curve
        ],
        "error_message": error_message,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_backtest(
    snapshot: FrozenDatasetSnapshot,
    request: BacktestRequest,
    *,
    run_id: str | None = None,
    now: datetime | None = None,
) -> BacktestRunResult:
    started_at = now or datetime.now(timezone.utc)
    identifier = run_id or f"backtest-{uuid4().hex}"
    bars = list(snapshot.bars)
    minimum = request.strategy.slow_period + 1
    if len(bars) < minimum:
        finished_at = datetime.now(timezone.utc)
        ending_mark_price = bars[-1].close if bars else None
        economics = backtest_economic_breakdown(
            initial_cash=request.initial_cash,
            ending_cash=request.initial_cash,
            ending_position=Decimal("0"),
            ending_mark_price=ending_mark_price,
            trades=[],
        )
        error_message = f"dataset requires at least {minimum} bars"
        fingerprint = backtest_economic_result_fingerprint(
            dataset_fingerprint=snapshot.dataset_fingerprint,
            strategy_id=request.strategy.strategy_id,
            strategy_parameters=request.strategy.model_dump(mode="json"),
            execution_policy=request.execution_policy.model_dump(mode="json"),
            formula_version=request.formula_version,
            status="failed",
            initial_cash=request.initial_cash,
            economics=economics,
            final_equity=request.initial_cash,
            total_return_percent=Decimal("0"),
            max_drawdown_percent=Decimal("0"),
            win_rate_percent=Decimal("0"),
            exposure_percent=Decimal("0"),
            trades=[],
            equity_curve=[],
            error_message=error_message,
        )
        return BacktestRunResult(
            run_id=identifier,
            dataset_id=snapshot.dataset_id,
            dataset_fingerprint=snapshot.dataset_fingerprint,
            strategy_id=request.strategy.strategy_id,
            strategy_parameters=request.strategy.model_dump(mode="json"),
            execution_policy=request.execution_policy.model_dump(mode="json"),
            formula_version=request.formula_version,
            status="failed",
            initial_cash=request.initial_cash,
            ending_cash=economics.ending_cash,
            ending_position=economics.ending_position,
            ending_mark_price=economics.ending_mark_price,
            realized_pnl=economics.realized_pnl,
            unrealized_pnl=economics.unrealized_pnl,
            final_equity=request.initial_cash,
            total_return_percent=Decimal("0"),
            max_drawdown_percent=Decimal("0"),
            win_rate_percent=Decimal("0"),
            exposure_percent=Decimal("0"),
            trade_count=0,
            economic_result_fingerprint=fingerprint,
            started_at=started_at,
            finished_at=finished_at,
            trades=(),
            equity_curve=(),
            logs=(
                BacktestLogEntry(
                    log_index=0,
                    level="error",
                    message="insufficient dataset history",
                ),
            ),
            error_message=error_message,
        )

    closes = [bar.close for bar in bars]
    fast = _aligned_sma(closes, request.strategy.fast_period)
    slow = _aligned_sma(closes, request.strategy.slow_period)
    cash = request.initial_cash
    position = Decimal("0")
    pending: tuple[str, int, datetime] | None = None
    trades: list[BacktestTrade] = []
    equity_curve: list[BacktestEquityPoint] = []
    logs: list[BacktestLogEntry] = []
    peak = request.initial_cash
    max_drawdown = Decimal("0")

    for index, bar in enumerate(bars):
        if pending is not None:
            side, signal_bar_index, signal_time = pending
            open_price = _fill_price(
                bar.open,
                side,
                request.execution_policy.slippage_bps,
            )
            def record(quantity: Decimal, commission: Decimal, side: str = side) -> None:
                trades.append(
                    BacktestTrade(
                        trade_index=len(trades),
                        side=cast(Any, side),
                        signal_bar_index=signal_bar_index,
                        fill_bar_index=index,
                        signal_time=signal_time,
                        fill_time=bar.start_time,
                        quantity=quantity,
                        fill_price=open_price,
                        commission=commission,
                        cash_after=cash,
                        position_after=position,
                    )
                )
                logs.append(
                    BacktestLogEntry(
                        log_index=len(logs),
                        bar_time=bar.start_time,
                        message=f"filled {side} at next bar open",
                        payload={
                            "signal_bar_index": signal_bar_index,
                            "fill_bar_index": index,
                            "signal_time": signal_time.isoformat(),
                            "fill_price": str(open_price),
                        },
                    )
                )

            commission_bps = request.execution_policy.commission_bps
            if side == "buy" and position < 0:
                # Cover the short first (TVP-7.2a).
                quantity = abs(position)
                commission = _commission(quantity * open_price, commission_bps)
                cash -= quantity * open_price + commission
                position = Decimal("0")
                record(quantity, commission)
            elif side == "sell" and position > 0:
                quantity = position
                notional = quantity * open_price
                commission = _commission(notional, commission_bps)
                cash += notional - commission
                position = Decimal("0")
                record(quantity, commission)
            if position == 0 and (side == "buy" or request.execution_policy.allow_short):
                # A long buys with the budget; a short sells the same notional (no margin model until TVP-7.2b).
                budget = cash * request.execution_policy.position_size_fraction
                commission_rate = commission_bps / Decimal("10000")
                quantity = budget / (open_price * (Decimal("1") + commission_rate))
                # A balance a short's loss took below zero opens nothing (the sign would flip the side).
                if quantity > 0:
                    notional = quantity * open_price
                    commission = _commission(notional, commission_bps)
                    if side == "buy":
                        cash -= notional + commission
                        position += quantity
                    else:
                        cash += notional - commission
                        position -= quantity
                    record(quantity, commission)
            pending = None

        equity = cash + position * bar.close
        peak = max(peak, equity)
        drawdown = (
            Decimal("0")
            if peak == 0
            else (peak - equity) / peak * Decimal("100")
        )
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve.append(
            BacktestEquityPoint(
                point_index=index,
                bar_time=bar.end_time,
                cash=cash,
                position=position,
                equity=equity,
                drawdown_percent=drawdown,
            )
        )

        if index == 0 or index not in fast or index not in slow:
            continue
        previous_index = index - 1
        if previous_index not in fast or previous_index not in slow:
            continue
        crossed_above = (
            fast[previous_index] <= slow[previous_index]
            and fast[index] > slow[index]
        )
        crossed_below = (
            fast[previous_index] >= slow[previous_index]
            and fast[index] < slow[index]
        )
        allow_short = request.execution_policy.allow_short
        if crossed_above and (position == 0 or (allow_short and position < 0)):
            pending = ("buy", index, bar.end_time)
        elif crossed_below and (position > 0 or (allow_short and position == 0)):
            pending = ("sell", index, bar.end_time)

    final_equity = equity_curve[-1].equity
    total_return = (
        final_equity / request.initial_cash - Decimal("1")
    ) * Decimal("100")
    win_rate = _round_trip_win_rate(trades)
    exposure = _exposure_percent(equity_curve)
    ending_mark_price = bars[-1].close
    economics = backtest_economic_breakdown(
        initial_cash=request.initial_cash,
        ending_cash=cash,
        ending_position=position,
        ending_mark_price=ending_mark_price,
        trades=trades,
    )
    fingerprint = backtest_economic_result_fingerprint(
        dataset_fingerprint=snapshot.dataset_fingerprint,
        strategy_id=request.strategy.strategy_id,
        strategy_parameters=request.strategy.model_dump(mode="json"),
        execution_policy=request.execution_policy.model_dump(mode="json"),
        formula_version=request.formula_version,
        status="completed",
        initial_cash=request.initial_cash,
        economics=economics,
        final_equity=final_equity,
        total_return_percent=total_return,
        max_drawdown_percent=max_drawdown,
        win_rate_percent=win_rate,
        exposure_percent=exposure,
        trades=trades,
        equity_curve=equity_curve,
    )
    finished_at = datetime.now(timezone.utc)
    logs.append(
        BacktestLogEntry(
            log_index=len(logs),
            bar_time=bars[-1].end_time,
            message="backtest completed",
            payload={
                "dataset_fingerprint": snapshot.dataset_fingerprint,
                "trade_count": len(trades),
                "ending_cash": str(economics.ending_cash),
                "ending_position": str(economics.ending_position),
                "ending_mark_price": str(economics.ending_mark_price),
                "mark_to_market_policy": BACKTEST_MARK_TO_MARKET_POLICY,
                "realized_pnl": str(economics.realized_pnl),
                "unrealized_pnl": str(economics.unrealized_pnl),
                "final_equity": str(final_equity),
                "economic_result_fingerprint": fingerprint,
                "win_rate_percent": str(win_rate),
                "exposure_percent": str(exposure),
            },
        )
    )
    return BacktestRunResult(
        run_id=identifier,
        dataset_id=snapshot.dataset_id,
        dataset_fingerprint=snapshot.dataset_fingerprint,
        strategy_id=request.strategy.strategy_id,
        strategy_parameters=request.strategy.model_dump(mode="json"),
        execution_policy=request.execution_policy.model_dump(mode="json"),
        formula_version=request.formula_version,
        status="completed",
        initial_cash=request.initial_cash,
        ending_cash=economics.ending_cash,
        ending_position=economics.ending_position,
        ending_mark_price=economics.ending_mark_price,
        realized_pnl=economics.realized_pnl,
        unrealized_pnl=economics.unrealized_pnl,
        final_equity=final_equity,
        total_return_percent=total_return,
        max_drawdown_percent=max_drawdown,
        win_rate_percent=win_rate,
        exposure_percent=exposure,
        trade_count=len(trades),
        economic_result_fingerprint=fingerprint,
        started_at=started_at,
        finished_at=finished_at,
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        logs=tuple(logs),
    )
