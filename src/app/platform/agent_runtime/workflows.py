"""Deterministic workflow-domain contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

WorkflowStepKind = Literal["capability", "condition", "approval"]
WorkflowRunStatus = Literal["queued", "running", "paused", "waiting_for_approval", "completed", "failed", "cancelled"]
WORKFLOW_END = "__end__"


class WorkflowStepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: WorkflowStepKind = "capability"
    capability_id: str | None = None
    input_template: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = None
    retry_limit: int = Field(default=0, ge=0, le=20)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    requires_approval: bool = False
    next_step_id: str | None = None
    on_true_step_id: str | None = None
    on_false_step_id: str | None = None

    @model_validator(mode="after")
    def validate_step_shape(self) -> "WorkflowStepDefinition":
        if self.kind == "capability" and not self.capability_id:
            raise ValueError("capability workflow step requires capability_id")
        if self.kind == "condition" and not self.condition:
            raise ValueError("condition workflow step requires condition")
        if self.kind != "condition" and (self.on_true_step_id or self.on_false_step_id):
            raise ValueError("only condition steps may define on_true_step_id/on_false_step_id")
        return self


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: int = Field(default=1, ge=1)
    name: str
    description: str = ""
    steps: list[WorkflowStepDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinition":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow step ids must be unique")
        known = set(ids)
        for step in self.steps:
            for target in (step.next_step_id, step.on_true_step_id, step.on_false_step_id):
                if target and target != WORKFLOW_END and target not in known:
                    raise ValueError(f"workflow step {step.id} targets unknown step {target}")
        return self


class WorkflowRunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    workflow_id: str
    workflow_version: int
    status: WorkflowRunStatus = "queued"
    current_step_id: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    revision: int = 1
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class WorkflowScheduleSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule_id: str
    workflow_id: str
    workflow_version: int
    input_payload: dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int | None = None
    next_run_at: datetime | None = None
    enabled: bool = True
    last_enqueued_at: datetime | None = None


class WorkflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    sequence: int | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
