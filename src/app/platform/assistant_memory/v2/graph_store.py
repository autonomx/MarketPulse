from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import (
    GraphAssertion,
    GraphEntityRef,
    GraphValue,
    MemorySpaceKey,
    Observation,
    VisibilityScope,
)
from .observation_store import PostgresMemoryV2ObservationStore


class GraphStoreError(RuntimeError):
    pass


class GraphEvidenceError(GraphStoreError):
    pass


@dataclass(frozen=True, slots=True)
class GraphState:
    graph_revision: int
    source_observation_watermark: int


@dataclass(frozen=True, slots=True)
class GraphReplayReport:
    matches: bool
    persisted_digest: str
    replay_digest: str
    persisted_count: int
    replay_count: int
    observation_watermark: int
    graph_revision: int


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _scopes(assertion: GraphAssertion) -> str:
    return _json([scope.model_dump(mode="json") for scope in assertion.visibility_scopes])


def _object_value(assertion: GraphAssertion) -> str:
    return _json(assertion.object.model_dump(mode="json"))


def _canonical_assertion(assertion: GraphAssertion) -> dict[str, Any]:
    return {
        "assertion_id": assertion.assertion_id,
        "space": assertion.space.model_dump(mode="json"),
        "visibility_scopes": [item.model_dump(mode="json") for item in assertion.visibility_scopes],
        "subject": assertion.subject.model_dump(mode="json"),
        "predicate": assertion.predicate,
        "object": assertion.object.model_dump(mode="json"),
        "domain": assertion.domain,
        "assertion_type": assertion.assertion_type,
        "confidence": assertion.confidence,
        "valid_from": assertion.valid_from.isoformat() if assertion.valid_from else None,
        "valid_until": assertion.valid_until.isoformat() if assertion.valid_until else None,
        "evidence_observation_ids": sorted(assertion.evidence_observation_ids),
        "evidence_assertion_ids": sorted(assertion.evidence_assertion_ids),
        "derivation_version": assertion.derivation_version,
        "supersedes": sorted(assertion.supersedes),
        "contradicted_by": sorted(assertion.contradicted_by),
        "status": assertion.status,
    }


def canonical_graph_digest(assertions: Iterable[GraphAssertion]) -> str:
    payload = sorted(
        (_canonical_assertion(item) for item in assertions),
        key=lambda item: item["assertion_id"],
    )
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


