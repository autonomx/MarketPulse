"""HTTP control surface for durable multi-profile TaskGraph runs."""
from __future__ import annotations

from app.capabilities.approvals import require_approver
from app.security import audit

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .task_graph import TaskGraphEvent, TaskGraphRunSnapshot
from .task_graph_optimizer import TaskGraphOptimizationPlan, optimize_task_graph
from .task_graph_runtime import (
    TaskGraphRuntimeError,
    default_task_graph_runtime,
)

router = APIRouter(prefix="/api/task-graph-runs", tags=["task-graph-runtime"])


class TaskGraphCommandRequest(BaseModel):
    command: Literal["advance", "recover", "cancel", "approve", "reject"]
    node_id: str | None = None
    approval_id: str | None = None


@router.get("/{run_id}", response_model=TaskGraphRunSnapshot)
def get_task_graph_run(run_id: str) -> TaskGraphRunSnapshot:
    snapshot = default_task_graph_runtime().get_status(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="task_graph_run_not_found")
    return snapshot


@router.get(
    "/{run_id}/optimization",
    response_model=TaskGraphOptimizationPlan,
)
def get_task_graph_optimization(run_id: str) -> TaskGraphOptimizationPlan:
    snapshot = default_task_graph_runtime().get_status(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="task_graph_run_not_found")
    return optimize_task_graph(snapshot.graph)


@router.get("/{run_id}/events", response_model=list[TaskGraphEvent])
def list_task_graph_events(
    run_id: str,
    after_sequence: int = 0,
) -> list[TaskGraphEvent]:
    runtime = default_task_graph_runtime()
    if runtime.get_status(run_id) is None:
        raise HTTPException(status_code=404, detail="task_graph_run_not_found")
    return runtime.stream_events(
        run_id,
        after_sequence=max(0, after_sequence),
    )


@router.post("/{run_id}/commands", response_model=TaskGraphRunSnapshot)
def command_task_graph_run(
    run_id: str,
    request: TaskGraphCommandRequest,
) -> TaskGraphRunSnapshot:
    runtime = default_task_graph_runtime()
    try:
        if request.command == "advance":
            return runtime.advance(run_id)
        if request.command == "recover":
            return runtime.recover(run_id)
        if request.command == "cancel":
            return runtime.cancel(run_id)
        if request.command in {"approve", "reject"}:
            approver = require_approver("agent:approve")
            if not request.node_id:
                raise HTTPException(status_code=422, detail="node_id_required")
            audit.record("approval.decide", target_type="task_graph_node", target_id=f"{run_id}/{request.node_id}",
                         details={"decision": request.command, "approval_id": request.approval_id})
            if request.command == "approve":
                return runtime.approve(
                    run_id,
                    request.node_id,
                    approved_by=approver,
                    approval_id=request.approval_id,
                )
            return runtime.reject(
                run_id,
                request.node_id,
                approval_id=request.approval_id,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task_graph_run_not_found") from exc
    except TaskGraphRuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail="unsupported_task_graph_command")


@router.get(
    "/{run_id}/events/stream",
    response_model=None,
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Task graph run events as Server-Sent Events.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
def stream_task_graph_events(
    run_id: str,
    after_sequence: int = 0,
) -> StreamingResponse:
    runtime = default_task_graph_runtime()
    if runtime.get_status(run_id) is None:
        raise HTTPException(status_code=404, detail="task_graph_run_not_found")

    from app.events.run_streams import run_event_wakeups, wakeup_key
    from app.runtime.tenant_context import current_tenant

    key = wakeup_key(current_tenant().workspace_id, "task_graph_run", run_id)

    async def generate():
        sequence = max(0, after_sequence)
        # Woken by the outbox relay when the run has new events (WP-5.3);
        # without a notification the stream reads every few seconds.
        with run_event_wakeups().subscribe(key) as wakeups:
            while True:
                rows = await asyncio.to_thread(
                    runtime.stream_events,
                    run_id,
                    after_sequence=sequence,
                )
                for event in rows:
                    sequence = max(sequence, int(event.sequence or 0))
                    yield (
                        f"id: {sequence}\n"
                        f"event: {event.event_type}\n"
                        f"data: {json.dumps(event.model_dump(mode='json'), sort_keys=True)}\n\n"
                    )

                snapshot = await asyncio.to_thread(runtime.get_status, run_id)
                if snapshot is None or snapshot.status in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    return
                if not rows and not await wakeups.wait():
                    yield ": heartbeat\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
