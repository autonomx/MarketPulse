from __future__ import annotations

"""Research-only diagnostics for leader-momentum-continuation v1.2.

This module never changes strategy decisions. It runs the frozen v1.2 evaluator
first, then reconstructs score/state/setup diagnostics from the same finalized
causal bars so research replays can explain *why* a symbol was or was not
recognized and traded.

The diagnostic trace deliberately keeps:
- exact v1.2 3-minute decision-score decomposition;
- a research-only 1-minute cadence view of the same score family;
- leader confirm/refresh/expiry and structural-break observations;
- best Mode A / Mode B candidate windows with gate distances;
- aggregate pass/fail counts for every setup gate.

Nothing in this module carries execution authority.
"""

from collections import Counter
from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import strategy_leader_momentum_continuation as leader
from .models import MarketBar
from .strategies.gap_pullback import session_vwap
from .strategy_timeframes import resample_final_bars
from app.apps.trading.us_equity_calendar import EASTERN as _ET


DiagnosticCadence = Literal["decision_3m", "research_1m"]
TransitionKind = Literal[
    "confirmed",
    "refreshed",
    "expired",
    "structural_invalidation_observed",
    "structure_recovered",
]


class LeaderScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cadence: DiagnosticCadence
    observed_at: datetime
    total_score: Decimal = Field(ge=0, le=100)
    market_leadership_points: Decimal = Field(ge=0)
    price_strength_points: Decimal = Field(ge=0)
    trend_points: Decimal = Field(ge=0)
    hod_points: Decimal = Field(ge=0)
    volume_points: Decimal = Field(ge=0)
    context_execution_points: Decimal
    base_context_points: Decimal
    tod_rvol_points: Decimal = Decimal("0")
    relative_strength_points: Decimal = Decimal("0")
    spread_penalty_points: Decimal = Decimal("0")
    dollar_volume_penalty_points: Decimal = Decimal("0")
    volume_acceleration_points: Decimal = Decimal("0")
    context_hod_points: Decimal = Decimal("0")
    session_return_pct: Decimal
    volume_ratio: Decimal
    new_high_count: int = Field(ge=0)
    vwap: Decimal
    ema9_1m: Decimal
    ema20_1m: Decimal


class LeaderTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    kind: TransitionKind
    score: Decimal | None = None
    session_return_pct: Decimal | None = None
    confirmed_until: datetime | None = None
    decision_state_changed: bool = False


class SetupGateDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate: str
    passed: bool
    actual: Decimal | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    distance_to_pass: Decimal | None = None


class SetupCandidateDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: leader.LeaderMode
    observed_at: datetime
    window_start: datetime
    window_end: datetime
    candidate: bool
    setup_valid: bool
    execution_valid: bool
    passed_gate_count: int = Field(ge=0)
    failed_gate_count: int = Field(ge=0)
    normalized_shortfall: Decimal = Field(ge=0)
    impulse_pct: Decimal | None = None
    retrace_pct: Decimal | None = None
    pullback_volume_ratio: Decimal | None = None
    compression_width_ratio: Decimal | None = None
    breakout_volume_ratio: Decimal | None = None
    close_location: Decimal | None = None
    ema9_extension_pct: Decimal | None = None
    ema9_extension_atr: Decimal | None = None
    proposed_risk_pct: Decimal | None = None
    gates: tuple[SetupGateDiagnostic, ...] = ()


class SetupGateCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: leader.LeaderMode
    gate: str
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)


class LeaderMomentumDiagnosticTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = leader.POLICY_VERSION
    session_date: str | None = None
    strategy_snapshot: leader.LeaderMomentumSnapshot

    first_score: LeaderScoreBreakdown | None = None
    max_score: LeaderScoreBreakdown | None = None
    last_score: LeaderScoreBreakdown | None = None
    first_confirmed_score: LeaderScoreBreakdown | None = None
    last_confirmed_score: LeaderScoreBreakdown | None = None

    research_1m_first_score: LeaderScoreBreakdown | None = None
    research_1m_max_score: LeaderScoreBreakdown | None = None
    research_1m_first_confirmed_score: LeaderScoreBreakdown | None = None

    first_leader_confirmed_at: datetime | None = None
    last_leader_confirmed_at: datetime | None = None
    bars_in_confirmed_state: int = Field(default=0, ge=0)

    first_setup_candidate_at: datetime | None = None
    first_setup_candidate: SetupCandidateDiagnostic | None = None
    first_setup_valid_at: datetime | None = None
    first_execution_valid_at: datetime | None = None
    leader_to_first_candidate_minutes: Decimal | None = None
    leader_to_first_valid_minutes: Decimal | None = None

    transitions: tuple[LeaderTransition, ...] = ()
    best_mode_a: SetupCandidateDiagnostic | None = None
    best_mode_b: SetupCandidateDiagnostic | None = None
    gate_counts: tuple[SetupGateCount, ...] = ()

    execution_authority: Literal[False] = False


class LeaderMomentumCohortObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cohort: Literal["winner", "control"]
    instrument_id: str
    trace: LeaderMomentumDiagnosticTrace


class LeaderMomentumCohortReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    winner_observations: int = Field(ge=0)
    control_observations: int = Field(ge=0)
    winner_confirmed: int = Field(ge=0)
    control_confirmed: int = Field(ge=0)
    winner_traded: int = Field(ge=0)
    control_traded: int = Field(ge=0)
    leader_recall: Decimal | None = None
    leader_false_positive_rate: Decimal | None = None
    leader_precision: Decimal | None = None
    trade_precision: Decimal | None = None


def _clamp(
    value: Decimal,
    low: Decimal = Decimal("0"),
    high: Decimal = Decimal("100"),
) -> Decimal:
    return max(low, min(high, value))


def _score_breakdown(
    regular: list[MarketBar],
    sampled: list[MarketBar],
    *,
    index: int,
    context: leader.LeaderMomentumContext | None,
    cadence: DiagnosticCadence,
) -> LeaderScoreBreakdown | None:
    current = sampled[index]
    regular_through = [bar for bar in regular if bar.end_time <= current.end_time]
    if not regular_through:
        return None

    vwap = session_vwap(regular_through)
    closes = [bar.close for bar in regular_through]
    ema9 = leader._ema(closes, 9)[-1]
    ema20 = leader._ema(closes, 20)[-1]
    if vwap is None or ema9 is None or ema20 is None:
        return None

    session_return = leader._pct_change(regular_through[0].open, current.close)
    positive_return = max(Decimal("0"), session_return)
    price_strength = min(
        Decimal("25"), positive_return / Decimal("20") * Decimal("25")
    )

    trend_points = Decimal("0")
    if current.close > vwap:
        trend_points += Decimal("7")
    if current.close > ema9 > ema20:
        trend_points += Decimal("8")
    if len(regular_through) >= 12:
        prior_ema9 = leader._ema(closes[:-3], 9)[-1]
        if prior_ema9 is not None and ema9 > prior_ema9:
            trend_points += Decimal("5")

    recent_count = 6 if cadence == "decision_3m" else 15
    prefix = sampled[: index + 1]
    recent = prefix[-recent_count:]
    prior_prefix = prefix[:-len(recent)] if recent else prefix
    running_high = max((bar.high for bar in prior_prefix), default=Decimal("0"))
    new_highs = 0
    for bar in recent:
        if bar.high > running_high:
            new_highs += 1
            running_high = bar.high
    hod_points = min(Decimal("15"), Decimal(new_highs) * Decimal("5"))

    prior_volume = prefix[max(0, index - 5):index]
    baseline = leader._average([bar.volume for bar in prior_volume])
    volume_ratio = current.volume / baseline if baseline > 0 else Decimal("0")
    volume_points = min(
        Decimal("20"), volume_ratio / Decimal("2") * Decimal("20")
    )

    base_context = Decimal("10")
    tod_rvol_points = Decimal("0")
    relative_strength_points = Decimal("0")
    spread_penalty = Decimal("0")
    dollar_volume_penalty = Decimal("0")
    volume_acceleration_points = Decimal("0")
    context_hod_points = Decimal("0")
    if context is not None:
        if context.tod_rvol is not None:
            tod_rvol_points = min(
                Decimal("5"), context.tod_rvol / Decimal("8") * Decimal("5")
            )
        if (
            context.relative_strength_pct is not None
            and context.relative_strength_pct > 0
        ):
            relative_strength_points = min(
                Decimal("5"),
                context.relative_strength_pct / Decimal("20") * Decimal("5"),
            )
        if context.spread_bps is not None and context.spread_bps > Decimal("150"):
            spread_penalty = Decimal("-10")
        if (
            context.dollar_volume is not None
            and context.dollar_volume < Decimal("2000000")
        ):
            dollar_volume_penalty = Decimal("-5")
        if (
            context.volume_acceleration is not None
            and context.volume_acceleration >= Decimal("1.5")
        ):
            volume_acceleration_points = Decimal("5")
        if context.hod_frequency_15m is not None:
            context_hod_points = min(
                Decimal("5"),
                Decimal(context.hod_frequency_15m) * Decimal("2.5"),
            )

    execution_points = (
        base_context
        + tod_rvol_points
        + relative_strength_points
        + spread_penalty
        + dollar_volume_penalty
        + volume_acceleration_points
        + context_hod_points
    )
    market_leadership = price_strength + trend_points + hod_points + volume_points
    total = _clamp(market_leadership + execution_points)

    return LeaderScoreBreakdown(
        cadence=cadence,
        observed_at=current.end_time,
        total_score=total,
        market_leadership_points=market_leadership,
        price_strength_points=price_strength,
        trend_points=trend_points,
        hod_points=hod_points,
        volume_points=volume_points,
        context_execution_points=execution_points,
        base_context_points=base_context,
        tod_rvol_points=tod_rvol_points,
        relative_strength_points=relative_strength_points,
        spread_penalty_points=spread_penalty,
        dollar_volume_penalty_points=dollar_volume_penalty,
        volume_acceleration_points=volume_acceleration_points,
        context_hod_points=context_hod_points,
        session_return_pct=session_return,
        volume_ratio=volume_ratio,
        new_high_count=new_highs,
        vwap=vwap,
        ema9_1m=ema9,
        ema20_1m=ema20,
    )


