"""Tenant-scoped PostgreSQL document records for compatibility surfaces."""

from __future__ import annotations

import builtins
import json
import sys
import threading
from collections.abc import Callable
from typing import Any

from .database import PostgresDatabase, default_database
from .document_schemas import document_matches, validate_document
from .errors import RevisionConflict
from app.runtime.tenant_context import RequestTenant


class DocumentRevisionConflict(RevisionConflict):
    """The document changed since the revision the caller read (HTTP 409)."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PostgresDocumentStore:
    """Small-document facade over ``omnix_module_records``.

    This is for existing feature contracts whose behavior is already expressed
    as read/modify/write over a bounded JSON document. New high-volume or
    independently queried domains should receive dedicated relational tables.
    """
    context = RequestTenant()

    def __init__(self, database: PostgresDatabase | None = None, *, context=None) -> None:
        self.database = database or default_database()
        self.context = context

    def read(
        self,
        *,
        module: str,
        record_type: str,
        record_id: str = "default",
        default: Any = None,
    ) -> Any:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT payload FROM omnix_module_records
                 WHERE workspace_id = %s AND module = %s AND record_type = %s
                   AND record_id = %s AND status = 'active'
                """,
                (self.context.workspace_id, module, record_type, record_id),
            ).fetchone()
        if row is None:
            return default
        value = row[0]
        document_matches(module, record_type, value, record_id=record_id)
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return list(value)
        return value

    def lock(self, *, module: str, record_type: str, record_id: str = "default") -> "DocumentLock":
        """A cross-process lock for one document's read-modify-write."""
        return DocumentLock(self, module=module, record_type=record_type, record_id=record_id)

    def read_versioned(
        self,
        *,
        module: str,
        record_type: str,
        record_id: str = "default",
        default: Any = None,
    ) -> tuple[Any, int]:
        """``(payload, revision)``; revision 0 when the document does not exist."""
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT payload, revision FROM omnix_module_records
                 WHERE workspace_id = %s AND module = %s AND record_type = %s
                   AND record_id = %s AND status = 'active'
                """,
                (self.context.workspace_id, module, record_type, record_id),
            ).fetchone()
        if row is None:
            return default, 0
        value = row[0]
        document_matches(module, record_type, value, record_id=record_id)
        if isinstance(value, dict):
            value = dict(value)
        elif isinstance(value, list):
            value = list(value)
        return value, int(row[1])

    def update(
        self,
        mutate: Callable[[Any], Any],
        *,
        module: str,
        record_type: str,
        record_id: str = "default",
        default: Any = None,
        attempts: int = 5,
    ) -> Any:
        """Read-modify-write without lost updates (WP-5.9).

        ``mutate`` receives the current payload (``default`` when missing) and
        returns the new one. A concurrent write makes the conditional write
        fail; the document is read again and ``mutate`` re-applied.
        """
        for _ in range(max(1, attempts)):
            current, revision = self.read_versioned(
                module=module, record_type=record_type, record_id=record_id, default=default,
            )
            updated = mutate(current)
            try:
                self.write(
                    updated, module=module, record_type=record_type, record_id=record_id,
                    expected_revision=revision,
                )
                return updated
            except DocumentRevisionConflict:
                continue
        raise DocumentRevisionConflict(f"{module}/{record_type}/{record_id} kept changing")

    def write(
        self,
        payload: Any,
        *,
        module: str,
        record_type: str,
        record_id: str = "default",
        status: str = "active",
        expires_at: str | None = None,
        expected_revision: int | None = None,
    ) -> int:
        """Store ``payload``; with ``expected_revision`` only if unchanged since then.

        ``expected_revision=0`` creates the document only if it does not exist.
        An active document must match its kind's registered shape.
        """
        if status == "active":
            validate_document(module, record_type, payload)
        if expected_revision is not None:
            return self._write_if_revision(
                payload, module=module, record_type=record_type, record_id=record_id,
                status=status, expires_at=expires_at, expected_revision=int(expected_revision),
            )
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                INSERT INTO omnix_module_records (
                    workspace_id, module, record_type, record_id, owner_user_id,
                    payload, status, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::timestamptz)
                ON CONFLICT (workspace_id, module, record_type, record_id)
                DO UPDATE SET payload = EXCLUDED.payload,
                              status = EXCLUDED.status,
                              expires_at = EXCLUDED.expires_at,
                              revision = omnix_module_records.revision + 1,
                              updated_at = CURRENT_TIMESTAMP
                RETURNING revision
                """,
                (
                    self.context.workspace_id,
                    module,
                    record_type,
                    record_id,
                    self.context.user_id,
                    _json(payload),
                    status,
                    expires_at,
                ),
            ).fetchone()
        return int(row[0])

    def _write_if_revision(
        self,
        payload: Any,
        *,
        module: str,
        record_type: str,
        record_id: str,
        status: str,
        expires_at: str | None,
        expected_revision: int,
    ) -> int:
        with self.database.transaction() as connection:
            if expected_revision == 0:
                row = connection.execute(
                    """
                    INSERT INTO omnix_module_records (
                        workspace_id, module, record_type, record_id, owner_user_id,
                        payload, status, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::timestamptz)
                    ON CONFLICT (workspace_id, module, record_type, record_id) DO NOTHING
                    RETURNING revision
                    """,
                    (
                        self.context.workspace_id, module, record_type, record_id,
                        self.context.user_id, _json(payload), status, expires_at,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    UPDATE omnix_module_records
                       SET payload = %s::jsonb, status = %s, expires_at = %s::timestamptz,
                           revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND module = %s AND record_type = %s
                       AND record_id = %s AND revision = %s
                    RETURNING revision
                    """,
                    (
                        _json(payload), status, expires_at, self.context.workspace_id,
                        module, record_type, record_id, expected_revision,
                    ),
                ).fetchone()
        if row is None:
            raise DocumentRevisionConflict(
                f"{module}/{record_type}/{record_id} is no longer at revision {expected_revision}"
            )
        return int(row[0])

    def list(
        self,
        *,
        module: str,
        record_type: str,
        limit: int = 500,
    ) -> builtins.list[tuple[str, Any, int]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT record_id, payload, revision
                  FROM omnix_module_records
                 WHERE workspace_id = %s AND module = %s AND record_type = %s
                   AND status = 'active'
                   AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                 ORDER BY updated_at DESC, record_id ASC LIMIT %s
                """,
                (
                    self.context.workspace_id,
                    module,
                    record_type,
                    max(1, min(int(limit), 5000)),
                ),
            ).fetchall()
        return [
            (
                str(row[0]),
                dict(row[1]) if isinstance(row[1], dict) else list(row[1]) if isinstance(row[1], list) else row[1],
                int(row[2]),
            )
            for row in rows
        ]

    def list_for_session(
        self,
        *,
        module: str,
        record_type: str,
        session_id: str,
        limit: int = 1000,
    ) -> builtins.list[tuple[str, Any, int]]:
        """Records whose payload ``session_id`` matches, newest first (WP-5.7).

        Chat conversation summaries have a partial index on this lookup
        (migration 0111); other record types fall back to the module index.
        """
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT record_id, payload, revision
                  FROM omnix_module_records
                 WHERE workspace_id = %s AND module = %s AND record_type = %s
                   AND payload->>'session_id' = %s
                   AND status = 'active'
                   AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                 ORDER BY updated_at DESC, record_id ASC LIMIT %s
                """,
                (self.context.workspace_id, module, record_type, session_id, max(1, min(int(limit), 5000))),
            ).fetchall()
        return [(str(row[0]), row[1], int(row[2])) for row in rows]

    def delete(
        self,
        *,
        module: str,
        record_type: str,
        record_id: str,
    ) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM omnix_module_records
                 WHERE workspace_id = %s AND module = %s AND record_type = %s
                   AND record_id = %s
                """,
                (self.context.workspace_id, module, record_type, record_id),
            )
        return cursor.rowcount == 1

    def clear(self, *, module: str, record_type: str) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM omnix_module_records
                 WHERE workspace_id = %s AND module = %s AND record_type = %s
                """,
                (self.context.workspace_id, module, record_type),
            )
        return int(cursor.rowcount)


class DocumentLock:
    """Serialize one document's read-modify-write across processes (WP-5.9).

    Stores that guard ``_read``/``_write`` sequences with ``with self._lock``
    use this in place of a ``threading.RLock``. Entering opens a transaction
    holding an advisory lock on the document, so other processes wait instead
    of overwriting the change. Nested entries in the same thread reuse it.
    """

    def __init__(
        self,
        store: PostgresDocumentStore,
        *,
        module: str,
        record_type: str,
        record_id: str = "default",
    ) -> None:
        self.store = store
        self.module = module
        self.record_type = record_type
        self.record_id = record_id
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self) -> "DocumentLock":
        self._thread_lock.acquire()
        try:
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                transaction = self.store.database.transaction()
                connection = transaction.__enter__()
                try:
                    key = f"{self.store.context.workspace_id}:{self.module}:{self.record_type}:{self.record_id}"
                    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
                except BaseException:
                    transaction.__exit__(*sys.exc_info())
                    raise
                self._local.transaction = transaction
                self._local.connection = connection
            self._local.depth = depth + 1
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, *exc_info: Any) -> None:
        try:
            self._local.depth -= 1
            if self._local.depth == 0:
                transaction = self._local.transaction
                self._local.transaction = self._local.connection = None
                transaction.__exit__(*exc_info)
        finally:
            self._thread_lock.release()

    @property
    def held(self) -> bool:
        return getattr(self._local, "depth", 0) > 0

    def read(self, *, default: Any = None) -> Any:
        """The document, read on the locked connection."""
        row = self._local.connection.execute(
            """
            SELECT payload FROM omnix_module_records
             WHERE workspace_id = %s AND module = %s AND record_type = %s
               AND record_id = %s AND status = 'active'
            """,
            (self.store.context.workspace_id, self.module, self.record_type, self.record_id),
        ).fetchone()
        if row is None:
            return default
        value = row[0]
        document_matches(self.module, self.record_type, value, record_id=self.record_id)
        return dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value

    def write(self, payload: Any) -> None:
        """Store the document on the locked connection; committed on exit."""
        validate_document(self.module, self.record_type, payload)
        context = self.store.context
        self._local.connection.execute(
            """
            INSERT INTO omnix_module_records (
                workspace_id, module, record_type, record_id, owner_user_id, payload
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, module, record_type, record_id)
            DO UPDATE SET payload = EXCLUDED.payload, status = 'active', expires_at = NULL,
                          revision = omnix_module_records.revision + 1,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (context.workspace_id, self.module, self.record_type, self.record_id, context.user_id, _json(payload)),
        )

