from __future__ import annotations

"""Ordered setup-funnel diagnostics for frozen Leader Momentum v1.1.

The existing diagnostics intentionally evaluate every gate on every candidate
window so researchers can inspect marginal failure rates.  Those counts are not
a causal funnel because ``_setup_at`` short-circuits.  This module adds a second,
additive view that mirrors the frozen evaluator's gate order without changing
strategy decisions or granting execution authority.
"""

from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from . import strategy_leader_momentum_continuation as leader
from . import strategy_leader_momentum_diagnostics as diag
from .models import MarketBar
from .strategy_timeframes import resample_final_bars

FunnelStage = Literal[
    "common",
    "controlled_pullback",
    "momentum_compression",
    "execution",
]


class SetupFunnelAttempt(BaseModel):
    """One short-circuit path actually reached by an evaluator attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    stage: FunnelStage
    mode: leader.LeaderMode | None = None
    window_length: int | None = Field(default=None, ge=2, le=6)
    steps: tuple[diag.SetupGateDiagnostic, ...] = ()
    first_failure_gate: str | None = None
    eligible: bool = False


class SetupGateFunnelCount(BaseModel):
    """Conditional pass/fail statistics for one ordered gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: FunnelStage
    mode: leader.LeaderMode | None = None
    order: int = Field(ge=1)
    gate: str
    reached: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    first_failure_count: int = Field(ge=0)
    conditional_pass_rate: Decimal | None = Field(default=None, ge=0, le=1)


