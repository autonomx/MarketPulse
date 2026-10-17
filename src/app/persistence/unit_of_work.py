from __future__ import annotations

import sys
import uuid
import logging
from types import TracebackType
from typing import Any, Callable, Hashable, Literal, cast

from .background_authority import require_background_owner
from .authority import (
    AuthorityOperation,
    require_authority_operation,
)
from .asset_repository import (
    PostgresAssetRepository,
    PostgresSecretReferenceRepository,
    PostgresSettingsRepository,
)
from .audit import PostgresAuditRepository
from .database import PostgresDatabase, default_database
from .job_repository import PostgresJobRepository
from .outbox_repository import (
    PostgresOutboxConsumerRepository,
    PostgresOutboxRepository,
    PostgresSideEffectRepository,
)
from .identity_service import PostgresIdentityRepository
from .repositories import PostgresIdempotencyRepository
from .repository_registry import repository_spec, repository_spec_by_alias
from app.runtime.statement_class import apply_statement_class
from .transaction_policy import TransactionPolicy, default_sleep, is_retryable_transaction_error, transaction_scope


class UnitOfWorkClosedError(RuntimeError):
    pass


class PostgresUnitOfWork:
    """One explicit transaction shared by all repositories in an operation."""

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        authority_operation: AuthorityOperation = AuthorityOperation.RUNTIME_MUTATION,
        job_priority_aging_seconds: int | None = None,
    ) -> None:
        from app.config.runtime import configured_job_priority_aging_seconds

        self.database = database or default_database()
        self.authority_operation = authority_operation
        self.job_priority_aging_seconds = (
            configured_job_priority_aging_seconds()
            if job_priority_aging_seconds is None
            else max(1, int(job_priority_aging_seconds))
        )
        # The open psycopg connection while the unit is active (None outside it).
        self.connection: Any = None
        self.identities: PostgresIdentityRepository
        self.audit: PostgresAuditRepository
        self.idempotency: PostgresIdempotencyRepository
        self.assets: PostgresAssetRepository
        self.settings: PostgresSettingsRepository
        self.secret_references: PostgresSecretReferenceRepository
        self.jobs: PostgresJobRepository
        self.outbox: PostgresOutboxRepository
        self.outbox_consumers: PostgresOutboxConsumerRepository
        self.side_effects: PostgresSideEffectRepository
        self._connection_context: Any | None = None
        self._transaction_scope_context: Any | None = None
        self._completed = False
        self._after_commit: list[Callable[[], Any]] = []
        self._committed = False
        self._feature_repositories: dict[Hashable, Any] = {}

    def __enter__(self) -> "PostgresUnitOfWork":
        if self.connection is not None:
            raise RuntimeError("Unit of Work cannot be entered twice")
        self._connection_context = self.database.connection()
        self.connection = self._connection_context.__enter__()
        try:
            require_authority_operation(self.connection, self.authority_operation)
            # The work's transaction holds its background owner's fencing epoch (WP-8.3).
            require_background_owner(self.connection, hold=True)
            settings = getattr(self.database, "settings", None)
            if settings is not None:
                apply_statement_class(self.connection, settings.statement_timeout_ms)
        except BaseException:
            context, self._connection_context = self._connection_context, None
            self.connection = None
            if context is not None:
                context.__exit__(*sys.exc_info())
            raise
        self._transaction_scope_context = transaction_scope()
        self._transaction_scope_context.__enter__()
        self.identities = PostgresIdentityRepository(self.connection)
        self.audit = PostgresAuditRepository(self.connection)
        self.idempotency = PostgresIdempotencyRepository(self.connection)
        self.assets = PostgresAssetRepository(self.connection)
        self.settings = PostgresSettingsRepository(self.connection)
        self.secret_references = PostgresSecretReferenceRepository(self.connection)
        self.jobs = PostgresJobRepository(
            self.connection,
            priority_aging_seconds=self.job_priority_aging_seconds,
        )
        self.outbox = PostgresOutboxRepository(self.connection)
        self.outbox_consumers = PostgresOutboxConsumerRepository(self.connection)
        self.side_effects = PostgresSideEffectRepository(self.connection)
        return self

    def repository(self, repo_type: Hashable) -> Any:
        connection = self._require_connection()
        if repo_type in self._feature_repositories:
            return self._feature_repositories[repo_type]
        spec = repository_spec(repo_type)
        if spec is None:
            raise KeyError(f"repository is not registered: {repo_type!r}")
        value = spec.factory(connection)
        self._feature_repositories[repo_type] = value
        return value

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        spec = repository_spec_by_alias(name)
        if spec is None:
            raise AttributeError(name)
        return self.repository(spec.type)

    def commit(self) -> None:
        connection = self._require_connection()
        connection.commit()
        self._completed = True
        self._committed = True

    def rollback(self) -> None:
        connection = self._require_connection()
        connection.rollback()
        self._completed = True
        self._committed = False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        connection = self._require_connection()
        try:
            if exc_type is not None or not self._completed:
                connection.rollback()
        finally:
            transaction_context, self._transaction_scope_context = (
                self._transaction_scope_context,
                None,
            )
            context, self._connection_context = self._connection_context, None
            self.connection = None
            self._completed = True
            if transaction_context is not None:
                transaction_context.__exit__(exc_type, exc, traceback)
            if context is not None:
                context.__exit__(exc_type, exc, traceback)
        if self._committed and exc_type is None:
            for callback in self._after_commit:
                try:
                    callback()
                except Exception:
                    logging.getLogger(__name__).exception('Post-commit maintenance failed')
        return False

    def _require_connection(self) -> Any:
        if self.connection is None:
            raise UnitOfWorkClosedError("Unit of Work is not active")
        return self.connection


