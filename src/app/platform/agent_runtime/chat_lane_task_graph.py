"""The TaskGraph lane of typed Chat: compiling, starting and steering task graphs (WP-8.2).
"""
from __future__ import annotations

from app.config.env import env_str
from typing import Any
from .active_objective import (
    ActiveObjective,
)
from .contracts import (
    AgentRunCommand,
    ModelRef,
    RequestModeSelection,
    WorkspaceSpec,
)
from .local_workspace import (
    LocalWorkspaceSelectionError,
    local_workspace_repository_root,
    validate_local_workspace_root,
)
from .router import OmnixRouteDecision
from .semantic_task import (
    SemanticTask,
    SemanticTaskCompilation,
)
from .turn_plan import (
    TurnPlan,
    derive_effective_objective,
)
from .task_graph import compile_task_graph
from .task_graph_optimizer import optimize_task_graph
from .task_graph_revision import (
    merge_task_graph_additive_revision,
    task_graph_preserves_execution_contract,
)
from .chat_lane_common import (
    GeneralizedChatResult,
    _TERMINAL_AGENT,
)
from .chat_lane_agent import (
    _agent_model_route,
    _agent_reasoning_effort,
    _agent_request_rejection,
    _agent_start_failure,
)


def default_agent_run_service(*args, **kwargs):
    # Resolved through chat_bridge at call time: the one place
    # callers and tests replace it.
    from . import chat_bridge

    return chat_bridge.default_agent_run_service(*args, **kwargs)


def default_task_graph_runtime(*args, **kwargs):
    # Resolved through chat_bridge at call time: the one place
    # callers and tests replace it.
    from . import chat_bridge

    return chat_bridge.default_task_graph_runtime(*args, **kwargs)


def _task_graph_result(
    session: Any,
    user_message: Any,
    decision: OmnixRouteDecision,
    *,
    provider_id: str | None,
    model_id: str | None,
    request_mode: RequestModeSelection,
    semantic_task: SemanticTask,
    semantic_compilation: SemanticTaskCompilation | None,
    routing_shadow: dict[str, Any] | None,
    turn_plan: TurnPlan,
    active_objective: ActiveObjective | None = None,
    semantic_reference_context: str = "",
) -> GeneralizedChatResult:
    content = str(user_message.content or "").strip()
    metadata = getattr(user_message, "metadata", {}) or {}
    workspace = _task_graph_workspace(metadata, decision, content)
    if isinstance(workspace, GeneralizedChatResult):
        return workspace

    resolved_provider, resolved_model = _agent_model_route(session, provider_id, model_id)
    if not resolved_provider or not resolved_model:
        return _agent_start_failure(
            decision,
            run_id=None,
            profile="task-graph",
            task=content,
            error=RuntimeError("TaskGraph Agent provider/model is not configured"),
        )

    def model() -> ModelRef:
        return ModelRef(
            provider_id=resolved_provider,
            model_id=resolved_model,
            reasoning_effort=_agent_reasoning_effort(resolved_provider),
        )

    try:
        runtime = default_task_graph_runtime()
        if (
            turn_plan.run_action == "steer_task_graph"
            and turn_plan.disposition == "replay_objective"
        ):
            snapshot = _replay_task_graph(runtime, turn_plan, content, semantic_reference_context)
            graph = snapshot.graph
        else:
            compiled = _compile_chat_task_graph(
                content,
                semantic_task,
                decision,
                turn_plan=turn_plan,
                active_objective=active_objective,
                model=model,
                workspace=workspace,
                reference_context=semantic_reference_context,
            )
            if isinstance(compiled, GeneralizedChatResult):
                return compiled
            graph = compiled
            if turn_plan.run_action == "steer_task_graph":
                steered = _steer_task_graph(
                    runtime,
                    graph,
                    content,
                    decision,
                    turn_plan=turn_plan,
                    model=model,
                    workspace=workspace,
                    reference_context=semantic_reference_context,
                )
                if isinstance(steered, GeneralizedChatResult):
                    return steered
                snapshot = steered
                graph = snapshot.graph
            else:
                _retire_superseded_run(runtime, turn_plan)
                snapshot = runtime.start(graph)
    except Exception as exc:
        return _agent_start_failure(
            decision,
            run_id=turn_plan.active_run_id,
            profile="task-graph",
            task=content,
            error=exc,
        )
    optimization = optimize_task_graph(graph)
    return GeneralizedChatResult(
        content=_task_graph_summary(snapshot, graph, turn_plan),
        metadata={
            "generation_status": "completed",
            "agent_mode": True,
            "task_graph_mode": True,
            "omnix_route": decision.model_dump(mode="json"),
            "request_mode": request_mode.model_dump(mode="json"),
            "semantic_task": semantic_task.model_dump(mode="json"),
            "semantic_compilation": (
                semantic_compilation.model_dump(mode="json")
                if semantic_compilation is not None
                else None
            ),
            "routing_decision": routing_shadow,
            "task_graph": graph.model_dump(mode="json"),
            "task_graph_optimization": optimization.model_dump(mode="json"),
            "task_graph_run": snapshot.model_dump(mode="json"),
        },
    )


