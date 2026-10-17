from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import MemorySpaceKey
from .convergence import (
    DerivedPlanner,
    PostgresMemoryV2DerivedCoordinator,
    StaleDerivedPlanError,
)
from .derived_state import PostgresMemoryV2DerivedStateStore
from .observation_store import _space_values
from .search_index import PostgresMemoryV2SearchIndex

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConvergenceLag:
    space: MemorySpaceKey
    observation_watermark: int
    governance_revision: int
    derived_observation_watermark: int
    derived_governance_revision: int
    derived_revision: int
    index_derived_revision: int
    observation_to_derived: int
    governance_to_derived: int
    derived_to_index: int


@dataclass(frozen=True, slots=True)
class ClaimedDeriveJob:
    space: MemorySpaceKey
    target_observation_watermark: int
    target_governance_revision: int
    attempts: int


@dataclass(frozen=True, slots=True)
class ClaimedProjectionJob:
    space: MemorySpaceKey
    target_derived_revision: int
    attempts: int


class PostgresMemoryV2ConvergenceWorker:
    """Durable, coalescing steady-state convergence workers.

    Claim transactions are deliberately short. Derive planning—including arbitrary model
    inference—runs after the job claim transaction is committed. The derived coordinator
    then performs an optimistic atomic commit. Stale plans are immediately requeued; other
    failures use bounded exponential backoff and retain diagnostics in the durable job row.

    A `running` claim is a lease, not a permanent state. Claims older than
    `claim_timeout_seconds` are automatically reclaimable after process death. Every job
    has a finite `max_attempts`; exhausted jobs remain failed and unclaimable until a new
    observation/governance event coalesces the job and resets attempts to zero.

    Production workers normally claim globally with `SKIP LOCKED`. A caller may optionally
    target one MemorySpaceKey for deterministic repair/admin work without changing the
    queue's global steady-state semantics.
    """

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        coordinator: PostgresMemoryV2DerivedCoordinator | None = None,
        search_index: PostgresMemoryV2SearchIndex | None = None,
        derived_store: PostgresMemoryV2DerivedStateStore | None = None,
        max_backoff_seconds: int = 300,
        max_attempts: int = 8,
        claim_timeout_seconds: int = 300,
    ) -> None:
        self.database = database or default_database()
        self.coordinator = coordinator or PostgresMemoryV2DerivedCoordinator(self.database)
        self.search_index = search_index or PostgresMemoryV2SearchIndex(self.database)
        from .embedding_index import PostgresMemoryV2EmbeddingIndex

        self.embedding_index = PostgresMemoryV2EmbeddingIndex(self.database)
        self.derived_store = derived_store or PostgresMemoryV2DerivedStateStore(self.database)
        self.max_backoff_seconds = max(1, int(max_backoff_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.claim_timeout_seconds = max(1, int(claim_timeout_seconds))

    @staticmethod
    def _space(row: Any, offset: int = 0) -> MemorySpaceKey:
        return MemorySpaceKey(
            principal_id=str(row[offset]),
            owner_type=cast(Any, str(row[offset + 1])),
            owner_id=str(row[offset + 2]),
        )

    def _mark_exhausted_stale_claims(self, connection: Any, table: str) -> None:
        if table not in {"omnix_memory_v2_derive_jobs", "omnix_memory_v2_projection_jobs"}:
            raise ValueError("unsupported convergence job table")
        connection.execute(
            f"""
            UPDATE {table}
               SET status = 'failed',
                   last_error = COALESCE(
                       last_error,
                       'claim lease expired after retry limit'
                   ),
                   available_at = CURRENT_TIMESTAMP,
                   claimed_at = NULL,
                   updated_at = CURRENT_TIMESTAMP
             WHERE status = 'running'
               AND attempts >= %s
               AND claimed_at IS NOT NULL
               AND claimed_at <= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
            """,
            (self.max_attempts, self.claim_timeout_seconds),
        )

    def claim_derive_job(
        self,
        space: MemorySpaceKey | None = None,
    ) -> ClaimedDeriveJob | None:
        conditions = [
            "attempts < %s",
            (
                "((status IN ('pending', 'failed') AND available_at <= CURRENT_TIMESTAMP) "
                "OR (status = 'running' AND claimed_at IS NOT NULL "
                "AND claimed_at <= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')))"
            ),
        ]
        params: list[Any] = [self.max_attempts, self.claim_timeout_seconds]
        if space is not None:
            conditions.extend(
                [
                    "principal_id = %s",
                    "owner_type = %s",
                    "owner_id = %s",
                ]
            )
            params.extend(_space_values(space))
        with self.database.transaction() as connection:
            self._mark_exhausted_stale_claims(connection, "omnix_memory_v2_derive_jobs")
            row = connection.execute(
                f"""
                SELECT principal_id, owner_type, owner_id,
                       target_observation_watermark, target_governance_revision, attempts
                  FROM omnix_memory_v2_derive_jobs
                 WHERE {' AND '.join(conditions)}
                 ORDER BY available_at, updated_at, principal_id, owner_type, owner_id
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if row is None:
                return None
            attempts = int(row[5]) + 1
            connection.execute(
                """
                UPDATE omnix_memory_v2_derive_jobs
                   SET status = 'running', attempts = %s, claimed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (attempts, str(row[0]), str(row[1]), str(row[2])),
            )
        return ClaimedDeriveJob(
            space=self._space(row),
            target_observation_watermark=int(row[3]),
            target_governance_revision=int(row[4]),
            attempts=attempts,
        )

    def claim_projection_job(
        self,
        space: MemorySpaceKey | None = None,
    ) -> ClaimedProjectionJob | None:
        conditions = [
            "attempts < %s",
            (
                "((status IN ('pending', 'failed') AND available_at <= CURRENT_TIMESTAMP) "
                "OR (status = 'running' AND claimed_at IS NOT NULL "
                "AND claimed_at <= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')))"
            ),
        ]
        params: list[Any] = [self.max_attempts, self.claim_timeout_seconds]
        if space is not None:
            conditions.extend(
                [
                    "principal_id = %s",
                    "owner_type = %s",
                    "owner_id = %s",
                ]
            )
            params.extend(_space_values(space))
        with self.database.transaction() as connection:
            self._mark_exhausted_stale_claims(
                connection,
                "omnix_memory_v2_projection_jobs",
            )
            row = connection.execute(
                f"""
                SELECT principal_id, owner_type, owner_id,
                       target_derived_revision, attempts
                  FROM omnix_memory_v2_projection_jobs
                 WHERE {' AND '.join(conditions)}
                 ORDER BY available_at, updated_at, principal_id, owner_type, owner_id
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if row is None:
                return None
            attempts = int(row[4]) + 1
            connection.execute(
                """
                UPDATE omnix_memory_v2_projection_jobs
                   SET status = 'running', attempts = %s, claimed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (attempts, str(row[0]), str(row[1]), str(row[2])),
            )
        return ClaimedProjectionJob(
            space=self._space(row),
            target_derived_revision=int(row[3]),
            attempts=attempts,
        )

    def _fail_job(
        self,
        table: str,
        space: MemorySpaceKey,
        *,
        attempts: int,
        error: Exception,
        immediate: bool = False,
    ) -> None:
        if table not in {"omnix_memory_v2_derive_jobs", "omnix_memory_v2_projection_jobs"}:
            raise ValueError("unsupported convergence job table")
        terminal = attempts >= self.max_attempts
        delay = (
            0
            if immediate or terminal
            else min(self.max_backoff_seconds, 2 ** min(attempts, 8))
        )
        message = str(error)[:4000]
        if terminal:
            message = f"terminal after {attempts} attempts: {message}"[:4000]
        with self.database.transaction() as connection:
            connection.execute(
                f"""
                UPDATE {table}
                   SET status = %s,
                       last_error = %s,
                       available_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                       claimed_at = NULL,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (
                    "failed" if terminal else ("pending" if immediate else "failed"),
                    message,
                    delay,
                    *_space_values(space),
                ),
            )

    def derive_once(
        self,
        planner: DerivedPlanner,
        *,
        space: MemorySpaceKey | None = None,
    ) -> bool:
        job = self.claim_derive_job(space)
        if job is None:
            return False
        try:
            prepared = self.coordinator.prepare(job.space, planner)
            if prepared is None:
                with self.database.transaction() as connection:
                    connection.execute(
                        """
                        DELETE FROM omnix_memory_v2_derive_jobs
                         WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                        """,
                        _space_values(job.space),
                    )
                return True
            self.coordinator.commit(prepared)
            return True
        except StaleDerivedPlanError as exc:
            self._fail_job(
                "omnix_memory_v2_derive_jobs",
                job.space,
                attempts=job.attempts,
                error=exc,
                immediate=True,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - persist arbitrary provider/planner failures
            self._fail_job(
                "omnix_memory_v2_derive_jobs",
                job.space,
                attempts=job.attempts,
                error=exc,
            )
            return True

    def project_once(self, *, space: MemorySpaceKey | None = None) -> bool:
        job = self.claim_projection_job(space)
        if job is None:
            return False
        try:
            current = self.derived_store.state(job.space)
            if current.derived_revision < job.target_derived_revision:
                raise RuntimeError("projection target is ahead of canonical derived state")
            self.search_index.rebuild(job.space)
            try:
                self.embedding_index.sync(job.space)
            except Exception:  # noqa: BLE001 - retrieval falls back to words until the next projection
                logger.warning("memory_v2_embedding_sync_failed", exc_info=True)
            return True
        except Exception as exc:  # noqa: BLE001 - persist arbitrary projection failures
            self._fail_job(
                "omnix_memory_v2_projection_jobs",
                job.space,
                attempts=job.attempts,
                error=exc,
            )
            return True

    def lag(self, space: MemorySpaceKey) -> ConvergenceLag:
        with self.database.transaction() as connection:
            stream = connection.execute(
                """
                SELECT observation_watermark, governance_revision
                  FROM omnix_memory_v2_authority_streams
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
        observation = int(stream[0]) if stream is not None else 0
        governance = int(stream[1]) if stream is not None else 0
        derived = self.derived_store.state(space)
        index = self.search_index.state(space)
        return ConvergenceLag(
            space=space,
            observation_watermark=observation,
            governance_revision=governance,
            derived_observation_watermark=derived.source_observation_watermark,
            derived_governance_revision=derived.source_governance_revision,
            derived_revision=derived.derived_revision,
            index_derived_revision=index.index_derived_revision,
            observation_to_derived=max(0, observation - derived.source_observation_watermark),
            governance_to_derived=max(0, governance - derived.source_governance_revision),
            derived_to_index=max(0, derived.derived_revision - index.index_derived_revision),
        )
