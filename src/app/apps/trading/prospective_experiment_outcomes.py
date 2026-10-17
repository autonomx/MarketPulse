from __future__ import annotations

"""Prospective opportunity outcomes from the authoritative SIP trade tape."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .prospective_prediction_evidence import SIPTradeEvent, sip_trade_eligible


class ProspectiveGeometryOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    started_at: datetime
    ended_at: datetime
    entry_reference: Decimal = Field(gt=0)
    invalidation_price: Decimal = Field(gt=0)
    risk_per_share: Decimal = Field(gt=0)
    eligible_trade_count: int = Field(ge=0)
    mfe_pct: Decimal
    mae_pct: Decimal
    peak_r: Decimal
    plus_one_r_before_minus_one_r: bool
    plus_two_r_before_minus_one_r: bool
    return_1m_pct: Decimal | None = None
    return_3m_pct: Decimal | None = None
    return_5m_pct: Decimal | None = None
    return_10m_pct: Decimal | None = None
    return_20m_pct: Decimal | None = None
    return_60m_pct: Decimal | None = None
    close_return_pct: Decimal
    sequence_authority: Literal["consolidated_sip_trade_events"] = (
        "consolidated_sip_trade_events"
    )

    @field_validator("started_at", "ended_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("prospective geometry timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


def _return_pct(price: Decimal, entry: Decimal) -> Decimal:
    return (price / entry - Decimal("1")) * Decimal("100")


def evaluate_geometry_on_sip_tape(
    trades: list[SIPTradeEvent] | tuple[SIPTradeEvent, ...],
    *,
    started_at: datetime,
    entry_reference: Decimal,
    invalidation_price: Decimal,
) -> ProspectiveGeometryOutcome:
    """Score opportunity geometry without assuming the counterfactual trade filled.

    Consolidated trade-event ordering resolves the intrabar stop/target ambiguity
    that bar OHLC cannot. Only formal-policy eligible prints participate.
    """

    if started_at.tzinfo is None:
        raise ValueError("prospective geometry start must be timezone-aware")
    if entry_reference <= 0 or invalidation_price <= 0:
        raise ValueError("prospective geometry prices must be positive")
    risk = entry_reference - invalidation_price
    if risk <= 0:
        raise ValueError("prospective geometry invalidation must be below entry")

    start = started_at.astimezone(timezone.utc)
    eligible = sorted(
        [
            trade
            for trade in trades
            if trade.event_timestamp.astimezone(timezone.utc) >= start
            and sip_trade_eligible(trade)
        ],
        key=lambda trade: (
            trade.event_timestamp,
            trade.sequence if trade.sequence is not None else -1,
            trade.provider_event_id or "",
        ),
    )
    if not eligible:
        raise ValueError("prospective_geometry_requires_post_decision_sip_trades")

    prices = [trade.price for trade in eligible]
    mfe = _return_pct(max(prices), entry_reference)
    mae = _return_pct(min(prices), entry_reference)
    peak_r = (max(prices) - entry_reference) / risk
    one = entry_reference + risk
    two = entry_reference + risk * Decimal("2")

    one_result = False
    two_result = False
    one_done = False
    two_done = False
    for trade in eligible:
        price = trade.price
        if not one_done:
            if price <= invalidation_price:
                one_done = True
                one_result = False
            elif price >= one:
                one_done = True
                one_result = True
        if not two_done:
            if price <= invalidation_price:
                two_done = True
                two_result = False
            elif price >= two:
                two_done = True
                two_result = True
        if one_done and two_done:
            break

    def forward(minutes: int) -> Decimal | None:
        target = start + timedelta(minutes=minutes)
        trade = next(
            (
                value
                for value in eligible
                if value.event_timestamp.astimezone(timezone.utc) >= target
            ),
            None,
        )
        return (
            _return_pct(trade.price, entry_reference)
            if trade is not None
            else None
        )

    last = eligible[-1]
    return ProspectiveGeometryOutcome(
        started_at=start,
        ended_at=last.event_timestamp,
        entry_reference=entry_reference,
        invalidation_price=invalidation_price,
        risk_per_share=risk,
        eligible_trade_count=len(eligible),
        mfe_pct=mfe,
        mae_pct=mae,
        peak_r=peak_r,
        plus_one_r_before_minus_one_r=one_result,
        plus_two_r_before_minus_one_r=two_result,
        return_1m_pct=forward(1),
        return_3m_pct=forward(3),
        return_5m_pct=forward(5),
        return_10m_pct=forward(10),
        return_20m_pct=forward(20),
        return_60m_pct=forward(60),
        close_return_pct=_return_pct(last.price, entry_reference),
    )


__all__ = [
    "ProspectiveGeometryOutcome",
    "evaluate_geometry_on_sip_tape",
]
