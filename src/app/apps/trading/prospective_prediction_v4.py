from __future__ import annotations

"""Prospective-gap v4 shadow-challenger contracts and deterministic research helpers.

This module deliberately does not mutate prospective-gap-v3.  It adds a separate,
versioned research path with three independent authorities:

1. forecast authority: immutable 09:29 evidence and forecast,
2. action authority: post-open confirmation/actionability,
3. execution authority: notional-aware execution economics.

Canonical historical truth is also kept separate from what the live forecaster
actually knew before its cutoff. Recovered data may repair historical analysis,
but cannot retroactively enter a frozen live forecast.
"""

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Final, Literal, Sequence, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .gapper_dataset import GapperCandidate, GapperUniverseSnapshot
from .models import AdjustmentMode, MarketBar
from .prospective_prediction_evidence import FrozenForecast
from app.apps.trading.us_equity_calendar import EASTERN as _ET


V4_PREDICTOR_VERSION: Final = "prospective-gap-v4-shadow"
V4_FEATURE_SCHEMA_VERSION = "prospective-gap-features-v4"
V4_MARKET_STATE_VERSION: Final = "premarket-market-state-v1"
V4_CALIBRATION_VERSION: Final = "prospective-gap-calibration-v1"
V4_EXTENSION_RISK_VERSION: Final = "extension-exhaustion-risk-v1"
V4_CONFIRMATION_VERSION: Final = "post-open-confirmation-v1"
V4_EXECUTION_COST_VERSION = "execution-cost-v2-round-trip"
V4_PAIRED_EVALUATION_VERSION: Final = "paired-v3-v4-v1"

EvidenceQuality = Literal["COMPLETE", "DEGRADED", "INSUFFICIENT"]
FeatureQuality = Literal["GOOD", "STALE", "RECOVERED", "MISSING", "CONFLICT", "PROXY"]
ModelState = Literal["PRODUCED", "FAILED", "NOT_APPLICABLE"]
Actionability = Literal["ACT", "WATCH", "ABSTAIN"]
RegimeTag = Literal[
    "FUNDAMENTAL_REPRICE",
    "SQUEEZE_MOMENTUM",
    "STALE_MULTI_DAY",
    "DISTRESS_SPECULATION",
    "UNEXPLAINED_TECHNICAL",
    "SUPPLY_OVERHANG",
    "LOW_LIQUIDITY",
    "HIGH_EXTENSION",
]
ConfirmationState = Literal[
    "WAIT_OPEN",
    "OBSERVE_INITIAL_STRUCTURE",
    "OBSERVE_PULLBACK",
    "CONFIRMED_LONG",
    "INVALIDATED",
    "WATCH",
    "EXPIRED",
    "SUSPENDED_DATA_QUALITY",
]
TradeDecision = Literal["LONG", "WATCH", "NO_TRADE"]


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def _clamp01(value: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), value))


