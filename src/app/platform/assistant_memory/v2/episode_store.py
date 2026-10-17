from __future__ import annotations

import json
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import Episode, MemorySpaceKey, VisibilityScope


class EpisodeStoreError(RuntimeError):
    pass


class EpisodeEvidenceError(EpisodeStoreError):
    pass


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _episode_from_row(row: Any, observation_ids: tuple[str, ...], assertion_ids: tuple[str, ...]) -> Episode:
    scopes = tuple(VisibilityScope.model_validate(item) for item in row[4])
    return Episode(
        episode_id=str(row[0]),
        space=MemorySpaceKey(principal_id=str(row[1]), owner_type=cast(Any, str(row[2])), owner_id=str(row[3])),
        visibility_scopes=scopes,
        title=str(row[5]),
        summary=str(row[6]),
        started_at=row[7],
        ended_at=row[8],
        participant_entity_ids=tuple(str(item) for item in row[9]),
        observation_ids=observation_ids,
        generated_assertion_ids=assertion_ids,
        importance=float(row[10]),
        derivation_version=str(row[11]),
        revision=int(row[12]),
    )


class PostgresMemoryV2EpisodeStore:
    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    @staticmethod
    def _validate_observations(connection: Any, episode: Episode) -> None:
        ids = tuple(dict.fromkeys(episode.observation_ids))
        rows = connection.execute(
            """
            SELECT o.observation_id,
                   COALESCE(d.state, 'active') AS disposition_state
              FROM omnix_memory_v2_observations o
              LEFT JOIN omnix_memory_v2_observation_dispositions d
                ON d.observation_id = o.observation_id
             WHERE o.principal_id = %s AND o.owner_type = %s AND o.owner_id = %s
               AND o.observation_id = ANY(%s)
             FOR UPDATE OF o
            """,
            (*_space_values(episode.space), list(ids)),
        ).fetchall()
        states = {str(row[0]): str(row[1]) for row in rows}
        missing = set(ids) - set(states)
        if missing:
            raise EpisodeEvidenceError(f"episode observation evidence not found in memory space: {sorted(missing)}")
        inactive = sorted(observation_id for observation_id, state in states.items() if state != "active")
        if inactive:
            raise EpisodeEvidenceError(f"episode observation evidence is inactive: {inactive}")

    @staticmethod
    def _validate_assertions(connection: Any, episode: Episode) -> None:
        ids = tuple(dict.fromkeys(episode.generated_assertion_ids))
        if not ids:
            return
        rows = connection.execute(
            """
            SELECT assertion_id
              FROM omnix_memory_v2_graph_assertions
             WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
               AND assertion_id = ANY(%s)
            """,
            (*_space_values(episode.space), list(ids)),
        ).fetchall()
        found = {str(row[0]) for row in rows}
        missing = set(ids) - found
        if missing:
            raise EpisodeEvidenceError(f"episode generated assertions not found in memory space: {sorted(missing)}")

    def put(self, episode: Episode) -> Episode:
        values = _space_values(episode.space)
        observation_ids = tuple(dict.fromkeys(episode.observation_ids))
        assertion_ids = tuple(dict.fromkeys(episode.generated_assertion_ids))
        with self.database.transaction() as connection:
            self._validate_observations(connection, episode)
            self._validate_assertions(connection, episode)
            connection.execute(
                """
                INSERT INTO omnix_memory_v2_episodes (
                    episode_id, principal_id, owner_type, owner_id, visibility_scopes,
                    title, summary, started_at, ended_at, participant_entity_ids,
                    importance, derivation_version, revision
                ) VALUES (
                    %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
                )
                ON CONFLICT (episode_id) DO UPDATE SET
                    visibility_scopes = EXCLUDED.visibility_scopes,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    started_at = EXCLUDED.started_at,
                    ended_at = EXCLUDED.ended_at,
                    participant_entity_ids = EXCLUDED.participant_entity_ids,
                    importance = EXCLUDED.importance,
                    derivation_version = EXCLUDED.derivation_version,
                    revision = EXCLUDED.revision,
                    updated_at = CURRENT_TIMESTAMP
                WHERE omnix_memory_v2_episodes.principal_id = EXCLUDED.principal_id
                  AND omnix_memory_v2_episodes.owner_type = EXCLUDED.owner_type
                  AND omnix_memory_v2_episodes.owner_id = EXCLUDED.owner_id
                """,
                (
                    episode.episode_id,
                    *values,
                    _json([scope.model_dump(mode="json") for scope in episode.visibility_scopes]),
                    episode.title,
                    episode.summary,
                    episode.started_at,
                    episode.ended_at,
                    _json(episode.participant_entity_ids),
                    episode.importance,
                    episode.derivation_version,
                    episode.revision,
                ),
            )
            owner = connection.execute(
                """
                SELECT principal_id, owner_type, owner_id
                  FROM omnix_memory_v2_episodes
                 WHERE episode_id = %s
                """,
                (episode.episode_id,),
            ).fetchone()
            if owner is None or tuple(str(item) for item in owner) != values:
                raise EpisodeStoreError("episode_id already belongs to another memory space")
            connection.execute(
                "DELETE FROM omnix_memory_v2_episode_observations WHERE episode_id = %s",
                (episode.episode_id,),
            )
            connection.execute(
                "DELETE FROM omnix_memory_v2_episode_assertions WHERE episode_id = %s",
                (episode.episode_id,),
            )
            for observation_id in observation_ids:
                connection.execute(
                    """
                    INSERT INTO omnix_memory_v2_episode_observations (
                        episode_id, observation_id, principal_id, owner_type, owner_id
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (episode.episode_id, observation_id, *values),
                )
            for assertion_id in assertion_ids:
                connection.execute(
                    """
                    INSERT INTO omnix_memory_v2_episode_assertions (
                        episode_id, assertion_id, principal_id, owner_type, owner_id
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (episode.episode_id, assertion_id, *values),
                )
        stored = self.get(episode.space, episode.episode_id)
        if stored is None:  # pragma: no cover - transaction invariant
            raise EpisodeStoreError("episode insert committed without a readable row")
        return stored

    def get(self, space: MemorySpaceKey, episode_id: str) -> Episode | None:
        values = _space_values(space)
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT episode_id, principal_id, owner_type, owner_id, visibility_scopes,
                       title, summary, started_at, ended_at, participant_entity_ids,
                       importance, derivation_version, revision
                  FROM omnix_memory_v2_episodes
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND episode_id = %s
                """,
                (*values, episode_id),
            ).fetchone()
            if row is None:
                return None
            observation_ids = tuple(
                str(item[0])
                for item in connection.execute(
                    """
                    SELECT observation_id
                      FROM omnix_memory_v2_episode_observations
                     WHERE episode_id = %s
                     ORDER BY observation_id
                    """,
                    (episode_id,),
                ).fetchall()
            )
            assertion_ids = tuple(
                str(item[0])
                for item in connection.execute(
                    """
                    SELECT assertion_id
                      FROM omnix_memory_v2_episode_assertions
                     WHERE episode_id = %s
                     ORDER BY assertion_id
                    """,
                    (episode_id,),
                ).fetchall()
            )
        return _episode_from_row(row, observation_ids, assertion_ids)

    def list(self, space: MemorySpaceKey, *, limit: int = 100) -> list[Episode]:
        with self.database.transaction() as connection:
            episode_ids = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT episode_id
                      FROM omnix_memory_v2_episodes
                     WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                     ORDER BY started_at DESC, episode_id
                     LIMIT %s
                    """,
                    (*_space_values(space), max(1, int(limit))),
                ).fetchall()
            ]
        return [episode for episode_id in episode_ids if (episode := self.get(space, episode_id)) is not None]