def _gate(
    gate: str,
    passed: bool,
    *,
    actual: Decimal | None = None,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> SetupGateDiagnostic:
    distance: Decimal | None = None
    if actual is not None:
        if minimum is not None and actual < minimum:
            distance = minimum - actual
        elif maximum is not None and actual > maximum:
            distance = actual - maximum
        else:
            distance = Decimal("0")
    elif not passed:
        distance = Decimal("1")
    return SetupGateDiagnostic(
        gate=gate,
        passed=passed,
        actual=actual,
        minimum=minimum,
        maximum=maximum,
        distance_to_pass=distance,
    )


def _shortfall(gates: list[SetupGateDiagnostic]) -> Decimal:
    total = Decimal("0")
    for item in gates:
        if item.passed:
            continue
        if item.distance_to_pass is None:
            total += Decimal("1")
            continue
        scale = Decimal("1")
        if item.minimum is not None and item.minimum != 0:
            scale = abs(item.minimum)
        elif item.maximum is not None and item.maximum != 0:
            scale = abs(item.maximum)
        total += item.distance_to_pass / scale
    return total


def _risk_pct(
    sampled: list[MarketBar],
    *,
    index: int,
    stop_reference: Decimal,
    entry_atr: Decimal | None,
) -> Decimal | None:
    if index + 1 >= len(sampled) or entry_atr is None or entry_atr <= 0:
        return None
    entry_price = sampled[index + 1].open
    if entry_price <= 0:
        return None
    stop = max(
        Decimal("0.0001"),
        stop_reference - entry_atr * leader.INITIAL_STOP_BUFFER_ATR,
    )
    risk = entry_price - stop
    if risk <= 0:
        return Decimal("-1")
    return risk / entry_price * Decimal("100")


def _common_setup_values(
    regular: list[MarketBar],
    sampled: list[MarketBar],
    ema9_3m: list[Decimal | None],
    atr14_3m: list[Decimal | None],
    *,
    index: int,
) -> dict[str, Decimal | bool | None]:
    current = sampled[index]
    regular_through = [bar for bar in regular if bar.end_time <= current.end_time]
    vwap = session_vwap(regular_through)
    closes = [bar.close for bar in regular_through]
    ema9_1m = leader._ema(closes, 9)[-1] if closes else None
    ema20_1m = leader._ema(closes, 20)[-1] if closes else None
    entry_atr = leader._atr(regular_through, 14)[-1] if regular_through else None
    ema9 = ema9_3m[index]
    atr3 = atr14_3m[index]

    trend_ok = bool(
        vwap is not None
        and ema9_1m is not None
        and ema20_1m is not None
        and current.close > vwap
        and current.close > ema9_1m
        and ema9_1m > ema20_1m
    )
    extension_pct = (
        leader._pct_change(ema9, current.close)
        if ema9 is not None and ema9 > 0
        else None
    )
    extension_atr = (
        (current.close - ema9) / atr3
        if ema9 is not None and atr3 is not None and atr3 > 0
        else None
    )
    prior_volumes = [bar.volume for bar in sampled[max(0, index - 5):index]]
    baseline = leader._average(prior_volumes)
    volume_ratio = current.volume / baseline if baseline > 0 else Decimal("0")
    return {
        "trend_ok": trend_ok,
        "entry_atr": entry_atr,
        "ema20_1m": ema20_1m,
        "extension_pct": extension_pct,
        "extension_atr": extension_atr,
        "volume_ratio": volume_ratio,
        "close_location": leader._close_location(current),
    }


def _mode_a_candidates(
    regular: list[MarketBar],
    sampled: list[MarketBar],
    ema9_3m: list[Decimal | None],
    atr14_3m: list[Decimal | None],
    *,
    index: int,
) -> list[SetupCandidateDiagnostic]:
    if index < 7:
        return []
    current = sampled[index]
    common = _common_setup_values(
        regular, sampled, ema9_3m, atr14_3m, index=index
    )
    result: list[SetupCandidateDiagnostic] = []
    for pullback_len in range(2, 7):
        impulse_end = index - pullback_len
        if impulse_end < 2:
            continue
        impulse_start = max(0, impulse_end - 4)
        contiguous = leader._is_contiguous(sampled, impulse_start, index)
        pullback = sampled[impulse_end:index]
        impulse_window = sampled[impulse_start : impulse_end + 1]
        impulse_low = min(
            (bar.low for bar in impulse_window), default=Decimal("0")
        )
        impulse_high = max(
            (bar.high for bar in impulse_window), default=Decimal("0")
        )
        impulse_pct = (
            leader._pct_change(impulse_low, impulse_high)
            if impulse_low > 0 and impulse_high > impulse_low
            else Decimal("0")
        )
        impulse_size = impulse_high - impulse_low
        pullback_low = min(
            (bar.low for bar in pullback), default=impulse_high
        )
        retrace = (
            (impulse_high - pullback_low) / impulse_size
            if impulse_size > 0
            else Decimal("999")
        )
        impulse_volume = leader._average([bar.volume for bar in impulse_window])
        pullback_volume = leader._average([bar.volume for bar in pullback])
        pullback_volume_ratio = (
            pullback_volume / impulse_volume
            if impulse_volume > 0
            else Decimal("999")
        )
        breakout_level = max(
            (bar.high for bar in pullback), default=Decimal("0")
        )
        pullback_new_high = max(
            (bar.high for bar in pullback), default=Decimal("0")
        ) > (impulse_high * Decimal("1.01"))
        risk_pct = _risk_pct(
            sampled,
            index=index,
            stop_reference=pullback_low,
            entry_atr=(
                common["entry_atr"]
                if isinstance(common["entry_atr"], Decimal)
                else None
            ),
        )

        gates = [
            _gate("continuity", contiguous),
            _gate("trend_structure", bool(common["trend_ok"])),
            _gate(
                "ema9_extension_pct",
                common["extension_pct"] is not None
                and common["extension_pct"] <= leader.MAX_EMA9_EXTENSION_PCT,
                actual=(
                    common["extension_pct"]
                    if isinstance(common["extension_pct"], Decimal)
                    else None
                ),
                maximum=leader.MAX_EMA9_EXTENSION_PCT,
            ),
            _gate(
                "ema9_extension_atr",
                common["extension_atr"] is not None
                and common["extension_atr"] <= leader.MAX_ATR_EXTENSION,
                actual=(
                    common["extension_atr"]
                    if isinstance(common["extension_atr"], Decimal)
                    else None
                ),
                maximum=leader.MAX_ATR_EXTENSION,
            ),
            _gate(
                "impulse_pct",
                impulse_pct >= leader.MIN_IMPULSE_PCT,
                actual=impulse_pct,
                minimum=leader.MIN_IMPULSE_PCT,
            ),
            _gate(
                "pullback_no_new_high",
                not leader.REQUIRE_PULLBACK_NO_NEW_HIGH or not pullback_new_high,
            ),
            _gate(
                "pullback_retrace_min",
                retrace >= leader.MIN_PULLBACK_RETRACE,
                actual=retrace,
                minimum=leader.MIN_PULLBACK_RETRACE,
            ),
            _gate(
                "pullback_retrace_max",
                retrace <= leader.MAX_PULLBACK_RETRACE,
                actual=retrace,
                maximum=leader.MAX_PULLBACK_RETRACE,
            ),
            _gate(
                "pullback_volume_ratio",
                pullback_volume_ratio <= leader.MAX_PULLBACK_VOLUME_RATIO,
                actual=pullback_volume_ratio,
                maximum=leader.MAX_PULLBACK_VOLUME_RATIO,
            ),
            _gate("breakout_close", current.close > breakout_level),
            _gate(
                "close_location",
                isinstance(common["close_location"], Decimal)
                and common["close_location"]
                >= leader.MIN_BREAKOUT_CLOSE_LOCATION,
                actual=(
                    common["close_location"]
                    if isinstance(common["close_location"], Decimal)
                    else None
                ),
                minimum=leader.MIN_BREAKOUT_CLOSE_LOCATION,
            ),
            _gate(
                "breakout_volume_ratio",
                isinstance(common["volume_ratio"], Decimal)
                and common["volume_ratio"] >= leader.MIN_BREAKOUT_VOLUME_RATIO,
                actual=(
                    common["volume_ratio"]
                    if isinstance(common["volume_ratio"], Decimal)
                    else None
                ),
                minimum=leader.MIN_BREAKOUT_VOLUME_RATIO,
            ),
        ]
        setup_valid = all(item.passed for item in gates)
        execution_gate = _gate(
            "proposed_risk_pct",
            risk_pct is not None
            and risk_pct > 0
            and risk_pct <= leader.MAX_ENTRY_RISK_PCT,
            actual=risk_pct,
            maximum=leader.MAX_ENTRY_RISK_PCT,
        )
        all_gates = [*gates, execution_gate]
        candidate = all(
            item.passed
            for item in all_gates
            if item.gate
            in {
                "continuity",
                "trend_structure",
                "ema9_extension_pct",
                "ema9_extension_atr",
                "impulse_pct",
            }
        )
        result.append(
            SetupCandidateDiagnostic(
                mode="controlled_pullback",
                observed_at=current.end_time,
                window_start=sampled[impulse_start].start_time,
                window_end=current.end_time,
                candidate=candidate,
                setup_valid=setup_valid,
                execution_valid=setup_valid and execution_gate.passed,
                passed_gate_count=sum(item.passed for item in all_gates),
                failed_gate_count=sum(not item.passed for item in all_gates),
                normalized_shortfall=_shortfall(all_gates),
                impulse_pct=impulse_pct,
                retrace_pct=retrace,
                pullback_volume_ratio=pullback_volume_ratio,
                breakout_volume_ratio=(
                    common["volume_ratio"]
                    if isinstance(common["volume_ratio"], Decimal)
                    else None
                ),
                close_location=(
                    common["close_location"]
                    if isinstance(common["close_location"], Decimal)
                    else None
                ),
                ema9_extension_pct=(
                    common["extension_pct"]
                    if isinstance(common["extension_pct"], Decimal)
                    else None
                ),
                ema9_extension_atr=(
                    common["extension_atr"]
                    if isinstance(common["extension_atr"], Decimal)
                    else None
                ),
                proposed_risk_pct=risk_pct,
                gates=tuple(all_gates),
            )
        )
    return result


def _mode_b_candidates(
    regular: list[MarketBar],
    sampled: list[MarketBar],
    ema9_3m: list[Decimal | None],
    atr14_3m: list[Decimal | None],
    *,
    index: int,
) -> list[SetupCandidateDiagnostic]:
    if index < 7:
        return []
    current = sampled[index]
    prior = sampled[:index]
    common = _common_setup_values(
        regular, sampled, ema9_3m, atr14_3m, index=index
    )
    result: list[SetupCandidateDiagnostic] = []
    for compression_len in range(2, 7):
        start = index - compression_len
        if start < 3:
            continue
        impulse_start = max(0, start - 3)
        contiguous = leader._is_contiguous(sampled, impulse_start, index)
        compression = sampled[start:index]
        impulse_window = sampled[impulse_start : start + 1]
        impulse_low = min(
            (bar.low for bar in impulse_window), default=Decimal("0")
        )
        impulse_high = max(
            (bar.high for bar in impulse_window), default=Decimal("0")
        )
        impulse_pct = (
            leader._pct_change(impulse_low, impulse_high)
            if impulse_low > 0 and impulse_high > impulse_low
            else Decimal("0")
        )
        impulse_size = impulse_high - impulse_low
        compression_high = max(
            (bar.high for bar in compression), default=Decimal("0")
        )
        compression_low = min(
            (bar.low for bar in compression), default=Decimal("0")
        )
        width_ratio = (
            (compression_high - compression_low) / impulse_size
            if impulse_size > 0
            else Decimal("999")
        )
        ema20 = (
            common["ema20_1m"]
            if isinstance(common["ema20_1m"], Decimal)
            else None
        )
        compression_above_ema20 = ema20 is not None and all(
            bar.close >= ema20 for bar in compression
        )
        breakout_level = compression_high
        hod_break = bool(prior) and current.close >= max(
            bar.high for bar in prior[-8:]
        )
        risk_pct = _risk_pct(
            sampled,
            index=index,
            stop_reference=compression_low,
            entry_atr=(
                common["entry_atr"]
                if isinstance(common["entry_atr"], Decimal)
                else None
            ),
        )

        gates = [
            _gate("continuity", contiguous),
            _gate("trend_structure", bool(common["trend_ok"])),
            _gate(
                "ema9_extension_pct",
                common["extension_pct"] is not None
                and common["extension_pct"] <= leader.MAX_EMA9_EXTENSION_PCT,
                actual=(
                    common["extension_pct"]
                    if isinstance(common["extension_pct"], Decimal)
                    else None
                ),
                maximum=leader.MAX_EMA9_EXTENSION_PCT,
            ),
            _gate(
                "ema9_extension_atr",
                common["extension_atr"] is not None
                and common["extension_atr"] <= leader.MAX_ATR_EXTENSION,
                actual=(
                    common["extension_atr"]
                    if isinstance(common["extension_atr"], Decimal)
                    else None
                ),
                maximum=leader.MAX_ATR_EXTENSION,
            ),
            _gate(
                "impulse_pct",
                impulse_pct >= leader.MIN_RUNAWAY_IMPULSE_PCT,
                actual=impulse_pct,
                minimum=leader.MIN_RUNAWAY_IMPULSE_PCT,
            ),
            _gate(
                "compression_width_ratio",
                width_ratio <= leader.MAX_COMPRESSION_WIDTH_RATIO,
                actual=width_ratio,
                maximum=leader.MAX_COMPRESSION_WIDTH_RATIO,
            ),
            _gate(
                "compression_above_ema20",
                not leader.REQUIRE_COMPRESSION_ABOVE_EMA20
                or compression_above_ema20,
            ),
            _gate("breakout_close", current.close > breakout_level),
            _gate(
                "hod_break",
                not leader.REQUIRE_COMPRESSION_HOD_BREAK or hod_break,
            ),
            _gate(
                "close_location",
                isinstance(common["close_location"], Decimal)
                and common["close_location"]
                >= leader.MIN_BREAKOUT_CLOSE_LOCATION,
                actual=(
                    common["close_location"]
                    if isinstance(common["close_location"], Decimal)
                    else None
                ),
                minimum=leader.MIN_BREAKOUT_CLOSE_LOCATION,
            ),
            _gate(
                "breakout_volume_ratio",
                isinstance(common["volume_ratio"], Decimal)
                and common["volume_ratio"] >= leader.MIN_COMPRESSION_VOLUME_RATIO,
                actual=(
                    common["volume_ratio"]
                    if isinstance(common["volume_ratio"], Decimal)
                    else None
                ),
                minimum=leader.MIN_COMPRESSION_VOLUME_RATIO,
            ),
        ]
        setup_valid = all(item.passed for item in gates)
        execution_gate = _gate(
            "proposed_risk_pct",
            risk_pct is not None
            and risk_pct > 0
            and risk_pct <= leader.MAX_ENTRY_RISK_PCT,
            actual=risk_pct,
            maximum=leader.MAX_ENTRY_RISK_PCT,
        )
        all_gates = [*gates, execution_gate]
        candidate = all(
            item.passed
            for item in all_gates
            if item.gate
            in {
                "continuity",
                "trend_structure",
                "ema9_extension_pct",
                "ema9_extension_atr",
                "impulse_pct",
            }
        )
        result.append(
            SetupCandidateDiagnostic(
                mode="momentum_compression",
                observed_at=current.end_time,
                window_start=sampled[impulse_start].start_time,
                window_end=current.end_time,
                candidate=candidate,
                setup_valid=setup_valid,
                execution_valid=setup_valid and execution_gate.passed,
                passed_gate_count=sum(item.passed for item in all_gates),
                failed_gate_count=sum(not item.passed for item in all_gates),
                normalized_shortfall=_shortfall(all_gates),
                impulse_pct=impulse_pct,
                compression_width_ratio=width_ratio,
                breakout_volume_ratio=(
                    common["volume_ratio"]
                    if isinstance(common["volume_ratio"], Decimal)
                    else None
                ),
                close_location=(
                    common["close_location"]
                    if isinstance(common["close_location"], Decimal)
                    else None
                ),
                ema9_extension_pct=(
                    common["extension_pct"]
                    if isinstance(common["extension_pct"], Decimal)
                    else None
                ),
                ema9_extension_atr=(
                    common["extension_atr"]
                    if isinstance(common["extension_atr"], Decimal)
                    else None
                ),
                proposed_risk_pct=risk_pct,
                gates=tuple(all_gates),
            )
        )
    return result


def _best(
    candidates: list[SetupCandidateDiagnostic],
) -> SetupCandidateDiagnostic | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            int(item.execution_valid),
            int(item.setup_valid),
            int(item.candidate),
            item.passed_gate_count,
            -item.normalized_shortfall,
            -item.observed_at.timestamp(),
        ),
    )


