"""TaskGraph steering/replanning semantics.

Revisions may reuse completed work only when both the node authority envelope and
its dependency lineage are unchanged. Removed or changed authority is never
inherited implicitly, and invalidation propagates transitively through the DAG.
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from .contracts import SuccessCriterion
from .task_graph import TaskEdge, TaskGraph, TaskNode, TaskNodeRunState, task_node_fingerprint
from typing import Any


class TaskGraphRevisionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reusable_completed_node_ids: list[str] = Field(default_factory=list)
    retained_running_node_ids: list[str] = Field(default_factory=list)
    invalidated_node_ids: list[str] = Field(default_factory=list)
    removed_node_ids: list[str] = Field(default_factory=list)
    added_node_ids: list[str] = Field(default_factory=list)
    authority_reduced_node_ids: list[str] = Field(default_factory=list)


def task_node_revision_fingerprint(graph: TaskGraph, node_id: str) -> str:
    node = next(item for item in graph.nodes if item.id == node_id)
    incoming = sorted(
        [
            edge.model_dump(mode="json")
            for edge in graph.edges
            if edge.target == node_id
        ],
        key=lambda row: json.dumps(row, sort_keys=True),
    )
    payload = {
        "node": task_node_fingerprint(node),
        "incoming": incoming,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _authority_set(graph: TaskGraph, node_id: str) -> set[str]:
    node = next(item for item in graph.nodes if item.id == node_id)
    return set(node.required_local_capabilities) | set(node.required_external_capabilities)


def _transitive_descendants(graph: TaskGraph, roots: set[str]) -> set[str]:
    """Return every revised-graph descendant of the supplied invalidation roots."""

    outgoing: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, set()).add(edge.target)
    invalidated = {node_id for node_id in roots if node_id in outgoing}
    pending = list(invalidated)
    while pending:
        source = pending.pop()
        for target in outgoing.get(source, set()):
            if target in invalidated:
                continue
            invalidated.add(target)
            pending.append(target)
    return invalidated


def plan_graph_revision(
    previous: TaskGraph,
    revised: TaskGraph,
    node_states: list[TaskNodeRunState],
) -> TaskGraphRevisionPlan:
    if revised.revision != previous.revision + 1:
        raise ValueError("task graph revision must advance exactly once")

    old_nodes = {node.id: node for node in previous.nodes}
    new_nodes = {node.id: node for node in revised.nodes}
    states = {state.node_id: state for state in node_states}

    removed = sorted(set(old_nodes) - set(new_nodes))
    added = sorted(set(new_nodes) - set(old_nodes))
    common = sorted(set(old_nodes) & set(new_nodes))
    reduced: list[str] = []
    directly_invalidated: set[str] = set()

    for node_id in common:
        old_authority = _authority_set(previous, node_id)
        new_authority = _authority_set(revised, node_id)
        if new_authority < old_authority:
            reduced.append(node_id)
        if (
            task_node_revision_fingerprint(previous, node_id)
            != task_node_revision_fingerprint(revised, node_id)
        ):
            directly_invalidated.add(node_id)

    # A node can have an identical literal incoming edge while its predecessor's
    # output contract changed. Reusing that node would preserve stale data. Once
    # any executable contract changes, every descendant must be recomputed from
    # the new lineage, including joins and synthesis nodes.
    invalidated_set = _transitive_descendants(revised, directly_invalidated)
    reusable: list[str] = []
    retained_running: list[str] = []
    for node_id in common:
        if node_id in invalidated_set:
            continue
        state = states.get(node_id)
        if state is not None and state.status == "completed":
            reusable.append(node_id)
        elif (
            state is not None
            and state.status in {"running", "waiting_for_approval"}
        ):
            retained_running.append(node_id)

    return TaskGraphRevisionPlan(
        reusable_completed_node_ids=reusable,
        retained_running_node_ids=retained_running,
        invalidated_node_ids=sorted(invalidated_set),
        removed_node_ids=removed,
        added_node_ids=added,
        authority_reduced_node_ids=sorted(reduced),
    )



def merge_task_graph_continuation(
    previous: TaskGraph,
    addition: TaskGraph,
    *,
    context_dependent: bool,
) -> TaskGraph:
    """Add graph work and preserve a user-facing synthesis result."""

    revision = previous.revision + 1
    prefix = f"r{revision}-"
    id_map = {node.id: f"{prefix}{node.id}" for node in addition.nodes}
    renamed_nodes = [
        node.model_copy(update={"id": id_map[node.id]})
        for node in addition.nodes
    ]
    renamed_edges = [
        edge.model_copy(
            update={
                "source": id_map[edge.source],
                "target": id_map[edge.target],
            }
        )
        for edge in addition.edges
    ]

    previous_result = str(
        previous.output_contract.get("result_node")
        or previous.nodes[-1].id
    )
    addition_result_raw = str(
        addition.output_contract.get("result_node")
        or addition.nodes[-1].id
    )
    addition_result = id_map[addition_result_raw]

    if context_dependent:
        addition_incoming = {edge.target for edge in renamed_edges}
        roots = [
            node.id
            for node in renamed_nodes
            if node.id not in addition_incoming
        ]
        for root in roots:
            renamed_edges.append(
                TaskEdge(
                    source=previous_result,
                    target=root,
                    kind="data",
                    source_output="result",
                    target_input="prior_graph_result",
                )
            )

    final_join_id = f"join-results-r{revision}"
    final_join = TaskNode(
        id=final_join_id,
        kind="join",
        objective="Aggregate prior and added graph results without new authority.",
        output_keys=["result"],
    )
    final_edges = [
        TaskEdge(
            source=previous_result,
            target=final_join_id,
            kind="data",
            source_output="result",
            target_input="previous_result",
        ),
        TaskEdge(
            source=addition_result,
            target=final_join_id,
            kind="data",
            source_output="result",
            target_input="addition_result",
        ),
    ]

    synthesis, synthesis_id = _continuation_synthesis(addition, previous, revision, final_edges, final_join_id)
    return TaskGraph(
        graph_id=previous.graph_id,
        revision=revision,
        user_request_digest=addition.user_request_digest,
        nodes=[
            *previous.nodes,
            *renamed_nodes,
            final_join,
            synthesis,
        ],
        edges=[
            *previous.edges,
            *renamed_edges,
            *final_edges,
        ],
        output_contract={"result_node": synthesis_id},
        reference_context=(
            addition.reference_context
            or previous.reference_context
        ),
        max_parallel_nodes=max(
            previous.max_parallel_nodes,
            addition.max_parallel_nodes,
        ),
    )


def _continuation_synthesis(addition, previous, revision, final_edges, final_join_id):
    """The synthesis node joining the prior result and the continuation, on the latest available model."""
    synthesis_model = next(
        (
            node.model
            for node in reversed(addition.nodes)
            if node.model is not None
        ),
        None,
    ) or next(
        (
            node.model
            for node in reversed(previous.nodes)
            if node.model is not None
        ),
        None,
    )
    if synthesis_model is None:
        raise ValueError("TaskGraph continuation requires a synthesis model")

    synthesis_id = f"synthesize-results-r{revision}"
    synthesis = TaskNode(
        id=synthesis_id,
        kind="synthesis",
        objective=(
            "Synthesize the prior TaskGraph result and the newly completed "
            "continuation into one final user-facing answer. Use only "
            "predecessor results as reference data and acquire no new authority."
        ),
        semantic_targets=["conversation"],
        semantic_action_intents=[],
        success_criteria=[
            SuccessCriterion(
                id=f"synthesis-complete-r{revision}",
                description=(
                    "Return one faithful final answer covering the retained "
                    "objective and the latest continuation."
                ),
            )
        ],
        model=synthesis_model,
        cacheable=False,
        estimated_cost=0.25,
    )
    final_edges.append(
        TaskEdge(
            source=final_join_id,
            target=synthesis_id,
            kind="data",
            source_output="result",
            target_input="graph_results",
        )
    )
    return synthesis, synthesis_id


_MUTATING_ACTION_INTENTS = {
    "workspace_mutate",
    "workspace_execute",
    "ops_execute",
    "home_mutate",
    "email_send",
    "email_draft",
    "calendar_create",
}
_TERMINAL_PERSONAL_ACTION_INTENTS = {
    "email_send",
    "email_draft",
    "calendar_create",
}


def _execution_nodes(graph: TaskGraph) -> list[TaskNode]:
    return [
        node
        for node in graph.nodes
        if node.kind not in {"join", "synthesis"}
    ]


def _requirement_contract(requirement) -> str:
    payload = requirement.model_dump(
        mode="json",
        exclude={"id"},
        exclude_none=True,
    )
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_json(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _execution_contract(node: TaskNode) -> dict[str, Any]:
    policy_shell = node.evidence_policy.model_dump(
        mode="json",
        exclude={"requirements"},
        exclude_none=True,
    )
    return {
        "kind": node.kind,
        "profile": str(node.profile_id or ""),
        "targets": set(node.semantic_targets),
        "actions": set(node.semantic_action_intents),
        "local": set(node.required_local_capabilities),
        "external": set(node.required_external_capabilities),
        "resources": {
            _stable_json(scope)
            for scope in node.resource_scopes
        },
        "evidence": {
            _requirement_contract(requirement)
            for requirement in node.evidence_policy.requirements
        },
        "evidence_policy": _stable_json(policy_shell),
        "workspace": _stable_json(node.workspace),
        "acceptance": _stable_json(node.acceptance_plan),
        "approval": node.approval_policy,
        "limits": _stable_json(node.limits),
        "capability_id": str(node.capability_id or ""),
        "input_template": _stable_json(node.input_template),
        "condition": str(node.condition or ""),
        "optional": bool(node.optional),
    }


def _contract_covers(required: dict[str, Any], available: dict[str, Any]) -> bool:
    exact_keys = {
        "kind",
        "profile",
        "evidence_policy",
        "workspace",
        "acceptance",
        "approval",
        "limits",
        "capability_id",
        "input_template",
        "condition",
        "optional",
    }
    if any(required[key] != available[key] for key in exact_keys):
        return False
    for key in {"targets", "actions", "local", "external", "resources", "evidence"}:
        if not set(required[key]) <= set(available[key]):
            return False
    return True


def task_graph_preserves_execution_contract(
    previous: TaskGraph,
    candidate: TaskGraph,
) -> bool:
    """Return whether a recompile retained every prior execution obligation.

    Matching is one-to-one rather than an aggregate-per-profile union. This
    preserves multiplicity for same-profile segments: two scoped coding reads
    cannot be silently collapsed into one node merely because they share the
    same capability ceiling. Candidate nodes may carry newly requested superset
    authority, but one candidate execution can satisfy at most one prior node.
    """

    required = [_execution_contract(node) for node in _execution_nodes(previous)]
    available = [_execution_contract(node) for node in _execution_nodes(candidate)]
    if len(available) < len(required):
        return False

    compatibility: list[list[int]] = []
    for required_contract in required:
        matches = [
            index
            for index, available_contract in enumerate(available)
            if _contract_covers(required_contract, available_contract)
        ]
        if not matches:
            return False
        compatibility.append(matches)

    candidate_to_required: dict[int, int] = {}

    def assign(required_index: int, seen: set[int]) -> bool:
        for candidate_index in compatibility[required_index]:
            if candidate_index in seen:
                continue
            seen.add(candidate_index)
            prior = candidate_to_required.get(candidate_index)
            if prior is None or assign(prior, seen):
                candidate_to_required[candidate_index] = required_index
                return True
        return False

    # Match the most constrained obligations first. Standard augmenting-path
    # reassignment then finds a complete one-to-one cover whenever one exists.
    for required_index in sorted(
        range(len(required)),
        key=lambda index: len(compatibility[index]),
    ):
        if not assign(required_index, set()):
            return False
    return True


def _core_edges(graph: TaskGraph, node_ids: set[str]) -> list[TaskEdge]:
    return [
        edge
        for edge in graph.edges
        if edge.source in node_ids and edge.target in node_ids
    ]


def _graph_roots(nodes: list[TaskNode], edges: list[TaskEdge]) -> list[TaskNode]:
    incoming = {edge.target for edge in edges}
    return [node for node in nodes if node.id not in incoming]


def _graph_leaves(nodes: list[TaskNode], edges: list[TaskEdge]) -> list[TaskNode]:
    outgoing = {edge.source for edge in edges}
    return [node for node in nodes if node.id not in outgoing]


def _edge_exists(
    edges: list[TaskEdge],
    source: str,
    target: str,
) -> bool:
    return any(
        edge.source == source and edge.target == target
        for edge in edges
    )


def merge_task_graph_additive_revision(
    previous: TaskGraph,
    addition: TaskGraph,
    *,
    context_dependent: bool,
) -> TaskGraph:
    """Compose an additive semantic delta without trusting a lossy full reparse.

    Existing executable node IDs/contracts are preserved. New executable nodes
    receive revision-prefixed IDs. Read-only additions become prerequisites of
    any existing terminal email/calendar consumer so newly requested evidence
    cannot race delivery. Later executable/mutating work is sequenced after the
    previous executable leaves. Authority-free join/synthesis is rebuilt over
    the resulting DAG.
    """

    revision = previous.revision + 1
    previous_core = _execution_nodes(previous)
    addition_core = _execution_nodes(addition)
    if not addition_core:
        raise ValueError("TaskGraph additive revision contains no executable nodes")

    previous_ids = {node.id for node in previous_core}
    previous_edges = _core_edges(previous, previous_ids)

    prefix = f"r{revision}-"
    id_map = {
        node.id: f"{prefix}{node.id}"
        for node in addition_core
    }
    renamed_addition = [
        node.model_copy(update={"id": id_map[node.id]})
        for node in addition_core
    ]
    addition_ids = {node.id for node in addition_core}
    addition_edges = [
        edge.model_copy(
            update={
                "source": id_map[edge.source],
                "target": id_map[edge.target],
            }
        )
        for edge in _core_edges(addition, addition_ids)
    ]

    edges = [*previous_edges, *addition_edges]
    previous_leaves = _graph_leaves(previous_core, previous_edges)
    addition_roots = _graph_roots(renamed_addition, addition_edges)
    addition_leaves = _graph_leaves(renamed_addition, addition_edges)

    _sequence_addition(previous_core, renamed_addition, addition_leaves, edges, context_dependent, previous_leaves, addition_roots)

    all_core, join, synthesis, synthesis_id = _rebuild_join_and_synthesis(previous_core, renamed_addition, edges, addition, previous)

    return TaskGraph(
        graph_id=previous.graph_id,
        revision=revision,
        user_request_digest=addition.user_request_digest,
        nodes=[*all_core, join, synthesis],
        edges=edges,
        output_contract={"result_node": synthesis_id},
        reference_context=(
            addition.reference_context
            or previous.reference_context
        ),
        max_parallel_nodes=max(
            previous.max_parallel_nodes,
            addition.max_parallel_nodes,
        ),
    )


def _sequence_addition(previous_core, renamed_addition, addition_leaves, edges, context_dependent, previous_leaves, addition_roots):
    """Order the addition: evidence before existing delivery consumers, later execution after the previous frontier."""
    terminal_consumers = [
        node
        for node in previous_core
        if node.profile_id == "personal-assistant"
        and bool(
            set(node.semantic_action_intents).intersection(
                _TERMINAL_PERSONAL_ACTION_INTENTS
            )
        )
    ]
    addition_is_read_only = all(
        not set(node.semantic_action_intents).intersection(
            _MUTATING_ACTION_INTENTS
        )
        for node in renamed_addition
    )

    if addition_is_read_only and terminal_consumers:
        # The latest observation must be complete before an already-issued
        # delivery/calendar action can execute. This incoming-edge change
        # intentionally invalidates/restarts that downstream node on revision.
        for source in addition_leaves:
            for target in terminal_consumers:
                if source.id == target.id or _edge_exists(
                    edges,
                    source.id,
                    target.id,
                ):
                    continue
                edges.append(
                    TaskEdge(
                        source=source.id,
                        target=target.id,
                        kind="data",
                        source_output="result",
                        target_input=f"{source.id}.result",
                    )
                )
    elif not addition_is_read_only or context_dependent:
        # Later execution/mutation is ordered after the current executable
        # frontier. For response/evidence-only additions without a terminal
        # consumer, context-dependent work also waits for prior graph results.
        for source in previous_leaves:
            for target in addition_roots:
                if source.id == target.id or _edge_exists(
                    edges,
                    source.id,
                    target.id,
                ):
                    continue
                edges.append(
                    TaskEdge(
                        source=source.id,
                        target=target.id,
                        kind="data",
                        source_output="result",
                        target_input=f"{source.id}.result",
                    )
                )


def _rebuild_join_and_synthesis(previous_core, renamed_addition, edges, addition, previous):
    """Rebuild the authority-free join and synthesis over all executable nodes."""
    all_core = [*previous_core, *renamed_addition]
    join_id = "join-results"
    synthesis_id = "synthesize-results"
    join = TaskNode(
        id=join_id,
        kind="join",
        objective="Aggregate completed node results without acquiring new authority.",
        output_keys=["result"],
    )
    for source in all_core:
        if not _edge_exists(edges, source.id, join_id):
            edges.append(
                TaskEdge(
                    source=source.id,
                    target=join_id,
                    kind="data",
                    source_output="result",
                )
            )

    synthesis_model = next(
        (
            node.model
            for node in reversed(addition.nodes)
            if node.model is not None
        ),
        None,
    ) or next(
        (
            node.model
            for node in reversed(previous.nodes)
            if node.model is not None
        ),
        None,
    )
    if synthesis_model is None:
        raise ValueError("TaskGraph additive revision requires a synthesis model")

    synthesis = TaskNode(
        id=synthesis_id,
        kind="synthesis",
        objective=(
            "Synthesize the completed TaskGraph node results into one final "
            "user-facing answer. Use only predecessor results as reference "
            "data; do not perform actions or acquire new evidence."
        ),
        semantic_targets=["conversation"],
        semantic_action_intents=[],
        success_criteria=[
            SuccessCriterion(
                id="synthesis-complete",
                description=(
                    "Return a faithful final answer from the retained objective "
                    "and latest continuation without inventing unsupported facts "
                    "or actions."
                ),
            )
        ],
        model=synthesis_model,
        cacheable=False,
        estimated_cost=0.25,
    )
    edges.append(
        TaskEdge(
            source=join_id,
            target=synthesis_id,
            kind="data",
            source_output="result",
            target_input="graph_results",
        )
    )
    return all_core, join, synthesis, synthesis_id
