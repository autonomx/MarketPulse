"""TaskGraph control: approvals, cancellation, revision and recovery (WP-8.2).

Functions over the TaskGraph runtime; ``PostgresTaskGraphRuntime`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception
from app.persistence.unit_of_work import unit_of_work
from .contracts import AgentRunCommand
from .task_graph import (
    TaskGraph,
    TaskGraphRunSnapshot,
    TaskNodeRunState,
)
from .task_graph_repository import PostgresTaskGraphRepository
from .task_graph_revision import plan_graph_revision
from typing import TYPE_CHECKING
from .task_graph_runtime import (
    TaskGraphRuntimeError,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.task_graph_runtime import PostgresTaskGraphRuntime


def _resolve_child_approval_id(
    graph_runtime: PostgresTaskGraphRuntime,
    state: TaskNodeRunState,
    approval_id: str | None,
) -> str:
    if not state.child_run_id:
        raise TaskGraphRuntimeError("node has no child approval run")
    pending = graph_runtime.agent_service.approvals(
        state.child_run_id,
        state="pending",
    )
    if approval_id:
        if not any(item.approval_id == approval_id for item in pending):
            raise TaskGraphRuntimeError("child approval not found")
        return approval_id
    if len(pending) != 1:
        raise TaskGraphRuntimeError(
            "approval_id is required when a child has multiple pending approvals"
        )
    return pending[0].approval_id


def approve(
    graph_runtime: PostgresTaskGraphRuntime,
    run_id: str,
    node_id: str,
    *,
    approved_by: str,
    approval_id: str | None = None,
) -> TaskGraphRunSnapshot:
    snapshot = graph_runtime.get_status(run_id)
    if snapshot is None:
        raise KeyError(run_id)
    node = next((item for item in snapshot.graph.nodes if item.id == node_id), None)
    state = next((item for item in snapshot.node_states if item.node_id == node_id), None)
    if node is None or state is None or state.status != "waiting_for_approval":
        raise TaskGraphRuntimeError("node is not waiting for approval")
    if node.kind == "approval":
        stored = graph_runtime._store_node(
            run_id,
            node_id,
            status="completed",
            output={**state.output, "approved": True, "approved_by": approved_by, "result": True},
            expected_state=state,
            expected_statuses=("waiting_for_approval",),
            graph_revision=snapshot.graph.revision,
        )
        if stored is None:
            raise TaskGraphRuntimeError("approval node changed during approval")
        return graph_runtime.advance(run_id)

    child_approval_id = graph_runtime._resolve_child_approval_id(
        state,
        approval_id,
    )
    graph_runtime.agent_service.command(
        AgentRunCommand(
            run_id=str(state.child_run_id),
            command_type="approve",
            payload={"approval_id": child_approval_id, "issued_by": approved_by},
        )
    )
    return graph_runtime.advance(run_id)


def reject(
    graph_runtime: PostgresTaskGraphRuntime,
    run_id: str,
    node_id: str,
    *,
    approval_id: str | None = None,
) -> TaskGraphRunSnapshot:
    snapshot = graph_runtime.get_status(run_id)
    if snapshot is None:
        raise KeyError(run_id)
    node = next((item for item in snapshot.graph.nodes if item.id == node_id), None)
    state = next((item for item in snapshot.node_states if item.node_id == node_id), None)
    if node is None or state is None or state.status != "waiting_for_approval":
        raise TaskGraphRuntimeError("node is not waiting for approval")
    if node.kind == "approval":
        stored = graph_runtime._store_node(
            run_id,
            node_id,
            status="cancelled",
            output={**state.output, "approved": False, "result": False},
            last_error="approval_rejected",
            expected_state=state,
            expected_statuses=("waiting_for_approval",),
            graph_revision=snapshot.graph.revision,
        )
        if stored is None:
            raise TaskGraphRuntimeError("approval node changed during rejection")
        return graph_runtime.cancel(run_id, reason="approval_rejected")

    child_approval_id = graph_runtime._resolve_child_approval_id(
        state,
        approval_id,
    )
    graph_runtime.agent_service.command(
        AgentRunCommand(
            run_id=str(state.child_run_id),
            command_type="reject",
            payload={"approval_id": child_approval_id},
        )
    )
    return graph_runtime.cancel(run_id, reason="child_approval_rejected")


def _cancel_child(graph_runtime: PostgresTaskGraphRuntime, child_run_id: str) -> None:
    try:
        graph_runtime.agent_service.command(
            AgentRunCommand(
                run_id=child_run_id,
                command_type="cancel",
                payload={"reason": "task_graph_cancelled"},
            )
        )
    except Exception as exc:
        log_recovered_exception("TaskGraph child cancellation", exc)
        pass


def cancel(
    graph_runtime: PostgresTaskGraphRuntime,
    run_id: str,
    *,
    reason: str = "cancelled_by_user",
) -> TaskGraphRunSnapshot:
    snapshot = graph_runtime.get_status(run_id)
    if snapshot is None:
        raise KeyError(run_id)
    if snapshot.status in {"completed", "failed", "cancelled"}:
        return snapshot
    for state in snapshot.node_states:
        if (
            state.status in {"ready", "running", "waiting_for_approval"}
            and state.child_run_id
        ):
            graph_runtime._cancel_child(state.child_run_id)
        if state.status not in {"completed", "failed", "cancelled", "skipped"}:
            graph_runtime._store_node(
                run_id,
                state.node_id,
                status="cancelled",
                last_error=reason,
                expected_state=state,
                expected_statuses=(state.status,),
                graph_revision=snapshot.graph.revision,
            )
    latest = graph_runtime.get_status(run_id)
    assert latest is not None
    return graph_runtime._set_run_status(latest, "cancelled", last_error=reason)


def revise(
    graph_runtime: PostgresTaskGraphRuntime,
    run_id: str,
    revised_graph: TaskGraph,
    *,
    user_instruction: str,
    reuse_completed: bool = True,
) -> TaskGraphRunSnapshot:
    snapshot = graph_runtime.get_status(run_id)
    if snapshot is None:
        raise KeyError(run_id)
    if snapshot.status in {"completed", "failed", "cancelled"}:
        raise TaskGraphRuntimeError(
            f"cannot revise terminal task graph:{snapshot.status}"
        )

    normalized = revised_graph.model_copy(
        update={
            "graph_id": snapshot.graph.graph_id,
            "revision": snapshot.graph.revision + 1,
        }
    )
    plan = plan_graph_revision(
        snapshot.graph,
        normalized,
        snapshot.node_states,
    )
    states = graph_runtime._state_map(snapshot.node_states)
    preserved = (
        set(plan.reusable_completed_node_ids)
        | set(plan.retained_running_node_ids)
        if reuse_completed
        else set()
    )
    invalidate = set(plan.invalidated_node_ids) | set(plan.removed_node_ids)
    if not reuse_completed:
        invalidate.update(states)
    children_to_cancel = [
        state.child_run_id
        for node_id in invalidate
        if (state := states.get(node_id)) is not None
        and state.status in {"ready", "running", "waiting_for_approval"}
        and state.child_run_id
    ]

    # Win the graph-revision CAS before cancelling old children. A losing
    # steering command must never cancel work retained by the winner.
    with unit_of_work(graph_runtime.database) as work:
        repository = PostgresTaskGraphRepository(work.connection, graph_runtime.context)
        repository.apply_revision(
            run_id,
            normalized,
            user_instruction=user_instruction,
            reusable_node_ids=preserved,
        )
        work.commit()
    for child_run_id in children_to_cancel:
        graph_runtime._cancel_child(str(child_run_id))
    return graph_runtime.advance(run_id)


def recover(graph_runtime: PostgresTaskGraphRuntime, run_id: str) -> TaskGraphRunSnapshot:
    """Resume durable graph work after coordinator restart.

    Claimed Agent nodes are restart-safe because their child run id was
    persisted before launch. A claimed capability node has unknown external
    outcome and therefore fails closed instead of being executed twice.
    """

    snapshot = graph_runtime.get_status(run_id)
    if snapshot is None:
        raise KeyError(run_id)
    if snapshot.status in {"completed", "failed", "cancelled"}:
        return snapshot
    node_map = graph_runtime._node_map(snapshot.graph)
    states = graph_runtime._state_map(snapshot.node_states)
    optimization = graph_runtime._optimization_plan(snapshot.graph)
    recovered_batches: set[str] = set()
    for state in snapshot.node_states:
        if state.status != "ready":
            continue
        node = node_map[state.node_id]
        batch_descriptor = state.output.get("evidence_batch")
        if isinstance(batch_descriptor, dict):
            leader_id = str(
                batch_descriptor.get("leader_id") or ""
            ).strip()
            batch_ids = [
                str(value)
                for value in batch_descriptor.get("node_ids") or []
                if str(value) in node_map
            ]
            batch_key = (
                f"{state.child_run_id or ''}:"
                + ",".join(sorted(batch_ids))
            )
            if batch_key in recovered_batches:
                continue
            recovered_batches.add(batch_key)
            batch_nodes = [
                node_map[node_id]
                for node_id in batch_ids
                if states[node_id].status in {"ready", "running"}
            ]
            ready_claims = [
                states[node_id]
                for node_id in batch_ids
                if states[node_id].status == "ready"
            ]
            child = (
                graph_runtime.agent_service.get(state.child_run_id)
                if state.child_run_id
                else None
            )
            if child is not None:
                for claim in ready_claims:
                    graph_runtime._store_node(
                        run_id,
                        claim.node_id,
                        status="running",
                        child_run_id=state.child_run_id,
                        output=claim.output,
                        expected_state=claim,
                        expected_statuses=("ready",),
                        graph_revision=snapshot.graph.revision,
                    )
                continue
            if (
                leader_id
                and batch_nodes
                and ready_claims
                and len(ready_claims) == len(batch_nodes)
            ):
                batch_nodes.sort(
                    key=lambda item: (
                        0 if item.id == leader_id else 1,
                        item.id,
                    )
                )
                claims_by_id = {
                    claim.node_id: claim for claim in ready_claims
                }
                selected_model = optimization.model_selections.get(
                    batch_nodes[0].id
                )
                graph_runtime._start_evidence_batch(
                    run_id,
                    snapshot.graph,
                    states,
                    batch_nodes,
                    [claims_by_id[item.id] for item in batch_nodes],
                    selected_model=selected_model,
                )
                continue
            # A partial batch descriptor cannot be safely reconstructed.
            for claim in ready_claims:
                graph_runtime._store_node(
                    run_id,
                    claim.node_id,
                    status="failed",
                    last_error="evidence_batch_recovery_incomplete",
                    expected_state=claim,
                    expected_statuses=("ready",),
                    graph_revision=snapshot.graph.revision,
                )
            continue

        if state.child_run_id:
            child = graph_runtime.agent_service.get(state.child_run_id)
            if child is not None:
                graph_runtime._store_node(
                    run_id,
                    node.id,
                    status=(
                        "waiting_for_approval"
                        if child.status == "waiting_for_approval"
                        else "running"
                    ),
                    child_run_id=state.child_run_id,
                    output={
                        **state.output,
                        "child_run_id": state.child_run_id,
                        "status": child.status,
                    },
                    expected_state=state,
                    expected_statuses=("ready",),
                    graph_revision=snapshot.graph.revision,
                )
                continue

        if node.kind == "capability":
            graph_runtime._store_node(
                run_id,
                node.id,
                status="failed",
                last_error="capability_outcome_unknown_after_coordinator_recovery",
                expected_state=state,
                expected_statuses=("ready",),
                graph_revision=snapshot.graph.revision,
            )
            continue
        graph_runtime._execute_claimed_node(
            run_id,
            snapshot.graph,
            states,
            node,
            state,
            selected_model=optimization.model_selections.get(node.id),
        )
    return graph_runtime.advance(run_id)
