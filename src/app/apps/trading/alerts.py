from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, ValidationError, model_validator

from app.security.url_policy import UrlPolicyError, check_outbound_url
from app.persistence.unit_of_work import PostgresUnitOfWork

from .alert_conditions import (
    MAX_ALERT_CONDITIONS,
    AlertConditionSpec,
    AlertFrequency,
    ChannelTarget,
    PriceSource,
    TrendlineAlertPoint,
    ScriptSource,
    TrendlineSource,
    ValueTarget,
    condition_sources,
    legacy_conditions,
    validate_conditions_against_registry,
    validate_external_scope,
)
from .alerts_evaluation import AlertConditionOutcome, ConditionObservation, operator_met, validate_conditions_can_fire
from .indicators.engine import CORE_INDICATOR_FORMULA_VERSION

logger = logging.getLogger(__name__)


AlertCondition = Literal[
    "price_above",
    "price_below",
    "percent_change_above",
    "percent_change_below",
    "indicator_above",
    "indicator_below",
    "indicator_cross_above",
    "indicator_cross_below",
    "volume_above",
    "volume_below",
    "trendline_crossing",
    "trendline_crossing_up",
    "trendline_crossing_down",
    "trendline_above",
    "trendline_below",
    # TVP-1.2: the alert is described by its ``conditions``.
    "conditions",
]
IndicatorId = Literal[
    "sma",
    "ema",
    "rsi",
    "macd",
    "bollinger",
    "atr",
    "vwap",
    "stochastic-rsi",
]
TrendlineMode = Literal[
    "crossing",
    "crossing_up",
    "crossing_down",
    "greater_than",
    "less_than",
]
AlertNotificationChannel = Literal["app", "toast", "sound", "webhook", "email", "push", "desktop"]

class AlertWebhookSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Write-only: a webhook URL can carry its token, so it is kept with the
    # secret in the protected store and never returned. Omit it on an update
    # to keep the stored one. The outbound URL policy is a write rule.
    url: str | None = Field(default=None, min_length=1, max_length=2000, exclude=True)
    # Read-only, set by the server: scheme and host of the stored URL.
    display_url: str | None = None
    # Read-only, set by the server: whether a signing secret is stored.
    has_secret: bool = False


class AlertEmailSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")


class AlertSoundSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)


class AlertDeliverySettings(BaseModel):
    """Per-alert channel settings (TVP-1.2 schema; delivery itself is TVP-0.5a-c / TVP-1.5)."""

    model_config = ConfigDict(extra="forbid")

    webhook: AlertWebhookSettings | None = None
    email: AlertEmailSettings | None = None
    sound: AlertSoundSettings | None = None


class TradingAlertParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_bars: int = Field(default=1, ge=1, le=500)
    indicator_id: IndicatorId | None = None
    period: int = Field(default=14, ge=1, le=500)
    fast_period: int = Field(default=12, ge=1, le=500)
    slow_period: int = Field(default=26, ge=1, le=500)
    signal_period: int = Field(default=9, ge=1, le=500)
    component: Literal[
        "value", "line", "signal", "histogram", "upper", "middle", "lower"
    ] = "value"
    anchor_bars_ago: int = Field(default=0, ge=0, le=499)
    message: str = Field(default="", max_length=500)
    # The alert's name, for the list and the {{alert_name}} placeholder (TVP-1.5).
    name: str = Field(default="", max_length=120)
    # The drawing (and which of its levels) a line alert follows (TVP-1.4): the chart moves the alert's line with
    # the drawing. The server evaluates trendline_points as usual; these only link the alert back.
    drawing_id: str | None = Field(default=None, max_length=200)
    drawing_level: str | None = Field(default=None, max_length=100)
    notification_channels: list[AlertNotificationChannel] = Field(
        default_factory=lambda: list[AlertNotificationChannel](["app", "toast"]),
        max_length=6,
    )
    # Deprecated mirror of the alert's ``frequency``; requests without a
    # ``frequency`` still set it here.
    trigger_policy: AlertFrequency = "every_time"
    delivery: AlertDeliverySettings = Field(default_factory=AlertDeliverySettings)
    trendline_points: list[TrendlineAlertPoint] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
    )
    trendline_mode: TrendlineMode | None = None


# Watchlist alerts (TVP-1.7): an alert on every symbol of a watchlist has the instrument ``watchlist:<record id>``.
WATCHLIST_INSTRUMENT_PREFIX = "watchlist:"
# The most symbols one watchlist alert evaluates; the default and each provider's own cap are lower.
WATCHLIST_SYMBOL_MAX = 1_000
WATCHLIST_SYMBOL_DEFAULT = 100


