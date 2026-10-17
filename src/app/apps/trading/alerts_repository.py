"""Trading alerts in PostgreSQL: the repository and its row mapping (moved out of alerts.py)."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast
from pydantic import BaseModel, ValidationError
from app.persistence.errors import RevisionConflict
from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.transaction_binding import share_transaction
from app.persistence.unit_of_work import unit_of_work
from .alert_conditions import (
    AlertConditionSpec,
)
from .alerts_delivery import enqueue_alert_deliveries
from .alerts_message import message_values, render_alert_message
from .alerts_evaluation import AlertConditionOutcome
from .alerts import (
    AlertEvaluationContext,
    AlertOutcomeRecord,
    NOTIFICATION_PARAMETER_FIELDS,
    TradingAlert,
    TradingAlertCreate,
    TradingAlertEvaluation,
    TradingAlertParameters,
    TradingAlertTrigger,
    TradingAlertUpdate,
    UnitOfWorkFactory,
    alert_trigger_key,
    cooldown_elapsed,
    frequency_allows,
    logger,
    policy_json,
    pushed_price_outcome,
    watchlist_id_of,
)

def _condition(row) -> AlertConditionSpec:
    return AlertConditionSpec.model_validate(
        {
            "source": row[2],
            "operator": str(row[3]),
            "target": row[4],
            "amount": Decimal(row[5]) if row[5] is not None else None,
            "bars": int(row[6]) if row[6] is not None else None,
        }
    )


def _split_parameters(parameters: TradingAlertParameters, webhook_ref: str | None) -> tuple[str, str]:
    """(condition_parameters, notification_settings) JSON for storage."""
    data = parameters.model_dump(mode="json")
    notification = {name: data.pop(name) for name in NOTIFICATION_PARAMETER_FIELDS}
    if webhook_ref is not None:
        notification["webhook_ref"] = webhook_ref
    return json.dumps(data), json.dumps(notification)


def _validation_reason(exc: ValidationError) -> str:
    """Where and why a stored row failed to read, without the stored values."""
    reasons = []
    for error in exc.errors()[:3]:
        location = ".".join(str(part) for part in error.get("loc", ()))
        reasons.append(f"{location or 'alert'}: {error.get('type', 'invalid')}")
    return "; ".join(reasons) or "invalid"


def _alert(row, conditions: list[Any] | None = None) -> TradingAlert:
    # Rows are trusted on read: only normalising validators run (see _AlertContract).
    notification = dict(row[17] or {})
    webhook_ref = notification.pop("webhook_ref", None)
    alert = TradingAlert(
        alert_id=str(row[0]),
        instrument_id=str(row[1]),
        binding_id=str(row[2]) if row[2] is not None else None,
        condition_type=cast(Any, str(row[3])),
        threshold=Decimal(row[4]),
        parameters=cast(Any, {**dict(row[5] or {}), **notification}),
        evaluation_policy=cast(Any, dict(row[6] or {})),
        enabled=bool(row[7]),
        cooldown_seconds=int(row[8]),
        expires_at=row[9],
        last_observed_price=Decimal(row[10]) if row[10] is not None else None,
        last_observed_value=Decimal(row[11]) if row[11] is not None else None,
        last_triggered_at=row[12],
        revision=int(row[13]),
        created_at=row[14],
        updated_at=row[15],
        frequency=cast(Any, str(row[16])),
        definition_revision=int(row[18]),
        conditions=conditions or [],
    )
    alert._webhook_ref = str(webhook_ref) if webhook_ref else None
    return alert


def _stub_alert(row) -> TradingAlert:
    """The identity of a row that cannot be read, for responses about it."""
    return TradingAlert(
        alert_id=str(row[0]),
        instrument_id=str(row[1]),
        enabled=False,
        revision=int(row[13]),
        definition_revision=int(row[18]),
    )


class TradingAlertUnreadable(BaseModel):
    """A stored alert that no longer reads; archive it with its revision."""

    alert_id: str
    instrument_id: str
    revision: int
    reason: str


@dataclass(frozen=True)
class AlertLockState:
    exists: bool
    webhook_ref: str | None


@dataclass(frozen=True)
class AlertListing:
    alerts: list[TradingAlert]
    unreadable: list[TradingAlertUnreadable]


def _trigger(row) -> TradingAlertTrigger:
    return TradingAlertTrigger(
        trigger_id=str(row[0]),
        alert_id=str(row[1]),
        instrument_id=str(row[2]),
        binding_id=str(row[3]) if row[3] is not None else None,
        provider=str(row[4]) if row[4] is not None else None,
        observed_value=Decimal(row[5]),
        observed_price=Decimal(row[6]),
        threshold=Decimal(row[7]),
        condition_type=cast(Any, str(row[8])),
        observed_at=row[9],
        evaluated_at=row[10],
        idempotency_key=str(row[11]),
        payload=dict(row[12] or {}),
    )


_ALERT_COLUMNS = """
    alert_id, instrument_id, binding_id, condition_type, threshold,
    condition_parameters, evaluation_policy, enabled, cooldown_seconds,
    expires_at, last_observed_price, last_observed_value, last_triggered_at,
    revision, created_at, updated_at, frequency, notification_settings,
    definition_revision
