from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.persistence.database import PostgresDatabase, default_database

from .contracts import ConsolidationReceipt, GraphAssertion, MemorySpaceKey, Observation
from .graph_store import PostgresMemoryV2GraphStore
from .observation_store import PostgresMemoryV2ObservationStore


class ConsolidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ConsolidationPlan:
    assertions: tuple[GraphAssertion, ...] = ()
    created_episode_ids: tuple[str, ...] = ()
    relationship_update_ids: tuple[str, ...] = ()
    affect_update_ids: tuple[str, ...] = ()


ConsolidationProjector = Callable[
    [MemorySpaceKey, tuple[Observation, ...], tuple[GraphAssertion, ...]],
    ConsolidationPlan,
]


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _receipt_key(
    space: MemorySpaceKey,
    start: int,
    through: int,
    version: str,
    schema_version: str,
    observations: tuple[Observation, ...],
) -> str:
    material = {
        "space": space.model_dump(mode="json"),
        "from": start,
        "through": through,
        "consolidator_version": version,
        "schema_version": schema_version,
        "observation_digests": [item.content_digest for item in observations],
    }
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _receipt_from_row(space: MemorySpaceKey, row: Any) -> ConsolidationReceipt:
    return ConsolidationReceipt(
        receipt_id=str(row[0]),
        space=space,
        input_observation_from=int(row[1]),
        input_observation_through=int(row[2]),
        consolidator_version=str(row[3]),
        schema_version=str(row[4]),
        provider_id=str(row[5]) if row[5] is not None else None,
        model_id=str(row[6]) if row[6] is not None else None,
        created_assertion_ids=tuple(str(item) for item in row[7]),
        reinforced_assertion_ids=tuple(str(item) for item in row[8]),
        superseded_assertion_ids=tuple(str(item) for item in row[9]),
        retracted_assertion_ids=tuple(str(item) for item in row[10]),
        conflicted_assertion_ids=tuple(str(item) for item in row[11]),
        created_episode_ids=tuple(str(item) for item in row[12]),
        relationship_update_ids=tuple(str(item) for item in row[13]),
        affect_update_ids=tuple(str(item) for item in row[14]),
        resulting_graph_revision=int(row[15]),
        idempotency_key=str(row[16]),
        started_at=row[17],
        completed_at=row[18],
    )


_RECEIPT_COLUMNS = """
receipt_id, input_observation_from, input_observation_through,
consolidator_version, schema_version, provider_id, model_id,
created_assertion_ids, reinforced_assertion_ids, superseded_assertion_ids,
retracted_assertion_ids, conflicted_assertion_ids, created_episode_ids,
relationship_update_ids, affect_update_ids, resulting_graph_revision,
idempotency_key, started_at, completed_at
"""