def watchlist_id_of(instrument_id: str) -> str | None:
    """The watchlist record id of a watchlist alert's instrument, or None for an ordinary alert."""
    if not instrument_id.startswith(WATCHLIST_INSTRUMENT_PREFIX):
        return None
    record_id = instrument_id[len(WATCHLIST_INSTRUMENT_PREFIX):]
    return record_id or None


def watchlist_symbols(payload: Mapping[str, Any]) -> list[str]:
    """A watchlist document's symbols, in list order and once each (``instrumentIds``, else its symbol items)."""
    raw = payload.get("instrumentIds")
    if not isinstance(raw, list):
        raw = [item.get("instrumentId") for item in payload.get("items", []) if isinstance(item, dict) and item.get("type") == "symbol"]
    symbols: list[str] = []
    for value in raw:
        if isinstance(value, str) and value and not value.startswith(WATCHLIST_INSTRUMENT_PREFIX) and value not in symbols:
            symbols.append(value)
    return symbols


class TradingAlertEvaluationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval: str = Field(default="1m", min_length=1, max_length=16)
    allow_partial_bars: bool = False
    formula_version: str = CORE_INDICATOR_FORMULA_VERSION
    # Watchlist alerts (TVP-1.7): the most symbols of the list evaluated, in list order; the server also caps it by
    # what the symbols' providers can serve each pass (``watchlist_symbol_cap``). Stored only when set.
    symbol_limit: int | None = Field(default=None, ge=1, le=WATCHLIST_SYMBOL_MAX)


def policy_json(policy: TradingAlertEvaluationPolicy) -> str:
    """The policy as stored: without unset optional fields, so alerts written before them keep the same JSON."""
    return policy.model_dump_json(exclude_none=True)


# Settings that say how an alert notifies, not when it fires. They are stored
# apart from condition_parameters (notification_settings), so editing them
# keeps the alert's trigger history.
NOTIFICATION_PARAMETER_FIELDS = ("message", "notification_channels", "delivery")


class _AlertContract(BaseModel):
    """Fields shared by stored alerts and requests.

    Validators here only normalise: reading a stored alert must never fail
    because a write rule changed. Write rules live in ``_AlertWrite``.
    """

    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(min_length=3, max_length=200)
    binding_id: str | None = Field(default=None, max_length=240)
    condition_type: AlertCondition = "conditions"
    threshold: Decimal = Decimal("0")
    conditions: list[AlertConditionSpec] = Field(default_factory=list, max_length=MAX_ALERT_CONDITIONS)
    frequency: AlertFrequency = "every_time"
    parameters: TradingAlertParameters = Field(default_factory=TradingAlertParameters)
    evaluation_policy: TradingAlertEvaluationPolicy = Field(
        default_factory=TradingAlertEvaluationPolicy
    )
    cooldown_seconds: int = Field(default=0, ge=0, le=31_536_000)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def normalize_contract(self):
        if "frequency" not in self.model_fields_set:
            self.frequency = self.parameters.trigger_policy
        self.parameters.trigger_policy = self.frequency
        if self.condition_type != "conditions" and not self.conditions:
            try:
                self.conditions = legacy_conditions(self.condition_type, self.threshold, self.parameters)
            except (ValueError, ValidationError):
                # A stored legacy alert that cannot be described has no conditions and never fires.
                self.conditions = []
        return self


