from __future__ import annotations

"""Research-only ablations for frozen Leader Momentum v1.2.

The production policy remains authoritative and unchanged.  This module reuses
its score/setup/trade helpers and permits only two structural research changes:

* ignore selected setup modes after the frozen setup engine identifies them;
* stop after a configurable number of accepted trades.

No thresholds, risk math, fills, stops, partials, or exit rules are modified.
The baseline research variant is expected to reproduce the frozen evaluator.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import strategy_leader_momentum_continuation as leader
from .models import MarketBar
from app.apps.trading.us_equity_calendar import EASTERN as _ET


ResearchVariantName = Literal[
    "baseline_v1_2",
    "controlled_pullback_only",
    "single_trade_only",
    "controlled_pullback_single_trade",
]


class LeaderMomentumResearchVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ResearchVariantName
    allowed_modes: tuple[leader.LeaderMode, ...]
    max_trades: int = Field(ge=1, le=leader.MAX_TRADES)

    @model_validator(mode="after")
    def validate_modes(self):
        if not self.allowed_modes:
            raise ValueError("leader_research_variant_requires_mode")
        if len(set(self.allowed_modes)) != len(self.allowed_modes):
            raise ValueError("leader_research_variant_duplicate_mode")
        return self


BASELINE_V1_2 = LeaderMomentumResearchVariant(
    name="baseline_v1_2",
    allowed_modes=("controlled_pullback", "momentum_compression"),
    max_trades=leader.MAX_TRADES,
)
CONTROLLED_PULLBACK_ONLY = LeaderMomentumResearchVariant(
    name="controlled_pullback_only",
    allowed_modes=("controlled_pullback",),
    max_trades=leader.MAX_TRADES,
)
SINGLE_TRADE_ONLY = LeaderMomentumResearchVariant(
    name="single_trade_only",
    allowed_modes=("controlled_pullback", "momentum_compression"),
    max_trades=1,
)
CONTROLLED_PULLBACK_SINGLE_TRADE = LeaderMomentumResearchVariant(
    name="controlled_pullback_single_trade",
    allowed_modes=("controlled_pullback",),
    max_trades=1,
)


class LeaderMomentumResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_policy_version: Literal["leader-momentum-continuation-v1.2"] = leader.POLICY_VERSION
    variant: LeaderMomentumResearchVariant
    snapshot: leader.LeaderMomentumSnapshot
    execution_authority: Literal[False] = False


def _snapshot_from_state(
    *,
    regular: list[MarketBar],
    sampled: list[MarketBar],
    ema9_3m: list[Decimal | None],
    atr14_3m: list[Decimal | None],
    trades: list[leader.LeaderMomentumTrade],
    latest_score: Decimal,
    latest_vwap: Decimal | None,
    latest_session_return: Decimal | None,
    latest_mode: leader.LeaderMode | None,
    latest_signal_index: int | None,
    latest_stop: Decimal | None,
    leader_confirmed_at: datetime | None,
    leader_confirmed_until: datetime | None,
    raw_gap: tuple[datetime, datetime] | None,
    recovered_gap_count: int,
    session_date,
    as_of: datetime,
) -> leader.LeaderMomentumSnapshot:
    final_ema9_1m = leader._ema([bar.close for bar in regular], 9)[-1]
    final_ema20_1m = leader._ema([bar.close for bar in regular], 20)[-1]
    final_atr14_1m = leader._atr(regular, 14)[-1]
    leader_latched_at_end = (
        leader_confirmed_until is not None and as_of <= leader_confirmed_until
    )
    shared = {
        "leader_score": latest_score,
        "leader_confirmed_at": leader_confirmed_at,
        "leader_confirmed_until": leader_confirmed_until,
        "session_return_pct": latest_session_return,
        "session_vwap": latest_vwap,
        "ema9_1m": final_ema9_1m,
        "ema20_1m": final_ema20_1m,
        "ema9_3m": ema9_3m[-1],
        "atr14_1m": final_atr14_1m,
        "atr14_3m": atr14_3m[-1],
        "recovered_gap_count": recovered_gap_count,
        "data_gap_start": raw_gap[0] if raw_gap is not None else None,
        "data_gap_resume": raw_gap[1] if raw_gap is not None else None,
        "session_date": session_date.isoformat(),
        "as_of": as_of,
    }
    if trades:
        last = trades[-1]
        state: leader.LeaderState = (
            "force_flat"
            if last.exit_reason_code == "LEADER_MOMENTUM_FORCE_FLAT"
            else "completed"
        )
        return leader.LeaderMomentumSnapshot(
            state=state,
            reason_code=last.exit_reason_code,
            setup_mode=last.mode,
            signal_time=last.signal_time,
            entry_time=last.entry_time,
            entry_price=last.entry_price,
            initial_stop_price=last.initial_stop_price,
            risk_pct=(last.entry_price - last.initial_stop_price)
            / last.entry_price
            * Decimal("100"),
            trades=tuple(trades),
            **shared,
        )
    if latest_signal_index is not None and latest_mode is not None:
        return leader.LeaderMomentumSnapshot(
            state="breakout_armed",
            reason_code="LEADER_MOMENTUM_BREAKOUT_AWAITING_ENTRY",
            setup_mode=latest_mode,
            signal_time=sampled[latest_signal_index].end_time,
            initial_stop_price=latest_stop,
            **shared,
        )
    return leader.LeaderMomentumSnapshot(
        state="waiting_setup" if leader_latched_at_end else "waiting_leader",
        reason_code=(
            "LEADER_MOMENTUM_WAITING_SETUP"
            if leader_latched_at_end
            else "LEADER_MOMENTUM_LEADER_NOT_CONFIRMED"
        ),
        **shared,
    )


def evaluate_leader_momentum_research_variant(
    bars: Sequence[MarketBar],
    *,
    variant: LeaderMomentumResearchVariant = BASELINE_V1_2,
    context: leader.LeaderMomentumContext | None = None,
    discovered_at: datetime | None = None,
    entry_start_et: time = time(9, 35),
    last_entry_et: time = time(15, 30),
    force_flat_et: time = time(15, 55),
) -> LeaderMomentumResearchResult:
    """Replay a structural ablation while preserving frozen v1.2 mechanics."""

    if discovered_at is not None:
        if discovered_at.tzinfo is None:
            raise ValueError("leader_research_discovery_time_requires_timezone")
        discovered_local = discovered_at.astimezone(_ET).time().replace(tzinfo=None)
        entry_start_et = max(entry_start_et, discovered_local)

    regular = leader._regular_final_bars(list(bars))
    if not regular:
        return LeaderMomentumResearchResult(
            variant=variant,
            snapshot=leader.LeaderMomentumSnapshot(
                state="waiting_session",
                reason_code="LEADER_MOMENTUM_WAITING_SESSION",
            ),
        )

    session_date = regular[-1].start_time.astimezone(_ET).date()
    regular = [
        bar for bar in regular if bar.start_time.astimezone(_ET).date() == session_date
    ]
    as_of = regular[-1].end_time
    raw_gap = leader._first_internal_gap(regular)
    sampled = [
        bar
        for bar in leader.resample_final_bars(regular, "3m")
        if bar.session == "regular"
    ]
    if len(sampled) < 10:
        return LeaderMomentumResearchResult(
            variant=variant,
            snapshot=leader.LeaderMomentumSnapshot(
                state="waiting_leader",
                reason_code="LEADER_MOMENTUM_INSUFFICIENT_HISTORY",
                session_date=session_date.isoformat(),
                as_of=as_of,
            ),
        )

    closes = [bar.close for bar in sampled]
    ema9_3m = leader._ema(closes, 9)
    atr14_3m = leader._atr(sampled, 14)
    recovered_gap_count = sum(
        1
        for left, right in zip(sampled, sampled[1:])
        if right.start_time - left.start_time != timedelta(minutes=3)
    )

    trades: list[leader.LeaderMomentumTrade] = []
    next_allowed_time = datetime.combine(session_date, entry_start_et, tzinfo=_ET)
    latest_score = Decimal("0")
    latest_vwap: Decimal | None = None
    latest_session_return: Decimal | None = None
    latest_mode: leader.LeaderMode | None = None
    latest_signal_index: int | None = None
    latest_stop: Decimal | None = None
    leader_confirmed_at: datetime | None = None
    leader_confirmed_until: datetime | None = None

    for index in range(9, len(sampled) - 1):
        bar = sampled[index]
        bar_et = bar.end_time.astimezone(_ET)
        if bar_et < next_allowed_time:
            continue
        if bar_et.time() < entry_start_et:
            continue
        if bar_et.time() > last_entry_et:
            break
        if not (leader.MIN_PRICE <= bar.close <= leader.MAX_PRICE):
            continue

        score = leader._leader_score(regular, sampled, index=index, context=context)
        latest_score = score
        regular_through = [item for item in regular if item.end_time <= bar.end_time]
        latest_vwap = leader.session_vwap(regular_through)
        latest_session_return = leader._pct_change(regular_through[0].open, bar.close)
        if (
            score >= leader.MIN_LEADER_SCORE
            and latest_session_return >= leader.MIN_SESSION_RETURN_PCT
        ):
            leader_confirmed_at = bar.end_time
            leader_confirmed_until = bar.end_time + leader.LEADER_LATCH_TTL
        leader_latched = (
            leader_confirmed_until is not None and bar.end_time <= leader_confirmed_until
        )
        if not leader_latched:
            continue

        setup = leader._setup_at(
            regular,
            sampled,
            ema9_3m,
            atr14_3m,
            index=index,
        )
        if setup is None:
            continue
        mode, stop_reference, _volume_ratio, entry_atr = setup
        if mode not in variant.allowed_modes:
            continue

        if trades and bar.close <= max(item.high for item in sampled[:index]):
            continue

        trade = leader._trade_from_signal(
            sampled,
            ema9_3m,
            atr14_3m,
            signal_index=index,
            mode=mode,
            stop_reference=stop_reference,
            entry_atr=entry_atr,
            force_flat_et=force_flat_et,
        )
        latest_mode = mode
        latest_signal_index = index
        if trade is None:
            continue
        if discovered_at is not None and trade.signal_time < discovered_at:
            raise RuntimeError("leader_research_signal_preceded_dynamic_discovery")
        latest_stop = trade.initial_stop_price
        trades.append(trade)
        if len(trades) >= variant.max_trades:
            break
        next_allowed_time = trade.exit_time.astimezone(_ET) + leader.REENTRY_COOLDOWN

    snapshot = _snapshot_from_state(
        regular=regular,
        sampled=sampled,
        ema9_3m=ema9_3m,
        atr14_3m=atr14_3m,
        trades=trades,
        latest_score=latest_score,
        latest_vwap=latest_vwap,
        latest_session_return=latest_session_return,
        latest_mode=latest_mode,
        latest_signal_index=latest_signal_index,
        latest_stop=latest_stop,
        leader_confirmed_at=leader_confirmed_at,
        leader_confirmed_until=leader_confirmed_until,
        raw_gap=raw_gap,
        recovered_gap_count=recovered_gap_count,
        session_date=session_date,
        as_of=as_of,
    )
    return LeaderMomentumResearchResult(variant=variant, snapshot=snapshot)


def assert_baseline_parity(
    bars: Sequence[MarketBar],
    *,
    context: leader.LeaderMomentumContext | None = None,
    discovered_at: datetime | None = None,
) -> None:
    """Fail if the research baseline diverges from frozen v1.2 semantics."""

    entry_start_et = time(9, 35)
    if discovered_at is not None:
        if discovered_at.tzinfo is None:
            raise ValueError("leader_research_discovery_time_requires_timezone")
        entry_start_et = max(
            entry_start_et,
            discovered_at.astimezone(_ET).time().replace(tzinfo=None),
        )
    frozen = leader.evaluate_leader_momentum_continuation(
        list(bars),
        context=context,
        entry_start_et=entry_start_et,
    )
    research = evaluate_leader_momentum_research_variant(
        bars,
        variant=BASELINE_V1_2,
        context=context,
        discovered_at=discovered_at,
    ).snapshot
    if research != frozen:
        raise AssertionError("leader_momentum_research_baseline_diverged_from_v1_2")


__all__ = [
    "BASELINE_V1_2",
    "CONTROLLED_PULLBACK_ONLY",
    "CONTROLLED_PULLBACK_SINGLE_TRADE",
    "SINGLE_TRADE_ONLY",
    "LeaderMomentumResearchResult",
    "LeaderMomentumResearchVariant",
    "assert_baseline_parity",
    "evaluate_leader_momentum_research_variant",
]
