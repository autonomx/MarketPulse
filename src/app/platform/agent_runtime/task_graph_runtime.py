"""Durable TaskGraph scheduling, parallel launch, aggregation, and recovery."""
from __future__ import annotations


import logging
import threading
from app.caching.bounded_cache import bounded_lru_cache
from typing import Any

from .capability_requests import CapabilityExecutor, default_capability_executor
from app.persistence.database import PostgresDatabase, default_database
from app.security.tenant_context import RequestTenant
from app.persistence.unit_of_work import unit_of_work

from .contracts import AgentRunSpec, ModelRef
from .task_graph import (
    TaskEdge,
    TaskGraph,
    TaskGraphRunSnapshot,
    TaskNode,
    TaskNodeRunState,
)
from .task_graph_optimizer import (
    EvidenceAcquisitionBatch,
    TaskGraphOptimizationPlan,
)
from .task_graph_repository import PostgresTaskGraphRepository


logger = logging.getLogger(__name__)


class TaskGraphRuntimeError(RuntimeError):
    pass


_AGENT_NODE_KINDS = {"agent", "evidence_read", "synthesis"}


class PostgresTaskGraphRuntime:
    """Authority-free coordinator over existing durable Agent/capability runtimes.

    The coordinator never receives the union of node capabilities. It may only
    launch already-compiled node envelopes, observe status, pass declared
    outputs over graph edges, and cancel work.
    """
    context = RequestTenant()

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        agent_service: Any | None = None,
        capability_executor: CapabilityExecutor | None = None,
        model_overrides: dict[str, ModelRef] | None = None,
    ) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant
        self._agent_service = agent_service
        self.capability_executor = capability_executor or default_capability_executor
        self.model_overrides = dict(model_overrides or {})
        self._supervisor_started = False
        self._supervisor_lock = threading.Lock()
        self._supervisor_stop = threading.Event()

    @property
    def agent_service(self):
        if self._agent_service is None:
            from .service import default_agent_run_service

            self._agent_service = default_agent_run_service()
        return self._agent_service

    def _ensure_supervisor(self) -> None:
        if self._supervisor_started:
            return
        with self._supervisor_lock:
            if self._supervisor_started:
                return
            self._supervisor_started = True
            threading.Thread(
                target=self._supervisor_loop,
                name="omnix-task-graph-supervisor",
                daemon=True,
            ).start()

    def _supervisor_loop(self) -> None:
        while not self._supervisor_stop.is_set():
            try:
                self._supervise_once()
            except Exception:
                logger.exception("TaskGraph supervisor iteration failed")
            self._supervisor_stop.wait(2.0)

    def _supervise_once(self) -> None:
        with unit_of_work(self.database) as work:
            repository = PostgresTaskGraphRepository(work.connection, self.context)
            run_ids = repository.list_active_run_ids()
            work.rollback()
        for run_id in run_ids:
            try:
                self.recover(run_id)
            except Exception:
                logger.exception(
                    "TaskGraph recovery failed for run %s",
                    run_id,
                )

    def close(self) -> None:
        self._supervisor_stop.set()

    def start(
        self,
        graph: TaskGraph,
        *,
        run_id: str | None = None,
    ) -> TaskGraphRunSnapshot:
        self._ensure_supervisor()
        with unit_of_work(self.database) as work:
            repository = PostgresTaskGraphRepository(work.connection, self.context)
            snapshot = repository.create_run(graph, run_id=run_id)
            work.commit()
        return self.advance(snapshot.run_id)

    def get_status(self, run_id: str) -> TaskGraphRunSnapshot | None:
        self._ensure_supervisor()
        with unit_of_work(self.database) as work:
            repository = PostgresTaskGraphRepository(work.connection, self.context)
            snapshot = repository.get_run(run_id)
            work.rollback()
        return snapshot

    def stream_events(self, run_id: str, *, after_sequence: int = 0):
        with unit_of_work(self.database) as work:
            repository = PostgresTaskGraphRepository(work.connection, self.context)
            rows = repository.stream_events(run_id, after_sequence=after_sequence)
            work.rollback()
        return rows

    def _node_map(self, graph: TaskGraph) -> dict[str, TaskNode]:
        return {node.id: node for node in graph.nodes}

    def _state_map(
        self,
        states: list[TaskNodeRunState],
    ) -> dict[str, TaskNodeRunState]:
        return {state.node_id: state for state in states}

    @staticmethod
    def _active_execution_count(states: list[TaskNodeRunState]) -> int:
        from . import task_graph_scheduling

        return task_graph_scheduling._active_execution_count(states)

    def _incoming(self, graph: TaskGraph, node_id: str) -> list[TaskEdge]:
        from . import task_graph_scheduling

        return task_graph_scheduling._incoming(self, graph, node_id)

    def _predecessor_outputs(
        self,
        graph: TaskGraph,
        states: dict[str, TaskNodeRunState],
        node_id: str,
    ) -> dict[str, Any]:
        from . import task_graph_scheduling

        return task_graph_scheduling._predecessor_outputs(self, graph, states, node_id)

    def _edge_allows(
        self,
        edge: TaskEdge,
        state: TaskNodeRunState,
    ) -> bool:
        from . import task_graph_scheduling

        return task_graph_scheduling._edge_allows(self, edge, state)

    def _readiness(
        self,
        graph: TaskGraph,
        states: dict[str, TaskNodeRunState],
        node: TaskNode,
    ) -> tuple[bool, bool]:
        from . import task_graph_scheduling

        return task_graph_scheduling._readiness(self, graph, states, node)

    def _poll_children(self, snapshot: TaskGraphRunSnapshot) -> None:
        from . import task_graph_scheduling

        return task_graph_scheduling._poll_children(self, snapshot)

    def _claim_node(
        self,
        run_id: str,
        graph: TaskGraph,
        node: TaskNode,
        *,
        child_run_id: str | None = None,
        claim_output: dict[str, Any] | None = None,
    ) -> TaskNodeRunState | None:
        from . import task_graph_scheduling

        return task_graph_scheduling._claim_node(self, run_id, graph, node, child_run_id=child_run_id, claim_output=claim_output)

    def _store_node(
        self,
        run_id: str,
        node_id: str,
        *,
        status: str,
        child_run_id: str | None = None,
        output: dict[str, Any] | None = None,
        last_error: str | None = None,
        increment_attempts: bool = False,
        expected_state: TaskNodeRunState | None = None,
        expected_statuses: tuple[str, ...] | list[str] | None = None,
        graph_revision: int | None = None,
    ) -> TaskNodeRunState | None:
        from . import task_graph_scheduling

        return task_graph_scheduling._store_node(self, run_id, node_id, status=status, child_run_id=child_run_id, output=output, last_error=last_error, increment_attempts=increment_attempts, expected_state=expected_state, expected_statuses=expected_statuses, graph_revision=graph_revision)

    def _child_result(self, child_run_id: str) -> str | None:
        from . import task_graph_scheduling

        return task_graph_scheduling._child_result(self, child_run_id)

    @staticmethod
    def _batch_policy_signature(node: TaskNode) -> str:
        from . import task_graph_batches

        return task_graph_batches._batch_policy_signature(node)

    def _batch_candidates(
        self,
        graph: TaskGraph,
        states: dict[str, TaskNodeRunState],
        node: TaskNode,
        plan: TaskGraphOptimizationPlan,
    ) -> tuple[EvidenceAcquisitionBatch | None, list[TaskNode]]:
        from . import task_graph_batches

        return task_graph_batches._batch_candidates(self, graph, states, node, plan)

    def _claim_evidence_batch(
        self,
        run_id: str,
        graph: TaskGraph,
        nodes: list[TaskNode],
        *,
        child_run_id: str,
        batch: EvidenceAcquisitionBatch,
    ) -> list[TaskNodeRunState]:
        from . import task_graph_batches

        return task_graph_batches._claim_evidence_batch(self, run_id, graph, nodes, child_run_id=child_run_id, batch=batch)

    @staticmethod
    def _merged_evidence_batch_node(
        nodes: list[TaskNode],
        *,
        selected_model: ModelRef | None,
    ) -> TaskNode:
        from . import task_graph_batches

        return task_graph_batches._merged_evidence_batch_node(nodes, selected_model=selected_model)

    def _agent_spec(
        self,
        node: TaskNode,
        *,
        child_run_id: str,
        selected_model: ModelRef | None = None,
    ) -> AgentRunSpec:
        from . import task_graph_batches

        return task_graph_batches._agent_spec(self, node, child_run_id=child_run_id, selected_model=selected_model)

    def _start_evidence_batch(
        self,
        run_id: str,
        graph: TaskGraph,
        states: dict[str, TaskNodeRunState],
        nodes: list[TaskNode],
        claims: list[TaskNodeRunState],
        *,
        selected_model: ModelRef | None,
    ) -> bool:
        from . import task_graph_batches

        return task_graph_batches._start_evidence_batch(self, run_id, graph, states, nodes, claims, selected_model=selected_model)

    def _set_run_status(
        self,
        snapshot: TaskGraphRunSnapshot,
        status: str,
        *,
        last_error: str | None = None,
    ) -> TaskGraphRunSnapshot:
        from . import task_graph_scheduling

        return task_graph_scheduling._set_run_status(self, snapshot, status, last_error=last_error)

    def _optimization_plan(self, graph: TaskGraph) -> TaskGraphOptimizationPlan:
        from . import task_graph_scheduling

        return task_graph_scheduling._optimization_plan(self, graph)

    def _optimized_nodes(
        self,
        graph: TaskGraph,
        plan: TaskGraphOptimizationPlan,
    ) -> list[TaskNode]:
        from . import task_graph_scheduling

        return task_graph_scheduling._optimized_nodes(self, graph, plan)

    def _cache_source(
        self,
        node: TaskNode,
        states: dict[str, TaskNodeRunState],
        plan: TaskGraphOptimizationPlan,
    ) -> TaskNodeRunState | None:
        from . import task_graph_scheduling

        return task_graph_scheduling._cache_source(self, node, states, plan)

    def _condition(self, expression: str, inputs: dict[str, Any]) -> bool:
        from . import task_graph_scheduling

        return task_graph_scheduling._condition(self, expression, inputs)

    def _execute_claimed_node(
        self,
        run_id: str,
        graph: TaskGraph,
        states: dict[str, TaskNodeRunState],
        node: TaskNode,
        claimed: TaskNodeRunState,
        *,
        selected_model: ModelRef | None = None,
    ) -> bool:
        from . import task_graph_scheduling

        return task_graph_scheduling._execute_claimed_node(self, run_id, graph, states, node, claimed, selected_model=selected_model)

    def _launch_node(
        self,
        run_id: str,
        graph: TaskGraph,
        states: dict[str, TaskNodeRunState],
        node: TaskNode,
        *,
        selected_model: ModelRef | None = None,
    ) -> bool:
        from . import task_graph_scheduling

        return task_graph_scheduling._launch_node(self, run_id, graph, states, node, selected_model=selected_model)


    def _fail_graph(
        self,
        snapshot: TaskGraphRunSnapshot,
        *,
        last_error: str,
    ) -> TaskGraphRunSnapshot:
        from . import task_graph_scheduling

        return task_graph_scheduling._fail_graph(self, snapshot, last_error=last_error)

    def advance(self, run_id: str) -> TaskGraphRunSnapshot:
        from . import task_graph_scheduling

        return task_graph_scheduling.advance(self, run_id)

    def _resolve_child_approval_id(
        self,
        state: TaskNodeRunState,
        approval_id: str | None,
    ) -> str:
        from . import task_graph_control

        return task_graph_control._resolve_child_approval_id(self, state, approval_id)

    def approve(
        self,
        run_id: str,
        node_id: str,
        *,
        approved_by: str,
        approval_id: str | None = None,
    ) -> TaskGraphRunSnapshot:
        from . import task_graph_control

        return task_graph_control.approve(self, run_id, node_id, approved_by=approved_by, approval_id=approval_id)

    def reject(
        self,
        run_id: str,
        node_id: str,
        *,
        approval_id: str | None = None,
    ) -> TaskGraphRunSnapshot:
        from . import task_graph_control

        return task_graph_control.reject(self, run_id, node_id, approval_id=approval_id)

    def _cancel_child(self, child_run_id: str) -> None:
        from . import task_graph_control

        return task_graph_control._cancel_child(self, child_run_id)

    def cancel(
        self,
        run_id: str,
        *,
        reason: str = "cancelled_by_user",
    ) -> TaskGraphRunSnapshot:
        from . import task_graph_control

        return task_graph_control.cancel(self, run_id, reason=reason)

    def revise(
        self,
        run_id: str,
        revised_graph: TaskGraph,
        *,
        user_instruction: str,
        reuse_completed: bool = True,
    ) -> TaskGraphRunSnapshot:
        from . import task_graph_control

        return task_graph_control.revise(self, run_id, revised_graph, user_instruction=user_instruction, reuse_completed=reuse_completed)

    def recover(self, run_id: str) -> TaskGraphRunSnapshot:
        from . import task_graph_control

        return task_graph_control.recover(self, run_id)


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def default_task_graph_runtime() -> PostgresTaskGraphRuntime:
    return PostgresTaskGraphRuntime()
