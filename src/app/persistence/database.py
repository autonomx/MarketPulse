from __future__ import annotations

from contextlib import contextmanager
from app.caching.bounded_cache import bounded_lru_cache
from time import perf_counter
from typing import Any, Iterator

from .config import DatabaseSettings, database_settings
from .transaction_policy import transaction_scope


class PostgresOperationError(RuntimeError):
    """Typed PostgreSQL operation failure with safe diagnostic metadata."""

    def __init__(
        self,
        message: str,
        *,
        sqlstate: str | None = None,
        error_class: str = "postgres_operation",
        table: str | None = None,
        constraint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.error_class = error_class
        self.table = table
        self.constraint = constraint


class DatabaseUnavailableError(PostgresOperationError):
    """Raised only when the authoritative PostgreSQL service cannot be reached."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(
            message,
            sqlstate=sqlstate,
            error_class="database_unavailable",
        )


class PostgresRetryableTransactionError(PostgresOperationError):
    pass


class PostgresLockTimeoutError(PostgresOperationError):
    pass


class PostgresStatementTimeoutError(PostgresOperationError):
    pass


class PostgresConstraintError(PostgresOperationError):
    pass


def _diagnostic_value(error: Exception, name: str) -> str | None:
    diagnostic = getattr(error, "diag", None)
    value = getattr(diagnostic, name, None) if diagnostic is not None else None
    text = str(value or "").strip()
    return text or None


def _classified_postgres_error(error: Exception) -> PostgresOperationError:
    sqlstate = str(getattr(error, "sqlstate", None) or "").strip() or None
    table = _diagnostic_value(error, "table_name")
    constraint = _diagnostic_value(error, "constraint_name")
    primary = _diagnostic_value(error, "message_primary") or str(error)
    message = f"PostgreSQL operation failed: {type(error).__name__}: {primary}"
    module = error.__class__.__module__
    name = error.__class__.__name__

    if (sqlstate and sqlstate.startswith("08")) or (
        not sqlstate
        and module.startswith("psycopg")
        and name in {"OperationalError", "InterfaceError"}
    ):
        return DatabaseUnavailableError(message, sqlstate=sqlstate)
    if sqlstate in {"40001", "40P01"}:
        return PostgresRetryableTransactionError(
            message,
            sqlstate=sqlstate,
            error_class="transaction_retryable",
            table=table,
            constraint=constraint,
        )
    if sqlstate == "55P03":
        return PostgresLockTimeoutError(
            message,
            sqlstate=sqlstate,
            error_class="lock_timeout",
            table=table,
            constraint=constraint,
        )
    if sqlstate == "57014":
        return PostgresStatementTimeoutError(
            message,
            sqlstate=sqlstate,
            error_class="statement_timeout",
            table=table,
            constraint=constraint,
        )
    if sqlstate and sqlstate.startswith("23"):
        return PostgresConstraintError(
            message,
            sqlstate=sqlstate,
            error_class="constraint_error",
            table=table,
            constraint=constraint,
        )
    return PostgresOperationError(
        message,
        sqlstate=sqlstate,
        error_class="postgres_operation",
        table=table,
        constraint=constraint,
    )


class PostgresDatabase:
    """Lazy synchronous PostgreSQL connection pool shared by application services."""

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings = settings or database_settings()
        self._pool: Any | None = None

    def open(self, *, wait: bool = True) -> None:
        if self._pool is not None:
            return
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise RuntimeError(
                "PostgreSQL dependencies are missing; install psycopg[binary] and psycopg-pool"
            ) from exc

        def configure(connection: Any) -> None:
            self._configure_connection(connection)

        self._pool = ConnectionPool(
            conninfo=self.settings.url,
            min_size=self.settings.pool_min,
            max_size=self.settings.pool_max,
            timeout=float(self.settings.connect_timeout_seconds),
            kwargs={
                "autocommit": False,
                "connect_timeout": self.settings.connect_timeout_seconds,
                "application_name": self.settings.application_name,
            },
            configure=configure,
            open=True,
        )
        if wait:
            try:
                self._pool.wait(timeout=float(self.settings.connect_timeout_seconds))
            except Exception as exc:
                self.close()
                classified = _classified_postgres_error(exc)
                if classified.sqlstate is not None:
                    raise classified from exc
                raise DatabaseUnavailableError(
                    f"PostgreSQL is unavailable at {self.settings.redacted_url}",
                ) from exc

    def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        from app.observability.metrics import record_db_connection_hold
        from app.runtime.statement_class import current_statement_class

        from .transaction_binding import shared_work
        from .background_authority import require_background_owner
        from .tenant_scope import apply_session_scope

        work = shared_work(self)
        if work is not None:
            # The shared transaction was fenced when it checked out its connection.
            yield work.connection
            return
        self.open()
        assert self._pool is not None
        try:
            with self._pool.connection() as connection:
                held = perf_counter()
                try:
                    # Row-level security follows the tenant of this checkout (WP-4.4).
                    apply_session_scope(connection)
                    # A background worker's transaction carries its owner's
                    # fencing epoch (WP-8.3).
                    require_background_owner(connection)
                    yield connection
                finally:
                    record_db_connection_hold(current_statement_class(), perf_counter() - held)
        except Exception as exc:
            if exc.__class__.__module__.startswith("psycopg"):
                raise _classified_postgres_error(exc) from exc
            raise

    def _configure_connection(self, connection: Any) -> None:
        """Session defaults, set once per physical connection (one round trip).

        Transactions then need no preamble: BEGIN carries the isolation level
        and the timeouts are already the session's (WP-5.10).
        """
        import psycopg

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('TimeZone', 'UTC', false), "
                "set_config('statement_timeout', %s, false), "
                "set_config('lock_timeout', %s, false)",
                (str(self.settings.statement_timeout_ms), str(self.settings.lock_timeout_ms)),
            )
        connection.commit()
        connection.isolation_level = psycopg.IsolationLevel.READ_COMMITTED

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        from app.runtime.statement_class import apply_statement_class

        from .background_authority import require_background_owner

        with self.connection() as connection:
            with connection.transaction():
                apply_statement_class(connection, self.settings.statement_timeout_ms)
                # The transaction holds its background owner's fencing epoch (WP-8.3).
                require_background_owner(connection, hold=True)
                with transaction_scope():
                    yield connection

    @contextmanager
    def dedicated_connection(self) -> Iterator[Any]:
        """A connection outside the pool, for session-long holders such as advisory locks."""
        import psycopg

        from .tenant_scope import apply_session_scope

        try:
            connection = psycopg.connect(
                self.settings.url,
                autocommit=False,
                connect_timeout=self.settings.connect_timeout_seconds,
                application_name=self.settings.application_name,
            )
        except Exception as exc:
            if exc.__class__.__module__.startswith("psycopg"):
                raise _classified_postgres_error(exc) from exc
            raise
        try:
            self._configure_connection(connection)
            apply_session_scope(connection)
            yield connection
        finally:
            connection.close()

    def health(self) -> dict[str, Any]:
        started = perf_counter()
        try:
            with self.connection() as connection:
                row = connection.execute(
                    "SELECT current_database(), current_user, "
                    "current_setting('server_version_num')::integer"
                ).fetchone()
        except Exception as exc:
            return {
                "ok": False,
                "backend": "postgresql",
                "database_url": self.settings.redacted_url,
                "latency_ms": round((perf_counter() - started) * 1000.0, 3),
                "error": exc.__class__.__name__,
                "error_class": getattr(exc, "error_class", None),
                "sqlstate": getattr(exc, "sqlstate", None),
            }
        return {
            "ok": True,
            "backend": "postgresql",
            "database_url": self.settings.redacted_url,
            "database": str(row[0]),
            "user": str(row[1]),
            "server_version_num": int(row[2]),
            "latency_ms": round((perf_counter() - started) * 1000.0, 3),
        }

    def pool_statistics(self) -> dict[str, Any]:
        """Return pool counters without opening a pool or exposing its DSN."""
        if self._pool is None:
            return {}
        stats = dict(self._pool.get_stats())
        stats['pool_used'] = max(0, stats.get('pool_size', 0) - stats.get('pool_available', 0))
        return stats


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0, on_evict=lambda database: database.close())
def default_database() -> PostgresDatabase:
    return PostgresDatabase(database_settings())


def close_default_database() -> None:
    if default_database.cache_info().currsize:
        default_database().close()
    default_database.cache_clear()
