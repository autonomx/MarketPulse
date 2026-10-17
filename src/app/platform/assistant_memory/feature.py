"""Assistant-memory feature declaration."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.platform.assistant_memory.jobs import (
    MEMORY_SUGGEST_JOB_TYPE,
    MemorySuggestionJobInput,
    process_memory_suggestion_job,
)
from app.platform.assistant_memory.owner_defaults import default_memory_service
from app.platform.chat.contracts import CHAT_MEMORY
from app.events.outbox_relay import OutboxConsumer
from app.jobs.handlers import JobExecutionContext, JobHandlerSpec
from app.jobs.models import JobRecord, ResourceClass
from app.runtime.features import FeatureContext, FeatureModule
from app.runtime.ports import ContributionSpec
from app.platform.assistant_memory.persistence.settings_store import assistant_memory_setting_spec

from .routes import register_assistant_memory_routes


def _execute_memory_suggestion(
    context: JobExecutionContext,
    job: JobRecord,
) -> JobRecord:
    services = context.services
    chat_store = getattr(services, "chat", None)
    if chat_store is None:
        raise RuntimeError("assistant memory job requires the chat service")
    process_memory_suggestion_job(
        job,
        chat_store=chat_store,
        memory_service=default_memory_service(),
        job_store=context.job_store,
        already_claimed=job.lease is not None,
    )
    return context.job_store.get_job(job.id) or job


def _suggest_memories_after_turn(connection: Any, event: dict[str, Any]) -> dict[str, Any]:
    from .turn_reactions import suggest_memories_after_turn

    return suggest_memories_after_turn(connection, event)


# Memory suggestions follow chat's completed turns through the outbox (PA-3.4).
_TURN_SUGGESTIONS = OutboxConsumer(
    consumer_name="assistant-memory.turn-suggestions",
    aggregate_types=frozenset({"chat_session"}),
    handler=_suggest_memories_after_turn,
    event_type_pattern="chat.turn.completed",
)


def _chat_memory(_context: FeatureContext) -> Any:
    from .chat_prompt import AssistantChatMemory

    return AssistantChatMemory()


def _router(context: FeatureContext) -> APIRouter:
    router = APIRouter()
    services = context.services
    kwargs: dict[str, Any] = {"chat_store_factory": lambda: services.chat}
    settings_service = getattr(services, "settings", None)
    if settings_service is not None:
        from app.platform.assistant_memory.persistence.settings_store import (
            SettingsServiceAssistantMemorySettingsStore,
        )

        kwargs["memory_settings_store_factory"] = lambda: (
            SettingsServiceAssistantMemorySettingsStore(settings_service)
        )
    register_assistant_memory_routes(router, **kwargs)
    return router

FEATURE = FeatureModule(
    id="assistant-memory",
    title="Assistant Memory",
    tier="platform",
    depends_on=("chat",),
    # Initiative planning reads the tools capability dashboard when tools are enabled.
    uses=("assistant-tools",),
    routers=(_router,),
    job_handlers=(
        JobHandlerSpec(
            type=MEMORY_SUGGEST_JOB_TYPE,
            handler=_execute_memory_suggestion,
            input_model=MemorySuggestionJobInput,
            resource_class=ResourceClass.CPU,
            timeout_seconds=300,
            max_attempts=3,
        ),
    ),
    outbox_consumers=(_TURN_SUGGESTIONS,),
    # Chat's memory: prompt memory, memory commands, snapshots and settings (PA-3.2).
    contributions=(ContributionSpec(CHAT_MEMORY, _chat_memory),),
    settings=(assistant_memory_setting_spec(),),
)
