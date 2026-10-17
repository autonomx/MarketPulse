from __future__ import annotations

from typing import Any, Mapping

from .rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


_COLUMNS = """
workspace_id, description_key, item_key, item_name, genre, context_hash,
summary, source, metadata, created_at, updated_at
"""


def _row(value: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(value[0]),
        "description_key": str(value[1]),
        "item_key": str(value[2]),
        "item_name": str(value[3]),
        "genre": str(value[4]),
        "context_hash": str(value[5]),
        "summary": str(value[6]),
        "source": str(value[7]),
        "metadata": dict(value[8]),
        "created_at": value[9].isoformat(),
        "updated_at": value[10].isoformat(),
    }


class PostgresRpgItemDescriptionRepository:
    """PostgreSQL cache for presentation-only inventory item descriptions."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get(
        self,
        context: TenantContext,
        description_key: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM omnix_rpg_item_descriptions "
            "WHERE workspace_id = %s AND description_key = %s",
            (context.workspace_id, description_key),
        ).fetchone()
        return _row(row) if row is not None else None

    def put(
        self,
        context: TenantContext,
        *,
        description_key: str,
        item_key: str,
        item_name: str,
        genre: str,
        context_hash: str,
        summary: str,
        source: str = "llm",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_item_descriptions (
                workspace_id, description_key, item_key, item_name, genre,
                context_hash, summary, source, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, description_key) DO UPDATE
               SET item_key = EXCLUDED.item_key,
                   item_name = EXCLUDED.item_name,
                   genre = EXCLUDED.genre,
                   context_hash = EXCLUDED.context_hash,
                   summary = EXCLUDED.summary,
                   source = EXCLUDED.source,
                   metadata = EXCLUDED.metadata,
                   updated_at = CURRENT_TIMESTAMP
            RETURNING {_COLUMNS}
            """,
            (
                context.workspace_id,
                description_key,
                item_key,
                item_name,
                genre,
                context_hash,
                summary,
                source,
                canonical_json(dict(metadata or {})),
            ),
        ).fetchone()
        if row is None:  # pragma: no cover - PostgreSQL RETURNING contract
            raise RuntimeError("item description write returned no row")
        return _row(row)
