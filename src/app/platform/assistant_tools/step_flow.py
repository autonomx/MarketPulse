"""Deterministic multi-step assistant tool flow helpers."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .models import AssistantToolRequest, AssistantToolResult

AssistantToolFlowStatus = Literal["ready", "waiting_for_approval", "running", "complete", "blocked"]
AssistantToolStepStatus = Literal["pending", "approved", "running", "complete", "blocked"]


class AssistantToolStep(BaseModel):
    id: str
    request: AssistantToolRequest
    label: str = ""
    status: AssistantToolStepStatus = "pending"
    requires_approval: bool = False
    result: AssistantToolResult | None = None


class AssistantToolFlow(BaseModel):
    id: str
    user_request: str = ""
    status: AssistantToolFlowStatus = "ready"
    current_step_id: str | None = None
    steps: list[AssistantToolStep] = Field(default_factory=list)


def create_assistant_tool_flow(*, flow_id: str, user_request: str, steps: list[AssistantToolStep]) -> AssistantToolFlow:
    current = next((step for step in steps if step.status in {"pending", "approved", "running"}), None)
    status: AssistantToolFlowStatus = "complete" if current is None else "ready"
    if current and current.requires_approval and current.status == "pending":
        status = "waiting_for_approval"
    return AssistantToolFlow(
        id=flow_id,
        user_request=user_request,
        status=status,
        current_step_id=current.id if current else None,
        steps=steps,
    )


def approve_assistant_tool_step(flow: AssistantToolFlow, step_id: str) -> AssistantToolFlow:
    steps = [
        step.model_copy(update={"status": "approved"}) if step.id == step_id else step
        for step in flow.steps
    ]
    return create_assistant_tool_flow(flow_id=flow.id, user_request=flow.user_request, steps=steps)


def record_assistant_tool_step_result(flow: AssistantToolFlow, step_id: str, result: AssistantToolResult) -> AssistantToolFlow:
    steps = [step.model_copy(update={"status": "complete" if result.error is None else "blocked", "result": result}) if step.id == step_id else step for step in flow.steps]
    updated = create_assistant_tool_flow(flow_id=flow.id, user_request=flow.user_request, steps=steps)
    if any(step.status == "blocked" for step in updated.steps):
        return updated.model_copy(update={"status": "blocked"})
    return updated


def summarize_assistant_tool_flow(flow: AssistantToolFlow) -> str:
    completed = sum(1 for step in flow.steps if step.status == "complete")
    return f"{completed}/{len(flow.steps)} assistant tool steps complete; status={flow.status}."
