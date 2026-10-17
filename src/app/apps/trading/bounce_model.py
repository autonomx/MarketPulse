from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .models import MarketBar
from typing import Final


LABEL_DEFINITION: Final = "P(+2R before -1R within 90 minutes)"


class BounceFeatureVector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gap_pct: Decimal
    premarket_dollar_volume_log10: Decimal
    tod_rvol: Decimal
    float_shares_log10: Decimal | None = None
    market_cap_log10: Decimal | None = None
    spread_bps: Decimal
    opening_impulse_pct: Decimal
    hod_distance_pct: Decimal
    pullback_depth_pct: Decimal
    pullback_volume_ratio: Decimal
    l2_over_l1: Decimal
    vwap_distance_pct: Decimal
    vwap_slope_pct: Decimal
    breakout_volume_ratio: Decimal
    atr_pct: Decimal
    minutes_since_open: Decimal
    catalyst_positive: Decimal = Decimal("0")
    catalyst_negative: Decimal = Decimal("0")
    dilution_flag: Decimal = Decimal("0")


class BounceModelScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = "gap_pullback_logistic"
    model_version: str = "1.0.0-baseline"
    observed_at: datetime
    probability: Decimal = Field(ge=0, le=1)
    label_definition: str = LABEL_DEFINITION
    features: BounceFeatureVector
    shadow_only: bool = True
    fingerprint: str


# Deliberately transparent starter coefficients. These are not claimed to be
# trained or profitable; fitting/replacing them requires walk-forward data.
_BASELINE_COEFFICIENTS: dict[str, Decimal] = {
    "gap_pct": Decimal("0.010"),
    "premarket_dollar_volume_log10": Decimal("0.20"),
    "tod_rvol": Decimal("0.035"),
    "spread_bps": Decimal("-0.004"),
    "opening_impulse_pct": Decimal("0.018"),
    "hod_distance_pct": Decimal("-0.025"),
    "pullback_depth_pct": Decimal("-0.020"),
    "pullback_volume_ratio": Decimal("-0.18"),
    "l2_over_l1": Decimal("0.85"),
    "vwap_distance_pct": Decimal("0.08"),
    "vwap_slope_pct": Decimal("0.15"),
    "breakout_volume_ratio": Decimal("0.22"),
    "atr_pct": Decimal("-0.03"),
    "minutes_since_open": Decimal("-0.003"),
    "catalyst_positive": Decimal("0.25"),
    "catalyst_negative": Decimal("-0.25"),
    "dilution_flag": Decimal("-0.40"),
}
_BASELINE_INTERCEPT = Decimal("-1.0")


def score_bounce_probability(
    features: BounceFeatureVector,
    *,
    observed_at: datetime,
    coefficients: dict[str, Decimal] | None = None,
    intercept: Decimal = _BASELINE_INTERCEPT,
    model_version: str = "1.0.0-baseline",
) -> BounceModelScore:
    """Versioned logistic baseline, always shadow-only by contract."""
    weights = coefficients or _BASELINE_COEFFICIENTS
    z = intercept
    values = features.model_dump()
    for name, weight in weights.items():
        value = values.get(name)
        if value is not None:
            z += weight * Decimal(str(value))
    z_float = max(-60.0, min(60.0, float(z)))
    probability = Decimal(str(1.0 / (1.0 + math.exp(-z_float))))
    payload = {
        "model_id": "gap_pullback_logistic",
        "model_version": model_version,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "probability": str(probability),
        "features": features.model_dump(mode="json"),
        "label_definition": LABEL_DEFINITION,
        "shadow_only": True,
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return BounceModelScore(
        model_version=model_version,
        observed_at=observed_at,
        probability=probability,
        features=features,
        fingerprint=fingerprint,
    )


def label_two_r_before_one_r(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    entry_time: datetime,
    entry_price: Decimal,
    risk_per_share: Decimal,
    horizon_minutes: int = 90,
) -> int | None:
    """Return 1 for +2R first, 0 for -1R first, None if neither occurs.

    Same-bar ambiguity resolves to the stop (0), preventing optimistic leakage.
    """
    if risk_per_share <= 0:
        raise ValueError("risk_per_share must be positive")
    target = entry_price + risk_per_share * Decimal("2")
    stop = entry_price - risk_per_share
    horizon = entry_time + timedelta(minutes=horizon_minutes)
    for bar in sorted(bars, key=lambda item: item.start_time):
        if bar.end_time <= entry_time:
            continue
        if bar.start_time > horizon:
            break
        stop_hit = bar.low <= stop
        target_hit = bar.high >= target
        if stop_hit:
            return 0
        if target_hit:
            return 1
    return None
