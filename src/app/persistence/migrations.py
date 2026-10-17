from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import PostgresDatabase, default_database

logger = logging.getLogger(__name__)


_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS omnix_schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL,
    execution_ms DOUBLE PRECISION NOT NULL,
    phase TEXT NOT NULL DEFAULT 'contract',
    transactional BOOLEAN NOT NULL DEFAULT TRUE
)
"""
_MIGRATION_HEADER = re.compile(
    r"^--\s*omnix-migration:\s*phase=(expand|contract|data)\s+transactional=(true|false)\s*$",
    re.IGNORECASE,
)

# Far above the number of migration files; keeps the history read bounded.
MAX_APPLIED_MIGRATIONS = 100_000
# Session-scoped migration lock. CLI migration is the only schema mutation path.
MIGRATION_ADVISORY_LOCK_KEY = 22351186257100871
SCHEMA_MIN_CONTRACT = "0100_migration_metadata"

_CANONICAL_MIGRATION_CHECKSUMS = {
    "0083_trading_evidence_execution_v3": (
        "44753b480f6fa7b74bbd52d1c5f5f2142e52e8b6a5f776ed7cd44900a2b20ca0"
    )
}

_LEGACY_MIGRATION_CHECKSUMS: dict[str, frozenset[str]] = {
    "0083_trading_evidence_execution_v3": frozenset(
        {
            "221d3953a6e0e43c6482f2a0604fdadfdc203e10c85b88ae10e450b2a3082877",
            "37abdf0f320e07b8c43dc6a1fcd8cf7adc657bd4fae010b3b905bbd55c9aeb1c",
            "453e6d11f5d9cd33d159c0cef4b736088fb83c96b02bca11a9da7b3149472aab",
        }
    )
}


class MigrationError(RuntimeError):
    pass


class MigrationDriftError(MigrationError):
    pass


class SchemaCompatibilityError(MigrationError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    path: Path
    checksum: str
    sql: str
    phase: str = "contract"
    transactional: bool = True


def _checksum_is_accepted(migration: Migration, checksum: str) -> bool:
    if checksum == migration.checksum:
        return True
    canonical = _CANONICAL_MIGRATION_CHECKSUMS.get(migration.version)
    return (
        migration.checksum == canonical
        and checksum in _LEGACY_MIGRATION_CHECKSUMS.get(migration.version, frozenset())
    )


def migration_root() -> Path:
    return Path(__file__).with_name("migrations")


def migration_roots() -> list[Path]:
    """Every folder schema migrations are read from (ADR-0016, PA-2.3).

    Exactly three places, found by convention so the kernel never reads the
    feature catalog: the kernel folder, a ``migrations/`` folder that sits next
    to a module's ``feature.py``, and retired modules' tombstone folders. Every
    module's migrations are found whether or not the feature is enabled: the
    schema is a release artifact, not a feature flag.
    """
    app_root = Path(__file__).resolve().parents[1]
    roots = [migration_root()]
    # Feature packages sit in a tier folder, app/<tier>/<package>, or nested,
    # app/<tier>/<package>/<feature> (PA-5.3); app/<package> stays accepted.
    roots += sorted(
        folder for pattern in ("*/migrations", "*/*/migrations", "*/*/*/migrations") for folder in app_root.glob(pattern)
        if folder.is_dir() and (folder.parent / "feature.py").is_file()
    )
    retired = migration_root().parent / "retired"
    if retired.is_dir():
        roots += sorted(folder for folder in retired.glob("*/migrations") if folder.is_dir())
    return roots


def _metadata(sql: str) -> tuple[str, bool]:
    first = sql.splitlines()[0].strip() if sql.splitlines() else ""
    match = _MIGRATION_HEADER.match(first)
    if match is None:
        return "contract", True
    return match.group(1).lower(), match.group(2).lower() == "true"


def discover_migrations(root: Path | None = None) -> list[Migration]:
    roots = [root] if root is not None else migration_roots()
    paths = sorted((path for folder in roots for path in folder.glob("*.sql")), key=lambda path: path.stem)
    stems = [path.stem for path in paths]
    if len(stems) != len(set(stems)):
        duplicates = sorted({stem for stem in stems if stems.count(stem) > 1})
        raise MigrationError("migration versions must be unique across folders: " + ", ".join(duplicates))
    migrations: list[Migration] = []
    for path in paths:
        sql = path.read_text(encoding="utf-8")
        phase, transactional = _metadata(sql)
        migrations.append(
            Migration(
                version=path.stem,
                path=path,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
                phase=phase,
                transactional=transactional,
            )
        )
    versions = [migration.version for migration in migrations]
    if versions != sorted(set(versions)):
        raise MigrationError("migration versions must be unique and lexically ordered")
    return migrations


# The newest migration this application knows; derived, never hand-edited (PA-2.3).
SCHEMA_KNOWN = discover_migrations()[-1].version
APPLICATION_SCHEMA_MIN = SCHEMA_MIN_CONTRACT
APPLICATION_SCHEMA_MAX = SCHEMA_KNOWN


def _has_metadata_columns(connection: Any) -> bool:
    rows = connection.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'omnix_schema_migrations'
           AND column_name IN ('phase', 'transactional')
         LIMIT 2
        """
    ).fetchall()
    return {str(row[0]) for row in rows} == {"phase", "transactional"}


