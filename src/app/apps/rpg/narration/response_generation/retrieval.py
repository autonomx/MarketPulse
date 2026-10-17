from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


_TOKEN = re.compile(r"[a-z0-9']+")
_SOURCE_PRIORITY = {
    "resolved_turn": 0,
    "scene": 1,
    "speaker": 2,
    "party": 3,
    "journal": 4,
    "campaign": 5,
    "lorebook": 6,
    "approved_proposal": 7,
}


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source: str
    content: Any
    visibility: str = "player_visible"
    confidence: float = 1.0
    entity_ids: tuple[str, ...] = ()
    speaker_ids: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    timestamp: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    evidence: tuple[EvidenceRecord, ...]
    hidden_evidence_ids: tuple[str, ...]
    conflicting_evidence_ids: tuple[str, ...]
    knowledge_status: str
    local_hit: bool
    trace: tuple[str, ...] = ()


class LocalKnowledgeRetriever:
    """Retrieve visible local evidence before any Hermes request."""

    def __init__(self, *, max_results: int = 12) -> None:
        self.max_results = max_results

    def retrieve(
        self,
        query: str,
        sources: Mapping[str, Iterable[EvidenceRecord | Mapping[str, Any]]],
        *,
        speaker_id: str = "",
        narrator_mode: bool = False,
    ) -> RetrievalResult:
        query_tokens = set(_tokens(query))
        rows: list[tuple[float, int, int, EvidenceRecord]] = []
        hidden: list[str] = []
        trace: list[str] = []
        index = 0
        for source_name in sorted(sources, key=lambda key: _SOURCE_PRIORITY.get(key, 99)):
            source_priority = _SOURCE_PRIORITY.get(source_name, 99)
            for raw in sources[source_name]:
                record = raw if isinstance(raw, EvidenceRecord) else _record(raw, source_name, index)
                index += 1
                if record.visibility == "hidden":
                    hidden.append(record.evidence_id)
                    continue
                if record.speaker_ids and not narrator_mode:
                    if not speaker_id or speaker_id not in record.speaker_ids:
                        trace.append(f"speaker_scope_excluded:{record.evidence_id}")
                        continue
                score = _score(query_tokens, record)
                if score <= 0 and query_tokens:
                    continue
                rows.append((score, -source_priority, -index, record))

        rows.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
        selected = tuple(item[3] for item in rows[: self.max_results])
        conflicts = _conflicts(selected)
        if not selected:
            status = "unknown"
        elif conflicts:
            status = "conflicting"
        elif max(record.confidence for record in selected) < 0.55:
            status = "partial"
        else:
            status = "known"
        return RetrievalResult(
            query=query,
            evidence=selected,
            hidden_evidence_ids=tuple(hidden),
            conflicting_evidence_ids=conflicts,
            knowledge_status=status,
            local_hit=bool(selected),
            trace=tuple(trace),
        )


def build_retrieval_sources(
    *,
    resolved_turn: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
    scene: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
    speaker: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
    party: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
    journal: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
    campaign: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
    lorebook: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
    approved_proposals: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
    approved_proposal: Iterable[EvidenceRecord | Mapping[str, Any]] = (),
) -> dict[str, Iterable[EvidenceRecord | Mapping[str, Any]]]:
    """Build ordered source buckets, accepting the legacy singular alias."""

    approved_rows = tuple(approved_proposals) or tuple(approved_proposal)
    return {
        "resolved_turn": tuple(resolved_turn),
        "scene": tuple(scene),
        "speaker": tuple(speaker),
        "party": tuple(party),
        "journal": tuple(journal),
        "campaign": tuple(campaign),
        "lorebook": tuple(lorebook),
        "approved_proposal": approved_rows,
    }


def _record(row: Mapping[str, Any], source: str, index: int) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=str(row.get("evidence_id") or f"{source}-{index}"),
        source=str(row.get("source") or source),
        content=row.get("content"),
        visibility=str(row.get("visibility") or "player_visible"),
        confidence=float(raw_confidence if (raw_confidence := row.get("confidence")) is not None else 1.0),
        entity_ids=_strings(row.get("entity_ids")),
        speaker_ids=_strings(row.get("speaker_ids")),
        aliases=_strings(row.get("aliases")),
        timestamp=str(row.get("timestamp") or ""),
        metadata=dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {},
    )


def _score(query_tokens: set[str], record: EvidenceRecord) -> float:
    content_tokens = set(_tokens(record.content))
    alias_tokens = set(token for alias in record.aliases for token in _tokens(alias))
    entity_tokens = set(token for entity in record.entity_ids for token in _tokens(entity))
    searchable = content_tokens | alias_tokens | entity_tokens
    if not query_tokens:
        overlap = 1.0
    else:
        overlap = len(query_tokens & searchable) / max(1, len(query_tokens))
    alias_bonus = 0.35 if query_tokens & alias_tokens else 0.0
    query_key = " ".join(sorted(query_tokens))
    searchable_key = " ".join(sorted(searchable))
    exact_bonus = 0.25 if query_key and query_key in searchable_key else 0.0
    return round((overlap + alias_bonus + exact_bonus) * max(0.0, min(1.0, record.confidence)), 6)


def _conflicts(records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]:
    grouped: dict[str, list[EvidenceRecord]] = {}
    for record in records:
        subject = str(record.metadata.get("subject") or "")
        if subject:
            grouped.setdefault(subject, []).append(record)
    conflicts: list[str] = []
    for rows in grouped.values():
        asserted_values = {
            str(row.metadata.get("asserted_value"))
            for row in rows
            if "asserted_value" in row.metadata
        }
        if len(asserted_values) > 1:
            conflicts.extend(row.evidence_id for row in rows)
    return tuple(dict.fromkeys(conflicts))


def _tokens(value: Any) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(str(value or "").casefold()))


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    try:
        return tuple(str(item) for item in value if str(item))
    except TypeError:
        return ()
