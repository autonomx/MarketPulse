"""TaskGraph scheduling: readiness, node claims, child polling, node execution and advance (WP-8.2).

Functions over the TaskGraph runtime; ``PostgresTaskGraphRuntime`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception
import json
import uuid
from typing import Any
from .capability_requests import AssistantToolRequest
from app.persistence.unit_of_work import unit_of_work
from .contracts import ModelRef
from .task_graph import (
    TaskEdge,
    TaskGraph,
    TaskGraphRunSnapshot,
    TaskNode,
    TaskNodeRunState,
    task_node_fingerprint,
)
from .task_graph_optimizer import (
    TaskGraphOptimizationPlan,
    optimize_task_graph,
)
from .task_graph_repository import PostgresTaskGraphRepository
from typing import TYPE_CHECKING
from .task_graph_runtime import (
    TaskGraphRuntimeError,
    _AGENT_NODE_KINDS,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.task_graph_runtime import PostgresTaskGraphRuntime


# Returned by an extracted step that did not settle its caller.
_CONTINUE = object()


def _active_execution_count(states: list[TaskNodeRunState]) -> int:
    """Count active child executions rather than graph-node projections.

    Compatible evidence nodes can share one durable child run. Counting
    each projected node as a separate slot defeats Phase 19 batching and can
    starve otherwise independent work, so all states sharing a child id
    consume exactly one parallel slot. Claimed non-child work is still
    counted by node id.
    """

    identities: set[tuple[str, str]] = set()
    for state in states:
        if state.status not in {"ready", "running"}:
            continue
        if state.child_run_id:
            identities.add(("child", str(state.child_run_id)))
        else:
            identities.add(("node", state.node_id))
    return len(identities)


def _incoming(graph_runtime: PostgresTaskGraphRuntime, graph: TaskGraph, node_id: str) -> list[TaskEdge]:
    return [edge for edge in graph.edges if edge.target == node_id]


def _predecessor_outputs(
    graph_runtime: PostgresTaskGraphRuntime,
    graph: TaskGraph,
    states: dict[str, TaskNodeRunState],
    node_id: str,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for edge in graph_runtime._incoming(graph, node_id):
        source = states[edge.source]
        value: Any = source.output
        if edge.source_output:
            value = source.output.get(edge.source_output)
        key = edge.target_input or edge.source
        output[key] = value
    return output


def _edge_allows(
    graph_runtime: PostgresTaskGraphRuntime,
    edge: TaskEdge,
    state: TaskNodeRunState,
) -> bool:
    if edge.kind != "condition":
        return True
    observed = (
        state.output.get(edge.source_output)
        if edge.source_output
        else state.output.get("matched")
    )
    expected = True if edge.expected_value is None else edge.expected_value
    return observed == expected


def _readiness(
    graph_runtime: PostgresTaskGraphRuntime,
    graph: TaskGraph,
    states: dict[str, TaskNodeRunState],
    node: TaskNode,
) -> tuple[bool, bool]:
    """Return (ready, should_skip)."""

    incoming = graph_runtime._incoming(graph, node.id)
    if not incoming:
        return True, False
    for edge in incoming:
        source = states[edge.source]
        if source.status in {"failed", "cancelled"}:
            return False, node.optional
        if source.status not in {"completed", "skipped"}:
            return False, False
        if not graph_runtime._edge_allows(edge, source):
            return False, True
    return True, False


def _poll_children(graph_runtime: PostgresTaskGraphRuntime, snapshot: TaskGraphRunSnapshot) -> None:
    node_map = graph_runtime._node_map(snapshot.graph)
    for state in snapshot.node_states:
        if (
            state.status not in {"running", "waiting_for_approval"}
            or not state.child_run_id
        ):
            continue
        node = node_map[state.node_id]
        child = graph_runtime.agent_service.get(state.child_run_id)
        if child is None:
            continue
        if child.status == "waiting_for_approval":
            if state.status != "waiting_for_approval":
                try:
                    approvals = graph_runtime.agent_service.approvals(
                        child.run_id,
                        state="pending",
                    )
                    pending = [
                        item.model_dump(mode="json")
                        for item in approvals
                    ]
                except Exception:
                    pending = []
                graph_runtime._store_node(
                    snapshot.run_id,
                    node.id,
                    status="waiting_for_approval",
                    output={
                        **state.output,
                        "child_run_id": child.run_id,
                        "status": child.status,
                        "pending_approvals": pending,
                    },
                    expected_state=state,
                    expected_statuses=("running",),
                    graph_revision=snapshot.graph.revision,
                )
            continue
        if (
            state.status == "waiting_for_approval"
            and child.status not in {"completed", "failed", "cancelled"}
        ):
            graph_runtime._store_node(
                snapshot.run_id,
                node.id,
                status="running",
                output={
                    **state.output,
                    "child_run_id": child.run_id,
                    "status": child.status,
                    "pending_approvals": [],
                },
                expected_state=state,
                expected_statuses=("waiting_for_approval",),
                graph_revision=snapshot.graph.revision,
            )
            continue
        if child.status not in {"completed", "failed", "cancelled"}:
            continue

        terminal_expected_statuses = ("running", "waiting_for_approval")
        output: dict[str, Any] = {
            "child_run_id": child.run_id,
            "status": child.status,
        }
        if child.status == "completed":
            try:
                artifacts = graph_runtime.agent_service.artifacts(child.run_id)
                output["artifacts"] = [
                    item.model_dump(mode="json")
                    for item in artifacts
                ]
            except Exception:
                output["artifacts"] = []
            final_result = graph_runtime._child_result(child.run_id)
            output["result"] = (
                final_result
                if final_result is not None
                else {
                    "child_run_id": child.run_id,
                    "status": child.status,
                    "artifacts": output["artifacts"],
                }
            )
            if node.evidence_policy.requirement == "required":
                try:
                    evidence = graph_runtime.agent_service.evidence_set(child.run_id)
                    output["evidence_passed"] = bool(evidence.passed)
                    output["evidence"] = evidence.model_dump(mode="json")
                    if not evidence.passed:
                        graph_runtime._store_node(
                            snapshot.run_id,
                            node.id,
                            status="failed",
                            output=output,
                            last_error="node_evidence_requirements_unsatisfied",
                            expected_state=state,
                            expected_statuses=terminal_expected_statuses,
                            graph_revision=snapshot.graph.revision,
                        )
                        continue
                except Exception as exc:
                    graph_runtime._store_node(
                        snapshot.run_id,
                        node.id,
                        status="failed",
                        output=output,
                        last_error=f"node_evidence_evaluation_failed:{type(exc).__name__}:{exc}"[:1000],
                        expected_state=state,
                        expected_statuses=terminal_expected_statuses,
                        graph_revision=snapshot.graph.revision,
                    )
                    continue
            graph_runtime._store_node(
                snapshot.run_id,
                node.id,
                status="completed",
                output=output,
                expected_state=state,
                expected_statuses=terminal_expected_statuses,
                graph_revision=snapshot.graph.revision,
            )
        elif node.optional:
            graph_runtime._store_node(
                snapshot.run_id,
                node.id,
                status="skipped",
                output=output,
                last_error=child.last_error or child.status,
                expected_state=state,
                expected_statuses=terminal_expected_statuses,
                graph_revision=snapshot.graph.revision,
            )
        else:
            graph_runtime._store_node(
                snapshot.run_id,
                node.id,
                status="failed",
                output=output,
                last_error=child.last_error or f"child_{child.status}",
                expected_state=state,
                expected_statuses=terminal_expected_statuses,
                graph_revision=snapshot.graph.revision,
            )


def _claim_node(
    graph_runtime: PostgresTaskGraphRuntime,
    run_id: str,
    graph: TaskGraph,
    node: TaskNode,
    *,
    child_run_id: str | None = None,
    claim_output: dict[str, Any] | None = None,
) -> TaskNodeRunState | None:
    with unit_of_work(graph_runtime.database) as work:
        repository = PostgresTaskGraphRepository(work.connection, graph_runtime.context)
        state = repository.claim_node(
            run_id,
            node.id,
            child_run_id=child_run_id,
            claim_output=claim_output,
            expected_fingerprint=task_node_fingerprint(node),
            expected_graph_revision=graph.revision,
        )
        work.commit()
    return state


def _store_node(
    graph_runtime: PostgresTaskGraphRuntime,
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
    with unit_of_work(graph_runtime.database) as work:
        repository = PostgresTaskGraphRepository(work.connection, graph_runtime.context)
        state = repository.update_node(
            run_id,
            node_id,
            status=status,
            child_run_id=child_run_id,
            output=output,
            last_error=last_error,
            increment_attempts=increment_attempts,
            expected_fingerprint=(
                expected_state.fingerprint
                if expected_state is not None
                else None
            ),
            expected_child_run_id=(
                expected_state.child_run_id
                if expected_state is not None
                else None
            ),
            match_child_run_id=expected_state is not None,
            expected_statuses=expected_statuses,
            expected_graph_revision=graph_revision,
        )
        work.commit()
    return state


def _child_result(graph_runtime: PostgresTaskGraphRuntime, child_run_id: str) -> str | None:
    """Return the latest visible terminal model message for graph dataflow."""

    try:
        events = graph_runtime.agent_service.events(child_run_id, after_sequence=0)
    except Exception as exc:
        log_recovered_exception("TaskGraph child result lookup", exc)
        return None
    for event in reversed(list(events)):
        if event.event_type != "model.message":
            continue
        if str(event.payload.get("phase") or "") != "message_end":
            continue
        text = str(event.payload.get("text") or "").strip()
        if text:
            return text
    return None


def _set_run_status(
    graph_runtime: PostgresTaskGraphRuntime,
    snapshot: TaskGraphRunSnapshot,
    status: str,
    *,
    last_error: str | None = None,
) -> TaskGraphRunSnapshot:
    if snapshot.status == status and snapshot.last_error == last_error:
        return snapshot
    with unit_of_work(graph_runtime.database) as work:
        repository = PostgresTaskGraphRepository(work.connection, graph_runtime.context)
        current = repository.update_run_status(
            snapshot.run_id,
            status,
            last_error=last_error,
            expected_revision=snapshot.revision,
            expected_graph_revision=snapshot.graph.revision,
            expected_statuses=(snapshot.status,),
        )
        if current is None:
            current = repository.get_run(snapshot.run_id)
            if current is None:
                raise KeyError(snapshot.run_id)
        work.commit()
    return current


def _optimization_plan(graph_runtime: PostgresTaskGraphRuntime, graph: TaskGraph) -> TaskGraphOptimizationPlan:
    return optimize_task_graph(
        graph,
        model_overrides=graph_runtime.model_overrides,
    )


def _optimized_nodes(
    graph_runtime: PostgresTaskGraphRuntime,
    graph: TaskGraph,
    plan: TaskGraphOptimizationPlan,
) -> list[TaskNode]:
    """Apply optimizer parallel levels, speculation, and critical-path order."""

    node_map = graph_runtime._node_map(graph)
    speculative = set(plan.speculative_read_nodes)
    priority = {
        node_id: index
        for index, node_id in enumerate(plan.cost_priority)
    }
    ordered: list[TaskNode] = []
    seen: set[str] = set()
    for level in plan.parallel_groups:
        for node_id in sorted(
            level,
            key=lambda value: (
                0 if value in speculative else 1,
                priority.get(value, 10_000),
                value,
            ),
        ):
            if node_id in node_map and node_id not in seen:
                seen.add(node_id)
                ordered.append(node_map[node_id])
    for node in graph.nodes:
        if node.id not in seen:
            ordered.append(node)
    return ordered


def _cache_source(
    graph_runtime: PostgresTaskGraphRuntime,
    node: TaskNode,
    states: dict[str, TaskNodeRunState],
    plan: TaskGraphOptimizationPlan,
) -> TaskNodeRunState | None:
    key = plan.cache_keys.get(node.id)
    if key is None:
        return None
    for source_id, source_key in plan.cache_keys.items():
        if source_id == node.id or source_key != key:
            continue
        state = states.get(source_id)
        if state is not None and state.status == "completed":
            return state
    return None


def _condition(graph_runtime: PostgresTaskGraphRuntime, expression: str, inputs: dict[str, Any]) -> bool:
    clean = str(expression or "").strip()
    negate = clean.startswith("not ")
    if negate:
        clean = clean[4:].strip()
    if clean.startswith("exists:"):
        value = inputs.get(clean.split(":", 1)[1])
        matched = value is not None
    elif clean.startswith("truthy:"):
        value = inputs.get(clean.split(":", 1)[1])
        matched = bool(value)
    else:
        raise TaskGraphRuntimeError(
            f"unsupported deterministic condition:{expression}"
        )
    return not matched if negate else matched


def _execute_claimed_node(
    graph_runtime: PostgresTaskGraphRuntime,
    run_id: str,
    graph: TaskGraph,
    states: dict[str, TaskNodeRunState],
    node: TaskNode,
    claimed: TaskNodeRunState,
    *,
    selected_model: ModelRef | None = None,
) -> bool:
    inputs = graph_runtime._predecessor_outputs(graph, states, node.id)
    if node.kind == "join":
        graph_runtime._store_node(
            run_id,
            node.id,
            status="completed",
            output={"result": inputs},
            expected_state=claimed,
            expected_statuses=("ready",),
            graph_revision=graph.revision,
        )
        return True

    if node.kind == "condition":
        matched = graph_runtime._condition(str(node.condition), inputs)
        graph_runtime._store_node(
            run_id,
            node.id,
            status="completed",
            output={"matched": matched, "inputs": inputs, "result": matched},
            expected_state=claimed,
            expected_statuses=("ready",),
            graph_revision=graph.revision,
        )
        return True

    if node.kind == "approval":
        graph_runtime._store_node(
            run_id,
            node.id,
            status="waiting_for_approval",
            output={"inputs": inputs},
            expected_state=claimed,
            expected_statuses=("ready",),
            graph_revision=graph.revision,
        )
        return True

    outcome = _execute_capability_node(node, run_id, claimed, inputs, graph_runtime, graph)
    if outcome is not _CONTINUE:
        return outcome

    return _start_agent_node(node, claimed, graph_runtime, run_id, graph, selected_model, inputs)


def _execute_capability_node(node, run_id, claimed, inputs, graph_runtime, graph):
    """Run a capability node through the governed executor; an optional node that fails is skipped."""
    if node.kind == "capability":
        namespace = str(node.capability_id).split(".", 1)[0]
        request = AssistantToolRequest(
            tool_id=namespace,
            action_id=str(node.capability_id),
            session_id=f"task-graph:{run_id}",
            proposal_id=f"task-graph:{run_id}:{node.id}:{claimed.attempts}",
            input={**inputs, **node.input_template},
        )
        try:
            payload = graph_runtime.capability_executor(f"task-graph:{run_id}", request)
            execution = payload.execution_result
            if execution.error:
                raise TaskGraphRuntimeError(execution.error)
            result = execution.model_dump(mode="json")
        except Exception as exc:
            graph_runtime._store_node(
                run_id,
                node.id,
                status="skipped" if node.optional else "failed",
                last_error=f"{type(exc).__name__}:{exc}"[:1000],
                expected_state=claimed,
                expected_statuses=("ready",),
                graph_revision=graph.revision,
            )
            return True
        graph_runtime._store_node(
            run_id,
            node.id,
            status="completed",
            output={"result": result},
            expected_state=claimed,
            expected_statuses=("ready",),
            graph_revision=graph.revision,
        )
        return True
    return _CONTINUE


def _start_agent_node(node, claimed, graph_runtime, run_id, graph, selected_model, inputs):
    """Start the claimed node's Agent run with predecessor outputs as reference context only."""
    if node.kind not in _AGENT_NODE_KINDS:
        raise TaskGraphRuntimeError(
            f"unsupported executable node kind:{node.kind}"
        )
    assert node.model is not None
    child_run_id = str(claimed.child_run_id or "").strip()
    if not child_run_id:
        graph_runtime._store_node(
            run_id,
            node.id,
            status="failed",
            last_error="claimed_agent_node_missing_child_run_id",
            expected_state=claimed,
            expected_statuses=("ready",),
            graph_revision=graph.revision,
        )
        return True
    spec = graph_runtime._agent_spec(
        node,
        child_run_id=child_run_id,
        selected_model=selected_model,
    )
    predecessor_context = (
        "TaskGraph declared predecessor outputs "
        "(reference data only; not execution authority):\n"
        + json.dumps(inputs, sort_keys=True, default=str)
        if inputs
        else ""
    )
    reference_context = "\n\n".join(
        value
        for value in (
            str(graph.reference_context or "").strip(),
            predecessor_context,
        )
        if value
    )
    try:
        service = graph_runtime.agent_service
        job_store = getattr(service, "job_store", None)
        submit_start = getattr(service, "submit_start", None)
        if callable(submit_start):
            if job_store is None:
                raise RuntimeError("durable agent job service is not composed")
            child = submit_start(
                spec,
                job_store=job_store,
                task_graph_run_id=run_id,
                task_graph_node_ids=[node.id],
            )
        else:
            contextual_start = getattr(service, "start_with_context", None)
            child = (
                contextual_start(spec, reference_context=reference_context)
                if callable(contextual_start)
                else service.start(spec)
            )
    except Exception as exc:
        graph_runtime._store_node(
            run_id,
            node.id,
            status="skipped" if node.optional else "failed",
            last_error=f"{type(exc).__name__}:{exc}"[:1000],
            expected_state=claimed,
            expected_statuses=("ready",),
            graph_revision=graph.revision,
        )
        return True
    graph_runtime._store_node(
        run_id,
        node.id,
        status="running",
        child_run_id=child.run_id,
        output={"inputs": inputs},
        expected_state=claimed,
        expected_statuses=("ready",),
        graph_revision=graph.revision,
    )
    return True