class PostgresMemoryV2Consolidator:
    """Apply one deterministic Observation -> Memory Graph window atomically."""

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        graph_store: PostgresMemoryV2GraphStore | None = None,
        observation_store: PostgresMemoryV2ObservationStore | None = None,
    ) -> None:
        self.database = database or default_database()
        self.graph_store = graph_store or PostgresMemoryV2GraphStore(self.database)
        self.observation_store = observation_store or PostgresMemoryV2ObservationStore(self.database)

    @staticmethod
    def _ensure_and_lock_state(connection: Any, space: MemorySpaceKey) -> int:
        values = _space_values(space)
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_consolidation_state (
                principal_id, owner_type, owner_id, consolidation_watermark
            ) VALUES (%s, %s, %s, 0)
            ON CONFLICT (principal_id, owner_type, owner_id) DO NOTHING
            """,
            values,
        )
        row = connection.execute(
            """
            SELECT consolidation_watermark
              FROM omnix_memory_v2_consolidation_state
             WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
             FOR UPDATE
            """,
            values,
        ).fetchone()
        if row is None:  # pragma: no cover - database invariant
            raise ConsolidationError("failed to establish consolidation state")
        return int(row[0])

    def watermark(self, space: MemorySpaceKey) -> int:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT consolidation_watermark
                  FROM omnix_memory_v2_consolidation_state
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def latest_receipt(self, space: MemorySpaceKey) -> ConsolidationReceipt | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                SELECT {_RECEIPT_COLUMNS}
                  FROM omnix_memory_v2_consolidation_receipts
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 ORDER BY input_observation_through DESC, completed_at DESC
                 LIMIT 1
                """,
                _space_values(space),
            ).fetchone()
        return _receipt_from_row(space, row) if row is not None else None

    def consolidate(
        self,
        space: MemorySpaceKey,
        projector: ConsolidationProjector,
        *,
        consolidator_version: str,
        schema_version: str = "memory-v2-consolidation@1",
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> ConsolidationReceipt | None:
        started_at = datetime.now(timezone.utc)
        values = _space_values(space)
        with self.database.transaction() as connection:
            stream = connection.execute(
                """
                SELECT observation_watermark
                  FROM omnix_memory_v2_authority_streams
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 FOR UPDATE
                """,
                values,
            ).fetchone()
            if stream is None:
                return None
            observation_watermark = int(stream[0])
            consolidation_watermark = self._ensure_and_lock_state(connection, space)
            if consolidation_watermark > observation_watermark:
                raise ConsolidationError("consolidation watermark exceeds Observation authority")
            if consolidation_watermark == observation_watermark:
                return None

            graph_state = self.graph_store._ensure_and_lock_state(connection, space)
            start = consolidation_watermark + 1
            connection.execute(
                """
                SELECT observation_id
                  FROM omnix_memory_v2_observations
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND authority_sequence >= %s AND authority_sequence <= %s
                 ORDER BY authority_sequence
                 FOR UPDATE
                """,
                (*values, start, observation_watermark),
            ).fetchall()
            observations = tuple(
                self.observation_store.list(
                    space,
                    after_sequence=consolidation_watermark,
                    through_sequence=observation_watermark,
                    limit=100_000,
                )
            )
            existing = tuple(self.graph_store.list_assertions(space))
            plan = projector(space, observations, existing)
            if any(item.space != space for item in plan.assertions):
                raise ConsolidationError("consolidator emitted assertion for another memory space")

            graph_revision = graph_state.graph_revision + 1
            previous_ids = {item.assertion_id for item in existing}
            for assertion in plan.assertions:
                self.graph_store._write_assertion(connection, assertion, graph_revision)

            connection.execute(
                """
                UPDATE omnix_memory_v2_graph_state
                   SET graph_revision = %s,
                       source_observation_watermark = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (graph_revision, observation_watermark, *values),
            )

            assertions = tuple(sorted(plan.assertions, key=lambda item: item.assertion_id))
            created = tuple(item.assertion_id for item in assertions if item.assertion_id not in previous_ids)
            reinforced = tuple(
                item.assertion_id
                for item in assertions
                if item.assertion_id in previous_ids and item.status == "active"
            )
            superseded = tuple(item.assertion_id for item in assertions if item.status == "superseded")
            retracted = tuple(item.assertion_id for item in assertions if item.status == "retracted")
            conflicted = tuple(item.assertion_id for item in assertions if item.status == "disputed")
            idempotency_key = _receipt_key(
                space,
                start,
                observation_watermark,
                consolidator_version,
                schema_version,
                observations,
            )
            receipt_id = f"consolidation:{idempotency_key}"
            completed_at = datetime.now(timezone.utc)
            row = connection.execute(
                f"""
                INSERT INTO omnix_memory_v2_consolidation_receipts (
                    receipt_id, principal_id, owner_type, owner_id,
                    input_observation_from, input_observation_through,
                    consolidator_version, schema_version, provider_id, model_id,
                    created_assertion_ids, reinforced_assertion_ids,
                    superseded_assertion_ids, retracted_assertion_ids,
                    conflicted_assertion_ids, created_episode_ids,
                    relationship_update_ids, affect_update_ids,
                    resulting_graph_revision, idempotency_key, started_at, completed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s
                )
                RETURNING {_RECEIPT_COLUMNS}
                """,
                (
                    receipt_id,
                    *values,
                    start,
                    observation_watermark,
                    consolidator_version,
                    schema_version,
                    provider_id,
                    model_id,
                    _canonical_json(created),
                    _canonical_json(reinforced),
                    _canonical_json(superseded),
                    _canonical_json(retracted),
                    _canonical_json(conflicted),
                    _canonical_json(tuple(sorted(plan.created_episode_ids))),
                    _canonical_json(tuple(sorted(plan.relationship_update_ids))),
                    _canonical_json(tuple(sorted(plan.affect_update_ids))),
                    graph_revision,
                    idempotency_key,
                    started_at,
                    completed_at,
                ),
            ).fetchone()
            connection.execute(
                """
                UPDATE omnix_memory_v2_consolidation_state
                   SET consolidation_watermark = %s,
                       last_receipt_id = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (observation_watermark, receipt_id, *values),
            )
            if row is None:  # pragma: no cover
                raise ConsolidationError("consolidation receipt insert returned no row")
            return _receipt_from_row(space, row)
