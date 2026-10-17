from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .contracts import MemorySpaceKey, RetrievalQuery, RetrievalResult

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _normalized_tokens(text: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_RE.findall(text))


def _policy_key(query: RetrievalQuery) -> tuple[Any, ...]:
    return (
        tuple((scope.kind, scope.scope_id) for scope in query.visible_scopes),
        query.domains,
        query.grant_ids,
        query.top_k,
        query.token_budget,
    )


@dataclass(frozen=True, slots=True)
class PrefetchKey:
    space: MemorySpaceKey
    session_id: str
    segment_id: str
    hypothesis_id: str


@dataclass(frozen=True, slots=True)
class SpeculativePrefetchEntry:
    key: PrefetchKey
    normalized_tokens: tuple[str, ...]
    query_as_of: Any
    policy_key: tuple[Any, ...]
    result: RetrievalResult
    created_monotonic: float


@dataclass(frozen=True, slots=True)
class PrefetchPromotion:
    result: RetrievalResult
    reused: bool
    source_hypothesis_id: str | None = None


ReuseGuard = Callable[[RetrievalQuery, RetrievalResult], bool]


class SpeculativeMemoryPrefetchController:
    """Read-only partial-STT memory prefetch with deterministic cancel/promote rules.

    The controller receives only a retrieval callable and cannot mutate memory. Cache reuse
    additionally requires an explicit read-only `reuse_guard` proving that the prefetched
    result remains authorized/current (for example, evidence is still active and relevant
    watermarks/grants have not changed). Without such a guard, promotion fails closed and
    performs a fresh final retrieval.
    """

    def __init__(
        self,
        retrieve: Callable[[RetrievalQuery], RetrievalResult],
        *,
        reuse_guard: ReuseGuard | None = None,
        ttl_ms: float = 1500.0,
        max_tail_tokens: int = 3,
        max_hypotheses_per_segment: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_ms < 0:
            raise ValueError("ttl_ms must be non-negative")
        if max_tail_tokens < 0:
            raise ValueError("max_tail_tokens must be non-negative")
        if max_hypotheses_per_segment < 1:
            raise ValueError("max_hypotheses_per_segment must be positive")
        self.retrieve = retrieve
        self.reuse_guard = reuse_guard
        self.ttl_ms = float(ttl_ms)
        self.max_tail_tokens = int(max_tail_tokens)
        self.max_hypotheses_per_segment = int(max_hypotheses_per_segment)
        self.clock = clock
        self._entries: dict[PrefetchKey, SpeculativePrefetchEntry] = {}

    def _expired(self, entry: SpeculativePrefetchEntry) -> bool:
        return (self.clock() - entry.created_monotonic) * 1000.0 > self.ttl_ms

    def _purge_expired(self) -> None:
        expired = [key for key, entry in self._entries.items() if self._expired(entry)]
        for key in expired:
            self._entries.pop(key, None)

    @staticmethod
    def _segment_matches(
        key: PrefetchKey,
        *,
        space: MemorySpaceKey,
        session_id: str,
        segment_id: str,
    ) -> bool:
        return key.space == space and key.session_id == session_id and key.segment_id == segment_id

    def _compatible(self, entry: SpeculativePrefetchEntry, final_query: RetrievalQuery) -> bool:
        if entry.key.space != final_query.space:
            return False
        if entry.policy_key != _policy_key(final_query):
            return False
        delta_ms = (final_query.as_of - entry.query_as_of).total_seconds() * 1000.0
        if delta_ms < 0 or delta_ms > self.ttl_ms:
            return False
        partial = entry.normalized_tokens
        final = _normalized_tokens(final_query.text)
        if not partial or not final:
            return False
        if final == partial:
            return True
        if len(final) < len(partial) or final[: len(partial)] != partial:
            return False
        return len(final) - len(partial) <= self.max_tail_tokens

    def prefetch(
        self,
        *,
        session_id: str,
        segment_id: str,
        hypothesis_id: str,
        query: RetrievalQuery,
    ) -> RetrievalResult:
        if query.authority != "partial":
            raise ValueError("speculative memory prefetch requires partial retrieval authority")
        if not session_id or not segment_id or not hypothesis_id:
            raise ValueError("session_id, segment_id, and hypothesis_id are required")
        self._purge_expired()
        key = PrefetchKey(
            space=query.space,
            session_id=session_id,
            segment_id=segment_id,
            hypothesis_id=hypothesis_id,
        )
        tokens = _normalized_tokens(query.text)
        policy = _policy_key(query)
        existing = self._entries.get(key)
        if (
            existing is not None
            and not self._expired(existing)
            and existing.normalized_tokens == tokens
            and existing.policy_key == policy
        ):
            return existing.result

        result = self.retrieve(query)
        self._entries[key] = SpeculativePrefetchEntry(
            key=key,
            normalized_tokens=tokens,
            query_as_of=query.as_of,
            policy_key=policy,
            result=result,
            created_monotonic=self.clock(),
        )

        siblings = sorted(
            (
                entry
                for entry in self._entries.values()
                if self._segment_matches(
                    entry.key,
                    space=query.space,
                    session_id=session_id,
                    segment_id=segment_id,
                )
            ),
            key=lambda entry: (entry.created_monotonic, entry.key.hypothesis_id),
        )
        while len(siblings) > self.max_hypotheses_per_segment:
            oldest = siblings.pop(0)
            self._entries.pop(oldest.key, None)
        return result

    def cancel(
        self,
        *,
        space: MemorySpaceKey,
        session_id: str,
        segment_id: str,
        hypothesis_id: str,
    ) -> bool:
        key = PrefetchKey(
            space=space,
            session_id=session_id,
            segment_id=segment_id,
            hypothesis_id=hypothesis_id,
        )
        return self._entries.pop(key, None) is not None

    def cancel_segment(
        self,
        *,
        space: MemorySpaceKey,
        session_id: str,
        segment_id: str,
    ) -> int:
        keys = [
            key
            for key in self._entries
            if self._segment_matches(
                key,
                space=space,
                session_id=session_id,
                segment_id=segment_id,
            )
        ]
        for key in keys:
            self._entries.pop(key, None)
        return len(keys)

    def promote_or_retrieve(
        self,
        *,
        session_id: str,
        segment_id: str,
        hypothesis_id: str,
        final_query: RetrievalQuery,
    ) -> PrefetchPromotion:
        if final_query.authority != "final":
            raise ValueError("prefetch promotion requires final retrieval authority")
        self._purge_expired()
        key = PrefetchKey(
            space=final_query.space,
            session_id=session_id,
            segment_id=segment_id,
            hypothesis_id=hypothesis_id,
        )
        entry = self._entries.get(key)
        can_reuse = (
            entry is not None
            and self._compatible(entry, final_query)
            and self.reuse_guard is not None
            and self.reuse_guard(final_query, entry.result)
        )
        if can_reuse and entry is not None:
            self.cancel_segment(
                space=final_query.space,
                session_id=session_id,
                segment_id=segment_id,
            )
            return PrefetchPromotion(
                result=entry.result.model_copy(update={"query_id": final_query.query_id}),
                reused=True,
                source_hypothesis_id=hypothesis_id,
            )

        self.cancel_segment(
            space=final_query.space,
            session_id=session_id,
            segment_id=segment_id,
        )
        return PrefetchPromotion(
            result=self.retrieve(final_query),
            reused=False,
            source_hypothesis_id=None,
        )

    @property
    def entry_count(self) -> int:
        self._purge_expired()
        return len(self._entries)
