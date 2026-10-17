from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .gapper_dataset import GapperCandidate, GapperUniverseSnapshot
from .indicators.engine import relative_strength_index
from .models import MarketBar
from .paper import (
    PaperAccount,
    PaperAccountSnapshot,
    PaperBalance,
    PaperExecutionPolicy,
    PaperMarketObservation,
    PaperOrder,
    PaperPosition,
    paper_fill_decision,
    paper_protection_trigger,
)
from .research.policy import ResearchPolicyDecision
from .strategy_research_policy import apply_research_policy_to_quality
from .strategies import evaluate_gap_pullback
from .strategies.models import GapPullbackConfig, StrategyRiskProfile, StrategySignal
from .strategy_risk import size_strategy_entry
from .strategy_v2_management import v2_active_stop_for_prior_high, v2_management_levels
from .strategy_timeframes import proposal_priority, resample_final_bars
from app.apps.trading.us_equity_calendar import EASTERN as _ET


class BacktestSessionDataset(BaseModel):
    """Frozen multi-symbol morning dataset paired to one point-in-time universe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    universe: GapperUniverseSnapshot
    bars_by_instrument: dict[str, tuple[MarketBar, ...]]
    dataset_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def candidate_coverage(self):
        if self.session_date != self.universe.session_date:
            raise ValueError("backtest session_date must match frozen universe session_date")
        missing = [
            candidate.instrument_id
            for candidate in self.universe.candidates
            if candidate.instrument_id not in self.bars_by_instrument
        ]
        if missing:
            raise ValueError(f"missing candidate bars: {','.join(missing)}")
        for instrument_id, bars in self.bars_by_instrument.items():
            if any(bar.instrument_id != instrument_id for bar in bars):
                raise ValueError(f"backtest bars do not match map instrument: {instrument_id}")
            if any(not bar.is_final for bar in bars):
                raise ValueError(f"backtest requires finalized bars: {instrument_id}")
            if any(bar.interval != "1m" for bar in bars):
                raise ValueError(f"gap_pullback_v1 backtest requires canonical 1m source bars: {instrument_id}")
            if any(bar.start_time.astimezone(_ET).date() != self.session_date for bar in bars):
                raise ValueError(f"backtest bars cross session_date: {instrument_id}")
        return self


class GapPullbackBacktestTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    discovery_rank: int | None = None
    quality_score: int = Field(default=0, ge=0, le=10)
    structure_interval: str = "1m"
    execution_interval: str = "1m"
    entry_time: datetime
    exit_time: datetime
    signal_entry_price: Decimal
    signal_risk_per_share: Decimal
    entry_price: Decimal
    exit_price: Decimal
    requested_quantity: Decimal
    entry_fill_quantity: Decimal
    entry_slippage_bps: Decimal
    exit_slippage_bps: Decimal
    stop_price: Decimal
    target_price: Decimal
    exit_reason: Literal["stop", "target", "rsi", "time", "eod"]
    pnl_per_share: Decimal
    r_multiple: Decimal
    mfe_r: Decimal
    mae_r: Decimal
    hold_minutes: Decimal
    trigger_bar_index: int
    entry_bar_index: int


class GapPullbackBacktestCandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    discovery_rank: int | None = None
    decision_at: datetime
    state: str
    rejection_reason: str | None = None
    triggered: bool = False
    quality_score: int | None = Field(default=None, ge=0, le=10)
    selected_trade: bool = False
    entry_time: datetime | None = None
    exit_time: datetime | None = None


class GapPullbackBacktestSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_count: int
    trigger_count: int
    trade_count: int
    no_next_bar_count: int
    entry_execution_rejection_count: int
    exit_execution_rejection_count: int
    invalid_risk_count: int
    portfolio_capacity_rejection_count: int
    risk_rejection_count: int
    risk_rejection_reasons: dict[str, int] = Field(default_factory=dict)
    research_rejection_count: int = 0
    research_rejection_reasons: dict[str, int] = Field(default_factory=dict)
    partial_entry_count: int
    win_count: int
    loss_count: int
    win_rate: Decimal
    expectancy_r: Decimal
    expectancy_r_ci95_low: Decimal | None = None
    expectancy_r_ci95_high: Decimal | None = None
    profit_factor: Decimal | None
    average_mfe_r: Decimal
    average_mae_r: Decimal
    candidate_to_trigger_rate: Decimal
    trigger_to_trade_rate: Decimal
    candidate_to_trade_rate: Decimal
    average_hold_minutes: Decimal
    average_entry_slippage_bps: Decimal
    average_exit_slippage_bps: Decimal
    trades_per_day: Decimal
    stop_count: int
    target_count: int
    indicator_exit_count: int


class GapPullbackBacktestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = "gap_pullback_v1"
    strategy_version: str = "1.0.0"
    dataset_fingerprint: str
    execution_policy_version: str
    risk_policy: StrategyRiskProfile
    initial_cash: Decimal
    trades: tuple[GapPullbackBacktestTrade, ...]
    candidate_decisions: tuple[GapPullbackBacktestCandidateDecision, ...] = ()
    summary: GapPullbackBacktestSummary


@dataclass(frozen=True)
class _TradeAttempt:
    trigger_bar_index: int | None = None
    trade: GapPullbackBacktestTrade | None = None
    rejection_reason: str | None = None

    @property
    def triggered(self) -> bool:
        return self.trigger_bar_index is not None


def freeze_backtest_session(
    *,
    session_date: date,
    universe: GapperUniverseSnapshot,
    bars_by_instrument: Mapping[str, Sequence[MarketBar]],
) -> BacktestSessionDataset:
    if session_date != universe.session_date:
        raise ValueError("backtest session_date must match frozen universe session_date")
    normalized = {
        instrument_id: tuple(sorted(bars, key=lambda bar: bar.start_time))
        for instrument_id, bars in sorted(bars_by_instrument.items())
    }
    payload = {
        "session_date": session_date.isoformat(),
        "universe_fingerprint": universe.source_fingerprint,
        "bars": {
            instrument_id: [
                {
                    "start": bar.start_time.astimezone(timezone.utc).isoformat(),
                    "end": bar.end_time.astimezone(timezone.utc).isoformat(),
                    "o": str(bar.open),
                    "h": str(bar.high),
                    "l": str(bar.low),
                    "c": str(bar.close),
                    "v": str(bar.volume),
                    "provider": bar.provider,
                    "final": bar.is_final,
                    "session": bar.session,
                    "adjustment_mode": str(bar.adjustment_mode),
                }
                for bar in bars
            ]
            for instrument_id, bars in normalized.items()
        },
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return BacktestSessionDataset(
        session_date=session_date,
        universe=universe,
        bars_by_instrument=normalized,
        dataset_fingerprint=fingerprint,
    )


def _bar_observation(
    bar: MarketBar,
    *,
    instrument_id: str,
    binding_id: str | None,
    spread_bps: Decimal,
    price: Decimal,
) -> PaperMarketObservation:
    half = spread_bps / Decimal("20000")
    return PaperMarketObservation(
        instrument_id=instrument_id,
        binding_id=binding_id,
        provider=f"backtest:{bar.provider}",
        price=price,
        bid=price * (Decimal("1") - half),
        ask=price * (Decimal("1") + half),
        high=bar.high,
        low=bar.low,
        volume=bar.volume,
        bar_start_time=bar.start_time,
        source_time=bar.start_time,
        evaluated_at=bar.start_time,
        execution_eligible=True,
        freshness_mode="historical",
    )


def _adverse_buy_slippage_bps(fill: Decimal, reference: Decimal) -> Decimal:
    if reference <= 0:
        return Decimal("0")
    return (fill - reference) / reference * Decimal("10000")


def _adverse_sell_slippage_bps(fill: Decimal, reference: Decimal) -> Decimal:
    if reference <= 0:
        return Decimal("0")
    return (reference - fill) / reference * Decimal("10000")


def _exit_market_decision(
    *,
    candidate,
    bar: MarketBar,
    quantity: Decimal,
    policy: PaperExecutionPolicy,
    assumed_spread_bps: Decimal,
    price: Decimal,
    order_suffix: str,
):
    order = PaperOrder(
        account_id="backtest",
        order_id=f"{order_suffix}:{candidate.instrument_id}:{bar.start_time.isoformat()}",
        instrument_id=candidate.instrument_id,
        binding_id=candidate.binding_id,
        side="sell",
        order_type="market",
        quantity=quantity,
        idempotency_key=f"{order_suffix}:{candidate.instrument_id}:{bar.start_time.isoformat()}",
    )
    return paper_fill_decision(
        order,
        _bar_observation(
            bar,
            instrument_id=candidate.instrument_id,
            binding_id=candidate.binding_id,
            spread_bps=assumed_spread_bps,
            price=price,
        ),
        policy,
    )


def _find_trade(
    candidate,
    bars: tuple[MarketBar, ...],
    config: GapPullbackConfig,
    policy: PaperExecutionPolicy,
    *,
    assumed_spread_bps: Decimal,
    max_hold_minutes: int,
    quantity: Decimal = Decimal("1"),
) -> _TradeAttempt:
    execution_bars = tuple(resample_final_bars(bars, config.execution_interval))
    structure_bars = tuple(resample_final_bars(bars, config.structure_interval))
    if not execution_bars or not structure_bars:
        return _TradeAttempt()

    trigger_index: int | None = None
    signal = None
    trigger_time: datetime | None = None
    for index in range(1, len(structure_bars) + 1):
        result = evaluate_gap_pullback(candidate, structure_bars[:index], config)
        if result.state == "entry_ready" and result.signal is not None:
            trigger_time = structure_bars[index - 1].end_time
            signal = result.signal
            eligible_trigger_indexes = [
                execution_index
                for execution_index, bar in enumerate(execution_bars)
                if bar.end_time <= trigger_time
            ]
            trigger_index = eligible_trigger_indexes[-1] if eligible_trigger_indexes else None
            break
    if trigger_index is None or trigger_time is None or signal is None:
        return _TradeAttempt()

    entry_index = next(
        (
            index
            for index, bar in enumerate(execution_bars)
            if bar.start_time >= trigger_time
        ),
        None,
    )
    if entry_index is None:
        return _TradeAttempt(
            trigger_bar_index=trigger_index,
            rejection_reason="no_next_bar",
        )

    entry_bar = execution_bars[entry_index]
    entry_order = PaperOrder(
        account_id="backtest",
        order_id=f"entry:{candidate.instrument_id}:{entry_index}",
        instrument_id=candidate.instrument_id,
        binding_id=candidate.binding_id,
        side="buy",
        order_type="market",
        quantity=quantity,
        idempotency_key=f"entry:{candidate.instrument_id}:{entry_index}",
    )
    entry_decision = paper_fill_decision(
        entry_order,
        _bar_observation(
            entry_bar,
            instrument_id=candidate.instrument_id,
            binding_id=candidate.binding_id,
            spread_bps=assumed_spread_bps,
            price=entry_bar.open,
        ),
        policy,
    )
    if (
        not entry_decision.should_fill
        or entry_decision.fill_price is None
        or entry_decision.fill_quantity is None
        or entry_decision.fill_quantity <= 0
    ):
        return _TradeAttempt(
            trigger_bar_index=trigger_index,
            rejection_reason=f"entry_execution:{entry_decision.reason}",
        )
    entry = entry_decision.fill_price
    entry_quantity = min(quantity, entry_decision.fill_quantity)
    entry_slippage_bps = _adverse_buy_slippage_bps(entry, entry_bar.open)
    stop = signal.stop_price
    risk = entry - stop
    if risk <= 0:
        return _TradeAttempt(
            trigger_bar_index=trigger_index,
            rejection_reason="invalid_risk_distance",
        )
    management = None
    horizon: datetime | None = None
    if config.strategy_version == "2.0.0":
        management = v2_management_levels(
            config,
            entry_price=entry,
            initial_stop=stop,
        )
        target = management.target_price
        horizon = entry_bar.start_time + timedelta(minutes=management.max_hold_minutes)
    else:
        target = entry + risk * config.reward_multiple
    if management is not None:
        horizon = entry_bar.start_time + timedelta(minutes=management.max_hold_minutes)
    max_high = entry
    min_low = entry

    # RSI is calculated from the execution stream so the exit can react at the
    # configured execution resolution. The indicator implementation is causal:
    # the value mapped to bar N only uses closes through bar N.
    rsi_values = relative_strength_index(
        [bar.close for bar in execution_bars],
        config.exit_rsi_period,
    )

    exit_price: Decimal | None = None
    exit_time: datetime | None = None
    exit_reference: Decimal | None = None
    exit_reason: Literal["stop", "target", "rsi", "time", "eod"] | None = None
    last_exit_rejection: str | None = None
    rsi_exit_requested = False

    for index in range(entry_index, len(execution_bars)):
        bar = execution_bars[index]
        # V11 management is causal: a bar may arm the protected stop only after
        # it is finalized, so the stop selected here uses prior bars' max high.
        active_stop = stop
        if management is not None:
            active_stop = v2_active_stop_for_prior_high(
                config,
                entry_price=entry,
                initial_stop=stop,
                prior_finalized_high=max_high,
            )
        max_high = max(max_high, bar.high)
        min_low = min(min_low, bar.low)
        observation = _bar_observation(
            bar,
            instrument_id=candidate.instrument_id,
            binding_id=candidate.binding_id,
            spread_bps=assumed_spread_bps,
            price=bar.open,
        )
        trigger = paper_protection_trigger(
            is_long=True,
            stop_price=active_stop,
            target_price=target,
            observation=observation,
            activated_at=entry_bar.start_time,
        )
        if trigger == "stop":
            stop_order = PaperOrder(
                account_id="backtest",
                order_id=f"stop:{candidate.instrument_id}:{index}",
                instrument_id=candidate.instrument_id,
                binding_id=candidate.binding_id,
                side="sell",
                order_type="stop",
                quantity=entry_quantity,
                stop_price=active_stop,
                idempotency_key=f"stop:{candidate.instrument_id}:{index}",
            )
            decision = paper_fill_decision(stop_order, observation, policy)
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
                and decision.fill_quantity >= entry_quantity
            ):
                exit_price = decision.fill_price
                exit_time = bar.end_time
                exit_reference = active_stop
                exit_reason = "stop"
                break
            last_exit_rejection = f"exit_execution:{decision.reason}"
        elif trigger == "target":
            target_order = PaperOrder(
                account_id="backtest",
                order_id=f"target:{candidate.instrument_id}:{index}",
                instrument_id=candidate.instrument_id,
                binding_id=candidate.binding_id,
                side="sell",
                order_type="limit",
                quantity=entry_quantity,
                limit_price=target,
                idempotency_key=f"target:{candidate.instrument_id}:{index}",
            )
            decision = paper_fill_decision(target_order, observation.model_copy(update={"price": target}), policy)
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
                and decision.fill_quantity >= entry_quantity
            ):
                exit_price = decision.fill_price
                exit_time = bar.end_time
                exit_reference = target
                exit_reason = "target"
                break
            last_exit_rejection = f"exit_execution:{decision.reason}"
        rsi_index = index - config.exit_rsi_period
        previous_rsi_index = rsi_index - 1
        if (
            previous_rsi_index >= 0
            and rsi_index >= 0
            and rsi_index < len(rsi_values)
            and rsi_values[previous_rsi_index] >= config.exit_rsi_threshold
            and rsi_values[rsi_index] < config.exit_rsi_threshold
        ):
            rsi_exit_requested = True

        if rsi_exit_requested:
            decision = _exit_market_decision(
                candidate=candidate,
                bar=bar,
                quantity=entry_quantity,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
                price=bar.close,
                order_suffix="rsi",
            )
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
                and decision.fill_quantity >= entry_quantity
            ):
                exit_price = decision.fill_price
                exit_time = bar.end_time
                exit_reference = bar.close
                exit_reason = "rsi"
                break
            last_exit_rejection = f"exit_execution:{decision.reason}"

        if horizon is not None and bar.end_time >= horizon:
            decision = _exit_market_decision(
                candidate=candidate,
                bar=bar,
                quantity=entry_quantity,
                policy=policy,
                assumed_spread_bps=assumed_spread_bps,
                price=bar.close,
                order_suffix="time",
            )
            if (
                decision.should_fill
                and decision.fill_price is not None
                and decision.fill_quantity is not None
                and decision.fill_quantity >= entry_quantity
            ):
                exit_price = decision.fill_price
                exit_time = bar.end_time
                exit_reference = bar.close
                exit_reason = "time"
                break
            last_exit_rejection = f"exit_execution:{decision.reason}"

    if exit_price is None:
        last_bar = execution_bars[-1]
        decision = _exit_market_decision(
            candidate=candidate,
            bar=last_bar,
            quantity=entry_quantity,
            policy=policy,
            assumed_spread_bps=assumed_spread_bps,
            price=last_bar.close,
            order_suffix="eod",
        )
        if (
            decision.should_fill
            and decision.fill_price is not None
            and decision.fill_quantity is not None
            and decision.fill_quantity >= entry_quantity
        ):
            exit_price = decision.fill_price
            exit_time = last_bar.end_time
            exit_reference = last_bar.close
            exit_reason = "eod"
        else:
            last_exit_rejection = f"exit_execution:{decision.reason}"

    if exit_price is None or exit_time is None or exit_reference is None or exit_reason is None:
        return _TradeAttempt(
            trigger_bar_index=trigger_index,
            rejection_reason=last_exit_rejection or "exit_execution:unfilled",
        )

    pnl = exit_price - entry
    r_multiple = pnl / risk
    mfe_r = (max_high - entry) / risk
    mae_r = (min_low - entry) / risk
    hold_minutes = Decimal(str((exit_time - entry_bar.start_time).total_seconds() / 60))
    trade = GapPullbackBacktestTrade(
        instrument_id=candidate.instrument_id,
        discovery_rank=candidate.discovery_rank,
        quality_score=signal.quality_score,
        structure_interval=config.structure_interval,
        execution_interval=config.execution_interval,
        entry_time=entry_bar.start_time,
        exit_time=exit_time,
        signal_entry_price=signal.entry_price,
        signal_risk_per_share=signal.risk_per_share,
        entry_price=entry,
        exit_price=exit_price,
        requested_quantity=quantity,
        entry_fill_quantity=entry_quantity,
        entry_slippage_bps=entry_slippage_bps,
        exit_slippage_bps=_adverse_sell_slippage_bps(exit_price, exit_reference),
        stop_price=stop,
        target_price=target,
        exit_reason=exit_reason,
        pnl_per_share=pnl,
        r_multiple=r_multiple,
        mfe_r=mfe_r,
        mae_r=mae_r,
        hold_minutes=hold_minutes,
        trigger_bar_index=trigger_index,
        entry_bar_index=entry_index,
    )
    return _TradeAttempt(trigger_bar_index=trigger_index, trade=trade)


def _virtual_snapshot(
    selected: list[GapPullbackBacktestTrade],
    *,
    entry_time: datetime,
    initial_cash: Decimal,
) -> tuple[PaperAccountSnapshot, Decimal, Decimal, set[str]]:
    closed = [trade for trade in selected if trade.exit_time <= entry_time]
    active = [trade for trade in selected if trade.entry_time <= entry_time < trade.exit_time]
    realized = sum(
        (trade.pnl_per_share * trade.entry_fill_quantity for trade in closed),
        Decimal("0"),
    )
    invested = sum(
        (trade.entry_price * trade.entry_fill_quantity for trade in active),
        Decimal("0"),
    )
    available = max(Decimal("0"), initial_cash + realized - invested)
    positions = [
        PaperPosition(
            instrument_id=trade.instrument_id,
            quantity=trade.entry_fill_quantity,
            average_cost=trade.entry_price,
            realized_pnl=Decimal("0"),
            last_price=trade.entry_price,
        )
        for trade in active
    ]
    snapshot = PaperAccountSnapshot(
        account=PaperAccount(
            account_id="backtest",
            name="Gap Pullback Backtest",
            base_currency="USD",
            commission_bps=Decimal("0"),
        ),
        balances=[PaperBalance(currency="USD", available=available)],
        positions=positions,
        open_orders=[],
        order_history=[],
        recent_fills=[],
        recent_ledger=[],
    )
    open_risk = sum(
        ((trade.entry_price - trade.stop_price) * trade.entry_fill_quantity for trade in active),
        Decimal("0"),
    )
    active_symbols = {trade.instrument_id for trade in active}
    return snapshot, realized, open_risk, active_symbols


def _expectancy_interval(trades: list[GapPullbackBacktestTrade]) -> tuple[Decimal | None, Decimal | None]:
    if len(trades) < 2:
        return None, None
    values = [float(trade.r_multiple) for trade in trades]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    error = 1.96 * math.sqrt(variance / len(values))
    return Decimal(str(mean - error)), Decimal(str(mean + error))


def run_gap_pullback_backtest(
    dataset: BacktestSessionDataset,
    config: GapPullbackConfig | None = None,
    execution_policy: PaperExecutionPolicy | None = None,
    *,
    assumed_spread_bps: Decimal = Decimal("40"),
    max_hold_minutes: int = 90,
    max_concurrent_positions: int = 3,
    risk_profile: StrategyRiskProfile | None = None,
    initial_cash: Decimal = Decimal("100000"),
    research_policy_resolver: Callable[[str, datetime], ResearchPolicyDecision] | None = None,
) -> GapPullbackBacktestResult:
    if assumed_spread_bps < 0:
        raise ValueError("assumed_spread_bps cannot be negative")
    if max_hold_minutes < 1:
        raise ValueError("max_hold_minutes must be positive")
    if max_concurrent_positions < 1:
        raise ValueError("max_concurrent_positions must be positive")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    active = config or GapPullbackConfig()
    policy = execution_policy or PaperExecutionPolicy(
        max_volume_participation_pct=Decimal("1")
    )
    risk = risk_profile or StrategyRiskProfile()
    if risk.max_positions != max_concurrent_positions:
        risk = risk.model_copy(update={"max_positions": min(risk.max_positions, max_concurrent_positions)})

    # First pass discovers causal trigger times with one share only. No portfolio
    # decision is made here; it exists solely to establish chronological proposals.
    attempts: list[_TradeAttempt] = []
    proposed: list[tuple[GapperCandidate, GapPullbackBacktestTrade]] = []
    decision_by_instrument: dict[str, GapPullbackBacktestCandidateDecision] = {}
    for candidate in dataset.universe.candidates:
        attempt = _find_trade(
            candidate,
            dataset.bars_by_instrument[candidate.instrument_id],
            active,
            policy,
            assumed_spread_bps=assumed_spread_bps,
            max_hold_minutes=max_hold_minutes,
            quantity=Decimal("1"),
        )
        attempts.append(attempt)
        structure_bars = tuple(resample_final_bars(dataset.bars_by_instrument[candidate.instrument_id], active.structure_interval))
        final_result = evaluate_gap_pullback(candidate, structure_bars, active) if structure_bars else None
        decision_at = structure_bars[-1].end_time if structure_bars else dataset.universe.evaluation_time
        state = final_result.state if final_result is not None else "no_market_data"
        reason = final_result.reason_code if final_result is not None else "NO_MARKET_DATA"
        quality = final_result.features.quality_score if final_result is not None else None
        if attempt.triggered:
            execution_bars = tuple(resample_final_bars(dataset.bars_by_instrument[candidate.instrument_id], active.execution_interval))
            if attempt.trigger_bar_index is not None and attempt.trigger_bar_index < len(execution_bars):
                decision_at = execution_bars[attempt.trigger_bar_index].end_time
            state = "entry_ready"
            reason = attempt.rejection_reason or "FAILED_SELL_OFF_CONFIRMED"
            quality = attempt.trade.quality_score if attempt.trade is not None else quality
        decision_by_instrument[candidate.instrument_id] = GapPullbackBacktestCandidateDecision(
            instrument_id=candidate.instrument_id,
            discovery_rank=candidate.discovery_rank,
            decision_at=decision_at,
            state=state,
            rejection_reason=reason if state != "entry_ready" or attempt.rejection_reason else None,
            triggered=attempt.triggered,
            quality_score=quality,
        )
        if attempt.trade is not None:
            proposed.append((candidate, attempt.trade))
    selected: list[GapPullbackBacktestTrade] = []
    risk_rejections: dict[str, int] = {}
    research_rejections: dict[str, int] = {}
    execution_rejections: list[str] = []
    ranked_proposals: list[tuple[GapperCandidate, GapPullbackBacktestTrade, int]] = []
    research_reason: str | None
    for candidate, proposal in proposed:
        adjusted_quality_score = proposal.quality_score
        if active.strategy_version == "1.2.0":
            if research_policy_resolver is None:
                research_reason = "RESEARCH_POLICY_RESOLVER_UNAVAILABLE"
            else:
                try:
                    research_decision = research_policy_resolver(proposal.instrument_id, proposal.entry_time)
                except Exception:
                    research_reason = "RESEARCH_POLICY_RESOLUTION_ERROR"
                else:
                    quality_gate = apply_research_policy_to_quality(
                        research_decision,
                        base_quality_score=proposal.quality_score,
                        minimum_quality_score=active.minimum_quality_score,
                        score_adjustment_enabled=active.research_score_adjustment_enabled,
                    )
                    research_reason = None if quality_gate.allowed else quality_gate.reason_code
                    adjusted_quality_score = quality_gate.adjusted_quality_score
            if research_reason is not None:
                research_rejections[research_reason] = research_rejections.get(research_reason, 0) + 1
                current = decision_by_instrument[proposal.instrument_id]
                decision_by_instrument[proposal.instrument_id] = current.model_copy(update={
                    "decision_at": proposal.entry_time,
                    "state": "research_rejected",
                    "rejection_reason": research_reason,
                    "quality_score": adjusted_quality_score,
                })
                continue
        ranked_proposals.append((candidate, proposal, adjusted_quality_score))
    ranked_proposals.sort(
        key=lambda item: proposal_priority(
            observed_at=item[1].entry_time,
            quality_score=item[2],
            discovery_rank=item[0].discovery_rank,
            instrument_id=item[1].instrument_id,
        )
    )

    for candidate, proposal, adjusted_quality_score in ranked_proposals:
        snapshot, realized, open_risk, active_symbols = _virtual_snapshot(
            selected,
            entry_time=proposal.entry_time,
            initial_cash=initial_cash,
        )
        prior_entries = [trade for trade in selected if trade.entry_time <= proposal.entry_time]
        traded_symbols = {trade.instrument_id for trade in prior_entries}
        signal = StrategySignal(
            instrument_id=proposal.instrument_id,
            state="entry_ready",
            entry_price=proposal.signal_entry_price,
            stop_price=proposal.stop_price,
            target_price=proposal.signal_entry_price + proposal.signal_risk_per_share * active.reward_multiple,
            risk_per_share=proposal.signal_risk_per_share,
            reason_code="FAILED_SELL_OFF_CONFIRMED",
            quality_score=adjusted_quality_score,
        )
        decision = size_strategy_entry(
            snapshot,
            signal,
            risk,
            spread_bps=assumed_spread_bps,
            trades_today=len(prior_entries),
            traded_symbols_today=traded_symbols,
            reserved_instruments=active_symbols,
            daily_realized_pnl=realized,
            open_strategy_risk=open_risk,
            observed_at=proposal.entry_time,
        )
        if not decision.allowed:
            risk_rejections[decision.reason_code] = risk_rejections.get(decision.reason_code, 0) + 1
            current = decision_by_instrument[proposal.instrument_id]
            decision_by_instrument[proposal.instrument_id] = current.model_copy(update={
                "decision_at": proposal.entry_time,
                "state": "risk_rejected",
                "rejection_reason": decision.reason_code,
                "quality_score": adjusted_quality_score,
            })
            continue
        sized = _find_trade(
            candidate,
            dataset.bars_by_instrument[candidate.instrument_id],
            active,
            policy,
            assumed_spread_bps=assumed_spread_bps,
            max_hold_minutes=max_hold_minutes,
            quantity=decision.quantity,
        )
        if sized.trade is None:
            if sized.rejection_reason:
                execution_rejections.append(sized.rejection_reason)
            current = decision_by_instrument[proposal.instrument_id]
            decision_by_instrument[proposal.instrument_id] = current.model_copy(update={
                "decision_at": proposal.entry_time,
                "state": "execution_rejected",
                "rejection_reason": sized.rejection_reason or "EXECUTION_REJECTED",
                "quality_score": adjusted_quality_score,
            })
            continue
        selected_trade = sized.trade.model_copy(update={"quality_score": adjusted_quality_score})
        selected.append(selected_trade)
        current = decision_by_instrument[proposal.instrument_id]
        decision_by_instrument[proposal.instrument_id] = current.model_copy(update={
            "decision_at": proposal.entry_time,
            "state": "traded",
            "rejection_reason": None,
            "quality_score": adjusted_quality_score,
            "selected_trade": True,
            "entry_time": selected_trade.entry_time,
            "exit_time": selected_trade.exit_time,
        })

    trigger_count = sum(1 for attempt in attempts if attempt.triggered)
    no_next_bar_count = sum(
        1 for attempt in attempts if attempt.rejection_reason == "no_next_bar"
    )
    entry_execution_rejections = sum(
        1
        for attempt in attempts
        if (attempt.rejection_reason or "").startswith("entry_execution:")
    ) + sum(1 for reason in execution_rejections if reason.startswith("entry_execution:"))
    exit_execution_rejections = sum(
        1
        for attempt in attempts
        if (attempt.rejection_reason or "").startswith("exit_execution:")
    ) + sum(1 for reason in execution_rejections if reason.startswith("exit_execution:"))
    invalid_risk_count = sum(
        1 for attempt in attempts if attempt.rejection_reason == "invalid_risk_distance"
    ) + sum(1 for reason in execution_rejections if reason == "invalid_risk_distance")

    count = len(selected)
    wins = sum(1 for trade in selected if trade.r_multiple > 0)
    losses = sum(1 for trade in selected if trade.r_multiple < 0)
    positive = sum(
        (trade.r_multiple for trade in selected if trade.r_multiple > 0), Decimal("0")
    )
    negative = abs(
        sum((trade.r_multiple for trade in selected if trade.r_multiple < 0), Decimal("0"))
    )
    divisor = Decimal(count) if count else Decimal("1")
    candidate_count = len(dataset.universe.candidates)
    ci_low, ci_high = _expectancy_interval(selected)
    summary = GapPullbackBacktestSummary(
        candidate_count=candidate_count,
        trigger_count=trigger_count,
        trade_count=count,
        no_next_bar_count=no_next_bar_count,
        entry_execution_rejection_count=entry_execution_rejections,
        exit_execution_rejection_count=exit_execution_rejections,
        invalid_risk_count=invalid_risk_count,
        portfolio_capacity_rejection_count=risk_rejections.get("MAX_POSITIONS", 0),
        risk_rejection_count=sum(risk_rejections.values()),
        risk_rejection_reasons=risk_rejections,
        research_rejection_count=sum(research_rejections.values()),
        research_rejection_reasons=research_rejections,
        partial_entry_count=sum(
            1 for trade in selected if trade.entry_fill_quantity < trade.requested_quantity
        ),
        win_count=wins,
        loss_count=losses,
        win_rate=Decimal(wins) / divisor,
        expectancy_r=sum((trade.r_multiple for trade in selected), Decimal("0")) / divisor,
        expectancy_r_ci95_low=ci_low,
        expectancy_r_ci95_high=ci_high,
        profit_factor=positive / negative if negative > 0 else None,
        average_mfe_r=sum((trade.mfe_r for trade in selected), Decimal("0")) / divisor,
        average_mae_r=sum((trade.mae_r for trade in selected), Decimal("0")) / divisor,
        candidate_to_trigger_rate=(
            Decimal(trigger_count) / Decimal(candidate_count)
            if candidate_count
            else Decimal("0")
        ),
        trigger_to_trade_rate=(
            Decimal(count) / Decimal(trigger_count) if trigger_count else Decimal("0")
        ),
        candidate_to_trade_rate=(
            Decimal(count) / Decimal(candidate_count) if candidate_count else Decimal("0")
        ),
        average_hold_minutes=sum((trade.hold_minutes for trade in selected), Decimal("0")) / divisor,
        average_entry_slippage_bps=sum(
            (trade.entry_slippage_bps for trade in selected), Decimal("0")
        ) / divisor,
        average_exit_slippage_bps=sum(
            (trade.exit_slippage_bps for trade in selected), Decimal("0"))
        / divisor,
        trades_per_day=Decimal(count),
        stop_count=sum(1 for trade in selected if trade.exit_reason == "stop"),
        target_count=sum(1 for trade in selected if trade.exit_reason == "target"),
        indicator_exit_count=sum(1 for trade in selected if trade.exit_reason == "rsi"),
    )
    return GapPullbackBacktestResult(
        strategy_version=active.strategy_version,
        dataset_fingerprint=dataset.dataset_fingerprint,
        execution_policy_version=policy.policy_version,
        risk_policy=risk,
        initial_cash=initial_cash,
        trades=tuple(selected),
        candidate_decisions=tuple(
            decision_by_instrument[candidate.instrument_id]
            for candidate in dataset.universe.candidates
            if candidate.instrument_id in decision_by_instrument
        ),
        summary=summary,
    )


def walk_forward_splits(
    sessions: list[BacktestSessionDataset] | tuple[BacktestSessionDataset, ...],
    *,
    train_sessions: int,
    test_sessions: int,
) -> list[tuple[tuple[BacktestSessionDataset, ...], tuple[BacktestSessionDataset, ...]]]:
    ordered = tuple(sorted(sessions, key=lambda item: item.session_date))
    if train_sessions < 1 or test_sessions < 1:
        raise ValueError("walk-forward windows must be positive")
    output = []
    start = 0
    while start + train_sessions + test_sessions <= len(ordered):
        train = ordered[start : start + train_sessions]
        test = ordered[start + train_sessions : start + train_sessions + test_sessions]
        output.append((train, test))
        start += test_sessions
    return output