class LeaderMomentumFunnelDiagnostic(BaseModel):
    """Frozen v1.1 result plus short-circuit setup-funnel evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = leader.POLICY_VERSION
    base_trace: diag.LeaderMomentumDiagnosticTrace
    attempts: tuple[SetupFunnelAttempt, ...] = ()
    gate_funnel: tuple[SetupGateFunnelCount, ...] = ()
    eligible_setup_attempts: int = Field(default=0, ge=0)
    execution_authority: Literal[False] = False


def _append_step(
    steps: list[diag.SetupGateDiagnostic],
    gate: diag.SetupGateDiagnostic,
) -> bool:
    steps.append(gate)
    return gate.passed


def _attempt(
    *,
    observed_at: datetime,
    stage: FunnelStage,
    steps: list[diag.SetupGateDiagnostic],
    mode: leader.LeaderMode | None = None,
    window_length: int | None = None,
    eligible: bool = False,
) -> SetupFunnelAttempt:
    first_failure = next((item.gate for item in steps if not item.passed), None)
    return SetupFunnelAttempt(
        observed_at=observed_at,
        stage=stage,
        mode=mode,
        window_length=window_length,
        steps=tuple(steps),
        first_failure_gate=first_failure,
        eligible=eligible,
    )


def summarize_setup_funnel(
    attempts: list[SetupFunnelAttempt] | tuple[SetupFunnelAttempt, ...],
) -> tuple[SetupGateFunnelCount, ...]:
    """Aggregate only gates that were actually reached before short-circuit."""

    reached: dict[tuple[FunnelStage, str | None, int, str], int] = defaultdict(int)
    passed: dict[tuple[FunnelStage, str | None, int, str], int] = defaultdict(int)
    failed: dict[tuple[FunnelStage, str | None, int, str], int] = defaultdict(int)
    first_failed: dict[tuple[FunnelStage, str | None, int, str], int] = defaultdict(int)

    for attempt in attempts:
        for order, step in enumerate(attempt.steps, start=1):
            mode = attempt.mode
            key = (attempt.stage, mode, order, step.gate)
            reached[key] += 1
            if step.passed:
                passed[key] += 1
            else:
                failed[key] += 1
                if attempt.first_failure_gate == step.gate:
                    first_failed[key] += 1
                # By construction a short-circuit attempt never reaches later
                # gates after its first failure.
                break

    result: list[SetupGateFunnelCount] = []
    for funnel_key in sorted(
        reached,
        key=lambda item: (item[0], item[1] or "", item[2], item[3]),
    ):
        stage, mode, order, gate = cast(Any, funnel_key)
        total = reached[funnel_key]
        result.append(
            SetupGateFunnelCount(
                stage=stage,
                mode=mode,
                order=order,
                gate=gate,
                reached=total,
                passed=passed[funnel_key],
                failed=failed[funnel_key],
                first_failure_count=first_failed[funnel_key],
                conditional_pass_rate=(
                    Decimal(passed[funnel_key]) / Decimal(total) if total else None
                ),
            )
        )
    return tuple(result)


def _common_attempt(
    regular: list[MarketBar],
    sampled: list[MarketBar],
    ema9_3m: list[Decimal | None],
    atr14_3m: list[Decimal | None],
    *,
    index: int,
) -> SetupFunnelAttempt:
    current = sampled[index]
    common = diag._common_setup_values(
        regular,
        sampled,
        ema9_3m,
        atr14_3m,
        index=index,
    )
    steps: list[diag.SetupGateDiagnostic] = []

    volatility_ready = (
        ema9_3m[index] is not None
        and (atr_value := atr14_3m[index]) is not None
        and atr_value > 0
    )
    if not _append_step(
        steps,
        diag._gate("trend_volatility_ready", volatility_ready),
    ):
        return _attempt(observed_at=current.end_time, stage="common", steps=steps)

    entry_atr = common["entry_atr"]
    entry_atr_ready = isinstance(entry_atr, Decimal) and entry_atr > 0
    if not _append_step(
        steps,
        diag._gate("entry_atr_ready", entry_atr_ready),
    ):
        return _attempt(observed_at=current.end_time, stage="common", steps=steps)

    if not _append_step(
        steps,
        diag._gate("trend_structure", bool(common["trend_ok"])),
    ):
        return _attempt(observed_at=current.end_time, stage="common", steps=steps)

    extension_pct = common["extension_pct"]
    if not _append_step(
        steps,
        diag._gate(
            "ema9_extension_pct",
            isinstance(extension_pct, Decimal)
            and extension_pct <= leader.MAX_EMA9_EXTENSION_PCT,
            actual=extension_pct if isinstance(extension_pct, Decimal) else None,
            maximum=leader.MAX_EMA9_EXTENSION_PCT,
        ),
    ):
        return _attempt(observed_at=current.end_time, stage="common", steps=steps)

    extension_atr = common["extension_atr"]
    _append_step(
        steps,
        diag._gate(
            "ema9_extension_atr",
            isinstance(extension_atr, Decimal)
            and extension_atr <= leader.MAX_ATR_EXTENSION,
            actual=extension_atr if isinstance(extension_atr, Decimal) else None,
            maximum=leader.MAX_ATR_EXTENSION,
        ),
    )
    return _attempt(
        observed_at=current.end_time,
        stage="common",
        steps=steps,
        eligible=all(item.passed for item in steps),
    )


def _window_attempt(
    candidate: diag.SetupCandidateDiagnostic,
    *,
    window_length: int,
) -> SetupFunnelAttempt:
    if candidate.mode == "controlled_pullback":
        ordered_names: tuple[str, ...] = (
            "continuity",
            "impulse_pct",
            "pullback_no_new_high",
            "pullback_retrace_min",
            "pullback_retrace_max",
            "pullback_volume_ratio",
            "breakout_close",
            "close_location",
            "breakout_volume_ratio",
        )
        stage: FunnelStage = "controlled_pullback"
    else:
        ordered_names = (
            "continuity",
            "impulse_pct",
            "compression_width_ratio",
            "compression_above_ema20",
            "breakout_close",
            "hod_break",
            "close_location",
            "breakout_volume_ratio",
        )
        stage = "momentum_compression"

    by_name = {item.gate: item for item in candidate.gates}
    steps: list[diag.SetupGateDiagnostic] = []
    for name in ordered_names:
        step = by_name[name]
        steps.append(step)
        if not step.passed:
            break
    return _attempt(
        observed_at=candidate.observed_at,
        stage=stage,
        mode=candidate.mode,
        window_length=window_length,
        steps=steps,
        eligible=all(item.passed for item in steps) and len(steps) == len(ordered_names),
    )


def _execution_attempt(
    candidate: diag.SetupCandidateDiagnostic,
    *,
    window_length: int,
) -> SetupFunnelAttempt:
    risk = next(item for item in candidate.gates if item.gate == "proposed_risk_pct")
    return _attempt(
        observed_at=candidate.observed_at,
        stage="execution",
        mode=candidate.mode,
        window_length=window_length,
        steps=[risk],
        eligible=risk.passed,
    )


def _setup_attempts_at(
    regular: list[MarketBar],
    sampled: list[MarketBar],
    ema9_3m: list[Decimal | None],
    atr14_3m: list[Decimal | None],
    *,
    index: int,
) -> tuple[list[SetupFunnelAttempt], bool]:
    """Mirror the frozen `_setup_at` short-circuit path for one bar.

    Returns attempts plus whether a setup survived the execution-risk gate.  A
    risk failure stops the same-bar search because frozen v1.1 has already
    returned a setup from `_setup_at` before `_trade_from_signal` rejects it.
    """

    attempts: list[SetupFunnelAttempt] = []
    common = _common_attempt(
        regular,
        sampled,
        ema9_3m,
        atr14_3m,
        index=index,
    )
    attempts.append(common)
    if not common.eligible:
        return attempts, False

    mode_a = diag._mode_a_candidates(
        regular,
        sampled,
        ema9_3m,
        atr14_3m,
        index=index,
    )
    for window_length, candidate in zip(range(2, 7), mode_a, strict=False):
        attempt = _window_attempt(candidate, window_length=window_length)
        attempts.append(attempt)
        if not attempt.eligible:
            continue
        execution = _execution_attempt(candidate, window_length=window_length)
        attempts.append(execution)
        return attempts, execution.eligible

    mode_b = diag._mode_b_candidates(
        regular,
        sampled,
        ema9_3m,
        atr14_3m,
        index=index,
    )
    for window_length, candidate in zip(range(2, 7), mode_b, strict=False):
        attempt = _window_attempt(candidate, window_length=window_length)
        attempts.append(attempt)
        if not attempt.eligible:
            continue
        execution = _execution_attempt(candidate, window_length=window_length)
        attempts.append(execution)
        return attempts, execution.eligible

    return attempts, False


def diagnose_leader_momentum_funnel(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    context: leader.LeaderMomentumContext | None = None,
    entry_start_et: time = time(9, 35),
    last_entry_et: time = time(15, 30),
    force_flat_et: time = time(15, 55),
) -> LeaderMomentumFunnelDiagnostic:
    """Add ordered gate funnels without altering the frozen v1.1 snapshot."""

    base_trace = diag.diagnose_leader_momentum_continuation(
        bars,
        context=context,
        entry_start_et=entry_start_et,
        last_entry_et=last_entry_et,
        force_flat_et=force_flat_et,
    )
    regular = leader._regular_final_bars(bars)
    if not regular:
        return LeaderMomentumFunnelDiagnostic(base_trace=base_trace)

    session_date = regular[-1].start_time.astimezone(diag._ET).date()
    regular = [
        bar
        for bar in regular
        if bar.start_time.astimezone(diag._ET).date() == session_date
    ]
    sampled = [
        bar
        for bar in resample_final_bars(regular, "3m")
        if bar.session == "regular"
    ]
    if len(sampled) < 10:
        return LeaderMomentumFunnelDiagnostic(base_trace=base_trace)

    ema9_3m = leader._ema([bar.close for bar in sampled], 9)
    atr14_3m = leader._atr(sampled, 14)
    leader_confirmed_until: datetime | None = None
    attempts: list[SetupFunnelAttempt] = []
    eligible_setup_attempts = 0

    for index in range(9, len(sampled) - 1):
        bar = sampled[index]
        bar_et = bar.end_time.astimezone(diag._ET)
        if bar_et.time() < entry_start_et:
            continue
        if bar_et.time() > last_entry_et:
            break
        if not (leader.MIN_PRICE <= bar.close <= leader.MAX_PRICE):
            continue

        score = leader._leader_score(
            regular,
            sampled,
            index=index,
            context=context,
        )
        regular_through = [item for item in regular if item.end_time <= bar.end_time]
        if not regular_through:
            continue
        session_return = leader._pct_change(regular_through[0].open, bar.close)
        if score >= leader.MIN_LEADER_SCORE and session_return >= leader.MIN_SESSION_RETURN_PCT:
            leader_confirmed_until = bar.end_time + leader.LEADER_LATCH_TTL
        latched = (
            leader_confirmed_until is not None
            and bar.end_time <= leader_confirmed_until
        )
        if not latched:
            continue

        bar_attempts, eligible = _setup_attempts_at(
            regular,
            sampled,
            ema9_3m,
            atr14_3m,
            index=index,
        )
        attempts.extend(bar_attempts)
        eligible_setup_attempts += int(eligible)

    return LeaderMomentumFunnelDiagnostic(
        base_trace=base_trace,
        attempts=tuple(attempts),
        gate_funnel=summarize_setup_funnel(attempts),
        eligible_setup_attempts=eligible_setup_attempts,
    )


__all__ = [
    "LeaderMomentumFunnelDiagnostic",
    "SetupFunnelAttempt",
    "SetupGateFunnelCount",
    "diagnose_leader_momentum_funnel",
    "summarize_setup_funnel",
]
