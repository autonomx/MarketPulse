"""Bounded, read-only Hermes narrative research for the Narrative Engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, cast

from .authority import AuthorityClass, EvidenceLifetime, VisibilityClass
from .contracts import EvidenceRecord, stable_hash
from collections.abc import Sequence


_ALLOWED_AUTHORITIES = {
    AuthorityClass.HISTORICAL_RECORD,
    AuthorityClass.PUBLIC_KNOWLEDGE,
    AuthorityClass.RUMOR,
    AuthorityClass.DISPUTED_CLAIM,
    AuthorityClass.GENERATED_PROPOSAL,
    AuthorityClass.OBJECTIVE_CANON,
    AuthorityClass.NPC_BELIEF,
    AuthorityClass.FACTION_DOCTRINE,
    AuthorityClass.SECRET_CANON,
}


@dataclass(frozen=True)
class HermesResearchPolicy:
    max_sources: int = 6
    max_findings: int = 12
    max_total_chars: int = 8_000
    max_excerpt_chars: int = 800
    max_query_chars: int = 500

    def bounded(self) -> "HermesResearchPolicy":
        return HermesResearchPolicy(
            max_sources=max(1, min(int(self.max_sources), 12)),
            max_findings=max(1, min(int(self.max_findings), 24)),
            max_total_chars=max(500, min(int(self.max_total_chars), 20_000)),
            max_excerpt_chars=max(100, min(int(self.max_excerpt_chars), 2_000)),
            max_query_chars=max(40, min(int(self.max_query_chars), 1_000)),
        )


@dataclass(frozen=True)
class HermesResearchRequest:
    research_id: str
    campaign_id: str
    query: str
    entity_ids: tuple[str, ...] = ()
    purpose: str = "narrative_grounding"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_id": self.research_id,
            "campaign_id": self.campaign_id,
            "query": self.query,
            "entity_ids": list(self.entity_ids),
            "purpose": self.purpose,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class HermesResearchSource:
    source_id: str
    title: str
    citation: str
    excerpt: str = ""
    published_at: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "citation": self.citation,
            "excerpt": self.excerpt,
            "published_at": self.published_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class HermesResearchFinding:
    finding_id: str
    content: str
    source_refs: tuple[str, ...]
    authority: AuthorityClass
    entity_refs: tuple[str, ...] = ()
    confidence: float = 0.5
    disputed: bool = False
    visibility: VisibilityClass = VisibilityClass.PLAYER_KNOWN
    known_by: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "content": self.content,
            "source_refs": list(self.source_refs),
            "authority": self.authority.value,
            "entity_refs": list(self.entity_refs),
            "confidence": self.confidence,
            "disputed": self.disputed,
            "visibility": self.visibility.value,
            "known_by": list(self.known_by),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class HermesResearchResult:
    research_id: str
    campaign_id: str
    query: str
    sources: tuple[HermesResearchSource, ...]
    findings: tuple[HermesResearchFinding, ...]
    rejected_items: tuple[Mapping[str, Any], ...] = ()
    truncated: bool = False
    provider: str = ""
    model: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return stable_hash(self.content_payload())

    def content_payload(self) -> dict[str, Any]:
        return {
            "research_id": self.research_id,
            "campaign_id": self.campaign_id,
            "query": self.query,
            "sources": [source.as_dict() for source in self.sources],
            "findings": [finding.as_dict() for finding in self.findings],
            "rejected_items": [dict(item) for item in self.rejected_items],
            "truncated": self.truncated,
            "provider": self.provider,
            "model": self.model,
            "metadata": dict(self.metadata),
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self.content_payload(), "content_hash": self.content_hash}

    def evidence(self) -> tuple[EvidenceRecord, ...]:
        return tuple(
            EvidenceRecord(
                evidence_id=f"hermes:{self.research_id}:{finding.finding_id}",
                content=finding.content,
                authority=finding.authority,
                visibility=finding.visibility,
                known_by=finding.known_by,
                entity_refs=finding.entity_refs,
                source_revision=int(
                    finding.metadata.get("canon_revision") or 0
                ),
                confidence=finding.confidence,
                lifetime=EvidenceLifetime.CAMPAIGN,
                metadata={
                    "source": "hermes_narrative_research",
                    "research_id": self.research_id,
                    "source_refs": list(finding.source_refs),
                    "disputed": finding.disputed,
                    "content_hash": self.content_hash,
                    **dict(finding.metadata),
                },
            )
            for finding in self.findings
        )


class HermesNarrativeResearcher(Protocol):
    def research(
        self,
        request: HermesResearchRequest,
        policy: HermesResearchPolicy,
    ) -> Mapping[str, Any]: ...


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[: max(0, int(limit))]


def _strings(value: Any, limit: int = 16) -> tuple[str, ...]:
    if not isinstance(value, list | tuple | set):
        return ()
    return tuple(
        str(item).strip()
        for item in list(value)[:limit]
        if str(item).strip()
    )


def _authority(value: Any, disputed: bool) -> AuthorityClass | None:
    if disputed:
        return AuthorityClass.DISPUTED_CLAIM
    try:
        selected = AuthorityClass(
            str(value or AuthorityClass.PUBLIC_KNOWLEDGE.value)
        )
    except ValueError:
        return None
    return selected if selected in _ALLOWED_AUTHORITIES else None


def _visibility(value: Any) -> VisibilityClass | None:
    aliases = {
        "public": VisibilityClass.PUBLIC,
        "player_known": VisibilityClass.PLAYER_KNOWN,
        "learned": VisibilityClass.PLAYER_KNOWN,
        "partially_known": VisibilityClass.PLAYER_KNOWN,
        "disputed": VisibilityClass.PLAYER_KNOWN,
        "npc_private": VisibilityClass.NPC_PRIVATE,
        "faction_private": VisibilityClass.FACTION_PRIVATE,
        "narrator_only": VisibilityClass.NARRATOR_ONLY,
        "hidden_from_player": VisibilityClass.GAME_MASTER_ONLY,
        "game_master_canon": VisibilityClass.GAME_MASTER_ONLY,
        "game_master_only": VisibilityClass.GAME_MASTER_ONLY,
    }
    return aliases.get(str(value or "player_known").strip().casefold())


def normalize_hermes_research(
    request: HermesResearchRequest,
    raw: Mapping[str, Any],
    *,
    policy: HermesResearchPolicy | None = None,
) -> HermesResearchResult:
    bounded = (policy or HermesResearchPolicy()).bounded()
    query = _text(request.query, bounded.max_query_chars)
    source_rows: Sequence[Any] = (
        raw_sources if isinstance(raw_sources := raw.get("sources"), list | tuple) else ()
    )
    finding_rows: Sequence[Any] = (
        raw_findings if isinstance(raw_findings := raw.get("findings"), list | tuple) else ()
    )
    sources: list[HermesResearchSource] = []
    source_ids: set[str] = set()
    char_count = 0
    truncated = (
        len(source_rows) > bounded.max_sources
        or len(finding_rows) > bounded.max_findings
    )

    for index, row in enumerate(source_rows[: bounded.max_sources], start=1):
        if not isinstance(row, Mapping):
            continue
        source_id = _text(
            row.get("source_id") or row.get("id") or f"source:{index}",
            160,
        )
        title = _text(row.get("title"), 300)
        citation = _text(
            row.get("citation")
            or row.get("url")
            or row.get("reference"),
            1_000,
        )
        excerpt = _text(
            row.get("excerpt") or row.get("summary"),
            bounded.max_excerpt_chars,
        )
        if not source_id or not title or not citation:
            continue
        projected = len(title) + len(citation) + len(excerpt)
        if char_count + projected > bounded.max_total_chars:
            truncated = True
            break
        char_count += projected
        source_ids.add(source_id)
        sources.append(
            HermesResearchSource(
                source_id=source_id,
                title=title,
                citation=citation,
                excerpt=excerpt,
                published_at=_text(
                    row.get("published_at") or row.get("date"),
                    80,
                ),
                metadata=dict(row.get("metadata") or {}),
            )
        )

    findings: list[HermesResearchFinding] = []
    rejected: list[Mapping[str, Any]] = []
    seen_findings: set[str] = set()
    for index, row in enumerate(
        finding_rows[: bounded.max_findings],
        start=1,
    ):
        if not isinstance(row, Mapping):
            rejected.append({"index": index, "reason": "not_mapping"})
            continue
        finding_id = _text(
            row.get("finding_id") or row.get("id") or f"finding:{index}",
            160,
        )
        content = _text(
            row.get("content")
            or row.get("statement")
            or row.get("summary"),
            2_000,
        )
        source_refs = _strings(
            row.get("source_refs") or row.get("sources")
        )
        disputed = bool(row.get("disputed"))
        authority = _authority(row.get("authority"), disputed)
        visibility = _visibility(row.get("visibility"))
        reason = ""
        if not finding_id or finding_id in seen_findings:
            reason = "missing_or_duplicate_id"
        elif not content:
            reason = "empty_content"
        elif not source_refs or not set(source_refs).issubset(source_ids):
            reason = "missing_or_unknown_source"
        elif authority is None:
            reason = "forbidden_authority"
        elif visibility is None:
            reason = "forbidden_visibility"
        elif char_count + len(content) > bounded.max_total_chars:
            truncated = True
            reason = "character_budget"
        if reason:
            rejected.append(
                {"finding_id": finding_id, "reason": reason}
            )
            continue
        char_count += len(content)
        seen_findings.add(finding_id)
        findings.append(
            HermesResearchFinding(
                finding_id=finding_id,
                content=content,
                source_refs=source_refs,
                authority=cast(Any, authority),
                entity_refs=_strings(
                    row.get("entity_refs") or row.get("entities")
                ),
                confidence=max(
                    0.0,
                    min(float(row.get("confidence") or 0.5), 1.0),
                ),
                disputed=disputed,
                visibility=cast(Any, visibility),
                known_by=_strings(row.get("known_by")),
                metadata=dict(row.get("metadata") or {}),
            )
        )

    return HermesResearchResult(
        research_id=request.research_id,
        campaign_id=request.campaign_id,
        query=query,
        sources=tuple(sources),
        findings=tuple(findings),
        rejected_items=tuple(rejected),
        truncated=truncated,
        provider=_text(raw.get("provider"), 120),
        model=_text(raw.get("model"), 160),
        metadata={
            "purpose": request.purpose,
            "read_only": True,
            "may_mutate_campaign_bible": False,
            "source_limit": bounded.max_sources,
            "finding_limit": bounded.max_findings,
            "character_limit": bounded.max_total_chars,
            **dict(raw.get("metadata") or {}),
        },
    )


def run_bounded_hermes_research(
    request: HermesResearchRequest,
    researcher: HermesNarrativeResearcher,
    *,
    policy: HermesResearchPolicy | None = None,
) -> HermesResearchResult:
    bounded = (policy or HermesResearchPolicy()).bounded()
    safe_request = HermesResearchRequest(
        research_id=_text(request.research_id, 160),
        campaign_id=_text(request.campaign_id, 200),
        query=_text(request.query, bounded.max_query_chars),
        entity_ids=request.entity_ids[:16],
        purpose=_text(request.purpose, 120) or "narrative_grounding",
        metadata=dict(request.metadata),
    )
    if (
        not safe_request.research_id
        or not safe_request.campaign_id
        or not safe_request.query
    ):
        raise ValueError(
            "Hermes narrative research requires research_id, campaign_id, and query"
        )
    raw = researcher.research(safe_request, bounded)
    return normalize_hermes_research(
        safe_request,
        raw,
        policy=bounded,
    )