def unit_of_work(
    database: PostgresDatabase | None = None,
    *,
    authority_operation: AuthorityOperation = AuthorityOperation.RUNTIME_MUTATION,
    job_priority_aging_seconds: int | None = None,
) -> PostgresUnitOfWork:
    from .transaction_binding import shared_work

    resolved = database or default_database()
    parent = shared_work(resolved)
    if parent is not None:
        if parent.authority_operation != authority_operation:
            raise RuntimeError('A shared transaction cannot change its authority operation')
        # The joined unit forwards everything but commit and rollback to its parent.
        return cast(PostgresUnitOfWork, _JoinedUnitOfWork(parent))
    return PostgresUnitOfWork(
        resolved,
        authority_operation=authority_operation,
        job_priority_aging_seconds=job_priority_aging_seconds,
    )


class _JoinedUnitOfWork:
    """Nested repository operation: commit releases a savepoint, never the root."""

    def __init__(self, parent: PostgresUnitOfWork | _JoinedUnitOfWork) -> None:
        self.parent = parent
        self.name = f'omnix_join_{uuid.uuid4().hex}'
        self.completed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.parent, name)

    def __enter__(self) -> _JoinedUnitOfWork:
        self.parent._require_connection().execute(f'SAVEPOINT {self.name}')
        self.callback_count = len(self.parent._after_commit)
        return self

    def commit(self) -> None:
        self.completed = True

    def rollback(self) -> None:
        self.parent._require_connection().execute(f'ROLLBACK TO SAVEPOINT {self.name}')
        del self.parent._after_commit[self.callback_count:]
        self.completed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        connection = self.parent._require_connection()
        if exc_type is not None or not self.completed:
            connection.execute(f'ROLLBACK TO SAVEPOINT {self.name}')
            del self.parent._after_commit[self.callback_count:]
        connection.execute(f'RELEASE SAVEPOINT {self.name}')
        return False


def run_unit_of_work(
    database: PostgresDatabase | None,
    operation: Callable[[Any], Any],
    *,
    policy: TransactionPolicy | None = None,
    sleep: Callable[[float], None] = default_sleep,
) -> Any:
    """Run ``operation`` in a unit of work, retrying deadlocks and serialization failures (WP-5.10).

    ``operation`` may run more than once; it must only touch the database
    through ``work``. The unit of work is committed when it returns. Inside
    a shared transaction it runs once: a deadlock aborts the outer
    transaction, which only its owner can retry.
    """
    from .transaction_binding import shared_work

    resolved = policy or TransactionPolicy()
    attempts = 1 if shared_work(database or default_database()) is not None else resolved.max_attempts
    for attempt in range(1, attempts + 1):
        try:
            with unit_of_work(database) as work:
                result = operation(work)
                work.commit()
                return result
        except Exception as exc:
            if attempt >= attempts or not is_retryable_transaction_error(exc):
                raise
            delay = resolved.delay_for_attempt(attempt)
            if delay:
                sleep(delay)
    raise AssertionError("unit of work retry loop exhausted without returning or raising")
