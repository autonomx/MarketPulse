"""Prompt templates in PostgreSQL (moved from kernel persistence, PA-2.2)."""
from __future__ import annotations

import json
from typing import Any

from app.persistence.errors import RevisionConflict
from app.persistence.tenant import TenantContext


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PostgresPromptRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create(
        self,
        context: TenantContext,
        *,
        prompt_id: str,
        name: str,
        template_type: str,
        content: str,
        variables: list[str] | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_prompt_templates (
                id, workspace_id, owner_user_id, name, template_type,
                content, variables
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id, name, template_type, content, variables, status,
                      revision, created_at, updated_at
            """,
            (
                prompt_id,
                context.workspace_id,
                context.user_id,
                name,
                template_type,
                content,
                _json(variables or []),
            ),
        ).fetchone()
        return self._record(row)

    def get(self, context: TenantContext, prompt_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT id, name, template_type, content, variables, status,
                   revision, created_at, updated_at
              FROM omnix_prompt_templates
             WHERE workspace_id = %s AND id = %s
            """,
            (context.workspace_id, prompt_id),
        ).fetchone()
        return self._record(row) if row is not None else None

    def update(
        self,
        context: TenantContext,
        *,
        prompt_id: str,
        content: str,
        variables: list[str],
        expected_revision: int,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            UPDATE omnix_prompt_templates
               SET content = %s, variables = %s::jsonb,
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND id = %s AND revision = %s
            RETURNING id, name, template_type, content, variables, status,
                      revision, created_at, updated_at
            """,
            (
                content,
                _json(variables),
                context.workspace_id,
                prompt_id,
                expected_revision,
            ),
        ).fetchone()
        if row is None:
            raise RevisionConflict(
                f"prompt {prompt_id} expected revision {expected_revision}"
            )
        return self._record(row)

    @staticmethod
    def _record(row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "name": str(row[1]),
            "template_type": str(row[2]),
            "content": str(row[3]),
            "variables": list(row[4]),
            "status": str(row[5]),
            "revision": int(row[6]),
            "created_at": row[7].isoformat(),
            "updated_at": row[8].isoformat(),
        }