def _applied(connection: Any, *, initialize_table: bool = True) -> dict[str, dict[str, Any]]:
    if initialize_table:
        connection.execute(_MIGRATION_TABLE_SQL)
        connection.commit()
    if _has_metadata_columns(connection):
        rows = connection.execute(
            "SELECT version, checksum, applied_at, execution_ms, phase, transactional "
            "FROM omnix_schema_migrations ORDER BY version LIMIT %s",
            (MAX_APPLIED_MIGRATIONS,),
        ).fetchall()
        return {
            str(row[0]): {
                "checksum": str(row[1]),
                "applied_at": row[2].isoformat(),
                "execution_ms": float(row[3]),
                "phase": str(row[4] or "contract"),
                "transactional": bool(row[5]),
            }
            for row in rows
        }
    rows = connection.execute(
        "SELECT version, checksum, applied_at, execution_ms "
        "FROM omnix_schema_migrations ORDER BY version LIMIT %s",
        (MAX_APPLIED_MIGRATIONS,),
    ).fetchall()
    return {
        str(row[0]): {
            "checksum": str(row[1]),
            "applied_at": row[2].isoformat(),
            "execution_ms": float(row[3]),
            "phase": "contract",
            "transactional": True,
        }
        for row in rows
    }


def _compatibility(
    *,
    discovered: list[Migration],
    applied: dict[str, dict[str, Any]],
    drift: list[str],
    pending: list[str],
) -> dict[str, Any]:
    known = {migration.version: migration for migration in discovered}
    unknown = sorted(set(applied) - set(known))
    unknown_contract = [
        version
        for version in unknown
        if str(applied[version].get("phase") or "contract") == "contract"
    ]
    unknown_compatible = [version for version in unknown if version not in unknown_contract]
    current = max(applied) if applied else None

    required_contracts = [
        migration.version
        for migration in discovered
        if migration.phase == "contract" and migration.version <= SCHEMA_MIN_CONTRACT
    ]
    missing_required = [version for version in required_contracts if version not in applied]
    compatible = not drift and not unknown_contract and not missing_required

    return {
        "current_schema": current,
        "application_schema_min": SCHEMA_MIN_CONTRACT,
        "application_schema_max": SCHEMA_KNOWN,
        "schema_min_contract": SCHEMA_MIN_CONTRACT,
        "schema_known": SCHEMA_KNOWN,
        "compatible": compatible,
        "unknown_applied": unknown,
        "unknown_contract": unknown_contract,
        "unknown_compatible": unknown_compatible,
        "missing_required": missing_required,
        "pending": pending,
    }


def migration_status(
    database: PostgresDatabase | None = None,
    *,
    root: Path | None = None,
    initialize_table: bool = True,
) -> dict[str, Any]:
    db = database or default_database()
    discovered = discover_migrations(root)
    with db.connection() as connection:
        applied = _applied(connection, initialize_table=initialize_table)
    drift: list[str] = []
    pending: list[str] = []
    for migration in discovered:
        record = applied.get(migration.version)
        if record is None:
            pending.append(migration.version)
        elif not _checksum_is_accepted(migration, record["checksum"]):
            drift.append(migration.version)
    compatibility = _compatibility(
        discovered=discovered,
        applied=applied,
        drift=drift,
        pending=pending,
    )
    return {
        "ok": not drift and not compatibility["unknown_contract"],
        "discovered": [migration.version for migration in discovered],
        "applied": sorted(applied),
        "pending": pending,
        "checksum_drift": drift,
        "records": applied,
        **compatibility,
    }


