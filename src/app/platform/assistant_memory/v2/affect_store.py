from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import AffectObservation, MemorySpaceKey


class AffectStoreError(RuntimeError):
    pass


class AffectEvidenceError(AffectStoreError):
    pass


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _from_row(space: MemorySpaceKey, row: Any) -> AffectObservation:
    return AffectObservation(
        affect_id=str(row[0]),
        space=space,
        source_observation_id=str(row[1]),
        source=cast(Any, str(row[2])),
        observed_at=row[3],
        valence=float(row[4]) if row[4] is not None else None,
        arousal=float(row[5]) if row[5] is not None else None,
        emotion_distribution={str(key): float(value) for key, value in row[6].items()},
        confidence=float(row[7]),
        model_version=str(row[8]),
    )


class PostgresMemoryV2AffectStore:
    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    @staticmethod
    def _validate_source(connection: Any, observation: AffectObservation) -> None:
        row = connection.execute(
            """
            SELECT COALESCE(d.state, 'active')
              FROM omnix_memory_v2_observations o
              LEFT JOIN omnix_memory_v2_observation_dispositions d
                ON d.observation_id = o.observation_id
             WHERE o.principal_id = %s AND o.owner_type = %s AND o.owner_id = %s
               AND o.observation_id = %s
             FOR UPDATE OF o
            """,
            (*_space_values(observation.space), observation.source_observation_id),
        ).fetchone()
        if row is None:
            raise AffectEvidenceError("affect source observation not found in memory space")
        if str(row[0]) != "active":
            raise AffectEvidenceError("affect source observation is inactive")

    def put(self, observation: AffectObservation) -> AffectObservation:
        values = _space_values(observation.space)
        with self.database.transaction() as connection:
            self._validate_source(connection, observation)
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_affect_observations (
                    affect_id, principal_id, owner_type, owner_id, source_observation_id,
                    source, observed_at, valence, arousal, emotion_distribution,
                    confidence, model_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (affect_id) DO UPDATE SET
                    source_observation_id = EXCLUDED.source_observation_id,
                    source = EXCLUDED.source,
                    observed_at = EXCLUDED.observed_at,
                    valence = EXCLUDED.valence,
                    arousal = EXCLUDED.arousal,
                    emotion_distribution = EXCLUDED.emotion_distribution,
                    confidence = EXCLUDED.confidence,
                    model_version = EXCLUDED.model_version
                WHERE omnix_memory_v2_affect_observations.principal_id = EXCLUDED.principal_id
                  AND omnix_memory_v2_affect_observations.owner_type = EXCLUDED.owner_type
                  AND omnix_memory_v2_affect_observations.owner_id = EXCLUDED.owner_id
                """,
                (
                    observation.affect_id,
                    *values,
                    observation.source_observation_id,
                    observation.source,
                    observation.observed_at,
                    observation.valence,
                    observation.arousal,
                    json.dumps(
                        observation.emotion_distribution,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    observation.confidence,
                    observation.model_version,
                ),
            )
            owner = connection.execute(
                """
                SELECT principal_id, owner_type, owner_id
                  FROM omnix_memory_v2_affect_observations
                 WHERE affect_id = %s
                """,
                (observation.affect_id,),
            ).fetchone()
            if owner is None or tuple(str(item) for item in owner) != values:
                raise AffectStoreError("affect_id already belongs to another memory space")
        stored = self.get(observation.space, observation.affect_id)
        if stored is None:  # pragma: no cover
            raise AffectStoreError("affect insert committed without a readable row")
        return stored

    def get(self, space: MemorySpaceKey, affect_id: str) -> AffectObservation | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT affect_id, source_observation_id, source, observed_at,
                       valence, arousal, emotion_distribution, confidence, model_version
                  FROM omnix_memory_v2_affect_observations
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND affect_id = %s
                """,
                (*_space_values(space), affect_id),
            ).fetchone()
        return _from_row(space, row) if row is not None else None

    def current(
        self,
        space: MemorySpaceKey,
        *,
        as_of: datetime,
        freshness_seconds: float,
        source_observation_id: str | None = None,
    ) -> AffectObservation | None:
        params: list[Any] = [*_space_values(space), as_of]
        source_sql = ""
        if source_observation_id is not None:
            source_sql = " AND a.source_observation_id = %s"
            params.append(source_observation_id)
        with self.database.transaction() as connection:
            row = connection.execute(
                f"""
                SELECT a.affect_id, a.source_observation_id, a.source, a.observed_at,
                       a.valence, a.arousal, a.emotion_distribution, a.confidence,
                       a.model_version
                  FROM omnix_memory_v2_affect_observations a
                  JOIN omnix_memory_v2_observations o
                    ON o.observation_id = a.source_observation_id
                   AND o.principal_id = a.principal_id
                   AND o.owner_type = a.owner_type
                   AND o.owner_id = a.owner_id
                  LEFT JOIN omnix_memory_v2_observation_dispositions d
                    ON d.observation_id = o.observation_id
                 WHERE a.principal_id = %s AND a.owner_type = %s AND a.owner_id = %s
                   AND a.observed_at <= %s
                   AND COALESCE(d.state, 'active') = 'active'
                   {source_sql}
                 ORDER BY a.observed_at DESC, a.affect_id
                 LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        if row is None:
            return None
        affect = _from_row(space, row)
        age_seconds = (as_of - affect.observed_at).total_seconds()
        if age_seconds < 0 or age_seconds > max(0.0, float(freshness_seconds)):
            return None
        return affect

    def history(self, space: MemorySpaceKey, *, limit: int = 100) -> list[AffectObservation]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT affect_id, source_observation_id, source, observed_at,
                       valence, arousal, emotion_distribution, confidence, model_version
                  FROM omnix_memory_v2_affect_observations
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 ORDER BY observed_at DESC, affect_id
                 LIMIT %s
                """,
                (*_space_values(space), max(1, int(limit))),
            ).fetchall()
        return [_from_row(space, row) for row in rows]
