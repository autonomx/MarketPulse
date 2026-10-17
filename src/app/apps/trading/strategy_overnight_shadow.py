"""Pure overnight-gap research logic for ``overnight-trading-strategy-shadow``.

Everything here is deterministic and provider-free: candidate features from
regular-session bars that ended by the data cutoff, per-arm selection, outcome
labelling from one-minute bars, and the per-session scorecard. The monitor in
``strategy_overnight_monitor`` fetches data and persists events; this module
never places, sizes or routes an order (SHADOW evidence only).
"""
from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .strategies.models import OvernightArmConfig, OvernightShadowConfig
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time

OVERNIGHT_TRADING_STRATEGY_ID = "overnight-trading-strategy-shadow"
OVERNIGHT_POPULATION_MIN_CHANGE = Decimal("0.10")
_EXIT_MISSING_RETURN = Decimal("-0.50")
_BIG_GAP = Decimal("0.20")


@dataclass(frozen=True, slots=True)
class OvernightBar:
    """A finalized SIP bar; ``start`` is timezone-aware."""

    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class OvernightCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    instrument_id: str
    prior_close: Decimal
    price: Decimal
    change: Decimal
    high: Decimal
    low: Decimal
    close_location: Decimal
    volume: Decimal
    adv: Decimal | None
    volume_ratio: Decimal | None
    dollar_volume: Decimal


