from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from .contracts import (
    DerivedPolicyEnvelope,
    Episode,
    GraphAssertion,
    GraphValue,
    MemoryDomain,
    MemorySpaceKey,
    RelationshipState,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResult,
    RetrievalScore,
    VisibilityScope,
)
from .derived_state import (
    PostgresMemoryV2DerivedStateStore,
    federation_revision_digest,
)
from .episode_store import PostgresMemoryV2EpisodeStore
from .graph_store import PostgresMemoryV2GraphStore
from .observation_store import PostgresMemoryV2ObservationStore
from .policy import visibility_satisfied
from .relationship_store import PostgresMemoryV2RelationshipStore
from .search_index import PostgresMemoryV2SearchIndex, SearchIndexHit

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


_SEMANTIC_FLOOR = 0.78
_SEMANTIC_SPAN = 0.12
_SEMANTIC_MARGIN = 0.04


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _lexical_similarity(query: str, content: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    content_tokens = _tokens(content)
    if not content_tokens:
        return 0.0
    return len(query_tokens & content_tokens) / len(query_tokens)


def _scope_visible(
    item_scopes: tuple[VisibilityScope, ...],
    visible_scopes: tuple[VisibilityScope, ...],
) -> bool:
    allowed = {(scope.kind, scope.scope_id) for scope in visible_scopes}
    return any((scope.kind, scope.scope_id) in allowed for scope in item_scopes)


def _object_text(value: GraphValue) -> str:
    if value.kind == "entity" and value.entity is not None:
        return value.entity.entity_id
    return str(value.literal)


def _assertion_content(item: GraphAssertion) -> str:
    # A seeded assertion is a curated memory whose text is the memory itself.
    if item.assertion_type == "seeded":
        return _object_text(item.object)
    return f"{item.subject.entity_id} {item.predicate.replace('_', ' ')} {_object_text(item.object)}"


def _episode_content(item: Episode) -> str:
    return f"{item.title}: {item.summary}"


def _relationship_content(item: RelationshipState) -> str:
    return item.prompt_interpretation


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _recency_score(timestamp: datetime | None, as_of: datetime) -> float:
    if timestamp is None:
        return 0.5
    age_seconds = max(0.0, (as_of - timestamp).total_seconds())
    age_days = age_seconds / 86_400.0
    return 1.0 / (1.0 + age_days / 30.0)


def _mean(values: list[float], default: float = 0.0) -> float:
    return sum(values) / len(values) if values else default


IndexRevisionProvider = Callable[[MemorySpaceKey], int]


class UnifiedMemoryV2Retriever:
    """Bounded read-only retrieval over canonical Memory v2 derived state.

    A fresh search projection is the normal assertion candidate generator. If projection
    state is stale or unavailable, retrieval explicitly falls back to bounded canonical
    graph enumeration. Evidence dispositions and derived policies are batch-hydrated when
    the backing stores support it.
    """

    def __init__(
        self,
        *,
        graph_store: PostgresMemoryV2GraphStore,
        observation_store: PostgresMemoryV2ObservationStore,
        episode_store: PostgresMemoryV2EpisodeStore,
        relationship_store: PostgresMemoryV2RelationshipStore,
        index_graph_revision_provider: IndexRevisionProvider | None = None,
        search_index: PostgresMemoryV2SearchIndex | None = None,
        derived_store: PostgresMemoryV2DerivedStateStore | None = None,
        embedding_index: Any | None = None,
    ) -> None:
        self.graph_store = graph_store
        self.observation_store = observation_store
        self.episode_store = episode_store
        self.relationship_store = relationship_store
        self.index_graph_revision_provider = index_graph_revision_provider or (lambda _space: 0)
        self.search_index = search_index
        self.derived_store = derived_store
        # VoiceMem-style embedding retrieval (PostgresMemoryV2EmbeddingIndex); None: words only.
        self.embedding_index = embedding_index

    @staticmethod
    def _deadline_exceeded(started: float, deadline_ms: float) -> bool:
        return (time.perf_counter() - started) * 1000.0 >= deadline_ms

    def _inactive_evidence(self, observation_ids: tuple[str, ...]) -> set[str]:
        ids = tuple(dict.fromkeys(observation_ids))
        if not ids:
            return set()
        batch = getattr(self.observation_store, "dispositions", None)
        if callable(batch):
            dispositions = batch(ids)
            return {
                observation_id
                for observation_id, disposition in dispositions.items()
                if disposition.state != "active"
            }
        inactive = set()
        for observation_id in ids:
            disposition = self.observation_store.disposition(observation_id)
            if disposition is not None and disposition.state != "active":
                inactive.add(observation_id)
        return inactive

    @staticmethod
    def _domain_allowed(domain: MemoryDomain, query: RetrievalQuery) -> bool:
        return not query.domains or domain in query.domains

    @staticmethod
    def _visible(
        policy: DerivedPolicyEnvelope | None,
        legacy_scopes: tuple[VisibilityScope, ...],
        query: RetrievalQuery,
    ) -> bool:
        if policy is not None:
            return visibility_satisfied(policy.effective_visibility, query.visible_scopes)
        return _scope_visible(legacy_scopes, query.visible_scopes)

    @staticmethod
    def _assertion_evidence_ids(
        item: GraphAssertion,
        policy: DerivedPolicyEnvelope | None,
    ) -> tuple[str, ...]:
        if policy is not None and policy.source_observation_ids:
            return policy.source_observation_ids
        return item.evidence_observation_ids

    def _assertion_candidate(
        self,
        item: GraphAssertion,
        query: RetrievalQuery,
        *,
        policy: DerivedPolicyEnvelope | None,
        inactive: set[str],
        fallback_reason: str = "bounded_graph_fallback",
    ) -> RetrievalCandidate | None:
        if item.status != "active" or not self._domain_allowed(item.domain, query):
            return None
        if item.evidence_assertion_ids and policy is None:
            # Recursive assertion provenance cannot be verified safely after governance
            # invalidation if its derived policy envelope has been removed. Fail closed.
            return None
        if not self._visible(policy, item.visibility_scopes, query):
            return None
        evidence_ids = self._assertion_evidence_ids(item, policy)
        if inactive.intersection(evidence_ids):
            return None
        content = _assertion_content(item)
        semantic = _lexical_similarity(query.text, content)
        temporal = 1.0
        recency = _recency_score(item.valid_from, query.as_of)
        graph = 1.0
        composite = (
            0.42 * semantic
            + 0.18 * graph
            + 0.14 * temporal
            + 0.14 * item.confidence
            + 0.12 * recency
        )
        reasons = [
            "active_graph_assertion",
            "evidence_active",
            "visibility_match",
            fallback_reason,
        ]
        if semantic > 0:
            reasons.append("query_term_match")
        return RetrievalCandidate(
            ref_id=item.assertion_id,
            item_type="assertion",
            domain=item.domain,
            content=content,
            scores=RetrievalScore(
                semantic=semantic,
                graph=graph,
                temporal=temporal,
                confidence=item.confidence,
                recency=recency,
                composite=composite,
            ),
            reasons=tuple(reasons),
            evidence_observation_ids=evidence_ids,
            policy=policy,
        )

    @staticmethod
    def _indexed_assertion_candidate(
        hit: SearchIndexHit, query: RetrievalQuery, cosine: float | None = None,
    ) -> RetrievalCandidate:
        semantic = max(0.0, min(1.0, _lexical_similarity(query.text, hit.content)))
        if cosine is not None:
            # e5 cosines sit between about 0.78 (unrelated) and 0.90+ (the same fact).
            semantic = max(semantic, max(0.0, min(1.0, (cosine - _SEMANTIC_FLOOR) / _SEMANTIC_SPAN)))
        recency = _recency_score(hit.valid_from, query.as_of)
        composite = (
            0.42 * semantic
            + 0.18
            + 0.14
            + 0.14 * hit.confidence
            + 0.12 * recency
        )
        return RetrievalCandidate(
            ref_id=hit.ref_id,
            item_type="assertion",
            domain=cast(Any, hit.domain),
            content=hit.content,
            scores=RetrievalScore(
                semantic=semantic,
                graph=1.0,
                temporal=1.0,
                confidence=hit.confidence,
                recency=recency,
                composite=composite,
            ),
            reasons=(
                "active_graph_assertion",
                "evidence_active",
                "visibility_match",
                "search_projection_candidate" if cosine is None else "embedding_candidate",
            ),
            evidence_observation_ids=hit.evidence_observation_ids,
            policy=hit.policy,
        )

    def _embedding_candidates(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        """Assertions near the question in embedding space, under the same visibility filters.

        Unrelated e5 neighbours score close to related ones, so only the entries
        within a small margin of the best match are kept.
        """
        if self.embedding_index is None or self.search_index is None:
            return []
        try:
            nearest = self.embedding_index.nearest(query.space, query.text, limit=max(query.top_k, 8))
        except Exception:  # embeddings are an accelerator: fall back to words
            logger.warning("memory_v2_embedding_retrieval_failed", exc_info=True)
            return []
        if not nearest:
            return []
        threshold = max(_SEMANTIC_FLOOR, nearest[0][1] - _SEMANTIC_MARGIN)
        kept = {ref_id: cosine for ref_id, cosine in nearest if cosine >= threshold}
        if not kept:
            return []
        hits = self.search_index.search(
            query.space,
            query.text,
            visible_scopes=query.visible_scopes,
            domains=query.domains,
            limit=len(kept),
            as_of=query.as_of,
            ref_ids=tuple(kept),
        )
        return [self._indexed_assertion_candidate(hit, query, cosine=kept[hit.ref_id]) for hit in hits]

    def _episode_candidate(
        self,
        item: Episode,
        query: RetrievalQuery,
        *,
        policy: DerivedPolicyEnvelope | None,
        inactive: set[str],
    ) -> RetrievalCandidate | None:
        if not self._domain_allowed("episode", query):
            return None
        if not self._visible(policy, item.visibility_scopes, query):
            return None
        if item.started_at > query.as_of:
            return None
        if inactive.intersection(item.observation_ids):
            return None
        content = _episode_content(item)
        semantic = _lexical_similarity(query.text, content)
        recency = _recency_score(item.ended_at or item.started_at, query.as_of)
        episodic = item.importance
        composite = 0.50 * semantic + 0.22 * episodic + 0.16 * recency + 0.12
        reasons = ["episode_evidence_active", "visibility_match"]
        if semantic > 0:
            reasons.append("query_term_match")
        return RetrievalCandidate(
            ref_id=item.episode_id,
            item_type="episode",
            domain="episode",
            content=content,
            scores=RetrievalScore(
                semantic=semantic,
                episodic=episodic,
                importance=item.importance,
                recency=recency,
                composite=composite,
            ),
            reasons=tuple(reasons),
            evidence_observation_ids=item.observation_ids,
            policy=policy,
        )

    def _relationship_candidate(
        self,
        item: RelationshipState,
        query: RetrievalQuery,
        *,
        policy: DerivedPolicyEnvelope | None,
        inactive: set[str],
    ) -> RetrievalCandidate | None:
        if item.status != "active" or not self._domain_allowed("relationship", query):
            return None
        evidence_ids = tuple(
            dict.fromkeys(
                (
                    *item.evidence_observation_ids,
                    *(
                        evidence
                        for metric in item.metrics
                        for evidence in metric.evidence_observation_ids
                    ),
                )
            )
        )
        if policy is not None and not visibility_satisfied(
            policy.effective_visibility,
            query.visible_scopes,
        ):
            return None
        if inactive.intersection(evidence_ids):
            return None
        content = _relationship_content(item)
        semantic = _lexical_similarity(query.text, content)
        metric_strength = _mean(
            [metric.value * metric.confidence for metric in item.metrics],
            default=0.5,
        )
        confidence = _mean([metric.confidence for metric in item.metrics], default=0.5)
        composite = 0.44 * semantic + 0.34 * metric_strength + 0.16 * confidence + 0.06
        reasons = ["relationship_evidence_active", "prompt_interpretation_only"]
        if semantic > 0:
            reasons.append("query_term_match")
        return RetrievalCandidate(
            ref_id=item.relationship_id,
            item_type="relationship",
            domain="relationship",
            content=content,
            scores=RetrievalScore(
                semantic=semantic,
                relationship=metric_strength,
                confidence=confidence,
                composite=composite,
            ),
            reasons=tuple(reasons),
            evidence_observation_ids=evidence_ids,
            policy=policy,
        )

    def _policy_map(
        self,
        space: MemorySpaceKey,
        item_type: str,
        ref_ids: tuple[str, ...],
    ) -> dict[str, DerivedPolicyEnvelope]:
        if self.derived_store is None or not ref_ids:
            return {}
        return self.derived_store.policies(space, item_type, ref_ids)  # type: ignore[arg-type]

    def _local_source_revisions(
        self,
        space: MemorySpaceKey,
        *,
        observation_watermark: int,
        index_graph_revision: int,
    ) -> tuple[Any, ...]:
        if self.derived_store is None:
            return ()
        return (
            self.derived_store.source_revision(
                space,
                observation_watermark=observation_watermark,
                index_revision=index_graph_revision,
            ),
        )

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        started = time.perf_counter()
        graph_state = self.graph_store.state(query.space)
        observation_watermark = self.observation_store.watermark(query.space)
        index_graph_revision = int(self.index_graph_revision_provider(query.space))
        source_revisions = self._local_source_revisions(
            query.space,
            observation_watermark=observation_watermark,
            index_graph_revision=index_graph_revision,
        )
        revision_digest = (
            federation_revision_digest(source_revisions) if source_revisions else None
        )
        if query.deadline_ms == 0 or self._deadline_exceeded(started, query.deadline_ms):
            return RetrievalResult(
                query_id=query.query_id,
                observation_watermark=observation_watermark,
                graph_revision=graph_state.graph_revision,
                index_graph_revision=index_graph_revision,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                deadline_ms=query.deadline_ms,
                source_revisions=source_revisions,
                federation_revision_digest=revision_digest,
            )

        candidates: list[RetrievalCandidate] = []
        used_projection = False
        if self.search_index is not None and not self._deadline_exceeded(started, query.deadline_ms):
            status = self.search_index.status(query.space)
            if not status.stale:
                hits = self.search_index.search(
                    query.space,
                    query.text,
                    visible_scopes=query.visible_scopes,
                    domains=query.domains,
                    limit=max(query.top_k * 8, 32),
                    as_of=query.as_of,
                )
                candidates.extend(
                    self._indexed_assertion_candidate(hit, query) for hit in hits
                )
                used_projection = bool(hits)
                if not self._deadline_exceeded(started, query.deadline_ms):
                    semantic = self._embedding_candidates(query)
                    candidates.extend(semantic)
                    used_projection = used_projection or bool(semantic)

        # A fresh projection is preferred. If it produces no lexical candidates, use a
        # bounded graph fallback so valid low-lexical memories are not silently lost.
        if not used_projection and not self._deadline_exceeded(started, query.deadline_ms):
            assertions = self.graph_store.list_assertions(
                query.space,
                statuses=("active",),
                domains=query.domains,
                as_of=query.as_of,
            )[: max(query.top_k * 8, 32)]
            assertion_policies = self._policy_map(
                query.space,
                "assertion",
                tuple(item.assertion_id for item in assertions),
            )
            assertion_evidence = tuple(
                observation_id
                for item in assertions
                for observation_id in self._assertion_evidence_ids(
                    item,
                    assertion_policies.get(item.assertion_id) or item.policy,
                )
            )
            inactive = self._inactive_evidence(assertion_evidence)
            for item in assertions:
                if self._deadline_exceeded(started, query.deadline_ms):
                    break
                candidate = self._assertion_candidate(
                    item,
                    query,
                    policy=assertion_policies.get(item.assertion_id) or item.policy,
                    inactive=inactive,
                )
                if candidate is not None:
                    candidates.append(candidate)

        if not self._deadline_exceeded(started, query.deadline_ms):
            episodes = self.episode_store.list(query.space, limit=max(query.top_k * 8, 32))
            episode_policies = self._policy_map(
                query.space,
                "episode",
                tuple(item.episode_id for item in episodes),
            )
            episode_inactive = self._inactive_evidence(
                tuple(
                    observation_id
                    for item in episodes
                    for observation_id in item.observation_ids
                )
            )
            for episode in episodes:
                if self._deadline_exceeded(started, query.deadline_ms):
                    break
                candidate = self._episode_candidate(
                    episode,
                    query,
                    policy=episode_policies.get(episode.episode_id) or episode.policy,
                    inactive=episode_inactive,
                )
                if candidate is not None:
                    candidates.append(candidate)

        if not self._deadline_exceeded(started, query.deadline_ms):
            relationships = self.relationship_store.list(
                query.space,
                status="active",
                limit=max(query.top_k * 4, 16),
            )
            relationship_policies = self._policy_map(
                query.space,
                "relationship",
                tuple(item.relationship_id for item in relationships),
            )
            relationship_evidence = tuple(
                observation_id
                for item in relationships
                for observation_id in (
                    *item.evidence_observation_ids,
                    *(
                        evidence
                        for metric in item.metrics
                        for evidence in metric.evidence_observation_ids
                    ),
                )
            )
            relationship_inactive = self._inactive_evidence(relationship_evidence)
            for relationship in relationships:
                if self._deadline_exceeded(started, query.deadline_ms):
                    break
                candidate = self._relationship_candidate(
                    relationship,
                    query,
                    policy=(
                        relationship_policies.get(relationship.relationship_id) or relationship.policy
                    ),
                    inactive=relationship_inactive,
                )
                if candidate is not None:
                    candidates.append(candidate)

        best: dict[tuple[str, str], RetrievalCandidate] = {}
        for candidate in candidates:
            key = (candidate.item_type, candidate.ref_id)
            current = best.get(key)
            if current is None or candidate.scores.composite > current.scores.composite:
                best[key] = candidate
        ranked = sorted(
            best.values(),
            key=lambda item: (-item.scores.composite, item.item_type, item.ref_id),
        )
        selected: list[RetrievalCandidate] = []
        context: list[str] = []
        token_estimate = 0
        for candidate in ranked:
            if len(selected) >= query.top_k:
                break
            estimate = _estimate_tokens(candidate.content)
            if token_estimate + estimate > query.token_budget:
                continue
            selected.append(candidate)
            context.append(candidate.content)
            token_estimate += estimate

        return RetrievalResult(
            query_id=query.query_id,
            candidates=tuple(selected),
            dynamic_context=tuple(context),
            token_estimate=token_estimate,
            observation_watermark=observation_watermark,
            graph_revision=graph_state.graph_revision,
            index_graph_revision=index_graph_revision,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            deadline_ms=query.deadline_ms,
            source_revisions=source_revisions,
            federation_revision_digest=revision_digest,
        )
