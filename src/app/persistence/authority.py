from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PostgresAuthorityError(RuntimeError):
    """Raised when a PostgreSQL operation is not allowed by cutover authority."""


class AuthorityOperation(str, Enum):
    RUNTIME_START = "runtime_start"
    RUNTIME_MUTATION = "runtime_mutation"
    DIAGNOSTIC_READ = "diagnostic_read"
    LEGACY_IMPORT = "legacy_import"


@dataclass(frozen=True, slots=True)
class PostgresAuthorityPolicy:
    mode: str
    authority_state: str
    runtime_start_allowed: bool
    runtime_writes_allowed: bool
    diagnostic_reads_allowed: bool
    legacy_import_allowed: bool


_RUNTIME_WRITE_STATES = frozenset(
    {"postgresql_open_for_writes", "postgresql_stabilized"}
)
_DIAGNOSTIC_READ_STATES = frozenset(
    {
        "imported_unverified",
        "imported_verified",
        "postgresql_activated_frozen",
        "postgresql_open_for_writes",
        "postgresql_stabilized",
        "rollback_recorded",
    }
)
_LEGACY_IMPORT_STATES = frozenset({"legacy_preflight", "imported_unverified"})


def authority_policy(*, mode: str, authority_state: str) -> PostgresAuthorityPolicy:
    state = str(authority_state)
    compatibility_mode = str(mode)
    runtime_allowed = compatibility_mode == "postgresql" and state in _RUNTIME_WRITE_STATES
    return PostgresAuthorityPolicy(
        mode=compatibility_mode,
        authority_state=state,
        runtime_start_allowed=runtime_allowed,
        runtime_writes_allowed=runtime_allowed,
        diagnostic_reads_allowed=state in _DIAGNOSTIC_READ_STATES,
        legacy_import_allowed=(
            compatibility_mode == "legacy_preflight" and state in _LEGACY_IMPORT_STATES
        ),
    )


def current_authority_policy(connection: Any, *, for_update: bool = False) -> PostgresAuthorityPolicy:
    suffix = " FOR UPDATE" if for_update else ""
    row = connection.execute(
        "SELECT mode, authority_state FROM omnix_persistence_cutover "
        "WHERE singleton = TRUE" + suffix
    ).fetchone()
    if row is None:
        raise PostgresAuthorityError("cutover authority singleton is missing")
    return authority_policy(mode=str(row[0]), authority_state=str(row[1]))


# Within an authority snapshot scope, one read of the cutover authority per
# database serves every unit of work (``authority_snapshot_scope``).
_SNAPSHOT: ContextVar[dict[tuple[Any, ...], PostgresAuthorityPolicy] | None] = ContextVar(
    "omnix_persistence_authority_snapshot",
    default=None,
)


@contextmanager
def authority_snapshot_scope() -> Iterator[None]:
    """Reuse the first read of the cutover authority for one short request.

    The check reads the authority without locking it, so it already holds for
    the moment a transaction starts; a foreground RPG turn opens about thirty
    units of work in under a second, and reading the singleton for each was a
    tenth of the turn's statements (WP-5.6). Inside this scope the policy is
    read once per database and reused until the scope ends.
    """
    token = _SNAPSHOT.set({})
    try:
        yield
    finally:
        _SNAPSHOT.reset(token)


def _snapshot_key(connection: Any) -> tuple[Any, ...]:
    info = getattr(connection, "info", None)
    return (getattr(info, "host", None), getattr(info, "port", None), getattr(info, "dbname", None))


def require_authority_operation(
    connection: Any,
    operation: AuthorityOperation,
) -> PostgresAuthorityPolicy:
    snapshot = _SNAPSHOT.get()
    key = _snapshot_key(connection) if snapshot is not None else ()
    policy = snapshot.get(key) if snapshot is not None else None
    if policy is None:
        policy = current_authority_policy(connection)
        if snapshot is not None:
            snapshot[key] = policy
    allowed = {
        AuthorityOperation.RUNTIME_START: policy.runtime_start_allowed,
        AuthorityOperation.RUNTIME_MUTATION: policy.runtime_writes_allowed,
        AuthorityOperation.DIAGNOSTIC_READ: policy.diagnostic_reads_allowed,
        AuthorityOperation.LEGACY_IMPORT: policy.legacy_import_allowed,
    }[operation]
    if not allowed:
        raise PostgresAuthorityError(
            f"PostgreSQL {operation.value} is blocked while authority_state="
            f"{policy.authority_state!r} and mode={policy.mode!r}"
        )
    return policy


def domain_row_count(connection: Any) -> int:
    tables = (
        "omnix_chat_sessions",
        "omnix_characters",
        "omnix_memory_records",
        "omnix_jobs",
        "omnix_assets",
        "omnix_rpg_campaigns",
        "omnix_provider_configs",
        "omnix_prompt_templates",
        "omnix_research_records",
        "omnix_reports",
        "omnix_module_records",
    )
    expressions = ", ".join(f"(SELECT COUNT(*) FROM {table})" for table in tables)
    row = connection.execute(f"SELECT {expressions}").fetchone()
    return sum(int(value) for value in row)


def initialize_fresh_install_authority(
    connection: Any,
    *,
    software_revision: str,
    schema_version: str,
) -> PostgresAuthorityPolicy:
    policy = current_authority_policy(connection)
    if policy.authority_state != "legacy_preflight" or policy.mode != "legacy_preflight":
        return policy
    policy = current_authority_policy(connection, for_update=True)
    if policy.authority_state != "legacy_preflight" or policy.mode != "legacy_preflight":
        return policy
    imports = int(
        connection.execute("SELECT COUNT(*) FROM omnix_legacy_import_runs").fetchone()[0]
    )
    if imports != 0 or domain_row_count(connection) != 0:
        return policy

    metadata = {
        "fresh_installation": True,
        "legacy_import_required": False,
        "initialization_schema_version": schema_version,
        "initialization_software_revision": software_revision,
    }
    connection.execute(
        """
        UPDATE omnix_persistence_cutover
           SET mode = 'postgresql',
               authority_state = 'postgresql_stabilized',
               activated_at = CURRENT_TIMESTAMP,
               opened_for_writes_at = CURRENT_TIMESTAMP,
               stabilized_at = CURRENT_TIMESTAMP,
               updated_at = CURRENT_TIMESTAMP,
               metadata = metadata || %s::jsonb
         WHERE singleton = TRUE
        """,
        (json.dumps(metadata, sort_keys=True, separators=(",", ":")),),
    )
    connection.execute(
        """
        INSERT INTO omnix_cutover_transitions (
            from_state, to_state, software_revision, schema_version,
            operator_note, metadata
        ) VALUES (
            'legacy_preflight', 'postgresql_stabilized', %s, %s,
            'Automatic initialization of an empty fresh installation', %s::jsonb
        )
        """,
        (
            software_revision,
            schema_version,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        ),
    )
    return current_authority_policy(connection)
