from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from app.persistence.database import PostgresDatabase, default_database

from .authority import AuthorityEpochState, PostgresMemoryV2AuthorityStore
from .contracts import (
    MemorySpaceKey,
    Observation,
    RetrievalQuery,
    RetrievalResult,
)
from .derived_state import PostgresMemoryV2DerivedStateStore
from .episode_store import PostgresMemoryV2EpisodeStore
from .federated_retrieval import FederatedMemoryV2Retriever
from .grant_store import PostgresMemoryV2GrantStore
from .graph_store import PostgresMemoryV2GraphStore
from .observation_store import (
    ObservationAppendRequest,
    ObservationIdempotencyConflict,
    ObservationStoreError,
    PostgresMemoryV2ObservationStore,
    _canonical_json,
    _observation_from_row,
    _space_values,
    observation_content_digest,
)
from .relationship_store import PostgresMemoryV2RelationshipStore
from .retrieval import UnifiedMemoryV2Retriever
from .search_index import PostgresMemoryV2SearchIndex




def _embedding_index(database: PostgresDatabase):
    # Imported on use: the embedding stack is not needed to compose the gateway.
    from .embedding_index import PostgresMemoryV2EmbeddingIndex

    return PostgresMemoryV2EmbeddingIndex(database)

class MemoryV2RuntimeError(RuntimeError):
    pass


class MemoryV2NotAuthoritativeError(MemoryV2RuntimeError):
    pass


class AuthoritativeIngestSequenceError(MemoryV2RuntimeError):
    pass