class TradingAlert(_AlertContract):
    alert_id: str
    enabled: bool = True
    last_observed_price: Decimal | None = None
    last_observed_value: Decimal | None = None
    last_triggered_at: datetime | None = None
    revision: int = Field(default=1, ge=1)
    # Bumped only when what the alert watches changes (not by notification or
    # lifecycle edits); per-bar trigger keys use it.
    definition_revision: int = Field(default=1, ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # The protected-store reference of the alert's webhook; never serialised.
    _webhook_ref: str | None = PrivateAttr(default=None)

    @property
    def webhook_ref(self) -> str | None:
        return self._webhook_ref

    @property
    def watchlist_id(self) -> str | None:
        """The watchlist a watchlist alert watches (TVP-1.7): its instrument is ``watchlist:<record id>``."""
        return watchlist_id_of(self.instrument_id)

    def is_expired(self, at: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        moment = at or datetime.now(timezone.utc)
        return self.expires_at <= moment


class _AlertWrite(_AlertContract):
    # Write-only; stored in the protected secret store, never returned.
    webhook_secret: SecretStr | None = Field(default=None, max_length=500, exclude=True)

    @model_validator(mode="after")
    def validate_write(self):
        if self.evaluation_policy.formula_version != CORE_INDICATOR_FORMULA_VERSION:
            raise ValueError(
                f"unsupported alert formula version: {self.evaluation_policy.formula_version}"
            )
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        webhook = self.parameters.delivery.webhook
        if webhook is not None and webhook.url is not None:
            try:
                # HTTPS only; loopback and private hosts only where OMNIX_ALLOWED_PRIVATE_NETWORKS allows them.
                # Hostnames are resolved and checked again before every delivery (TVP-0.5a).
                webhook.url = check_outbound_url(webhook.url.strip(), strict=True, https_only=True)
            except UrlPolicyError as exc:
                raise ValueError(f"webhook url is not allowed: {exc}") from exc
        if self.condition_type == "conditions":
            if not self.conditions:
                raise ValueError("condition_type 'conditions' needs one to five conditions")
            if self.threshold != 0:
                raise ValueError("alerts described by conditions use a zero threshold")
        else:
            self._validate_legacy_fields()
            derived = legacy_conditions(self.condition_type, self.threshold, self.parameters)
            if "conditions" in self.model_fields_set and self.conditions and self.conditions != derived:
                raise ValueError(
                    "conditions disagree with the legacy condition_type; send condition_type 'conditions'"
                )
            self.conditions = derived
        if self.instrument_id.startswith(WATCHLIST_INSTRUMENT_PREFIX) and watchlist_id_of(self.instrument_id) is None:
            raise ValueError("a watchlist alert names its watchlist: watchlist:<record id>")
        if watchlist_id_of(self.instrument_id) is not None:
            # One definition for every symbol of a list: a trendline is drawn on one symbol's chart.
            if any(isinstance(source, TrendlineSource) for condition in self.conditions for source in condition_sources(condition)):
                raise ValueError("a watchlist alert cannot use a trendline")
            # A script run per symbol and pass is more than the monitor's budget allows.
            if any(isinstance(source, ScriptSource) for condition in self.conditions for source in condition_sources(condition)):
                raise ValueError("a watchlist alert cannot use a script")
            if self.binding_id is not None:
                raise ValueError("a watchlist alert uses each symbol's own feed, not a binding")
        elif self.evaluation_policy.symbol_limit is not None:
            raise ValueError("symbol_limit is for watchlist alerts")
        validate_conditions_against_registry(self.conditions)
        if watchlist_id_of(self.instrument_id) is None:
            # A list's members are checked as each is evaluated: a member without the data has no value.
            validate_external_scope(self.instrument_id, self.conditions)
        validate_conditions_can_fire(self.conditions, self.evaluation_policy.interval)
        # Only "once per bar close" waits for closed bars; every other frequency
        # is intrabar, whatever an older client sends.
        self.evaluation_policy.allow_partial_bars = self.frequency != "once_per_bar_close"
        return self

    def _validate_legacy_fields(self) -> None:
        if (
            self.condition_type.startswith("indicator_")
            and self.parameters.indicator_id is None
        ):
            raise ValueError("indicator conditions require parameters.indicator_id")
        if (
            self.parameters.indicator_id == "macd"
            and self.parameters.fast_period >= self.parameters.slow_period
        ):
            raise ValueError("MACD fast_period must be smaller than slow_period")
        if self.condition_type.startswith("trendline_"):
            if self.parameters.trendline_points is None:
                raise ValueError("trendline conditions require two trendline points")
            if self.threshold != 0:
                raise ValueError("trendline conditions use a zero threshold")
            expected_mode = {
                "trendline_crossing": "crossing",
                "trendline_crossing_up": "crossing_up",
                "trendline_crossing_down": "crossing_down",
                "trendline_above": "greater_than",
                "trendline_below": "less_than",
            }[self.condition_type]
            if self.parameters.trendline_mode not in (None, expected_mode):
                raise ValueError("trendline_mode must match condition_type")


class TradingAlertCreate(_AlertWrite):
    # No "/" or other separators: alert ids are part of protected-store references.
    alert_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class TradingAlertUpdate(_AlertWrite):
    enabled: bool = True


class TradingAlertEvaluation(BaseModel):
    """An observed price pushed to ``POST /api/trading/alerts/evaluate``."""

    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    binding_id: str | None = None
    resolved_binding_id: str | None = None
    provider: str | None = None
    interval: str = "1m"
    observed_price: Decimal
    observed_volume: Decimal | None = None
    is_final: bool = True
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    percent_changes: dict[str, Decimal] = Field(default_factory=dict)
    indicator_values: dict[str, Decimal] = Field(default_factory=dict)


class TradingAlertTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_id: str
    alert_id: str
    instrument_id: str
    binding_id: str | None = None
    provider: str | None = None
    observed_value: Decimal
    observed_price: Decimal
    threshold: Decimal
    condition_type: AlertCondition
    observed_at: datetime
    evaluated_at: datetime
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class AlertEvaluationContext:
    """Where one batch of outcomes came from."""

    instrument_id: str
    interval: str
    evaluated_at: datetime
    binding_id: str | None = None
    resolved_binding_id: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class AlertOutcomeRecord:
    """An outcome computed for one revision of one alert."""

    alert_id: str
    revision: int
    outcome: AlertConditionOutcome


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[PostgresUnitOfWork]: ...


def cooldown_elapsed(
    last_triggered_at: datetime | None,
    evaluated_at: datetime,
    cooldown_seconds: int,
) -> bool:
    if last_triggered_at is None or cooldown_seconds <= 0:
        return True
    return evaluated_at >= last_triggered_at + timedelta(seconds=cooldown_seconds)


ONCE_PER_MINUTE = timedelta(seconds=60)


def frequency_allows(
    frequency: str,
    last_triggered_at: datetime | None,
    evaluated_at: datetime,
) -> bool:
    """The time-based part of a frequency; per-bar and once limits are idempotency keys."""
    if frequency != "once_per_minute" or last_triggered_at is None:
        return True
    return evaluated_at >= last_triggered_at + ONCE_PER_MINUTE


def alert_trigger_key(
    alert_id: str,
    frequency: str,
    outcome: AlertConditionOutcome,
    *,
    revision: int,
    definition_revision: int,
) -> str:
    """The idempotency key of a trigger.

    ``once`` has one key per alert revision, so re-enabling a fired alert
    re-arms it. The others use the definition revision, which notification
    and lifecycle edits leave alone, so such an edit cannot let the same bar
    trigger twice. The per-bar frequencies have one key per bar. ``every_time`` and ``once_per_minute`` have one per closed bar, and
    on a forming bar one per bar and observed values, so a revised or
    re-served closed bar (provider failover) cannot trigger twice. Keys live
    in PostgreSQL, so a restart cannot trigger the same thing twice either.
    """
    parts = [alert_id, frequency, str(revision if frequency == "once" else definition_revision)]
    if frequency != "once":
        parts.append(outcome.bar_start.astimezone(timezone.utc).isoformat())
    if frequency in {"every_time", "once_per_minute"}:
        if outcome.bar_is_final:
            parts.append("closed")
        else:
            parts.append("forming")
            parts.extend(json.dumps(observation.payload(), sort_keys=True) for observation in outcome.observations)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def pushed_price_outcome(
    alert: TradingAlert,
    evaluation: TradingAlertEvaluation,
) -> AlertConditionOutcome | None:
    """``POST /evaluate``: the pushed price against the previous observation.

    Only alerts whose conditions all read one price field (close from
    ``observed_price``, or volume from ``observed_volume``) against value
    targets qualify; ``last_observed_value`` is the previous value.
    """
    fields = {
        condition.source.field if isinstance(condition.source, PriceSource) else None
        for condition in alert.conditions
    }
    if len(fields) != 1 or not fields <= {"close", "volume"}:
        return None
    current = evaluation.observed_volume if fields == {"volume"} else evaluation.observed_price
    if current is None:
        return None
    previous = alert.last_observed_value
    observations: list[ConditionObservation] = []
    for position, condition in enumerate(alert.conditions):
        target = condition.target
        if isinstance(target, ValueTarget):
            pair = (target.value, target.value)
            met = operator_met(condition.operator, (previous, current), target=pair)
            observations.append(
                ConditionObservation(position, condition.operator, met, current, previous, target.value, target.value)
            )
        elif (
            isinstance(target, ChannelTarget)
            and isinstance(target.upper, ValueTarget)
            and isinstance(target.lower, ValueTarget)
        ):
            upper = (target.upper.value, target.upper.value)
            lower = (target.lower.value, target.lower.value)
            met = operator_met(condition.operator, (previous, current), upper=upper, lower=lower)
            observations.append(
                ConditionObservation(
                    position, condition.operator, met, current, previous,
                    upper=upper[1], lower=lower[1], upper_previous=upper[0], lower_previous=lower[0],
                )
            )
        else:
            return None
    return AlertConditionOutcome(
        met=all(observation.met for observation in observations),
        bar_start=evaluation.observed_at,
        bar_end=evaluation.observed_at,
        bar_is_final=evaluation.is_final,
        close=evaluation.observed_price,
        volume=evaluation.observed_volume or Decimal("0"),
        observations=tuple(observations),
        volume_known=evaluation.observed_volume is not None,
    )
