from __future__ import annotations

from typing import Any, Mapping

from app.persistence.errors import EntityNotFound, RevisionConflict
from .rpg_repository import canonical_json
from app.persistence.tenant import TenantContext
from app.runtime.pagination import page_limit


class RpgWorldRevisionConflict(RevisionConflict):
    pass


def _world_row(row: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(row[0]),
        "id": str(row[1]),
        "title": str(row[2]),
        "description": str(row[3]),
        "status": str(row[4]),
        "source_mode": str(row[5]),
        "genre": str(row[6]),
        "tone": str(row[7]),
        "seed": int(row[8]),
        "draft_revision": int(row[9]),
        "metadata": dict(row[10]),
        "created_at": row[11].isoformat(),
        "updated_at": row[12].isoformat(),
    }


def _revision_row(row: Any, *, kind: str) -> dict[str, Any]:
    if kind == "world":
        return {
            "workspace_id": str(row[0]),
            "world_id": str(row[1]),
            "revision": int(row[2]),
            "document": dict(row[3]),
            "content_hash": str(row[4]),
            "created_at": row[5].isoformat(),
        }
    if kind == "release":
        return {
            "workspace_id": str(row[0]),
            "world_id": str(row[1]),
            "world_revision": int(row[2]),
            "release": int(row[3]),
            "document": dict(row[4]),
            "release_hash": str(row[5]),
            "created_at": row[6].isoformat(),
        }
    return {
        "workspace_id": str(row[0]),
        "scenario_id": str(row[1]),
        "revision": int(row[2]),
        "world_id": str(row[3]),
        "world_revision": int(row[4]),
        "document": dict(row[5]),
        "content_hash": str(row[6]),
        "created_at": row[7].isoformat(),
    }


