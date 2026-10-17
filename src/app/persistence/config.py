from __future__ import annotations

from dataclasses import dataclass
from app.caching.bounded_cache import bounded_lru_cache
from urllib.parse import urlparse

from app.config.env import env_int, env_str


class DatabaseConfigurationError(ValueError):
    """Raised when authoritative persistence configuration is unsafe or invalid."""


def _integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        return env_int(name, default, minimum=minimum, maximum=maximum)
    except ValueError as exc:
        raise DatabaseConfigurationError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    url: str
    pool_min: int = 1
    pool_max: int = 10
    connect_timeout_seconds: int = 5
    statement_timeout_ms: int = 30_000
    lock_timeout_ms: int = 5_000
    transaction_max_attempts: int = 3
    transaction_retry_base_ms: int = 25
    application_name: str = "omnix"

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"postgresql", "postgres"}:
            raise DatabaseConfigurationError(
                "OMNIX_DATABASE_URL must use postgresql://; SQLite is not a supported runtime backend"
            )
        if not parsed.hostname:
            raise DatabaseConfigurationError("OMNIX_DATABASE_URL must include a host")
        if not parsed.path or parsed.path == "/":
            raise DatabaseConfigurationError("OMNIX_DATABASE_URL must include a database name")
        if self.pool_min < 0:
            raise DatabaseConfigurationError("pool_min cannot be negative")
        if self.pool_max < 1 or self.pool_max < self.pool_min:
            raise DatabaseConfigurationError("pool_max must be positive and >= pool_min")
        if self.connect_timeout_seconds < 1:
            raise DatabaseConfigurationError("connect timeout must be positive")
        if self.statement_timeout_ms < 100:
            raise DatabaseConfigurationError("statement timeout must be at least 100ms")
        if self.lock_timeout_ms < 100:
            raise DatabaseConfigurationError("lock timeout must be at least 100ms")
        if self.lock_timeout_ms > self.statement_timeout_ms:
            raise DatabaseConfigurationError("lock timeout cannot exceed statement timeout")
        if not 1 <= self.transaction_max_attempts <= 10:
            raise DatabaseConfigurationError("transaction_max_attempts must be between 1 and 10")
        if not 0 <= self.transaction_retry_base_ms <= 10_000:
            raise DatabaseConfigurationError(
                "transaction_retry_base_ms must be between 0 and 10000"
            )

    @property
    def redacted_url(self) -> str:
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        database = parsed.path.lstrip("/")
        username = parsed.username or ""
        user = f"{username}:***@" if username else ""
        return f"postgresql://{user}{host}{port}/{database}"


# Default pool size per process role (WP-5.10); OMNIX_DATABASE_POOL_MAX overrides.
ROLE_POOL_MAX = {"api": 10, "worker": 10, "job-worker": 5, "scheduler": 3}
# Connections a process opens outside its pool: the background or scheduler
# lock holder and the LISTEN connections for live events and run streams.
DEDICATED_CONNECTIONS_PER_PROCESS = 3


def process_role() -> str:
    return (env_str("OMNIX_GATEWAY_BACKGROUND_ROLE", "worker") or "worker").strip().lower()


def role_pool_max(role: str) -> int:
    return ROLE_POOL_MAX.get(role, 10)


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def database_settings() -> DatabaseSettings:
    database_url = (env_str("OMNIX_DATABASE_URL", "") or "").strip()
    if not database_url:
        raise DatabaseConfigurationError(
            "OMNIX_DATABASE_URL must be configured; start Omnix through the "
            "credential-aware launcher or provide an explicit PostgreSQL URL"
        )
    pool_min = _integer("OMNIX_DATABASE_POOL_MIN", 1, minimum=0, maximum=100)
    pool_max = _integer("OMNIX_DATABASE_POOL_MAX", role_pool_max(process_role()), minimum=1, maximum=200)
    statement_timeout_ms = _integer(
        "OMNIX_DATABASE_STATEMENT_TIMEOUT", 30_000, minimum=100, maximum=3_600_000
    )
    return DatabaseSettings(
        url=database_url,
        pool_min=pool_min,
        pool_max=pool_max,
        connect_timeout_seconds=_integer(
            "OMNIX_DATABASE_CONNECT_TIMEOUT", 5, minimum=1, maximum=120
        ),
        statement_timeout_ms=statement_timeout_ms,
        lock_timeout_ms=_integer(
            "OMNIX_DATABASE_LOCK_TIMEOUT", 5_000, minimum=100, maximum=statement_timeout_ms
        ),
        transaction_max_attempts=_integer(
            "OMNIX_DATABASE_TRANSACTION_MAX_ATTEMPTS", 3, minimum=1, maximum=10
        ),
        transaction_retry_base_ms=_integer(
            "OMNIX_DATABASE_TRANSACTION_RETRY_BASE_MS", 25, minimum=0, maximum=10_000
        ),
        application_name=(env_str("OMNIX_DATABASE_APPLICATION_NAME", "omnix") or "omnix").strip()
        or "omnix",
    )


def reset_database_settings_cache() -> None:
    database_settings.cache_clear()


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def migration_database_settings() -> DatabaseSettings:
    """Return DDL-owner settings, falling back to the runtime URL for local installs."""
    migration_url = (env_str("OMNIX_MIGRATION_DATABASE_URL", "") or "").strip()
    if not migration_url:
        return database_settings()
    runtime = database_settings()
    return DatabaseSettings(
        url=migration_url,
        pool_min=0,
        pool_max=max(1, min(runtime.pool_max, 2)),
        connect_timeout_seconds=runtime.connect_timeout_seconds,
        statement_timeout_ms=max(runtime.statement_timeout_ms, 300_000),
        lock_timeout_ms=runtime.lock_timeout_ms,
        transaction_max_attempts=runtime.transaction_max_attempts,
        transaction_retry_base_ms=runtime.transaction_retry_base_ms,
        application_name="omnix-migrator",
    )


def reset_migration_database_settings_cache() -> None:
    migration_database_settings.cache_clear()
