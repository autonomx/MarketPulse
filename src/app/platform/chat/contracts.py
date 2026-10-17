"""Stable Chat services and data types consumed by neighboring features."""
from __future__ import annotations

from importlib import import_module

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.runtime.ports import Port

from app.platform.chat.compaction import build_deterministic_summary, compaction_enabled
from app.platform.chat.context_budget import PromptBudget, prompt_budget_from_env
from app.platform.chat.live_call_prewarm import live_call_provider_affinity
from app.platform.chat.live_chat_async_sse_bridge import eager_async_sse_stream
from app.platform.chat.memory_port import CHAT_MEMORY, ChatMemory, resolve_prompt_memory
from app.platform.chat.retention_policy import explicit_memory_mutation_allowed
from app.platform.chat.models import ChatMessage, ChatSession, SendChatMessageRequest, SendChatMessageResponse
from app.platform.chat.research_jobs import link_user_message_to_research_job
from app.platform.chat.character_store import CHAT_STORE_FACTORY
from app.platform.chat.prompt_assembly import (
    PromptAssembly,
    build_prompt_assembly,
    resolve_system_session_identity,
)
from app.platform.chat.prompt_window import (
    build_prompt_assembly_with_window,
    normal_chat_prompt_window_enabled,
    normal_chat_recent_message_limit,
)
from app.platform.chat.provider_routing import resolve_effective_provider_id
from app.platform.chat.session_identity import CHARACTER_RESOLVER, CharacterResolver, resolve_shared_memory_categories
from app.platform.chat.prompt_rendering import (
    RenderedPrompt,
    RenderedPromptMessage,
    render_prompt_assembly,
)
from app.platform.chat.provider_metrics import merge_provider_response_metrics
from app.platform.chat.routing_deadline import provider_turn_deadline, remaining_turn_seconds
from app.platform.chat.store import _model_key as model_key
from app.platform.chat.store import _provider_key as provider_key
from app.conversation.contracts import estimate_tokens


# Assist mode diagnostics for the Hermes routes; loaded on first use (WP-8.2).
class AssistReadout(Protocol):
    """A named, read-only payload assist mode can return (ADR-0016 port)."""

    name: str

    def payload(self, args: dict[str, Any]) -> dict[str, Any]: ...


# Features that offer assist-mode readouts contribute them; chat never imports them.
ASSIST_READOUTS: Port[AssistReadout] = Port("chat.assist_readouts", AssistReadout, "many")


@dataclass(frozen=True)
class ResearchTurn:
    """How research applies to one assistant-context turn, as chat sees it."""

    requested_mode: str
    effective_mode: str
    status: str
    reason: str
    warnings: list[str] = field(default_factory=list)
    release: dict[str, Any] = field(default_factory=dict)
    notice: str | None = None
    show_diagnostics: bool = False
    unavailable: dict[str, Any] | None = None
    state: Any = None


class ChatResearch(Protocol):
    """Research behaviour the assistant-context chat endpoints use (ADR-0016 port)."""

    def resolve_turn(
        self, request: Any, session_id: str, *, settings: Any = None, release_policy: Any = None, policy: Any = None,
    ) -> ResearchTurn: ...

    def begin_deep_research(
        self, session_id: str, request: Any, *, chat_store: Any, job_store: Any, turn: ResearchTurn, send_request: Any,
    ) -> SendChatMessageResponse: ...

    def quick_context(
        self, request: Any, *, web_search_factory: Any = None, quick_search_factory: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...

    def render_cited_reply(
        self, content: str, context_items: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]] | None: ...


# The research feature contributes this; without it, turns run with research disabled.
CHAT_RESEARCH: Port[ChatResearch] = Port("chat.research", ChatResearch, "at_most_one")


class TypedTurnRouter(Protocol):
    """Routes a typed chat turn to the agent runtime; returns None to let chat answer."""

    def __call__(self, session: Any, user_message: Any, **options: Any) -> Any: ...


# The agent runtime contributes this; without it chat answers every turn itself.
TYPED_TURN_ROUTER: Port[TypedTurnRouter] = Port("chat.typed_turn_router", TypedTurnRouter, "at_most_one")


def route_typed_turn(session: Any, user_message: Any, **options: Any) -> Any:
    from app.runtime.ports import optional

    router = optional(TYPED_TURN_ROUTER)
    return router(session, user_message, **options) if router is not None else None


def hermes_assist_status_payload() -> dict:
    from app.platform.chat.assist.diagnostics import hermes_diagnostics_status_payload

    return hermes_diagnostics_status_payload()


def hermes_assist_test_payload(*, content: str, session_id: str, domain: str, metadata: dict) -> dict:
    from app.platform.chat.assist.diagnostics import HermesDiagnosticsTestRequest, hermes_diagnostics_test_payload

    return hermes_diagnostics_test_payload(
        HermesDiagnosticsTestRequest(content=content, session_id=session_id, domain=domain, metadata=metadata)
    )


def hermes_assist_readout_payload(name: str, args: dict) -> dict:
    from app.platform.chat.assist.modes import readout_payload

    return readout_payload(name, args)

__all__ = [
    "CHAT_MEMORY",
    "ChatMemory",
    "explicit_memory_mutation_allowed",
    "resolve_shared_memory_categories",
    "CHARACTER_RESOLVER",
    "CharacterResolver",
    "TYPED_TURN_ROUTER",
    "TypedTurnRouter",
    "route_typed_turn",
    "CHAT_STORE_FACTORY",
    "CHAT_RESEARCH",
    "ChatResearch",
    "ResearchTurn",
    "SendChatMessageResponse",
    "link_user_message_to_research_job",
    "ASSIST_READOUTS",
    "AssistReadout",
    "ChatMessage",
    "ChatSession",
    "PromptAssembly",
    "PromptBudget",
    "RenderedPrompt",
    "RenderedPromptMessage",
    "SendChatMessageRequest",
    "build_deterministic_summary",
    "build_prompt_assembly",
    "build_prompt_assembly_with_window",
    "compaction_enabled",
    "eager_async_sse_stream",
    "estimate_tokens",
    "hermes_assist_readout_payload",
    "hermes_assist_status_payload",
    "hermes_assist_test_payload",
    "live_call_provider_affinity",
    "merge_provider_response_metrics",
    "model_key",
    "normal_chat_prompt_window_enabled",
    "normal_chat_recent_message_limit",
    "prompt_budget_from_env",
    "provider_key",
    "provider_turn_deadline",
    "remaining_turn_seconds",
    "render_prompt_assembly",
    "resolve_prompt_memory",
    "resolve_effective_provider_id",
    "resolve_system_session_identity",
]


# Services apps import through this contract, loaded on first use (ADR-0016).
_LAZY_EXPORTS = {
    # Events chat publishes through the outbox (PA-3.4).
    "CHAT_SESSION_AGGREGATE": "turn_events",
    "CHAT_TURN_COMPLETED": "turn_events",
    "ChatTurnCompleted": "turn_events",
    "turn_completed_event_key": "turn_events",
    "build_chat_routing_context": "routing_context",
    "stream_live_call_greeting_chunks": "live_call_greeting",
    "ProactiveDeliveryRequest": "live_conversation_proactive",
    "ProactiveDeliveryResponse": "live_conversation_proactive",
    "commit_proactive_delivery": "live_conversation_proactive",
    "stream_proactive_turn_chunks": "live_conversation_proactive",
    "ChatSessionStore": "character_store",
    "default_chat_store": "character_store",
}


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"app.platform.chat.{module}"), name)
