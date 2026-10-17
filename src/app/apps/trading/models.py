from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    EQUITY = "equity"
    FOREX = "forex"
    COMMODITY = "commodity"
    # Economic series (TVP-10.5): research data to chart, never traded.
    ECONOMIC = "economic"


class InstrumentType(StrEnum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    EQUITY = "equity"
    INDEX = "index"


class FeedType(StrEnum):
    REST = "rest"
    WEBSOCKET = "websocket"
    WEBSOCKET_AND_REST = "websocket_and_rest"
    SOCKET = "socket"
    HISTORICAL_POLLING = "historical_polling"
    HISTORICAL_DAILY = "historical_daily"


class UsageScope(StrEnum):
    PERSONAL_LOCAL = "personal_local"
    INTERNAL = "internal"
    EXTERNAL_DISPLAY = "external_display"
    LICENSED = "licensed"


class AdjustmentMode(StrEnum):
    RAW = "raw"
    SPLIT = "split_adjusted"
    DIVIDEND = "dividend_adjusted"


class CanonicalInstrument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str = Field(min_length=3, max_length=200)
    asset_class: AssetClass
    instrument_type: InstrumentType
    venue: str = Field(min_length=1, max_length=64)
    venue_symbol: str = Field(min_length=1, max_length=64)
    display_symbol: str = Field(min_length=1, max_length=64)
    base_currency: str | None = Field(default=None, max_length=16)
    quote_currency: str | None = Field(default=None, max_length=16)
    exchange_timezone: str = Field(default="UTC", max_length=64)
    session_calendar: str = Field(default="24x7", max_length=64)
    price_scale: int = Field(default=100, gt=0)
    minimum_tick: Decimal = Field(default=Decimal("0.01"), gt=0)
    status: Literal["active", "inactive", "delisted"] = "active"
    # A descriptive name where the symbol alone says little (an economic series' title).
    name: str | None = Field(default=None, max_length=200)

    @field_validator("venue", "venue_symbol", "display_symbol", "base_currency", "quote_currency")
    @classmethod
    def normalize_upper(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None


class ProviderPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    usage_scope: UsageScope
    redistribution_allowed: bool = False
    authentication_required: bool = False
    is_official_api: bool = True
    realtime_scope: str = "none"
    delay_seconds: int = Field(default=0, ge=0)
    terms_reference: str = ""
    supported_asset_classes: tuple[AssetClass, ...] = ()
    supported_intervals: tuple[str, ...] = ()
    history_depth: str = "provider_defined"
    rate_limit_policy: str = "bounded"


class ProviderBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: str = Field(min_length=3, max_length=240)
    instrument_id: str = Field(min_length=3, max_length=200)
    provider: str = Field(min_length=1, max_length=64)
    provider_symbol: str = Field(min_length=1, max_length=96)
    feed_type: FeedType
    realtime_scope: str = "none"
    delay_seconds: int = Field(default=0, ge=0)
    adjustment_capabilities: tuple[AdjustmentMode, ...] = (AdjustmentMode.RAW,)
    supported_intervals: tuple[str, ...] = ()
    usage_scope: UsageScope = UsageScope.PERSONAL_LOCAL
    is_official_api: bool = True


class MarketBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    interval: str
    start_time: datetime
    end_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    is_final: bool = True
    adjustment_mode: AdjustmentMode = AdjustmentMode.RAW
    session: str = "regular"
    provider: str
    provider_event_id: str | None = None
    provider_sequence: int | None = None
    ingestion_revision: int = Field(default=1, ge=1)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("start_time", "end_time", "received_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Trading timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("high")
    @classmethod
    def validate_high(cls, value: Decimal, info):
        data = info.data
        if "open" in data and value < data["open"]:
            raise ValueError("high must be at least open")
        return value

    @field_validator("low")
    @classmethod
    def validate_low(cls, value: Decimal, info):
        data = info.data
        if "open" in data and value > data["open"]:
            raise ValueError("low must be at most open")
        return value


class DatasetProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    requested_binding: str
    resolved_binding: str
    fallback_reason: str | None = None
    dataset_fingerprint: str
    freshness_mode: Literal["live", "polled", "delayed", "cached", "fallback"]
    as_of: datetime
    received_at: datetime
    delay_seconds: int = Field(default=0, ge=0)
    cached: bool = False
    history_complete: bool = False


class BarsResponse(BaseModel):
    instrument: CanonicalInstrument
    binding: ProviderBinding
    provenance: DatasetProvenance
    interval: str
    bars: list[MarketBar]


class DrawingPoint(BaseModel):
    time: datetime
    price: Decimal


class DrawingDocument(BaseModel):
    drawing_id: str
    instrument_id: str
    tool_type: str
    points: list[DrawingPoint]
    interval_visibility: tuple[str, ...] = ()
    style: dict[str, str | int | float | bool] = Field(default_factory=dict)
    locked: bool = False
    hidden: bool = False
    revision: int = Field(default=1, ge=1)


class ChartState(BaseModel):
    chart_id: str
    instrument_id: str
    binding_id: str
    interval: str = "1m"
    chart_type: Literal["candlestick", "line", "volume"] = "candlestick"
    indicators: list[dict[str, Any]] = Field(default_factory=list)


class TradingWorkspaceDocument(BaseModel):
    workspace_id: str
    name: str
    layout: Literal["one", "four", "two-horizontal", "two-vertical"] = "one"
    charts: list[ChartState] = Field(default_factory=list)
    link_groups: list[dict[str, Any]] = Field(default_factory=list)
    panels: dict[str, bool] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)


class TradingWatchlistDocument(BaseModel):
    watchlist_id: str
    name: str
    instrument_ids: list[str] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)


class TradingDocumentEnvelope(BaseModel):
    record_id: str
    record_type: str
    revision: int
    payload: dict[str, Any]
    updated_at: datetime | None = None