def _minutes(left: datetime | None, right: datetime | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return Decimal(str((right - left).total_seconds() / 60))


def diagnose_leader_momentum_continuation(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    context: leader.LeaderMomentumContext | None = None,
    entry_start_et: time = time(9, 35),
    last_entry_et: time = time(15, 30),
    force_flat_et: time = time(15, 55),
) -> LeaderMomentumDiagnosticTrace:
    """Return research diagnostics without changing the frozen v1.2 decision path."""

    snapshot = leader.evaluate_leader_momentum_continuation(
        bars,
        context=context,
        entry_start_et=entry_start_et,
        last_entry_et=last_entry_et,
        force_flat_et=force_flat_et,
    )
    regular = leader._regular_final_bars(bars)
    if not regular:
        return LeaderMomentumDiagnosticTrace(strategy_snapshot=snapshot)

    session_date = regular[-1].start_time.astimezone(_ET).date()
    regular = [
        bar
        for bar in regular
        if bar.start_time.astimezone(_ET).date() == session_date
    ]
    sampled = [
        bar
        for bar in resample_final_bars(regular, "3m")
        if bar.session == "regular"
    ]
    if not sampled:
        return LeaderMomentumDiagnosticTrace(
            session_date=session_date.isoformat(),
            strategy_snapshot=snapshot,
        )

    ema9_3m = leader._ema([bar.close for bar in sampled], 9)
    atr14_3m = leader._atr(sampled, 14)

    decision_scores: list[LeaderScoreBreakdown] = []
    confirmed_scores: list[LeaderScoreBreakdown] = []
    transitions: list[LeaderTransition] = []
    all_candidates: list[SetupCandidateDiagnostic] = []
    gate_pass: Counter[tuple[leader.LeaderMode, str]] = Counter()
    gate_fail: Counter[tuple[leader.LeaderMode, str]] = Counter()

    leader_confirmed_until: datetime | None = None
    first_leader_confirmed_at: datetime | None = None
    last_leader_confirmed_at: datetime | None = None
    bars_in_confirmed_state = 0
    previously_latched = False
    previous_structure_ok: bool | None = None

    first_setup_candidate_at: datetime | None = None
    first_setup_candidate: SetupCandidateDiagnostic | None = None
    first_setup_valid_at: datetime | None = None
    first_execution_valid_at: datetime | None = None

    for index in range(9, len(sampled) - 1):
        bar = sampled[index]
        bar_et = bar.end_time.astimezone(_ET)
        if bar_et.time() < entry_start_et:
            continue
        if bar_et.time() > last_entry_et:
            break
        if not (leader.MIN_PRICE <= bar.close <= leader.MAX_PRICE):
            continue

        breakdown = _score_breakdown(
            regular,
            sampled,
            index=index,
            context=context,
            cadence="decision_3m",
        )
        if breakdown is None:
            continue
        exact = leader._leader_score(
            regular,
            sampled,
            index=index,
            context=context,
        )
        if breakdown.total_score != exact:
            raise AssertionError("leader_diagnostic_score_drift")
        decision_scores.append(breakdown)

        qualifies = (
            breakdown.total_score >= leader.MIN_LEADER_SCORE
            and breakdown.session_return_pct >= leader.MIN_SESSION_RETURN_PCT
        )
        was_latched = (
            leader_confirmed_until is not None
            and bar.end_time <= leader_confirmed_until
        )
        if qualifies:
            kind: TransitionKind = "refreshed" if was_latched else "confirmed"
            leader_confirmed_until = bar.end_time + leader.LEADER_LATCH_TTL
            first_leader_confirmed_at = first_leader_confirmed_at or bar.end_time
            last_leader_confirmed_at = bar.end_time
            confirmed_scores.append(breakdown)
            transitions.append(
                LeaderTransition(
                    observed_at=bar.end_time,
                    kind=kind,
                    score=breakdown.total_score,
                    session_return_pct=breakdown.session_return_pct,
                    confirmed_until=leader_confirmed_until,
                    decision_state_changed=kind == "confirmed",
                )
            )
        elif previously_latched and (
            leader_confirmed_until is None
            or bar.end_time > leader_confirmed_until
        ):
            transitions.append(
                LeaderTransition(
                    observed_at=bar.end_time,
                    kind="expired",
                    score=breakdown.total_score,
                    session_return_pct=breakdown.session_return_pct,
                    confirmed_until=leader_confirmed_until,
                    decision_state_changed=True,
                )
            )

        latched = (
            leader_confirmed_until is not None
            and bar.end_time <= leader_confirmed_until
        )
        if latched:
            bars_in_confirmed_state += 1
            common = _common_setup_values(
                regular,
                sampled,
                ema9_3m,
                atr14_3m,
                index=index,
            )
            structure_ok = bool(common["trend_ok"])
            if previous_structure_ok is True and not structure_ok:
                transitions.append(
                    LeaderTransition(
                        observed_at=bar.end_time,
                        kind="structural_invalidation_observed",
                        score=breakdown.total_score,
                        session_return_pct=breakdown.session_return_pct,
                        confirmed_until=leader_confirmed_until,
                        decision_state_changed=False,
                    )
                )
            elif previous_structure_ok is False and structure_ok:
                transitions.append(
                    LeaderTransition(
                        observed_at=bar.end_time,
                        kind="structure_recovered",
                        score=breakdown.total_score,
                        session_return_pct=breakdown.session_return_pct,
                        confirmed_until=leader_confirmed_until,
                        decision_state_changed=False,
                    )
                )
            previous_structure_ok = structure_ok

            candidates = [
                *_mode_a_candidates(
                    regular,
                    sampled,
                    ema9_3m,
                    atr14_3m,
                    index=index,
                ),
                *_mode_b_candidates(
                    regular,
                    sampled,
                    ema9_3m,
                    atr14_3m,
                    index=index,
                ),
            ]
            all_candidates.extend(candidates)
            for candidate in candidates:
                for gate in candidate.gates:
                    gate_key = (candidate.mode, gate.gate)
                    if gate.passed:
                        gate_pass[gate_key] += 1
                    else:
                        gate_fail[gate_key] += 1
                if candidate.candidate and first_setup_candidate_at is None:
                    first_setup_candidate_at = candidate.observed_at
                    first_setup_candidate = candidate
                if candidate.setup_valid and first_setup_valid_at is None:
                    first_setup_valid_at = candidate.observed_at
                if candidate.execution_valid and first_execution_valid_at is None:
                    first_execution_valid_at = candidate.observed_at
        elif not previously_latched:
            previous_structure_ok = None

        previously_latched = latched

    research_scores: list[LeaderScoreBreakdown] = []
    for index, bar in enumerate(regular[:-1]):
        bar_et = bar.end_time.astimezone(_ET)
        if bar_et.time() < entry_start_et or bar_et.time() > last_entry_et:
            continue
        if not (leader.MIN_PRICE <= bar.close <= leader.MAX_PRICE):
            continue
        breakdown = _score_breakdown(
            regular,
            regular,
            index=index,
            context=context,
            cadence="research_1m",
        )
        if breakdown is not None:
            research_scores.append(breakdown)

    research_confirmed = [
        item
        for item in research_scores
        if item.total_score >= leader.MIN_LEADER_SCORE
        and item.session_return_pct >= leader.MIN_SESSION_RETURN_PCT
    ]

    gate_keys = sorted(set(gate_pass) | set(gate_fail))
    return LeaderMomentumDiagnosticTrace(
        session_date=session_date.isoformat(),
        strategy_snapshot=snapshot,
        first_score=decision_scores[0] if decision_scores else None,
        max_score=(
            max(decision_scores, key=lambda item: item.total_score)
            if decision_scores
            else None
        ),
        last_score=decision_scores[-1] if decision_scores else None,
        first_confirmed_score=(confirmed_scores[0] if confirmed_scores else None),
        last_confirmed_score=(confirmed_scores[-1] if confirmed_scores else None),
        research_1m_first_score=(research_scores[0] if research_scores else None),
        research_1m_max_score=(
            max(research_scores, key=lambda item: item.total_score)
            if research_scores
            else None
        ),
        research_1m_first_confirmed_score=(
            research_confirmed[0] if research_confirmed else None
        ),
        first_leader_confirmed_at=first_leader_confirmed_at,
        last_leader_confirmed_at=last_leader_confirmed_at,
        bars_in_confirmed_state=bars_in_confirmed_state,
        first_setup_candidate_at=first_setup_candidate_at,
        first_setup_candidate=first_setup_candidate,
        first_setup_valid_at=first_setup_valid_at,
        first_execution_valid_at=first_execution_valid_at,
        leader_to_first_candidate_minutes=_minutes(
            first_leader_confirmed_at, first_setup_candidate_at
        ),
        leader_to_first_valid_minutes=_minutes(
            first_leader_confirmed_at, first_setup_valid_at
        ),
        transitions=tuple(transitions),
        best_mode_a=_best(
            [item for item in all_candidates if item.mode == "controlled_pullback"]
        ),
        best_mode_b=_best(
            [item for item in all_candidates if item.mode == "momentum_compression"]
        ),
        gate_counts=tuple(
            SetupGateCount(
                mode=mode,
                gate=gate,
                passed=gate_pass[(mode, gate)],
                failed=gate_fail[(mode, gate)],
            )
            for mode, gate in gate_keys
        ),
    )


def build_leader_momentum_cohort_report(
    observations: list[LeaderMomentumCohortObservation]
    | tuple[LeaderMomentumCohortObservation, ...],
) -> LeaderMomentumCohortReport:
    """Aggregate winner/control traces without using outcome labels in decisions."""

    winners = [item for item in observations if item.cohort == "winner"]
    controls = [item for item in observations if item.cohort == "control"]
    winner_confirmed = sum(
        item.trace.first_leader_confirmed_at is not None for item in winners
    )
    control_confirmed = sum(
        item.trace.first_leader_confirmed_at is not None for item in controls
    )
    winner_traded = sum(bool(item.trace.strategy_snapshot.trades) for item in winners)
    control_traded = sum(bool(item.trace.strategy_snapshot.trades) for item in controls)
    total_confirmed = winner_confirmed + control_confirmed
    total_traded = winner_traded + control_traded

    def ratio(left: int, right: int) -> Decimal | None:
        if right <= 0:
            return None
        return Decimal(left) / Decimal(right)

    return LeaderMomentumCohortReport(
        winner_observations=len(winners),
        control_observations=len(controls),
        winner_confirmed=winner_confirmed,
        control_confirmed=control_confirmed,
        winner_traded=winner_traded,
        control_traded=control_traded,
        leader_recall=ratio(winner_confirmed, len(winners)),
        leader_false_positive_rate=ratio(control_confirmed, len(controls)),
        leader_precision=ratio(winner_confirmed, total_confirmed),
        trade_precision=ratio(winner_traded, total_traded),
    )


__all__ = [
    "LeaderMomentumCohortObservation",
    "LeaderMomentumCohortReport",
    "LeaderMomentumDiagnosticTrace",
    "LeaderScoreBreakdown",
    "LeaderTransition",
    "SetupCandidateDiagnostic",
    "SetupGateCount",
    "SetupGateDiagnostic",
    "build_leader_momentum_cohort_report",
    "diagnose_leader_momentum_continuation",
]