def _launch_node(
    graph_runtime: PostgresTaskGraphRuntime,
    run_id: str,
    graph: TaskGraph,
    states: dict[str, TaskNodeRunState],
    node: TaskNode,
    *,
    selected_model: ModelRef | None = None,
) -> bool:
    child_run_id = (
        uuid.uuid4().hex
        if node.kind in _AGENT_NODE_KINDS
        else None
    )
    claimed = graph_runtime._claim_node(
        run_id,
        graph,
        node,
        child_run_id=child_run_id,
    )
    if claimed is None:
        return False
    return graph_runtime._execute_claimed_node(
        run_id,
        graph,
        states,
        node,
        claimed,
        selected_model=selected_model,
    )


def _fail_graph(
    graph_runtime: PostgresTaskGraphRuntime,
    snapshot: TaskGraphRunSnapshot,
    *,
    last_error: str,
) -> TaskGraphRunSnapshot:
    for state in snapshot.node_states:
        if state.status in {"completed", "failed", "cancelled", "skipped"}:
            continue
        if (
            state.child_run_id
            and state.status in {
                "ready",
                "running",
                "waiting_for_approval",
            }
        ):
            graph_runtime._cancel_child(state.child_run_id)
        graph_runtime._store_node(
            snapshot.run_id,
            state.node_id,
            status="cancelled",
            last_error=f"graph_failed:{last_error}"[:1000],
            expected_state=state,
            expected_statuses=(state.status,),
            graph_revision=snapshot.graph.revision,
        )
    latest = graph_runtime.get_status(snapshot.run_id)
    assert latest is not None
    return graph_runtime._set_run_status(
        latest,
        "failed",
        last_error=last_error[:1000],
    )


