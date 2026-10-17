"""Campaign Bible aggregate contracts and evidence projection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, TypeVar

from .authority import AuthorityClass, EvidenceLifetime, VisibilityClass
from .contracts import EvidenceRecord
from .evidence import EvidenceQuery
from enum import Enum


_EnumT = TypeVar("_EnumT", bound=Enum)
_VISIBLE_DISCOVERY_STATUSES = {
    "public_at_campaign_start",
    "learned",
    "partially_known",
    "disputed",
}
_ENTITY_SAFE_FIELDS: dict[str, tuple[str, ...]] = {
    "npc": (
        "appearance",
        "personality",
        "speech_style",
        "role",
        "location_id",
        "faction_ids",
        "description",
        "backstory",
        "goals",
        "motives",
        "relationships",
        "known_facts",
        "current_situation",
    ),
    "location": (
        "region_id",
        "sensory_profile",
        "description",
        "services",
        "inhabitants",
        "landmarks",
    ),
    "faction": (
        "values",
        "public_goal",
        "goals",
        "description",
        "headquarters",
        "territory",
    ),
    "race": ("description", "traits", "culture", "homeland", "languages"),
    "class": ("description", "role", "abilities", "traditions", "requirements"),
    "monster": ("description", "appearance", "habitat", "behavior", "traits"),
    "item": (
        "description",
        "appearance",
        "materials",
        "origin",
        "properties",
        "effects",
        "uses",
        "use",
        "limitations",
        "current_situation",
    ),
    "vehicle": (
        "description",
        "appearance",
        "function",
        "capabilities",
        "limitations",
        "current_situation",
    ),
    "technology": (
        "description",
        "appearance",
        "capability",
        "failure_mode",
        "limitations",
        "current_situation",
    ),
    "network": (
        "description",
        "purpose",
        "security_pressure",
        "access",
        "current_situation",
    ),
    "spell": ("description", "tradition", "effect", "limitations", "components"),
    "feat": ("description", "requirements", "benefits"),
    "quest": (
        "description",
        "premise",
        "stakes",
        "objectives",
        "public_goal",
        "location_id",
        "location_ids",
        "faction_ids",
        "complications",
        "rewards",
        "current_situation",
    ),
}
_GENERIC_ENTITY_SAFE_FIELDS = (
    "description",
    "summary",
    "appearance",
    "role",
    "region_id",
    "location_id",
    "faction_ids",
    "traits",
    "values",
    "public_goal",
    "goals",
)


def _enum(enum_type: type[_EnumT], value: Any, default: _EnumT) -> _EnumT:
    try:
        return enum_type(str(value))
    except (TypeError, ValueError):
        return default


def _visibility(value: Any) -> VisibilityClass:
    normalized = str(value or "public").strip().casefold()
    aliases = {
        "public": VisibilityClass.PUBLIC,
        "public_at_campaign_start": VisibilityClass.PUBLIC,
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
    return aliases.get(normalized, VisibilityClass.GAME_MASTER_ONLY)


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple | set):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or () if isinstance(item, Mapping)]


def _effective_visibility(
    raw_visibility: Any,
    *,
    discovery_status: str = "",
) -> VisibilityClass:
    raw = _visibility(raw_visibility)
    if raw not in {VisibilityClass.PUBLIC, VisibilityClass.PLAYER_KNOWN}:
        return raw
    status = str(discovery_status or "").strip().casefold()
    if not status:
        return raw
    if status not in _VISIBLE_DISCOVERY_STATUSES:
        return VisibilityClass.GAME_MASTER_ONLY
    if status == "public_at_campaign_start":
        return VisibilityClass.PUBLIC
    return VisibilityClass.PLAYER_KNOWN


def _content_value(value: Any) -> str:
    if isinstance(value, Mapping):
        parts = []
        for key, item in value.items():
            text = _content_value(item)
            if text:
                parts.append(f"{key}: {text}")
        return ", ".join(parts)
    if isinstance(value, list | tuple | set):
        return ", ".join(str(item) for item in value if str(item).strip())
    return str(value or "").strip()


def _entity_content(entity_id: str, entity: Mapping[str, Any]) -> str:
    kind = str(entity.get("kind") or "entity").strip().casefold()
    name = str(entity.get("name") or entity.get("title") or entity_id).strip()
    fields = _ENTITY_SAFE_FIELDS.get(kind, _GENERIC_ENTITY_SAFE_FIELDS)
    details = []
    for field in fields:
        value = _content_value(entity.get(field))
        if value:
            details.append(f"{field.replace('_', ' ').title()}: {value}")
    heading = f"{name} ({kind.replace('_', ' ')})"
    return "\n".join((heading, *details)).strip()


@dataclass(frozen=True)
class CampaignBibleSnapshot:
    campaign_id: str
    revision: int
    content_hash: str
    document: Mapping[str, Any]
    provenance: Mapping[str, Any]
    consistency_report: Mapping[str, Any]
    completeness: Mapping[str, Any]

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "CampaignBibleSnapshot":
        return cls(
            campaign_id=str(value.get("campaign_id") or ""),
            revision=int(value.get("revision") or 0),
            content_hash=str(value.get("content_hash") or ""),
            document=dict(value.get("document") or {}),
            provenance=dict(value.get("provenance") or {}),
            consistency_report=dict(value.get("consistency_report") or {}),
            completeness=dict(value.get("completeness") or {}),
        )


def _record(
    snapshot: CampaignBibleSnapshot,
    raw: Mapping[str, Any],
    *,
    index: int,
    evidence_id: str,
    content: str,
    category: str,
    visibility: VisibilityClass | None = None,
    authority: AuthorityClass | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id or f"bible:{snapshot.campaign_id}:{index}",
        content=content,
        authority=authority
        or _enum(
            AuthorityClass,
            raw.get("authority"),
            AuthorityClass.OBJECTIVE_CANON,
        ),
        visibility=visibility or _visibility(raw.get("visibility")),
        known_by=_strings(raw.get("known_by")),
        entity_refs=_strings(raw.get("entity_refs") or raw.get("entities")),
        source_revision=snapshot.revision,
        confidence=max(0.0, min(float(raw.get("confidence") or 1.0), 1.0)),
        lifetime=_enum(
            EvidenceLifetime,
            raw.get("lifetime"),
            EvidenceLifetime.CAMPAIGN,
        ),
        claim_refs=_strings(raw.get("claim_refs")),
        metadata={
            "source": "postgresql_campaign_bible",
            "campaign_id": snapshot.campaign_id,
            "campaign_bible_hash": snapshot.content_hash,
            "category": category,
            "document_id": str(raw.get("document_id") or ""),
            "title": str(raw.get("title") or raw.get("name") or ""),
            "fact_id": str(raw.get("fact_id") or ""),
            "keywords": list(raw.get("keywords") or ()),
            "canon_revision": int(raw.get("canon_revision") or snapshot.revision),
            **dict(metadata or {}),
        },
    )


def campaign_bible_evidence(snapshot: CampaignBibleSnapshot) -> tuple[EvidenceRecord, ...]:
    """Project all authorized canon, including lore pages and dossiers, into evidence."""

    records: list[EvidenceRecord] = []
    index = 0
    for key in ("facts", "relationships", "retrieval_cards", "knowledge_rules"):
        for raw in _rows(snapshot.document.get(key)):
            content = str(
                raw.get("content")
                or raw.get("summary")
                or raw.get("statement")
                or ""
            ).strip()
            if not content:
                continue
            index += 1
            records.append(
                _record(
                    snapshot,
                    raw,
                    index=index,
                    evidence_id=str(raw.get("evidence_id") or raw.get("id") or ""),
                    content=content,
                    category=str(raw.get("category") or key.rstrip("s")),
                    metadata={"record_type": key.rstrip("s")},
                )
            )

    discovery = _mapping(snapshot.document.get("discovery_state"))
    page_statuses = _mapping(discovery.get("pages"))
    entity_statuses = _mapping(discovery.get("entities"))

    for raw in _rows(snapshot.document.get("documents")):
        document_id = str(raw.get("document_id") or raw.get("id") or "").strip()
        title = str(raw.get("title") or document_id or "Campaign lore").strip()
        body = str(
            raw.get("full_text")
            or raw.get("summary_500")
            or raw.get("summary_120")
            or raw.get("summary")
            or ""
        ).strip()
        if not body:
            continue
        index += 1
        status = str(page_statuses.get(document_id) or "")
        disputed = status == "disputed" or str(raw.get("visibility") or "") == "disputed"
        records.append(
            _record(
                snapshot,
                raw,
                index=index,
                evidence_id=str(
                    raw.get("evidence_id")
                    or f"bible:document:{document_id or index}"
                ),
                content=f"{title}\n{body}"[:8_000],
                category=str(raw.get("category") or raw.get("topic_id") or "world_lore"),
                visibility=_effective_visibility(
                    raw.get("visibility"),
                    discovery_status=status,
                ),
                authority=(
                    AuthorityClass.DISPUTED_CLAIM
                    if disputed
                    else _enum(
                        AuthorityClass,
                        raw.get("authority"),
                        AuthorityClass.OBJECTIVE_CANON,
                    )
                ),
                metadata={
                    "record_type": "document",
                    "topic_id": str(raw.get("topic_id") or ""),
                    "discovery_status": status,
                },
            )
        )

    entities = _mapping(snapshot.document.get("entities"))
    for entity_id, value in entities.items():
        if not isinstance(value, Mapping):
            continue
        raw = {"id": str(entity_id), **dict(value)}
        raw["entity_refs"] = list(
            dict.fromkeys((str(entity_id), *_strings(raw.get("entity_refs"))))
        )
        content = _entity_content(str(entity_id), raw)
        if not content:
            continue
        index += 1
        status = str(entity_statuses.get(str(entity_id)) or "")
        records.append(
            _record(
                snapshot,
                raw,
                index=index,
                evidence_id=str(raw.get("evidence_id") or f"bible:entity:{entity_id}"),
                content=content[:4_000],
                category=str(raw.get("kind") or "entity"),
                visibility=_effective_visibility(
                    raw.get("visibility"),
                    discovery_status=status,
                ),
                authority=_enum(
                    AuthorityClass,
                    raw.get("authority"),
                    AuthorityClass.PUBLIC_KNOWLEDGE,
                ),
                metadata={
                    "record_type": "entity",
                    "entity_id": str(entity_id),
                    "entity_kind": str(raw.get("kind") or "entity"),
                    "discovery_status": status,
                },
            )
        )

    by_id: dict[str, EvidenceRecord] = {}
    for record in records:
        previous = by_id.get(record.evidence_id)
        if previous is None or record.source_revision >= previous.source_revision:
            by_id[record.evidence_id] = record
    return tuple(by_id.values())


class CampaignBibleEvidenceSource:
    source_id = "postgresql_campaign_bible"

    def __init__(self, snapshot: CampaignBibleSnapshot) -> None:
        self.snapshot = snapshot
        self._records = campaign_bible_evidence(snapshot)

    def records(self, query: EvidenceQuery) -> Sequence[EvidenceRecord]:
        return self._records