_OBSERVATION_COLUMNS = """
observation_id, principal_id, owner_type, owner_id, authority_sequence,
idempotency_key, visibility_kind, visibility_scope_id, event_type, occurred_at,
provenance, recorded_at, payload, sensitivity, correlation_id, schema_version,
content_digest
"""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class PostgresMemoryV2Runtime:
    """Production authority boundary for canonical Memory v2 reads and writes.

    Canonical writes atomically advance authoritative-event and observation watermarks and
    coalesce a durable derive job. Retrieval uses the revision-aware derived/search path.
    Low-level stores remain available for replay and focused tests but are not the
    production mutation boundary.
    """

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        authority_store: PostgresMemoryV2AuthorityStore | None = None,
    ) -> None:
        self.database = database or default_database()
        self.authority_store = authority_store or PostgresMemoryV2AuthorityStore(self.database)
        self.observation_store = PostgresMemoryV2ObservationStore(self.database)
        self.graph_store = PostgresMemoryV2GraphStore(self.database)
        self.search_index = PostgresMemoryV2SearchIndex(self.database)
        self.derived_store = PostgresMemoryV2DerivedStateStore(self.database)
        self.episode_store = PostgresMemoryV2EpisodeStore(self.database)
        self.relationship_store = PostgresMemoryV2RelationshipStore(self.database)
        self.grant_store = PostgresMemoryV2GrantStore(self.database)
        self.local_retriever = UnifiedMemoryV2Retriever(
            graph_store=self.graph_store,
            observation_store=self.observation_store,
            episode_store=self.episode_store,
            relationship_store=self.relationship_store,
            index_graph_revision_provider=self.search_index.index_graph_revision,
            search_index=self.search_index,
            derived_store=self.derived_store,
            embedding_index=_embedding_index(self.database),
        )
        self.federated_retriever = FederatedMemoryV2Retriever(
            local_retriever=self.local_retriever,
            grant_store=self.grant_store,
        )

    def current(self) -> AuthorityEpochState:
        return self.authority_store.current()

    def assert_v2_authoritative(self) -> AuthorityEpochState:
        state = self.current()
        if state.epoch.authority != "v2":
            raise MemoryV2NotAuthoritativeError(
                "Memory v2 canonical access requires an active v2 authority epoch"
            )
        return state

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.assert_v2_authoritative()
        return self.federated_retriever.retrieve(query)

    @staticmethod
    def _lock_v2_authority(connection: Any) -> int:
        row = connection.execute(
            """
            SELECT c.current_epoch, e.authority
              FROM omnix_memory_v2_authority_current c
              JOIN omnix_memory_v2_authority_epochs e ON e.epoch = c.current_epoch
             WHERE c.singleton = TRUE
             FOR UPDATE OF c
            """
        ).fetchone()
        if row is None:
            raise MemoryV2RuntimeError("current memory authority epoch is missing")
        if str(row[1]) != "v2":
            raise MemoryV2NotAuthoritativeError(
                "Memory v2 canonical writes are disabled while v1 is authoritative"
            )
        return int(row[0])

    def append_authoritative(
        self,
        request: ObservationAppendRequest,
        *,
        authoritative_event_sequence: int,
    ) -> Observation:
        """Backward-compatible alias for an exact-sequence authoritative append."""

        return self.append_authoritative_exact(
            request,
            authoritative_event_sequence=authoritative_event_sequence,
        )

    def append_authoritative_exact(
        self,
        request: ObservationAppendRequest,
        *,
        authoritative_event_sequence: int,
    ) -> Observation:
        """Append one caller-sequenced authoritative event atomically.

        This boundary is intentionally reserved for replay/import producers that already
        own an exact authoritative sequence. Ordinary live producers should use
        ``append_authoritative_next`` so sequence allocation happens inside the same locked
        transaction as idempotency resolution and watermark advancement.
        """

        sequence = int(authoritative_event_sequence)
        if sequence < 1:
            raise AuthoritativeIngestSequenceError(
                "authoritative event sequence must be positive"
            )
        return self._append_authoritative_locked(request, exact_sequence=sequence)

    def append_authoritative_next(
        self,
        request: ObservationAppendRequest,
        *,
        supersede: Callable[[Any], Iterable[str]] | None = None,
        actor_id: str = "memory-v2-runtime",
    ) -> Observation:
        """Atomically allocate and append the next authoritative sequence.

        Idempotency lookup, synchronized stream locking, next-sequence allocation,
        observation insertion, watermark advancement, and derive-job coalescing all happen
        inside one transaction. Competing ordinary producers therefore never calculate or
        retry authority watermarks themselves.

        ``supersede`` runs under those locks before the insert (it may raise to
        abort, e.g. on a revision conflict) and names observations to revoke in
        the same transaction, so a new revision never coexists with its
        predecessor.
        """

        return self._append_authoritative_locked(
            request, exact_sequence=None, supersede=supersede, actor_id=actor_id,
        )

    def govern_authoritative(
        self,
        space: MemorySpaceKey,
        decide: Callable[[Any], Iterable[tuple[str, str, str]]],
        *,
        actor_id: str,
    ) -> int:
        """Apply governance decisions under the v2 authority and stream locks.

        ``decide(connection)`` returns ``(observation_id, state, reason)``
        triples (state ``revoked``, ``purged`` or ``active``); it may raise to
        abort. Returns how many observations changed.
        """
        with self.database.transaction() as connection:
            self._lock_v2_authority(connection)
            PostgresMemoryV2ObservationStore._ensure_and_lock_stream(connection, space)
            changed = 0
            for observation_id, state, reason in decide(connection):
                PostgresMemoryV2ObservationStore.apply_disposition(
                    connection, space, observation_id, state=state, actor_id=actor_id, reason=reason,
                )
                changed += 1
            return changed

    def _append_authoritative_locked(
        self,
        request: ObservationAppendRequest,
        *,
        exact_sequence: int | None,
        supersede: Callable[[Any], Iterable[str]] | None = None,
        actor_id: str = "memory-v2-runtime",
    ) -> Observation:
        digest = observation_content_digest(request)
        values = _space_values(request.space)
        with self.database.transaction() as connection:
            self._lock_v2_authority(connection)
            last_sequence, observation_watermark = (
                PostgresMemoryV2ObservationStore._ensure_and_lock_stream(
                    connection,
                    request.space,
                )
            )
            event_row = connection.execute(
                """
                SELECT authoritative_event_watermark
                  FROM omnix_memory_v2_authority_streams
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                values,
            ).fetchone()
            if event_row is None:  # pragma: no cover - stream invariant
                raise ObservationStoreError("authoritative event stream is missing")
            event_watermark = int(event_row[0])

            existing = connection.execute(
                f"""
                SELECT {_OBSERVATION_COLUMNS}
                  FROM omnix_memory_v2_observations
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND idempotency_key = %s
                """,
                (*values, request.idempotency_key),
            ).fetchone()
            if existing is not None:
                observation = _observation_from_row(existing)
                if observation.content_digest != digest:
                    raise ObservationIdempotencyConflict(
                        "idempotency key already committed with different observation content"
                    )
                if exact_sequence is not None and observation.authority_sequence != exact_sequence:
                    raise AuthoritativeIngestSequenceError(
                        "idempotent observation belongs to a different authoritative sequence"
                    )
                if event_watermark < observation.authority_sequence:
                    raise AuthoritativeIngestSequenceError(
                        "authoritative event watermark trails an already committed observation"
                    )
                return observation

            if not (
                last_sequence == observation_watermark == event_watermark
            ):
                raise AuthoritativeIngestSequenceError(
                    "authoritative event and observation watermarks are not synchronized"
                )

            superseded = tuple(supersede(connection)) if supersede is not None else ()

            expected_previous = event_watermark
            sequence = expected_previous + 1 if exact_sequence is None else exact_sequence
            if exact_sequence is not None and exact_sequence - 1 != expected_previous:
                raise AuthoritativeIngestSequenceError(
                    "authoritative event sequence is not the exact next synchronized sequence"
                )

            observation_id = request.observation_id or f"obs:{__import__('uuid').uuid4()}"
            recorded_at = datetime.now(timezone.utc)
            provenance_json = _canonical_json(request.provenance.model_dump(mode="json"))
            payload_json = _canonical_json(request.payload)
            row = connection.execute(
                f"""
                INSERT INTO omnix_memory_v2_observations (
                    observation_id, principal_id, owner_type, owner_id,
                    authority_sequence, idempotency_key, visibility_kind,
                    visibility_scope_id, event_type, occurred_at, recorded_at,
                    payload, provenance, sensitivity, correlation_id, schema_version,
                    content_digest
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s, %s, %s
                )
                RETURNING {_OBSERVATION_COLUMNS}
                """,
                (
                    observation_id,
                    *values,
                    sequence,
                    request.idempotency_key,
                    request.visibility_scope.kind,
                    request.visibility_scope.scope_id,
                    request.event_type,
                    request.occurred_at,
                    recorded_at,
                    payload_json,
                    provenance_json,
                    request.sensitivity,
                    request.correlation_id,
                    request.schema_version,
                    digest,
                ),
            ).fetchone()
            stream_row = connection.execute(
                """
                UPDATE omnix_memory_v2_authority_streams
                   SET last_sequence = %s,
                       observation_watermark = %s,
                       authoritative_event_watermark = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND last_sequence = %s
                   AND observation_watermark = %s
                   AND authoritative_event_watermark = %s
                RETURNING last_sequence
                """,
                (
                    sequence,
                    sequence,
                    sequence,
                    *values,
                    expected_previous,
                    expected_previous,
                    expected_previous,
                ),
            ).fetchone()
            if row is None or stream_row is None:  # pragma: no cover - locked invariant
                raise ObservationStoreError(
                    "authoritative observation append failed to advance synchronized watermarks"
                )
            PostgresMemoryV2ObservationStore._coalesce_derive_job(
                connection,
                request.space,
                target_observation_watermark=sequence,
            )
            for observation_id in superseded:
                PostgresMemoryV2ObservationStore.apply_disposition(
                    connection,
                    request.space,
                    observation_id,
                    state="revoked",
                    actor_id=actor_id,
                    reason=f"superseded by {row[0]}",
                )
            return _observation_from_row(row)


