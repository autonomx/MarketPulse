from __future__ import annotations

import re

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from app.persistence.database import PostgresDatabase, default_database

from .contracts import DerivedPolicyEnvelope, MemorySpaceKey, VisibilityScope


# Words that carry no meaning for memory lookup ("What is my favorite game?"
# keeps "favorite" and "game"). The index uses the 'simple' configuration, which
# keeps every word, so a question's own words must not be required.
_STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here hers
herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off on
once only or other our ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up very was we were what when where which
while who whom why will with would you your yours yourself yourselves tell remember know please
""".split())
_WORD = re.compile(r"[a-z0-9]+")
_MAX_TERMS = 24


def match_query(text: str) -> str | None:
    """A tsquery that matches any meaningful word of ``text`` (longer words as prefixes).

    ``plainto_tsquery`` required every word, so a natural question matched
    nothing. Words are reduced to ``[a-z0-9]`` before they reach the query,
    so user text cannot inject tsquery syntax.
    """
    terms: list[str] = []
    for word in _WORD.findall(text.casefold()):
        if len(word) < 2 or word in _STOPWORDS or word in terms:
            continue
        terms.append(word)
        if len(terms) == _MAX_TERMS:
            break
    if not terms:
        return None
    return " | ".join(f"{word}:*" if len(word) >= 4 else word for word in terms)


@dataclass(frozen=True, slots=True)
class SearchIndexState:
    index_graph_revision: int
    source_observation_watermark: int
    governance_digest: str
    projection_digest: str
    entry_count: int
    built_at: datetime | None
    index_derived_revision: int = 0


@dataclass(frozen=True, slots=True)
class SearchIndexStatus:
    state: SearchIndexState
    graph_revision: int
    observation_watermark: int
    governance_digest: str
    stale: bool
    reasons: tuple[str, ...]
    derived_revision: int = 0


@dataclass(frozen=True, slots=True)
class SearchIndexHit:
    ref_id: str
    item_type: str
    domain: str
    content: str
    rank: float
    evidence_observation_ids: tuple[str, ...]
    source_revision: int
    confidence: float
    valid_from: datetime | None
    valid_until: datetime | None
    policy: DerivedPolicyEnvelope | None


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _object_text(object_value: dict[str, Any]) -> str:
    if object_value.get("kind") == "entity":
        entity = object_value.get("entity") or {}
        return str(entity.get("entity_id") or "")
    return str(object_value.get("literal"))


def _policy_from_columns(row: Any, start: int) -> DerivedPolicyEnvelope | None:
    if row[start] is None:
        return None
    return DerivedPolicyEnvelope(
        sensitivity=cast(Any, str(row[start])),
        effective_visibility=tuple(
            VisibilityScope.model_validate(item) for item in row[start + 1]
        ),
        trust_class=cast(Any, str(row[start + 2])),
        source_observation_ids=tuple(str(item) for item in row[start + 3]),
        source_assertion_ids=tuple(str(item) for item in row[start + 4]),
        source_governance_revision=int(row[start + 5]),
        policy_version=str(row[start + 6]),
        policy_digest=str(row[start + 7]),
    )


class PostgresMemoryV2SearchIndex:
    """Disposable PostgreSQL candidate projection over canonical derived memory."""

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()

    @staticmethod
    def _governance_digest(connection: Any, space: MemorySpaceKey) -> str:
        rows = connection.execute(
            """
            SELECT observation_id, state, revision
              FROM omnix_memory_v2_observation_dispositions
             WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
             ORDER BY observation_id
            """,
            _space_values(space),
        ).fetchall()
        return _digest([(str(row[0]), str(row[1]), int(row[2])) for row in rows])

    @staticmethod
    def _empty_state() -> SearchIndexState:
        empty = _digest([])
        return SearchIndexState(0, 0, empty, empty, 0, None, 0)

    def state(self, space: MemorySpaceKey) -> SearchIndexState:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT index_graph_revision, source_observation_watermark,
                       governance_digest, projection_digest, entry_count, built_at,
                       index_derived_revision
                  FROM omnix_memory_v2_search_index_state
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
        if row is None:
            return self._empty_state()
        return SearchIndexState(
            index_graph_revision=int(row[0]),
            source_observation_watermark=int(row[1]),
            governance_digest=str(row[2]),
            projection_digest=str(row[3]),
            entry_count=int(row[4]),
            built_at=row[5],
            index_derived_revision=int(row[6]),
        )

    def index_graph_revision(self, space: MemorySpaceKey) -> int:
        return self.state(space).index_graph_revision

    def status(self, space: MemorySpaceKey) -> SearchIndexStatus:
        state = self.state(space)
        with self.database.transaction() as connection:
            graph_row = connection.execute(
                """
                SELECT graph_revision
                  FROM omnix_memory_v2_graph_state
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
            observation_row = connection.execute(
                """
                SELECT observation_watermark
                  FROM omnix_memory_v2_authority_streams
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
            derived_row = connection.execute(
                """
                SELECT derived_revision
                  FROM omnix_memory_v2_derived_state
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                _space_values(space),
            ).fetchone()
            governance_digest = self._governance_digest(connection, space)
        graph_revision = int(graph_row[0]) if graph_row is not None else 0
        observation_watermark = int(observation_row[0]) if observation_row is not None else 0
        derived_revision = int(derived_row[0]) if derived_row is not None else 0
        reasons = []
        if state.index_graph_revision != graph_revision:
            reasons.append("graph_revision_mismatch")
        if state.source_observation_watermark != observation_watermark:
            reasons.append("observation_watermark_mismatch")
        if state.governance_digest != governance_digest:
            reasons.append("governance_changed")
        if state.index_derived_revision != derived_revision:
            reasons.append("derived_revision_mismatch")
        return SearchIndexStatus(
            state=state,
            graph_revision=graph_revision,
            observation_watermark=observation_watermark,
            governance_digest=governance_digest,
            stale=bool(reasons),
            reasons=tuple(reasons),
            derived_revision=derived_revision,
        )

    def rebuild(self, space: MemorySpaceKey) -> SearchIndexState:
        values = _space_values(space)
        with self.database.transaction() as connection:
            graph_row = connection.execute(
                """
                SELECT graph_revision, source_observation_watermark
                  FROM omnix_memory_v2_graph_state
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 FOR UPDATE
                """,
                values,
            ).fetchone()
            authority_row = connection.execute(
                """
                SELECT observation_watermark
                  FROM omnix_memory_v2_authority_streams
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 FOR UPDATE
                """,
                values,
            ).fetchone()
            derived_row = connection.execute(
                """
                SELECT derived_revision
                  FROM omnix_memory_v2_derived_state
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                 FOR UPDATE
                """,
                values,
            ).fetchone()
            graph_revision = int(graph_row[0]) if graph_row is not None else 0
            graph_observation_watermark = int(graph_row[1]) if graph_row is not None else 0
            observation_watermark = int(authority_row[0]) if authority_row is not None else 0
            derived_revision = int(derived_row[0]) if derived_row is not None else 0
            governance_digest = self._governance_digest(connection, space)

            rows = connection.execute(
                """
                SELECT a.assertion_id, a.visibility_scopes, a.subject_entity_id,
                       a.predicate, a.object_value, a.domain, a.revision,
                       COALESCE(
                           jsonb_agg(e.observation_id ORDER BY e.observation_id)
                               FILTER (WHERE e.observation_id IS NOT NULL),
                           '[]'::jsonb
                       ) AS evidence_ids,
                       BOOL_AND(COALESCE(d.state, 'active') = 'active') AS evidence_active,
                       p.sensitivity, p.effective_visibility, p.trust_class,
                       p.source_observation_ids, p.source_assertion_ids,
                       p.source_governance_revision, p.policy_version, p.policy_digest,
                       a.assertion_type
                  FROM omnix_memory_v2_graph_assertions a
                  LEFT JOIN omnix_memory_v2_assertion_observation_evidence e
                    ON e.assertion_id = a.assertion_id
                  LEFT JOIN omnix_memory_v2_observation_dispositions d
                    ON d.observation_id = e.observation_id
                  LEFT JOIN omnix_memory_v2_derived_policy_envelopes p
                    ON p.principal_id = a.principal_id
                   AND p.owner_type = a.owner_type
                   AND p.owner_id = a.owner_id
                   AND p.item_type = 'assertion'
                   AND p.ref_id = a.assertion_id
                 WHERE a.principal_id = %s AND a.owner_type = %s AND a.owner_id = %s
                   AND a.status = 'active'
                 GROUP BY a.assertion_id, a.visibility_scopes, a.subject_entity_id,
                          a.predicate, a.object_value, a.domain, a.revision,
                          p.sensitivity, p.effective_visibility, p.trust_class,
                          p.source_observation_ids, p.source_assertion_ids,
                          p.source_governance_revision, p.policy_version, p.policy_digest,
                          a.assertion_type
                 ORDER BY a.assertion_id
                """,
                values,
            ).fetchall()
            entries = []
            for row in rows:
                if row[8] is False:
                    continue
                object_value = dict(row[4])
                # A seeded assertion is a curated memory whose text is the memory itself.
                content = (
                    _object_text(object_value)
                    if row[17] == "seeded"
                    else f"{row[2]!s} {str(row[3]).replace('_', ' ')} {_object_text(object_value)}"
                )
                policy = _policy_from_columns(row, 9)
                effective_visibility = (
                    list(row[10]) if policy is not None else list(row[1])
                )
                entries.append(
                    {
                        "ref_id": str(row[0]),
                        "item_type": "assertion",
                        "domain": str(row[5]),
                        "content": content,
                        "visibility_scopes": list(row[1]),
                        "evidence_ids": [str(item) for item in row[7]],
                        "source_revision": int(row[6]),
                        "sensitivity": policy.sensitivity if policy else "normal",
                        "trust_class": policy.trust_class if policy else "assistant_inference",
                        "effective_visibility": effective_visibility,
                        "policy_digest": policy.policy_digest if policy else "",
                    }
                )
            projection_digest = _digest(entries)

            connection.execute(
                """
                INSERT INTO omnix_memory_v2_search_index_state (
                    principal_id, owner_type, owner_id, index_graph_revision,
                    source_observation_watermark, governance_digest,
                    projection_digest, entry_count, built_at, index_derived_revision
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                ON CONFLICT (principal_id, owner_type, owner_id) DO UPDATE SET
                    index_graph_revision = EXCLUDED.index_graph_revision,
                    source_observation_watermark = EXCLUDED.source_observation_watermark,
                    governance_digest = EXCLUDED.governance_digest,
                    projection_digest = EXCLUDED.projection_digest,
                    entry_count = EXCLUDED.entry_count,
                    index_derived_revision = EXCLUDED.index_derived_revision,
                    built_at = CURRENT_TIMESTAMP
                """,
                (
                    *values,
                    graph_revision,
                    observation_watermark,
                    governance_digest,
                    projection_digest,
                    len(entries),
                    derived_revision,
                ),
            )
            connection.execute(
                """
                DELETE FROM omnix_memory_v2_search_index_entries
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                """,
                values,
            )
            for entry in entries:
                connection.execute(
                    """
                    INSERT INTO omnix_memory_v2_search_index_entries (
                        principal_id, owner_type, owner_id, item_type, ref_id,
                        domain, content, visibility_scopes, evidence_observation_ids,
                        source_revision, graph_revision, sensitivity, trust_class,
                        effective_visibility, policy_digest
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s, %s, %s, %s, %s::jsonb, %s
                    )
                    """,
                    (
                        *values,
                        entry["item_type"],
                        entry["ref_id"],
                        entry["domain"],
                        entry["content"],
                        _json(entry["visibility_scopes"]),
                        _json(entry["evidence_ids"]),
                        entry["source_revision"],
                        graph_revision,
                        entry["sensitivity"],
                        entry["trust_class"],
                        _json(entry["effective_visibility"]),
                        entry["policy_digest"],
                    ),
                )
            connection.execute(
                """
                DELETE FROM omnix_memory_v2_projection_jobs
                 WHERE principal_id = %s AND owner_type = %s AND owner_id = %s
                   AND target_derived_revision <= %s
                """,
                (*values, derived_revision),
            )
            if graph_observation_watermark > observation_watermark:
                raise RuntimeError("graph source watermark exceeds Observation authority watermark")
        return self.state(space)

    def search(
        self,
        space: MemorySpaceKey,
        text: str,
        *,
        visible_scopes: tuple[VisibilityScope, ...],
        domains: tuple[str, ...] = (),
        limit: int = 20,
        as_of: datetime | None = None,
        ref_ids: tuple[str, ...] | None = None,
    ) -> list[SearchIndexHit]:
        """Entries matching ``text``, or exactly ``ref_ids`` (semantic matches), under the same visibility."""
        if not visible_scopes:
            return []
        match_value: str | list[str]
        if ref_ids is not None:
            if not ref_ids:
                return []
            match_sql, match_value = "i.ref_id = ANY(%s)", list(ref_ids)
            rank_sql, rank_params = "0.0::float8", []
        else:
            match = match_query(text)
            if match is None:
                return []
            match_sql, match_value = "i.search_vector @@ to_tsquery('simple', %s)", match
            rank_sql, rank_params = "ts_rank_cd(i.search_vector, to_tsquery('simple', %s))", [match]
        active_evidence_sql = (
            "NOT EXISTS ("
            " SELECT 1 FROM jsonb_array_elements_text(i.evidence_observation_ids) evidence_id"
            " LEFT JOIN omnix_memory_v2_observation_dispositions d"
            "   ON d.observation_id = evidence_id"
            " WHERE COALESCE(d.state, 'active') <> 'active'"
            ")"
        )
        conditions = [
            "i.principal_id = %s",
            "i.owner_type = %s",
            "i.owner_id = %s",
            match_sql,
            active_evidence_sql,
            "i.effective_visibility <@ %s::jsonb",
            "(a.valid_from IS NULL OR a.valid_from <= %s)",
            "(a.valid_until IS NULL OR a.valid_until > %s)",
        ]
        visible_json = _json([item.model_dump(mode="json") for item in visible_scopes])
        effective_as_of = as_of or datetime.now(timezone.utc)
        where_params: list[Any] = [
            *_space_values(space),
            match_value,
            visible_json,
            effective_as_of,
            effective_as_of,
        ]
        if domains:
            conditions.append("i.domain = ANY(%s)")
            where_params.append(list(domains))
        params = [*rank_params, *where_params, max(1, min(int(limit), 1000))]
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT i.ref_id, i.item_type, i.domain, i.content,
                       {rank_sql} AS rank,
                       i.evidence_observation_ids, i.source_revision,
                       a.confidence, a.valid_from, a.valid_until,
                       p.sensitivity, p.effective_visibility, p.trust_class,
                       p.source_observation_ids, p.source_assertion_ids,
                       p.source_governance_revision, p.policy_version, p.policy_digest
                  FROM omnix_memory_v2_search_index_entries i
                  JOIN omnix_memory_v2_graph_assertions a
                    ON a.principal_id = i.principal_id
                   AND a.owner_type = i.owner_type
                   AND a.owner_id = i.owner_id
                   AND a.assertion_id = i.ref_id
                  LEFT JOIN omnix_memory_v2_derived_policy_envelopes p
                    ON p.principal_id = i.principal_id
                   AND p.owner_type = i.owner_type
                   AND p.owner_id = i.owner_id
                   AND p.item_type = i.item_type
                   AND p.ref_id = i.ref_id
                 WHERE {' AND '.join(conditions)}
                 ORDER BY rank DESC, i.item_type, i.ref_id
                 LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        return [
            SearchIndexHit(
                ref_id=str(row[0]),
                item_type=str(row[1]),
                domain=str(row[2]),
                content=str(row[3]),
                rank=float(row[4]),
                evidence_observation_ids=tuple(str(item) for item in row[5]),
                source_revision=int(row[6]),
                confidence=float(row[7]),
                valid_from=row[8],
                valid_until=row[9],
                policy=_policy_from_columns(row, 10),
            )
            for row in rows
        ]