class PostgresRpgWorldScenarioRepository:
    """PostgreSQL authority for reusable RPG worlds, releases, and scenarios."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_world(
        self,
        context: TenantContext,
        *,
        world_id: str,
        title: str,
        description: str = "",
        source_mode: str = "manual",
        genre: str = "classic_fantasy",
        tone: str = "heroic adventure",
        seed: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_worlds (
                workspace_id, id, title, description, source_mode,
                genre, tone, seed, metadata_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING workspace_id, id, title, description, status, source_mode,
                      genre, tone, seed, draft_revision, metadata_jsonb,
                      created_at, updated_at
            """,
            (
                context.workspace_id,
                world_id,
                title,
                description,
                source_mode,
                genre,
                tone,
                int(seed),
                canonical_json(dict(metadata or {})),
            ),
        ).fetchone()
        return _world_row(row)

    def get_world(
        self,
        context: TenantContext,
        world_id: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.connection.execute(
            "SELECT workspace_id, id, title, description, status, source_mode, "
            "genre, tone, seed, draft_revision, metadata_jsonb, created_at, updated_at "
            "FROM omnix_rpg_worlds WHERE workspace_id = %s AND id = %s" + suffix,
            (context.workspace_id, world_id),
        ).fetchone()
        return _world_row(row) if row is not None else None

    def list_worlds(
        self,
        context: TenantContext,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT workspace_id, id, title, description, status, source_mode, "
            "genre, tone, seed, draft_revision, metadata_jsonb, created_at, updated_at "
            "FROM omnix_rpg_worlds WHERE workspace_id = %s "
            "ORDER BY updated_at DESC, id LIMIT %s",
            (context.workspace_id, page_limit(limit, default=100)),
        ).fetchall()
        return [_world_row(row) for row in rows]

    def put_topic(
        self,
        context: TenantContext,
        *,
        world_id: str,
        topic_id: str,
        draft_revision: int,
        source: str,
        status: str,
        content: Mapping[str, Any],
        directives: Mapping[str, Any] | None = None,
        dependency_hashes: Mapping[str, str] | None = None,
        input_hash: str = "",
        content_hash: str = "",
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.get_world(context, world_id) is None:
            raise EntityNotFound(world_id)
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_world_topics (
                workspace_id, world_id, topic_id, draft_revision, source, status,
                content_jsonb, directives_jsonb, dependency_hashes_jsonb,
                input_hash, content_hash, provenance_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                      %s::jsonb, %s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, world_id, topic_id) DO UPDATE
               SET draft_revision = EXCLUDED.draft_revision,
                   source = EXCLUDED.source,
                   status = EXCLUDED.status,
                   content_jsonb = EXCLUDED.content_jsonb,
                   directives_jsonb = EXCLUDED.directives_jsonb,
                   dependency_hashes_jsonb = EXCLUDED.dependency_hashes_jsonb,
                   input_hash = EXCLUDED.input_hash,
                   content_hash = EXCLUDED.content_hash,
                   provenance_jsonb = EXCLUDED.provenance_jsonb,
                   updated_at = CURRENT_TIMESTAMP
            RETURNING topic_id, draft_revision, source, status, content_jsonb,
                      directives_jsonb, dependency_hashes_jsonb, input_hash,
                      content_hash, provenance_jsonb, updated_at
            """,
            (
                context.workspace_id,
                world_id,
                topic_id,
                int(draft_revision),
                source,
                status,
                canonical_json(dict(content)),
                canonical_json(dict(directives or {})),
                canonical_json(dict(dependency_hashes or {})),
                input_hash,
                content_hash,
                canonical_json(dict(provenance or {})),
            ),
        ).fetchone()
        return {
            "topic_id": str(row[0]),
            "draft_revision": int(row[1]),
            "source": str(row[2]),
            "status": str(row[3]),
            "content": dict(row[4]),
            "directives": dict(row[5]),
            "dependency_hashes": dict(row[6]),
            "input_hash": str(row[7]),
            "content_hash": str(row[8]),
            "provenance": dict(row[9]),
            "updated_at": row[10].isoformat(),
        }

    def publish_world_revision(
        self,
        context: TenantContext,
        *,
        world_id: str,
        document: Mapping[str, Any],
        content_hash: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        world = self.get_world(context, world_id, for_update=True)
        if world is None:
            raise EntityNotFound(world_id)
        current = self.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s",
            (context.workspace_id, world_id),
        ).fetchone()
        current_revision = int(current[0])
        if current_revision != int(expected_revision):
            raise RpgWorldRevisionConflict(
                f"world {world_id} expected revision {expected_revision}; current {current_revision}"
            )
        next_revision = current_revision + 1
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_world_revisions (
                workspace_id, world_id, revision, document_jsonb, content_hash
            ) VALUES (%s, %s, %s, %s::jsonb, %s)
            RETURNING workspace_id, world_id, revision, document_jsonb,
                      content_hash, created_at
            """,
            (
                context.workspace_id,
                world_id,
                next_revision,
                canonical_json(dict(document)),
                content_hash,
            ),
        ).fetchone()
        self.connection.execute(
            "UPDATE omnix_rpg_worlds SET status = 'published', "
            "draft_revision = draft_revision + 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, world_id),
        )
        return _revision_row(row, kind="world")

    def get_world_revision(
        self,
        context: TenantContext,
        world_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT workspace_id, world_id, revision, document_jsonb, "
            "content_hash, created_at FROM omnix_rpg_world_revisions "
            "WHERE workspace_id = %s AND world_id = %s AND revision = %s",
            (context.workspace_id, world_id, int(revision)),
        ).fetchone()
        return _revision_row(row, kind="world") if row is not None else None

    def publish_world_release(
        self,
        context: TenantContext,
        *,
        world_id: str,
        world_revision: int,
        document: Mapping[str, Any],
        release_hash: str,
    ) -> dict[str, Any]:
        if self.get_world_revision(context, world_id, world_revision) is None:
            raise EntityNotFound(f"{world_id}:{world_revision}")
        current = self.connection.execute(
            "SELECT COALESCE(MAX(release), 0) FROM omnix_rpg_world_releases "
            "WHERE workspace_id = %s AND world_id = %s AND world_revision = %s",
            (context.workspace_id, world_id, int(world_revision)),
        ).fetchone()
        next_release = int(current[0]) + 1
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_world_releases (
                workspace_id, world_id, world_revision, release,
                document_jsonb, release_hash
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            RETURNING workspace_id, world_id, world_revision, release,
                      document_jsonb, release_hash, created_at
            """,
            (
                context.workspace_id,
                world_id,
                int(world_revision),
                next_release,
                canonical_json(dict(document)),
                release_hash,
            ),
        ).fetchone()
        return _revision_row(row, kind="release")

    def get_world_release(
        self,
        context: TenantContext,
        world_id: str,
        world_revision: int,
        release: int,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT workspace_id, world_id, world_revision, release, "
            "document_jsonb, release_hash, created_at "
            "FROM omnix_rpg_world_releases WHERE workspace_id = %s "
            "AND world_id = %s AND world_revision = %s AND release = %s",
            (context.workspace_id, world_id, int(world_revision), int(release)),
        ).fetchone()
        return _revision_row(row, kind="release") if row is not None else None

    def create_scenario(
        self,
        context: TenantContext,
        *,
        scenario_id: str,
        world_id: str,
        title: str,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.get_world(context, world_id) is None:
            raise EntityNotFound(world_id)
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_scenarios (
                workspace_id, id, world_id, title, description, metadata_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING workspace_id, id, world_id, title, description,
                      status, metadata_jsonb, created_at, updated_at
            """,
            (
                context.workspace_id,
                scenario_id,
                world_id,
                title,
                description,
                canonical_json(dict(metadata or {})),
            ),
        ).fetchone()
        return {
            "workspace_id": str(row[0]),
            "id": str(row[1]),
            "world_id": str(row[2]),
            "title": str(row[3]),
            "description": str(row[4]),
            "status": str(row[5]),
            "metadata": dict(row[6]),
            "created_at": row[7].isoformat(),
            "updated_at": row[8].isoformat(),
        }

    def publish_scenario_revision(
        self,
        context: TenantContext,
        *,
        scenario_id: str,
        world_id: str,
        world_revision: int,
        document: Mapping[str, Any],
        content_hash: str,
    ) -> dict[str, Any]:
        current = self.connection.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM omnix_rpg_scenario_revisions "
            "WHERE workspace_id = %s AND scenario_id = %s",
            (context.workspace_id, scenario_id),
        ).fetchone()
        next_revision = int(current[0]) + 1
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_scenario_revisions (
                workspace_id, scenario_id, revision, world_id,
                world_revision, document_jsonb, content_hash
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING workspace_id, scenario_id, revision, world_id,
                      world_revision, document_jsonb, content_hash, created_at
            """,
            (
                context.workspace_id,
                scenario_id,
                next_revision,
                world_id,
                int(world_revision),
                canonical_json(dict(document)),
                content_hash,
            ),
        ).fetchone()
        self.connection.execute(
            "UPDATE omnix_rpg_scenarios SET status = 'published', "
            "updated_at = CURRENT_TIMESTAMP WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, scenario_id),
        )
        return _revision_row(row, kind="scenario")

    def get_scenario_revision(
        self,
        context: TenantContext,
        scenario_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT workspace_id, scenario_id, revision, world_id, "
            "world_revision, document_jsonb, content_hash, created_at "
            "FROM omnix_rpg_scenario_revisions WHERE workspace_id = %s "
            "AND scenario_id = %s AND revision = %s",
            (context.workspace_id, scenario_id, int(revision)),
        ).fetchone()
        return _revision_row(row, kind="scenario") if row is not None else None

    def bind_campaign(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        world_id: str,
        world_revision: int,
        world_release: int,
        scenario_id: str,
        scenario_revision: int,
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_campaign_world_bindings (
                workspace_id, campaign_id, world_id, world_revision,
                world_release, scenario_id, scenario_revision, binding_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (workspace_id, campaign_id) DO NOTHING
            RETURNING binding_jsonb, created_at
            """,
            (
                context.workspace_id,
                campaign_id,
                world_id,
                int(world_revision),
                int(world_release),
                scenario_id,
                int(scenario_revision),
                canonical_json(dict(binding)),
            ),
        ).fetchone()
        if row is None:
            existing = self.get_campaign_binding(context, campaign_id)
            if existing != dict(binding):
                raise RpgWorldRevisionConflict(
                    f"campaign world binding already exists: {campaign_id}"
                )
            return existing
        return dict(row[0])

    def get_campaign_binding(
        self,
        context: TenantContext,
        campaign_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT binding_jsonb FROM omnix_rpg_campaign_world_bindings "
            "WHERE workspace_id = %s AND campaign_id = %s",
            (context.workspace_id, campaign_id),
        ).fetchone()
        return dict(row[0]) if row is not None else None
