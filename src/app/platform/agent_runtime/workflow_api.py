"""HTTP surface for deterministic reusable workflows."""
from __future__ import annotations

from app.capabilities.approvals import require_approver
from app.security import audit

from typing import Literal, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .workflow_runtime import WorkflowRuntimeError, default_workflow_runtime
from .workflows import WorkflowDefinition

router = APIRouter(tags=["workflow-runtime"])


class WorkflowStartRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class WorkflowCommandRequest(BaseModel):
    command: Literal["pause", "resume", "cancel", "approve", "reject"]
    step_id: str | None = None


@router.get("/api/workflows", response_model=list[WorkflowDefinition])
def list_workflows() -> list[WorkflowDefinition]:
    return default_workflow_runtime().list_definitions()


@router.post("/api/workflows", response_model=WorkflowDefinition)
def register_workflow(definition: WorkflowDefinition) -> WorkflowDefinition:
    return default_workflow_runtime().register(definition)


@router.post("/api/workflows/{workflow_id}/runs")
def start_workflow(workflow_id: str, request: WorkflowStartRequest) -> dict[str, Any]:
    try:
        run_id = default_workflow_runtime().start(workflow_id, request.input)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workflow_not_found") from exc
    return {"run_id": run_id, "status": default_workflow_runtime().get_status(run_id)}


@router.get("/api/workflow-runs/{run_id}")
def get_workflow_run(run_id: str) -> dict[str, Any]:
    state = default_workflow_runtime().get_status(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="workflow_run_not_found")
    return state


@router.post("/api/workflow-runs/{run_id}/commands")
def command_workflow_run(run_id: str, request: WorkflowCommandRequest) -> dict[str, Any]:
    runtime = default_workflow_runtime()
    try:
        if request.command == "pause":
            runtime.pause(run_id)
        elif request.command == "resume":
            runtime.resume(run_id)
        elif request.command == "cancel":
            runtime.cancel(run_id)
        elif request.command in {"approve", "reject"}:
            approver = require_approver("agent:approve")
            if not request.step_id:
                raise HTTPException(status_code=422, detail="step_id_required")
            if request.command == "approve":
                runtime.approve(run_id, request.step_id, approved_by=approver)
            else:
                runtime.reject(run_id, request.step_id)
            audit.record("approval.decide", target_type="workflow_step", target_id=f"{run_id}/{request.step_id}",
                         details={"decision": request.command})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workflow_run_not_found") from exc
    except WorkflowRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = runtime.get_status(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="workflow_run_not_found")
    return state
