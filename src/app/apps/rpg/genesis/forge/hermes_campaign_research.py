"""Hermes turn researcher over established campaign canon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from app.apps.rpg.narration.narrative_engine import (
    AuthorityClass,
    CampaignBibleEvidenceSource,
    CampaignBibleSnapshot,
    EvidenceAccessContext,
    EvidenceBroker,
    EvidenceQuery,
    HermesResearchPolicy,
    HermesResearchRequest,
    HermesResearchResult,
    run_bounded_hermes_research,
)

from .campaign_bible_runtime import load_campaign_bible_snapshot

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions


@dataclass(frozen=True)
class CampaignResearchPacket:
    result: HermesResearchResult
    snapshot: CampaignBibleSnapshot
    topic_titles: tuple[str, ...]
    selected_evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.as_dict(),
            "campaign_bible_revision": self.snapshot.revision,
            "campaign_bible_hash": self.snapshot.content_hash,
            "topic_titles": list(self.topic_titles),
            "selected_evidence_ids": list(self.selected_evidence_ids),
            "topic_count": len(self.topic_titles),
            "read_only": True,
            "state_changed": False,
        }


def _title(record: Any) -> str:
    metadata = dict(record.metadata)
    title = str(metadata.get("title") or "").strip()
    if title:
        return title
    category = str(metadata.get("category") or "Campaign Canon")
    return category.replace("_", " ").title()


def _research_authority(authority: AuthorityClass) -> str:
    """Hermes reports sources; CampaignBibleEvidenceSource retains canon authority."""

    if authority is AuthorityClass.RUMOR:
        return AuthorityClass.RUMOR.value
    if authority is AuthorityClass.DISPUTED_CLAIM:
        return AuthorityClass.DISPUTED_CLAIM.value
    if authority in {AuthorityClass.NPC_BELIEF, AuthorityClass.FACTION_DOCTRINE}:
        return AuthorityClass.PUBLIC_KNOWLEDGE.value
    return AuthorityClass.HISTORICAL_RECORD.value


class CampaignBibleHermesResearcher:
    """Select at most five relevant, already-authorized Campaign Bible topics."""

    def __init__(
        self,
        snapshot: CampaignBibleSnapshot,
        *,
        access: EvidenceAccessContext,
    ) -> None:
        self.snapshot = snapshot
        self.access = access

    def research(
        self,
        request: HermesResearchRequest,
        policy: HermesResearchPolicy,
    ) -> Mapping[str, Any]:
        source_limit = min(5, policy.max_sources)
        broker = EvidenceBroker(
            [CampaignBibleEvidenceSource(self.snapshot)]
        )
        retrieval = broker.retrieve(
            EvidenceQuery(
                text=request.query,
                entity_ids=request.entity_ids,
                limit=max(source_limit * 3, source_limit),
                access=self.access,
            )
        )
        selected = []
        seen_topics: set[str] = set()
        for record in retrieval.evidence:
            metadata = dict(record.metadata)
            topic_key = str(
                metadata.get("document_id")
                or metadata.get("category")
                or record.evidence_id
            )
            if topic_key in seen_topics:
                continue
            seen_topics.add(topic_key)
            selected.append(record)
            if len(selected) >= source_limit:
                break

        sources = []
        findings = []
        for index, record in enumerate(selected, start=1):
            metadata = dict(record.metadata)
            source_id = f"canon-topic:{index}:{record.evidence_id}"
            title = _title(record)
            sources.append(
                {
                    "source_id": source_id,
                    "title": title,
                    "citation": (
                        f"campaign-bible:{self.snapshot.campaign_id}"
                        f"@{self.snapshot.revision}#{record.evidence_id}"
                    ),
                    "excerpt": record.content,
                    "metadata": {
                        "evidence_id": record.evidence_id,
                        "document_id": metadata.get("document_id"),
                        "category": metadata.get("category"),
                        "campaign_bible_hash": self.snapshot.content_hash,
                    },
                }
            )
            findings.append(
                {
                    "finding_id": f"topic:{index}",
                    "content": record.content,
                    "source_refs": [source_id],
                    "authority": _research_authority(record.authority),
                    "visibility": record.visibility.value,
                    "known_by": list(record.known_by),
                    "entity_refs": list(record.entity_refs),
                    "confidence": record.confidence,
                    "disputed": record.authority
                    is AuthorityClass.DISPUTED_CLAIM,
                    "metadata": {
                        "original_evidence_id": record.evidence_id,
                        "original_authority": record.authority.value,
                        "canon_revision": self.snapshot.revision,
                        "topic_title": title,
                        "campaign_bible_hash": self.snapshot.content_hash,
                    },
                }
            )
        return {
            "provider": "hermes_campaign_bible",
            "model": "deterministic_topic_selector_v1",
            "sources": sources,
            "findings": findings,
            "metadata": {
                "campaign_bible_revision": self.snapshot.revision,
                "campaign_bible_hash": self.snapshot.content_hash,
                "candidate_count": retrieval.trace.candidate_count,
                "excluded": [list(row) for row in retrieval.trace.excluded],
                "selected_evidence_ids": [
                    record.evidence_id for record in selected
                ],
                "topic_titles": [_title(record) for record in selected],
                "read_only": True,
                "may_mutate_campaign_bible": False,
            },
        }


def research_campaign_turn(
    *,
    sessions: CampaignSessions,
    campaign_id: str,
    query: str,
    session: Mapping[str, Any] | None = None,
    speaker_id: str | None = None,
    actor_ids: tuple[str, ...] = (),
    faction_ids: tuple[str, ...] = (),
    entity_ids: tuple[str, ...] = (),
    research_id: str | None = None,
    max_topics: int = 5,
    snapshot: CampaignBibleSnapshot | None = None,
) -> CampaignResearchPacket | None:
    """Research one turn against the exact snapshot selected for narrative grounding."""

    resolved_snapshot = snapshot or load_campaign_bible_snapshot(
        campaign_id,
        session=session,
        sessions=sessions,
    )
    if resolved_snapshot is None:
        return None
    access = EvidenceAccessContext(
        player_id="player",
        speaker_id=speaker_id,
        actor_ids=actor_ids,
        faction_ids=faction_ids,
        narrator_mode=False,
    )
    request = HermesResearchRequest(
        research_id=research_id
        or f"research:{campaign_id}:{abs(hash(query)) & 0xFFFFFFFF:x}",
        campaign_id=campaign_id,
        query=query or "current campaign context",
        entity_ids=entity_ids,
        purpose="campaign_bible_turn_grounding",
        metadata={
            "speaker_id": speaker_id,
            "actor_ids": list(actor_ids),
            "faction_ids": list(faction_ids),
            "evidence_scope": "speaker" if speaker_id else "player",
        },
    )
    policy = HermesResearchPolicy(
        max_sources=max(1, min(int(max_topics), 5)),
        max_findings=max(1, min(int(max_topics), 5)),
        max_total_chars=8_000,
        max_excerpt_chars=1_200,
        max_query_chars=500,
    )
    result = run_bounded_hermes_research(
        request,
        CampaignBibleHermesResearcher(resolved_snapshot, access=access),
        policy=policy,
    )
    return CampaignResearchPacket(
        result=result,
        snapshot=resolved_snapshot,
        topic_titles=tuple(
            str(source.title) for source in result.sources
        ),
        selected_evidence_ids=tuple(
            str(finding.metadata.get("original_evidence_id") or "")
            for finding in result.findings
            if finding.metadata.get("original_evidence_id")
        ),
    )