class PostgresMemoryV2GraphStore:
    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    @staticmethod
    def _ensure_and_lock_state(connection: Any, space: MemorySpaceKey) -> GraphState:
        values = _space_values(space)
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_graph_state (
                principal_id, owner_type, owner_id, graph_revision, source_observation_watermark
            ) VALUES (%s, %s, %s, 0, 0)
            ON CONFLICT (principal_id, owner_type, owner_id) DO NOTHING
            """,
            values,
        )
        row = connection.execute(
            """
            SELECT graph_revision, source_observation_watermark
              FROM omnix_memory_v2_graph_state
             WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
             FOR UPDATE
            """,
            values,
        ).fetchone()
        if row is None:  # pragma: no cover
            raise GraphStoreError("failed to establish Memory v2 graph state")
        return GraphState(int(row[0]), int(row[1]))

    @staticmethod
    def _assert_observation_evidence(connection: Any, assertion: GraphAssertion) -> None:
        if not assertion.evidence_observation_ids:
            return
        values = _space_values(assertion.space)
        rows = connection.execute(
            """
            SELECT o.observation_id, COALESCE(d.state, 'active')
              FROM omnix_memory_v2_observations o
              LEFT JOIN omnix_memory_v2_observation_dispositions d
                ON d.observation_id = o.observation_id
             WHERE o.principal_id = %s AND o.owner_type = %s AND o.owner_id = %s
               AND o.observation_id = ANY(%s)
            """,
            (*values, list(assertion.evidence_observation_ids)),
        ).fetchall()
        states = {str(row[0]): str(row[1]) for row in rows}
        missing = set(assertion.evidence_observation_ids) - set(states)
        if missing:
            raise GraphEvidenceError(f"observation evidence not found in assertion space: {sorted(missing)}")
        inactive = sorted(key for key, state in states.items() if state != "active")
        if inactive and assertion.status == "active":
            raise GraphEvidenceError(f"active assertion cannot depend on inactive evidence: {inactive}")

    @staticmethod
    def _assert_assertion_evidence(connection: Any, assertion: GraphAssertion) -> None:
        refs = set(assertion.evidence_assertion_ids) | set(assertion.supersedes) | set(assertion.contradicted_by)
        if not refs:
            return
        values = _space_values(assertion.space)
        rows = connection.execute(
            """
            SELECT assertion_id
              FROM omnix_memory_v2_graph_assertions
             WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
               AND assertion_id = ANY(%s)
            """,
            (*values, list(refs)),
        ).fetchall()
        found = {str(row[0]) for row in rows}
        missing = refs - found
        if missing:
            raise GraphEvidenceError(f"assertion references not found in assertion space: {sorted(missing)}")

    @staticmethod
    def _upsert_entity(connection: Any, space: MemorySpaceKey, entity: GraphEntityRef) -> None:
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_graph_entities (
                principal_id, owner_type, owner_id, entity_id, entity_type
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (principal_id, owner_type, owner_id, entity_id) DO UPDATE
               SET entity_type = EXCLUDED.entity_type, updated_at = CURRENT_TIMESTAMP
            """,
            (*_space_values(space), entity.entity_id, entity.entity_type),
        )

    def _write_assertion(self, connection: Any, assertion: GraphAssertion, graph_revision: int) -> None:
        self._assert_observation_evidence(connection, assertion)
        self._assert_assertion_evidence(connection, assertion)
        self._upsert_entity(connection, assertion.space, assertion.subject)
        if assertion.object.kind == "entity" and assertion.object.entity is not None:
            self._upsert_entity(connection, assertion.space, assertion.object.entity)
        values = _space_values(assertion.space)
        connection.execute(
            """
            INSERT INTO omnix_memory_v2_graph_assertions (
                assertion_id, principal_id, owner_type, owner_id, visibility_scopes,
                subject_entity_id, subject_entity_type, predicate, object_value, domain,
                assertion_type, confidence, valid_from, valid_until, derivation_version,
                status, revision, graph_revision
            ) VALUES (
                %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (assertion_id) DO UPDATE
               SET visibility_scopes = EXCLUDED.visibility_scopes,
                   subject_entity_id = EXCLUDED.subject_entity_id,
                   subject_entity_type = EXCLUDED.subject_entity_type,
                   predicate = EXCLUDED.predicate,
                   object_value = EXCLUDED.object_value,
                   domain = EXCLUDED.domain,
                   assertion_type = EXCLUDED.assertion_type,
                   confidence = EXCLUDED.confidence,
                   valid_from = EXCLUDED.valid_from,
                   valid_until = EXCLUDED.valid_until,
                   derivation_version = EXCLUDED.derivation_version,
                   status = EXCLUDED.status,
                   revision = EXCLUDED.revision,
                   graph_revision = EXCLUDED.graph_revision,
                   updated_at = CURRENT_TIMESTAMP
             WHERE omnix_memory_v2_graph_assertions.principal_id = EXCLUDED.principal_id
               AND omnix_memory_v2_graph_assertions.owner_type = EXCLUDED.owner_type
               AND omnix_memory_v2_graph_assertions.owner_id = EXCLUDED.owner_id
            """,
            (
                assertion.assertion_id,
                *values,
                _scopes(assertion),
                assertion.subject.entity_id,
                assertion.subject.entity_type,
                assertion.predicate,
                _object_value(assertion),
                assertion.domain,
                assertion.assertion_type,
                assertion.confidence,
                assertion.valid_from,
                assertion.valid_until,
                assertion.derivation_version,
                assertion.status,
                assertion.revision,
                graph_revision,
            ),
        )
        connection.execute(
            "DELETE FROM omnix_memory_v2_assertion_observation_evidence WHERE assertion_id = %s",
            (assertion.assertion_id,),
        )
        for observation_id in assertion.evidence_observation_ids:
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_assertion_observation_evidence (
                    assertion_id, observation_id, principal_id, owner_type, owner_id
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (assertion.assertion_id, observation_id, *values),
            )
        connection.execute(
            "DELETE FROM omnix_memory_v2_assertion_assertion_evidence WHERE assertion_id = %s",
            (assertion.assertion_id,),
        )
        for evidence_id in assertion.evidence_assertion_ids:
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_assertion_assertion_evidence (
                    assertion_id, evidence_assertion_id, principal_id, owner_type, owner_id
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (assertion.assertion_id, evidence_id, *values),
            )
        connection.execute(
            "DELETE FROM omnix_memory_v2_assertion_relations WHERE assertion_id = %s",
            (assertion.assertion_id,),
        )
        for related_id in assertion.supersedes:
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_assertion_relations (
                    assertion_id, related_assertion_id, principal_id, owner_type, owner_id, relation_type
                ) VALUES (%s, %s, %s, %s, %s, 'supersedes')
                """,
                (assertion.assertion_id, related_id, *values),
            )
        for related_id in assertion.contradicted_by:
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_assertion_relations (
                    assertion_id, related_assertion_id, principal_id, owner_type, owner_id, relation_type
                ) VALUES (%s, %s, %s, %s, %s, 'contradicted_by')
                """,
                (assertion.assertion_id, related_id, *values),
            )

    def put(self, assertion: GraphAssertion, *, source_observation_watermark: int | None = None) -> int:
        with self.database.transaction() as connection:
            state = self._ensure_and_lock_state(connection, assertion.space)
            graph_revision = state.graph_revision + 1
            self._write_assertion(connection, assertion, graph_revision)
            source_watermark = (
                state.source_observation_watermark
                if source_observation_watermark is None
                else max(state.source_observation_watermark, int(source_observation_watermark))
            )
            connection.execute(
                """
                UPDATE omnix_memory_v2_graph_state
                   SET graph_revision = %s, source_observation_watermark = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (graph_revision, source_watermark, *_space_values(assertion.space)),
            )
        return graph_revision

    def replace_space(
        self,
        space: MemorySpaceKey,
        assertions: Iterable[GraphAssertion],
        *,
        source_observation_watermark: int,
    ) -> int:
        items = tuple(assertions)
        if any(item.space != space for item in items):
            raise GraphStoreError("replacement graph contains assertion from another memory space")
        with self.database.transaction() as connection:
            state = self._ensure_and_lock_state(connection, space)
            graph_revision = state.graph_revision + 1
            values = _space_values(space)
            connection.execute(
                """
                DELETE FROM omnix_memory_v2_graph_assertions
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                values,
            )
            pending = list(items)
            written: set[str] = set()
            while pending:
                progressed = False
                for item in pending[:]:
                    dependencies = set(item.evidence_assertion_ids) | set(item.supersedes) | set(item.contradicted_by)
                    if dependencies - written:
                        continue
                    self._write_assertion(connection, item, graph_revision)
                    written.add(item.assertion_id)
                    pending.remove(item)
                    progressed = True
                if not progressed:
                    unresolved = sorted(item.assertion_id for item in pending)
                    raise GraphEvidenceError(f"cyclic or unresolved assertion dependencies: {unresolved}")
            connection.execute(
                """
                UPDATE omnix_memory_v2_graph_state
                   SET graph_revision = %s, source_observation_watermark = %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                (graph_revision, int(source_observation_watermark), *values),
            )
        return graph_revision

    def state(self, space: MemorySpaceKey) -> GraphState:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT graph_revision, source_observation_watermark
                  FROM omnix_memory_v2_graph_state
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
        return GraphState(0, 0) if row is None else GraphState(int(row[0]), int(row[1]))

    def list_assertions(
        self,
        space: MemorySpaceKey,
        *,
        statuses: tuple[str, ...] = (),
        domains: tuple[str, ...] = (),
        as_of: datetime | None = None,
    ) -> list[GraphAssertion]:
        conditions = ["a.principal_id = %s", "a.owner_type = %s", "a.owner_id = %s"]
        params: list[Any] = list(_space_values(space))
        if statuses:
            conditions.append("a.status = ANY(%s)")
            params.append(list(statuses))
        if domains:
            conditions.append("a.domain = ANY(%s)")
            params.append(list(domains))
        if as_of is not None:
            conditions.extend(["(a.valid_from IS NULL OR a.valid_from <= %s)", "(a.valid_until IS NULL OR a.valid_until > %s)"])
            params.extend((as_of, as_of))
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT a.assertion_id, a.visibility_scopes, a.subject_entity_id,
                       a.subject_entity_type, a.predicate, a.object_value, a.domain,
                       a.assertion_type, a.confidence, a.valid_from, a.valid_until,
                       a.derivation_version, a.status, a.revision
                  FROM omnix_memory_v2_graph_assertions a
                 WHERE {' AND '.join(conditions)}
                 ORDER BY a.assertion_id
                """,
                tuple(params),
            ).fetchall()
            result: list[GraphAssertion] = []
            for row in rows:
                assertion_id = str(row[0])
                observation_rows = connection.execute(
                    "SELECT observation_id FROM omnix_memory_v2_assertion_observation_evidence WHERE assertion_id = %s ORDER BY observation_id",
                    (assertion_id,),
                ).fetchall()
                assertion_rows = connection.execute(
                    "SELECT evidence_assertion_id FROM omnix_memory_v2_assertion_assertion_evidence WHERE assertion_id = %s ORDER BY evidence_assertion_id",
                    (assertion_id,),
                ).fetchall()
                relation_rows = connection.execute(
                    "SELECT related_assertion_id, relation_type FROM omnix_memory_v2_assertion_relations WHERE assertion_id = %s ORDER BY relation_type, related_assertion_id",
                    (assertion_id,),
                ).fetchall()
                supersedes = tuple(str(item[0]) for item in relation_rows if item[1] == "supersedes")
                contradicted_by = tuple(str(item[0]) for item in relation_rows if item[1] == "contradicted_by")
                object_data = dict(row[5])
                result.append(
                    GraphAssertion(
                        assertion_id=assertion_id,
                        space=space,
                        visibility_scopes=tuple(VisibilityScope(**item) for item in row[1]),
                        subject=GraphEntityRef(entity_id=str(row[2]), entity_type=str(row[3])),
                        predicate=str(row[4]),
                        object=GraphValue(**object_data),
                        domain=cast(Any, str(row[6])),
                        assertion_type=cast(Any, str(row[7])),
                        confidence=float(row[8]),
                        valid_from=row[9],
                        valid_until=row[10],
                        evidence_observation_ids=tuple(str(item[0]) for item in observation_rows),
                        evidence_assertion_ids=tuple(str(item[0]) for item in assertion_rows),
                        derivation_version=str(row[11]),
                        supersedes=supersedes,
                        contradicted_by=contradicted_by,
                        status=cast(Any, str(row[12])),
                        revision=int(row[13]),
                    )
                )
        return result

    def dependent_assertion_ids(self, space: MemorySpaceKey, observation_id: str) -> tuple[str, ...]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT e.assertion_id
                  FROM omnix_memory_v2_assertion_observation_evidence e
                 WHERE e.principal_id = %s AND e.owner_type = %s AND e.owner_id = %s
                   AND e.observation_id = %s
                 ORDER BY e.assertion_id
                """,
                (*_space_values(space), observation_id),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)


Projector = Callable[[tuple[Observation, ...]], Iterable[GraphAssertion]]


class GraphReplayValidator:
    def __init__(
        self,
        graph_store: PostgresMemoryV2GraphStore,
        observation_store: PostgresMemoryV2ObservationStore,
    ) -> None:
        self.graph_store = graph_store
        self.observation_store = observation_store

    def expected(self, space: MemorySpaceKey, projector: Projector) -> tuple[GraphAssertion, ...]:
        observations = tuple(self.observation_store.list(space, limit=100_000))
        assertions = tuple(projector(observations))
        if any(item.space != space for item in assertions):
            raise GraphStoreError("replay projector emitted assertion for another memory space")
        return assertions

    def validate(self, space: MemorySpaceKey, projector: Projector) -> GraphReplayReport:
        expected = self.expected(space, projector)
        persisted = tuple(self.graph_store.list_assertions(space))
        state = self.graph_store.state(space)
        replay_digest = canonical_graph_digest(expected)
        persisted_digest = canonical_graph_digest(persisted)
        return GraphReplayReport(
            matches=replay_digest == persisted_digest,
            persisted_digest=persisted_digest,
            replay_digest=replay_digest,
            persisted_count=len(persisted),
            replay_count=len(expected),
            observation_watermark=self.observation_store.watermark(space),
            graph_revision=state.graph_revision,
        )

    def rebuild(self, space: MemorySpaceKey, projector: Projector) -> GraphReplayReport:
        expected = self.expected(space, projector)
        watermark = self.observation_store.watermark(space)
        self.graph_store.replace_space(space, expected, source_observation_watermark=watermark)
        return self.validate(space, projector)
