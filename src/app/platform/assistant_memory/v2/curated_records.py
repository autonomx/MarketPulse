"""Curated memory records on Memory v2 (WP-8.5).

Once v2 is authoritative, the curated memory a person saves, approves, edits,
pins, moves, archives or forgets lives in the v2 observation log: one
``curated_memory`` observation per record revision, with the record in its
payload. A new revision revokes the previous one in the same transaction, so
a record has at most one active observation; forgetting purges every
revision, removing the text. v1 records imported before the cutover
(``imported_legacy_memory``) are read the same way, and editing one continues
it as a curated record.

A memory space is a tenant workspace's owner: ``principal_id`` is the tenant
workspace id, and profile, chat workspace, project and session are visibility
scopes inside it, exactly as v1 keys records by workspace and owner.

What may reach a prompt is decided at projection (``curated_projector``):
only active, non-secret, approved records become assertions, ending at the
record's expiry. Listing and management see every record.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, cast

from app.conversation.memory_contracts import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryRecord,
)
from app.persistence.database import PostgresDatabase, default_database

from .contracts import (
    MemorySpaceKey,
    Observation,
    ObservationProvenance,
    VisibilityScope,
)
from .observation_store import ObservationAppendRequest, ObservationIdempotencyConflict
from .runtime import PostgresMemoryV2Runtime

CURATED_EVENT = "curated_memory"
LEGACY_EVENT = "imported_legacy_memory"
CURATED_SCHEMA = "memory-v2-curated@1"
ACTOR = "memory-v2:curated-records"

_RECORD_ID_SQL = "COALESCE(o.payload->'memory_record'->>'id', o.payload->'legacy_record'->>'id')"
_RECORD_SQL = "COALESCE(o.payload->'memory_record', o.payload->'legacy_record')"
_EVENTS = (CURATED_EVENT, LEGACY_EVENT)

# v1 trust levels as v2 provenance trust.
TRUST_MAP = {
    "user_approved": "user_explicit",
    "system_trusted": "system_trusted",
    "unverified_import": "imported_unverified",
    "unverified_agent": "assistant_inference",
    "external_untrusted": "external_untrusted",
}
_SOURCE_TYPE = {
    "user_saved": "user",
    "assistant_suggested": "assistant",
    "hermes": "external",
    "imported": "import",
}


def tenant_principal() -> str:
    """The memory principal of the current request or job: its tenant workspace."""
    from app.runtime.tenant_context import current_tenant

    return current_tenant().workspace_id


def memory_space(principal_id: str, owner_type: str, owner_id: str) -> MemorySpaceKey:
    return MemorySpaceKey(principal_id=principal_id, owner_type=cast(Any, owner_type), owner_id=owner_id)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _revision_digest(space: MemorySpaceKey, record_id: str, revision: int) -> str:
    material = f"curated\0{space.principal_id}\0{space.owner_type}\0{space.owner_id}\0{record_id}\0{revision}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def curated_observation_id(space: MemorySpaceKey, record_id: str, revision: int) -> str:
    return f"obs:curated:{_revision_digest(space, record_id, revision)[:40]}"


def record_from_payload(payload: dict[str, Any]) -> MemoryRecord | None:
    value = payload.get("memory_record") or payload.get("legacy_record")
    return MemoryRecord.model_validate(value) if isinstance(value, dict) else None


def curated_request(
    space: MemorySpaceKey,
    record: MemoryRecord,
    *,
    idempotency_key: str | None = None,
) -> ObservationAppendRequest:
    """The append request for one revision of a curated record."""
    return ObservationAppendRequest(
        space=space,
        visibility_scope=VisibilityScope(kind=record.scope, scope_id=record.scope_id),
        event_type=CURATED_EVENT,
        occurred_at=_parse_time(record.updated_at),
        provenance=ObservationProvenance(
            source_type=cast(Any, _SOURCE_TYPE.get(record.source, "system")),
            source_id=f"memory:{record.id}"[:240],
            trust_level=cast(Any, TRUST_MAP[record.trust_level]),
            message_id=record.provenance_id[:200] if record.provenance_id else None,
        ),
        idempotency_key=idempotency_key
        or f"curated-memory:{_revision_digest(space, record.id, record.revision)}",
        observation_id=curated_observation_id(space, record.id, record.revision),
        payload={"memory_record": record.model_dump(mode="json"), "schema": CURATED_SCHEMA},
        sensitivity=record.sensitivity,
    )


class PostgresMemoryV2CuratedRecords:
    """Record-level reads and writes of curated memory on the v2 observation log.

    Writes require an active v2 authority epoch (the runtime enforces it under
    lock). Every read and write is confined to one principal (tenant).
    """

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        runtime: PostgresMemoryV2Runtime | None = None,
    ) -> None:
        self.database = database or default_database()
        self.runtime = runtime or PostgresMemoryV2Runtime(self.database)

    # -- reads ---------------------------------------------------------------

    @staticmethod
    def _current(
        connection: Any,
        principal_id: str,
        record_id: str,
        *,
        lock: bool = False,
    ) -> tuple[str, MemorySpaceKey, MemoryRecord] | None:
        row = connection.execute(
            f"""
            SELECT o.observation_id, o.owner_type, o.owner_id, o.payload
              FROM omnix_memory_v2_observations o
              LEFT JOIN omnix_memory_v2_observation_dispositions d
                ON d.observation_id = o.observation_id
             WHERE o.principal_id = %s
               AND o.event_type IN ('{CURATED_EVENT}', '{LEGACY_EVENT}')
               AND {_RECORD_ID_SQL} = %s
               AND COALESCE(d.state, 'active') = 'active'
             ORDER BY o.authority_sequence DESC
             LIMIT 1
             {"FOR UPDATE OF o" if lock else ""}
            """,
            (principal_id, record_id),
        ).fetchone()
        if row is None:
            return None
        record = record_from_payload(dict(row[3]))
        if record is None:
            return None
        return str(row[0]), memory_space(principal_id, str(row[1]), str(row[2])), record

    def get(self, principal_id: str, record_id: str) -> MemoryRecord | None:
        with self.database.transaction() as connection:
            current = self._current(connection, principal_id, record_id)
        return current[2] if current is not None else None

    def list(
        self,
        principal_id: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        scope: str | None = None,
        scope_id: str | None = None,
        status: str | None = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """Current revisions, ordered as v1 lists them (pinned, newest, id)."""
        conditions = [
            "o.principal_id = %s",
            f"o.event_type IN ('{CURATED_EVENT}', '{LEGACY_EVENT}')",
            "COALESCE(d.state, 'active') = 'active'",
        ]
        params: list[Any] = [principal_id]
        for column, value in (("o.owner_type", owner_type), ("o.owner_id", owner_id),
                              ("o.visibility_kind", scope), ("o.visibility_scope_id", scope_id)):
            if value is not None:
                conditions.append(f"{column} = %s")
                params.append(value)
        if status is not None:
            conditions.append(f"{_RECORD_SQL}->>'status' = %s")
            params.append(status)
        params.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT o.payload
                  FROM omnix_memory_v2_observations o
                  LEFT JOIN omnix_memory_v2_observation_dispositions d
                    ON d.observation_id = o.observation_id
                 WHERE {' AND '.join(conditions)}
                 ORDER BY ({_RECORD_SQL}->>'pinned')::boolean DESC,
                          ({_RECORD_SQL}->>'updated_at')::timestamptz DESC,
                          {_RECORD_ID_SQL} ASC
                 LIMIT %s OFFSET %s
                """,
                tuple(params),
            ).fetchall()
        records = [record_from_payload(dict(row[0])) for row in rows]
        return [record for record in records if record is not None]

    def find_by_idempotency_key(self, space: MemorySpaceKey, key: str) -> MemoryRecord | None:
        """The current revision of the record first written under ``key``."""
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT payload FROM omnix_memory_v2_observations
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND idempotency_key = %s
                """,
                (space.principal_id, space.owner_type, space.owner_id, key),
            ).fetchone()
        first = record_from_payload(dict(row[0])) if row is not None else None
        if first is None:
            return None
        return self.get(space.principal_id, first.id) or first

    # -- writes --------------------------------------------------------------

    def create(
        self,
        principal_id: str,
        record: MemoryRecord,
        *,
        idempotency_key: str | None = None,
    ) -> MemoryRecord:
        """Append a new record. With ``idempotency_key``, a retry returns the first result."""
        space = memory_space(principal_id, record.owner_type, record.owner_id)
        request = curated_request(space, record, idempotency_key=idempotency_key)

        def reject_existing(connection: Any) -> tuple[str, ...]:
            if self._current(connection, principal_id, record.id, lock=True) is not None:
                raise MemoryConflictError(f"memory {record.id} already exists")
            return ()

        try:
            observation = self.runtime.append_authoritative_next(
                request, supersede=reject_existing, actor_id=ACTOR,
            )
        except ObservationIdempotencyConflict:
            if idempotency_key is None:
                raise
            existing = self.find_by_idempotency_key(space, idempotency_key)
            if existing is None:  # pragma: no cover - the conflict proves it exists
                raise
            return existing
        return self._record_of(observation)

    def update(
        self,
        principal_id: str,
        record: MemoryRecord,
        *,
        expected_revision: int,
    ) -> MemoryRecord:
        """Write the next revision of an existing record; v1 revision semantics."""
        def check(connection: Any) -> tuple[str, ...]:
            current = self._current(connection, principal_id, record.id, lock=True)
            if current is None:
                raise MemoryNotFoundError(record.id)
            observation_id, space, stored = current
            if (space.owner_type, space.owner_id) != (record.owner_type, record.owner_id):
                raise MemoryConflictError(f"memory {record.id} cannot change owner")
            if stored.revision != expected_revision:
                raise MemoryConflictError(
                    f"memory revision conflict for {record.id}: "
                    f"expected {expected_revision}, actual {stored.revision}"
                )
            return (observation_id,)

        changed = record.model_copy(update={"revision": expected_revision + 1})
        space = memory_space(principal_id, record.owner_type, record.owner_id)
        try:
            observation = self.runtime.append_authoritative_next(
                curated_request(space, changed), supersede=check, actor_id=ACTOR,
            )
        except ObservationIdempotencyConflict as exc:
            # Another write already produced this revision: a stale expected revision.
            raise MemoryConflictError(
                f"memory revision conflict for {record.id}: revision {changed.revision} already written"
            ) from exc
        return self._record_of(observation)

    def forget(self, principal_id: str, record_id: str, *, expected_revision: int) -> bool:
        """Purge every revision of a record; ``False`` if it does not exist."""
        with self.database.transaction() as connection:
            current = self._current(connection, principal_id, record_id)
        if current is None:
            return False
        space = current[1]

        def purge(connection: Any) -> list[tuple[str, str, str]]:
            locked = self._current(connection, principal_id, record_id, lock=True)
            if locked is None:
                return []
            if locked[2].revision != expected_revision:
                raise MemoryConflictError(
                    f"memory revision conflict for {record_id}: "
                    f"expected {expected_revision}, actual {locked[2].revision}"
                )
            rows = connection.execute(
                f"""
                SELECT o.observation_id
                  FROM omnix_memory_v2_observations o
                  LEFT JOIN omnix_memory_v2_observation_dispositions d
                    ON d.observation_id = o.observation_id
                 WHERE o.principal_id = %s AND o.owner_type = %s AND o.owner_id = %s
                   AND o.event_type IN ('{CURATED_EVENT}', '{LEGACY_EVENT}')
                   AND {_RECORD_ID_SQL} = %s
                   AND COALESCE(d.state, 'active') <> 'purged'
                 ORDER BY o.authority_sequence
                """,
                (space.principal_id, space.owner_type, space.owner_id, record_id),
            ).fetchall()
            return [(str(row[0]), "purged", "memory forgotten") for row in rows]

        return self.runtime.govern_authoritative(space, purge, actor_id=ACTOR) > 0

    def purge_owner(self, principal_id: str, owner_type: str, owner_id: str) -> int:
        """Forget every curated record of one owner (owner reset). Returns records purged."""
        space = memory_space(principal_id, owner_type, owner_id)
        records: set[str] = set()

        def purge(connection: Any) -> list[tuple[str, str, str]]:
            rows = connection.execute(
                f"""
                SELECT o.observation_id, {_RECORD_ID_SQL}
                  FROM omnix_memory_v2_observations o
                  LEFT JOIN omnix_memory_v2_observation_dispositions d
                    ON d.observation_id = o.observation_id
                 WHERE o.principal_id = %s AND o.owner_type = %s AND o.owner_id = %s
                   AND o.event_type IN ('{CURATED_EVENT}', '{LEGACY_EVENT}')
                   AND COALESCE(d.state, 'active') <> 'purged'
                 ORDER BY o.authority_sequence
                """,
                (space.principal_id, space.owner_type, space.owner_id),
            ).fetchall()
            records.update(str(row[1]) for row in rows if row[1] is not None)
            return [(str(row[0]), "purged", "memory owner reset") for row in rows]

        self.runtime.govern_authoritative(space, purge, actor_id=ACTOR)
        return len(records)

    @staticmethod
    def _record_of(observation: Observation) -> MemoryRecord:
        record = record_from_payload(dict(observation.payload))
        if record is None:  # pragma: no cover - written by this class
            raise ValueError(f"observation {observation.observation_id} holds no memory record")
        return record


def curated_planner(
    _space: MemorySpaceKey,
    window: tuple[Observation, ...],
    _existing: tuple[Any, ...],
) -> Any:
    """Derive plan for curated memory: the deterministic curated projector, no model."""
    from .convergence import DerivedPlanPayload
    from .curated_projection import curated_projector

    return DerivedPlanPayload(assertions=curated_projector(window), consolidator_version=CURATED_SCHEMA)


class MemoryV2CuratedConvergence:
    """Drive the durable derive and projection jobs for curated memory.

    Used inline after a write (one space, so the next prompt sees it) and by
    the scheduled sweep (every space, retrying failures with the worker's
    backoff). Both claim the same durable jobs, so they never double-apply.
    """

    def __init__(self, database: PostgresDatabase | None = None, *, worker: Any = None) -> None:
        from .operations import PostgresMemoryV2ConvergenceWorker

        self.database = database or default_database()
        self.worker = worker or PostgresMemoryV2ConvergenceWorker(self.database)

    def run(self, *, space: MemorySpaceKey | None = None, max_steps: int = 200) -> int:
        """Run claimed jobs until none is due (or ``max_steps``). Returns steps run."""
        steps = 0
        while steps < max_steps:
            derived = self.worker.derive_once(curated_planner, space=space)
            projected = self.worker.project_once(space=space)
            if not derived and not projected:
                break
            steps += int(derived) + int(projected)
        return steps


__all__ = [
    "CURATED_EVENT",
    "MemoryV2CuratedConvergence",
    "curated_planner",
    "LEGACY_EVENT",
    "PostgresMemoryV2CuratedRecords",
    "curated_observation_id",
    "curated_request",
    "memory_space",
    "record_from_payload",
    "tenant_principal",
]
