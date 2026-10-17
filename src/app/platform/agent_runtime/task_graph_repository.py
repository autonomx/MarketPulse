"""PostgreSQL repository for durable TaskGraph coordination."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any, cast

from app.persistence.outbox_repository import PostgresOutboxRepository
from app.persistence.tenant import TenantContext

from .contracts import AgentEvent
from .repository import PostgresAgentRunRepository
from .task_graph import (
    TaskGraph,
    TaskGraphEvent,
    TaskGraphRunSnapshot,
    TaskNodeRunState,
    task_node_fingerprint,
)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class TaskGraphConcurrencyError(RuntimeError):
    pass


class PostgresTaskGraphRepository:
    def __init__(self, connection: Any, context: TenantContext) -> None:
        self.connection = connection
        self.context = context
        self.outbox = PostgresOutboxRepository(connection)

    def _revoke_agent_runs(
        self,
        child_run_ids: list[str] | tuple[str, ...] | set[str],
        *,
        reason: str,
    ) -> list[str]:
        ids = sorted({
            str(value).strip()
            for value in child_run_ids
            if str(value).strip()
        })
        if not ids:
            return []
        rows = self.connection.execute(
            """
            UPDATE omnix_agent_runs
               SET status = 'cancel_requested',
                   desired_state = 'cancelled',
                   last_error = %s,
                   revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s
               AND run_id = ANY(%s)
               AND status NOT IN ('completed','failed','cancelled')
               AND desired_state <> 'cancelled'
            RETURNING run_id
            """,
            (
                reason[:2000],
                self.context.workspace_id,
                ids,
            ),
        ).fetchall()
        revoked = [str(row[0]) for row in rows]
        if revoked:
            agent_repository = PostgresAgentRunRepository(
                self.connection,
                self.context,
            )
            for child_run_id in revoked:
                current = agent_repository.get_run(child_run_id)
                agent_repository.append_event(
                    AgentEvent(
                        run_id=child_run_id,
                        event_type="run.status",
                        payload={
                            "status": (
                                current.status
                                if current is not None
                                else "cancel_requested"
                            ),
                            "desired_state": "cancelled",
                            "reason": reason,
                            "source": "task_graph_authority_revocation",
                        },
                    )
                )
        return revoked

    def create_run(
        self,
        graph: TaskGraph,
        *,
        run_id: str | None = None,
    ) -> TaskGraphRunSnapshot:
        issued_run_id = str(run_id or uuid.uuid4().hex)
        inserted = self.connection.execute(
            """
            INSERT INTO omnix_task_graph_runs (
                workspace_id, run_id, graph_id, graph_revision, graph, status
            ) VALUES (%s, %s, %s, %s, %s::jsonb, 'running')
            ON CONFLICT (workspace_id, run_id) DO NOTHING
            RETURNING run_id
            """,
            (
                self.context.workspace_id,
                issued_run_id,
                graph.graph_id,
                graph.revision,
                _json(graph),
            ),
        ).fetchone()
        if inserted is None:
            existing = self.get_run(issued_run_id)
            if existing is None or existing.graph != graph:
                raise TaskGraphConcurrencyError(
                    "task graph run id already exists with different graph"
                )
            return existing

        self.connection.execute(
            """
            INSERT INTO omnix_task_graph_revisions (
                workspace_id, run_id, graph_revision, user_instruction, graph
            ) VALUES (%s, %s, %s, NULL, %s::jsonb)
            """,
            (
                self.context.workspace_id,
                issued_run_id,
                graph.revision,
                _json(graph),
            ),
        )
        for node in graph.nodes:
            self.connection.execute(
                """
                INSERT INTO omnix_task_graph_node_runs (
                    workspace_id, run_id, node_id, status, fingerprint
                ) VALUES (%s, %s, %s, 'pending', %s)
                """,
                (
                    self.context.workspace_id,
                    issued_run_id,
                    node.id,
                    task_node_fingerprint(node),
                ),
            )
        self.append_event(
            TaskGraphEvent(
                run_id=issued_run_id,
                event_type="task_graph.run.started",
                payload={
                    "graph_id": graph.graph_id,
                    "graph_revision": graph.revision,
                },
            )
        )
        result = self.get_run(issued_run_id)
        assert result is not None
        return result

    def get_run(self, run_id: str) -> TaskGraphRunSnapshot | None:
        row = self.connection.execute(
            """
            SELECT graph, status, revision, last_error,
                   created_at, updated_at, completed_at
              FROM omnix_task_graph_runs
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        ).fetchone()
        if row is None:
            return None
        graph = TaskGraph.model_validate(row[0])
        node_states = self.list_node_states(run_id)
        result_node_id = str(graph.output_contract.get("result_node") or "").strip()
        result_state = next(
            (
                state
                for state in node_states
                if state.node_id == result_node_id
                and state.status == "completed"
            ),
            None,
        )
        result = (
            result_state.output.get("result")
            if result_state is not None
            else None
        )
        return TaskGraphRunSnapshot(
            run_id=run_id,
            graph=graph,
            status=cast(Any, str(row[1])),
            revision=int(row[2]),
            node_states=node_states,
            result=result,
            last_error=str(row[3]) if row[3] else None,
            created_at=row[4],
            updated_at=row[5],
            completed_at=row[6],
        )

    def list_node_states(self, run_id: str) -> list[TaskNodeRunState]:
        rows = self.connection.execute(
            """
            SELECT node_id, status, attempts, child_run_id, output, last_error,
                   fingerprint, started_at, completed_at
              FROM omnix_task_graph_node_runs
             WHERE workspace_id = %s AND run_id = %s
             ORDER BY node_id
            """,
            (self.context.workspace_id, run_id),
        ).fetchall()
        return [
            TaskNodeRunState(
                node_id=str(row[0]),
                status=cast(Any, str(row[1])),
                attempts=int(row[2]),
                child_run_id=str(row[3]) if row[3] else None,
                output=dict(row[4] or {}),
                last_error=str(row[5]) if row[5] else None,
                fingerprint=str(row[6]),
                started_at=row[7],
                completed_at=row[8],
            )
            for row in rows
        ]

    def list_active_run_ids(self, *, limit: int = 200) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT run_id
              FROM omnix_task_graph_runs
             WHERE workspace_id = %s
               AND status IN ('queued','running','waiting_for_approval')
             ORDER BY updated_at, run_id
             LIMIT %s
            """,
            (self.context.workspace_id, max(1, min(int(limit), 1000))),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def claim_node(
        self,
        run_id: str,
        node_id: str,
        *,
        child_run_id: str | None = None,
        claim_output: dict[str, Any] | None = None,
        expected_fingerprint: str | None = None,
        expected_graph_revision: int | None = None,
    ) -> TaskNodeRunState | None:
        """Atomically reserve one pending node for the exact graph revision.

        Optional CAS predicates are emitted only when supplied. This avoids
        nullable untyped SQL placeholders while ensuring a stale scheduler
        cannot claim a node after steering has replaced its contract.
        """

        conditions = [
            "node.workspace_id = %s",
            "node.run_id = %s",
            "node.node_id = %s",
            "node.status = 'pending'",
        ]
        where_params: list[Any] = [
            self.context.workspace_id,
            run_id,
            node_id,
        ]
        if expected_fingerprint is not None:
            conditions.append("node.fingerprint = %s")
            where_params.append(expected_fingerprint)
        if expected_graph_revision is not None:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                      FROM omnix_task_graph_runs AS run
                     WHERE run.workspace_id = node.workspace_id
                       AND run.run_id = node.run_id
                       AND run.graph_revision = %s
                )
                """.strip()
            )
            where_params.append(expected_graph_revision)

        row = self.connection.execute(
            f"""
            UPDATE omnix_task_graph_node_runs AS node
               SET status = 'ready',
                   attempts = attempts + 1,
                   child_run_id = COALESCE(%s, child_run_id),
                   output = COALESCE(%s::jsonb, output),
                   started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                   last_error = NULL
             WHERE {" AND ".join(conditions)}
            RETURNING node_id, status, attempts, child_run_id, output, last_error,
                      fingerprint, started_at, completed_at
            """,
            (
                child_run_id,
                _json(claim_output) if claim_output is not None else None,
                *where_params,
            ),
        ).fetchone()
        if row is None:
            return None
        stored = TaskNodeRunState(
            node_id=str(row[0]),
            status=cast(Any, str(row[1])),
            attempts=int(row[2]),
            child_run_id=str(row[3]) if row[3] else None,
            output=dict(row[4] or {}),
            last_error=str(row[5]) if row[5] else None,
            fingerprint=str(row[6]),
            started_at=row[7],
            completed_at=row[8],
        )
        self.append_event(
            TaskGraphEvent(
                run_id=run_id,
                event_type="task_graph.node.ready",
                payload={
                    "node_id": node_id,
                    "child_run_id": stored.child_run_id,
                    "attempts": stored.attempts,
                    "batched": bool(
                        isinstance(stored.output.get("evidence_batch"), dict)
                    ),
                },
            )
        )
        return stored

    def update_node(
        self,
        run_id: str,
        node_id: str,
        *,
        status: str,
        child_run_id: str | None = None,
        output: dict[str, Any] | None = None,
        last_error: str | None = None,
        increment_attempts: bool = False,
        expected_fingerprint: str | None = None,
        expected_child_run_id: str | None = None,
        match_child_run_id: bool = False,
        expected_statuses: tuple[str, ...] | list[str] | None = None,
        expected_graph_revision: int | None = None,
    ) -> TaskNodeRunState | None:
        """Update one node only if its durable execution identity still matches."""

        now = datetime.now(timezone.utc)
        terminal = status in {"completed", "failed", "cancelled", "skipped"}
        conditions = [
            "workspace_id = %s",
            "run_id = %s",
            "node_id = %s",
        ]
        where_params: list[Any] = [
            self.context.workspace_id,
            run_id,
            node_id,
        ]
        if expected_fingerprint is not None:
            conditions.append("fingerprint = %s")
            where_params.append(expected_fingerprint)
        if match_child_run_id:
            conditions.append("child_run_id IS NOT DISTINCT FROM %s")
            where_params.append(expected_child_run_id)
        if expected_statuses:
            conditions.append("status = ANY(%s)")
            where_params.append(list(expected_statuses))
        if expected_graph_revision is not None:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                      FROM omnix_task_graph_runs AS run
                     WHERE run.workspace_id = omnix_task_graph_node_runs.workspace_id
                       AND run.run_id = omnix_task_graph_node_runs.run_id
                       AND run.graph_revision = %s
                )
                """.strip()
            )
            where_params.append(expected_graph_revision)

        row = self.connection.execute(
            f"""
            UPDATE omnix_task_graph_node_runs
               SET status = %s,
                   attempts = attempts + %s,
                   child_run_id = COALESCE(%s, child_run_id),
                   output = COALESCE(%s::jsonb, output),
                   last_error = %s,
                   started_at = CASE
                       WHEN %s IN ('ready','running','waiting_for_approval')
                           THEN COALESCE(started_at, %s)
                       ELSE started_at
                   END,
                   completed_at = CASE
                       WHEN %s THEN COALESCE(completed_at, %s)
                       WHEN %s IN ('pending','ready','running','waiting_for_approval')
                           THEN NULL
                       ELSE completed_at
                   END
             WHERE {" AND ".join(conditions)}
            RETURNING node_id, status, attempts, child_run_id, output, last_error,
                      fingerprint, started_at, completed_at
            """,
            (
                status,
                1 if increment_attempts else 0,
                child_run_id,
                _json(output) if output is not None else None,
                last_error,
                status,
                now,
                terminal,
                now,
                status,
                *where_params,
            ),
        ).fetchone()
        if row is None:
            exists = self.connection.execute(
                """
                SELECT 1
                  FROM omnix_task_graph_node_runs
                 WHERE workspace_id = %s AND run_id = %s AND node_id = %s
                """,
                (self.context.workspace_id, run_id, node_id),
            ).fetchone()
            if exists is None:
                raise KeyError(f"{run_id}:{node_id}")
            # The node exists but belongs to a newer revision/claim/state.
            # Stale coordinator writes are intentionally ignored.
            return None
        stored = TaskNodeRunState(
            node_id=str(row[0]),
            status=cast(Any, str(row[1])),
            attempts=int(row[2]),
            child_run_id=str(row[3]) if row[3] else None,
            output=dict(row[4] or {}),
            last_error=str(row[5]) if row[5] else None,
            fingerprint=str(row[6]),
            started_at=row[7],
            completed_at=row[8],
        )
        revoked_child_runs: list[str] = []
        if status == "cancelled" and stored.child_run_id:
            revoked_child_runs = self._revoke_agent_runs(
                [stored.child_run_id],
                reason=f"task_graph_node_cancelled:{run_id}:{node_id}",
            )
        self.append_event(
            TaskGraphEvent(
                run_id=run_id,
                event_type=f"task_graph.node.{status}",
                payload={
                    "node_id": node_id,
                    "child_run_id": stored.child_run_id,
                    "attempts": stored.attempts,
                    "error": stored.last_error,
                    "graph_revision": expected_graph_revision,
                    "revoked_child_runs": revoked_child_runs,
                },
            )
        )
        return stored

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        last_error: str | None = None,
        expected_revision: int | None = None,
        expected_graph_revision: int | None = None,
        expected_statuses: tuple[str, ...] | list[str] | None = None,
    ) -> TaskGraphRunSnapshot | None:
        """CAS one run-level lifecycle transition without resurrecting terminals."""

        terminal = status in {"completed", "failed", "cancelled"}
        conditions = [
            "workspace_id = %s",
            "run_id = %s",
            "status NOT IN ('completed','failed','cancelled')",
        ]
        where_params: list[Any] = [self.context.workspace_id, run_id]
        if expected_revision is not None:
            conditions.append("revision = %s")
            where_params.append(expected_revision)
        if expected_graph_revision is not None:
            conditions.append("graph_revision = %s")
            where_params.append(expected_graph_revision)
        if expected_statuses:
            conditions.append("status = ANY(%s)")
            where_params.append(list(expected_statuses))

        row = self.connection.execute(
            f"""
            UPDATE omnix_task_graph_runs
               SET status = %s,
                   last_error = %s,
                   revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP,
                   completed_at = CASE
                       WHEN %s THEN COALESCE(completed_at, CURRENT_TIMESTAMP)
                       ELSE NULL
                   END
             WHERE {" AND ".join(conditions)}
            RETURNING revision
            """,
            (
                status,
                last_error,
                terminal,
                *where_params,
            ),
        ).fetchone()
        if row is None:
            exists = self.connection.execute(
                """
                SELECT 1
                  FROM omnix_task_graph_runs
                 WHERE workspace_id = %s AND run_id = %s
                """,
                (self.context.workspace_id, run_id),
            ).fetchone()
            if exists is None:
                raise KeyError(run_id)
            # A concurrent lifecycle/revision transition won, or the run is
            # already terminal. The caller must reload rather than overwrite it.
            return None
        self.append_event(
            TaskGraphEvent(
                run_id=run_id,
                event_type=f"task_graph.run.{status}",
                payload={"error": last_error},
            )
        )
        current = self.get_run(run_id)
        assert current is not None
        return current

    def apply_revision(
        self,
        run_id: str,
        graph: TaskGraph,
        *,
        user_instruction: str,
        reusable_node_ids: set[str],
    ) -> TaskGraphRunSnapshot:
        self._lock_revisable_run(run_id, graph)

        existing_rows = self.connection.execute(
            """
            SELECT node_id, status, fingerprint, child_run_id
              FROM omnix_task_graph_node_runs
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, run_id),
        ).fetchall()
        old_ids = {str(row[0]) for row in existing_rows}
        new_ids = {node.id for node in graph.nodes}
        new_fingerprints = {
            node.id: task_node_fingerprint(node)
            for node in graph.nodes
        }
        revoked_child_runs = self._revoke_agent_runs(
            [
                str(row[3])
                for row in existing_rows
                if row[3]
                and str(row[1]) in {
                    "ready",
                    "running",
                    "waiting_for_approval",
                }
                and not (
                    str(row[0]) in reusable_node_ids
                    and str(row[0]) in new_ids
                    and str(row[2]) == new_fingerprints[str(row[0])]
                )
            ],
            reason=(
                f"task_graph_revision_revoked:{run_id}:"
                f"revision:{graph.revision}"
            ),
        )
        removed = old_ids - new_ids
        if removed:
            # Revision history and events preserve the audit record. The node-run
            # table represents only the current graph execution set; keeping
            # removed rows here would make recovered state disagree with graph.nodes.
            self.connection.execute(
                """
                DELETE FROM omnix_task_graph_node_runs
                 WHERE workspace_id = %s AND run_id = %s
                   AND node_id = ANY(%s)
                """,
                (self.context.workspace_id, run_id, list(removed)),
            )

        self._reset_revised_nodes(graph, run_id, reusable_node_ids)

        self.connection.execute(
            """
            UPDATE omnix_task_graph_runs
               SET graph_id = %s,
                   graph_revision = %s,
                   graph = %s::jsonb,
                   status = 'running',
                   revision = revision + 1,
                   last_error = NULL,
                   updated_at = CURRENT_TIMESTAMP,
                   completed_at = NULL
             WHERE workspace_id = %s AND run_id = %s
            """,
            (
                graph.graph_id,
                graph.revision,
                _json(graph),
                self.context.workspace_id,
                run_id,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO omnix_task_graph_revisions (
                workspace_id, run_id, graph_revision, user_instruction, graph
            ) VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                self.context.workspace_id,
                run_id,
                graph.revision,
                user_instruction,
                _json(graph),
            ),
        )
        self.append_event(
            TaskGraphEvent(
                run_id=run_id,
                event_type="task_graph.revised",
                payload={
                    "graph_revision": graph.revision,
                    "reused_nodes": sorted(reusable_node_ids),
                    "removed_nodes": sorted(removed),
                    "revoked_child_runs": revoked_child_runs,
                },
            )
        )
        current = self.get_run(run_id)
        assert current is not None
        return current

    def _lock_revisable_run(self, run_id, graph):
        """Lock the run; only a live graph may be revised, exactly one revision ahead."""
        locked = self.connection.execute(
            """
            SELECT graph_revision, status
              FROM omnix_task_graph_runs
             WHERE workspace_id = %s AND run_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, run_id),
        ).fetchone()
        if locked is None:
            raise KeyError(run_id)
        current_status = str(locked[1])
        if current_status in {"completed", "failed", "cancelled"}:
            raise TaskGraphConcurrencyError(
                f"cannot revise terminal task graph:{current_status}"
            )
        expected = int(locked[0]) + 1
        if graph.revision != expected:
            raise TaskGraphConcurrencyError(
                f"graph revision must advance exactly once: expected {expected}"
            )

    def _reset_revised_nodes(self, graph, run_id, reusable_node_ids):
        """Insert new nodes and reset changed ones to pending; a reusable node with an unchanged fingerprint keeps its result."""
        for node in graph.nodes:
            fingerprint = task_node_fingerprint(node)
            row = self.connection.execute(
                """
                SELECT status, fingerprint
                  FROM omnix_task_graph_node_runs
                 WHERE workspace_id = %s AND run_id = %s AND node_id = %s
                """,
                (self.context.workspace_id, run_id, node.id),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO omnix_task_graph_node_runs (
                        workspace_id, run_id, node_id, status, fingerprint
                    ) VALUES (%s, %s, %s, 'pending', %s)
                    """,
                    (self.context.workspace_id, run_id, node.id, fingerprint),
                )
                continue
            if node.id in reusable_node_ids and str(row[1]) == fingerprint:
                continue
            self.connection.execute(
                """
                UPDATE omnix_task_graph_node_runs
                   SET status = 'pending',
                       attempts = 0,
                       child_run_id = NULL,
                       output = '{}'::jsonb,
                       last_error = NULL,
                       fingerprint = %s,
                       started_at = NULL,
                       completed_at = NULL
                 WHERE workspace_id = %s AND run_id = %s AND node_id = %s
                """,
                (
                    fingerprint,
                    self.context.workspace_id,
                    run_id,
                    node.id,
                ),
            )

    def stream_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[TaskGraphEvent]:
        rows = self.connection.execute(
            """
            SELECT event_id, sequence, event_type, payload, created_at
              FROM omnix_task_graph_events
             WHERE workspace_id = %s AND run_id = %s AND sequence > %s
             ORDER BY sequence
            """,
            (
                self.context.workspace_id,
                run_id,
                max(0, int(after_sequence)),
            ),
        ).fetchall()
        return [
            TaskGraphEvent(
                event_id=str(row[0]),
                run_id=run_id,
                sequence=int(row[1]),
                event_type=str(row[2]),
                payload=dict(row[3] or {}),
                created_at=row[4],
            )
            for row in rows
        ]

    def append_event(self, event: TaskGraphEvent) -> TaskGraphEvent:
        locked = self.connection.execute(
            """
            SELECT revision
              FROM omnix_task_graph_runs
             WHERE workspace_id = %s AND run_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, event.run_id),
        ).fetchone()
        if locked is None:
            raise KeyError(event.run_id)
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
              FROM omnix_task_graph_events
             WHERE workspace_id = %s AND run_id = %s
            """,
            (self.context.workspace_id, event.run_id),
        ).fetchone()
        stored = event.model_copy(update={"sequence": int(row[0])})
        self.connection.execute(
            """
            INSERT INTO omnix_task_graph_events (
                workspace_id, run_id, sequence, event_id,
                event_type, payload, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                self.context.workspace_id,
                stored.run_id,
                stored.sequence,
                stored.event_id,
                stored.event_type,
                _json(stored.payload),
                stored.created_at,
            ),
        )
        self.outbox.append(
            self.context,
            aggregate_type="task_graph_run",
            aggregate_id=stored.run_id,
            event_type=stored.event_type,
            payload=stored.model_dump(mode="json"),
            ordering_key=f"task-graph:{stored.run_id}",
            event_key=f"task-graph:{stored.event_id}",
        )
        return stored
