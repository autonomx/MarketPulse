from __future__ import annotations

import hashlib
import json
from typing import Any

from app.persistence.errors import EntityNotFound, RevisionConflict
from app.persistence.tenant import TenantContext
from app.runtime.pagination import MAX_PAGE_SIZE, page_limit


MAX_COMPACT_TURN_RESPONSE_BYTES = 20_000


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def state_hash(state: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


def _campaign(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "owner_user_id": str(row[2]) if row[2] is not None else None,
        "title": str(row[3]),
        "revision": int(row[4]),
        "state": dict(row[5]),
        "state_hash": str(row[6]),
        "engine_version": str(row[7]),
        "schema_version": str(row[8]),
        "seed": str(row[9]),
        "status": str(row[10]),
        "created_at": row[11].isoformat(),
        "updated_at": row[12].isoformat(),
        "metadata": dict(row[13]),
    }


# Top-level state keys a session list row carries (``app.apps.rpg.session.list_summaries``).
SESSION_SUMMARY_STATE_KEYS = (
    "location",
    "current_location",
    "current_location_id",
    "current_location_name",
    "published_world",
    "environment_snapshot",
)

_CAMPAIGN_COLUMNS = """
id, workspace_id, owner_user_id, title, revision, state_jsonb, state_hash,
engine_version, schema_version, seed, status, created_at, updated_at, metadata
"""


def _turn(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "campaign_id": str(row[2]),
        "sequence": int(row[3]),
        "submission_id": str(row[4]),
        "expected_revision": int(row[5]),
        "resulting_revision": int(row[6]),
        "command": dict(row[7]),
        "canonical_effects": dict(row[8]),
        "state_hash_before": str(row[9]),
        "state_hash_after": str(row[10]),
        "engine_version": str(row[11]),
        "schema_version": str(row[12]),
        "interaction_id": str(row[13]),
        "compact_response": dict(row[14]),
        "created_at": row[15].isoformat(),
        "state_delta": list(row[16]) if row[16] is not None else None,
    }


_TURN_COLUMNS = """
id, workspace_id, campaign_id, sequence, submission_id, expected_revision,
resulting_revision, command_jsonb, canonical_effects_jsonb, state_hash_before,
state_hash_after, engine_version, schema_version, interaction_id,
compact_response, created_at, state_delta_jsonb
"""


def _turn_delta(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    from .state_delta import state_delta

    return state_delta(before, after)


class CompactTurnResponseTooLarge(ValueError):
    pass


class StateHashConflict(RevisionConflict):
    pass


class PostgresRpgRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create_campaign(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        title: str,
        state: dict[str, Any],
        engine_version: str,
        schema_version: str,
        seed: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        digest = state_hash(state)
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_campaigns (
                id, workspace_id, owner_user_id, title, state_jsonb, state_hash,
                engine_version, schema_version, seed, metadata
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)
            RETURNING {_CAMPAIGN_COLUMNS}
            """,
            (
                campaign_id,
                context.workspace_id,
                context.user_id,
                title,
                canonical_json(state),
                digest,
                engine_version,
                schema_version,
                seed,
                canonical_json(metadata or {}),
            ),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO omnix_rpg_participants
                (campaign_id, user_id, role, permissions)
            VALUES (%s, %s, 'owner', ARRAY['read', 'write', 'admin'])
            ON CONFLICT DO NOTHING
            """,
            (campaign_id, context.user_id),
        )
        return _campaign(row)

    def get_campaign(
        self,
        context: TenantContext,
        campaign_id: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.connection.execute(
            f"SELECT {_CAMPAIGN_COLUMNS} FROM omnix_rpg_campaigns "
            f"WHERE id = %s AND workspace_id = %s{suffix}",
            (campaign_id, context.workspace_id),
        ).fetchone()
        return _campaign(row) if row is not None else None

    def lock_campaign(self, context: TenantContext, campaign_id: str) -> bool:
        """Lock the campaign row ``FOR UPDATE`` without reading its state; False when there is none."""
        row = self.connection.execute(
            "SELECT 1 FROM omnix_rpg_campaigns WHERE id = %s AND workspace_id = %s FOR UPDATE",
            (campaign_id, context.workspace_id),
        ).fetchone()
        return row is not None

    def launch_gate_source(self, context: TenantContext, campaign_id: str) -> dict[str, Any] | None:
        """What the launch-readiness gate reads of the state, without decoding the saved game.

        ``campaign_launch_readiness`` asks whether the setup's world forge is a
        non-empty object, whether the state has a (truthy) campaign bible, and
        reads the runtime's launch gate; the truthiness is evaluated here.
        """
        row = self.connection.execute(
            """
            SELECT COALESCE(jsonb_typeof(state_jsonb #> '{setup_payload,world_forge}') = 'object'
                            AND state_jsonb #> '{setup_payload,world_forge}' <> '{}'::jsonb, FALSE),
                   COALESCE(state_jsonb #> '{state,campaign_bible}' NOT IN
                            ('null'::jsonb, 'false'::jsonb, '0'::jsonb, '""'::jsonb, '{}'::jsonb, '[]'::jsonb), FALSE),
                   state_jsonb #> '{runtime_state,campaign_launch_gate}'
              FROM omnix_rpg_campaigns WHERE id = %s AND workspace_id = %s
            """,
            (campaign_id, context.workspace_id),
        ).fetchone()
        if row is None:
            return None
        gate = row[2] if isinstance(row[2], dict) else {}
        return {
            "setup_payload": {"world_forge": {"enabled": True} if row[0] else {}},
            "state": {"campaign_bible": True if row[1] else None},
            "runtime_state": {"campaign_launch_gate": dict(gate)},
        }

    def stored_version(self, context: TenantContext, campaign_id: str) -> tuple[int, str] | None:
        """The campaign's stored (revision, state_hash), without its state."""
        row = self.connection.execute(
            "SELECT revision, state_hash FROM omnix_rpg_campaigns WHERE id = %s AND workspace_id = %s",
            (campaign_id, context.workspace_id),
        ).fetchone()
        return (int(row[0]), str(row[1])) if row is not None else None

    def stored_rng_seed(self, context: TenantContext, campaign_id: str) -> tuple[bool, Any]:
        """Whether the campaign exists, and its stored ``simulation_state.rng_seed`` (without loading the state)."""
        row = self.connection.execute(
            "SELECT state_jsonb #> '{simulation_state,rng_seed}' FROM omnix_rpg_campaigns WHERE id = %s AND workspace_id = %s",
            (campaign_id, context.workspace_id),
        ).fetchone()
        return (row is not None, row[0] if row is not None else None)

    def list_campaigns(
        self,
        context: TenantContext,
        *,
        limit: int = 100,
        status: str = "active",
        before: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """One page, newest first; pass the last row's ``(updated_at, id)`` as ``before``."""
        rows = self.connection.execute(
            f"SELECT {_CAMPAIGN_COLUMNS} FROM omnix_rpg_campaigns "
            "WHERE workspace_id = %s AND status = %s "
            "AND (%s::timestamptz IS NULL OR (updated_at, id) < (%s::timestamptz, %s::text)) "
            "ORDER BY updated_at DESC, id DESC LIMIT %s",
            (
                context.workspace_id,
                status,
                before[0] if before else None,
                before[0] if before else None,
                before[1] if before else None,
                page_limit(limit, default=100),
            ),
        ).fetchall()
        return [_campaign(row) for row in rows]

    def list_campaign_summary_sources(
        self,
        context: TenantContext,
        *,
        limit: int = 100,
        status: str = "active",
        before: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """One page of campaigns, newest first, each with only what a list row reads.

        ``source`` keeps the session document's shape (``manifest``, and of
        ``state`` the world and region environments, the scene's environment
        context and the opening location keys). A trigger keeps it in
        ``summary_jsonb`` with the turn count, player level and location name
        on every state write (migration 0157), so a list never reads a saved
        game's state.
        """
        rows = self.connection.execute(
            """
            SELECT id, updated_at, summary_jsonb, turn_count, player_level, location_name
              FROM omnix_rpg_campaigns
             WHERE workspace_id = %s AND status = %s
               AND (%s::timestamptz IS NULL OR (updated_at, id) < (%s::timestamptz, %s::text))
             ORDER BY updated_at DESC, id DESC LIMIT %s
            """,
            (
                context.workspace_id,
                status,
                before[0] if before else None,
                before[0] if before else None,
                before[1] if before else None,
                page_limit(limit, default=100),
            ),
        ).fetchall()
        return [
            {
                "id": str(row[0]),
                "updated_at": row[1].isoformat(),
                "source": dict(row[2]),
                "turn_count": row[3],
                "player_level": row[4],
                "location_name": row[5],
            }
            for row in rows
        ]

    def iter_campaigns(self, context: TenantContext, *, status: str = "active"):
        """Every campaign with ``status``, newest first, a page at a time."""
        before: tuple[str, str] | None = None
        while True:
            page = self.list_campaigns(context, limit=MAX_PAGE_SIZE, status=status, before=before)
            yield from page
            if len(page) < MAX_PAGE_SIZE:
                return
            before = (page[-1]["updated_at"], page[-1]["id"])

    def get_turn_by_submission(
        self,
        context: TenantContext,
        campaign_id: str,
        submission_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_TURN_COLUMNS} FROM omnix_rpg_turns "
            "WHERE workspace_id = %s AND campaign_id = %s AND submission_id = %s",
            (context.workspace_id, campaign_id, submission_id),
        ).fetchone()
        return _turn(row) if row is not None else None

    def commit_turn(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        turn_id: str,
        submission_id: str,
        interaction_id: str,
        expected_revision: int,
        command: dict[str, Any],
        next_state: dict[str, Any],
        canonical_effects: dict[str, Any],
        interaction_event: dict[str, Any],
        compact_response: dict[str, Any],
        engine_version: str,
        schema_version: str,
        create_snapshot: bool = False,
        snapshot_id: str | None = None,
        locked_campaign: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit one turn. ``locked_campaign`` is the row the caller already read ``FOR UPDATE`` in this transaction."""
        encoded_response = canonical_json(compact_response).encode("utf-8")
        if not 0 < len(encoded_response) <= MAX_COMPACT_TURN_RESPONSE_BYTES:
            raise CompactTurnResponseTooLarge(
                f"compact turn response is {len(encoded_response)} bytes; "
                f"limit is {MAX_COMPACT_TURN_RESPONSE_BYTES}"
            )

        campaign = locked_campaign if locked_campaign is not None else self.get_campaign(context, campaign_id, for_update=True)
        if campaign is None or campaign["id"] != campaign_id:
            raise EntityNotFound(campaign_id)

        existing = self.get_turn_by_submission(context, campaign_id, submission_id)
        if existing is not None:
            return {"idempotent_replay": True, "campaign": campaign, "turn": existing}

        if campaign["revision"] != expected_revision:
            raise RevisionConflict(
                f"campaign {campaign_id} expected revision {expected_revision}; "
                f"current {campaign['revision']}"
            )
        # The row's state and hash are always written together, and the commit
        # is conditional on the revision: re-hashing the stored state (a full
        # canonical serialization) on every turn caught only out-of-band edits
        # to the row (WP-5.6). Snapshots still verify the hash they store.
        before_hash = campaign["state_hash"]

        resulting_revision = expected_revision + 1
        sequence = resulting_revision
        # Serialized and hashed once: the campaign row and the snapshot store the same text.
        encoded_state = canonical_json(next_state)
        after_hash = hashlib.sha256(encoded_state.encode("utf-8")).hexdigest()
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_turns (
                id, workspace_id, campaign_id, sequence, submission_id,
                expected_revision, resulting_revision, command_jsonb,
                canonical_effects_jsonb, state_hash_before, state_hash_after,
                engine_version, schema_version, interaction_id, compact_response,
                state_delta_jsonb
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb
            ) RETURNING {_TURN_COLUMNS}
            """,
            (
                turn_id,
                context.workspace_id,
                campaign_id,
                sequence,
                submission_id,
                expected_revision,
                resulting_revision,
                canonical_json(command),
                canonical_json(canonical_effects),
                before_hash,
                after_hash,
                engine_version,
                schema_version,
                interaction_id,
                encoded_response.decode("utf-8"),
                canonical_json(_turn_delta(campaign["state"], next_state)),
            ),
        ).fetchone()
        turn = _turn(row)

        updated = self.connection.execute(
            f"""
            UPDATE omnix_rpg_campaigns
               SET state_jsonb = %s::jsonb,
                   state_hash = %s,
                   revision = %s,
                   engine_version = %s,
                   schema_version = %s,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s AND revision = %s
            RETURNING {_CAMPAIGN_COLUMNS}
            """,
            (
                encoded_state,
                after_hash,
                resulting_revision,
                engine_version,
                schema_version,
                campaign_id,
                context.workspace_id,
                expected_revision,
            ),
        ).fetchone()
        if updated is None:
            raise RevisionConflict(f"campaign changed while committing turn: {campaign_id}")
        updated_campaign = _campaign(updated)

        self.connection.execute(
            """
            INSERT INTO omnix_rpg_interactions (
                interaction_id, workspace_id, campaign_id, turn_id, sequence,
                state_revision, event_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                interaction_id,
                context.workspace_id,
                campaign_id,
                turn_id,
                sequence,
                resulting_revision,
                canonical_json(interaction_event),
            ),
        )

        snapshot: dict[str, Any] | None = None
        if create_snapshot:
            snapshot = self.create_snapshot(
                context,
                campaign_id=campaign_id,
                snapshot_id=snapshot_id or f"snapshot:{campaign_id}:{resulting_revision}",
                revision=resulting_revision,
                state=next_state,
                state_hash_value=after_hash,
                engine_version=engine_version,
                schema_version=schema_version,
                encoded_state=encoded_state,
            )
        return {
            "idempotent_replay": False,
            "campaign": updated_campaign,
            "turn": turn,
            "snapshot": snapshot,
        }

    def create_snapshot(
        self,
        context: TenantContext,
        *,
        campaign_id: str,
        snapshot_id: str,
        revision: int,
        state: dict[str, Any],
        state_hash_value: str,
        engine_version: str,
        schema_version: str,
        encoded_state: str | None = None,
    ) -> dict[str, Any]:
        """``encoded_state``, when given, is ``canonical_json(state)`` already computed by the caller."""
        encoded = encoded_state if encoded_state is not None else canonical_json(state)
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != state_hash_value:
            raise StateHashConflict("snapshot state hash does not match snapshot state")
        row = self.connection.execute(
            """
            INSERT INTO omnix_rpg_snapshots (
                id, workspace_id, campaign_id, revision, snapshot_jsonb,
                state_hash, engine_version, schema_version
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            RETURNING id, campaign_id, revision, snapshot_jsonb, state_hash,
                      engine_version, schema_version, created_at
            """,
            (
                snapshot_id,
                context.workspace_id,
                campaign_id,
                revision,
                encoded,
                state_hash_value,
                engine_version,
                schema_version,
            ),
        ).fetchone()
        return {
            "id": str(row[0]),
            "campaign_id": str(row[1]),
            "revision": int(row[2]),
            "state": dict(row[3]),
            "state_hash": str(row[4]),
            "engine_version": str(row[5]),
            "schema_version": str(row[6]),
            "created_at": row[7].isoformat(),
        }