class OvernightPick(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm_id: str
    rank: int
    candidate: OvernightCandidate


class OvernightOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm_id: str
    symbol: str
    instrument_id: str
    session_date: date
    next_session_date: date
    entry_price: Decimal | None
    exit_open: Decimal | None
    premarket_0800: Decimal | None
    price_0945: Decimal | None
    gross_return: Decimal | None
    net_return: Decimal | None
    gap_return: Decimal | None
    status: Literal["labelled", "no_entry", "no_open"]


def half_spread(price: Decimal) -> Decimal:
    """Per-side cost proxy by price bucket (same model as the research backtests)."""

    if price < 3:
        return Decimal("0.015")
    if price < 10:
        return Decimal("0.01")
    return Decimal("0.005")


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


def build_candidate(
    *,
    symbol: str,
    instrument_id: str,
    prior_close: Decimal,
    bars: Sequence[OvernightBar],
    adv_volumes: Sequence[Decimal],
    session_date: date,
    config: OvernightShadowConfig,
    bar_minutes: int = 30,
) -> OvernightCandidate | None:
    """Features from regular-session ``bar_minutes`` bars that ENDED by ``config.data_cutoff_et``."""

    if prior_close <= 0:
        return None
    cutoff = datetime.combine(session_date, config.data_cutoff_et, tzinfo=_ET)
    regular = sorted(
        (
            bar for bar in bars
            if bar.start.astimezone(_ET).date() == session_date
            and bar.start.astimezone(_ET).time() >= time(9, 30)
            and bar.start < cutoff
        ),
        key=lambda bar: bar.start,
    )
    if not regular:
        return None
    # A bar that has not ended by the cutoff is not known at decision time.
    regular = [bar for bar in regular if bar.start + timedelta(minutes=bar_minutes) <= cutoff]
    if not regular:
        return None
    price = regular[-1].close
    high = max(bar.high for bar in regular)
    low = min(bar.low for bar in regular)
    volume = sum((bar.volume for bar in regular), Decimal("0"))
    recent = list(adv_volumes)[-config.adv_sessions:]
    adv = (sum(recent, Decimal("0")) / len(recent)) if len(recent) >= config.min_adv_sessions else None
    return OvernightCandidate(
        symbol=symbol,
        instrument_id=instrument_id,
        prior_close=prior_close,
        price=price,
        change=_q(price / prior_close - 1),
        high=high,
        low=low,
        close_location=_q((price - low) / (high - low)) if high > low else Decimal("1"),
        volume=volume,
        adv=_q(adv) if adv is not None else None,
        volume_ratio=_q(volume / adv) if adv else None,
        dollar_volume=_q(sum((bar.close * bar.volume for bar in regular), Decimal("0"))),
    )


def _qualifies(candidate: OvernightCandidate, arm: OvernightArmConfig, config: OvernightShadowConfig) -> bool:
    if not (config.minimum_price <= candidate.price <= config.maximum_price):
        return False
    if candidate.change < arm.min_change:
        return False
    if arm.max_change is not None and candidate.change >= arm.max_change:
        return False
    if candidate.volume_ratio is None or candidate.volume_ratio < arm.min_volume_ratio:
        return False
    return candidate.close_location >= arm.min_close_location


def select_arm_picks(
    candidates: Iterable[OvernightCandidate],
    config: OvernightShadowConfig,
) -> dict[str, list[OvernightPick]]:
    """Top ``max_picks_per_arm`` qualifying candidates per enabled arm, by dollar volume then symbol."""

    pool = list(candidates)
    picks: dict[str, list[OvernightPick]] = {}
    for arm in config.arms:
        if not arm.enabled:
            continue
        qualified = sorted(
            (c for c in pool if _qualifies(c, arm, config)),
            key=lambda c: (-c.dollar_volume, c.symbol),
        )[: config.max_picks_per_arm]
        picks[arm.arm_id] = [
            OvernightPick(arm_id=arm.arm_id, rank=index + 1, candidate=c)
            for index, c in enumerate(qualified)
        ]
    return picks


def population(candidates: Iterable[OvernightCandidate], config: OvernightShadowConfig) -> list[OvernightCandidate]:
    """Baseline population: every in-band stock up >= 10% at decision time (no other rules)."""

    return sorted(
        (
            c for c in candidates
            if c.change >= OVERNIGHT_POPULATION_MIN_CHANGE
            and config.minimum_price <= c.price <= config.maximum_price
        ),
        key=lambda c: c.symbol,
    )


def _first_at_or_after(bars: Sequence[OvernightBar], moment: datetime, before: datetime) -> OvernightBar | None:
    return next((bar for bar in sorted(bars, key=lambda b: b.start) if moment <= bar.start < before), None)


def label_outcome(
    *,
    arm_id: str,
    candidate: OvernightCandidate,
    session_date: date,
    next_session_date: date,
    session_bars: Sequence[OvernightBar],
    next_bars: Sequence[OvernightBar],
    config: OvernightShadowConfig,
    session_close: time | None = None,
    next_close: time | None = None,
) -> OvernightOutcome:
    """Entry at the ``entry_et`` 1m open; primary exit at the next session's 09:30 open."""

    session_close = session_close or regular_close_time(session_date)
    next_close = next_close or regular_close_time(next_session_date)
    entry_bar = _first_at_or_after(
        session_bars,
        datetime.combine(session_date, config.entry_et, tzinfo=_ET),
        datetime.combine(session_date, session_close, tzinfo=_ET),
    )
    open_at = datetime.combine(next_session_date, time(9, 30), tzinfo=_ET)
    flat_at = datetime.combine(next_session_date, next_close, tzinfo=_ET) - timedelta(minutes=5)
    open_bar = _first_at_or_after(next_bars, open_at, flat_at)
    premarket = [
        bar for bar in next_bars
        if bar.start.astimezone(_ET).date() == next_session_date
        and time(4, 0) <= bar.start.astimezone(_ET).time()
        and bar.start + timedelta(minutes=1) <= datetime.combine(next_session_date, time(8, 0), tzinfo=_ET)
    ]
    bar_0945 = _first_at_or_after(next_bars, datetime.combine(next_session_date, time(9, 45), tzinfo=_ET), flat_at)
    common = dict(
        arm_id=arm_id, symbol=candidate.symbol, instrument_id=candidate.instrument_id,
        session_date=session_date, next_session_date=next_session_date,
        premarket_0800=premarket[-1].close if premarket else None,
        price_0945=bar_0945.open if bar_0945 else None,
    )
    if entry_bar is None:
        return OvernightOutcome(**common, entry_price=None, exit_open=None, gross_return=None,
                                net_return=None, gap_return=None, status="no_entry")
    entry = entry_bar.open
    if open_bar is None:
        return OvernightOutcome(**common, entry_price=entry, exit_open=None, gross_return=None,
                                net_return=_EXIT_MISSING_RETURN, gap_return=None, status="no_open")
    exit_price = open_bar.open
    gross = exit_price / entry - 1
    net = exit_price * (1 - half_spread(exit_price)) / (entry * (1 + half_spread(entry))) - 1
    return OvernightOutcome(
        **common, entry_price=entry, exit_open=exit_price, gross_return=_q(gross), net_return=_q(net),
        gap_return=_q(exit_price / candidate.price - 1), status="labelled",
    )


class OvernightArmScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm_id: str
    picks: int
    labelled: int
    mean_net_return: Decimal | None
    median_net_return: Decimal | None
    winners: int
    big_gap_ups: int
    big_gap_downs: int


def score(arm_id: str, outcomes: Sequence[OvernightOutcome]) -> OvernightArmScore:
    """Equal-weight session score for one arm or population (``no_open`` counts as its -50%)."""

    nets = [o.net_return for o in outcomes if o.net_return is not None]
    gross = [o.gross_return for o in outcomes if o.gross_return is not None]
    return OvernightArmScore(
        arm_id=arm_id,
        picks=len(outcomes),
        labelled=len(nets),
        mean_net_return=_q(sum(nets, Decimal("0")) / len(nets)) if nets else None,
        median_net_return=_q(Decimal(statistics.median(nets))) if nets else None,
        winners=sum(1 for n in nets if n > 0),
        big_gap_ups=sum(1 for g in gross if g >= _BIG_GAP),
        big_gap_downs=sum(1 for g in gross if g <= -_BIG_GAP),
    )


__all__ = [
    "OVERNIGHT_POPULATION_MIN_CHANGE",
    "OVERNIGHT_TRADING_STRATEGY_ID",
    "OvernightArmScore",
    "OvernightBar",
    "OvernightCandidate",
    "OvernightOutcome",
    "OvernightPick",
    "build_candidate",
    "half_spread",
    "label_outcome",
    "population",
    "score",
    "select_arm_picks",
]
