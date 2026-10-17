from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
import builtins
from typing import Any, Protocol

from app.persistence.errors import RevisionConflict
from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import PostgresUnitOfWork, unit_of_work
from app.persistence.document_schemas import register_document_schema
from app.runtime.pagination import MAX_PAGE_SIZE


TRADING_MODULE = "trading"
# Script versions kept per script (TVP-11.3); older ones are dropped as new ones are saved.
SCRIPT_VERSIONS_KEPT = 200
SUPPORTED_DOCUMENT_TYPES = frozenset(
    {"workspace", "watchlist", "watchlist_flag_set", "drawing", "indicator_preset", "script"}
)


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[PostgresUnitOfWork]: ...


def _record(row: Any) -> dict[str, Any]:
    return {
        "module": str(row[0]),
        "record_type": str(row[1]),
        "record_id": str(row[2]),
        "owner_user_id": str(row[3]) if row[3] is not None else None,
        "payload": dict(row[4]),
        "status": str(row[5]),
        "revision": int(row[6]),
        "expires_at": row[7].isoformat() if row[7] is not None else None,
        "created_at": row[8].isoformat(),
        "updated_at": row[9].isoformat(),
    }


class TradingDocumentRepository:
    """Revisioned Trading documents backed by Omnix PostgreSQL module records."""
    context = RequestTenant()

    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory: UnitOfWorkFactory = unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    @staticmethod
    def _require_type(record_type: str) -> str:
        clean = str(record_type).strip()
        if clean not in SUPPORTED_DOCUMENT_TYPES:
            raise ValueError(f"unsupported Trading document type: {clean}")
        return clean

    def get(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        clean_type = self._require_type(record_type)
        with self.uow_factory() as uow:
            return uow.module_records.get(
                self.context,
                module=TRADING_MODULE,
                record_type=clean_type,
                record_id=record_id,
            )

    def list(
        self,
        record_type: str,
        *,
        limit: int = 100,
        after: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Most recently updated first; ``after`` is the last ``(updated_at, record_id)`` of the previous page."""
        clean_type = self._require_type(record_type)
        with self.uow_factory() as uow:
            return uow.module_records.list(
                self.context,
                module=TRADING_MODULE,
                record_type=clean_type,
                limit=limit,
                after=after,
            )

    def iter(self, record_type: str) -> Iterator[dict[str, Any]]:
        """Every record of ``record_type``, one page at a time (WP-5.5)."""
        clean_type = self._require_type(record_type)
        after: tuple[str, str] | None = None
        while True:
            with self.uow_factory() as uow:
                page = uow.module_records.list(
                    self.context,
                    module=TRADING_MODULE,
                    record_type=clean_type,
                    limit=MAX_PAGE_SIZE,
                    after=after,
                )
            yield from page
            if len(page) < MAX_PAGE_SIZE:
                return
            after = (page[-1]["updated_at"], page[-1]["record_id"])

    def create(self, record_type: str, record_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean_type = self._require_type(record_type)
        with self.uow_factory() as uow:
            record = uow.module_records.put(
                self.context,
                module=TRADING_MODULE,
                record_type=clean_type,
                record_id=record_id,
                payload=payload,
            )
            if clean_type == "script":
                self._record_script_version(uow.connection, record)
            uow.commit()
            return record

    def update(
        self,
        record_type: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        clean_type = self._require_type(record_type)
        with self.uow_factory() as uow:
            record = uow.module_records.put(
                self.context,
                module=TRADING_MODULE,
                record_type=clean_type,
                record_id=record_id,
                payload=payload,
                expected_revision=expected_revision,
            )
            if clean_type == "script":
                self._record_script_version(uow.connection, record)
            uow.commit()
            return record

    def _record_script_version(self, connection: Any, record: dict[str, Any]) -> None:
        """A script saved with a new source or name gets a version, in the save's transaction (TVP-11.3)."""
        payload = record["payload"]
        source = payload.get("source")
        if not isinstance(source, str):
            return
        name = str(payload.get("name") or "")
        workspace_id = self.context.workspace_id
        latest = connection.execute(
            """
            SELECT name, source FROM omnix_trading_script_versions
             WHERE workspace_id = %s AND script_id = %s
             ORDER BY revision DESC LIMIT 1
            """,
            (workspace_id, record["record_id"]),
        ).fetchone()
        if latest is not None and latest[0] == name and latest[1] == source:
            return
        connection.execute(
            """
            INSERT INTO omnix_trading_script_versions (workspace_id, script_id, revision, name, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, script_id, revision) DO UPDATE SET name = EXCLUDED.name, source = EXCLUDED.source
            """,
            (workspace_id, record["record_id"], record["revision"], name, source),
        )
        connection.execute(
            """
            DELETE FROM omnix_trading_script_versions
             WHERE workspace_id = %s AND script_id = %s AND revision <= (
                   SELECT revision FROM omnix_trading_script_versions
                    WHERE workspace_id = %s AND script_id = %s
                    ORDER BY revision DESC OFFSET %s LIMIT 1)
            """,
            (workspace_id, record["record_id"], workspace_id, record["record_id"], SCRIPT_VERSIONS_KEPT),
        )

    # The class's own list() method shadows the builtin in annotations here.
    def script_versions(self, script_id: str) -> builtins.list[dict[str, Any]]:
        """A script's versions, newest first, without their source."""
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT revision, name, saved_at, length(source), array_length(string_to_array(source, E'\\n'), 1)
                  FROM omnix_trading_script_versions
                 WHERE workspace_id = %s AND script_id = %s
                 ORDER BY revision DESC
                """,
                (self.context.workspace_id, script_id),
            ).fetchall()
        return [
            {"revision": int(row[0]), "name": str(row[1]), "saved_at": row[2].isoformat(), "characters": int(row[3] or 0), "lines": int(row[4] or 0)}
            for row in rows
        ]

    def script_version_at(self, script_id: str, revision: int) -> dict[str, Any] | None:
        """The script as it was at document revision ``revision``: the newest version saved at or before it."""
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT revision FROM omnix_trading_script_versions
                 WHERE workspace_id = %s AND script_id = %s AND revision <= %s
                 ORDER BY revision DESC LIMIT 1
                """,
                (self.context.workspace_id, script_id, revision),
            ).fetchone()
        return None if row is None else self.script_version(script_id, int(row[0]))

    def script_version(self, script_id: str, revision: int) -> dict[str, Any] | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT revision, name, saved_at, source FROM omnix_trading_script_versions
                 WHERE workspace_id = %s AND script_id = %s AND revision = %s
                """,
                (self.context.workspace_id, script_id, revision),
            ).fetchone()
        if row is None:
            return None
        return {"revision": int(row[0]), "name": str(row[1]), "saved_at": row[2].isoformat(), "source": str(row[3])}

    def archive(
        self,
        record_type: str,
        record_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        clean_type = self._require_type(record_type)
        with self.uow_factory() as uow:
            record = uow.module_records.archive(
                self.context, module=TRADING_MODULE, record_type=clean_type, record_id=record_id,
                expected_revision=expected_revision,
            )
            if record is None:
                raise RevisionConflict(
                    f"Trading document expected revision {expected_revision}: {clean_type}/{record_id}"
                )
            uow.commit()
            return record


RepositoryFactory = Callable[[], TradingDocumentRepository]


def default_trading_repository() -> TradingDocumentRepository:
    return TradingDocumentRepository()


# Document shapes (WP-5.9): the terminal's documents are client-owned JSON
# objects (layouts, watchlists, watchlist colour flags, drawings, indicator
# presets).
for _record_type in SUPPORTED_DOCUMENT_TYPES:
    register_document_schema(TRADING_MODULE, _record_type, dict[str, Any])
