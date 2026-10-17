"""What the kernel reads about assistant memory without loading it (ADR-0016): kernel imports only."""
from __future__ import annotations

import json
from typing import Any

from app.persistence.declarations import LegacyImport


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _restore_memory_lifecycle(work: Any, context: Any, _stable_id: str, item: dict[str, Any]) -> None:
    """A legacy memory record's candidates, snapshots and events (PA-2.2)."""
    for candidate in list(item.get("candidates") or []):
        work.connection.execute(
            """
            INSERT INTO omnix_memory_candidates (
                id, workspace_id, source_session_id, source_message_id,
                candidate_fingerprint, proposed_owner_type, proposed_owner_id,
                proposed_category, proposed_content, confidence, source,
                trust_level, sensitivity, extraction_metadata, status,
                created_at, resolved_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP),
                %s::timestamptz
            ) ON CONFLICT (id) DO NOTHING
            """,
            (
                candidate["id"],
                context.workspace_id,
                candidate.get("source_session_id"),
                candidate.get("source_message_id") or candidate["id"],
                candidate.get("candidate_fingerprint") or candidate["id"],
                candidate.get("proposed_owner_type", "workspace"),
                candidate.get("proposed_owner_id", context.workspace_id),
                candidate.get("proposed_category", "fact"),
                candidate.get("proposed_content", ""),
                float(candidate.get("confidence", 0.5)),
                candidate.get("source", "imported"),
                candidate.get("trust_level", "unverified_import"),
                candidate.get("sensitivity", "normal"),
                _json(candidate.get("extraction_metadata") or {}),
                candidate.get("status", "pending"),
                candidate.get("created_at"),
                candidate.get("resolved_at"),
            ),
        )
    for snapshot in list(item.get("snapshots") or []):
        work.connection.execute(
            """
            INSERT INTO omnix_memory_snapshots (
                id, workspace_id, owner_type, owner_id, revision, status,
                session_id, token_estimate, created_at, refreshed_at
            ) VALUES (
                %s, %s, %s, %s, %s, 'active', %s, %s,
                COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), %s::timestamptz
            ) ON CONFLICT (id) DO NOTHING
            """,
            (
                snapshot["id"],
                context.workspace_id,
                snapshot.get("owner_type", "system"),
                snapshot.get("owner_id", "system-assistant"),
                int(snapshot.get("revision", 1)),
                snapshot.get("session_id"),
                int(snapshot.get("token_estimate", 0)),
                snapshot.get("created_at"),
                snapshot.get("refreshed_at"),
            ),
        )
        for position, entry in enumerate(list(snapshot.get("items") or [])):
            work.connection.execute(
                """
                INSERT INTO omnix_memory_snapshot_items (
                    snapshot_id, memory_record_id, position, record_revision,
                    frozen_content, revoked_at
                ) VALUES (%s, %s, %s, %s, %s, %s::timestamptz)
                ON CONFLICT (snapshot_id, memory_record_id) DO NOTHING
                """,
                (
                    snapshot["id"],
                    entry["memory_record_id"],
                    int(entry.get("position", position)),
                    int(entry.get("record_revision", 1)),
                    entry.get("frozen_content"),
                    entry.get("revoked_at"),
                ),
            )
    for event in list(item.get("events") or []):
        work.connection.execute(
            """
            INSERT INTO omnix_memory_events (
                workspace_id, entity_type, entity_id, event_type, payload, created_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb,
                      COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))
            """,
            (
                context.workspace_id,
                event.get("entity_type", "memory"),
                event.get("entity_id") or event.get("id") or "legacy",
                event.get("event_type", "legacy.event"),
                _json(event.get("payload") or {}),
                event.get("created_at"),
            ),
        )


_SCOPES = {"global", "workspace", "project", "session"}
_CATEGORIES = {"preference", "fact", "project", "relationship", "instruction"}
_SOURCES = {"user_saved", "assistant_suggested", "imported", "hermes"}
_TRUST = {"user_approved", "system_trusted", "unverified_import", "unverified_agent", "external_untrusted"}


def _choice(value: Any, allowed: set[str], default: str) -> Any:
    return value if value in allowed else default


def _restore_memory_record(work: Any, context: Any, stable_id: str, item: dict[str, Any]) -> None:
    """A legacy curated memory record, into Memory v2 (WP-8.5).

    Memory v1 is retired, so the record becomes a v2 curated record of the
    tenant workspace; a resumed import returns the same record (idempotency
    key). Pre-owner records (owned by a scope) belong to the System Assistant
    in that scope. A trust level the current contract does not know is an
    unverified import, which needs approval before it reaches a prompt. The
    scheduler's convergence task derives and indexes it.
    """
    from datetime import datetime, timezone

    from app.conversation.memory_contracts import MemoryRecord
    from app.platform.assistant_memory.v2.curated_records import PostgresMemoryV2CuratedRecords

    owner_type = str(item.get("owner_type") or "system")
    owned = owner_type in {"system", "character"}
    scope = str(item.get("scope") or (owner_type if owner_type in _SCOPES else "workspace"))
    scope_id = str(item.get("scope_id") or (item.get("owner_id") if owner_type in _SCOPES else context.workspace_id))
    content = str(item["content"]).strip()
    now = datetime.now(timezone.utc).isoformat()
    record = MemoryRecord(
        id=stable_id,
        owner_type="character" if owner_type == "character" else "system",
        owner_id=str(item.get("owner_id")) if owned and item.get("owner_id") else "system-assistant",
        scope=_choice(scope, _SCOPES, "workspace"),
        scope_id=scope_id,
        category=_choice(item.get("category"), _CATEGORIES, "fact"),
        source=_choice(item.get("source"), _SOURCES, "imported"),
        content=content,
        normalized_content=str(item.get("normalized_content") or content.lower()),
        confidence=float(item.get("confidence", 1.0)),
        pinned=bool(item.get("pinned", False)),
        trust_level=_choice(item.get("trust_level"), _TRUST, "unverified_import"),
        sensitivity=_choice(item.get("sensitivity"), {"normal", "sensitive", "secret"}, "normal"),
        provenance_type="import",
        provenance_id=str(item.get("provenance_id") or f"legacy:{stable_id}")[:240],
        status=_choice(item.get("status"), {"active", "superseded", "archived"}, "active"),
        created_at=str(item.get("created_at") or now),
        updated_at=str(item.get("updated_at") or item.get("created_at") or now),
        expires_at=item.get("expires_at"),
    )
    PostgresMemoryV2CuratedRecords(work.database).create(
        context.workspace_id, record, idempotency_key=f"legacy-import:{stable_id}",
    )


LEGACY_IMPORTS = (
    LegacyImport("assistant-memory.lifecycle", _restore_memory_lifecycle),
    LegacyImport("assistant-memory.record", _restore_memory_record),
)
