"""Resolve authoritative memory into trusted chat prompt items (chat calls it through CHAT_MEMORY, PA-3.2)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.platform.chat.contracts import prompt_budget_from_env, resolve_shared_memory_categories
from app.conversation.contracts import ChatSession, PromptMemoryItem

from .contracts import (
    MemoryService,
    default_memory_service,
    estimate_memory_tokens,
    load_memory_runtime_settings,
    resolve_chat_scope,
    resolve_session_memory_scope,
    resolve_snapshot_view,
    select_memory_records,
)
from .v2 import MemorySpaceKey, RetrievalQuery, VisibilityScope


def chat_memory_enabled() -> bool:
    return load_memory_runtime_settings().curated_memory_enabled


def _memory_v2_runtime(
    runtime_factory: Callable[[], Any] | None,
) -> Any | None:
    if runtime_factory is not None:
        return runtime_factory()
    from app.persistence.runtime import uses_postgresql_runtime
    if not uses_postgresql_runtime():
        return None
    from app.platform.assistant_memory.v2.runtime import PostgresMemoryV2Runtime

    return PostgresMemoryV2Runtime()


def _v2_space_and_scopes(session: ChatSession) -> tuple[MemorySpaceKey, tuple[VisibilityScope, ...]]:
    context = resolve_session_memory_scope(session)
    scopes = [VisibilityScope(kind="global", scope_id=context.profile_id)]
    scopes.append(VisibilityScope(kind="workspace", scope_id=context.workspace_id))
    if context.project_id:
        scopes.append(VisibilityScope(kind="project", scope_id=context.project_id))
    scopes.append(VisibilityScope(kind="session", scope_id=context.session_id))
    return (
        MemorySpaceKey(
            principal_id=_memory_principal(),
            owner_type=context.owner_type,
            owner_id=context.owner_id,
        ),
        tuple(scopes),
    )


def _memory_principal() -> str:
    """Memory v2 spaces belong to the tenant workspace; profiles are visibility scopes."""
    from app.runtime.tenant_context import current_tenant

    return current_tenant().workspace_id


def _memory_query_text(session: ChatSession, explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for message in reversed(getattr(session, "messages", ())):
        if getattr(message, "role", None) == "user":
            content = str(getattr(message, "content", "") or "").strip()
            if content:
                return content
    return "current conversation memory context"


def _candidate_is_shared(candidate: Any) -> bool:
    return any(str(reason).startswith("memory_grant:") for reason in candidate.reasons)


def _v2_prompt_item(candidate: Any, *, shared: bool) -> PromptMemoryItem:
    return PromptMemoryItem(
        memory_id=candidate.ref_id,
        content=candidate.content,
        scope="retrieved",
        category=candidate.domain,
        revision=1,
        source="shared_memory_v2" if shared else "memory_v2",
    )


def _active_grant_ids(runtime: Any, space: MemorySpaceKey) -> tuple[str, ...]:
    return tuple(grant.grant_id for grant in runtime.grant_store.active_for_target(space))


def _granted_only_v2_items(
    runtime: Any,
    query: RetrievalQuery,
) -> list[PromptMemoryItem]:
    """Retrieve only granted source spaces when target-local reads are disabled."""

    candidates: list[Any] = []
    for grant in runtime.grant_store.active_for_target(query.space):
        allowed_scopes = query.visible_scopes
        if grant.scope_constraints:
            constraints = {(scope.kind, scope.scope_id) for scope in grant.scope_constraints}
            allowed_scopes = tuple(
                scope
                for scope in query.visible_scopes
                if (scope.kind, scope.scope_id) in constraints
            )
        if not allowed_scopes:
            continue
        source_query = query.model_copy(
            update={
                "query_id": f"{query.query_id}:grant:{grant.grant_id}"[:200],
                "space": grant.source_space,
                "visible_scopes": allowed_scopes,
                "domains": grant.allowed_domains,
                "grant_ids": (),
            }
        )
        result = runtime.local_retriever.retrieve(source_query)
        candidates.extend(result.candidates)

    best: dict[tuple[str, str], Any] = {}
    for candidate in candidates:
        key = (candidate.item_type, candidate.ref_id)
        current = best.get(key)
        if current is None or candidate.scores.composite > current.scores.composite:
            best[key] = candidate
    ranked = sorted(
        best.values(),
        key=lambda item: (-item.scores.composite, item.item_type, item.ref_id),
    )
    selected: list[PromptMemoryItem] = []
    used_tokens = 0
    for candidate in ranked:
        if len(selected) >= query.top_k:
            break
        cost = estimate_memory_tokens(candidate.content)
        if used_tokens + cost > query.token_budget:
            continue
        selected.append(_v2_prompt_item(candidate, shared=True))
        used_tokens += cost
    return selected


def _resolve_v2_prompt_memory(
    session: ChatSession,
    *,
    runtime: Any,
    service: MemoryService,
    read_allowed: bool,
    shared_allowed: bool,
    shared_categories: list[str],
    query_text: str | None,
    diagnostics: dict[str, Any],
) -> tuple[list[PromptMemoryItem], dict[str, Any]]:
    """Memory v2 prompt memory.

    Pinned memories always come first (pinning means "always include");
    ranked retrieval for the turn fills the rest of the budget; a character
    session's shared System Assistant memory follows the category rule, plus
    anything an explicit v2 grant federates.
    """
    authority = runtime.assert_v2_authoritative()
    space, visible_scopes = _v2_space_and_scopes(session)
    budget = prompt_budget_from_env().memory_tokens
    character_session = session.interaction_mode == "character"

    selected: list[PromptMemoryItem] = []
    used_tokens = 0
    if read_allowed:
        context = resolve_session_memory_scope(session)
        selection = select_memory_records(service.list_active(context), context, token_budget=budget)
        for record in selection.records:
            if not record.pinned:
                continue
            selected.append(PromptMemoryItem(
                memory_id=record.id,
                content=record.content,
                scope=record.scope,
                category=record.category,
                revision=record.revision,
                source="character" if character_session else "system",
            ))
            used_tokens += estimate_memory_tokens(record.content)

    query = RetrievalQuery(
        query_id=f"prompt-memory:{session.id}"[:200],
        space=space,
        visible_scopes=visible_scopes,
        text=_memory_query_text(session, query_text),
        authority="final",
        as_of=datetime.now(timezone.utc),
        top_k=12,
        token_budget=max(0, budget - used_tokens),
        deadline_ms=50.0,
        grant_ids=(
            _active_grant_ids(runtime, space)
            if read_allowed and shared_allowed
            else ()
        ),
    )

    if read_allowed:
        result = runtime.retrieve(query)
        retrieved = [
            _v2_prompt_item(candidate, shared=_candidate_is_shared(candidate))
            for candidate in result.candidates
            if candidate.prompt_eligible
        ]
        watermarks = {
            "observation": result.observation_watermark,
            "graph_revision": result.graph_revision,
            "index_graph_revision": result.index_graph_revision,
        }
    else:
        retrieved = _granted_only_v2_items(runtime, query) if shared_allowed else []
        state = runtime.graph_store.state(space)
        watermarks = {
            "observation": runtime.observation_store.watermark(space),
            "graph_revision": state.graph_revision,
            "index_graph_revision": runtime.search_index.index_graph_revision(space),
        }
    seen = {item.content for item in selected}
    for item in retrieved:
        if item.content not in seen:
            seen.add(item.content)
            selected.append(item)

    shared_excluded: dict[str, int] = {}
    if shared_allowed:
        owner_tokens = sum(estimate_memory_tokens(item.content) for item in selected)
        shared_items, shared_excluded = _shared_system_items(
            session, service, shared_categories, token_budget=max(0, budget - owner_tokens),
        )
        for item in shared_items:
            if item.content not in seen:
                seen.add(item.content)
                selected.append(item)

    shared_selected = [item for item in selected if item.source in {"shared_memory_v2", "shared_system"}]
    diagnostics.update({
        "status": "resolved_v2",
        "authority": "v2",
        "authority_epoch": authority.epoch.epoch,
        "snapshot_id": None,
        "snapshot_revision": None,
        "selected_memory_ids": [item.memory_id for item in selected],
        "selected_memory_count": len(selected),
        "shared_selected_memory_ids": [item.memory_id for item in shared_selected],
        "shared_selected_memory_count": len(shared_selected),
        "shared_excluded_reason_counts": shared_excluded,
        "v2_watermarks": watermarks,
    })
    return selected, diagnostics


def _shared_system_items(
    session: ChatSession,
    service: MemoryService,
    shared_categories: list[str],
    *,
    token_budget: int,
) -> tuple[list[PromptMemoryItem], dict[str, int]]:
    """The System Assistant memories a character session may read (the category rule).

    Allowed categories only, never session-scoped, normal sensitivity, then
    the usual selection within the remaining budget. Under Memory v2 the
    service reads the same records from v2.
    """
    excluded: dict[str, int] = {}
    system_context = resolve_chat_scope(
        session.id,
        profile_id=session.profile_id,
        workspace_id=session.workspace_id,
        project_id=session.project_id,
    )
    candidates = []
    for record in service.list_active(system_context):
        reason = None
        if record.scope == "session":
            reason = "session_scope_blocked"
        elif record.category not in shared_categories:
            reason = "category_not_allowed"
        elif record.sensitivity != "normal":
            reason = "sensitivity_not_normal"
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
        else:
            candidates.append(record)
    selection = select_memory_records(candidates, system_context, token_budget=token_budget)
    for reason, count in selection.diagnostics.excluded_reason_counts.items():
        excluded[reason] = excluded.get(reason, 0) + count
    items = [
        PromptMemoryItem(
            memory_id=record.id,
            content=record.content,
            scope=record.scope,
            category=record.category,
            revision=record.revision,
            source="shared_system",
        )
        for record in selection.records
    ]
    return items, excluded


def resolve_prompt_memory(
    session: ChatSession,
    *,
    query_text: str | None = None,
    memory_service_factory: Callable[[], MemoryService] = default_memory_service,
    memory_v2_runtime_factory: Callable[[], Any] | None = None,
) -> tuple[list[PromptMemoryItem], dict[str, Any]]:
    character_session = session.interaction_mode == "character"
    read_allowed = session.read_memory if character_session else session.memory_enabled
    shared_categories = resolve_shared_memory_categories(session)
    shared_allowed = bool(shared_categories)
    diagnostics: dict[str, Any] = {
        "memory_enabled": bool(chat_memory_enabled() and (read_allowed or shared_allowed)),
        "owner_type": "character" if character_session else "system",
        "owner_id": session.character_id if character_session else "system-assistant",
        "snapshot_id": session.memory_snapshot_id,
        "snapshot_revision": session.memory_snapshot_revision,
        "selected_memory_ids": [],
        "selected_memory_count": 0,
        "invalidated_count": 0,
        "excluded_reason_counts": {},
        "shared_memory_access": session.shared_memory_access if character_session else "none",
        "shared_allowed_categories": shared_categories,
        "shared_selected_memory_ids": [],
        "shared_selected_memory_count": 0,
        "shared_excluded_reason_counts": {},
    }
    if not chat_memory_enabled():
        diagnostics["status"] = "disabled_by_feature_flag"
        return [], diagnostics
    if not read_allowed and not shared_allowed:
        diagnostics["status"] = "disabled_for_session"
        return [], diagnostics

    runtime = _memory_v2_runtime(memory_v2_runtime_factory)
    if runtime is not None:
        return _resolve_v2_prompt_memory(
            session,
            runtime=runtime,
            service=memory_service_factory(),
            read_allowed=read_allowed,
            shared_allowed=shared_allowed,
            shared_categories=shared_categories,
            query_text=query_text,
            diagnostics=diagnostics,
        )

    # Without PostgreSQL (the provider-free mode) memory is read from the
    # session's snapshot in the in-memory repository.
    if read_allowed and not session.memory_snapshot_id:
        diagnostics["status"] = "snapshot_missing"
        return [], diagnostics

    service = memory_service_factory()
    selected: list[PromptMemoryItem] = []
    excluded: dict[str, int] = {}
    invalidated_count = 0
    if read_allowed:
        context = resolve_session_memory_scope(session)
        view = resolve_snapshot_view(service, context, session.memory_snapshot_id)
        if view is None:
            diagnostics["status"] = "snapshot_unavailable"
            return [], diagnostics
        invalidated_count = view.invalidated_count
        for item in view.items:
            if not item.active:
                reason = item.invalidation_reason or "invalidated"
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            record = service.repository.get_record(item.memory_record_id)
            if record is None:
                excluded["record_forgotten"] = excluded.get("record_forgotten", 0) + 1
                continue
            if (record.owner_type, record.owner_id) != (context.owner_type, context.owner_id):
                excluded["owner_mismatch"] = excluded.get("owner_mismatch", 0) + 1
                continue
            selected.append(
                PromptMemoryItem(
                    memory_id=item.memory_record_id,
                    content=item.content,
                    scope=record.scope,
                    category=record.category,
                    revision=item.record_revision,
                    source="character" if character_session else "system",
                )
            )

    shared_excluded: dict[str, int] = {}
    shared_selected: list[PromptMemoryItem] = []
    if shared_allowed:
        owner_tokens = sum(estimate_memory_tokens(item.content) for item in selected)
        shared_selected, shared_excluded = _shared_system_items(
            session,
            service,
            shared_categories,
            token_budget=max(0, prompt_budget_from_env().memory_tokens - owner_tokens),
        )
        selected.extend(shared_selected)

    diagnostics.update({
        "status": "resolved",
        "authority": "provider_free",
        "selected_memory_ids": [item.memory_id for item in selected],
        "selected_memory_count": len(selected),
        "invalidated_count": invalidated_count,
        "excluded_reason_counts": excluded,
        "shared_selected_memory_ids": [item.memory_id for item in shared_selected],
        "shared_selected_memory_count": len(shared_selected),
        "shared_excluded_reason_counts": shared_excluded,
    })
    return selected, diagnostics


class AssistantChatMemory:
    """Assistant memory's implementation of chat's ``CHAT_MEMORY`` port (PA-3.2)."""

    def runtime_settings(self) -> Any:
        return load_memory_runtime_settings()

    def service(self) -> Any:
        return default_memory_service()

    def resolve_prompt_memory(self, session: Any, **options: Any) -> tuple[list[PromptMemoryItem], dict[str, Any]]:
        return resolve_prompt_memory(session, **options)

    def parse_command(self, content: str) -> Any | None:
        from .chat_commands import parse_memory_command

        return parse_memory_command(content)

    def execute_command(self, store: Any, service: Any, session_id: str, user_message_id: str, command: Any) -> Any:
        from .chat_commands import execute_memory_command

        return execute_memory_command(store, service, session_id, user_message_id, command)

    def create_session_snapshot(self, session: Any, *, token_budget: int) -> Any:
        return default_memory_service().create_session_snapshot(
            resolve_session_memory_scope(session), token_budget=token_budget,
        )
