"""PostgreSQL-backed deterministic WorkflowRuntime."""
from __future__ import annotations

from .exception_logging import log_recovered_exception

from datetime import datetime
from app.caching.bounded_cache import bounded_lru_cache
import json
import os
import threading
import uuid
from typing import Any, cast

from app.capabilities.executor import (
    CapabilityExecutor,
    execute_capability,
)
from app.persistence.database import PostgresDatabase, default_database
from app.security.tenant_context import RequestTenant
from app.persistence.outbox_repository import PostgresOutboxRepository
from app.persistence.unit_of_work import unit_of_work

from app.capabilities import default_capability_registry
from .interfaces import WorkflowRuntime
from .workflow_repository import PostgresWorkflowRepository
from .workflows import (
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowRunSnapshot,
    WorkflowScheduleSnapshot,
    WorkflowStepDefinition,
)


class WorkflowRuntimeError(RuntimeError):
    pass


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class PostgresWorkflowRuntime(WorkflowRuntime):
    context = RequestTenant()
    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        capability_executor: CapabilityExecutor = execute_capability,
    ) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant
        self.capability_executor = capability_executor
        self.worker_id = f"workflow:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        self._supervisor_started = False
        self._supervisor_lock = threading.Lock()
        self._supervisor_stop = threading.Event()

    def repository(self, connection: Any) -> PostgresWorkflowRepository:
        """The workflow tables on ``connection``, scoped to the current tenant."""
        return PostgresWorkflowRepository(connection, self.context)

    def _append_event(
        self,
        connection,
        event: WorkflowEvent,
    ) -> WorkflowEvent:
        locked = self.repository(connection).lock_run_revision(event.run_id).fetchone()
        if locked is None:
            raise KeyError(event.run_id)
        row = self.repository(connection).next_event_sequence(event.run_id).fetchone()
        stored = event.model_copy(update={"sequence": int(row[0])})
        self.repository(connection).insert_event(stored.run_id, stored.sequence, stored.event_id, stored.event_type, _json(stored.payload), stored.created_at)
        PostgresOutboxRepository(connection).append(
            self.context,
            aggregate_type="workflow_run",
            aggregate_id=stored.run_id,
            event_type=stored.event_type,
            payload=stored.model_dump(mode="json"),
            ordering_key=f"workflow:{stored.run_id}",
            event_key=f"workflow:{stored.event_id}",
        )
        return stored

    def register(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        self._validate_definition_capabilities(definition)
        with unit_of_work(self.database) as work:
            inserted = self.repository(work.connection).insert_definition(definition.id, definition.version, definition.name, _json(definition)).fetchone()
            if inserted is None:
                row = self.repository(work.connection).definition_by_version(definition.id, definition.version).fetchone()
                if row is None:
                    raise WorkflowRuntimeError("workflow_version_conflict")
                existing = WorkflowDefinition.model_validate(row[0])
                if existing != definition:
                    raise WorkflowRuntimeError(
                        f"workflow_version_immutable:{definition.id}:v{definition.version}"
                    )
                work.rollback()
                return existing
            work.commit()
        return definition

    @staticmethod
    def _validate_definition_capabilities(definition: WorkflowDefinition) -> None:
        registry = default_capability_registry()
        for step in definition.steps:
            if step.kind != "capability":
                continue
            capability = registry.get(str(step.capability_id or ""))
            if capability is None:
                raise WorkflowRuntimeError(
                    f"workflow_capability_unknown:{step.capability_id}"
                )
            if not capability.enabled:
                raise WorkflowRuntimeError(
                    f"workflow_capability_disabled:{capability.id}"
                )
            if capability.execution_zone != "broker":
                raise WorkflowRuntimeError(
                    f"workflow_capability_zone_unsupported:{capability.id}:{capability.execution_zone}"
                )

    def list_definitions(self) -> list[WorkflowDefinition]:
        with unit_of_work(self.database) as work:
            rows = self.repository(work.connection).active_definitions().fetchall()
            work.rollback()
        return [WorkflowDefinition.model_validate(row[0]) for row in rows]

    def lookup(self, name_or_id: str) -> str | None:
        value = str(name_or_id or "").strip().casefold()
        if not value:
            return None
        with unit_of_work(self.database) as work:
            row = self.repository(work.connection).lookup_workflow_id(value, value, f"%{value}%").fetchone()
            work.rollback()
        return str(row[0]) if row else None

    def start(self, workflow_id: str, input_payload: dict[str, Any]) -> str:
        self._ensure_supervisor()
        definition = self._definition(workflow_id)
        if definition is None:
            raise KeyError(workflow_id)
        return self._start_definition(definition, input_payload)

    def _start_definition(
        self,
        definition: WorkflowDefinition,
        input_payload: dict[str, Any],
    ) -> str:
        run_id = uuid.uuid4().hex
        idempotency_key = str(input_payload.get("idempotency_key") or "").strip() or None
        with unit_of_work(self.database) as work:
            inserted = self.repository(work.connection).insert_run(run_id, definition.id, definition.version, _json(input_payload), "running" if definition.steps else "completed", definition.steps[0].id if definition.steps else None, idempotency_key).fetchone()
            if inserted is None:
                if not idempotency_key:
                    raise WorkflowRuntimeError("workflow_run_insert_conflict")
                existing = self.repository(work.connection).run_by_idempotency_key(idempotency_key).fetchone()
                if existing is None:
                    raise WorkflowRuntimeError("workflow_idempotency_conflict")
                if (
                    str(existing[1]) != definition.id
                    or int(existing[2]) != definition.version
                ):
                    raise WorkflowRuntimeError(
                        f"workflow_idempotency_key_reused:{idempotency_key}"
                    )
                work.rollback()
                return str(existing[0])
            for ordinal, step in enumerate(definition.steps):
                self.repository(work.connection).insert_step_run(run_id, step.id, ordinal)
            self._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type=(
                        "workflow.run.started"
                        if definition.steps
                        else "workflow.run.completed"
                    ),
                    payload={
                        "workflow_id": definition.id,
                        "workflow_version": definition.version,
                    },
                ),
            )
            work.commit()
        self._advance(run_id)
        return run_id

    def stream_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[WorkflowEvent]:
        with unit_of_work(self.database) as work:
            rows = self.repository(work.connection).events_after(run_id, max(0, int(after_sequence))).fetchall()
            work.rollback()
        return [
            WorkflowEvent(
                event_id=str(row[0]),
                run_id=run_id,
                sequence=int(row[1]),
                event_type=str(row[2]),
                payload=dict(row[3] or {}),
                created_at=row[4],
            )
            for row in rows
        ]

    def list_runs(
        self,
        *,
        workflow_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 1000))
        with unit_of_work(self.database) as work:
            if workflow_id is None:
                rows = self.repository(work.connection).recent_run_ids(bounded).fetchall()
            else:
                rows = self.repository(work.connection).recent_run_ids_for_workflow(workflow_id, bounded).fetchall()
            work.rollback()
        result: list[dict[str, Any]] = []
        for row in rows:
            state = self.get_status(str(row[0]))
            if state is not None:
                result.append(state)
        return result

    def schedule(
        self,
        workflow_id: str,
        input_payload: dict[str, Any],
        *,
        run_at: datetime,
        interval_seconds: int | None = None,
        version: int | None = None,
        schedule_id: str | None = None,
    ) -> str:
        from . import workflow_scheduling

        return workflow_scheduling.schedule(self, workflow_id, input_payload, run_at=run_at, interval_seconds=interval_seconds, version=version, schedule_id=schedule_id)

    def list_schedules(self) -> list[WorkflowScheduleSnapshot]:
        from . import workflow_scheduling

        return workflow_scheduling.list_schedules(self)

    def cancel_schedule(self, schedule_id: str) -> None:
        from . import workflow_scheduling

        return workflow_scheduling.cancel_schedule(self, schedule_id)

    def pause(self, run_id: str) -> None:
        self._ensure_supervisor()
        state = self.get_status(run_id)
        if state is None:
            raise KeyError(run_id)
        if state["status"] in {"completed", "failed", "cancelled"}:
            return
        self._set_status(run_id, "paused")

    def resume(self, run_id: str) -> None:
        self._ensure_supervisor()
        state = self.get_status(run_id)
        if state is None:
            raise KeyError(run_id)
        if state["status"] in {"completed", "failed", "cancelled"}:
            return
        self._set_status(run_id, "running")
        self._advance(run_id)

    def cancel(self, run_id: str) -> None:
        self._ensure_supervisor()
        state = self.get_status(run_id)
        if state is None:
            raise KeyError(run_id)
        if state["status"] in {"completed", "failed", "cancelled"}:
            return
        self._set_status(run_id, "cancelled")

    def approve(self, run_id: str, step_id: str, *, approved_by: str) -> None:
        self._ensure_supervisor()
        self._resolve_approval(run_id, step_id, approved_by=approved_by)
        self._advance(run_id)

    def reject(self, run_id: str, step_id: str) -> None:
        self._ensure_supervisor()
        self._resolve_approval(run_id, step_id, approved_by=None)

    def _resolve_approval(self, run_id: str, step_id: str, *, approved_by: str | None) -> None:
        from . import workflow_execution

        return workflow_execution._resolve_approval(self, run_id, step_id, approved_by=approved_by)

    def get_status(self, run_id: str) -> dict[str, Any] | None:
        self._ensure_supervisor()
        with unit_of_work(self.database) as work:
            row = self.repository(work.connection).run_status_row(run_id).fetchone()
            work.rollback()
        if row is None:
            return None
        return WorkflowRunSnapshot(
            run_id=run_id,
            workflow_id=str(row[0]),
            workflow_version=int(row[1]),
            status=cast(Any, str(row[2])),
            current_step_id=str(row[3]) if row[3] else None,
            input_payload=dict(row[4] or {}),
            revision=int(row[5]),
            last_error=str(row[6]) if row[6] else None,
            created_at=row[7],
            updated_at=row[8],
            completed_at=row[9],
        ).model_dump(mode="json")

    def _advance(self, run_id: str) -> None:
        from . import workflow_execution

        return workflow_execution._advance(self, run_id)

    @staticmethod
    def _step_requires_approval(step: WorkflowStepDefinition) -> bool:
        from . import workflow_execution

        return workflow_execution._step_requires_approval(step)

    @staticmethod
    def _step_retry_safe(step: WorkflowStepDefinition) -> bool:
        from . import workflow_execution

        return workflow_execution._step_retry_safe(step)

    def _execute_with_timeout(
        self,
        run_id: str,
        step: WorkflowStepDefinition,
        context: dict[str, Any],
        *,
        approved_by: str | None,
    ) -> dict[str, Any]:
        from . import workflow_execution

        return workflow_execution._execute_with_timeout(self, run_id, step, context, approved_by=approved_by)

    def _execute_step(
        self,
        run_id: str,
        step: WorkflowStepDefinition,
        context: dict[str, Any],
        *,
        approved_by: str | None,
    ) -> dict[str, Any]:
        from . import workflow_execution

        return workflow_execution._execute_step(self, run_id, step, context, approved_by=approved_by)

    def _context(self, run_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        from . import workflow_execution

        return workflow_execution._context(self, run_id, input_payload)

    @classmethod
    def _render_input(cls, template: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        from . import workflow_execution

        return workflow_execution._render_input(cls, template, context)

    @classmethod
    def _render_value(cls, value: Any, context: dict[str, Any]) -> Any:
        from . import workflow_execution

        return workflow_execution._render_value(cls, value, context)

    @staticmethod
    def _lookup(context: dict[str, Any], path: str) -> Any:
        from . import workflow_execution

        return workflow_execution._lookup(context, path)

    @classmethod
    def _condition(cls, expression: str | None, context: dict[str, Any]) -> bool:
        from . import workflow_execution

        return workflow_execution._condition(cls, expression, context)

    @staticmethod
    def _next_target(
        definition: WorkflowDefinition,
        step: WorkflowStepDefinition,
        result: dict[str, Any],
    ) -> str | None:
        from . import workflow_execution

        return workflow_execution._next_target(definition, step, result)

    def _step_state(self, run_id: str, step_id: str) -> dict[str, Any] | None:
        from . import workflow_steps

        return workflow_steps._step_state(self, run_id, step_id)

    def _claim_step(self, run_id: str, step_id: str) -> int | None:
        from . import workflow_steps

        return workflow_steps._claim_step(self, run_id, step_id)

    def _set_waiting_for_approval(self, run_id: str, step_id: str) -> None:
        from . import workflow_steps

        return workflow_steps._set_waiting_for_approval(self, run_id, step_id)

    def _set_step_retry(self, run_id: str, step_id: str, error: str) -> bool:
        from . import workflow_steps

        return workflow_steps._set_step_retry(self, run_id, step_id, error)

    def _set_step_failed(self, run_id: str, step_id: str, error: str) -> bool:
        from . import workflow_steps

        return workflow_steps._set_step_failed(self, run_id, step_id, error)

    def _set_step_completed(
        self,
        run_id: str,
        step_id: str,
        result: dict[str, Any],
    ) -> bool:
        from . import workflow_steps

        return workflow_steps._set_step_completed(self, run_id, step_id, result)

    def _transition(self, run_id: str, target: str | None) -> None:
        from . import workflow_steps

        return workflow_steps._transition(self, run_id, target)

    def _finish(self, run_id: str) -> None:
        from . import workflow_steps

        return workflow_steps._finish(self, run_id)

    def _set_status(self, run_id: str, status: str, *, error: str | None = None) -> None:
        from . import workflow_steps

        return workflow_steps._set_status(self, run_id, status, error=error)

    def _ensure_supervisor(self) -> None:
        if self._supervisor_started:
            return
        with self._supervisor_lock:
            if self._supervisor_started:
                return
            self._supervisor_started = True
            threading.Thread(
                target=self._supervisor_loop,
                name="omnix-workflow-supervisor",
                daemon=True,
            ).start()

    def _supervisor_loop(self) -> None:
        while not self._supervisor_stop.is_set():
            try:
                self._supervise_once()
            except Exception as exc:
                log_recovered_exception("workflow supervisor iteration", exc)
                pass
            self._supervisor_stop.wait(30.0)

    def _supervise_once(self) -> None:
        from . import workflow_scheduling

        return workflow_scheduling._supervise_once(self)

    def _enqueue_due_schedule_fires(self) -> None:
        from . import workflow_scheduling

        return workflow_scheduling._enqueue_due_schedule_fires(self)

    def _dispatch_pending_schedule_fires(self) -> None:
        from . import workflow_scheduling

        return workflow_scheduling._dispatch_pending_schedule_fires(self)

    def _definition(self, workflow_id: str, *, version: int | None = None) -> WorkflowDefinition | None:
        with unit_of_work(self.database) as work:
            if version is None:
                row = self.repository(work.connection).latest_active_definition(workflow_id).fetchone()
            else:
                row = self.repository(work.connection).definition_by_version(workflow_id, version).fetchone()
            work.rollback()
        return WorkflowDefinition.model_validate(row[0]) if row else None


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def default_workflow_runtime() -> PostgresWorkflowRuntime:
    return PostgresWorkflowRuntime()
