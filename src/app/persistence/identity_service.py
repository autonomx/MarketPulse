from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .authority import AuthorityOperation
from .database import PostgresDatabase
from .errors import EntityNotFound, RevisionConflict
from .tenant import (
    LOCAL_MEMBERSHIP_ID,
    LOCAL_USER_ID,
    LOCAL_WORKSPACE_ID,
    TenantAccessDenied,
    TenantContext,
)


def _workspace(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "name": str(row[1]),
        "status": str(row[2]),
        "revision": int(row[3]),
        "created_by": str(row[4]),
        "created_at": row[5].isoformat(),
        "updated_at": row[6].isoformat(),
        "metadata": dict(row[7] or {}),
    }


class PostgresIdentityRepository:
    """Kernel-owned access to identities, workspaces, and memberships."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def ensure_local_identity(self) -> TenantContext:
        self.connection.execute(
            """INSERT INTO omnix_users (id, display_name, metadata)
                VALUES (%s, %s, %s::jsonb) ON CONFLICT (id) DO NOTHING""",
            (LOCAL_USER_ID, "Local Omnix User", json.dumps({"installation_local": True})),
        )
        self.connection.execute(
            """INSERT INTO omnix_workspaces (id, name, created_by, metadata)
                VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT (id) DO NOTHING""",
            (LOCAL_WORKSPACE_ID, "Local Omnix Workspace", LOCAL_USER_ID,
             json.dumps({"installation_local": True})),
        )
        self.connection.execute(
            """INSERT INTO omnix_workspace_memberships (id, workspace_id, user_id, roles)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (workspace_id, user_id) DO NOTHING""",
            (LOCAL_MEMBERSHIP_ID, LOCAL_WORKSPACE_ID, LOCAL_USER_ID,
             ["owner", "admin", "member"]),
        )
        return self.load_context(user_id=LOCAL_USER_ID, workspace_id=LOCAL_WORKSPACE_ID)

    def load_context(self, *, user_id: str, workspace_id: str) -> TenantContext:
        row = self.connection.execute(
            """SELECT m.id, m.user_id, m.workspace_id, m.roles
                 FROM omnix_workspace_memberships AS m
                 JOIN omnix_users AS u ON u.id = m.user_id AND u.status = 'active'
                 JOIN omnix_workspaces AS w ON w.id = m.workspace_id AND w.status = 'active'
                WHERE m.user_id = %s AND m.workspace_id = %s AND m.status = 'active'""",
            (user_id, workspace_id),
        ).fetchone()
        if row is None:
            raise TenantAccessDenied(
                f"user {user_id} has no active membership in workspace {workspace_id}"
            )
        return TenantContext(
            membership_id=str(row[0]), user_id=str(row[1]), workspace_id=str(row[2]),
            roles=frozenset(str(role) for role in row[3]),
        )

    def provision_member(
        self,
        *,
        user_id: str,
        display_name: str,
        email: str | None,
        workspace_id: str,
        roles: tuple[str, ...],
    ) -> TenantContext:
        """Create an externally authenticated user and membership when absent.

        Existing users, memberships and roles are never widened here; an
        operator changes roles explicitly.
        """
        if not roles:
            raise ValueError("at least one role is required")
        # Accounts are never merged by email (that would allow takeover via a
        # second identity provider subject); a taken email is left unset.
        self.connection.execute(
            """INSERT INTO omnix_users (id, display_name, email, metadata)
                SELECT %s, %s,
                       CASE WHEN EXISTS (
                           SELECT 1 FROM omnix_users WHERE lower(email) = lower(%s)
                       ) THEN NULL ELSE %s END,
                       %s::jsonb
                ON CONFLICT (id) DO NOTHING""",
            (user_id, display_name, email, email, json.dumps({"provisioned_by": "oidc"})),
        )
        self.connection.execute(
            """INSERT INTO omnix_workspace_memberships (id, workspace_id, user_id, roles)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (workspace_id, user_id) DO NOTHING""",
            (f"membership:{uuid.uuid4().hex}", workspace_id, user_id, list(roles)),
        )
        return self.load_context(user_id=user_id, workspace_id=workspace_id)

    # Accounts (local sign-in, WP-4.1) -------------------------------------

    def user_account(self, user_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id, display_name, email, account_kind, status FROM omnix_users WHERE id = %s",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return {"user_id": str(row[0]), "display_name": str(row[1]), "email": str(row[2]) if row[2] else None,
                "account_kind": str(row[3]), "status": str(row[4])}

    def user_by_email(self, email: str) -> str | None:
        row = self.connection.execute(
            "SELECT id FROM omnix_users WHERE lower(email) = lower(%s) AND status = 'active'",
            (email,),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def email_taken(self, email: str, *, except_user: str | None = None) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM omnix_users WHERE lower(email) = lower(%s) AND id IS DISTINCT FROM %s",
            (email, except_user),
        ).fetchone()
        return row is not None

    def update_account(self, user_id: str, *, display_name: str, email: str | None, account_kind: str) -> None:
        self.connection.execute(
            """UPDATE omnix_users
                  SET display_name = %s, email = %s, account_kind = %s,
                      revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s""",
            (display_name, email, account_kind, user_id),
        )

    def primary_workspace(self, user_id: str) -> str | None:
        """The workspace a sign-in opens: the user's oldest active membership."""
        row = self.connection.execute(
            """SELECT m.workspace_id
                 FROM omnix_workspace_memberships m
                 JOIN omnix_workspaces w ON w.id = m.workspace_id AND w.status = 'active'
                WHERE m.user_id = %s AND m.status = 'active'
                ORDER BY m.created_at, m.workspace_id
                LIMIT 1""",
            (user_id,),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def create_account_with_workspace(
        self,
        *,
        user_id: str,
        display_name: str,
        email: str | None,
        account_kind: str,
        roles: tuple[str, ...],
        seed_settings_from: str | None = LOCAL_WORKSPACE_ID,
    ) -> TenantContext:
        """A new user with a workspace of their own.

        The workspace starts with a copy of the installation workspace's
        settings (providers, models, appearance), so the account works on its
        first sign-in. Secrets are not settings: they stay in the secret store.
        """
        workspace_id = f"workspace:{uuid.uuid4().hex}"
        self.connection.execute(
            """INSERT INTO omnix_users (id, display_name, email, account_kind, metadata)
                VALUES (%s, %s, %s, %s, %s::jsonb)""",
            (user_id, display_name, email, account_kind, json.dumps({"provisioned_by": "sign_up"})),
        )
        name = "Guest workspace" if account_kind == "guest" else f"{display_name}'s workspace"
        self.connection.execute(
            """INSERT INTO omnix_workspaces (id, name, created_by, metadata)
                VALUES (%s, %s, %s, %s::jsonb)""",
            (workspace_id, name[:200], user_id, json.dumps({"personal": True, "account_kind": account_kind})),
        )
        self.connection.execute(
            """INSERT INTO omnix_workspace_memberships (id, workspace_id, user_id, roles)
                VALUES (%s, %s, %s, %s)""",
            (f"membership:{uuid.uuid4().hex}", workspace_id, user_id, list(roles)),
        )
        if seed_settings_from:
            self.connection.execute(
                """INSERT INTO omnix_settings (workspace_id, setting_scope, setting_key, value, updated_by)
                    SELECT %s, setting_scope, setting_key, value, %s
                      FROM omnix_settings WHERE workspace_id = %s
                    ON CONFLICT DO NOTHING""",
                (workspace_id, user_id, seed_settings_from),
            )
        return self.load_context(user_id=user_id, workspace_id=workspace_id)

    def set_membership_roles(self, *, user_id: str, workspace_id: str, roles: tuple[str, ...]) -> None:
        self.connection.execute(
            """UPDATE omnix_workspace_memberships SET roles = %s
                WHERE user_id = %s AND workspace_id = %s""",
            (list(roles), user_id, workspace_id),
        )

    def get_workspace(self, context: TenantContext, workspace_id: str) -> dict[str, Any] | None:
        context.require_workspace(workspace_id)
        row = self.connection.execute(
            """SELECT w.id, w.name, w.status, w.revision, w.created_by,
                      w.created_at, w.updated_at, w.metadata
                 FROM omnix_workspaces AS w
                 JOIN omnix_workspace_memberships AS m
                   ON m.workspace_id = w.id AND m.user_id = %s AND m.status = 'active'
                WHERE w.id = %s AND w.status = 'active'""",
            (context.user_id, workspace_id),
        ).fetchone()
        return _workspace(row) if row is not None else None

    def update_workspace_name(
        self,
        context: TenantContext,
        *,
        workspace_id: str,
        name: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        context.require_workspace(workspace_id)
        context.require_role("owner", "admin")
        normalized = str(name).strip()
        if not normalized:
            raise ValueError("workspace name is required")
        row = self.connection.execute(
            """UPDATE omnix_workspaces
                  SET name = %s, revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'active' AND revision = %s
                RETURNING id, name, status, revision, created_by,
                          created_at, updated_at, metadata""",
            (normalized, workspace_id, expected_revision),
        ).fetchone()
        if row is not None:
            return _workspace(row)
        current = self.connection.execute(
            "SELECT revision FROM omnix_workspaces WHERE id = %s", (workspace_id,)
        ).fetchone()
        if current is None:
            raise EntityNotFound(workspace_id)
        raise RevisionConflict(
            f"workspace {workspace_id} expected revision {expected_revision}; current {int(current[0])}"
        )


def _request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensure_local_identity(
    database: PostgresDatabase,
    *,
    authority_operation: AuthorityOperation = AuthorityOperation.RUNTIME_MUTATION,
) -> TenantContext:
    """Ensure the local identity once schema compatibility is already verified.

    This function never applies migrations. It emits the bootstrap audit event
    only when the local membership did not already exist.
    """
    from .tenant_scope import system_scope
    from .unit_of_work import unit_of_work

    with system_scope("identity.provision"), unit_of_work(database, authority_operation=authority_operation) as work:
        existed = work.connection.execute(
            """
            SELECT 1
              FROM omnix_workspace_memberships
             WHERE workspace_id = %s AND user_id = %s
            """,
            ("workspace:local", "user:local"),
        ).fetchone() is not None
        context = work.identities.ensure_local_identity()
        if not existed:
            work.audit.append(
                context,
                aggregate_type="workspace",
                aggregate_id=context.workspace_id,
                action="workspace.local_bootstrap",
                payload={"local_installation": True},
            )
        work.commit()
        return context


SYSTEM_ROLE = "system"


def list_active_workspace_contexts(
    database: PostgresDatabase, *, limit: int | None = None, include_inactive: bool = False,
) -> list[TenantContext]:
    """System contexts for per-workspace background work (WP-4.2).

    Job workers and scheduled tasks iterate these instead of assuming the
    local workspace. The context acts as the workspace's creator with only
    the ``system`` role; it is never a request principal. Retiring a module
    (PA-4.3) asks for inactive workspaces too: their jobs are in flight as well.
    Every workspace is returned unless the caller sets ``limit``.
    """
    from .tenant_scope import system_scope
    from .unit_of_work import unit_of_work

    with system_scope("identity.workspaces"), unit_of_work(
        database, authority_operation=AuthorityOperation.DIAGNOSTIC_READ
    ) as work:
        rows = work.connection.execute(
            """SELECT id, created_by FROM omnix_workspaces
                WHERE status = 'active' OR %s ORDER BY id LIMIT %s""",
            (include_inactive, limit),
        ).fetchall()
        work.rollback()
    return [_system_context(row) for row in rows]


def list_workspace_contexts_with_ready_jobs(
    database: PostgresDatabase, resource_classes: list[str] | tuple[str, ...], *, limit: int = 256,
) -> list[TenantContext]:
    """System contexts of the active workspaces that have a job ready to claim.

    One query per poll, whatever the number of workspaces: a job worker claims
    only where there is work instead of trying every workspace in turn. The
    predicate is a superset of ``claim_next``'s, so a listed workspace may
    still yield nothing. Workspaces come oldest-waiting job first, so the
    ``limit`` never starves one: a workspace left out this poll has waited
    less than every listed one and moves up as they are served.
    """
    from .tenant_scope import system_scope
    from .unit_of_work import unit_of_work

    if not resource_classes:
        return []
    with system_scope("identity.workspaces"), unit_of_work(
        database, authority_operation=AuthorityOperation.DIAGNOSTIC_READ
    ) as work:
        rows = work.connection.execute(
            """SELECT w.id, w.created_by, MIN(j.available_at) AS waiting_since
                 FROM omnix_workspaces w
                 JOIN omnix_jobs j ON j.workspace_id = w.id
                WHERE w.status = 'active'
                  AND j.status IN ('queued', 'retrying', 'waiting')
                  AND j.available_at <= CURRENT_TIMESTAMP
                  AND j.resource_class = ANY(%s)
                  AND j.attempt_count < j.max_attempts
                GROUP BY w.id, w.created_by
                ORDER BY waiting_since, w.id
                LIMIT %s""",
            (list(resource_classes), max(1, int(limit))),
        ).fetchall()
        work.rollback()
    return [_system_context(row) for row in rows]


def _system_context(row: Any) -> TenantContext:
    return TenantContext(
        user_id=str(row[1]),
        workspace_id=str(row[0]),
        membership_id=f"system:{row[0]}",
        roles=frozenset({SYSTEM_ROLE}),
    )


def get_workspace(database: PostgresDatabase, context: TenantContext) -> dict[str, Any]:
    from .unit_of_work import unit_of_work

    with unit_of_work(database) as work:
        workspace = work.identities.get_workspace(context, context.workspace_id)
        if workspace is None:
            raise KeyError(context.workspace_id)
        work.rollback()
        return workspace


def rename_workspace(
    database: PostgresDatabase,
    context: TenantContext,
    *,
    name: str,
    expected_revision: int,
    operation_key: str,
) -> dict[str, Any]:
    from .unit_of_work import unit_of_work

    request: dict[str, Any] = {
        "workspace_id": context.workspace_id,
        "name": str(name).strip(),
        "expected_revision": int(expected_revision),
    }
    digest = _request_hash(request)
    with unit_of_work(database) as work:
        reservation = work.idempotency.reserve(
            context,
            scope="workspace.rename",
            key=operation_key,
            request_hash=digest,
        )
        if reservation["status"] == "completed":
            response = reservation.get("response")
            if not isinstance(response, dict):
                raise RuntimeError("completed idempotency record has no response")
            work.rollback()
            return response
        if not reservation["owner"]:
            raise RuntimeError("workspace rename is already in progress")
        workspace = work.identities.update_workspace_name(
            context,
            workspace_id=context.workspace_id,
            name=request["name"],
            expected_revision=expected_revision,
        )
        work.audit.append(
            context,
            aggregate_type="workspace",
            aggregate_id=context.workspace_id,
            action="workspace.renamed",
            payload={
                "revision": workspace["revision"],
                "operation_key": operation_key,
            },
        )
        work.idempotency.complete(
            context,
            scope="workspace.rename",
            key=operation_key,
            response=workspace,
        )
        work.commit()
        return workspace