def _task_graph_workspace(
    metadata: dict[str, Any],
    decision: OmnixRouteDecision,
    content: str,
) -> WorkspaceSpec | GeneralizedChatResult | None:
    """The attached Local folder, else the configured default repository."""
    selected_workspace = str(metadata.get("workspace_root") or "").strip()
    if selected_workspace:
        try:
            selected_workspace = validate_local_workspace_root(selected_workspace)
            repository_root = local_workspace_repository_root(selected_workspace)
        except LocalWorkspaceSelectionError as exc:
            return _agent_request_rejection(
                decision,
                profile="task-graph",
                task=content,
                reason="local_workspace_unavailable",
                message=f"I can't use the attached Local folder for this task graph: {exc}",
            )
        return WorkspaceSpec(
            root=selected_workspace,
            repository=repository_root,
            worktree=selected_workspace if repository_root else None,
            base_ref="HEAD",
        )
    repository = env_str("OMNIX_AGENT_DEFAULT_REPOSITORY", "").strip()
    if not repository:
        return None
    return WorkspaceSpec(
        root=repository,
        repository=repository,
        base_ref=env_str(
            "OMNIX_AGENT_DEFAULT_BASE_REF", "HEAD",
        ).strip() or "HEAD",
    )


def _active_task_graph(runtime: Any, turn_plan: TurnPlan) -> tuple[str, Any]:
    active_run_id = str(turn_plan.active_run_id or "").strip()
    if not active_run_id:
        raise RuntimeError("active TaskGraph run id is unavailable")
    previous = runtime.get_status(active_run_id)
    if previous is None:
        raise RuntimeError("active TaskGraph run is unavailable")
    return active_run_id, previous


def _replay_task_graph(
    runtime: Any,
    turn_plan: TurnPlan,
    content: str,
    reference_context: str,
) -> Any:
    """Run the active graph's objective again from the start."""
    active_run_id, previous = _active_task_graph(runtime, turn_plan)
    replay_graph = previous.graph.model_copy(
        update={
            "reference_context": str(
                reference_context or previous.graph.reference_context
            )[:12000]
        }
    )
    return runtime.revise(
        active_run_id,
        replay_graph,
        user_instruction=content,
        reuse_completed=False,
    )


def _compilation_rejection(
    compilation: Any,
    decision: OmnixRouteDecision,
    *,
    task: str,
    fallback_detail: str,
    fallback_reason: str,
    message: str,
) -> GeneralizedChatResult:
    detail = "; ".join(
        f"{row.code}: {row.detail}"
        for row in compilation.anomalies
    ) or fallback_detail
    return _agent_request_rejection(
        decision,
        profile="task-graph",
        task=task,
        reason=(
            compilation.anomalies[0].code
            if compilation.anomalies
            else fallback_reason
        ),
        message=message.format(detail=detail),
    )


