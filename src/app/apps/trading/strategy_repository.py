from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal, TYPE_CHECKING, Union, cast

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from app.persistence.errors import RevisionConflict
from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work

from .gapper_dataset import GapperUniverseSnapshot
from .strategies.models import (
    StrategyMode,
    StrategyRiskProfile,
)
from .strategies.registrations import STRATEGY_REGISTRY
from .strategies.registry import StrategyRegistry, UnknownStrategyKind
from .trade_logging import trade_log


class _StrategyConfigDocumentBase(BaseModel):
    """Validation shared by every registry's configuration document."""

    model_config = ConfigDict(extra="forbid")
    strategy_registry: ClassVar[StrategyRegistry]
    if TYPE_CHECKING:
        # Fields the registry's model adds (strategy_config_document_model).
        strategy_id: str
        parent_strategy_id: str | None
        strategy_kind: str
        strategy_version: str
        mode: StrategyMode
        config: Any

    @model_validator(mode="before")
    @classmethod
    def parse_config_for_its_kind(cls, data):
        # A configuration is parsed by its own kind's model, not guessed from
        # the union of every registered model.
        if isinstance(data, dict) and isinstance(data.get("config"), dict):
            kind = data.get("strategy_kind", cls.strategy_registry.kinds()[0])
            try:
                entry = cls.strategy_registry.get(str(kind))
            except UnknownStrategyKind:
                return data
            data = {**data, "config": entry.config_model.model_validate(data["config"])}
        return data

    @model_validator(mode="after")
    def validate_strategy_version_alignment(self):
        if self.parent_strategy_id == self.strategy_id:
            raise ValueError("strategy_cannot_parent_itself")
        if self.strategy_version != self.config.strategy_version:
            raise ValueError("strategy_version_mismatch_between_document_and_config")
        entry = type(self).strategy_registry.get(self.strategy_kind)
        if not isinstance(self.config, entry.config_model):
            raise ValueError("strategy_kind_config_mismatch")
        if self.mode not in entry.allowed_modes:
            raise ValueError(getattr(entry, "mode_rejection", "strategy_mode_not_allowed"))
        return self


def strategy_config_document_model(
    registry: StrategyRegistry,
    *,
    name: str = "TradingStrategyConfigDocument",
) -> type[_StrategyConfigDocumentBase]:
    """The persisted configuration document for every kind in ``registry``.

    ``strategy_kind`` accepts exactly the registered kinds and ``config`` their
    configuration models; the first registration is the default.
    """
    default = registry.get(registry.kinds()[0])
    models = registry.config_models()
    config_type = models[0] if len(models) == 1 else Union[models]  # noqa: UP007
    model = create_model(
        name,
        __base__=_StrategyConfigDocumentBase,
        __module__=__name__,
        strategy_id=(str, Field(min_length=1, max_length=200)),
        parent_strategy_id=(str | None, Field(default=None, min_length=1, max_length=200)),
        account_id=(str, Field(min_length=1, max_length=200)),
        strategy_kind=(Literal[registry.kinds()], default.kind),
        strategy_version=(str, "1.0.0"),
        mode=(StrategyMode, "off"),
        active_universe_id=(str | None, None),
        config=(config_type, Field(default_factory=default.config_model)),
        risk=(StrategyRiskProfile, Field(default_factory=StrategyRiskProfile)),
        enabled=(bool, True),
        archived_at=(datetime | None, None),
        archived_reason=(str | None, None),
        revision=(int, Field(default=1, ge=1)),
        created_at=(datetime | None, None),
        updated_at=(datetime | None, None),
    )
    model.strategy_registry = registry
    return model


if TYPE_CHECKING:
    class TradingStrategyConfigDocument(_StrategyConfigDocumentBase):
        """What type checkers see; the runtime model is built from the registry below."""

        strategy_id: str
        parent_strategy_id: str | None = None
        account_id: str
        strategy_kind: str = "gap_pullback_v1"
        strategy_version: str = "1.0.0"
        mode: StrategyMode = "off"
        active_universe_id: str | None = None
        # The kind's own configuration model (gap pullback, Stoch RSI, ...).
        config: Any = None
        risk: StrategyRiskProfile = Field(default_factory=StrategyRiskProfile)
        enabled: bool = True
        archived_at: datetime | None = None
        archived_reason: str | None = None
        revision: int = 1
        created_at: datetime | None = None
        updated_at: datetime | None = None
