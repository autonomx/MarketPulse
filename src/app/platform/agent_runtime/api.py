"""HTTP API for durable generalized agent runs."""
from __future__ import annotations

from app.capabilities.approvals import require_approver
from app.security import audit
from app.security.rate_limit import rate_limited
from app.security.permissions import ensure_permission

import asyncio
import json
import logging
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .contracts import (
    AgentApproval,
    AgentArtifact,
    AgentEvent,
    AgentRunCommand,
    AgentRunSnapshot,
    AgentRunSpec,
    EvidenceReceipt,
    EvidenceSet,
    ModelRef,
    QualityPolicy,
    ResourceScope,
    ReviewResult,
    SelfReviewResult,
    RunLimits,
    SuccessCriterion,
    TaskRevision,
    ValidationResult,
    WorkspaceSpec,
)
from .evidence import EvidenceCompilationError, classify_evidence, compile_task_authority, task_requires_workspace_mutation
from .local_workspace import (
    LocalWorkspaceSelectionError,
    local_request_host_allowed,
    local_request_origin_allowed,
    pick_local_workspace,
)
from .profiles import get_agent_profile, profile_produces_diff, resolve_profile_capabilities
from .request_policy import allowed_workspace_root, validate_request_policy
from .subagents import ChildRunRequest
from .service import AgentRunService, default_agent_run_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-runs", tags=["agent-runtime"])


class StartAgentRunRequest(BaseModel):
    task: str
    objective: str = ""
    provider_id: str
    model_id: str
    reasoning_effort: str | None = None
    profile: str = "coding"
    repository: str | None = None
    workspace_root: str | None = None
    base_ref: str = "main"
    # None takes the profile's default (the sandbox for profiles that change workspaces).
    isolation_policy: str | None = None
    capabilities: list[str] | None = None
    external_capabilities: list[str] | None = None
    resource_scopes: list[ResourceScope] = Field(default_factory=list)
    approval_policy: Literal[
        "allow_automatic",
        "ask_sensitive",
        "always_ask",
        "disabled",
    ] = "ask_sensitive"
    quality_policy: QualityPolicy = "strict"
    quality_reserve_fraction: float = Field(default=0.25, ge=0.0, le=0.5)
    limits: RunLimits | None = None
    allowed_paths: list[str] = Field(default_factory=lambda: ["**"])
    forbidden_paths: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_profile_policy(self) -> StartAgentRunRequest:
        profile = get_agent_profile(self.profile)
        if self.isolation_policy is None:
            self.isolation_policy = profile.isolation_policy
        validate_request_policy(
            profile, approval_policy=self.approval_policy,
            isolation_policy=self.isolation_policy, allowed_paths=self.allowed_paths,
        )
        self.allowed_paths = [value.replace("\\", "/") for value in self.allowed_paths]
        return self


class AgentCommandRequest(BaseModel):
    command_type: Literal["steer", "pause", "resume", "cancel", "approve", "reject"]
    payload: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str | None = None


class LocalWorkspacePickResponse(BaseModel):
    path: str | None = None
    name: str | None = None
    cancelled: bool = False


def _service(request: Request | None = None) -> AgentRunService:
    if request is not None:
        services = getattr(request.app.state, "runtime_services", None)
        service = getattr(services, "agent_runs", None) if services is not None else None
        if service is not None:
            return service
    return default_agent_run_service()


@router.post("/workspace-picker", response_model=LocalWorkspacePickResponse)
def pick_agent_workspace(request: Request) -> LocalWorkspacePickResponse:
    host = request.client.host if request.client is not None else None
    origin = request.headers.get("origin")
    if not local_request_host_allowed(host) or not local_request_origin_allowed(origin):
        raise HTTPException(status_code=403, detail="local_workspace_picker_requires_loopback")
    try:
        selected = pick_local_workspace()
    except LocalWorkspaceSelectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if selected is None:
        return LocalWorkspacePickResponse(cancelled=True)
    from pathlib import Path

    return LocalWorkspacePickResponse(
        path=selected,
        name=Path(selected).name or selected,
        cancelled=False,
    )