def advance(graph_runtime: PostgresTaskGraphRuntime, run_id: str) -> TaskGraphRunSnapshot:
    snapshot = graph_runtime.get_status(run_id)
    if snapshot is None:
        raise KeyError(run_id)
    if snapshot.status in {"completed", "failed", "cancelled"}:
        return snapshot

    graph_runtime._poll_children(snapshot)
    snapshot = graph_runtime.get_status(run_id)
    assert snapshot is not None
    node_map = graph_runtime._node_map(snapshot.graph)
    states = graph_runtime._state_map(snapshot.node_states)

    required_failure = next(
        (
            state
            for state in snapshot.node_states
            if state.status == "failed" and not node_map[state.node_id].optional
        ),
        None,
    )
    if required_failure is not None:
        return graph_runtime._fail_graph(
            snapshot,
            last_error=(
                f"task_graph_node_failed:{required_failure.node_id}:"
                f"{required_failure.last_error or ''}"
            ),
        )

    running = graph_runtime._active_execution_count(snapshot.node_states)
    available_slots = max(0, snapshot.graph.max_parallel_nodes - running)
    progressed = True
    while progressed:
        progressed = False
        snapshot = graph_runtime.get_status(run_id)
        assert snapshot is not None
        states = graph_runtime._state_map(snapshot.node_states)
        running = graph_runtime._active_execution_count(snapshot.node_states)
        available_slots = max(0, snapshot.graph.max_parallel_nodes - running)
        optimization = graph_runtime._optimization_plan(snapshot.graph)

        for node in graph_runtime._optimized_nodes(snapshot.graph, optimization):
            state = states[node.id]
            if state.status != "pending":
                continue
            ready, skip = graph_runtime._readiness(snapshot.graph, states, node)
            if skip:
                graph_runtime._store_node(
                    run_id,
                    node.id,
                    status="skipped",
                    last_error="graph_condition_or_optional_dependency_skipped",
                    expected_state=state,
                    expected_statuses=("pending",),
                    graph_revision=snapshot.graph.revision,
                )
                progressed = True
                continue
            if not ready:
                continue

            cache_source = graph_runtime._cache_source(
                node,
                states,
                optimization,
            )
            if cache_source is not None:
                cached_output = {
                    **cache_source.output,
                    "cache_reused_from": cache_source.node_id,
                }
                stored = graph_runtime._store_node(
                    run_id,
                    node.id,
                    status="completed",
                    output=cached_output,
                    expected_state=state,
                    expected_statuses=("pending",),
                    graph_revision=snapshot.graph.revision,
                )
                if stored is not None:
                    progressed = True
                continue

            if node.kind in _AGENT_NODE_KINDS and available_slots <= 0:
                continue

            if node.kind == "evidence_read":
                evidence_batch, candidates = graph_runtime._batch_candidates(
                    snapshot.graph,
                    states,
                    node,
                    optimization,
                )
                if (
                    evidence_batch is not None
                    and len(candidates) >= 2
                ):
                    # Only the optimizer-selected first candidate claims the
                    # whole batch; peers defer rather than racing a duplicate.
                    if candidates[0].id != node.id:
                        continue
                    child_run_id = uuid.uuid4().hex
                    claims = graph_runtime._claim_evidence_batch(
                        run_id,
                        snapshot.graph,
                        candidates,
                        child_run_id=child_run_id,
                        batch=evidence_batch,
                    )
                    if claims:
                        graph_runtime._start_evidence_batch(
                            run_id,
                            snapshot.graph,
                            states,
                            candidates,
                            claims,
                            selected_model=(
                                optimization.model_selections.get(node.id)
                            ),
                        )
                        progressed = True
                        available_slots -= 1
                        continue

            if graph_runtime._launch_node(
                run_id,
                snapshot.graph,
                states,
                node,
                selected_model=optimization.model_selections.get(node.id),
            ):
                progressed = True
                if node.kind in _AGENT_NODE_KINDS:
                    available_slots -= 1

    return _settle_graph_status(graph_runtime, run_id)


def _settle_graph_status(graph_runtime, run_id):
    """The graph's status after a scheduling pass: failed, completed, waiting for approval, or running."""
    snapshot = graph_runtime.get_status(run_id)
    assert snapshot is not None
    node_map = graph_runtime._node_map(snapshot.graph)
    required_failure = next(
        (
            state
            for state in snapshot.node_states
            if state.status == "failed"
            and not node_map[state.node_id].optional
        ),
        None,
    )
    if required_failure is not None:
        return graph_runtime._fail_graph(
            snapshot,
            last_error=(
                f"task_graph_node_failed:{required_failure.node_id}:"
                f"{required_failure.last_error or ''}"
            ),
        )
    statuses = {state.status for state in snapshot.node_states}
    if all(
        state.status in {"completed", "skipped"}
        for state in snapshot.node_states
    ):
        return graph_runtime._set_run_status(snapshot, "completed")
    if "waiting_for_approval" in statuses:
        # Surface an approval immediately even while independent siblings
        # are still running. The supervisor continues advancing waiting
        # graphs, so this does not pause safe parallel work.
        return graph_runtime._set_run_status(snapshot, "waiting_for_approval")
    return graph_runtime._set_run_status(snapshot, "running")
