from __future__ import annotations

"""Durable deterministic trigger-plan state machine for AI research arms."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work


TriggerPlanStatus = Literal[
    "ARMED",
    "TRIGGERED",
    "EXECUTION_CHECK",
    "FILLED",
    "REJECTED",
    "EXPIRED",
    "INVALIDATED",
    "TRIGGER_ORDER_UNRESOLVED",
]
TriggerType = Literal[
    "bar_close_above",
    "bar_close_below",
    "vwap_reclaim",
    "new_session_high",
    "volume_expansion",
]
RunnerPolicy = Literal["none", "ema20_trail", "atr_trail", "structure_trail"]


class TriggerCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_type: TriggerType
    price: Decimal | None = Field(default=None, gt=0)
    min_volume_ratio: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _required_fields(self):
        if self.trigger_type in {"bar_close_above", "bar_close_below"} and self.price is None:
            raise ValueError("price trigger requires price")
        if self.trigger_type == "volume_expansion" and self.min_volume_ratio is None:
            raise ValueError("volume trigger requires min_volume_ratio")
        return self


class AuthoritativeTradeGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    geometry_version: str = "deterministic-runner-geometry-v1"
    entry_reference: Decimal = Field(gt=0)
    invalidation_price: Decimal = Field(gt=0)
    target_1: Decimal = Field(gt=0)
    target_2: Decimal | None = Field(default=None, gt=0)
    risk_per_share: Decimal = Field(gt=0)
    estimated_cost_bps: Decimal = Field(default=Decimal("0"), ge=0)
    net_r_target_1: Decimal | None = None
    net_r_target_2: Decimal | None = None
    runner_policy: RunnerPolicy = "none"
    valid: bool = True
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _long_geometry(self):
        if self.invalidation_price >= self.entry_reference:
            raise ValueError("long invalidation must be below entry")
        if self.target_1 <= self.entry_reference:
            raise ValueError("long target_1 must be above entry")
        if self.target_2 is not None and self.target_2 <= self.target_1:
            raise ValueError("target_2 must be above target_1")
        return self


class TriggerPlanOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str
    provider: str | None = None
    model: str | None = None
    setup_family: str
    thesis: str
    policy_version: str


class TriggerMarketSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    current_price: Decimal = Field(gt=0)
    session_high: Decimal = Field(gt=0)
    session_vwap: Decimal | None = Field(default=None, gt=0)
    volume_ratio: Decimal | None = Field(default=None, ge=0)
    previous_price: Decimal | None = Field(default=None, gt=0)
    previous_session_high: Decimal | None = Field(default=None, gt=0)
    previous_session_vwap: Decimal | None = Field(default=None, gt=0)
    bar_high: Decimal | None = Field(default=None, gt=0)
    bar_low: Decimal | None = Field(default=None, gt=0)
    bar_close: Decimal | None = Field(default=None, gt=0)

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("trigger market snapshot must be timezone-aware")
        return value.astimezone(timezone.utc)


class TriggerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_plan_id: str
    strategy_id: str
    arm_id: str
    instrument_id: str
    status: TriggerPlanStatus = "ARMED"
    created_at: datetime
    expires_at: datetime
    trigger: TriggerCondition
    geometry: AuthoritativeTradeGeometry
    required_certificate_ids: tuple[str, ...] = ()
    max_spread_bps: Decimal = Field(gt=0)
    origin: TriggerPlanOrigin
    transition_reason: str | None = None
    revision: int = Field(default=1, ge=1)
    updated_at: datetime | None = None

    @field_validator("created_at", "expires_at", "updated_at")
    @classmethod
    def _aware_optional(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("trigger plan timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _ordered(self):
        if self.expires_at <= self.created_at:
            raise ValueError("trigger plan expiry must follow creation")
        return self


_TERMINAL = {
    "FILLED",
    "REJECTED",
    "EXPIRED",
    "INVALIDATED",
    "TRIGGER_ORDER_UNRESOLVED",
}


def create_trigger_plan_id(
    *,
    strategy_id: str,
    arm_id: str,
    instrument_id: str,
    decision_id: str,
) -> str:
    raw = "|".join((strategy_id, arm_id, instrument_id, decision_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _trigger_hit(plan: TriggerPlan, snapshot: TriggerMarketSnapshot) -> bool:
    trigger = plan.trigger
    if trigger.trigger_type == "bar_close_above":
        return (
            trigger.price is not None
            and snapshot.bar_close is not None
            and snapshot.bar_close >= trigger.price
        )
    if trigger.trigger_type == "bar_close_below":
        return (
            trigger.price is not None
            and snapshot.bar_close is not None
            and snapshot.bar_close <= trigger.price
        )
    if trigger.trigger_type == "vwap_reclaim":
        if snapshot.session_vwap is None:
            return False
        previous_below = (
            snapshot.previous_price is None
            or snapshot.previous_session_vwap is None
            or snapshot.previous_price < snapshot.previous_session_vwap
        )
        return previous_below and snapshot.current_price >= snapshot.session_vwap
    if trigger.trigger_type == "new_session_high":
        return (
            snapshot.previous_session_high is not None
            and snapshot.session_high > snapshot.previous_session_high
        )
    if trigger.trigger_type == "volume_expansion":
        return (
            trigger.min_volume_ratio is not None
            and snapshot.volume_ratio is not None
            and snapshot.volume_ratio >= trigger.min_volume_ratio
        )
    return False


def _same_bar_order_ambiguous(
    plan: TriggerPlan,
    snapshot: TriggerMarketSnapshot,
    *,
    trigger_hit: bool,
    invalidation_hit: bool,
) -> bool:
    if not trigger_hit or not invalidation_hit:
        return False
    if snapshot.bar_high is None or snapshot.bar_low is None:
        return False
    trigger_price = plan.trigger.price
    if plan.trigger.trigger_type in {"bar_close_above", "bar_close_below"}:
        if trigger_price is None:
            return False
        return (
            snapshot.bar_high >= max(trigger_price, plan.geometry.invalidation_price)
            and snapshot.bar_low <= min(trigger_price, plan.geometry.invalidation_price)
        )
    # For derived trigger types OHLC cannot prove whether the trigger transition
    # occurred before a stop breach inside the same bar.
    return True


def evaluate_armed_trigger(
    plan: TriggerPlan,
    snapshot: TriggerMarketSnapshot,
) -> TriggerPlan:
    if plan.status != "ARMED":
        return plan
    if snapshot.observed_at >= plan.expires_at:
        return plan.model_copy(
            update={
                "status": "EXPIRED",
                "transition_reason": "trigger_expired",
                "revision": plan.revision + 1,
                "updated_at": snapshot.observed_at,
            }
        )

    invalidation_hit = (
        snapshot.current_price <= plan.geometry.invalidation_price
        or (
            snapshot.bar_low is not None
            and snapshot.bar_low <= plan.geometry.invalidation_price
        )
    )
    trigger_hit = _trigger_hit(plan, snapshot)
    if _same_bar_order_ambiguous(
        plan,
        snapshot,
        trigger_hit=trigger_hit,
        invalidation_hit=invalidation_hit,
    ):
        return plan.model_copy(
            update={
                "status": "TRIGGER_ORDER_UNRESOLVED",
                "transition_reason": "ohlc_cannot_order_trigger_and_invalidation",
                "revision": plan.revision + 1,
                "updated_at": snapshot.observed_at,
            }
        )
    if invalidation_hit:
        return plan.model_copy(
            update={
                "status": "INVALIDATED",
                "transition_reason": "invalidation_breached_before_trigger",
                "revision": plan.revision + 1,
                "updated_at": snapshot.observed_at,
            }
        )
    if trigger_hit:
        return plan.model_copy(
            update={
                "status": "TRIGGERED",
                "transition_reason": "deterministic_trigger_satisfied",
                "revision": plan.revision + 1,
                "updated_at": snapshot.observed_at,
            }
        )
    return plan


def transition_trigger_plan(
    plan: TriggerPlan,
    *,
    status: TriggerPlanStatus,
    reason: str,
    observed_at: datetime,
) -> TriggerPlan:
    if observed_at.tzinfo is None:
        raise ValueError("trigger transition clock must be timezone-aware")
    if plan.status in _TERMINAL:
        raise ValueError(f"terminal_trigger_plan_cannot_transition:{plan.status}")
    allowed = {
        "ARMED": {"TRIGGERED", "EXPIRED", "INVALIDATED", "TRIGGER_ORDER_UNRESOLVED"},
        "TRIGGERED": {"EXECUTION_CHECK", "REJECTED"},
        "EXECUTION_CHECK": {"FILLED", "REJECTED"},
    }
    if status not in allowed.get(plan.status, set()):
        raise ValueError(f"invalid_trigger_transition:{plan.status}->{status}")
    return plan.model_copy(
        update={
            "status": status,
            "transition_reason": reason,
            "revision": plan.revision + 1,
            "updated_at": observed_at,
        }
    )


_COLUMNS = """
trigger_plan_id, strategy_id, arm_id, instrument_id, status,
created_at, expires_at, trigger_payload, geometry_payload,
required_certificate_ids, max_spread_bps, origin_payload, transition_reason,
revision, updated_at
"""


def _row_to_plan(row) -> TriggerPlan:
    return TriggerPlan(
        trigger_plan_id=str(row[0]),
        strategy_id=str(row[1]),
        arm_id=str(row[2]),
        instrument_id=str(row[3]),
        status=cast(Any, str(row[4])),
        created_at=row[5],
        expires_at=row[6],
        trigger=TriggerCondition.model_validate(row[7]),
        geometry=AuthoritativeTradeGeometry.model_validate(row[8]),
        required_certificate_ids=tuple(row[9] or ()),
        max_spread_bps=Decimal(str(row[10])),
        origin=TriggerPlanOrigin.model_validate(row[11]),
        transition_reason=str(row[12]) if row[12] is not None else None,
        revision=int(row[13]),
        updated_at=row[14],
    )


class TriggerPlanRepository:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory=unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def create(self, plan: TriggerPlan) -> TriggerPlan:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                INSERT INTO omnix_trading_trigger_plans (
                    workspace_id, trigger_plan_id, strategy_id, arm_id,
                    instrument_id, status, created_at, expires_at,
                    trigger_payload, geometry_payload, required_certificate_ids,
                    max_spread_bps, origin_payload, transition_reason, revision
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT (workspace_id, trigger_plan_id) DO NOTHING
                RETURNING {_COLUMNS}
                """,
                (
                    self.context.workspace_id,
                    plan.trigger_plan_id,
                    plan.strategy_id,
                    plan.arm_id,
                    plan.instrument_id,
                    plan.status,
                    plan.created_at,
                    plan.expires_at,
                    json.dumps(plan.trigger.model_dump(mode="json")),
                    json.dumps(plan.geometry.model_dump(mode="json")),
                    json.dumps(list(plan.required_certificate_ids)),
                    plan.max_spread_bps,
                    json.dumps(plan.origin.model_dump(mode="json")),
                    plan.transition_reason,
                    plan.revision,
                ),
            ).fetchone()
            if row is None:
                row = uow.connection.execute(
                    f"""
                    SELECT {_COLUMNS}
                      FROM omnix_trading_trigger_plans
                     WHERE workspace_id = %s AND trigger_plan_id = %s
                    """,
                    (self.context.workspace_id, plan.trigger_plan_id),
                ).fetchone()
            uow.commit()
        return _row_to_plan(row)

    def active_for_strategy(self, strategy_id: str) -> list[TriggerPlan]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_COLUMNS}
                  FROM omnix_trading_trigger_plans
                 WHERE workspace_id = %s
                   AND strategy_id = %s
                   AND status IN ('ARMED', 'TRIGGERED', 'EXECUTION_CHECK')
                 ORDER BY updated_at, trigger_plan_id
                """,
                (self.context.workspace_id, strategy_id),
            ).fetchall()
        return [_row_to_plan(row) for row in rows]

    def save_transition(
        self,
        prior: TriggerPlan,
        updated: TriggerPlan,
    ) -> TriggerPlan:
        if updated.revision != prior.revision + 1:
            raise ValueError("trigger_plan_revision_must_advance_once")
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                UPDATE omnix_trading_trigger_plans
                   SET status = %s,
                       transition_reason = %s,
                       revision = %s,
                       updated_at = %s
                 WHERE workspace_id = %s
                   AND trigger_plan_id = %s
                   AND revision = %s
                RETURNING {_COLUMNS}
                """,
                (
                    updated.status,
                    updated.transition_reason,
                    updated.revision,
                    updated.updated_at,
                    self.context.workspace_id,
                    prior.trigger_plan_id,
                    prior.revision,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("trigger_plan_revision_conflict")
            uow.commit()
        return _row_to_plan(row)


def default_trigger_plan_repository() -> TriggerPlanRepository:
    return TriggerPlanRepository()


__all__ = [
    "AuthoritativeTradeGeometry",
    "TriggerCondition",
    "TriggerMarketSnapshot",
    "TriggerPlan",
    "TriggerPlanOrigin",
    "TriggerPlanRepository",
    "TriggerPlanStatus",
    "create_trigger_plan_id",
    "default_trigger_plan_repository",
    "evaluate_armed_trigger",
    "transition_trigger_plan",
]
