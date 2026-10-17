"""PostgreSQL authority for legacy chat-session compatibility."""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from app.persistence.database import default_database
from app.persistence.document_store import PostgresDocumentStore
from app.persistence.module_repositories import PostgresModuleRecordRepository
from app.persistence.document_schemas import register_document_schema
from app.security.tenant_context import current_tenant

_T = TypeVar("_T")
_LEGACY_SESSIONS = {"module": "platform", "record_type": "legacy-chat-sessions", "record_id": "default"}


def load_legacy_chat_sessions() -> dict[str, Any]:
    payload = PostgresDocumentStore().read(
        module="platform",
        record_type="legacy-chat-sessions",
        default={},
    )
    return dict(payload or {})


def save_legacy_chat_sessions(payload: dict[str, Any]) -> None:
    PostgresDocumentStore().write(
        dict(payload),
        module="platform",
        record_type="legacy-chat-sessions",
    )


def mutate_legacy_chat_sessions(
    mutator: Callable[[dict[str, Any]], _T],
) -> tuple[dict[str, Any], _T]:
    database = default_database()
    context = current_tenant()
    with database.transaction() as connection:
        records = PostgresModuleRecordRepository(connection)
        records.ensure(context, payload={}, **_LEGACY_SESSIONS)
        current = dict(records.payload(context, **_LEGACY_SESSIONS, lock=True) or {})
        result = mutator(current)
        records.upsert(context, payload=current, **_LEGACY_SESSIONS)
    return current, result


def install_postgresql_legacy_session_callbacks() -> None:
    from app.conversation.legacy_sessions import install_legacy_session_callbacks

    install_legacy_session_callbacks(
        load_callback=load_legacy_chat_sessions,
        save_callback=save_legacy_chat_sessions,
        update_callback=mutate_legacy_chat_sessions,
    )


# Document shapes (WP-5.9): the retired session map, keyed by session id.
register_document_schema("platform", "legacy-chat-sessions", dict[str, Any])
