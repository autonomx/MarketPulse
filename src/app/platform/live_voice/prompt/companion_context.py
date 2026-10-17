"""Build deterministic companion packets for the live-voice prompt path."""
from __future__ import annotations
from app.config.env import env_str as _env_str

from collections.abc import Callable
from typing import Any

from app.platform.assistant_memory.contracts import (
    build_companion_context_packet,
    companion_rollout_policy,
    initiative_prompt_directive,
    load_memory_runtime_settings,
    observe_paralinguistic_turn,
    paralinguistic_prompt_directive,
    plan_companion_initiative,
    record_companion_diagnostics,
    record_memory_usage,
    resolve_session_memory_scope,
    retrieve_temporal_context,
)
from app.conversation.contracts import PromptMemoryItem
from app.platform.characters.contracts import (
    LiveConversationProfile,
    default_live_conversation_profile_store,
)
from app.platform.chat.contracts import (
    build_prompt_assembly,
    compaction_enabled,
    render_prompt_assembly,
    resolve_prompt_memory,
)

from app.platform.live_voice.prompt import profile as live_profile
from app.observability.tts_stream_diagnostics import stream_log

_TEMPORAL_CATEGORY = {
    "routine": "routine",
    "open_loop": "open_loop",
    "goal": "goal",
    "episode": "episode",
    "relationship_state": "relationship",
}


def _temporal_prompt_memory(result: Any) -> list[PromptMemoryItem]:
    values: list[PromptMemoryItem] = []
    for selected in result.items:
        record = selected.record
        values.append(
            PromptMemoryItem(
                memory_id=record.id,
                content=record.content,
                scope=record.scope,
                category=_TEMPORAL_CATEGORY.get(record.kind, record.category),
                revision=record.revision,
                source="character" if record.owner_type == "character" else "system",
            )
        )
    return values


def _merge_memory(
    approved: list[PromptMemoryItem],
    temporal: list[PromptMemoryItem],
) -> list[PromptMemoryItem]:
    merged: dict[str, PromptMemoryItem] = {item.memory_id: item for item in approved}
    for item in temporal:
        merged[item.memory_id] = item
    return list(merged.values())


def _effective_profile(session_id: str) -> LiveConversationProfile:
    try:
        from app.platform.live_voice.prompt.cache import get_live_conversation_profile_cached

        return get_live_conversation_profile_cached(
            default_live_conversation_profile_store(),
            session_id,
        ).effective
    except Exception:
        return LiveConversationProfile()


def _create_memory_service(factory: Callable[[], Any]) -> Any:
    """Create the memory service only when live prompt resolution needs it."""
    return factory()