else:
    TradingStrategyConfigDocument = strategy_config_document_model(STRATEGY_REGISTRY)


class StrategyEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    event_id: str
    run_id: str | None = None
    instrument_id: str
    event_type: str
    state: str
    reason_code: str | None = None
    observed_at: datetime
    idempotency_key: str
    correlation_version: str | None = None
    strategy_revision: int | None = None
    session_id: str | None = None
    setup_id: str | None = None
    trade_attempt_id: str | None = None
    trade_intent_id: str | None = None
    risk_decision_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StrategyProtection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    protection_id: str
    account_id: str
    instrument_id: str
    entry_order_id: str
    exit_order_id: str | None = None
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    initial_stop_price: Decimal | None = Field(default=None, gt=0)
    initial_target_price: Decimal | None = Field(default=None, gt=0)
    mae_price: Decimal | None = Field(default=None, gt=0)
    mfe_price: Decimal | None = Field(default=None, gt=0)
    quantity: Decimal = Field(gt=0)
    status: Literal["pending_entry", "active", "exit_submitted", "closed", "cancelled", "quarantined"] = "pending_entry"
    trigger_reason: str | None = None
    revision: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _config(row) -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id=str(row[0]),
        parent_strategy_id=str(row[1]) if row[1] is not None else None,
        account_id=str(row[2]),
        strategy_kind=str(row[3]),
        strategy_version=str(row[4]),
        mode=cast(Any, str(row[5])),
        active_universe_id=str(row[6]) if row[6] is not None else None,
        config=STRATEGY_REGISTRY.get(str(row[3])).config_model.model_validate(row[7]),
        risk=StrategyRiskProfile.model_validate(row[8]),
        enabled=bool(row[9]),
        archived_at=row[10],
        archived_reason=str(row[11]) if row[11] is not None else None,
        revision=int(row[12]),
        created_at=row[13],
        updated_at=row[14],
    )


def _event(row) -> StrategyEvent:
    return StrategyEvent(
        strategy_id=row[0],
        event_id=row[1],
        run_id=row[2],
        instrument_id=row[3],
        event_type=row[4],
        state=row[5],
        reason_code=row[6],
        observed_at=row[7],
        idempotency_key=row[8],
        correlation_version=row[9],
        strategy_revision=row[10],
        session_id=row[11],
        setup_id=row[12],
        trade_attempt_id=row[13],
        trade_intent_id=row[14],
        risk_decision_id=row[15],
        payload=row[16],
    )


def _protection(row) -> StrategyProtection:
    return StrategyProtection(
        strategy_id=str(row[0]),
        protection_id=str(row[1]),
        account_id=str(row[2]),
        instrument_id=str(row[3]),
        entry_order_id=str(row[4]),
        exit_order_id=str(row[5]) if row[5] is not None else None,
        stop_price=Decimal(row[6]),
        target_price=Decimal(row[7]),
        initial_stop_price=Decimal(row[8]) if row[8] is not None else None,
        initial_target_price=Decimal(row[9]) if row[9] is not None else None,
        mae_price=Decimal(row[10]) if row[10] is not None else None,
        mfe_price=Decimal(row[11]) if row[11] is not None else None,
        quantity=Decimal(row[12]),
        status=cast(Any, str(row[13])),
        trigger_reason=str(row[14]) if row[14] is not None else None,
        revision=int(row[15]),
        created_at=row[16],
        updated_at=row[17],
    )


def _universe(row) -> GapperUniverseSnapshot:
    return GapperUniverseSnapshot.model_validate(
        {
            "universe_id": row[0],
            "session_date": row[1],
            "evaluation_time": row[2],
            "discovery_source": row[3],
            "source_locator": row[4],
            "source_candidate_symbols": row[5] or [],
            "source_member_dispositions": row[6] or [],
            "source_fingerprint": row[7],
            "candidates": row[8],
        }
    )


_CONFIG_COLUMNS = """
strategy_id, parent_strategy_id, account_id, strategy_kind, strategy_version, mode,
active_universe_id, config, risk, enabled, archived_at, archived_reason,
revision, created_at, updated_at
"""
_EVENT_COLUMNS = """
strategy_id, event_id, run_id, instrument_id, event_type,
state, reason_code, observed_at, idempotency_key,
correlation_version, strategy_revision, session_id, setup_id,
trade_attempt_id, trade_intent_id, risk_decision_id, payload
"""
_PROTECTION_COLUMNS = """
strategy_id, protection_id, account_id, instrument_id, entry_order_id,
exit_order_id, stop_price, target_price, initial_stop_price, initial_target_price,
mae_price, mfe_price, quantity, status, trigger_reason, revision, created_at, updated_at
"""
_UNIVERSE_COLUMNS = """
universe_id, session_date, evaluation_time, discovery_source,
source_locator, source_candidate_symbols, source_member_dispositions,
source_fingerprint, candidates
"""


