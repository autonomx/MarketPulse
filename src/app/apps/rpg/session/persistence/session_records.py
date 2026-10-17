from __future__ import annotations

import hashlib
from typing import Any

from app.persistence.database import PostgresDatabase, default_database
from app.persistence.errors import RevisionConflict
from app.security.tenant_context import current_tenant
from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json, state_hash
from app.apps.rpg.foundation.persistence.rpg_session_save_policy import session_save_deferred
from app.persistence.unit_of_work import unit_of_work
from app.persistence.repository_registry import register_repository_specs
from app.runtime.pagination import MAX_PAGE_SIZE


def _database() -> PostgresDatabase:
    return default_database()


def _context(database: PostgresDatabase):
    del database
    from app.apps.rpg.session.persistence.feature_repositories import RPG_REPOSITORY_SPECS

    register_repository_specs(RPG_REPOSITORY_SPECS)
    return current_tenant()


def _campaign_id(session: dict[str, Any]) -> str:
    manifest = raw_manifest if isinstance(raw_manifest := session.get("manifest"), dict) else {}
    value = str(manifest.get("session_id") or manifest.get("id") or "").strip()
    if not value:
        raise ValueError("RPG session manifest requires session_id")
    return value


def _revision(session: dict[str, Any]) -> int:
    runtime = raw_runtime_state if isinstance(raw_runtime_state := session.get("runtime_state"), dict) else {}
    manifest = raw_manifest if isinstance(raw_manifest := session.get("manifest"), dict) else {}
    return max(0, int(runtime.get("state_revision") or manifest.get("turn_count") or 0))


class TurnStateRewriteConflict(RevisionConflict):
    """A save inside a turn would rewrite the revision that turn committed."""


def _refuse_rewrite_inside_turn(campaign_id: str, revision: int) -> None:
    """A turn commits its state once (WP-5.6): within its execution, saving
    different content at the committed revision would make a revision with no
    turn behind it, so it is a conflict (HTTP 409) instead of a silent bump."""
    from app.jobs.foreground_execution import current_foreground_execution

    execution = current_foreground_execution()
    if execution is not None and execution.session_id == campaign_id:
        raise TurnStateRewriteConflict(
            f"RPG turn {execution.submission_id} attempted to rewrite revision {revision} "
            f"of {campaign_id} after committing it"
        )


