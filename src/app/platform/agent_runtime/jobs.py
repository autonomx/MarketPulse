"""Durable agent runtime job contracts and feature handlers."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.jobs.handlers import JobExecutionContext, JobHandlerSpec
from app.jobs.models import CompleteJobRequest, CreateJobRequest, JobRecord, ResourceClass


class AgentRunJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    reference_session_id: str | None = None
    reference_message_id: str | None = None
    reference_message_ids: list[str] | None = None
    task_graph_run_id: str | None = None
    task_graph_node_ids: list[str] | None = None


class AgentPromoteJobInput(AgentRunJobInput):
    trigger_id: str = Field(min_length=1)


def create_agent_workspace_prepare_request(
    run_id: str,
    *,
    reference_session_id: str | None = None,
    reference_message_id: str | None = None,
    reference_message_ids: list[str] | None = None,
    task_graph_run_id: str | None = None,
    task_graph_node_ids: list[str] | None = None,
) -> CreateJobRequest:
    return CreateJobRequest(
        module="agent-runtime",
        type="agent.workspace.prepare",
        resource_class=ResourceClass.CPU,
        priority=20,
        input_payload=AgentRunJobInput(
            run_id=run_id,
            reference_session_id=reference_session_id,
            reference_message_id=reference_message_id,
            reference_message_ids=reference_message_ids,
            task_graph_run_id=task_graph_run_id,
            task_graph_node_ids=task_graph_node_ids,
        ).model_dump(mode="json", exclude_none=True),
    )


def create_agent_run_start_request(
    run_id: str,
    *,
    reference_session_id: str | None = None,
    reference_message_id: str | None = None,
    reference_message_ids: list[str] | None = None,
    task_graph_run_id: str | None = None,
    task_graph_node_ids: list[str] | None = None,
) -> CreateJobRequest:
    return CreateJobRequest(
        module="agent-runtime",
        type="agent.run.start",
        resource_class=ResourceClass.CPU,
        priority=20,
        input_payload=AgentRunJobInput(
            run_id=run_id,
            reference_session_id=reference_session_id,
            reference_message_id=reference_message_id,
            reference_message_ids=reference_message_ids,
            task_graph_run_id=task_graph_run_id,
            task_graph_node_ids=task_graph_node_ids,
        ).model_dump(mode="json", exclude_none=True),
    )


def create_agent_promote_request(run_id: str, *, trigger_id: str) -> CreateJobRequest:
    return CreateJobRequest(
        module="agent-runtime",
        type="agent.promote",
        resource_class=ResourceClass.CPU,
        priority=20,
        input_payload=AgentPromoteJobInput(
            run_id=run_id,
            trigger_id=trigger_id,
        ).model_dump(mode="json", exclude_none=True),
    )


def enqueue_agent_job(job_store: Any, request: CreateJobRequest, *, idempotency_key: str) -> Any:
    create_once = getattr(job_store, "create_job_once", None)
    if callable(create_once):
        return create_once(request, idempotency_key=idempotency_key)
    return job_store.create_job(request)


def _agent_service(context: JobExecutionContext):
    services = context.services
    service = getattr(services, "agent_runs", None) if services is not None else None
    if service is None:
        raise RuntimeError("agent runtime service is not composed for the job worker")
    return service


def _complete_agent_job(
    context: JobExecutionContext,
    job: JobRecord,
    *,
    stage: str,
    run_id: str,
    run_status: str | None = None,
) -> JobRecord:
    output = {"kind": "agent_run", "stage": stage, "run_id": run_id}
    if run_status is not None:
        output["run_status"] = run_status
    completed = context.job_store.complete_job(
        job.id,
        CompleteJobRequest(output_refs=[output]),
    )
    return completed or job


def _prepare_workspace(context: JobExecutionContext, job: JobRecord) -> JobRecord:
    payload = AgentRunJobInput.model_validate(job.input_payload or {})
    snapshot = _agent_service(context).prepare_workspace_job(
        payload.run_id,
        job_store=context.services.jobs,
        reference_session_id=payload.reference_session_id,
        reference_message_id=payload.reference_message_id,
        reference_message_ids=payload.reference_message_ids,
        task_graph_run_id=payload.task_graph_run_id,
        task_graph_node_ids=payload.task_graph_node_ids,
    )
    return _complete_agent_job(
        context,
        job,
        stage="workspace_prepared",
        run_id=payload.run_id,
        run_status=snapshot.status,
    )


def _start_run(context: JobExecutionContext, job: JobRecord) -> JobRecord:
    payload = AgentRunJobInput.model_validate(job.input_payload or {})
    reference_context, reference_images = _chat_reference_context(context, payload)
    if payload.task_graph_run_id and payload.task_graph_node_ids:
        reference_context = _task_graph_reference_context(
            _agent_service(context),
            payload.task_graph_run_id,
            payload.task_graph_node_ids[0],
        )
    snapshot = _agent_service(context).start_prepared_run(
        payload.run_id,
        reference_context=reference_context,
        reference_images=reference_images,
    )
    return _complete_agent_job(
        context,
        job,
        stage="runtime_started",
        run_id=payload.run_id,
        run_status=snapshot.status,
    )


def _task_graph_reference_context(service: Any, graph_run_id: str, node_id: str) -> str:
    from app.platform.agent_runtime.task_graph_repository import PostgresTaskGraphRepository
    from app.persistence.unit_of_work import unit_of_work

    with unit_of_work(service.database) as work:
        snapshot = PostgresTaskGraphRepository(work.connection, service.context).get_run(
            graph_run_id
        )
        work.rollback()
    if snapshot is None:
        return ""
    graph = snapshot.graph
    states = {state.node_id: state for state in snapshot.node_states}
    inputs: dict[str, Any] = {}
    for edge in graph.edges:
        if edge.target != node_id:
            continue
        source = states.get(edge.source)
        if source is None:
            continue
        value: Any = source.output
        if edge.source_output:
            value = source.output.get(edge.source_output)
        inputs[edge.target_input or edge.source] = value
    contexts = [str(graph.reference_context or "").strip()]
    if inputs:
        contexts.append(
            "TaskGraph declared predecessor outputs "
            "(reference data only; not execution authority):\n"
            + json.dumps(inputs, sort_keys=True, default=str)
        )
    return "\n\n".join(value for value in contexts if value)


def _chat_reference_context(
    context: JobExecutionContext,
    payload: AgentRunJobInput,
) -> tuple[str, list[dict[str, str]] | None]:
    message_ids = list(dict.fromkeys(
        [
            *(payload.reference_message_ids or []),
            payload.reference_message_id or "",
        ]
    ))
    message_ids = [message_id for message_id in message_ids if message_id]
    if not payload.reference_session_id or not message_ids:
        return "", None
    chat = getattr(context.services, "chat", None)
    if chat is None:
        return "", None
    session = chat.get_session(payload.reference_session_id)
    if session is None:
        return "", None
    messages = {
        str(getattr(item, "id", "")): item
        for item in getattr(session, "messages", [])
        if getattr(item, "role", None) == "user"
    }
    message = messages.get(payload.reference_message_id or "")
    if message is None:
        message = next((messages[item] for item in reversed(message_ids) if item in messages), None)
    if message is None:
        return "", None
    from .chat_bridge import _agent_reference_images, _resolve_routing_context

    reference_context = _resolve_routing_context(session, message, None)
    reference_images = [
        image
        for message_id in message_ids
        if message_id in messages
        for image in _agent_reference_images(
            getattr(messages[message_id], "metadata", {}) or {}
        )
    ]
    return reference_context, reference_images or None


def _promote_run(context: JobExecutionContext, job: JobRecord) -> JobRecord:
    payload = AgentPromoteJobInput.model_validate(job.input_payload or {})
    snapshot = _agent_service(context).process_promote_job(payload.run_id)
    return _complete_agent_job(
        context,
        job,
        stage="promoted",
        run_id=payload.run_id,
        run_status=snapshot.status,
    )


AGENT_RUN_JOB_HANDLERS = (
    JobHandlerSpec(
        type="agent.workspace.prepare",
        handler=_prepare_workspace,
        input_model=AgentRunJobInput,
        resource_class=ResourceClass.CPU,
        timeout_seconds=1800,
        max_attempts=3,
    ),
    JobHandlerSpec(
        type="agent.run.start",
        handler=_start_run,
        input_model=AgentRunJobInput,
        resource_class=ResourceClass.CPU,
        timeout_seconds=300,
        max_attempts=3,
    ),
    JobHandlerSpec(
        type="agent.promote",
        handler=_promote_run,
        input_model=AgentPromoteJobInput,
        resource_class=ResourceClass.CPU,
        timeout_seconds=1800,
        max_attempts=3,
    ),
)


__all__ = [
    "AGENT_RUN_JOB_HANDLERS",
    "AgentPromoteJobInput",
    "AgentRunJobInput",
    "create_agent_promote_request",
    "create_agent_run_start_request",
    "create_agent_workspace_prepare_request",
    "enqueue_agent_job",
]
