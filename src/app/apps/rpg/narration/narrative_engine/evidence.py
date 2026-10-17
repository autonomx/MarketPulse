"""Deterministic evidence retrieval and pre-generation knowledge filtering."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .authority import VisibilityClass
from .contracts import EvidenceRecord


_TOKEN_PATTERN = re.compile(r"[a-z0-9']+", re.IGNORECASE)


@dataclass(frozen=True)
class EvidenceAccessContext:
    player_id: str = "player"
    speaker_id: str | None = None
    actor_ids: tuple[str, ...] = ()
    faction_ids: tuple[str, ...] = ()
    narrator_mode: bool = False


@dataclass(frozen=True)
class EvidenceQuery:
    text: str
    entity_ids: tuple[str, ...] = ()
    limit: int = 12
    access: EvidenceAccessContext = field(default_factory=EvidenceAccessContext)


@dataclass(frozen=True)
class RetrievalTrace:
    query: str
    selected_ids: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]
    candidate_count: int


@dataclass(frozen=True)
class EvidenceRetrievalResult:
    evidence: tuple[EvidenceRecord, ...]
    trace: RetrievalTrace


@dataclass(frozen=True)
class EvidenceGrantSet:
    """Pre-filtered grants for player-visible, narrator, and speaker beats."""

    player: tuple[EvidenceRecord, ...] = ()
    narrator: tuple[EvidenceRecord, ...] = ()
    speakers: Mapping[str, tuple[EvidenceRecord, ...]] = field(default_factory=dict)
    traces: Mapping[str, RetrievalTrace] = field(default_factory=dict)

    def for_speaker(self, speaker_id: str | None) -> tuple[EvidenceRecord, ...]:
        if not speaker_id:
            return self.player
        return tuple(self.speakers.get(speaker_id, ()))

    def all_records(self) -> tuple[EvidenceRecord, ...]:
        by_id: dict[str, EvidenceRecord] = {}
        groups = [self.player, self.narrator, *self.speakers.values()]
        for group in groups:
            for record in group:
                previous = by_id.get(record.evidence_id)
                if previous is None or record.source_revision >= previous.source_revision:
                    by_id[record.evidence_id] = record
        return tuple(by_id[key] for key in sorted(by_id))

    def allowed_ids(self, scope: str, speaker_id: str | None = None) -> frozenset[str]:
        if scope == "narrator":
            records = self.narrator
        elif scope == "speaker":
            records = self.for_speaker(speaker_id)
        else:
            records = self.player
        return frozenset(record.evidence_id for record in records)


class EvidenceSource(Protocol):
    source_id: str

    def records(self, query: EvidenceQuery) -> Sequence[EvidenceRecord]: ...


class InMemoryEvidenceSource:
    def __init__(self, records: Iterable[EvidenceRecord], *, source_id: str = "memory") -> None:
        self.source_id = source_id
        self._records = tuple(records)

    def records(self, query: EvidenceQuery) -> Sequence[EvidenceRecord]:
        return self._records


def _visible(record: EvidenceRecord, access: EvidenceAccessContext) -> tuple[bool, str]:
    visibility = record.visibility
    if visibility in {VisibilityClass.PUBLIC, VisibilityClass.PLAYER_KNOWN}:
        return True, "visible"
    if visibility is VisibilityClass.NPC_PRIVATE:
        if access.speaker_id and access.speaker_id in record.known_by:
            return True, "speaker_knows"
        return False, "npc_private"
    if visibility is VisibilityClass.FACTION_PRIVATE:
        if set(access.faction_ids).intersection(record.known_by):
            return True, "faction_knows"
        return False, "faction_private"
    if visibility is VisibilityClass.NARRATOR_ONLY:
        return (access.narrator_mode, "narrator_only" if not access.narrator_mode else "narrator")
    return False, "game_master_only"


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(value.casefold()))


def _metadata_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_metadata_text(item) for item in value.values())
    if isinstance(value, list | tuple | set):
        return " ".join(str(item) for item in value if str(item).strip())
    return str(value or "")


def _search_text(record: EvidenceRecord) -> str:
    metadata = dict(record.metadata or {})
    searchable = {
        key: metadata.get(key)
        for key in (
            "title",
            "category",
            "topic_id",
            "document_id",
            "entity_id",
            "entity_kind",
            "fact_id",
            "keywords",
            "aliases",
            "tags",
        )
    }
    return " ".join((record.content, _metadata_text(searchable))).strip()


def _score(record: EvidenceRecord, query: EvidenceQuery) -> tuple[float, int, int, str]:
    query_tokens = _tokens(query.text)
    search_text = _search_text(record)
    content_tokens = _tokens(search_text)
    overlap = len(query_tokens.intersection(content_tokens))
    entity_overlap = len(set(query.entity_ids).intersection(record.entity_refs))
    normalized_query = " ".join(_TOKEN_PATTERN.findall(query.text.casefold()))
    normalized_search = " ".join(_TOKEN_PATTERN.findall(search_text.casefold()))
    phrase_bonus = 6 if normalized_query and normalized_query in normalized_search else 0
    score = (
        float(overlap * 3 + entity_overlap * 8 + phrase_bonus)
        + max(0.0, min(record.confidence, 1.0))
    )
    return (score, entity_overlap, record.source_revision, record.evidence_id)


class EvidenceBroker:
    """Collect, filter, rank, and trace evidence before planning or writing."""

    def __init__(self, sources: Iterable[EvidenceSource]) -> None:
        self._sources = tuple(sources)

    def retrieve(self, query: EvidenceQuery) -> EvidenceRetrievalResult:
        by_id: dict[str, EvidenceRecord] = {}
        for source in self._sources:
            for record in source.records(query):
                previous = by_id.get(record.evidence_id)
                if previous is None or record.source_revision > previous.source_revision:
                    by_id[record.evidence_id] = record

        eligible: list[EvidenceRecord] = []
        excluded: list[tuple[str, str]] = []
        for record in by_id.values():
            allowed, reason = _visible(record, query.access)
            if allowed:
                eligible.append(record)
            else:
                excluded.append((record.evidence_id, reason))

        ranked = sorted(
            eligible,
            key=lambda record: (
                -_score(record, query)[0],
                -_score(record, query)[1],
                -_score(record, query)[2],
                _score(record, query)[3],
            ),
        )
        selected = tuple(ranked[: max(1, min(int(query.limit), 50))])
        return EvidenceRetrievalResult(
            evidence=selected,
            trace=RetrievalTrace(
                query=query.text,
                selected_ids=tuple(record.evidence_id for record in selected),
                excluded=tuple(sorted(excluded)),
                candidate_count=len(by_id),
            ),
        )