def _timed(name: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    import time

    started = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        from app.platform.live_voice.prompt.cache import record_prompt_stage_time
        from app.platform.live_voice.prompt.dependency_stages import record_dependency_stage_time

        record_prompt_stage_time(name, elapsed_ms)
        record_dependency_stage_time(name, elapsed_ms)


def _lazy_memory_service_factory(factory: Callable[[], Any]) -> Callable[[], Any]:
    """Return a turn-local memoized factory without constructing eagerly."""
    service: Any | None = None

    def resolve() -> Any:
        nonlocal service
        if service is None:
            service = _create_memory_service(factory)
        return service

    return resolve


def build_companion_prompt(
    self: Any,
    session: Any,
    user_message: Any,
    context_items: list[dict[str, Any]] | None,
):
    from app.providers.service import get_global_system_prompt

    budget = _timed("budget_ms", live_profile._live_voice_prompt_budget)
    memory_service_factory = _lazy_memory_service_factory(self.memory_service_factory)
    query = str(getattr(user_message, "content", "") or "")
    approved_memory, memory_diagnostics = _timed(
        "memory_ms",
        resolve_prompt_memory,
        session,
        query_text=query,
        memory_service_factory=memory_service_factory,
    )
    settings = load_memory_runtime_settings()
    rollout = companion_rollout_policy(settings)
    scope_context = resolve_session_memory_scope(session)
    private_mode = (
        getattr(session, "transcript_policy", "persistent") != "persistent"
        or not settings.transcript_retention_enabled
    )
    profile = _timed("profile_ms", _effective_profile, session.id)
    if profile.initiative_mode == "active" and not rollout.active_initiative_enabled:
        profile = profile.model_copy(update={"initiative_mode": "gentle"})
    temporal_result = None
    legacy_temporal_allowed = memory_diagnostics.get("authority") != "v2"
    if (
        legacy_temporal_allowed
        and memory_diagnostics.get("memory_enabled")
        and rollout.memory_read_enabled
    ):
        temporal_result = retrieve_temporal_context(
            memory_service_factory(),
            scope_context,
            query,
            timezone_name=_env_str("OMNIX_USER_TIMEZONE"),
            deadline_ms=float(
                _env_str("OMNIX_LIVE_MEMORY_RETRIEVAL_DEADLINE_MS") or 50
            ),
            limit=int(_env_str("OMNIX_LIVE_MEMORY_RETRIEVAL_LIMIT") or 12),
        )
    temporal_memory = _temporal_prompt_memory(temporal_result) if temporal_result else []
    packet = _timed(
        "packet_ms",
        build_companion_context_packet,
        session,
        user_message,
        _merge_memory(
            approved_memory if rollout.memory_read_enabled else [],
            temporal_memory,
        ),
        token_budget=budget.memory_tokens,
        category_section_overrides={
            "routine": "due_routines",
            "open_loop": "open_loops",
            "goal": "active_goals",
            "episode": "recent_episodes",
        },
        category_score_overrides={
            "routine": 575,
            "open_loop": 525,
            "goal": 475,
            "episode": 350,
        },
    )
    _timed(
        "memory_usage_ms",
        record_memory_usage,
        session.id,
        [
            {
                "memory_id": item.memory_id,
                "selection_reason": item.selection_reason,
                "activation_score": item.activation_score,
                "section": item.section,
                "source_revision": item.source_revision,
            }
            for items in packet.sections.values()
            for item in items
        ],
    )
    summary_record = (
        self.summary_repository_factory().latest(session.id)
        if compaction_enabled()
        else None
    )
    recent_message_limit = live_profile._live_voice_recent_message_limit()
    assembly = _timed(
        "assembly_ms",
        build_prompt_assembly,
        session,
        user_message,
        global_system_prompt=get_global_system_prompt(),
        context_items=context_items or [],
        approved_memory=packet.prompt_memory,
        retrieved_history=[],
        session_summary=summary_record.summary if summary_record is not None else None,
        recent_message_limit=recent_message_limit,
        budget=budget,
        identity_resolver=_cached_identity_resolver(),
    )
    initiative_decision = None
    if temporal_result is not None and rollout.proactive_memory_enabled:
        try:
            initiative_decision = plan_companion_initiative(
                temporal_result,
                scope_context,
                profile,
                query,
                privacy_mode=private_mode,
            )
            initiative_directive = initiative_prompt_directive(
                initiative_decision,
                temporal_result,
            )
            if initiative_directive:
                assembly.system_instructions.append(initiative_directive)
        except Exception:
            initiative_decision = None
    paralinguistic_state = None
    if rollout.paralinguistic_signals_enabled:
        paralinguistic_state = observe_paralinguistic_turn(
            session.id,
            query,
            metadata=dict(getattr(user_message, "metadata", {}) or {}),
            private_mode=private_mode,
        )
        style_directive = paralinguistic_prompt_directive(
            paralinguistic_state,
            emotional_attunement=profile.emotional_attunement,
        )
        if style_directive:
            assembly.system_instructions.append(style_directive)
    assembly.diagnostics["memory"] = memory_diagnostics
    assembly.diagnostics["companion_context"] = packet.content_free_diagnostics()
    assembly.diagnostics["temporal_retrieval"] = (
        temporal_result.content_free_diagnostics()
        if temporal_result is not None
        else {
            "candidate_count": 0,
            "selected_count": 0,
            "disabled_reason": (
                "memory_v2_authoritative"
                if not legacy_temporal_allowed
                else "rollout_stage_disabled"
                if not rollout.memory_read_enabled
                else "memory_not_enabled"
            ),
        }
    )
    assembly.diagnostics["initiative"] = (
        initiative_decision.content_free_diagnostics()
        if initiative_decision is not None
        else {
            "action": "suppress",
            "reason": (
                "memory_v2_authoritative"
                if not legacy_temporal_allowed
                else "rollout_stage_disabled"
                if not rollout.proactive_memory_enabled
                else "initiative_unavailable"
            ),
            "proactive": False,
        }
    )
    assembly.diagnostics["paralinguistic_state"] = (
        paralinguistic_state.content_free_diagnostics()
        if paralinguistic_state is not None
        else {
            "signal_count": 0,
            "signal_kinds": [],
            "private_mode": private_mode,
            "durable_candidate_created": False,
            "disabled_reason": "rollout_stage_disabled",
        }
    )
    assembly.diagnostics["rollout"] = rollout.content_free_diagnostics()
    assembly.diagnostics["compaction"] = (
        {
            "enabled": True,
            "summary_id": summary_record.id,
            "summary_revision": summary_record.revision,
            "through_message_id": summary_record.through_message_id,
            "source_message_count": summary_record.source_message_count,
            "recent_message_limit": recent_message_limit,
        }
        if summary_record is not None
        else {
            "enabled": compaction_enabled(),
            "summary_id": None,
            "recent_message_limit": recent_message_limit,
        }
    )
    assembly.diagnostics["history_recall"] = {
        "enabled": False,
        "retrieved_count": 0,
        "reason": "live_voice_latency_profile",
    }
    rendered = _timed("render_ms", render_prompt_assembly, assembly)
    assembly.diagnostics["latency_profile"] = {
        "name": "live_voice",
        "companion_context_enabled": rollout.memory_read_enabled,
        "temporal_retrieval_enabled": temporal_result is not None,
        "initiative_enabled": initiative_decision is not None,
        "paralinguistic_enabled": bool(
            paralinguistic_state and paralinguistic_state.signals
        ),
        "recent_message_limit": recent_message_limit,
        "max_input_tokens": budget.max_input_tokens,
        "reserved_output_tokens": budget.reserved_output_tokens,
        "memory_tokens": budget.memory_tokens,
        "summary_tokens": budget.summary_tokens,
        "history_tokens": budget.history_tokens,
        "external_context_tokens": budget.external_context_tokens,
        "estimated_tokens": rendered.diagnostics.estimated_tokens,
    }
    record_companion_diagnostics(assembly.diagnostics)
    stream_log(
        "gateway-live-chat-first-token",
        "runtime",
        "live_chat_companion_context_packet",
        session_message_count=len(getattr(session, "messages", []) or []),
        selected_count=packet.selected_count,
        candidate_count=packet.candidate_count,
        packet_tokens=packet.token_estimate,
        packet_budget=packet.token_budget,
        packet_build_ms=round(packet.build_ms, 3),
        cache_hit=packet.cache_hit,
        truncated=packet.truncated,
        rollout_stage=rollout.stage,
        temporal_selected_count=(
            temporal_result.selected_count if temporal_result else 0
        ),
        temporal_preload_ms=(
            round(temporal_result.preload_ms, 3) if temporal_result else 0.0
        ),
        temporal_preload_timed_out=(
            temporal_result.preload_timed_out if temporal_result else False
        ),
        initiative_action=(
            initiative_decision.action if initiative_decision else "suppress"
        ),
        initiative_reason=(
            initiative_decision.reason
            if initiative_decision
            else "initiative_unavailable"
        ),
        initiative_tool=(
            initiative_decision.requested_tool if initiative_decision else None
        ),
        paralinguistic_signal_count=(
            len(paralinguistic_state.signals) if paralinguistic_state else 0
        ),
        paralinguistic_signal_kinds=(
            [signal.kind for signal in paralinguistic_state.signals]
            if paralinguistic_state
            else []
        ),
    )
    return assembly, rendered


def _cached_identity_resolver() -> Callable[[Any], Any]:
    from app.platform.live_voice.prompt.cache import resolve_system_session_identity_cached

    return resolve_system_session_identity_cached


__all__ = ["build_companion_prompt"]
