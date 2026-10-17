from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from app.caching.bounded_cache import bounded_lru_cache
from typing import Any

from app.config.env import env_bool, env_str
from app.errors import LegacyPersistenceRetired

from .authority import (
    AuthorityOperation,
    PostgresAuthorityError,
    initialize_fresh_install_authority,
    require_authority_operation,
)
from .database import PostgresDatabase, default_database
from .migrations import assert_schema_compatible, migration_status


class PersistenceMode(str, Enum):
    POSTGRESQL = "postgresql"
    LEGACY_TEST = "legacy_test"
    LEGACY_IMPORT = "legacy_import"


class PersistenceReadinessError(RuntimeError):
    """Raised when PostgreSQL is not ready to serve authoritative runtime state."""


@dataclass(frozen=True, slots=True)
class RuntimePersistenceStatus:
    mode: PersistenceMode
    backend: str
    ready: bool
    cutover_mode: str | None
    authority_state: str | None
    migrations_pending: tuple[str, ...]
    details: dict[str, Any]


def _under_pytest() -> bool:
    return "pytest" in sys.modules or bool(env_str("PYTEST_CURRENT_TEST", ""))


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def persistence_mode() -> PersistenceMode:
    raw = (env_str("OMNIX_PERSISTENCE_MODE", "postgresql") or "postgresql").strip().lower()
    try:
        mode = PersistenceMode(raw)
    except ValueError as exc:
        raise PersistenceReadinessError(
            "OMNIX_PERSISTENCE_MODE must be postgresql, legacy_test, or legacy_import"
        ) from exc
    if mode == PersistenceMode.LEGACY_TEST:
        allowed = env_bool("OMNIX_ALLOW_LEGACY_TEST_PERSISTENCE", False)
        if not allowed or not (_under_pytest() or env_bool("CI", False)):
            raise LegacyPersistenceRetired(
                "legacy_test persistence is restricted to explicit CI or pytest execution"
            )
    if mode == PersistenceMode.LEGACY_IMPORT:
        allowed = env_bool("OMNIX_ALLOW_LEGACY_IMPORT", False)
        if not allowed:
            raise LegacyPersistenceRetired(
                "legacy_import persistence requires OMNIX_ALLOW_LEGACY_IMPORT=1"
            )
    return mode


_PERSISTENCE_MODE_CACHE_CLEAR = persistence_mode.cache_clear


def reset_persistence_mode_cache() -> None:
    _PERSISTENCE_MODE_CACHE_CLEAR()


def require_legacy_persistence(*, purpose: str) -> None:
    mode = persistence_mode()
    if mode not in {PersistenceMode.LEGACY_TEST, PersistenceMode.LEGACY_IMPORT}:
        raise LegacyPersistenceRetired(
            f"{purpose} uses retired legacy persistence; run the PostgreSQL migration "
            "or invoke an explicit legacy import/test tool"
        )


def uses_postgresql_runtime() -> bool:
    return persistence_mode() == PersistenceMode.POSTGRESQL


def ensure_postgresql_runtime_ready(
    database: PostgresDatabase | None = None,
    *,
    auto_initialize_fresh_install: bool = False,
    apply_schema_changes: bool = False,
) -> RuntimePersistenceStatus:
    mode = persistence_mode()
    if mode != PersistenceMode.POSTGRESQL:
        return RuntimePersistenceStatus(
            mode=mode,
            backend="legacy",
            ready=True,
            cutover_mode=None,
            authority_state=None,
            migrations_pending=(),
            details={"restricted_mode": True},
        )
    db = database or default_database()
    health = db.health()
    if health.get("ok") is not True:
        raise PersistenceReadinessError("PostgreSQL health check failed")
    if apply_schema_changes:
        raise PersistenceReadinessError(
            "runtime schema mutation is disabled; run python -m app.persistence migrate"
        )
    migrations = migration_status(db, initialize_table=False)
    pending = tuple(str(item) for item in migrations.get("pending") or ())
    try:
        assert_schema_compatible(migrations)
    except Exception as exc:
        raise PersistenceReadinessError(str(exc)) from exc
    if not migrations.get("ok") or pending:
        raise PersistenceReadinessError(
            f"PostgreSQL migration state is not ready: pending={list(pending)}"
        )
    schema_version = str(migrations.get("current_schema") or "unknown-schema")
    software_revision = (
        env_str("OMNIX_SOFTWARE_REVISION", "fresh-install-unversioned")
        or "fresh-install-unversioned"
    ).strip()
    try:
        with db.transaction() as connection:
            if auto_initialize_fresh_install:
                initialize_fresh_install_authority(
                    connection,
                    software_revision=software_revision,
                    schema_version=schema_version,
                )
            policy = require_authority_operation(
                connection,
                AuthorityOperation.RUNTIME_START,
            )
    except PostgresAuthorityError as exc:
        raise PersistenceReadinessError(str(exc)) from exc
    cutover_mode = policy.mode

    with db.connection() as connection:
        runtime = connection.execute(
            """
            SELECT backend, runtime_schema_version, legacy_runtime_enabled, metadata
              FROM omnix_runtime_persistence_state WHERE singleton = TRUE
            """
        ).fetchone()
    if runtime is None or str(runtime[0]) != "postgresql" or bool(runtime[2]):
        raise PersistenceReadinessError("runtime persistence retirement marker is invalid")
    return RuntimePersistenceStatus(
        mode=mode,
        backend="postgresql",
        ready=True,
        cutover_mode=cutover_mode,
        authority_state=policy.authority_state,
        migrations_pending=pending,
        details={
            "health": health,
            "authority_state": policy.authority_state,
            "runtime_schema_version": str(runtime[1]),
            "application_schema_min": migrations.get("application_schema_min"),
            "application_schema_max": migrations.get("application_schema_max"),
            "metadata": dict(runtime[3]),
        },
    )