class TradingStrategyRepository:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory=unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    @contextmanager
    def exclusive_pass(self, strategy_id: str) -> Iterator[bool]:
        """Hold the strategy's pass lock for one configuration pass; yields whether it was acquired.

        The strategy monitor and the strategy runner can run in different
        processes. A session advisory lock on a dedicated connection, held
        while a pass runs, keeps two passes of one configuration from ever
        overlapping (for instance while its execution owner is switched).
        Closing the connection releases it.
        """
        unit = self.uow_factory()
        database = getattr(unit, "database", None)
        if database is None:
            from app.persistence.database import default_database

            database = default_database()
        key = f"omnix:trading:strategy-pass:{self.context.workspace_id}:{strategy_id}"
        with database.dedicated_connection() as connection:
            acquired = bool(
                connection.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
            )
            connection.commit()
            yield acquired

    def create_config(self, document: TradingStrategyConfigDocument) -> TradingStrategyConfigDocument:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                INSERT INTO omnix_trading_strategy_configs (
                    workspace_id, strategy_id, parent_strategy_id, account_id, owner_user_id,
                    strategy_kind, strategy_version, mode, active_universe_id,
                    config, risk, enabled
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                RETURNING {_CONFIG_COLUMNS}
                """,
                (
                    self.context.workspace_id,
                    document.strategy_id,
                    document.parent_strategy_id,
                    document.account_id,
                    self.context.user_id,
                    document.strategy_kind,
                    document.strategy_version,
                    document.mode,
                    document.active_universe_id,
                    json.dumps(document.config.model_dump(mode="json")),
                    json.dumps(document.risk.model_dump(mode="json")),
                    document.enabled,
                ),
            ).fetchone()
            uow.commit()
            return _config(row)

    def update_config(
        self,
        strategy_id: str,
        document: TradingStrategyConfigDocument,
        *,
        expected_revision: int,
    ) -> TradingStrategyConfigDocument:
        if strategy_id != document.strategy_id:
            raise ValueError("strategy_id_mismatch")
        if document.archived_at is not None:
            raise ValueError("archived_strategy_is_read_only")
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                UPDATE omnix_trading_strategy_configs
                   SET parent_strategy_id = %s, account_id = %s, strategy_kind = %s, strategy_version = %s,
                       mode = %s, active_universe_id = %s, config = %s::jsonb,
                       risk = %s::jsonb, enabled = %s,
                       revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND strategy_id = %s AND revision = %s
                   AND archived_at IS NULL
                RETURNING {_CONFIG_COLUMNS}
                """,
                (
                    document.parent_strategy_id,
                    document.account_id,
                    document.strategy_kind,
                    document.strategy_version,
                    document.mode,
                    document.active_universe_id,
                    json.dumps(document.config.model_dump(mode="json")),
                    json.dumps(document.risk.model_dump(mode="json")),
                    document.enabled,
                    self.context.workspace_id,
                    strategy_id,
                    expected_revision,
                ),
            ).fetchone()
            if row is None:
                raise RevisionConflict("stale trading strategy configuration")
            uow.commit()
            return _config(row)

    def delete_config(self, strategy_id: str, *, expected_revision: int) -> None:
        """Soft-archive a safely stopped strategy without deleting evidence."""

        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT mode, revision, archived_at
                  FROM omnix_trading_strategy_configs
                 WHERE workspace_id = %s AND strategy_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, strategy_id),
            ).fetchone()
            if row is None:
                raise ValueError("strategy_config_not_found")
            if int(row[1]) != expected_revision:
                raise RevisionConflict("stale trading strategy configuration")
            if row[2] is not None:
                raise ValueError("strategy_already_archived")
            if str(row[0]) == "auto_paper":
                raise ValueError("disable_auto_paper_before_archive")
            active = uow.connection.execute(
                """
                SELECT COUNT(*)
                  FROM omnix_trading_strategy_protections
                 WHERE workspace_id = %s AND strategy_id = %s
                   AND status IN ('pending_entry', 'active', 'exit_submitted')
                """,
                (self.context.workspace_id, strategy_id),
            ).fetchone()
            if active is not None and int(active[0]) > 0:
                raise ValueError("close_strategy_protections_before_archive")
            archived = uow.connection.execute(
                """
                UPDATE omnix_trading_strategy_configs
                   SET mode = 'off', enabled = FALSE,
                       archived_at = CURRENT_TIMESTAMP,
                       archived_reason = 'operator_archive',
                       revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND strategy_id = %s
                   AND revision = %s AND archived_at IS NULL
                RETURNING strategy_id
                """,
                (self.context.workspace_id, strategy_id, expected_revision),
            ).fetchone()
            if archived is None:
                raise RevisionConflict("stale trading strategy configuration")
            uow.commit()

    def list_configs(self, *, active_only: bool = False) -> list[TradingStrategyConfigDocument]:
        predicate = "AND archived_at IS NULL AND enabled = TRUE AND mode <> 'off'" if active_only else ""
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_CONFIG_COLUMNS}
                  FROM omnix_trading_strategy_configs
                 WHERE workspace_id = %s {predicate}
                 ORDER BY (archived_at IS NOT NULL), updated_at DESC, strategy_id
                """,
                (self.context.workspace_id,),
            ).fetchall()
        return [_config(row) for row in rows]

    def get_config(self, strategy_id: str) -> TradingStrategyConfigDocument:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"SELECT {_CONFIG_COLUMNS} FROM omnix_trading_strategy_configs WHERE workspace_id = %s AND strategy_id = %s",
                (self.context.workspace_id, strategy_id),
            ).fetchone()
        if row is None:
            raise ValueError("strategy_config_not_found")
        return _config(row)

    def save_universe(self, snapshot: GapperUniverseSnapshot) -> GapperUniverseSnapshot:
        with self.uow_factory() as uow:
            existing = uow.connection.execute(
                """
                SELECT universe_id, source_fingerprint
                  FROM omnix_trading_gapper_universes
                 WHERE workspace_id = %s AND universe_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, snapshot.universe_id),
            ).fetchone()
            if existing is not None:
                if str(existing[1]) != snapshot.source_fingerprint:
                    raise ValueError("gapper_universe_id_payload_mismatch")
                return snapshot
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_gapper_universes (
                    workspace_id, universe_id, session_date, evaluation_time,
                    discovery_source, source_locator, source_candidate_symbols,
                    source_member_dispositions, source_fingerprint, candidates
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)
                """,
                (
                    self.context.workspace_id,
                    snapshot.universe_id,
                    snapshot.session_date,
                    snapshot.evaluation_time,
                    snapshot.discovery_source,
                    snapshot.source_locator,
                    json.dumps(list(snapshot.source_candidate_symbols)),
                    json.dumps([
                        item.model_dump(mode="json")
                        for item in snapshot.source_member_dispositions
                    ]),
                    snapshot.source_fingerprint,
                    json.dumps([item.model_dump(mode="json") for item in snapshot.candidates]),
                ),
            )
            uow.commit()
        return snapshot

    def get_universe(self, universe_id: str) -> GapperUniverseSnapshot:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"SELECT {_UNIVERSE_COLUMNS} FROM omnix_trading_gapper_universes WHERE workspace_id = %s AND universe_id = %s",
                (self.context.workspace_id, universe_id),
            ).fetchone()
        if row is None:
            raise ValueError("gapper_universe_not_found")
        return _universe(row)

    def list_universes(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[GapperUniverseSnapshot]:
        if end_date < start_date:
            raise ValueError("universe_end_date_precedes_start_date")
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_UNIVERSE_COLUMNS}
                  FROM omnix_trading_gapper_universes
                 WHERE workspace_id = %s
                   AND session_date >= %s AND session_date <= %s
                 ORDER BY session_date, evaluation_time, universe_id
                """,
                (self.context.workspace_id, start_date, end_date),
            ).fetchall()
        return [_universe(row) for row in rows]

    def append_event(self, event: StrategyEvent) -> bool:
        with self.uow_factory() as uow:
            inserted = uow.connection.execute(
                """
                INSERT INTO omnix_trading_strategy_events (
                    workspace_id, strategy_id, event_id, run_id, instrument_id,
                    event_type, state, reason_code, observed_at, idempotency_key,
                    correlation_version, strategy_revision, session_id, setup_id,
                    trade_attempt_id, trade_intent_id, risk_decision_id, payload
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (workspace_id, strategy_id, idempotency_key) DO NOTHING
                RETURNING event_id
                """,
                (
                    self.context.workspace_id,
                    event.strategy_id,
                    event.event_id,
                    event.run_id,
                    event.instrument_id,
                    event.event_type,
                    event.state,
                    event.reason_code,
                    event.observed_at,
                    event.idempotency_key,
                    event.correlation_version,
                    event.strategy_revision,
                    event.session_id,
                    event.setup_id,
                    event.trade_attempt_id,
                    event.trade_intent_id,
                    event.risk_decision_id,
                    json.dumps(event.payload, default=str),
                ),
            ).fetchone()
            uow.commit()
        persisted = inserted is not None
        if persisted and event.event_type != "research_llm":
            trade_log(
                "auto_trading",
                "strategy_event",
                persisted=True,
                strategy_id=event.strategy_id,
                run_id=event.run_id,
                event_id=event.event_id,
                instrument_id=event.instrument_id,
                event_type=event.event_type,
                state=event.state,
                reason_code=event.reason_code,
                observed_at=event.observed_at,
                idempotency_key=event.idempotency_key,
                correlation_version=event.correlation_version,
                strategy_revision=event.strategy_revision,
                session_id=event.session_id,
                setup_id=event.setup_id,
                trade_attempt_id=event.trade_attempt_id,
                trade_intent_id=event.trade_intent_id,
                risk_decision_id=event.risk_decision_id,
                payload=event.payload,
            )
        return persisted

    def recent_events(self, strategy_id: str, limit: int = 200) -> list[StrategyEvent]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_EVENT_COLUMNS}
                  FROM omnix_trading_strategy_events
                 WHERE workspace_id = %s AND strategy_id = %s
                 ORDER BY observed_at DESC, created_at DESC LIMIT %s
                """,
                (self.context.workspace_id, strategy_id, limit),
            ).fetchall()
        return [_event(row) for row in rows]

    def events_between(
        self,
        strategy_id: str,
        *,
        start_time: datetime,
        end_time: datetime,
        limit: int = 50_000,
    ) -> list[StrategyEvent]:
        """Return the exact bounded strategy-event population without a type filter."""

        if start_time.tzinfo is None or end_time.tzinfo is None:
            raise ValueError("strategy event boundaries must be timezone-aware")
        if end_time <= start_time:
            raise ValueError("strategy event end_time must follow start_time")
        if limit < 1 or limit > 100_000:
            raise ValueError("strategy event limit must be between 1 and 100000")
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_EVENT_COLUMNS}
                  FROM omnix_trading_strategy_events
                 WHERE workspace_id = %s AND strategy_id = %s
                   AND observed_at >= %s AND observed_at < %s
                 ORDER BY observed_at, created_at, event_id
                 LIMIT %s
                """,
                (
                    self.context.workspace_id,
                    strategy_id,
                    start_time,
                    end_time,
                    limit,
                ),
            ).fetchall()
        return [_event(row) for row in rows]

    def events_by_types_between(
        self,
        strategy_id: str,
        *,
        event_types: list[str] | tuple[str, ...],
        start_time: datetime,
        end_time: datetime,
        limit: int = 10_000,
    ) -> list[StrategyEvent]:
        if start_time.tzinfo is None or end_time.tzinfo is None:
            raise ValueError("qualification event boundaries must be timezone-aware")
        if end_time <= start_time:
            raise ValueError("qualification event end_time must follow start_time")
        normalized_types = [str(value).strip() for value in event_types if str(value).strip()]
        if not normalized_types:
            return []
        if limit < 1 or limit > 50_000:
            raise ValueError("qualification event limit must be between 1 and 50000")
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_EVENT_COLUMNS}
                  FROM omnix_trading_strategy_events
                 WHERE workspace_id = %s AND strategy_id = %s
                   AND event_type = ANY(%s)
                   AND observed_at >= %s AND observed_at < %s
                 ORDER BY observed_at, created_at, event_id
                 LIMIT %s
                """,
                (
                    self.context.workspace_id,
                    strategy_id,
                    normalized_types,
                    start_time,
                    end_time,
                    limit,
                ),
            ).fetchall()
        return [_event(row) for row in rows]

    def entry_events_between(
        self,
        strategy_id: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> list[StrategyEvent]:
        if start_time.tzinfo is None or end_time.tzinfo is None:
            raise ValueError("entry event boundaries must be timezone-aware")
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_EVENT_COLUMNS}
                  FROM omnix_trading_strategy_events
                 WHERE workspace_id = %s AND strategy_id = %s
                   AND event_type = 'entry_order_submitted'
                   AND observed_at >= %s AND observed_at < %s
                 ORDER BY observed_at, created_at, event_id
                """,
                (self.context.workspace_id, strategy_id, start_time, end_time),
            ).fetchall()
        return [_event(row) for row in rows]

    def daily_paper_pnl(
        self,
        account_id: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> Decimal:
        if start_time.tzinfo is None or end_time.tzinfo is None:
            raise ValueError("daily paper pnl boundaries must be timezone-aware")
        if end_time <= start_time:
            raise ValueError("daily paper pnl end_time must follow start_time")
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                  FROM omnix_trading_paper_ledger
                 WHERE workspace_id = %s AND account_id = %s
                   AND entry_type IN ('realized_pnl', 'commission')
                   AND created_at >= %s AND created_at < %s
                """,
                (
                    self.context.workspace_id,
                    account_id,
                    start_time,
                    end_time,
                ),
            ).fetchone()
        return Decimal(row[0]) if row is not None else Decimal("0")

    def save_protection(self, protection: StrategyProtection) -> StrategyProtection:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                INSERT INTO omnix_trading_strategy_protections (
                    workspace_id, strategy_id, protection_id, account_id,
                    instrument_id, entry_order_id, exit_order_id, stop_price,
                    target_price, initial_stop_price, initial_target_price,
                    mae_price, mfe_price, quantity, status, trigger_reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, strategy_id, entry_order_id) DO UPDATE
                   SET exit_order_id = EXCLUDED.exit_order_id,
                       stop_price = EXCLUDED.stop_price,
                       target_price = EXCLUDED.target_price,
                       initial_stop_price = COALESCE(omnix_trading_strategy_protections.initial_stop_price, EXCLUDED.initial_stop_price),
                       initial_target_price = COALESCE(EXCLUDED.initial_target_price, omnix_trading_strategy_protections.initial_target_price),
                       mae_price = EXCLUDED.mae_price,
                       mfe_price = EXCLUDED.mfe_price,
                       quantity = EXCLUDED.quantity,
                       status = EXCLUDED.status,
                       trigger_reason = EXCLUDED.trigger_reason,
                       revision = omnix_trading_strategy_protections.revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                RETURNING {_PROTECTION_COLUMNS}
                """,
                (
                    self.context.workspace_id,
                    protection.strategy_id,
                    protection.protection_id,
                    protection.account_id,
                    protection.instrument_id,
                    protection.entry_order_id,
                    protection.exit_order_id,
                    protection.stop_price,
                    protection.target_price,
                    protection.initial_stop_price,
                    protection.initial_target_price,
                    protection.mae_price,
                    protection.mfe_price,
                    protection.quantity,
                    protection.status,
                    protection.trigger_reason,
                ),
            ).fetchone()
            uow.commit()
        saved = _protection(row)
        trade_log(
            "auto_trading",
            "protection_saved",
            strategy_id=saved.strategy_id,
            protection_id=saved.protection_id,
            account_id=saved.account_id,
            instrument_id=saved.instrument_id,
            entry_order_id=saved.entry_order_id,
            exit_order_id=saved.exit_order_id,
            stop_price=saved.stop_price,
            target_price=saved.target_price,
            initial_stop_price=saved.initial_stop_price,
            initial_target_price=saved.initial_target_price,
            mae_price=saved.mae_price,
            mfe_price=saved.mfe_price,
            quantity=saved.quantity,
            status=saved.status,
            trigger_reason=saved.trigger_reason,
            revision=saved.revision,
            created_at=saved.created_at,
            updated_at=saved.updated_at,
        )
        return saved

    def list_protections(self, strategy_id: str, *, active_only: bool = True) -> list[StrategyProtection]:
        predicate = "AND status IN ('pending_entry', 'active', 'exit_submitted')" if active_only else ""
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_PROTECTION_COLUMNS}
                  FROM omnix_trading_strategy_protections
                 WHERE workspace_id = %s AND strategy_id = %s {predicate}
                 ORDER BY created_at
                """,
                (self.context.workspace_id, strategy_id),
            ).fetchall()
        return [_protection(row) for row in rows]


def default_strategy_repository() -> TradingStrategyRepository:
    return TradingStrategyRepository()