@router.post("", response_model=AgentRunSnapshot, status_code=202)
def start_agent_run(request: StartAgentRunRequest, http_request: Request) -> AgentRunSnapshot:
    try:
        profile = get_agent_profile(request.profile)
        # Validate both paths: an allowed workspace cannot hide an arbitrary
        # repository path later consumed by the worktree manager.
        repository = allowed_workspace_root(request.repository) if request.repository else None
        workspace_root = allowed_workspace_root(request.workspace_root) if request.workspace_root else None
        effective_task = request.objective or request.task
        evidence_decision = classify_evidence(effective_task, profile_id=request.profile)
        compiled = compile_task_authority(profile, effective_task, evidence_decision)
        requested_local = request.capabilities if request.capabilities is not None else list(compiled.required_local)
        requested_external = (
            request.external_capabilities
            if request.external_capabilities is not None
            else list(compiled.required_external)
        )
        issued_capabilities, issued_external = resolve_profile_capabilities(
            profile,
            requested=requested_local,
            requested_external=requested_external,
        )
    except (ValueError, EvidenceCompilationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    root = workspace_root or repository
    if profile.requires_workspace and not root:
        raise HTTPException(status_code=422, detail="repository or workspace_root is required for this profile")
    limit_kwargs: dict[str, Any] = {"limits": request.limits} if request.limits is not None else {}
    spec = AgentRunSpec(
        task=request.task,
        objective=request.objective,
        profile=request.profile,
        model=ModelRef(
            provider_id=request.provider_id,
            model_id=request.model_id,
            reasoning_effort=request.reasoning_effort,
        ),
        capabilities=issued_capabilities,
        external_capabilities=issued_external,
        context_sources=list(profile.context_sources),
        evidence_policy=evidence_decision.policy,
        resource_scopes=request.resource_scopes,
        approval_policy=request.approval_policy,
        quality_policy=request.quality_policy,
        quality_reserve_fraction=request.quality_reserve_fraction,
        **limit_kwargs,
        workspace=(
            WorkspaceSpec(
                root=str(root),
                repository=repository,
                base_ref=request.base_ref,
                isolation_policy=request.isolation_policy or get_agent_profile(request.profile).isolation_policy,
                allowed_paths=request.allowed_paths,
                forbidden_paths=request.forbidden_paths,
            )
            if root
            else None
        ),
        success_criteria=[
            SuccessCriterion(id=f"criterion-{index + 1}", description=value)
            for index, value in enumerate(request.success_criteria)
        ],
        expected_artifacts=(
            ["diff"]
            if profile_produces_diff(request.profile) and task_requires_workspace_mutation(effective_task)
            else []
        ),
    )
    try:
        services = getattr(http_request.app.state, "runtime_services", None)
        job_store = getattr(services, "jobs", None) if services is not None else None
        if job_store is None:
            raise RuntimeError("durable agent job service is not composed")
        started = _service(http_request).submit_start(spec, job_store=job_store)
        audit.record("agent.run.start", target_type="agent_run", target_id=started.run_id,
                     details={"profile": spec.profile, "provider_id": spec.model.provider_id})
        return started
    except Exception as exc:
        # The failure text can carry paths and provider errors: log it, return the code (WP-10.5).
        logger.exception("agent_start_failed")
        raise HTTPException(status_code=503, detail="agent_start_failed") from exc


@router.post("/{run_id}/children", response_model=AgentRunSnapshot, status_code=202)
def start_child_agent_run(
    run_id: str,
    request: ChildRunRequest,
    http_request: Request,
) -> AgentRunSnapshot:
    try:
        services = getattr(http_request.app.state, "runtime_services", None)
        job_store = getattr(services, "jobs", None) if services is not None else None
        if job_store is None:
            raise RuntimeError("durable agent job service is not composed")
        return _service(http_request).submit_child_start(
            run_id,
            request,
            job_store=job_store,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent_run_not_found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{run_id}", response_model=AgentRunSnapshot)
def get_agent_run(run_id: str) -> AgentRunSnapshot:
    snapshot = _service().get(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return snapshot


@router.post("/{run_id}/commands", response_model=AgentRunSnapshot, dependencies=[Depends(rate_limited("approvals"))])
def command_agent_run(
    run_id: str,
    request: AgentCommandRequest,
    http_request: Request,
) -> AgentRunSnapshot:
    # The command type decides the permission (WP-4.3). Approvals record the
    # approving principal; a client cannot supply it (WP-4.5).
    payload = {key: value for key, value in request.payload.items() if key != "issued_by"}
    if request.command_type in {"approve", "reject"}:
        payload["issued_by"] = require_approver("agent:approve")
    elif request.command_type == "steer":
        ensure_permission("agent:steer")
    try:
        snapshot = _service(http_request).command(
            AgentRunCommand(
                run_id=run_id,
                command_type=request.command_type,
                payload=payload,
                **(cast(dict[str, Any], {"idempotency_key": request.idempotency_key}) if request.idempotency_key else {}),
            )
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent_run_not_found") from exc
    if request.command_type in {"approve", "reject"}:
        audit.record("approval.decide", target_type="agent_approval", target_id=str(payload.get("approval_id") or ""),
                     details={"run_id": run_id, "decision": request.command_type})
    elif request.command_type == "cancel":
        audit.record("agent.run.stop", target_type="agent_run", target_id=run_id)
    return snapshot


@router.get("/{run_id}/events", response_model=list[AgentEvent])
def list_agent_events(run_id: str, after_sequence: int = 0) -> list[AgentEvent]:
    return _service().events(run_id, after_sequence=max(0, after_sequence))


@router.get("/{run_id}/approvals", response_model=list[AgentApproval])
def list_agent_approvals(run_id: str, state: str | None = None) -> list[AgentApproval]:
    if _service().get(run_id) is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return _service().approvals(run_id, state=state)


@router.get("/{run_id}/artifacts", response_model=list[AgentArtifact])
def list_agent_artifacts(run_id: str) -> list[AgentArtifact]:
    return _service().artifacts(run_id)


@router.get("/{run_id}/task-revisions", response_model=list[TaskRevision])
def list_agent_task_revisions(run_id: str) -> list[TaskRevision]:
    if _service().get(run_id) is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return _service().task_revisions(run_id)


@router.get("/{run_id}/quality", response_model=dict[str, object])
def get_agent_quality_state(run_id: str) -> dict[str, object]:
    try:
        return _service().quality_state(run_id) or {}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent_run_not_found") from exc


@router.get("/{run_id}/quality/validations", response_model=list[ValidationResult])
def list_agent_validation_results(run_id: str) -> list[ValidationResult]:
    if _service().get(run_id) is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return _service().validation_results(run_id)


@router.get("/{run_id}/quality/self-reviews", response_model=list[SelfReviewResult])
def list_agent_self_review_results(run_id: str) -> list[SelfReviewResult]:
    if _service().get(run_id) is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return _service().self_review_results(run_id)


@router.get("/{run_id}/quality/reviews", response_model=list[ReviewResult])
def list_agent_review_results(run_id: str) -> list[ReviewResult]:
    if _service().get(run_id) is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return _service().review_results(run_id)


@router.get("/{run_id}/evidence/receipts", response_model=list[EvidenceReceipt])
def list_agent_evidence_receipts(run_id: str) -> list[EvidenceReceipt]:
    if _service().get(run_id) is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    return _service().evidence_receipts(run_id)


@router.get("/{run_id}/evidence", response_model=EvidenceSet)
def get_agent_evidence_set(run_id: str) -> EvidenceSet:
    try:
        return _service().evidence_set(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent_run_not_found") from exc


@router.get(
    "/{run_id}/events/stream",
    response_model=None,
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Agent run events as Server-Sent Events.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
def stream_agent_events(run_id: str, after_sequence: int = 0) -> StreamingResponse:
    if _service().get(run_id) is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")

    from app.events.run_streams import run_event_wakeups, wakeup_key
    from app.runtime.tenant_context import current_tenant

    key = wakeup_key(current_tenant().workspace_id, "agent_run", run_id)

    async def generate():
        sequence = max(0, after_sequence)
        # Woken by the outbox relay when the run has new events (WP-5.3);
        # without a notification the stream reads every few seconds.
        with run_event_wakeups().subscribe(key) as wakeups:
            while True:
                rows = await asyncio.to_thread(_service().events, run_id, after_sequence=sequence)
                if rows:
                    for event in rows:
                        sequence = max(sequence, int(event.sequence or 0))
                        yield f"id: {sequence}\nevent: {event.event_type}\ndata: {json.dumps(event.model_dump(mode='json'), sort_keys=True)}\n\n"
                    snapshot = await asyncio.to_thread(_service().get, run_id)
                    if snapshot and snapshot.status in {"completed", "failed", "cancelled"}:
                        return
                elif not await wakeups.wait():
                    yield ": heartbeat\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
