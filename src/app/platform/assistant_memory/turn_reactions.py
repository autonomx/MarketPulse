"""Assistant memory's reactions to chat events (ADR-0016, PA-3.4).

Chat publishes ``chat.turn.completed`` to the outbox after a turn commits;
this consumer queues the memory suggestion job for it. Chat imports memory's
contract, so memory reads the event by its wire contract (aggregate type,
event type and payload fields) rather than importing chat's model.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.persistence.identity_service import SYSTEM_ROLE
from app.runtime.tenant_context import TenantContext, pop_tenant, push_tenant

from .jobs import enqueue_memory_suggestion_job

logger = logging.getLogger(__name__)

class _TurnCompleted(BaseModel):
    """The fields of chat's ``chat.turn.completed`` payload this consumer reads."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    user_message_id: str
    user_id: str
    memory_writes_allowed: bool


def suggest_memories_after_turn(_connection: Any, event: dict[str, Any]) -> dict[str, Any]:
    """Queue the turn's memory suggestion job; idempotent, so a redelivered event queues nothing new."""
    turn = _TurnCompleted.model_validate(event["payload"])
    if not turn.memory_writes_allowed:
        return {"skipped": "memory_writes_off"}
    workspace_id = str(event["workspace_id"])
    token = push_tenant(TenantContext(
        user_id=turn.user_id, workspace_id=workspace_id, membership_id=f"system:{workspace_id}",
        roles=frozenset({SYSTEM_ROLE}),
    ))
    try:
        job = enqueue_memory_suggestion_job(turn.session_id, turn.user_message_id)
    finally:
        pop_tenant(token)
    if job is None:
        return {"skipped": "suggestions_off"}
    logger.info("Memory suggestion job queued: job_id=%s session_id=%s", job.id, turn.session_id)
    return {"job_id": job.id}