def assert_schema_compatible(status: dict[str, Any]) -> None:
    if status.get("compatible") is True:
        return
    raise SchemaCompatibilityError(
        "PostgreSQL schema is incompatible with this Omnix release: "
        f"current={status.get('current_schema')!r}, "
        f"required_contract={status.get('schema_min_contract')!r}, "
        f"missing_required={status.get('missing_required') or []}, "
        f"unknown_contract={status.get('unknown_contract') or []}, "
        f"drift={status.get('checksum_drift') or []}"
    )


def _record_migration(connection: Any, migration: Migration, elapsed_ms: float) -> None:
    if _has_metadata_columns(connection):
        connection.execute(
            """
            INSERT INTO omnix_schema_migrations
                (version, checksum, applied_at, execution_ms, phase, transactional)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                migration.version,
                migration.checksum,
                datetime.now(timezone.utc),
                elapsed_ms,
                migration.phase,
                migration.transactional,
            ),
        )
    else:
        connection.execute(
            "INSERT INTO omnix_schema_migrations "
            "(version, checksum, applied_at, execution_ms) VALUES (%s, %s, %s, %s)",
            (
                migration.version,
                migration.checksum,
                datetime.now(timezone.utc),
                elapsed_ms,
            ),
        )


def _assert_application_order(
    migrations: list[Migration],
    applied: dict[str, dict[str, Any]],
    *,
    allow_out_of_order: bool,
) -> None:
    if allow_out_of_order or not applied:
        return
    highest_applied = max(applied)
    lower_pending = [
        migration.version
        for migration in migrations
        if migration.version < highest_applied and migration.version not in applied
    ]
    if lower_pending:
        raise MigrationDriftError(
            "refusing out-of-order migrations below already-applied "
            f"{highest_applied}: {lower_pending}"
        )


def apply_migrations(
    database: PostgresDatabase | None = None,
    *,
    root: Path | None = None,
    allow_out_of_order: bool = False,
) -> dict[str, Any]:
    """Apply migrations from an operator/release path, never a request transaction."""
    from time import perf_counter
    from .tenant_scope import system_scope
    from .transaction_binding import shared_work

    db = database or default_database()
    if shared_work(db) is not None:
        raise MigrationError("schema migrations cannot run inside a runtime transaction")

    migrations = discover_migrations(root)
    applied_now: list[str] = []
    # Data migrations span every workspace (row-level security, WP-4.4).
    with system_scope("migrations"), db.connection() as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))
        connection.commit()
        try:
            applied = _applied(connection)
            known_versions = {migration.version for migration in migrations}
            unknown_contract = [
                version
                for version, record in applied.items()
                if version not in known_versions
                and str(record.get("phase") or "contract") == "contract"
            ]
            if unknown_contract:
                raise MigrationDriftError(
                    f"database contains unknown contract migrations: {unknown_contract}"
                )
            _assert_application_order(
                migrations,
                applied,
                allow_out_of_order=allow_out_of_order,
            )
            for migration in migrations:
                record = applied.get(migration.version)
                if record is not None:
                    if not _checksum_is_accepted(migration, record["checksum"]):
                        raise MigrationDriftError(
                            f"migration checksum drift for {migration.version}"
                        )
                    continue

                started = perf_counter()
                if migration.transactional:
                    with connection.transaction():
                        connection.execute(migration.sql, prepare=False)
                        elapsed_ms = (perf_counter() - started) * 1000.0
                        _record_migration(connection, migration, elapsed_ms)
                else:
                    statements = [part.strip() for part in migration.sql.split(";") if part.strip()]
                    if len(statements) != 1:
                        raise MigrationError(
                            f"non-transactional migration {migration.version} must contain one statement"
                        )
                    # End the read transaction left by _applied(); the
                    # session advisory lock is kept. CREATE INDEX CONCURRENTLY
                    # cannot run inside a transaction block.
                    connection.commit()
                    previous = connection.autocommit
                    connection.autocommit = True
                    try:
                        connection.execute(statements[0], prepare=False)
                    finally:
                        connection.autocommit = previous
                    elapsed_ms = (perf_counter() - started) * 1000.0
                    with connection.transaction():
                        _record_migration(connection, migration, elapsed_ms)
                applied_now.append(migration.version)
                applied[migration.version] = {
                    "checksum": migration.checksum,
                    "phase": migration.phase,
                    "transactional": migration.transactional,
                }
        finally:
            try:
                connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))
                connection.commit()
            except Exception:
                logger.debug("Could not release migration advisory lock", exc_info=True)

    status = migration_status(db, root=root)
    status["applied_now"] = applied_now
    assert_schema_compatible(status)
    return status