def _compile_chat_task_graph(
    content: str,
    semantic_task: SemanticTask,
    decision: OmnixRouteDecision,
    *,
    turn_plan: TurnPlan,
    active_objective: ActiveObjective | None,
    model: Any,
    workspace: WorkspaceSpec | None,
    reference_context: str,
) -> Any:
    """Compile the graph for the whole objective when it continues the active one."""
    graph_content = content
    if (
        active_objective is not None
        and (
            turn_plan.run_action == "replace_agent_with_task_graph"
            or (
                turn_plan.run_action == "steer_task_graph"
                and turn_plan.relation == "continue"
            )
        )
    ):
        graph_content = derive_effective_objective(
            active_objective.effective_objective_text(),
            turn_plan,
        )
    compilation = compile_task_graph(
        graph_content,
        semantic_task,
        model=model(),
        workspace=workspace,
        reference_context=reference_context,
    )
    if not compilation.ok or compilation.graph is None:
        return _compilation_rejection(
            compilation,
            decision,
            task=graph_content,
            fallback_detail="task graph compilation failed",
            fallback_reason="task_graph_compilation_failed",
            message="I can't safely compile this multi-profile task: {detail}",
        )
    return compilation.graph


def _steer_task_graph(
    runtime: Any,
    graph: Any,
    content: str,
    decision: OmnixRouteDecision,
    *,
    turn_plan: TurnPlan,
    model: Any,
    workspace: WorkspaceSpec | None,
    reference_context: str,
) -> Any:
    """Revise the active graph, keeping prior compiled work the reparse dropped."""
    active_run_id, previous = _active_task_graph(runtime, turn_plan)
    if (
        turn_plan.relation == "continue"
        and not task_graph_preserves_execution_contract(
            previous.graph,
            graph,
        )
    ):
        # The second LLM parse of the reconstructed objective is advisory only.
        # If it silently drops prior compiled work, retain the durable graph and
        # compile the latest semantic delta separately, then compose the
        # revision deterministically.
        delta_compilation = compile_task_graph(
            content,
            turn_plan.semantic_task,
            model=model(),
            workspace=workspace,
            reference_context=reference_context,
        )
        if not delta_compilation.ok or delta_compilation.graph is None:
            return _compilation_rejection(
                delta_compilation,
                decision,
                task=content,
                fallback_detail="task graph delta compilation failed",
                fallback_reason="task_graph_delta_compilation_failed",
                message=(
                    "I can't safely preserve the active graph while "
                    "adding this continuation: {detail}"
                ),
            )
        graph = merge_task_graph_additive_revision(
            previous.graph,
            delta_compilation.graph,
            context_dependent=(
                turn_plan.semantic_task.request_completeness
                == "context_dependent"
            ),
        )
    # revise() normalizes graph identity/revision and invalidates only nodes
    # whose authority or incoming dependency contract changed.
    return runtime.revise(
        active_run_id,
        graph,
        user_instruction=content,
    )


def _retire_superseded_run(runtime: Any, turn_plan: TurnPlan) -> None:
    """Cancel the Agent run or task graph a new graph replaces."""
    old_run_id = str(turn_plan.active_run_id or "").strip()
    if not old_run_id:
        return
    if turn_plan.run_action == "replace_agent_with_task_graph":
        service = default_agent_run_service()
        old_run = service.get(old_run_id)
        if old_run is not None and old_run.status not in _TERMINAL_AGENT:
            service.command(
                AgentRunCommand(
                    run_id=old_run_id,
                    command_type="cancel",
                    payload={"reason": "superseded_by_task_graph"},
                )
            )
    elif turn_plan.run_action == "replace_task_graph_with_task_graph":
        runtime.cancel(
            old_run_id,
            reason="superseded_by_new_task_graph",
        )


def _task_graph_summary(snapshot: Any, graph: Any, turn_plan: TurnPlan) -> str:
    if snapshot.status == "completed":
        return (
            snapshot.result.strip()
            if isinstance(snapshot.result, str)
            and snapshot.result.strip()
            else "Task graph completed."
        )
    if snapshot.status == "waiting_for_approval":
        return "Task graph is waiting for approval."
    if snapshot.status == "failed":
        return f"Task graph failed: {snapshot.last_error or 'unknown error'}"
    verb = "revised" if turn_plan.run_action == "steer_task_graph" else "started"
    return (
        f"Task graph {verb} with {len(graph.nodes)} nodes "
        f"({sum(1 for row in snapshot.node_states if row.status == 'running')} running)."
    )
