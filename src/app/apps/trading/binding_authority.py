from __future__ import annotations

"""Typed provider-binding purpose and market-data authority guards.

Binding purpose is deliberately separate from provider capability. In
particular, IBKR is introduced as LIVE_DATA only: installing or enabling market
data can never, by itself, create brokerage/order execution authority.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BindingPurpose = Literal["LIVE_DATA", "EXECUTION", "REPLAY", "RESEARCH"]
MarketDataCapability = Literal[
    "QUOTE",
    "BID_ASK",
    "HISTORICAL_BARS",
    "EXACT_RANGE",
    "STREAMING_QUOTES",
    "STREAMING_BARS",
    "PARTIAL_MARKET",
    "HALT_STATUS",
]
MarketDataType = Literal["LIVE", "FROZEN", "DELAYED", "DELAYED_FROZEN", "UNKNOWN"]
AuthorityHealth = Literal[
    "READY",
    "OBSERVATION_ONLY",
    "DISCONNECTED",
    "CLIENT_UNAVAILABLE",
    "CONTRACT_AMBIGUOUS",
    "CONTRACT_UNAVAILABLE",
    "DELAYED",
    "FROZEN",
    "STALE",
    "ENTITLEMENT_MISSING",
    "ERROR",
    "UNKNOWN",
]


def infer_binding_purpose(binding_id: str | None) -> BindingPurpose:
    """Classify legacy binding IDs conservatively by their durable prefix.

    Existing production execution bindings predate an explicit purpose field, so
    ordinary legacy bindings remain EXECUTION for compatibility. New IBKR
    bindings are structurally LIVE_DATA in this phase even though their catalog
    IDs do not use the historical live: prefix.
    """

    value = str(binding_id or "").strip().casefold()
    if value.startswith("replay:"):
        return "REPLAY"
    if value.startswith("research:"):
        return "RESEARCH"
    if value.startswith("live:") or value.startswith("ibkr:"):
        return "LIVE_DATA"
    return "EXECUTION"


def _explicit_purpose_binding(binding_id: str | None) -> bool:
    value = str(binding_id or "").strip().casefold()
    return value.startswith(("replay:", "research:", "live:", "ibkr:"))


class PurposeBoundBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str = Field(min_length=1, max_length=240)
    purpose: BindingPurpose

    @model_validator(mode="after")
    def _prefix_consistent(self):
        inferred = infer_binding_purpose(self.binding_id)
        if _explicit_purpose_binding(self.binding_id) and inferred != self.purpose:
            raise ValueError("binding_purpose_conflicts_with_binding_id")
        return self


class MarketDataAuthorityDecision(BaseModel):
    """Typed explanation of whether a provider may own live-data authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    binding_id: str = Field(min_length=1, max_length=240)
    instrument_id: str = Field(min_length=1, max_length=200)
    purpose: BindingPurpose = "LIVE_DATA"
    capabilities: tuple[MarketDataCapability, ...] = ()
    health: AuthorityHealth = "UNKNOWN"
    market_data_type: MarketDataType = "UNKNOWN"
    entitlement_live: bool | None = None
    quote_age_seconds: Decimal | None = Field(default=None, ge=0)
    observed_at: datetime
    authoritative: bool = False
    reason_codes: tuple[str, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("market-data authority timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _authority_invariants(self):
        if self.provider.casefold() == "ibkr" and self.purpose == "EXECUTION":
            raise ValueError("ibkr_execution_authority_disabled")
        if self.authoritative and self.purpose != "LIVE_DATA":
            raise ValueError("market_data_authority_requires_live_data_purpose")
        if self.authoritative and (
            self.health != "READY"
            or self.market_data_type != "LIVE"
            or self.entitlement_live is not True
            or self.reason_codes
        ):
            raise ValueError("authoritative_market_data_decision_not_ready")
        return self


def require_execution_binding(
    binding_id: str | None,
    *,
    purpose: BindingPurpose | None = None,
) -> str | None:
    if binding_id is None:
        return None
    inferred = infer_binding_purpose(binding_id)
    resolved = purpose or inferred
    # Explicit LIVE_DATA/REPLAY/RESEARCH bindings are never up-cast by a caller
    # passing purpose="EXECUTION".
    if _explicit_purpose_binding(binding_id) and inferred != "EXECUTION":
        resolved = inferred
    if resolved != "EXECUTION":
        raise ValueError(f"execution_binding_purpose_invalid:{resolved}")
    return binding_id


def binding_can_execute(
    binding_id: str | None,
    *,
    purpose: BindingPurpose | None = None,
) -> bool:
    try:
        require_execution_binding(binding_id, purpose=purpose)
    except ValueError:
        return False
    return True


__all__ = [
    "AuthorityHealth",
    "BindingPurpose",
    "MarketDataAuthorityDecision",
    "MarketDataCapability",
    "MarketDataType",
    "PurposeBoundBinding",
    "binding_can_execute",
    "infer_binding_purpose",
    "require_execution_binding",
]
