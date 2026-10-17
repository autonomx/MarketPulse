from __future__ import annotations

import time

from .contracts import (
    MemoryDomain,
    MemoryGrant,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResult,
    RetrievalSourceRevision,
    VisibilityScope,
)
from .derived_state import federation_revision_digest
from .grant_store import PostgresMemoryV2GrantStore
from .policy import sensitivity_allows
from .retrieval import UnifiedMemoryV2Retriever


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _intersect_domains(
    query_domains: tuple[MemoryDomain, ...],
    grant: MemoryGrant,
) -> tuple[MemoryDomain, ...]:
    allowed = set(grant.allowed_domains)
    if query_domains:
        return tuple(item for item in query_domains if item in allowed)
    return grant.allowed_domains


def _intersect_scopes(
    query_scopes: tuple[VisibilityScope, ...],
    grant: MemoryGrant,
) -> tuple[VisibilityScope, ...]:
    if not grant.scope_constraints:
        return query_scopes
    allowed = {(item.kind, item.scope_id) for item in grant.scope_constraints}
    return tuple(item for item in query_scopes if (item.kind, item.scope_id) in allowed)


def _with_grant_reason(candidate: RetrievalCandidate, grant: MemoryGrant) -> RetrievalCandidate:
    return candidate.model_copy(
        update={
            "source_space": grant.source_space,
            "reasons": (
                *candidate.reasons,
                f"memory_grant:{grant.grant_id}",
                f"federated_source:{grant.source_space.owner_type}:{grant.source_space.owner_id}",
                f"grant_revision:{grant.revision}",
            ),
        }
    )


def _grant_revision(
    revision: RetrievalSourceRevision,
    grant: MemoryGrant,
) -> RetrievalSourceRevision:
    return revision.model_copy(
        update={
            "source_space": grant.source_space,
            "grant_revision": grant.revision,
        }
    )


class FederatedMemoryV2Retriever:
    """Read across explicitly granted memory spaces without copying source memory.

    Cross-space candidates fail closed unless they carry a deterministic policy envelope.
    `MemoryGrant.max_sensitivity` is evaluated against that envelope before a candidate can
    enter federation ranking. Results retain the revision of every contributing source and
    grant so speculative reuse can prove that the complete federation is still current.
    """

    def __init__(
        self,
        *,
        local_retriever: UnifiedMemoryV2Retriever,
        grant_store: PostgresMemoryV2GrantStore,
    ) -> None:
        self.local_retriever = local_retriever
        self.grant_store = grant_store

    @staticmethod
    def _remaining_ms(started: float, deadline_ms: float) -> float:
        elapsed = (time.perf_counter() - started) * 1000.0
        return max(0.0, deadline_ms - elapsed)

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        started = time.perf_counter()
        local_query = query.model_copy(update={"grant_ids": ()})
        local = self.local_retriever.retrieve(local_query)
        candidates: list[RetrievalCandidate] = [
            candidate.model_copy(
                update={"source_space": candidate.source_space or query.space}
            )
            for candidate in local.candidates
        ]
        source_revisions: list[RetrievalSourceRevision] = list(local.source_revisions)

        if query.grant_ids and self._remaining_ms(started, query.deadline_ms) > 0:
            grants = self.grant_store.active_for_target(
                query.space,
                grant_ids=query.grant_ids,
            )
            for grant in grants:
                remaining_ms = self._remaining_ms(started, query.deadline_ms)
                if remaining_ms <= 0:
                    break
                domains = _intersect_domains(query.domains, grant)
                if not domains:
                    continue
                scopes = _intersect_scopes(query.visible_scopes, grant)
                if not scopes:
                    continue
                source_query = query.model_copy(
                    update={
                        "query_id": f"{query.query_id}:grant:{grant.grant_id}",
                        "space": grant.source_space,
                        "visible_scopes": scopes,
                        "domains": domains,
                        "grant_ids": (),
                        "top_k": min(100, max(query.top_k * 4, query.top_k)),
                        "deadline_ms": remaining_ms,
                    }
                )
                source_result = self.local_retriever.retrieve(source_query)
                authorized = [
                    _with_grant_reason(candidate, grant)
                    for candidate in source_result.candidates
                    if candidate.policy is not None
                    and sensitivity_allows(
                        candidate.policy.sensitivity,
                        grant.max_sensitivity,
                    )
                ]
                candidates.extend(authorized)
                if authorized:
                    source_revisions.extend(
                        _grant_revision(revision, grant)
                        for revision in source_result.source_revisions
                    )

        best: dict[tuple[str, str, str, str, str], RetrievalCandidate] = {}
        for candidate in candidates:
            source = candidate.source_space or query.space
            key = (
                source.principal_id,
                source.owner_type,
                source.owner_id,
                candidate.item_type,
                candidate.ref_id,
            )
            current = best.get(key)
            if current is None or candidate.scores.composite > current.scores.composite:
                best[key] = candidate
        ranked = sorted(
            best.values(),
            key=lambda item: (
                -item.scores.composite,
                (item.source_space or query.space).owner_type,
                (item.source_space or query.space).owner_id,
                item.item_type,
                item.ref_id,
            ),
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

        revisions_by_key: dict[tuple[str, str, str, int], RetrievalSourceRevision] = {}
        for revision in source_revisions:
            revision_key = (
                revision.source_space.principal_id,
                revision.source_space.owner_type,
                revision.source_space.owner_id,
                revision.grant_revision,
            )
            revisions_by_key[revision_key] = revision
        revisions = tuple(
            sorted(
                revisions_by_key.values(),
                key=lambda item: (
                    item.source_space.principal_id,
                    item.source_space.owner_type,
                    item.source_space.owner_id,
                    item.grant_revision,
                ),
            )
        )
        digest = federation_revision_digest(revisions) if revisions else None

        return RetrievalResult(
            query_id=query.query_id,
            candidates=tuple(selected),
            dynamic_context=tuple(context),
            token_estimate=token_estimate,
            observation_watermark=local.observation_watermark,
            graph_revision=local.graph_revision,
            index_graph_revision=local.index_graph_revision,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            deadline_ms=query.deadline_ms,
            source_revisions=revisions,
            federation_revision_digest=digest,
        )
