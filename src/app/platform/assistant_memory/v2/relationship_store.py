from __future__ import annotations

from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import (
    GraphEntityRef,
    MemorySpaceKey,
    RelationshipMetric,
    RelationshipState,
)


class RelationshipStoreError(RuntimeError):
    pass


class RelationshipEvidenceError(RelationshipStoreError):
    pass


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


class PostgresMemoryV2RelationshipStore:
    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    @staticmethod
    def _validate_active_evidence(connection: Any, state: RelationshipState) -> None:
        ids = tuple(
            dict.fromkeys(
                (*state.evidence_observation_ids, *(item for metric in state.metrics for item in metric.evidence_observation_ids))
            )
        )
        rows = connection.execute(
            """
            SELECT o.observation_id, COALESCE(d.state, 'active')
              FROM omnix_memory_v2_observations o
              LEFT JOIN omnix_memory_v2_observation_dispositions d
                ON d.observation_id = o.observation_id
             WHERE o.principal_id = %s AND o.owner_type = %s AND o.owner_id = %s
               AND o.observation_id = ANY(%s)
             FOR UPDATE OF o
            """,
            (*_space_values(state.space), list(ids)),
        ).fetchall()
        found = {str(row[0]): str(row[1]) for row in rows}
        missing = set(ids) - set(found)
        if missing:
            raise RelationshipEvidenceError(
                f"relationship evidence not found in memory space: {sorted(missing)}"
            )
        inactive = sorted(observation_id for observation_id, disposition in found.items() if disposition != "active")
        if inactive:
            raise RelationshipEvidenceError(f"relationship evidence is inactive: {inactive}")

    def put(self, state: RelationshipState) -> RelationshipState:
        values = _space_values(state.space)
        with self.database.transaction() as connection:
            self._validate_active_evidence(connection, state)
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_relationships (
                    relationship_id, principal_id, owner_type, owner_id,
                    subject_entity_id, subject_entity_type,
                    counterpart_entity_id, counterpart_entity_type,
                    prompt_interpretation, derivation_version, status, revision
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (relationship_id) DO UPDATE SET
                    subject_entity_id = EXCLUDED.subject_entity_id,
                    subject_entity_type = EXCLUDED.subject_entity_type,
                    counterpart_entity_id = EXCLUDED.counterpart_entity_id,
                    counterpart_entity_type = EXCLUDED.counterpart_entity_type,
                    prompt_interpretation = EXCLUDED.prompt_interpretation,
                    derivation_version = EXCLUDED.derivation_version,
                    status = EXCLUDED.status,
                    revision = EXCLUDED.revision,
                    updated_at = CURRENT_TIMESTAMP
                WHERE omnix_memory_v2_relationships.principal_id = EXCLUDED.principal_id
                  AND omnix_memory_v2_relationships.owner_type = EXCLUDED.owner_type
                  AND omnix_memory_v2_relationships.owner_id = EXCLUDED.owner_id
                """,
                (
                    state.relationship_id,
                    *values,
                    state.subject.entity_id,
                    state.subject.entity_type,
                    state.counterpart.entity_id,
                    state.counterpart.entity_type,
                    state.prompt_interpretation,
                    state.derivation_version,
                    state.status,
                    state.revision,
                ),
            )
            owner = connection.execute(
                """
                SELECT principal_id, owner_type, owner_id
                  FROM omnix_memory_v2_relationships
                 WHERE relationship_id = %s
                """,
                (state.relationship_id,),
            ).fetchone()
            if owner is None or tuple(str(item) for item in owner) != values:
                raise RelationshipStoreError("relationship_id already belongs to another memory space")

            connection.execute(
                "DELETE FROM omnix_memory_v2_relationship_metric_evidence WHERE relationship_id = %s",
                (state.relationship_id,),
            )
            connection.execute(
                "DELETE FROM omnix_memory_v2_relationship_evidence WHERE relationship_id = %s",
                (state.relationship_id,),
            )
            connection.execute(
                "DELETE FROM omnix_memory_v2_relationship_metrics WHERE relationship_id = %s",
                (state.relationship_id,),
            )
            for metric in state.metrics:
                connection.execute(
                    """
                    INSERT INTO omnix_memory_v2_relationship_metrics (
                        relationship_id, metric_name, principal_id, owner_type, owner_id,
                        metric_value, confidence
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        state.relationship_id,
                        metric.name,
                        *values,
                        metric.value,
                        metric.confidence,
                    ),
                )
                for observation_id in tuple(dict.fromkeys(metric.evidence_observation_ids)):
                    connection.execute(
                        """
                        INSERT INTO omnix_memory_v2_relationship_metric_evidence (
                            relationship_id, metric_name, observation_id,
                            principal_id, owner_type, owner_id
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (state.relationship_id, metric.name, observation_id, *values),
                    )
            for observation_id in tuple(dict.fromkeys(state.evidence_observation_ids)):
                connection.execute(
                    """
                    INSERT INTO omnix_memory_v2_relationship_evidence (
                        relationship_id, observation_id, principal_id, owner_type, owner_id
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (state.relationship_id, observation_id, *values),
                )
        stored = self.get(state.space, state.relationship_id)
        if stored is None:  # pragma: no cover - transaction invariant
            raise RelationshipStoreError("relationship insert committed without a readable row")
        return stored

    def get(self, space: MemorySpaceKey, relationship_id: str) -> RelationshipState | None:
        values = _space_values(space)
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT relationship_id, subject_entity_id, subject_entity_type,
                       counterpart_entity_id, counterpart_entity_type,
                       prompt_interpretation, derivation_version, status, revision
                  FROM omnix_memory_v2_relationships
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND relationship_id = %s
                """,
                (*values, relationship_id),
            ).fetchone()
            if row is None:
                return None
            evidence_ids = tuple(
                str(item[0])
                for item in connection.execute(
                    """
                    SELECT observation_id
                      FROM omnix_memory_v2_relationship_evidence
                     WHERE relationship_id = %s
                     ORDER BY observation_id
                    """,
                    (relationship_id,),
                ).fetchall()
            )
            metric_rows = connection.execute(
                """
                SELECT metric_name, metric_value, confidence
                  FROM omnix_memory_v2_relationship_metrics
                 WHERE relationship_id = %s
                 ORDER BY metric_name
                """,
                (relationship_id,),
            ).fetchall()
            metrics = []
            for metric_row in metric_rows:
                metric_name = str(metric_row[0])
                metric_evidence = tuple(
                    str(item[0])
                    for item in connection.execute(
                        """
                        SELECT observation_id
                          FROM omnix_memory_v2_relationship_metric_evidence
                         WHERE relationship_id = %s AND metric_name = %s
                         ORDER BY observation_id
                        """,
                        (relationship_id, metric_name),
                    ).fetchall()
                )
                metrics.append(
                    RelationshipMetric(
                        name=metric_name,
                        value=float(metric_row[1]),
                        confidence=float(metric_row[2]),
                        evidence_observation_ids=metric_evidence,
                    )
                )
        return RelationshipState(
            relationship_id=str(row[0]),
            space=space,
            subject=GraphEntityRef(entity_id=str(row[1]), entity_type=str(row[2])),
            counterpart=GraphEntityRef(entity_id=str(row[3]), entity_type=str(row[4])),
            metrics=tuple(metrics),
            prompt_interpretation=str(row[5]),
            evidence_observation_ids=evidence_ids,
            derivation_version=str(row[6]),
            status=cast(Any, str(row[7])),
            revision=int(row[8]),
        )

    def list(self, space: MemorySpaceKey, *, status: str | None = "active", limit: int = 100) -> list[RelationshipState]:
        params: list[Any] = [*_space_values(space)]
        status_sql = ""
        if status is not None:
            status_sql = " AND status = %s"
            params.append(status)
        params.append(max(1, int(limit)))
        with self.database.transaction() as connection:
            ids = [
                str(row[0])
                for row in connection.execute(
                    f"""
                    SELECT relationship_id
                      FROM omnix_memory_v2_relationships
                     WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                     {status_sql}
                     ORDER BY relationship_id
                     LIMIT %s
                    """,
                    tuple(params),
                ).fetchall()
            ]
        return [state for relationship_id in ids if (state := self.get(space, relationship_id)) is not None]