class FinvizFrozenCohort(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cohort_id: str
    session_date: date
    discovery_cutoff_at: datetime
    frozen_at: datetime
    symbols: tuple[str, ...]
    discovery_source: Literal["finviz_top_gainers"] = "finviz_top_gainers"

    @field_validator("discovery_cutoff_at", "frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def frozen_before_cutoff(self):
        if self.frozen_at > self.discovery_cutoff_at:
            raise ValueError("cohort_frozen_after_discovery_cutoff")
        if not self.symbols:
            raise ValueError("cohort_requires_symbols")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("cohort_symbols_must_be_unique")
        return self

    @property
    def cohort_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


def finviz_cohort_from_universe(
    universe: GapperUniverseSnapshot,
    *,
    prediction_cutoff_at: datetime,
    limit: int = 10,
) -> FinvizFrozenCohort:
    """Bind v4 to the existing point-in-time Finviz universe archive."""

    if universe.discovery_source != "finviz":
        raise ValueError("v4_requires_finviz_universe")
    if limit < 1:
        raise ValueError("cohort_limit_must_be_positive")
    cutoff = _utc(prediction_cutoff_at)
    if universe.evaluation_time > cutoff:
        raise ValueError("finviz_universe_observed_after_prediction_cutoff")
    symbols = tuple(universe.source_candidate_symbols[:limit])
    if not symbols:
        symbols = tuple(candidate.instrument_id for candidate in universe.candidates[:limit])
    return FinvizFrozenCohort(
        cohort_id=universe.universe_id,
        session_date=universe.session_date,
        discovery_cutoff_at=cutoff,
        frozen_at=universe.evaluation_time,
        symbols=symbols,
    )


class PremarketFeature(BaseModel):
    """One market-state feature with both event truth and live-knowledge timestamps."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    value: Decimal | None = None
    unit: str | None = None
    event_at: datetime | None = None
    observed_at: datetime | None = None
    ingested_at: datetime | None = None
    recovered_at: datetime | None = None
    source: str
    freshness_seconds: int | None = Field(default=None, ge=0)
    quality: FeatureQuality
    available: bool
    available_to_live_forecaster: bool
    provenance_fingerprint: str | None = None
    ingestion_time_basis: Literal["provider_receipt", "snapshot_proxy", "unknown"] = "provider_receipt"

    @field_validator("event_at", "observed_at", "ingested_at", "recovered_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def consistency(self):
        if self.available and self.value is None:
            raise ValueError("available_feature_requires_value")
        if self.available_to_live_forecaster and not self.available:
            raise ValueError("live_feature_must_be_available")
        if self.ingested_at is not None and self.observed_at is not None and self.ingested_at < self.observed_at:
            raise ValueError("feature_ingested_before_observed")
        if self.quality == "MISSING" and self.available:
            raise ValueError("missing_feature_cannot_be_available")
        return self


class PremarketMarketStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    version: Literal["premarket-market-state-v1"] = V4_MARKET_STATE_VERSION
    cohort_id: str
    cohort_fingerprint: str
    instrument_id: str
    prediction_cutoff_at: datetime
    frozen_at: datetime
    features: tuple[PremarketFeature, ...]

    @field_validator("prediction_cutoff_at", "frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def causal_live_gate(self):
        if self.frozen_at > self.prediction_cutoff_at:
            raise ValueError("market_state_frozen_after_prediction_cutoff")
        for feature in self.features:
            if not feature.available_to_live_forecaster:
                continue
            if feature.observed_at is None or feature.ingested_at is None:
                raise ValueError(f"live_feature_missing_observation_timestamps:{feature.name}")
            if feature.observed_at > self.prediction_cutoff_at:
                raise ValueError(f"live_feature_observed_after_cutoff:{feature.name}")
            if feature.ingested_at > self.prediction_cutoff_at:
                raise ValueError(f"live_feature_ingested_after_cutoff:{feature.name}")
        return self

    @property
    def snapshot_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))

    @property
    def live_feature_fingerprint(self) -> str:
        payload = [
            feature.model_dump(mode="json")
            for feature in self.features
            if feature.available_to_live_forecaster
        ]
        return _hash(payload)

    def live_value(self, name: str) -> Decimal | None:
        for feature in self.features:
            if feature.name == name and feature.available_to_live_forecaster:
                return feature.value
        return None


def build_premarket_market_state(
    *,
    cohort: FinvizFrozenCohort,
    instrument_id: str,
    bars: Sequence[MarketBar],
    snapshot_id: str,
    prediction_cutoff_at: datetime,
    frozen_at: datetime,
    prior_close: Decimal | None = None,
    float_shares: Decimal | None = None,
    first_catalyst_at: datetime | None = None,
    prior_1d_return_pct: Decimal | None = None,
    prior_3d_return_pct: Decimal | None = None,
) -> PremarketMarketStateSnapshot:
    """Derive causal market-state features from finalized RAW one-minute premarket bars.

    Only bars whose event window and provider receipt are available by the prediction
    cutoff are eligible for live features. Later-received recovery data is excluded
    from the live vector rather than retroactively repairing the forecast.
    """

    cutoff = _utc(prediction_cutoff_at)
    frozen = _utc(frozen_at)
    start_et = datetime.combine(cohort.session_date, time(4, 0), tzinfo=_ET).astimezone(timezone.utc)
    open_et = datetime.combine(cohort.session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    if cutoff > open_et:
        raise ValueError("premarket_snapshot_cutoff_after_regular_open")
    if frozen > cutoff:
        raise ValueError("premarket_snapshot_frozen_after_cutoff")

    candidates = [
        bar
        for bar in bars
        if bar.instrument_id == instrument_id
        and bar.interval == "1m"
        and bar.is_final
        and bar.adjustment_mode == AdjustmentMode.RAW
        and start_et <= bar.start_time < open_et
        and bar.end_time <= cutoff
    ]
    if not candidates:
        raise ValueError("premarket_market_state_requires_final_raw_1m_bars")
    providers = {bar.provider for bar in candidates}
    if len(providers) != 1:
        raise ValueError("premarket_market_state_requires_single_provider")
    provider = next(iter(providers))

    by_window: dict[tuple[datetime, datetime], MarketBar] = {}
    for bar in candidates:
        key = (bar.start_time, bar.end_time)
        prior = by_window.get(key)
        if prior is None:
            by_window[key] = bar
            continue
        prior_rank = (
            prior.ingestion_revision,
            prior.received_at,
            prior.provider_sequence if prior.provider_sequence is not None else -1,
        )
        current_rank = (
            bar.ingestion_revision,
            bar.received_at,
            bar.provider_sequence if bar.provider_sequence is not None else -1,
        )
        if current_rank > prior_rank:
            by_window[key] = bar

    canonical = tuple(sorted(by_window.values(), key=lambda bar: (bar.start_time, bar.end_time)))
    live = tuple(bar for bar in canonical if bar.received_at <= cutoff)
    if not live:
        raise ValueError("premarket_market_state_has_no_live_eligible_bars")

    latest = live[-1]
    session_high = max(bar.high for bar in live)
    session_low = min(bar.low for bar in live)
    total_volume = sum((max(Decimal("0"), bar.volume) for bar in live), Decimal("0"))
    cumulative_pv = sum(
        (((bar.high + bar.low + bar.close) / Decimal("3")) * max(Decimal("0"), bar.volume) for bar in live),
        Decimal("0"),
    )
    vwap = cumulative_pv / total_volume if total_volume > 0 else None
    range_size = session_high - session_low

    live_bar_fingerprint = _hash([
        (
            bar.provider_event_id,
            bar.provider_sequence,
            bar.ingestion_revision,
            bar.start_time.isoformat(),
            bar.end_time.isoformat(),
            str(bar.open),
            str(bar.high),
            str(bar.low),
            str(bar.close),
            str(bar.volume),
            bar.received_at.isoformat(),
        )
        for bar in live
    ])

    def feature(
        name: str,
        value: Decimal | None,
        *,
        unit: str | None = None,
        source: str = provider,
        quality: FeatureQuality = "GOOD",
        event_at: datetime | None = None,
        observed_at: datetime | None = None,
        ingested_at: datetime | None = None,
        available_to_live_forecaster: bool = True,
        provenance_suffix: str = "",
    ) -> PremarketFeature:
        available = value is not None
        return PremarketFeature(
            name=name,
            value=value,
            unit=unit,
            event_at=event_at or latest.end_time,
            observed_at=observed_at or latest.end_time,
            ingested_at=ingested_at or latest.received_at,
            source=source,
            freshness_seconds=max(0, int((cutoff - latest.end_time).total_seconds())) if available else None,
            quality=quality if available else "MISSING",
            available=available,
            available_to_live_forecaster=available and available_to_live_forecaster,
            provenance_fingerprint=_hash((live_bar_fingerprint, name, provenance_suffix)),
        )

    gap_pct = (
        (latest.close - prior_close) / prior_close * Decimal("100")
        if prior_close is not None and prior_close > 0
        else None
    )
    vwap_distance = (
        (latest.close - vwap) / vwap * Decimal("100")
        if vwap is not None and vwap > 0
        else None
    )
    distance_from_low = (
        (latest.close - session_low) / session_low * Decimal("100")
        if session_low > 0
        else None
    )
    range_position = (
        (latest.close - session_low) / range_size
        if range_size > 0
        else Decimal("0.5")
    )
    turnover = (
        total_volume / float_shares
        if float_shares is not None and float_shares > 0
        else None
    )

    catalyst_move: Decimal | None = None
    if first_catalyst_at is not None:
        catalyst_at = _utc(first_catalyst_at)
        post_catalyst = [bar for bar in live if bar.end_time >= catalyst_at]
        if post_catalyst and post_catalyst[0].open > 0:
            catalyst_move = (
                (latest.close - post_catalyst[0].open)
                / post_catalyst[0].open
                * Decimal("100")
            )

    late_start = cutoff - timedelta(minutes=15)
    late = [bar for bar in live if bar.end_time > late_start]
    late_volume = sum((max(Decimal("0"), bar.volume) for bar in late), Decimal("0"))
    late_volume_share = late_volume / total_volume if total_volume > 0 else None
    late_acceleration: Decimal | None = None
    if len(late) >= 3 and late[0].open > 0:
        half = max(1, len(late) // 2)
        first_half = late[:half]
        second_half = late[half:]
        if second_half and first_half[0].open > 0 and second_half[0].open > 0:
            first_return = (first_half[-1].close - first_half[0].open) / first_half[0].open
            second_return = (second_half[-1].close - second_half[0].open) / second_half[0].open
            late_acceleration = _clamp01((second_return - first_return + Decimal("0.10")) / Decimal("0.20")) * Decimal("2") - Decimal("1")

    recovered_bars = [bar for bar in canonical if bar.received_at > cutoff]
    recovered_count = Decimal(len(recovered_bars))
    recovery_observed_at = max((bar.end_time for bar in recovered_bars), default=latest.end_time)
    recovery_ingested_at = max((bar.received_at for bar in recovered_bars), default=latest.received_at)
    recovery_feature = PremarketFeature(
        name="recovered_after_cutoff_bar_count",
        value=recovered_count,
        unit="count",
        event_at=recovery_observed_at,
        observed_at=recovery_observed_at,
        ingested_at=recovery_ingested_at,
        recovered_at=recovery_ingested_at if recovered_bars else None,
        source=provider,
        freshness_seconds=None,
        quality="RECOVERED" if recovered_bars else "GOOD",
        available=True,
        available_to_live_forecaster=False,
        provenance_fingerprint=_hash((live_bar_fingerprint, "recovered_after_cutoff", str(recovered_count))),
    )

    features = (
        feature("gap_from_prior_close_pct", gap_pct, unit="pct"),
        feature("premarket_move_since_first_catalyst_pct", catalyst_move, unit="pct"),
        feature("distance_from_premarket_vwap_pct", vwap_distance, unit="pct"),
        feature("distance_from_premarket_low_pct", distance_from_low, unit="pct"),
        feature("position_in_premarket_range", range_position, unit="ratio"),
        feature(
            "prior_1d_return_pct",
            prior_1d_return_pct,
            unit="pct",
            source="historical_context",
            event_at=latest.end_time,
            observed_at=latest.end_time,
            ingested_at=latest.received_at,
        ),
        feature(
            "prior_3d_return_pct",
            prior_3d_return_pct,
            unit="pct",
            source="historical_context",
            event_at=latest.end_time,
            observed_at=latest.end_time,
            ingested_at=latest.received_at,
        ),
        feature("float_turnover", turnover, unit="x"),
        feature("late_premarket_acceleration", late_acceleration, unit="normalized"),
        feature("late_premarket_volume_share", late_volume_share, unit="ratio"),
        recovery_feature,
    )
    return PremarketMarketStateSnapshot(
        snapshot_id=snapshot_id,
        cohort_id=cohort.cohort_id,
        cohort_fingerprint=cohort.cohort_fingerprint,
        instrument_id=instrument_id,
        prediction_cutoff_at=cutoff,
        frozen_at=frozen,
        features=features,
    )


class PredictionEvidenceQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quality: EvidenceQuality
    critical_features: tuple[str, ...]
    missing_critical_features: tuple[str, ...] = ()
    degraded_features: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class V4ForecastAttempt(BaseModel):
    """Records model availability independently from evidence quality."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    session_date: date
    attempted_at: datetime
    model_state: ModelState
    evidence_quality: EvidenceQuality
    forecast_fingerprint: str | None = None
    failure_reason: str | None = None

    @field_validator("attempted_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def consistency(self):
        if self.model_state == "PRODUCED" and self.forecast_fingerprint is None:
            raise ValueError("produced_forecast_attempt_requires_fingerprint")
        if self.model_state == "FAILED" and not self.failure_reason:
            raise ValueError("failed_forecast_attempt_requires_reason")
        if self.model_state == "NOT_APPLICABLE" and self.evidence_quality != "INSUFFICIENT":
            raise ValueError("not_applicable_requires_insufficient_evidence")
        return self


def summarize_evidence_quality(
    snapshot: PremarketMarketStateSnapshot,
    *,
    critical_features: Sequence[str],
    important_features: Sequence[str] = (),
) -> PredictionEvidenceQuality:
    """Summarize quality without treating every missing enrichment as fatal.

    Missing critical features make the model undefined and therefore INSUFFICIENT.
    Missing/degraded important enrichments produce DEGRADED while the forecast
    remains scoreable.
    """

    by_name = {feature.name: feature for feature in snapshot.features}
    missing: list[str] = []
    degraded: list[str] = []
    for name in critical_features:
        feature = by_name.get(name)
        if feature is None or not feature.available_to_live_forecaster or feature.value is None:
            missing.append(name)
            continue
        if feature.quality != "GOOD":
            degraded.append(name)

    for name in important_features:
        feature = by_name.get(name)
        if feature is None or not feature.available_to_live_forecaster or feature.value is None:
            degraded.append(name)
            continue
        if feature.quality != "GOOD":
            degraded.append(name)

    degraded = list(dict.fromkeys(degraded))
    if missing:
        quality: EvidenceQuality = "INSUFFICIENT"
    elif degraded:
        quality = "DEGRADED"
    else:
        quality = "COMPLETE"
    reasons = tuple(
        [f"MISSING_CRITICAL:{name}" for name in missing]
        + [f"DEGRADED_OR_MISSING_IMPORTANT:{name}" for name in degraded]
    )
    return PredictionEvidenceQuality(
        quality=quality,
        critical_features=tuple(critical_features),
        missing_critical_features=tuple(missing),
        degraded_features=tuple(degraded),
        reasons=reasons,
    )


def market_state_from_candidate(
    *,
    cohort: FinvizFrozenCohort,
    candidate: GapperCandidate,
    snapshot_id: str,
    prediction_cutoff_at: datetime,
    frozen_at: datetime,
) -> PremarketMarketStateSnapshot:
    """Create a degraded-capable live snapshot from the existing frozen candidate.

    This is the resilient baseline when richer one-minute market-state enrichment
    is unavailable. It never fabricates VWAP/range/acceleration fields.
    """

    cutoff = _utc(prediction_cutoff_at)
    frozen = _utc(frozen_at)
    if candidate.observed_at is None:
        raise ValueError("candidate_market_state_requires_observed_at")
    observed = _utc(candidate.observed_at)
    if observed > cutoff:
        raise ValueError("candidate_observed_after_prediction_cutoff")
    if frozen > cutoff:
        raise ValueError("candidate_market_state_frozen_after_cutoff")

    candidate_symbol = candidate.instrument_id.split(":")[-1]
    if candidate_symbol not in cohort.symbols and candidate.instrument_id not in cohort.symbols:
        raise ValueError("candidate_not_in_frozen_cohort")

    def candidate_feature(
        name: str,
        value: Decimal | None,
        *,
        unit: str,
        evidence_key: str | None = None,
    ) -> PremarketFeature:
        evidence_time = candidate.evidence_observed_at.get(evidence_key or name, observed)
        evidence_time = _utc(evidence_time)
        live = evidence_time <= cutoff
        if value is None:
            quality: FeatureQuality = "MISSING"
        elif not live:
            quality = "RECOVERED"
        else:
            quality = "PROXY"
        if candidate.data_quality_flags and value is not None and live:
            quality = "CONFLICT"
        return PremarketFeature(
            name=name,
            value=value,
            unit=unit,
            event_at=evidence_time,
            observed_at=evidence_time,
            ingested_at=evidence_time,
            source="gapper_candidate",
            freshness_seconds=max(0, int((cutoff - evidence_time).total_seconds())) if value is not None and live else None,
            quality=quality,
            available=value is not None,
            available_to_live_forecaster=value is not None and live,
            provenance_fingerprint=_hash((
                candidate.instrument_id,
                name,
                str(value),
                evidence_time.isoformat(),
                candidate.data_quality_flags,
            )),
            ingestion_time_basis="snapshot_proxy",
        )

    turnover = (
        candidate.premarket_volume / candidate.float_shares
        if candidate.float_shares is not None and candidate.float_shares > 0
        else None
    )
    features = (
        candidate_feature("gap_from_prior_close_pct", candidate.gap_pct, unit="pct", evidence_key="finviz_top_gainers"),
        candidate_feature("premarket_price", candidate.premarket_price, unit="usd"),
        candidate_feature("premarket_volume", candidate.premarket_volume, unit="shares"),
        candidate_feature("float_turnover", turnover, unit="x"),
        candidate_feature("tod_rvol", candidate.tod_rvol, unit="x"),
        candidate_feature("spread_bps", candidate.spread_bps, unit="bps"),
    )
    return PremarketMarketStateSnapshot(
        snapshot_id=snapshot_id,
        cohort_id=cohort.cohort_id,
        cohort_fingerprint=cohort.cohort_fingerprint,
        instrument_id=candidate.instrument_id,
        prediction_cutoff_at=cutoff,
        frozen_at=frozen,
        features=features,
    )


def extension_components_from_market_state(
    snapshot: PremarketMarketStateSnapshot,
) -> ExtensionComponents:
    return ExtensionComponents(
        gap_from_prior_close_pct=snapshot.live_value("gap_from_prior_close_pct"),
        premarket_move_since_first_catalyst_pct=snapshot.live_value("premarket_move_since_first_catalyst_pct"),
        distance_from_premarket_vwap_pct=snapshot.live_value("distance_from_premarket_vwap_pct"),
        distance_from_premarket_low_pct=snapshot.live_value("distance_from_premarket_low_pct"),
        position_in_premarket_range=snapshot.live_value("position_in_premarket_range"),
        prior_1d_return_pct=snapshot.live_value("prior_1d_return_pct"),
        prior_3d_return_pct=snapshot.live_value("prior_3d_return_pct"),
        float_turnover=snapshot.live_value("float_turnover"),
        late_premarket_acceleration=snapshot.live_value("late_premarket_acceleration"),
        late_premarket_volume_share=snapshot.live_value("late_premarket_volume_share"),
    )


class CatalystDecomposition(BaseModel):
    """Derived catalyst inference. Raw source evidence remains authoritative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strength: Decimal = Field(ge=0, le=1)
    finality: Decimal = Field(ge=0, le=1)
    freshness: Decimal = Field(ge=0, le=1)
    surprise: Decimal = Field(ge=0, le=1)
    economic_materiality: Decimal = Field(ge=0, le=1)
    source_evidence_ids: tuple[str, ...] = ()
    inference_version: str = "catalyst-decomposition-v1"


class ExtensionComponents(BaseModel):
    """Observed inputs used to derive extension/exhaustion risk."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gap_from_prior_close_pct: Decimal | None = None
    premarket_move_since_first_catalyst_pct: Decimal | None = None
    distance_from_premarket_vwap_pct: Decimal | None = None
    distance_from_premarket_low_pct: Decimal | None = None
    position_in_premarket_range: Decimal | None = Field(default=None, ge=0, le=1)
    prior_1d_return_pct: Decimal | None = None
    prior_3d_return_pct: Decimal | None = None
    float_turnover: Decimal | None = Field(default=None, ge=0)
    late_premarket_acceleration: Decimal | None = None
    late_premarket_volume_share: Decimal | None = Field(default=None, ge=0, le=1)


class ExtensionExhaustionRisk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["extension-exhaustion-risk-v1"] = V4_EXTENSION_RISK_VERSION
    score: Decimal = Field(ge=0, le=1)
    used_components: tuple[str, ...]
    missing_components: tuple[str, ...]


def derive_extension_exhaustion_risk(components: ExtensionComponents) -> ExtensionExhaustionRisk:
    """Transparent v1 risk head; missing observations are not silently neutralized."""

    weighted: list[tuple[str, Decimal, Decimal]] = []

    def add(name: str, raw: Decimal | None, risk: Decimal, weight: str) -> None:
        if raw is not None:
            weighted.append((name, _clamp01(risk), Decimal(weight)))

    gap = components.gap_from_prior_close_pct
    add("gap_from_prior_close_pct", gap, (gap - Decimal("20")) / Decimal("180") if gap is not None else Decimal(0), "0.24")

    move = components.premarket_move_since_first_catalyst_pct
    add(
        "premarket_move_since_first_catalyst_pct",
        move,
        (move - Decimal("10")) / Decimal("190") if move is not None else Decimal(0),
        "0.14",
    )

    vwap = components.distance_from_premarket_vwap_pct
    add(
        "distance_from_premarket_vwap_pct",
        vwap,
        max(Decimal("0"), vwap) / Decimal("30") if vwap is not None else Decimal(0),
        "0.14",
    )

    low = components.distance_from_premarket_low_pct
    add(
        "distance_from_premarket_low_pct",
        low,
        max(Decimal("0"), low) / Decimal("200") if low is not None else Decimal(0),
        "0.08",
    )

    pos = components.position_in_premarket_range
    add("position_in_premarket_range", pos, pos if pos is not None else Decimal(0), "0.08")

    prior1 = components.prior_1d_return_pct
    add(
        "prior_1d_return_pct",
        prior1,
        max(Decimal("0"), prior1) / Decimal("200") if prior1 is not None else Decimal(0),
        "0.10",
    )

    prior3 = components.prior_3d_return_pct
    add(
        "prior_3d_return_pct",
        prior3,
        max(Decimal("0"), prior3) / Decimal("400") if prior3 is not None else Decimal(0),
        "0.06",
    )

    turnover = components.float_turnover
    add("float_turnover", turnover, turnover / Decimal("3") if turnover is not None else Decimal(0), "0.08")

    accel = components.late_premarket_acceleration
    add(
        "late_premarket_acceleration",
        accel,
        (Decimal("1") - _clamp01((accel + Decimal("1")) / Decimal("2"))) if accel is not None else Decimal(0),
        "0.04",
    )

    volume_share = components.late_premarket_volume_share
    add(
        "late_premarket_volume_share",
        volume_share,
        Decimal("1") - volume_share if volume_share is not None else Decimal(0),
        "0.04",
    )

    all_names = tuple(ExtensionComponents.model_fields.keys())
    used = tuple(name for name, _, _ in weighted)
    missing = tuple(name for name in all_names if getattr(components, name) is None)
    if not weighted:
        raise ValueError("extension_risk_requires_observed_components")
    total_weight = sum((weight for _, _, weight in weighted), Decimal("0"))
    score = sum((risk * weight for _, risk, weight in weighted), Decimal("0")) / total_weight
    return ExtensionExhaustionRisk(
        score=_clamp01(score),
        used_components=used,
        missing_components=missing,
    )


class MechanismRiskScores(BaseModel):
    """Diagnostic mechanism heads; these are not calibrated probabilities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    continuation_score: Decimal = Field(ge=0, le=1)
    opening_exhaustion_score: Decimal = Field(ge=0, le=1)
    squeeze_tail_score: Decimal = Field(ge=0, le=1)
    fade_risk_score: Decimal = Field(ge=0, le=1)
    diagnostic_only: Literal[True] = True


class V4ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_version: Literal["prospective-gap-v4-shadow"] = V4_PREDICTOR_VERSION
    intercept: Decimal = Decimal("0.35")
    catalyst_strength_weight: Decimal = Decimal("0.08")
    catalyst_finality_weight: Decimal = Decimal("0.06")
    catalyst_freshness_weight: Decimal = Decimal("0.05")
    catalyst_surprise_weight: Decimal = Decimal("0.04")
    catalyst_materiality_weight: Decimal = Decimal("0.08")
    continuation_weight: Decimal = Decimal("0.18")
    squeeze_tail_weight: Decimal = Decimal("0.04")
    extension_exhaustion_weight: Decimal = Decimal("-0.22")
    fade_risk_weight: Decimal = Decimal("-0.12")

    @property
    def implementation_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


DEFAULT_V4_MODEL_SPEC = V4ModelSpec()


def score_v4_raw_probability(
    *,
    catalyst: CatalystDecomposition,
    extension_risk: ExtensionExhaustionRisk,
    mechanisms: MechanismRiskScores,
    spec: V4ModelSpec = DEFAULT_V4_MODEL_SPEC,
) -> Decimal:
    """Initial transparent shadow score. It is intentionally not trained on Sep 15-17."""

    probability = (
        spec.intercept
        + catalyst.strength * spec.catalyst_strength_weight
        + catalyst.finality * spec.catalyst_finality_weight
        + catalyst.freshness * spec.catalyst_freshness_weight
        + catalyst.surprise * spec.catalyst_surprise_weight
        + catalyst.economic_materiality * spec.catalyst_materiality_weight
        + mechanisms.continuation_score * spec.continuation_weight
        + mechanisms.squeeze_tail_score * spec.squeeze_tail_weight
        + extension_risk.score * spec.extension_exhaustion_weight
        + mechanisms.fade_risk_score * spec.fade_risk_weight
    )
    return min(Decimal("0.95"), max(Decimal("0.05"), probability))


class CalibratorArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calibrator_id: str
    calibrator_version: Literal["prospective-gap-calibration-v1"] = V4_CALIBRATION_VERSION
    method: Literal["identity", "prior_blend_v1"]
    training_cutoff_at: datetime
    training_population_fingerprint: str
    training_dataset_fingerprint: str
    sample_count: int = Field(ge=0)
    population_definition: str
    feature_schema_version: str = V4_FEATURE_SCHEMA_VERSION
    target_label_version: str = "close_above_open_v1"
    parent_calibrator_id: str | None = None
    created_at: datetime
    code_version: str
    prior_probability: Decimal | None = Field(default=None, ge=0, le=1)
    prior_weight: Decimal | None = Field(default=None, ge=0, le=1)

    @field_validator("training_cutoff_at", "created_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def parameters(self):
        if self.method == "prior_blend_v1":
            if self.prior_probability is None or self.prior_weight is None:
                raise ValueError("prior_blend_requires_prior_probability_and_weight")
        return self

    @property
    def calibrator_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


def apply_calibrator(
    raw_probability: Decimal,
    calibrator: CalibratorArtifact,
    *,
    forecast_session_date: date,
) -> Decimal:
    session_start = datetime.combine(forecast_session_date, time(9, 30), tzinfo=_ET).astimezone(timezone.utc)
    if calibrator.training_cutoff_at >= session_start:
        raise ValueError("calibrator_training_cutoff_must_precede_forecast_session")
    raw_probability = _clamp01(raw_probability)
    if calibrator.method == "identity":
        return raw_probability
    assert calibrator.prior_probability is not None
    assert calibrator.prior_weight is not None
    calibrated = (
        raw_probability * (Decimal("1") - calibrator.prior_weight)
        + calibrator.prior_probability * calibrator.prior_weight
    )
    return _clamp01(calibrated)


class FrozenForecastV4(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    session_date: date
    cohort_id: str
    cohort_fingerprint: str
    evidence_snapshot_id: str
    market_state_snapshot_id: str
    predictor_version: Literal["prospective-gap-v4-shadow"] = V4_PREDICTOR_VERSION
    feature_schema_version: str = V4_FEATURE_SCHEMA_VERSION
    model_spec_fingerprint: str
    feature_vector_fingerprint: str
    frozen_at: datetime
    model_state: ModelState = "PRODUCED"
    raw_p_close_above_open: Decimal = Field(ge=0, le=1)
    calibrated_p_close_above_open: Decimal = Field(ge=0, le=1)
    return_q10: Decimal | None = None
    return_q50: Decimal | None = None
    return_q90: Decimal | None = None
    p_return_gt_2pct: Decimal | None = Field(default=None, ge=0, le=1)
    p_return_lt_minus_5pct: Decimal | None = Field(default=None, ge=0, le=1)
    mae_bucket: Literal["low", "moderate", "high"] | None = None
    mfe_bucket: Literal["low", "moderate", "high"] | None = None
    uncertainty: Literal["low", "moderate", "high"] = "high"
    evidence_quality: PredictionEvidenceQuality
    mechanism_scores: MechanismRiskScores
    regime_tags: tuple[RegimeTag, ...] = ()
    regime_primary: RegimeTag | None = None
    regime_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    calibrator_id: str
    calibrator_fingerprint: str

    @field_validator("frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def ordering(self):
        if self.return_q10 is not None and self.return_q50 is not None and self.return_q90 is not None:
            if not self.return_q10 <= self.return_q50 <= self.return_q90:
                raise ValueError("v4_return_quantiles_must_be_monotonic")
        if self.regime_primary is not None and self.regime_primary not in self.regime_tags:
            raise ValueError("primary_regime_must_be_in_regime_tags")
        return self

    @property
    def immutable_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


def freeze_v4_forecast(
    *,
    instrument_id: str,
    session_date: date,
    cohort: FinvizFrozenCohort,
    evidence_snapshot_id: str,
    market_state: PremarketMarketStateSnapshot,
    evidence_quality: PredictionEvidenceQuality,
    catalyst: CatalystDecomposition,
    extension_risk: ExtensionExhaustionRisk,
    mechanisms: MechanismRiskScores,
    calibrator: CalibratorArtifact,
    feature_vector_fingerprint: str,
    frozen_at: datetime,
    regime_tags: Sequence[RegimeTag] = (),
    regime_primary: RegimeTag | None = None,
    regime_confidence: Decimal | None = None,
    uncertainty: Literal["low", "moderate", "high"] = "high",
    spec: V4ModelSpec = DEFAULT_V4_MODEL_SPEC,
    return_q10: Decimal | None = None,
    return_q50: Decimal | None = None,
    return_q90: Decimal | None = None,
    p_return_gt_2pct: Decimal | None = None,
    p_return_lt_minus_5pct: Decimal | None = None,
    mae_bucket: Literal["low", "moderate", "high"] | None = None,
    mfe_bucket: Literal["low", "moderate", "high"] | None = None,
) -> FrozenForecastV4:
    frozen_at = _utc(frozen_at)
    if frozen_at > market_state.prediction_cutoff_at:
        raise ValueError("v4_forecast_frozen_after_prediction_cutoff")
    if cohort.cohort_id != market_state.cohort_id or cohort.cohort_fingerprint != market_state.cohort_fingerprint:
        raise ValueError("v4_market_state_cohort_mismatch")
    if instrument_id != market_state.instrument_id:
        raise ValueError("v4_market_state_instrument_mismatch")
    if evidence_quality.quality == "INSUFFICIENT":
        raise ValueError("insufficient_evidence_cannot_produce_v4_forecast")

    raw = score_v4_raw_probability(
        catalyst=catalyst,
        extension_risk=extension_risk,
        mechanisms=mechanisms,
        spec=spec,
    )
    calibrated = apply_calibrator(raw, calibrator, forecast_session_date=session_date)
    effective_uncertainty: Literal["low", "moderate", "high"] = uncertainty
    if evidence_quality.quality == "DEGRADED" and uncertainty == "low":
        effective_uncertainty = "moderate"
    return FrozenForecastV4(
        instrument_id=instrument_id,
        session_date=session_date,
        cohort_id=cohort.cohort_id,
        cohort_fingerprint=cohort.cohort_fingerprint,
        evidence_snapshot_id=evidence_snapshot_id,
        market_state_snapshot_id=market_state.snapshot_id,
        model_spec_fingerprint=spec.implementation_fingerprint,
        feature_vector_fingerprint=feature_vector_fingerprint,
        frozen_at=frozen_at,
        raw_p_close_above_open=raw,
        calibrated_p_close_above_open=calibrated,
        return_q10=return_q10,
        return_q50=return_q50,
        return_q90=return_q90,
        p_return_gt_2pct=p_return_gt_2pct,
        p_return_lt_minus_5pct=p_return_lt_minus_5pct,
        mae_bucket=mae_bucket,
        mfe_bucket=mfe_bucket,
        uncertainty=effective_uncertainty,
        evidence_quality=evidence_quality,
        mechanism_scores=mechanisms,
        regime_tags=tuple(regime_tags),
        regime_primary=regime_primary,
        regime_confidence=regime_confidence,
        calibrator_id=calibrator.calibrator_id,
        calibrator_fingerprint=calibrator.calibrator_fingerprint,
    )


class ActionabilityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    decision_at: datetime
    actionability: Actionability
    reasons: tuple[str, ...] = ()

    @field_validator("decision_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "WAIT_OPEN": {"OBSERVE_INITIAL_STRUCTURE", "SUSPENDED_DATA_QUALITY", "EXPIRED"},
    "OBSERVE_INITIAL_STRUCTURE": {"OBSERVE_PULLBACK", "WATCH", "INVALIDATED", "SUSPENDED_DATA_QUALITY", "EXPIRED"},
    "OBSERVE_PULLBACK": {"CONFIRMED_LONG", "WATCH", "INVALIDATED", "SUSPENDED_DATA_QUALITY", "EXPIRED"},
    "WATCH": {"OBSERVE_INITIAL_STRUCTURE", "OBSERVE_PULLBACK", "CONFIRMED_LONG", "INVALIDATED", "SUSPENDED_DATA_QUALITY", "EXPIRED"},
    "SUSPENDED_DATA_QUALITY": {"OBSERVE_INITIAL_STRUCTURE", "OBSERVE_PULLBACK", "WATCH", "EXPIRED"},
    "CONFIRMED_LONG": set(),
    "INVALIDATED": set(),
    "EXPIRED": set(),
}


class ConfirmationTransitionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["post-open-confirmation-v1"] = V4_CONFIRMATION_VERSION
    instrument_id: str
    transition_at: datetime
    previous_state: ConfirmationState
    new_state: ConfirmationState
    trigger: str
    bar_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    latest_finalized_bar_at: datetime | None = None
    reasons: tuple[str, ...] = ()

    @field_validator("transition_at", "latest_finalized_bar_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc(value)


def transition_confirmation(
    *,
    instrument_id: str,
    previous_state: ConfirmationState,
    new_state: ConfirmationState,
    transition_at: datetime,
    trigger: str,
    bar_ids: Sequence[str] = (),
    evidence_ids: Sequence[str] = (),
    latest_finalized_bar_at: datetime | None = None,
    reasons: Sequence[str] = (),
) -> ConfirmationTransitionReceipt:
    if new_state not in _ALLOWED_TRANSITIONS[previous_state]:
        raise ValueError(f"invalid_confirmation_transition:{previous_state}->{new_state}")
    transition_at = _utc(transition_at)
    if latest_finalized_bar_at is not None:
        latest_finalized_bar_at = _utc(latest_finalized_bar_at)
        if latest_finalized_bar_at > transition_at:
            raise ValueError("finalized_bar_cannot_be_after_transition")
    if new_state == "CONFIRMED_LONG":
        if not bar_ids:
            raise ValueError("confirmed_long_requires_finalized_bar_evidence")
        if latest_finalized_bar_at is None:
            raise ValueError("confirmed_long_requires_latest_finalized_bar_timestamp")
    return ConfirmationTransitionReceipt(
        instrument_id=instrument_id,
        transition_at=transition_at,
        previous_state=previous_state,
        new_state=new_state,
        trigger=trigger,
        bar_ids=tuple(bar_ids),
        evidence_ids=tuple(evidence_ids),
        latest_finalized_bar_at=latest_finalized_bar_at,
        reasons=tuple(reasons),
    )


def actionability_from_confirmation(receipt: ConfirmationTransitionReceipt) -> ActionabilityDecision:
    if receipt.new_state == "CONFIRMED_LONG":
        action: Actionability = "ACT"
    elif receipt.new_state in {"WATCH", "OBSERVE_INITIAL_STRUCTURE", "OBSERVE_PULLBACK", "SUSPENDED_DATA_QUALITY"}:
        action = "WATCH"
    else:
        action = "ABSTAIN"
    return ActionabilityDecision(
        instrument_id=receipt.instrument_id,
        decision_at=receipt.transition_at,
        actionability=action,
        reasons=receipt.reasons,
    )


class PostOpenConfirmationConfig(BaseModel):
    """Deterministic post-open setup evaluator.

    This does not change the frozen 09:29 forecast. It only decides whether
    finalized post-open structure is actionable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_finalized_bars: int = Field(default=3, ge=3, le=30)
    minimum_volume_ratio: Decimal = Field(default=Decimal("0.80"), ge=0)
    require_vwap_reclaim: bool = True
    require_break_of_pullback_high: bool = True


def _confirmation_bar_id(bar: MarketBar) -> str:
    return str(
        bar.provider_event_id
        or f"{bar.instrument_id}:{bar.interval}:{bar.start_time.astimezone(timezone.utc).isoformat()}"
    )


def _confirmation_vwap(bars: Sequence[MarketBar]) -> Decimal | None:
    volume = sum((max(Decimal("0"), bar.volume) for bar in bars), Decimal("0"))
    if volume <= 0:
        return None
    notional = sum(
        (
            ((bar.high + bar.low + bar.close) / Decimal("3"))
            * max(Decimal("0"), bar.volume)
            for bar in bars
        ),
        Decimal("0"),
    )
    return notional / volume


def evaluate_post_open_confirmation(
    *,
    instrument_id: str,
    previous_state: ConfirmationState,
    bars: Sequence[MarketBar],
    transition_at: datetime,
    data_quality_ok: bool,
    data_quality_reasons: Sequence[str] = (),
    config: PostOpenConfirmationConfig | None = None,
) -> ConfirmationTransitionReceipt | None:
    """Evaluate one causal confirmation transition from finalized regular bars.

    Returning None means stay in the current state; it is not a hidden
    transition. Historical gaps only suspend this evaluator when they invalidate
    the features needed now.
    """

    if previous_state in {"CONFIRMED_LONG", "INVALIDATED", "EXPIRED"}:
        return None
    cfg = config or PostOpenConfirmationConfig()
    transition_at = _utc(transition_at)

    if not data_quality_ok:
        if previous_state == "SUSPENDED_DATA_QUALITY":
            return None
        return transition_confirmation(
            instrument_id=instrument_id,
            previous_state=previous_state,
            new_state="SUSPENDED_DATA_QUALITY",
            transition_at=transition_at,
            trigger="required current confirmation features unavailable",
            reasons=tuple(data_quality_reasons) or ("CURRENT_CONFIRMATION_DATA_INVALID",),
        )

    regular = sorted(
        (
            bar
            for bar in bars
            if bar.instrument_id == instrument_id
            and bar.is_final
            and bar.session == "regular"
            and bar.end_time <= transition_at
        ),
        key=lambda bar: (bar.end_time, bar.start_time),
    )
    if not regular:
        return None

    latest = regular[-1]
    latest_id = _confirmation_bar_id(latest)
    if previous_state == "WAIT_OPEN":
        return transition_confirmation(
            instrument_id=instrument_id,
            previous_state=previous_state,
            new_state="OBSERVE_INITIAL_STRUCTURE",
            transition_at=transition_at,
            trigger="first finalized regular-session bar",
            bar_ids=(latest_id,),
            latest_finalized_bar_at=latest.end_time,
        )

    if previous_state == "SUSPENDED_DATA_QUALITY":
        resume = "OBSERVE_PULLBACK" if len(regular) >= 2 else "OBSERVE_INITIAL_STRUCTURE"
        return transition_confirmation(
            instrument_id=instrument_id,
            previous_state=previous_state,
            new_state=cast(Any, resume),
            transition_at=transition_at,
            trigger="current confirmation features requalified",
            bar_ids=(latest_id,),
            latest_finalized_bar_at=latest.end_time,
            reasons=("DATA_QUALITY_RECOVERED",),
        )

    opening_low = regular[0].low
    if latest.close < opening_low and latest.low < opening_low:
        if "INVALIDATED" in _ALLOWED_TRANSITIONS[previous_state]:
            return transition_confirmation(
                instrument_id=instrument_id,
                previous_state=previous_state,
                new_state="INVALIDATED",
                transition_at=transition_at,
                trigger="opening structure failed",
                bar_ids=(latest_id,),
                latest_finalized_bar_at=latest.end_time,
                reasons=("CLOSE_BELOW_OPENING_STRUCTURE_LOW",),
            )

    if previous_state == "OBSERVE_INITIAL_STRUCTURE":
        if len(regular) < 2:
            return None
        return transition_confirmation(
            instrument_id=instrument_id,
            previous_state=previous_state,
            new_state="OBSERVE_PULLBACK",
            transition_at=transition_at,
            trigger="initial structure finalized; observe pullback",
            bar_ids=tuple(_confirmation_bar_id(bar) for bar in regular[-2:]),
            latest_finalized_bar_at=latest.end_time,
        )

    if previous_state not in {"OBSERVE_PULLBACK", "WATCH"}:
        return None
    if len(regular) < cfg.minimum_finalized_bars:
        return None

    pullback = regular[-2]
    older = regular[:-2]
    higher_low = bool(older) and pullback.low > min(bar.low for bar in older)
    vwap = _confirmation_vwap(regular)
    vwap_reclaimed = vwap is not None and latest.close >= vwap
    broke_pullback_high = latest.close > pullback.high
    prior_volume = regular[:-1][-5:]
    average_prior_volume = (
        sum((max(Decimal("0"), bar.volume) for bar in prior_volume), Decimal("0"))
        / Decimal(len(prior_volume))
        if prior_volume
        else Decimal("0")
    )
    volume_ratio = (
        max(Decimal("0"), latest.volume) / average_prior_volume
        if average_prior_volume > 0
        else Decimal("0")
    )
    volume_ok = volume_ratio >= cfg.minimum_volume_ratio

    required = (
        higher_low
        and (vwap_reclaimed or not cfg.require_vwap_reclaim)
        and (broke_pullback_high or not cfg.require_break_of_pullback_high)
        and volume_ok
    )
    if not required:
        return None

    reasons = (
        "HIGHER_LOW_CONFIRMED",
        "VWAP_RECLAIM_CONFIRMED" if vwap_reclaimed else "VWAP_RECLAIM_NOT_REQUIRED",
        "PULLBACK_HIGH_BROKEN" if broke_pullback_high else "PULLBACK_BREAK_NOT_REQUIRED",
        f"VOLUME_RATIO={volume_ratio}",
    )
    return transition_confirmation(
        instrument_id=instrument_id,
        previous_state=previous_state,
        new_state="CONFIRMED_LONG",
        transition_at=transition_at,
        trigger="deterministic finalized-bar confirmation",
        bar_ids=tuple(_confirmation_bar_id(bar) for bar in regular[-3:]),
        latest_finalized_bar_at=latest.end_time,
        reasons=reasons,
    )


class GrossReturnDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    q10: Decimal
    q50: Decimal
    q90: Decimal
    expected_return: Decimal | None = None
    expected_shortfall_10pct: Decimal | None = None
    p_return_gt_2pct: Decimal | None = Field(default=None, ge=0, le=1)
    p_return_lt_minus_5pct: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self):
        if not self.q10 <= self.q50 <= self.q90:
            raise ValueError("gross_return_quantiles_must_be_monotonic")
        return self


class ExecutionCostInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    decision_at: datetime
    notional: Decimal = Field(gt=0)
    reference_price: Decimal = Field(gt=0)
    observed_bid: Decimal = Field(gt=0)
    observed_ask: Decimal = Field(gt=0)
    estimated_slippage_bps: Decimal = Field(ge=0)
    estimated_impact_bps: Decimal = Field(ge=0)
    expected_exit_spread_bps: Decimal | None = Field(default=None, ge=0)
    expected_exit_slippage_bps: Decimal = Field(default=Decimal("0"), ge=0)
    expected_exit_impact_bps: Decimal = Field(default=Decimal("0"), ge=0)
    estimated_round_trip_commission_bps: Decimal = Field(default=Decimal("0"), ge=0)
    cost_model_version: str = V4_EXECUTION_COST_VERSION

    @field_validator("decision_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def valid_market(self):
        if self.observed_ask < self.observed_bid:
            raise ValueError("ask_cannot_be_below_bid")
        return self


class NetReturnDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gross: GrossReturnDistribution
    observed_spread_bps: Decimal
    entry_spread_cost_bps: Decimal
    expected_exit_spread_cost_bps: Decimal
    entry_slippage_bps: Decimal
    expected_exit_slippage_bps: Decimal
    entry_impact_bps: Decimal
    expected_exit_impact_bps: Decimal
    commission_bps: Decimal
    total_cost_bps: Decimal
    q10: Decimal
    q50: Decimal
    q90: Decimal
    expected_return: Decimal | None = None
    expected_shortfall_10pct: Decimal | None = None

    @property
    def spread_bps(self) -> Decimal:
        """Compatibility alias for the observed decision-time quoted spread."""
        return self.observed_spread_bps


def apply_execution_costs(
    gross: GrossReturnDistribution,
    cost: ExecutionCostInput,
) -> NetReturnDistribution:
    """Apply explicit round-trip execution costs to trade-time return forecasts.

    Gross returns are interpreted from the decision-time reference mid. Entry and
    exit each pay one half-spread. If no exit spread estimate is supplied, the
    observed entry spread is reused as the conservative symmetric estimate.
    """

    mid = (cost.observed_bid + cost.observed_ask) / Decimal("2")
    observed_spread_bps = (cost.observed_ask - cost.observed_bid) / mid * Decimal("10000")
    exit_spread_bps = (
        cost.expected_exit_spread_bps
        if cost.expected_exit_spread_bps is not None
        else observed_spread_bps
    )
    entry_spread_cost_bps = observed_spread_bps / Decimal("2")
    expected_exit_spread_cost_bps = exit_spread_bps / Decimal("2")
    total_cost_bps = (
        entry_spread_cost_bps
        + expected_exit_spread_cost_bps
        + cost.estimated_slippage_bps
        + cost.expected_exit_slippage_bps
        + cost.estimated_impact_bps
        + cost.expected_exit_impact_bps
        + cost.estimated_round_trip_commission_bps
    )
    cost_return = total_cost_bps / Decimal("10000")
    return NetReturnDistribution(
        gross=gross,
        observed_spread_bps=observed_spread_bps,
        entry_spread_cost_bps=entry_spread_cost_bps,
        expected_exit_spread_cost_bps=expected_exit_spread_cost_bps,
        entry_slippage_bps=cost.estimated_slippage_bps,
        expected_exit_slippage_bps=cost.expected_exit_slippage_bps,
        entry_impact_bps=cost.estimated_impact_bps,
        expected_exit_impact_bps=cost.expected_exit_impact_bps,
        commission_bps=cost.estimated_round_trip_commission_bps,
        total_cost_bps=total_cost_bps,
        q10=gross.q10 - cost_return,
        q50=gross.q50 - cost_return,
        q90=gross.q90 - cost_return,
        expected_return=(
            gross.expected_return - cost_return
            if gross.expected_return is not None
            else None
        ),
        expected_shortfall_10pct=(
            gross.expected_shortfall_10pct - cost_return
            if gross.expected_shortfall_10pct is not None
            else None
        ),
    )


class TradeAuthorizationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    forecast_fingerprint: str
    confirmation_receipt_fingerprint: str
    decision_at: datetime
    decision: TradeDecision
    notional: Decimal = Field(ge=0)
    max_positive_alpha_notional: Decimal | None = Field(default=None, ge=0)
    reference_price: Decimal | None = None
    observed_bid: Decimal | None = None
    observed_ask: Decimal | None = None
    observed_spread_bps: Decimal | None = None
    total_cost_bps: Decimal | None = None
    estimated_slippage_bps: Decimal | None = None
    estimated_impact_bps: Decimal | None = None
    gross_median_return: Decimal | None = None
    net_median_return: Decimal | None = None
    gross_expected_return: Decimal | None = None
    net_expected_return: Decimal | None = None
    net_q10: Decimal | None = None
    cost_model_version: str | None = None
    evidence_fingerprint: str
    reasons: tuple[str, ...] = ()

    @property
    def spread_bps(self) -> Decimal | None:
        """Compatibility alias for older reporting code."""
        return self.observed_spread_bps

    @field_validator("decision_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return _utc(value)


def authorize_trade(
    *,
    forecast: FrozenForecastV4,
    confirmation: ConfirmationTransitionReceipt,
    actionability: ActionabilityDecision,
    gross: GrossReturnDistribution | None,
    cost: ExecutionCostInput | None,
    evidence_fingerprint: str,
    max_positive_alpha_notional: Decimal | None = None,
    minimum_net_expected_return: Decimal = Decimal("0"),
    minimum_net_q10: Decimal = Decimal("-0.10"),
) -> TradeAuthorizationReceipt:
    """Authorize research capital only from ACT + positive net expectancy.

    The frozen 09:29 probability is never used as a direct capital gate.
    """

    if actionability.instrument_id != forecast.instrument_id or confirmation.instrument_id != forecast.instrument_id:
        raise ValueError("authorization_instrument_mismatch")
    confirmation_fingerprint = _hash(confirmation.model_dump(mode="json"))

    base: dict[str, Any] = {
        "instrument_id": forecast.instrument_id,
        "forecast_fingerprint": forecast.immutable_fingerprint,
        "confirmation_receipt_fingerprint": confirmation_fingerprint,
        "decision_at": actionability.decision_at,
        "max_positive_alpha_notional": max_positive_alpha_notional,
        "evidence_fingerprint": evidence_fingerprint,
    }

    if actionability.actionability != "ACT" or confirmation.new_state != "CONFIRMED_LONG":
        decision: TradeDecision = "WATCH" if actionability.actionability == "WATCH" else "NO_TRADE"
        return TradeAuthorizationReceipt(
            **base,
            decision=decision,
            notional=Decimal("0"),
            reasons=actionability.reasons,
        )

    if gross is None or cost is None:
        return TradeAuthorizationReceipt(
            **base,
            decision="NO_TRADE",
            notional=Decimal("0"),
            reasons=("EXECUTION_ECONOMICS_UNAVAILABLE",),
        )

    net = apply_execution_costs(gross, cost)
    reasons: tuple[str, ...]
    if net.expected_return is None:
        decision = "NO_TRADE"
        reasons = ("EXPECTED_RETURN_UNAVAILABLE",)
        notional = Decimal("0")
    elif net.expected_return <= minimum_net_expected_return:
        decision = "NO_TRADE"
        reasons = ("EXPECTED_NET_ALPHA_NONPOSITIVE",)
        notional = Decimal("0")
    elif net.q10 < minimum_net_q10:
        decision = "NO_TRADE"
        reasons = ("DOWNSIDE_TAIL_EXCEEDS_LIMIT",)
        notional = Decimal("0")
    else:
        decision = "LONG"
        reasons = ()
        notional = cost.notional
        if max_positive_alpha_notional is not None:
            notional = min(notional, max_positive_alpha_notional)

    return TradeAuthorizationReceipt(
        **base,
        decision=decision,
        notional=notional,
        reference_price=cost.reference_price,
        observed_bid=cost.observed_bid,
        observed_ask=cost.observed_ask,
        observed_spread_bps=net.observed_spread_bps,
        total_cost_bps=net.total_cost_bps,
        estimated_slippage_bps=cost.estimated_slippage_bps,
        estimated_impact_bps=cost.estimated_impact_bps,
        gross_median_return=gross.q50,
        net_median_return=net.q50,
        gross_expected_return=gross.expected_return,
        net_expected_return=net.expected_return,
        net_q10=net.q10,
        cost_model_version=cost.cost_model_version,
        reasons=reasons,
    )


class CashPreservingAuthorizedPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    allocation: Decimal = Field(gt=0)
    weight: Decimal = Field(gt=0, le=1)
    authorization_decision_at: datetime
    net_expected_return: Decimal | None = None
    net_q10: Decimal | None = None


class CashPreservingAuthorizedPortfolio(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_version: Literal["cash-preserving-authorized-v1"] = "cash-preserving-authorized-v1"
    starting_equity: Decimal = Field(gt=0)
    max_position_fraction: Decimal = Field(gt=0, le=1)
    positions: tuple[CashPreservingAuthorizedPosition, ...] = ()
    cash: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def conserve(self):
        allocated = sum((position.allocation for position in self.positions), Decimal("0"))
        if abs(self.starting_equity - allocated - self.cash) > Decimal("0.01"):
            raise ValueError("cash_preserving_portfolio_must_conserve_equity")
        return self


def freeze_cash_preserving_authorized_portfolio(
    receipts: Sequence[TradeAuthorizationReceipt],
    *,
    starting_equity: Decimal = Decimal("1000"),
    max_position_fraction: Decimal = Decimal("0.20"),
) -> CashPreservingAuthorizedPortfolio:
    """Allocate only authorized notional; never redistribute unused budget."""

    if starting_equity <= 0:
        raise ValueError("starting_equity_must_be_positive")
    if not Decimal("0") < max_position_fraction <= Decimal("1"):
        raise ValueError("max_position_fraction_out_of_range")
    remaining = starting_equity
    cap = starting_equity * max_position_fraction
    positions: list[CashPreservingAuthorizedPosition] = []
    eligible = sorted(
        (
            receipt
            for receipt in receipts
            if receipt.decision == "LONG" and receipt.notional > 0
        ),
        key=lambda receipt: (receipt.decision_at, receipt.instrument_id),
    )
    for receipt in eligible:
        if remaining <= 0:
            break
        desired = receipt.notional
        if receipt.max_positive_alpha_notional is not None:
            desired = min(desired, receipt.max_positive_alpha_notional)
        allocation = min(desired, cap, remaining)
        if allocation <= 0:
            continue
        positions.append(
            CashPreservingAuthorizedPosition(
                instrument_id=receipt.instrument_id,
                allocation=allocation,
                weight=allocation / starting_equity,
                authorization_decision_at=receipt.decision_at,
                net_expected_return=receipt.net_expected_return,
                net_q10=receipt.net_q10,
            )
        )
        remaining -= allocation
    return CashPreservingAuthorizedPortfolio(
        starting_equity=starting_equity,
        max_position_fraction=max_position_fraction,
        positions=tuple(positions),
        cash=remaining,
    )


class ProspectiveGapActionCycle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    prior_state: ConfirmationState
    resulting_state: ConfirmationState
    confirmation: ConfirmationTransitionReceipt | None = None
    actionability: ActionabilityDecision | None = None
    authorization: TradeAuthorizationReceipt | None = None


def evaluate_prospective_gap_action_cycle(
    *,
    forecast: FrozenForecastV4,
    previous_state: ConfirmationState,
    bars: Sequence[MarketBar],
    evaluated_at: datetime,
    data_quality_ok: bool,
    gross: GrossReturnDistribution | None = None,
    cost: ExecutionCostInput | None = None,
    evidence_fingerprint: str,
    data_quality_reasons: Sequence[str] = (),
    confirmation_config: PostOpenConfirmationConfig | None = None,
    max_positive_alpha_notional: Decimal | None = None,
) -> ProspectiveGapActionCycle:
    """Run one operational forecast-to-confirmation-to-authorization cycle."""

    confirmation = evaluate_post_open_confirmation(
        instrument_id=forecast.instrument_id,
        previous_state=previous_state,
        bars=bars,
        transition_at=evaluated_at,
        data_quality_ok=data_quality_ok,
        data_quality_reasons=data_quality_reasons,
        config=confirmation_config,
    )
    if confirmation is None:
        return ProspectiveGapActionCycle(
            instrument_id=forecast.instrument_id,
            prior_state=previous_state,
            resulting_state=previous_state,
        )
    actionability = actionability_from_confirmation(confirmation)
    authorization = authorize_trade(
        forecast=forecast,
        confirmation=confirmation,
        actionability=actionability,
        gross=gross,
        cost=cost,
        evidence_fingerprint=evidence_fingerprint,
        max_positive_alpha_notional=max_positive_alpha_notional,
    )
    return ProspectiveGapActionCycle(
        instrument_id=forecast.instrument_id,
        prior_state=previous_state,
        resulting_state=confirmation.new_state,
        confirmation=confirmation,
        actionability=actionability,
        authorization=authorization,
    )


class PairedForecastObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    cohort_fingerprint: str
    instrument_id: str
    v3_probability: Decimal = Field(ge=0, le=1)
    v4_probability: Decimal = Field(ge=0, le=1)
    outcome: bool


class PairedForecastMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["paired-v3-v4-v1"] = V4_PAIRED_EVALUATION_VERSION
    n: int
    mean_delta_brier_v4_minus_v3: Decimal | None
    mean_delta_log_loss_v4_minus_v3: Decimal | None
    mean_delta_abs_error_v4_minus_v3: Decimal | None
    v3_brier: Decimal | None
    v4_brier: Decimal | None


def _log_loss(probability: Decimal, outcome: bool) -> Decimal:
    epsilon = Decimal("0.000001")
    p = min(Decimal("1") - epsilon, max(epsilon, probability))
    p_float = float(p if outcome else Decimal("1") - p)
    return Decimal(str(-math.log(p_float)))


def evaluate_paired_v3_v4(observations: Sequence[PairedForecastObservation]) -> PairedForecastMetrics:
    if not observations:
        return PairedForecastMetrics(
            n=0,
            mean_delta_brier_v4_minus_v3=None,
            mean_delta_log_loss_v4_minus_v3=None,
            mean_delta_abs_error_v4_minus_v3=None,
            v3_brier=None,
            v4_brier=None,
        )

    v3_briers: list[Decimal] = []
    v4_briers: list[Decimal] = []
    log_deltas: list[Decimal] = []
    abs_deltas: list[Decimal] = []
    for observation in observations:
        y = Decimal("1") if observation.outcome else Decimal("0")
        b3 = (observation.v3_probability - y) ** 2
        b4 = (observation.v4_probability - y) ** 2
        v3_briers.append(b3)
        v4_briers.append(b4)
        log_deltas.append(
            _log_loss(observation.v4_probability, observation.outcome)
            - _log_loss(observation.v3_probability, observation.outcome)
        )
        abs_deltas.append(
            abs(observation.v4_probability - y) - abs(observation.v3_probability - y)
        )

    n = Decimal(len(observations))
    v3_brier = sum(v3_briers, Decimal("0")) / n
    v4_brier = sum(v4_briers, Decimal("0")) / n
    return PairedForecastMetrics(
        n=len(observations),
        mean_delta_brier_v4_minus_v3=v4_brier - v3_brier,
        mean_delta_log_loss_v4_minus_v3=sum(log_deltas, Decimal("0")) / n,
        mean_delta_abs_error_v4_minus_v3=sum(abs_deltas, Decimal("0")) / n,
        v3_brier=v3_brier,
        v4_brier=v4_brier,
    )


class SelectiveForecastObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    actionability: Actionability
    probability: Decimal = Field(ge=0, le=1)
    outcome: bool


class SelectiveForecastMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_forecasts: int
    acted_forecasts: int
    coverage: Decimal | None
    selective_accuracy: Decimal | None
    bullish_precision: Decimal | None


def evaluate_selective_forecasts(
    observations: Sequence[SelectiveForecastObservation],
    *,
    threshold: Decimal = Decimal("0.50"),
) -> SelectiveForecastMetrics:
    total = len(observations)
    acted = [observation for observation in observations if observation.actionability == "ACT"]
    if total == 0:
        return SelectiveForecastMetrics(
            total_forecasts=0,
            acted_forecasts=0,
            coverage=None,
            selective_accuracy=None,
            bullish_precision=None,
        )
    coverage = Decimal(len(acted)) / Decimal(total)
    if not acted:
        return SelectiveForecastMetrics(
            total_forecasts=total,
            acted_forecasts=0,
            coverage=coverage,
            selective_accuracy=None,
            bullish_precision=None,
        )
    correct = sum((observation.probability > threshold) == observation.outcome for observation in acted)
    bullish = [observation for observation in acted if observation.probability > threshold]
    bullish_wins = sum(observation.outcome for observation in bullish)
    return SelectiveForecastMetrics(
        total_forecasts=total,
        acted_forecasts=len(acted),
        coverage=coverage,
        selective_accuracy=Decimal(correct) / Decimal(len(acted)),
        bullish_precision=(
            Decimal(bullish_wins) / Decimal(len(bullish)) if bullish else None
        ),
    )


def bind_v3_v4_pair(
    *,
    v3: FrozenForecast,
    v4: FrozenForecastV4,
    outcome: bool,
) -> PairedForecastObservation:
    if v3.instrument_id != v4.instrument_id:
        raise ValueError("paired_forecast_instrument_mismatch")
    if v3.evidence_snapshot_id != v4.evidence_snapshot_id:
        raise ValueError("paired_forecast_evidence_snapshot_mismatch")
    return PairedForecastObservation(
        session_date=v4.session_date,
        cohort_fingerprint=v4.cohort_fingerprint,
        instrument_id=v4.instrument_id,
        v3_probability=v3.p_close_above_open,
        v4_probability=v4.calibrated_p_close_above_open,
        outcome=outcome,
    )


__all__ = [
    "ActionabilityDecision",
    "CashPreservingAuthorizedPortfolio",
    "CashPreservingAuthorizedPosition",
    "CalibratorArtifact",
    "CatalystDecomposition",
    "ConfirmationTransitionReceipt",
    "DEFAULT_V4_MODEL_SPEC",
    "EvidenceQuality",
    "ExecutionCostInput",
    "ExtensionComponents",
    "ExtensionExhaustionRisk",
    "FinvizFrozenCohort",
    "FrozenForecastV4",
    "GrossReturnDistribution",
    "PostOpenConfirmationConfig",
    "ProspectiveGapActionCycle",
    "MechanismRiskScores",
    "NetReturnDistribution",
    "PairedForecastMetrics",
    "PairedForecastObservation",
    "PredictionEvidenceQuality",
    "PremarketFeature",
    "PremarketMarketStateSnapshot",
    "SelectiveForecastMetrics",
    "SelectiveForecastObservation",
    "TradeAuthorizationReceipt",
    "V4ForecastAttempt",
    "V4ModelSpec",
    "actionability_from_confirmation",
    "apply_calibrator",
    "apply_execution_costs",
    "evaluate_post_open_confirmation",
    "evaluate_prospective_gap_action_cycle",
    "freeze_cash_preserving_authorized_portfolio",
    "authorize_trade",
    "bind_v3_v4_pair",
    "build_premarket_market_state",
    "derive_extension_exhaustion_risk",
    "evaluate_paired_v3_v4",
    "evaluate_selective_forecasts",
    "finviz_cohort_from_universe",
    "freeze_v4_forecast",
    "market_state_from_candidate",
    "extension_components_from_market_state",
    "score_v4_raw_probability",
    "summarize_evidence_quality",
    "transition_confirmation",
]
