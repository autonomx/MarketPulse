"""Parent/child agent authority narrowing and local child circuit breakers."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .evidence import classify_evidence, compile_task_authority, task_requires_workspace_mutation
from .profiles import get_agent_profile, profile_produces_diff
from .contracts import (
    AgentRunSnapshot,
    AgentRunSpec,
    EvidencePolicy,
    ModelRef,
    ResourceScope,
    RunLimits,
    SuccessCriterion,
    WorkspaceSpec,
)
from typing import Any


class ChildRunRequest(BaseModel):
    task: str
    objective: str = ""
    profile_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    reasoning_effort: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    external_capabilities: list[str] = Field(default_factory=list)
    resource_scopes: list[ResourceScope] | None = None
    success_criteria: list[str] = Field(default_factory=list)
    limits: RunLimits | None = None


def derive_child_spec(
    parent: AgentRunSnapshot,
    request: ChildRunRequest,
    *,
    workspace_override: WorkspaceSpec | None = None,
) -> AgentRunSpec:
    parent_spec = parent.spec
    effective_task = request.objective or request.task
    explicit_profile = request.profile_id is not None
    profile_id = request.profile_id or parent_spec.profile
    profile = get_agent_profile(profile_id)
    reviewer = profile_id == "coding-reviewer"

    if reviewer:
        # Independent review is deliberately local/read-only. Repository text
        # and the reviewer model cannot acquire external evidence authority.
        evidence_policy = EvidencePolicy(requirement="none", external_access="forbidden")
        compiled_external: list[str] = []
    else:
        evidence_decision = classify_evidence(effective_task, profile_id=profile_id)
        compiled = compile_task_authority(profile, effective_task, evidence_decision)
        evidence_policy = evidence_decision.policy
        compiled_external = list(compiled.required_external)

    local = list(dict.fromkeys(request.capabilities))
    external = list(dict.fromkeys([*compiled_external, *request.external_capabilities]))
    if not set(local).issubset(set(parent_spec.capabilities)):
        raise ValueError("child local capabilities exceed parent authority")
    if not set(local).issubset(set(profile.capabilities)):
        raise ValueError("child local capabilities exceed child profile ceiling")
    if not set(external).issubset(set(parent_spec.external_capabilities)):
        raise ValueError("child external capabilities exceed parent authority")
    if not set(external).issubset(set(profile.external_capabilities) | set(profile.optional_external_capabilities)):
        raise ValueError("child external capabilities exceed child profile ceiling")

    if reviewer:
        scopes: list[ResourceScope] = []
    else:
        scopes = list(request.resource_scopes if request.resource_scopes is not None else parent_spec.resource_scopes)
        _validate_scopes(parent_spec.resource_scopes, scopes)
    limits = request.limits or _default_child_limits(parent_spec.limits)
    _validate_limits(parent_spec.limits, limits)

    provider_id = request.provider_id or parent_spec.model.provider_id
    model_id = request.model_id or parent_spec.model.model_id
    effort = request.reasoning_effort if request.reasoning_effort is not None else parent_spec.model.reasoning_effort

    workspace = workspace_override or _child_workspace(parent_spec.workspace, local)
    # Preserve the Phase 1-19 contract for inherited child profiles: an older
    # parent RunSpec may legitimately have no WorkspaceSpec even when its
    # profile is workspace-oriented, and deriving a read-only child must not
    # retroactively invalidate that durable parent. Profile switching is a new
    # operation, however, so an explicitly requested profile must satisfy its
    # workspace contract at derivation time.
    if explicit_profile and profile.requires_workspace and workspace is None:
        raise ValueError("child profile requires an issued workspace")
    if explicit_profile and not profile.requires_workspace and workspace is not None:
        raise ValueError("child profile does not permit workspace authority")

    return AgentRunSpec(
        session_id=parent_spec.session_id,
        parent_run_id=parent.run_id,
        task=request.task,
        objective=request.objective or request.task,
        success_criteria=[
            SuccessCriterion(id=f"child-criterion-{index + 1}", description=value)
            for index, value in enumerate(request.success_criteria)
        ],
        runtime=parent_spec.runtime,
        profile=profile_id,
        model=ModelRef(
            provider_id=provider_id,
            model_id=model_id,
            reasoning_effort=effort,
            parameters=dict(parent_spec.model.parameters),
        ),
        capabilities=local,
        resource_scopes=scopes,
        external_capabilities=external,
        request_mode=parent_spec.request_mode,
        evidence_policy=evidence_policy,
        workspace=workspace,
        execution=parent_spec.execution,
        limits=limits,
        approval_policy="disabled" if reviewer else parent_spec.approval_policy,
        quality_policy="off" if reviewer else parent_spec.quality_policy,
        quality_reserve_fraction=0.0 if reviewer else parent_spec.quality_reserve_fraction,
        context_sources=[] if reviewer else list(parent_spec.context_sources),
        artifact_policy=parent_spec.artifact_policy,
        expected_artifacts=(
            ["diff"]
            if profile_produces_diff(profile_id) and task_requires_workspace_mutation(effective_task)
            else []
        ),
        persistence_policy=parent_spec.persistence_policy,
    )


def reserve_child_budget(
    parent: AgentRunSnapshot,
    existing_children: list[AgentRunSnapshot],
    child: AgentRunSpec,
    *,
    parent_usage: dict[str, Any] | None = None,
) -> None:
    """Legacy aggregate reservation check.

    New durable service paths use ``PostgresResourceGrantRepository`` so terminal
    child spend can be reclaimed accurately. Keep this helper for compatibility
    callers, but do not add wall times: wall time is a deadline-like bound, not
    a consumable quantity.
    """

    limits = parent.spec.limits
    usage = parent_usage or {}
    active = [
        item.spec.limits
        for item in existing_children
        if item.status not in {"completed", "failed", "cancelled"}
    ]
    _bounded_sum(
        "max_steps",
        limits.max_steps,
        [int(usage.get("steps", 0)), *[item.max_steps for item in active], child.limits.max_steps],
    )
    _bounded_sum(
        "max_tool_calls",
        limits.max_tool_calls,
        [int(usage.get("tool_calls", 0)), *[item.max_tool_calls for item in active], child.limits.max_tool_calls],
    )
    if child.limits.max_wall_time_seconds > limits.max_wall_time_seconds:
        raise ValueError("child max_wall_time_seconds exceeds parent")
    if limits.max_tokens is not None:
        _bounded_sum(
            "max_tokens",
            limits.max_tokens,
            [
                int(usage.get("output_tokens", 0)),
                *[item.max_tokens or 0 for item in active],
                child.limits.max_tokens or 0,
            ],
        )
    if limits.max_cost is not None:
        _bounded_sum(
            "max_cost",
            limits.max_cost,
            [
                float(usage.get("cost", 0.0)),
                *[item.max_cost or 0 for item in active],
                child.limits.max_cost or 0,
            ],
        )


def default_reviewer_limits(
    parent: RunLimits,
    reserve_fraction: float | None = None,
    *,
    complexity_score: int = 0,
    available: dict[str, int | float | None] | None = None,
) -> RunLimits:
    """Return a reviewer *circuit breaker*, not a pre-spent quality slice.

    ``reserve_fraction`` remains accepted for older callers but no longer drives
    reviewer sizing. Reviewer capacity is completion-oriented and is instead
    clamped by the parent's currently available global grant at launch time.
    """

    del reserve_fraction
    score = max(0, min(int(complexity_score), 16))
    target_steps = min(parent.max_steps, max(60, min(120, 72 + score * 3)))
    target_tools = min(parent.max_tool_calls, max(150, min(260, 170 + score * 6)))
    target_wall = min(parent.max_wall_time_seconds, 1200)
    target_tokens = (
        max(1, min(parent.max_tokens, int(parent.max_tokens * 0.60)))
        if parent.max_tokens is not None
        else None
    )
    target_cost = parent.max_cost * 0.60 if parent.max_cost is not None else None

    if available is not None:
        target_steps = max(1, min(target_steps, int(available.get("max_steps") or 0)))
        target_tools = max(1, min(target_tools, int(available.get("max_tool_calls") or 0)))
        target_wall = max(1, min(target_wall, int(available.get("max_wall_time_seconds") or 0)))
        if target_tokens is not None and available.get("max_tokens") is not None:
            target_tokens = max(1, min(target_tokens, int(available["max_tokens"] or 0)))
        if target_cost is not None and available.get("max_cost") is not None:
            target_cost = max(0.0, min(target_cost, float(available["max_cost"] or 0.0)))

    return RunLimits(
        max_steps=target_steps,
        max_wall_time_seconds=target_wall,
        max_tokens=target_tokens,
        max_cost=target_cost,
        max_tool_calls=target_tools,
    )


def _default_child_limits(parent: RunLimits) -> RunLimits:
    return RunLimits(
        max_steps=max(1, min(50, parent.max_steps // 4 or 1)),
        max_wall_time_seconds=max(1, min(900, parent.max_wall_time_seconds // 4 or 1)),
        max_tokens=max(1, parent.max_tokens // 4) if parent.max_tokens is not None else None,
        max_cost=parent.max_cost / 4 if parent.max_cost is not None else None,
        max_tool_calls=max(1, min(100, parent.max_tool_calls // 4 or 1)),
    )


def _validate_limits(parent: RunLimits, child: RunLimits) -> None:
    if child.max_steps > parent.max_steps:
        raise ValueError("child max_steps exceeds parent")
    if child.max_tool_calls > parent.max_tool_calls:
        raise ValueError("child max_tool_calls exceeds parent")
    if child.max_wall_time_seconds > parent.max_wall_time_seconds:
        raise ValueError("child wall time exceeds parent")
    if parent.max_tokens is not None and (child.max_tokens is None or child.max_tokens > parent.max_tokens):
        raise ValueError("child max_tokens exceeds parent")
    if parent.max_cost is not None and (child.max_cost is None or child.max_cost > parent.max_cost):
        raise ValueError("child max_cost exceeds parent")


def _validate_scopes(parent: list[ResourceScope], child: list[ResourceScope]) -> None:
    if parent and not child:
        raise ValueError("child resource scopes cannot remove parent restrictions")
    if not child:
        return
    parent_keys = {
        (row.capability, row.resource_type, row.resource_id, tuple(sorted(row.constraints.items())))
        for row in parent
    }
    for row in child:
        key = (row.capability, row.resource_type, row.resource_id, tuple(sorted(row.constraints.items())))
        if key not in parent_keys:
            raise ValueError("child resource scope is not an exact/narrow parent scope")


def _child_workspace(parent: WorkspaceSpec | None, capabilities: list[str]) -> WorkspaceSpec | None:
    if parent is None:
        return None
    mutating = any(
        item in {"workspace.edit", "workspace.write", "workspace.command", "workspace.test"}
        for item in capabilities
    )
    if not mutating:
        return parent
    if not parent.repository:
        raise ValueError("mutating child requires a repository-backed parent workspace")
    return WorkspaceSpec(
        root=parent.repository,
        repository=parent.repository,
        base_ref=parent.base_ref,
        isolation_policy=parent.isolation_policy,
        allowed_paths=list(parent.allowed_paths),
        forbidden_paths=list(parent.forbidden_paths),
    )


def _bounded_sum(name: str, maximum, values) -> None:
    if sum(values) > maximum:
        raise ValueError(f"aggregate child {name} budget exceeds parent")