"""
_TRIGGER_COLUMNS = """
    trigger_id, alert_id, instrument_id, binding_id, provider,
    observed_value, observed_price, threshold, condition_type,
    observed_at, evaluated_at, idempotency_key, payload
"""
_CONDITION_COLUMNS = "alert_id, position, source, operator, target, amount, bars"


class TradingAlertRepository:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory: UnitOfWorkFactory = unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def _condition_rows(self, connection, alert_ids: Iterable[str]) -> dict[str, list[Any]]:
        ids = list(alert_ids)
        if not ids:
            return {}
        rows = connection.execute(
            f"""
            SELECT {_CONDITION_COLUMNS}
              FROM omnix_trading_alert_conditions
             WHERE workspace_id = %s AND alert_id = ANY(%s)
             ORDER BY alert_id, position
            """,
            (self.context.workspace_id, ids),
        ).fetchall()
        grouped: dict[str, list[Any]] = {}
        for row in rows:
            grouped.setdefault(str(row[0]), []).append(row)
        return grouped

    def _stored_conditions(self, connection, alert_id: str) -> list[AlertConditionSpec] | None:
        """An alert's stored conditions; None when they cannot be read."""
        try:
            return [_condition(row) for row in self._condition_rows(connection, [alert_id]).get(alert_id, [])]
        except ValidationError:
            return None

    def _listing(self, connection, rows) -> AlertListing:
        condition_rows = self._condition_rows(connection, (str(row[0]) for row in rows))
        alerts: list[TradingAlert] = []
        unreadable: list[TradingAlertUnreadable] = []
        for row in rows:
            try:
                conditions = [_condition(item) for item in condition_rows.get(str(row[0]), [])]
                alerts.append(_alert(row, conditions))
            except ValidationError as exc:
                # One unreadable row (or condition row) must not hide every other
                # alert or stop the monitor; it is reported so it can be archived.
                reason = _validation_reason(exc)
                logger.warning("trading_alert_unreadable alert_id=%s reason=%s", row[0], reason)
                unreadable.append(
                    TradingAlertUnreadable(
                        alert_id=str(row[0]), instrument_id=str(row[1]), revision=int(row[13]), reason=reason
                    )
                )
        return AlertListing(alerts, unreadable)

    def _alerts(self, connection, rows) -> list[TradingAlert]:
        return self._listing(connection, rows).alerts

    def get(self, alert_id: str) -> TradingAlert | None:
        """The alert, or None when it does not exist or cannot be read."""
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                SELECT {_ALERT_COLUMNS}
                  FROM omnix_trading_alerts
                 WHERE workspace_id = %s AND alert_id = %s
                """,
                (self.context.workspace_id, alert_id),
            ).fetchone()
            if row is None:
                return None
            alerts = self._alerts(uow.connection, [row])
            return alerts[0] if alerts else None

    def _write_conditions(self, connection, alert_id: str, conditions: list[AlertConditionSpec]) -> None:
        connection.execute(
            "DELETE FROM omnix_trading_alert_conditions WHERE workspace_id = %s AND alert_id = %s",
            (self.context.workspace_id, alert_id),
        )
        for position, condition in enumerate(conditions):
            connection.execute(
                """
                INSERT INTO omnix_trading_alert_conditions (
                    workspace_id, alert_id, position, source, operator, target, amount, bars
                ) VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s)
                """,
                (
                    self.context.workspace_id,
                    alert_id,
                    position,
                    condition.source.model_dump_json(),
                    condition.operator,
                    condition.target.model_dump_json() if condition.target is not None else None,
                    condition.amount,
                    condition.bars,
                ),
            )

    def list_alerts_report(self, limit: int = 200) -> AlertListing:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_ALERT_COLUMNS}
                  FROM omnix_trading_alerts
                 WHERE workspace_id = %s
                 ORDER BY updated_at DESC, alert_id
                 LIMIT %s
                """,
                (self.context.workspace_id, limit),
            ).fetchall()
            return self._listing(uow.connection, rows)

    def list_alerts(self, limit: int = 200) -> list[TradingAlert]:
        return self.list_alerts_report(limit).alerts

    @contextmanager
    def alert_transaction(self, alert_id: str) -> Iterator[AlertLockState]:
        """One transaction around an alert write and its protected-store changes.

        Holds a per-(workspace, alert) PostgreSQL advisory lock across
        processes, locks the row if it exists, and yields what it currently
        references. Repository calls inside join this transaction; it commits
        when the block ends without an error.
        """
        with self.uow_factory() as uow:
            # Two keys hashed separately, so no workspace/alert pair can alias another
            # through its separators; a hash collision only serialises two writes.
            uow.connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                (f"omnix-trading-alert-workspace:{self.context.workspace_id}", alert_id),
            )
            row = uow.connection.execute(
                """
                SELECT notification_settings->>'webhook_ref'
                  FROM omnix_trading_alerts
                 WHERE workspace_id = %s AND alert_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, alert_id),
            ).fetchone()
            with share_transaction(uow):
                yield AlertLockState(exists=row is not None, webhook_ref=row[0] if row else None)
            uow.commit()

    def create(self, request: TradingAlertCreate, *, webhook_ref: str | None) -> TradingAlert:
        """Create an alert. webhook_ref names its webhook in the protected store (the API sets it)."""
        condition_parameters, notification_settings = _split_parameters(request.parameters, webhook_ref)
        with self.uow_factory() as uow:
            if uow.connection.execute(
                "SELECT 1 FROM omnix_trading_alerts WHERE workspace_id = %s AND alert_id = %s",
                (self.context.workspace_id, request.alert_id),
            ).fetchone() is not None:
                raise RevisionConflict(f"Trading alert already exists: {request.alert_id}")
            row = uow.connection.execute(
                f"""
                INSERT INTO omnix_trading_alerts (
                    workspace_id, alert_id, owner_user_id, instrument_id, binding_id,
                    condition_type, threshold, condition_parameters, evaluation_policy,
                    cooldown_seconds, expires_at, frequency, notification_settings
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb)
                RETURNING {_ALERT_COLUMNS}
                """,
                (
                    self.context.workspace_id,
                    request.alert_id,
                    self.context.user_id,
                    request.instrument_id,
                    request.binding_id,
                    request.condition_type,
                    request.threshold,
                    condition_parameters,
                    policy_json(request.evaluation_policy),
                    request.cooldown_seconds,
                    request.expires_at,
                    request.frequency,
                    notification_settings,
                ),
            ).fetchone()
            self._write_conditions(uow.connection, request.alert_id, request.conditions)
            uow.commit()
            return _alert(row, request.conditions)

    def update(
        self,
        alert_id: str,
        request: TradingAlertUpdate,
        expected_revision: int,
        *,
        webhook_ref: str | None,
    ) -> TradingAlert:
        """Replace an alert at expected_revision. webhook_ref as for create."""
        condition_parameters, notification_settings = _split_parameters(request.parameters, webhook_ref)
        with self.uow_factory() as uow:
            previous_conditions = self._stored_conditions(uow.connection, alert_id)
            was_enabled = uow.connection.execute(
                "SELECT enabled FROM omnix_trading_alerts WHERE workspace_id = %s AND alert_id = %s",
                (self.context.workspace_id, alert_id),
            ).fetchone()
            row = uow.connection.execute(
                f"""
                UPDATE omnix_trading_alerts
                   SET instrument_id = %s, binding_id = %s, condition_type = %s,
                       threshold = %s, condition_parameters = %s::jsonb,
                       evaluation_policy = %s::jsonb, enabled = %s,
                       cooldown_seconds = %s, expires_at = %s, frequency = %s,
                       notification_settings = %s::jsonb,
                       last_observed_price = NULL, last_observed_value = NULL,
                       last_triggered_at = NULL, revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND alert_id = %s AND revision = %s
                RETURNING {_ALERT_COLUMNS}
                """,
                (
                    request.instrument_id,
                    request.binding_id,
                    request.condition_type,
                    request.threshold,
                    condition_parameters,
                    policy_json(request.evaluation_policy),
                    request.enabled,
                    request.cooldown_seconds,
                    request.expires_at,
                    request.frequency,
                    notification_settings,
                    self.context.workspace_id,
                    alert_id,
                    expected_revision,
                ),
            ).fetchone()
            if row is None:
                raise RevisionConflict(
                    f"Trading alert expected revision {expected_revision}: {alert_id}"
                )
            if previous_conditions != request.conditions:
                # The lifecycle trigger keeps observation history and the definition
                # revision when the alert's own columns are unchanged; a new
                # condition set is a new definition with a new history.
                row = uow.connection.execute(
                    f"""
                    UPDATE omnix_trading_alerts
                       SET last_observed_price = NULL, last_observed_value = NULL,
                           last_triggered_at = NULL,
                           definition_revision = definition_revision + 1
                     WHERE workspace_id = %s AND alert_id = %s
                    RETURNING {_ALERT_COLUMNS}
                    """,
                    (self.context.workspace_id, alert_id),
                ).fetchone()
                self._write_conditions(uow.connection, alert_id, request.conditions)
            if request.enabled and was_enabled is not None and not was_enabled[0] and watchlist_id_of(request.instrument_id) is not None:
                # Re-enabling a watchlist alert re-arms it on every symbol ('once' fired there, cooldowns).
                uow.connection.execute(
                    "DELETE FROM omnix_trading_alert_symbol_states WHERE workspace_id = %s AND alert_id = %s",
                    (self.context.workspace_id, alert_id),
                )
            uow.commit()
            return _alert(row, request.conditions)

    def archive(self, alert_id: str, expected_revision: int) -> TradingAlert:
        with self.uow_factory() as uow:
            conditions = self._stored_conditions(uow.connection, alert_id)
            row = uow.connection.execute(
                f"""
                DELETE FROM omnix_trading_alerts
                 WHERE workspace_id = %s AND alert_id = %s AND revision = %s
                RETURNING {_ALERT_COLUMNS}
                """,
                (self.context.workspace_id, alert_id, expected_revision),
            ).fetchone()
            if row is None:
                raise RevisionConflict(
                    f"Trading alert expected revision {expected_revision}: {alert_id}"
                )
            uow.commit()
            try:
                archived = _alert(row, conditions)
            except ValidationError:
                archived = _stub_alert(row)
            return archived.model_copy(update={"enabled": False})

    def list_triggers(self, limit: int = 200) -> list[TradingAlertTrigger]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_TRIGGER_COLUMNS}
                  FROM omnix_trading_alert_triggers
                 WHERE workspace_id = %s
                 ORDER BY observed_at DESC, evaluated_at DESC, trigger_id
                 LIMIT %s
                """,
                (self.context.workspace_id, limit),
            ).fetchall()
            return [_trigger(row) for row in rows]

    def _locked_alerts(self, connection, instrument_id: str, evaluated_at: datetime) -> list[TradingAlert]:
        rows = connection.execute(
            f"""
            SELECT {_ALERT_COLUMNS}
              FROM omnix_trading_alerts
             WHERE workspace_id = %s AND instrument_id = %s AND enabled = TRUE
               AND (expires_at IS NULL OR expires_at > %s)
             ORDER BY alert_id
             FOR UPDATE
            """,
            (self.context.workspace_id, instrument_id, evaluated_at),
        ).fetchall()
        return self._alerts(connection, rows)

    def evaluate(self, evaluation: TradingAlertEvaluation) -> list[TradingAlertTrigger]:
        """Evaluate a pushed price; see ``pushed_price_outcome`` for which alerts qualify."""
        context = AlertEvaluationContext(
            instrument_id=evaluation.instrument_id,
            interval=evaluation.interval,
            evaluated_at=evaluation.evaluated_at,
            binding_id=evaluation.binding_id,
            resolved_binding_id=evaluation.resolved_binding_id,
            provider=evaluation.provider,
        )
        triggers: list[TradingAlertTrigger] = []
        with self.uow_factory() as uow:
            for alert in self._locked_alerts(uow.connection, evaluation.instrument_id, evaluation.evaluated_at):
                if alert.watchlist_id is not None or (alert.binding_id and alert.binding_id != evaluation.binding_id):
                    continue
                if alert.evaluation_policy.interval != evaluation.interval:
                    continue
                if alert.frequency in {"once_per_bar", "once_per_bar_close"}:
                    continue  # a pushed price carries no bar
                outcome = pushed_price_outcome(alert, evaluation)
                if outcome is None:
                    continue
                trigger = self._apply(uow.connection, alert, outcome, context)
                if trigger is not None:
                    triggers.append(trigger)
            uow.commit()
        return triggers

    def record_outcomes(
        self,
        context: AlertEvaluationContext,
        outcomes: Iterable[AlertOutcomeRecord],
    ) -> list[TradingAlertTrigger]:
        """Apply frequency, cooldown and idempotency to outcomes computed on bars."""
        by_alert: Mapping[str, AlertOutcomeRecord] = {record.alert_id: record for record in outcomes}
        triggers: list[TradingAlertTrigger] = []
        if not by_alert:
            return triggers
        with self.uow_factory() as uow:
            for alert in self._locked_alerts(uow.connection, context.instrument_id, context.evaluated_at):
                record = by_alert.get(alert.alert_id)
                # An alert edited since its bars were evaluated waits for the next pass.
                if record is None or record.revision != alert.revision or alert.watchlist_id is not None:
                    continue
                trigger = self._apply(uow.connection, alert, record.outcome, context)
                if trigger is not None:
                    triggers.append(trigger)
            uow.commit()
        return triggers

    def record_watchlist_outcomes(
        self,
        context: AlertEvaluationContext,
        outcomes: Iterable[AlertOutcomeRecord],
    ) -> list[TradingAlertTrigger]:
        """Watchlist alerts' outcomes on one symbol (``context.instrument_id``), with that symbol's own state (TVP-1.7).

        Frequency, cooldown and ``once`` apply per symbol: the alert stays on when one symbol fires once, and its
        trigger key names the symbol, so each symbol fires on its own.
        """
        records = list(outcomes)
        triggers: list[TradingAlertTrigger] = []
        if not records:
            return triggers
        symbol = context.instrument_id
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_ALERT_COLUMNS}
                  FROM omnix_trading_alerts
                 WHERE workspace_id = %s AND alert_id = ANY(%s) AND enabled = TRUE
                   AND (expires_at IS NULL OR expires_at > %s)
                 ORDER BY alert_id
                 FOR UPDATE
                """,
                (self.context.workspace_id, [record.alert_id for record in records], context.evaluated_at),
            ).fetchall()
            by_alert = {record.alert_id: record for record in records}
            for alert in self._alerts(uow.connection, rows):
                record = by_alert.get(alert.alert_id)
                # An alert edited since its bars were evaluated waits for the next pass.
                if record is None or record.revision != alert.revision or alert.watchlist_id is None:
                    continue
                trigger = self._apply_symbol(uow.connection, alert, symbol, record.outcome, context)
                if trigger is not None:
                    triggers.append(trigger)
            uow.commit()
        return triggers

    def _apply_symbol(
        self,
        connection,
        alert: TradingAlert,
        symbol: str,
        outcome: AlertConditionOutcome,
        context: AlertEvaluationContext,
    ) -> TradingAlertTrigger | None:
        final_only = alert.frequency == "once_per_bar_close" or not alert.evaluation_policy.allow_partial_bars
        if final_only and not outcome.bar_is_final:
            return None
        state = connection.execute(
            """
            SELECT alert_revision, definition_revision, last_triggered_at, fired_once
              FROM omnix_trading_alert_symbol_states
             WHERE workspace_id = %s AND alert_id = %s AND instrument_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, alert.alert_id, symbol),
        ).fetchone()
        # A state written under an older definition starts afresh; notification edits keep it, and re-enabling the
        # alert deletes it (``update``), so 'once' fires again on each symbol.
        current = state is not None and int(state[1]) == alert.definition_revision
        last_triggered_at = state[2] if current else None
        fired_once = current and bool(state[3])
        evaluated_at = context.evaluated_at
        should_trigger = (
            outcome.met
            and not fired_once
            and not (outcome.bar_is_final and alert.updated_at is not None and outcome.bar_end <= alert.updated_at)
            and cooldown_elapsed(last_triggered_at, evaluated_at, alert.cooldown_seconds)
            and frequency_allows(alert.frequency, last_triggered_at, evaluated_at)
        )
        inserted_trigger: TradingAlertTrigger | None = None
        if should_trigger:
            # The symbol is part of the key, so each symbol of the list fires on its own. 'once' is keyed by the
            # definition and bar, not the revision: the symbol's state says whether it fired, and a notification
            # edit (a new revision) must not fire it again.
            scope = f"{alert.alert_id}|{symbol}"
            if alert.frequency == "once":
                scope += "|" + outcome.bar_start.astimezone(timezone.utc).isoformat()
            key = alert_trigger_key(
                scope,
                alert.frequency,
                outcome,
                revision=alert.definition_revision,
                definition_revision=alert.definition_revision,
            )
            inserted_trigger = self._record_trigger(connection, alert.model_copy(update={"instrument_id": symbol}), outcome, context, key)
            if inserted_trigger is not None:
                last_triggered_at = evaluated_at
                fired_once = alert.frequency == "once"
        connection.execute(
            """
            INSERT INTO omnix_trading_alert_symbol_states (
                workspace_id, alert_id, instrument_id, alert_revision, definition_revision,
                last_observed_price, last_observed_value, last_triggered_at, fired_once
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, alert_id, instrument_id) DO UPDATE
               SET alert_revision = EXCLUDED.alert_revision,
                   definition_revision = EXCLUDED.definition_revision,
                   last_observed_price = EXCLUDED.last_observed_price,
                   last_observed_value = COALESCE(EXCLUDED.last_observed_value, omnix_trading_alert_symbol_states.last_observed_value),
                   last_triggered_at = EXCLUDED.last_triggered_at,
                   fired_once = EXCLUDED.fired_once,
                   updated_at = CURRENT_TIMESTAMP
            """,
            (
                self.context.workspace_id, alert.alert_id, symbol, alert.revision, alert.definition_revision,
                outcome.close, outcome.primary_value, last_triggered_at, fired_once,
            ),
        )
        if inserted_trigger is not None:
            # The alert's own row shows when any of its symbols last fired.
            connection.execute(
                "UPDATE omnix_trading_alerts SET last_triggered_at = %s WHERE workspace_id = %s AND alert_id = %s",
                (evaluated_at, self.context.workspace_id, alert.alert_id),
            )
        return inserted_trigger

    def _record_trigger(
        self,
        connection,
        alert: TradingAlert,
        outcome: AlertConditionOutcome,
        context: AlertEvaluationContext,
        key: str,
    ) -> TradingAlertTrigger | None:
        """Insert a trigger with this idempotency key and its outbox rows; None when the key was already used.

        For a watchlist alert ``alert`` is a copy whose instrument is the symbol that fired (TVP-1.7).
        """
        evaluated_at = context.evaluated_at
        primary_value = outcome.primary_value
        resolved_binding_id = context.resolved_binding_id or context.binding_id
        payload = {
            "instrument_id": alert.instrument_id,
            "condition_type": alert.condition_type,
            "frequency": alert.frequency,
            "conditions": [condition.model_dump(mode="json") for condition in alert.conditions],
            "observations": outcome.observation_payload(),
            "condition_parameters": alert.parameters.model_dump(mode="json"),
            "evaluation_policy": alert.evaluation_policy.model_dump(mode="json", exclude_none=True),
            "provider": context.provider,
            "requested_binding_id": context.binding_id,
            "resolved_binding_id": resolved_binding_id,
            "bar_start": outcome.bar_start.isoformat(),
            "bar_is_final": outcome.bar_is_final,
            "source_time": outcome.bar_end.isoformat(),
            "evaluation_time": evaluated_at.isoformat(),
            "previous_value": str(outcome.observations[0].source_previous) if outcome.observations else None,
            "observed_value": str(primary_value),
            "threshold": str(alert.threshold),
            "expires_at": alert.expires_at.isoformat() if alert.expires_at else None,
            "interval": context.interval,
        }
        # The message with its placeholders filled in, as every channel shows it (TVP-1.5).
        names, plots = message_values(alert, outcome, interval=context.interval, evaluated_at=evaluated_at)
        # Without a message of its own, a script alert says what the script's alert() or alertcondition says (TVP-11.4).
        template = alert.parameters.message.strip() or next((item.message for item in outcome.observations if item.message), "")
        payload["message"] = render_alert_message(template, names, plots)
        inserted = connection.execute(
            f"""
            INSERT INTO omnix_trading_alert_triggers (
                workspace_id, trigger_id, alert_id, instrument_id,
                binding_id, provider, observed_value, observed_price,
                threshold, condition_type, observed_at, evaluated_at,
                idempotency_key, payload
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb
            )
            ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
            RETURNING {_TRIGGER_COLUMNS}
            """,
            (
                self.context.workspace_id,
                key[:32],
                alert.alert_id,
                alert.instrument_id,
                resolved_binding_id,
                context.provider,
                primary_value if primary_value is not None else outcome.close,
                outcome.close,
                alert.threshold,
                alert.condition_type,
                outcome.bar_end,
                evaluated_at,
                key,
                json.dumps(payload),
            ),
        ).fetchone()
        if inserted is not None:
            inserted_trigger = _trigger(inserted)
            # The outbox rows commit with the trigger: no trigger is lost or delivered twice by the outbox.
            enqueue_alert_deliveries(connection, self.context.workspace_id, alert, inserted_trigger)
            return inserted_trigger
        return None

    def _apply(
        self,
        connection,
        alert: TradingAlert,
        outcome: AlertConditionOutcome,
        context: AlertEvaluationContext,
    ) -> TradingAlertTrigger | None:
        final_only = alert.frequency == "once_per_bar_close" or not alert.evaluation_policy.allow_partial_bars
        if final_only and not outcome.bar_is_final:
            return None
        evaluated_at = context.evaluated_at
        should_trigger = (
            outcome.met
            # A bar that closed before the alert was created, edited or re-enabled is history.
            and not (outcome.bar_is_final and alert.updated_at is not None and outcome.bar_end <= alert.updated_at)
            and cooldown_elapsed(alert.last_triggered_at, evaluated_at, alert.cooldown_seconds)
            and frequency_allows(alert.frequency, alert.last_triggered_at, evaluated_at)
        )
        triggered_at = alert.last_triggered_at
        enabled = alert.enabled
        inserted_trigger: TradingAlertTrigger | None = None
        primary_value = outcome.primary_value
        if should_trigger:
            key = alert_trigger_key(
                alert.alert_id,
                alert.frequency,
                outcome,
                revision=alert.revision,
                definition_revision=alert.definition_revision,
            )
            inserted_trigger = self._record_trigger(connection, alert, outcome, context, key)
            if inserted_trigger is not None:
                triggered_at = evaluated_at
                if alert.frequency == "once":
                    enabled = False
        connection.execute(
            """
            UPDATE omnix_trading_alerts
               SET last_observed_price = %s,
                   last_observed_value = COALESCE(%s, last_observed_value),
                   last_triggered_at = %s,
                   enabled = %s,
                   updated_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE updated_at END
             WHERE workspace_id = %s AND alert_id = %s
            """,
            (
                outcome.close,
                primary_value,
                triggered_at,
                enabled,
                enabled != alert.enabled,
                self.context.workspace_id,
                alert.alert_id,
            ),
        )
        return inserted_trigger


AlertRepositoryFactory = Callable[[], TradingAlertRepository]


def default_alert_repository() -> TradingAlertRepository:
    return TradingAlertRepository()