def save_session_to_postgres(
    session: dict[str, Any],
    *,
    compact: bool = False,
) -> dict[str, Any]:
    del compact
    if session_save_deferred():
        return session
    database = _database()
    context = _context(database)
    campaign_id = _campaign_id(session)
    revision = _revision(session)
    digest = state_hash(session)
    manifest = raw_manifest if isinstance(raw_manifest := session.get("manifest"), dict) else {}
    engine_version = str(
        manifest.get("engine_version") or session.get("engine_version") or "rpg-engine"
    )
    schema_version = str(session.get("save_version") or "rpg-session-v1")
    seed = str(manifest.get("seed") or session.get("seed") or campaign_id)
    title = str(manifest.get("title") or campaign_id)

    with unit_of_work(database) as work:
        existing = work.rpg.get_campaign(context, campaign_id, for_update=True)
        if existing is None:
            work.connection.execute(
                """
                INSERT INTO omnix_rpg_campaigns (
                    id, workspace_id, owner_user_id, title, revision, state_jsonb,
                    state_hash, engine_version, schema_version, seed, status, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    campaign_id,
                    context.workspace_id,
                    context.user_id,
                    title,
                    revision,
                    canonical_json(session),
                    digest,
                    engine_version,
                    schema_version,
                    seed,
                    "archived" if manifest.get("archived") else "active",
                    canonical_json({"compatibility_save": True}),
                ),
            )
            work.connection.execute(
                """
                INSERT INTO omnix_rpg_participants
                    (campaign_id, user_id, role, permissions)
                VALUES (%s, %s, 'owner', ARRAY['read', 'write', 'admin'])
                ON CONFLICT DO NOTHING
                """,
                (campaign_id, context.user_id),
            )
        else:
            if revision < existing["revision"]:
                raise RevisionConflict(
                    f"RPG session {campaign_id} attempted to save stale revision {revision}; "
                    f"current {existing['revision']}"
                )
            if revision == existing["revision"] and digest == existing["state_hash"]:
                work.rollback()
                return existing["state"]
            if revision == existing["revision"] and digest != existing["state_hash"]:
                _refuse_rewrite_inside_turn(campaign_id, revision)
                # An edit outside a turn (rename, lore, settings): the session
                # was loaded at this revision, so the edit is the next one.
                revision = existing["revision"] + 1
                runtime = session.get("runtime_state")
                if not isinstance(runtime, dict):
                    runtime = {}
                    session["runtime_state"] = runtime
                runtime["state_revision"] = revision
                digest = state_hash(session)
            cursor = work.connection.execute(
                """
                UPDATE omnix_rpg_campaigns
                   SET title = %s, revision = %s, state_jsonb = %s::jsonb,
                       state_hash = %s, engine_version = %s, schema_version = %s,
                       seed = %s, status = %s, updated_at = CURRENT_TIMESTAMP,
                       metadata = metadata || %s::jsonb
                 WHERE id = %s AND workspace_id = %s AND revision = %s
                """,
                (
                    title,
                    revision,
                    canonical_json(session),
                    digest,
                    engine_version,
                    schema_version,
                    seed,
                    "archived" if manifest.get("archived") else "active",
                    canonical_json({"compatibility_save": True}),
                    campaign_id,
                    context.workspace_id,
                    existing["revision"],
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict(f"RPG session changed while saving: {campaign_id}")
        work.commit()
    return session


def load_session_version_from_postgres(session_id: str) -> tuple[int, str] | None:
    database = _database()
    context = _context(database)
    with unit_of_work(database) as work:
        version = work.rpg.stored_version(context, session_id)
        work.rollback()
    return version


def load_rng_seed_from_postgres(session_id: str) -> tuple[bool, Any]:
    database = _database()
    context = _context(database)
    with unit_of_work(database) as work:
        stored = work.rpg.stored_rng_seed(context, session_id)
        work.rollback()
    return stored


def load_session_from_postgres(session_id: str) -> dict[str, Any] | None:
    database = _database()
    context = _context(database)
    with unit_of_work(database) as work:
        campaign = work.rpg.get_campaign(context, session_id)
        work.rollback()
    if campaign is None:
        return None
    from app.apps.rpg.session.campaign_state import upcast_if_outdated

    # A campaign stored before the current schema loads upcast (WP-5.6).
    return upcast_if_outdated(dict(campaign["state"]))


def load_launch_gate_source_from_postgres(session_id: str) -> dict[str, Any] | None:
    """What the launch-readiness gate reads of a campaign; None when it does not exist."""
    database = _database()
    context = _context(database)
    with unit_of_work(database) as work:
        source = work.rpg.launch_gate_source(context, session_id)
        work.rollback()
    return source


def list_sessions_from_postgres() -> list[dict[str, Any]]:
    database = _database()
    context = _context(database)
    with unit_of_work(database) as work:
        campaigns = list(work.rpg.iter_campaigns(context, status="active"))
        archived = list(work.rpg.iter_campaigns(context, status="archived"))
        work.rollback()
    return [_campaign_state_for_listing(record) for record in campaigns + archived]


def _campaign_state_for_listing(record: dict[str, Any]) -> dict[str, Any]:
    """Project a stored campaign using the database row id as list identity.

    Imported or partially-created legacy states can carry the placeholder
    ``session:unknown`` in their manifest.  More than one such row is valid in
    PostgreSQL because the authoritative campaign ids are distinct, but exposing
    the placeholder makes the UI produce duplicate options and React keys.
    """

    state = dict(record["state"])
    manifest = (
        dict(state["manifest"])
        if isinstance(state.get("manifest"), dict)
        else {}
    )
    campaign_id = str(record["id"])
    manifest["id"] = campaign_id
    manifest["session_id"] = campaign_id
    state["manifest"] = manifest
    return state


def list_session_summaries_from_postgres(
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Project the authoritative campaign list into the bounded UI summary contract."""

    from app.apps.rpg.session.list_summaries import session_list_summary

    wanted = None if limit is None else max(0, int(limit))
    sources: list[dict[str, Any]] = []
    database = _database()
    context = _context(database)
    with unit_of_work(database) as work:
        # Active campaigns first, then archived, each newest first; only the rows the page shows.
        for status in ("active", "archived"):
            before: tuple[str, str] | None = None
            while wanted is None or len(sources) < wanted:
                size = MAX_PAGE_SIZE if wanted is None else min(MAX_PAGE_SIZE, wanted - len(sources))
                page = work.rpg.list_campaign_summary_sources(context, limit=size, status=status, before=before)
                sources.extend(page)
                if len(page) < size:
                    break
                before = (page[-1]["updated_at"], page[-1]["id"])
        work.rollback()
    summaries = []
    for row in sources:
        summary = session_list_summary(_campaign_state_for_listing({"id": row["id"], "state": row["source"]}))
        summary["manifest"].update(
            turn_count=row["turn_count"],
            player_level=row["player_level"],
            location_name=row["location_name"],
        )
        summaries.append(summary)
    return summaries


def archive_session_in_postgres(session_id: str) -> dict[str, Any]:
    database = _database()
    context = _context(database)
    with database.transaction() as connection:
        row = connection.execute(
            """
            UPDATE omnix_rpg_campaigns
               SET status = 'archived', revision = revision + 1,
                   state_jsonb = jsonb_set(
                       state_jsonb,
                       '{manifest,archived}',
                       'true'::jsonb,
                       TRUE
                   ),
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
            RETURNING state_jsonb
            """,
            (session_id, context.workspace_id),
        ).fetchone()
    return {"ok": row is not None, "session": dict(row[0]) if row is not None else None}


def append_interaction_event_postgres(
    session_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    database = _database()
    context = _context(database)
    interaction_id = str(event.get("interaction_id") or "").strip()
    if not interaction_id:
        canonical = canonical_json(event)
        interaction_id = f"interaction:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    sequence = int(event.get("sequence") or 0)
    revision = int(event.get("state_revision") or 0)
    with database.transaction() as connection:
        campaign = connection.execute(
            "SELECT id FROM omnix_rpg_campaigns WHERE id = %s AND workspace_id = %s",
            (session_id, context.workspace_id),
        ).fetchone()
        if campaign is None:
            raise KeyError(session_id)
        turn = connection.execute(
            "SELECT id FROM omnix_rpg_turns WHERE campaign_id = %s AND interaction_id = %s",
            (session_id, interaction_id),
        ).fetchone()
        if turn is None:
            turn_id = f"turn:compat:{interaction_id}"
            submission_id = str(event.get("submission_id") or turn_id)
            response = {
                "ok": True,
                "interaction_id": interaction_id,
                "compatibility_record": True,
            }
            campaign_row = connection.execute(
                "SELECT revision, state_hash, engine_version, schema_version "
                "FROM omnix_rpg_campaigns WHERE id = %s FOR UPDATE",
                (session_id,),
            ).fetchone()
            resulting_revision = max(revision, int(campaign_row[0]))
            sequence = max(sequence, resulting_revision, 1)
            connection.execute(
                """
                INSERT INTO omnix_rpg_turns (
                    id, workspace_id, campaign_id, sequence, submission_id,
                    expected_revision, resulting_revision, command_jsonb,
                    canonical_effects_jsonb, state_hash_before, state_hash_after,
                    engine_version, schema_version, interaction_id, compact_response
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, '{}'::jsonb,
                          %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (campaign_id, interaction_id) DO NOTHING
                """,
                (
                    turn_id,
                    context.workspace_id,
                    session_id,
                    sequence,
                    submission_id,
                    max(0, resulting_revision - 1),
                    max(1, resulting_revision),
                    campaign_row[1],
                    campaign_row[1],
                    campaign_row[2],
                    campaign_row[3],
                    interaction_id,
                    canonical_json(response),
                ),
            )
            turn = (turn_id,)
        connection.execute(
            """
            INSERT INTO omnix_rpg_interactions (
                interaction_id, workspace_id, campaign_id, turn_id, sequence,
                state_revision, event_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (interaction_id) DO UPDATE SET
                event_jsonb = EXCLUDED.event_jsonb,
                state_revision = EXCLUDED.state_revision
            """,
            (
                interaction_id,
                context.workspace_id,
                session_id,
                str(turn[0]),
                max(1, sequence),
                max(0, revision),
                canonical_json(event),
            ),
        )
    canonical = canonical_json(event)
    return {
        "format_version": "rpg_interaction_event_log_v1",
        "checksum": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "event": event,
    }


def load_interaction_events_postgres(
    session_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 1_000,
) -> list[dict[str, Any]]:
    database = _database()
    context = _context(database)
    with database.connection() as connection:
        rows = connection.execute(
            """
            SELECT event_jsonb FROM omnix_rpg_interactions
             WHERE workspace_id = %s AND campaign_id = %s AND sequence > %s
             ORDER BY sequence ASC LIMIT %s
            """,
            (
                context.workspace_id,
                session_id,
                int(after_sequence),
                max(1, min(int(limit), 5000)),
            ),
        ).fetchall()
    return [dict(row[0]) for row in rows]


def compact_interaction_events_postgres(
    session_id: str,
    *,
    through_sequence: int,
) -> int:
    database = _database()
    context = _context(database)
    with database.connection() as connection:
        remaining = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM omnix_rpg_interactions
                 WHERE workspace_id = %s AND campaign_id = %s AND sequence > %s
                """,
                (context.workspace_id, session_id, int(through_sequence)),
            ).fetchone()[0]
        )
    return remaining


def interaction_log_status_postgres(session_id: str) -> dict[str, Any]:
    database = _database()
    context = _context(database)
    with database.connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(octet_length(event_jsonb::text)), 0)
              FROM omnix_rpg_interactions
             WHERE workspace_id = %s AND campaign_id = %s
            """,
            (context.workspace_id, session_id),
        ).fetchone()
    return {
        "format_version": "rpg_interaction_event_log_v1",
        "path": "postgresql://omnix_rpg_interactions",
        "exists": int(row[0]) > 0,
        "size_bytes": int(row[1]),
        "event_count": int(row[0]),
        "compaction_required": False,
    }
