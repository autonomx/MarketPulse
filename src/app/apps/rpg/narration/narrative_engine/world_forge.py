"""Deterministic World Forge proposal, cross-linking, and approval pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .campaign_bible import CampaignBibleSnapshot
from .contracts import stable_hash

_PROPOSAL_COLLECTIONS = (
    "entities",
    "facts",
    "relationships",
    "retrieval_cards",
)


def _validated_rows(value: Any, key: str) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"world_forge_proposal.{key}_must_be_array")
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ValueError(f"world_forge_proposal.{key}[{index}]_must_be_object")
        rows.append(dict(row))
    return tuple(rows)


@dataclass(frozen=True)
class WorldForgeIssue:
    code: str
    message: str
    item_id: str = ""
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "item_id": self.item_id,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class WorldForgeAudit:
    passed: bool
    issues: tuple[WorldForgeIssue, ...] = ()
    cross_links: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [issue.as_dict() for issue in self.issues],
            "cross_links": {
                key: list(value) for key, value in sorted(self.cross_links.items())
            },
        }


@dataclass(frozen=True)
class WorldForgeProposal:
    """Structurally valid proposal presented to the deterministic approval audit."""

    proposal_id: str
    campaign_id: str
    base_bible_revision: int
    entities: tuple[Mapping[str, Any], ...] = ()
    facts: tuple[Mapping[str, Any], ...] = ()
    relationships: tuple[Mapping[str, Any], ...] = ()
    retrieval_cards: tuple[Mapping[str, Any], ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        proposal_id = str(self.proposal_id or "").strip()
        campaign_id = str(self.campaign_id or "").strip()
        if not proposal_id:
            raise ValueError("world_forge_proposal.proposal_id_required")
        if not campaign_id:
            raise ValueError("world_forge_proposal.campaign_id_required")
        if isinstance(self.base_bible_revision, bool) or not isinstance(
            self.base_bible_revision, int
        ):
            raise ValueError("world_forge_proposal.base_bible_revision_must_be_integer")
        if self.base_bible_revision < 0:
            raise ValueError("world_forge_proposal.base_bible_revision_must_be_nonnegative")
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "campaign_id", campaign_id)
        for key in _PROPOSAL_COLLECTIONS:
            object.__setattr__(self, key, _validated_rows(getattr(self, key), key))
        if not isinstance(self.provenance, Mapping):
            raise ValueError("world_forge_proposal.provenance_must_be_object")
        object.__setattr__(self, "provenance", dict(self.provenance))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorldForgeProposal":
        if not isinstance(value, Mapping):
            raise ValueError("world_forge_proposal.root_must_be_object")
        revision = value.get("base_bible_revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ValueError("world_forge_proposal.base_bible_revision_must_be_integer")
        provenance = value.get("provenance")
        if provenance is None:
            provenance = {}
        if not isinstance(provenance, Mapping):
            raise ValueError("world_forge_proposal.provenance_must_be_object")
        return cls(
            proposal_id=str(value.get("proposal_id") or ""),
            campaign_id=str(value.get("campaign_id") or ""),
            base_bible_revision=revision,
            entities=_validated_rows(value.get("entities"), "entities"),
            facts=_validated_rows(value.get("facts"), "facts"),
            relationships=_validated_rows(
                value.get("relationships"), "relationships"
            ),
            retrieval_cards=_validated_rows(
                value.get("retrieval_cards"), "retrieval_cards"
            ),
            provenance=dict(provenance),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "campaign_id": self.campaign_id,
            "base_bible_revision": self.base_bible_revision,
            "entities": [dict(row) for row in self.entities],
            "facts": [dict(row) for row in self.facts],
            "relationships": [dict(row) for row in self.relationships],
            "retrieval_cards": [dict(row) for row in self.retrieval_cards],
            "provenance": dict(self.provenance),
        }

    @property
    def proposal_hash(self) -> str:
        return stable_hash(self.as_dict())


@dataclass(frozen=True)
class WorldForgeProposalValidationReceipt:
    schema_version: str
    proposal_id: str
    proposal_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "proposal_hash": self.proposal_hash,
        }


@dataclass(frozen=True)
class ValidatedWorldForgeProposal:
    proposal: WorldForgeProposal
    receipt: WorldForgeProposalValidationReceipt


def validate_world_forge_proposal_for_publication(
    proposal: WorldForgeProposal,
) -> ValidatedWorldForgeProposal:
    if not isinstance(proposal, WorldForgeProposal):
        raise TypeError("world_forge_proposal.publication_requires_domain_type")
    canonical = WorldForgeProposal.from_dict(proposal.as_dict())
    return ValidatedWorldForgeProposal(
        proposal=canonical,
        receipt=WorldForgeProposalValidationReceipt(
            schema_version="rpg_world_forge_proposal_domain_v2",
            proposal_id=canonical.proposal_id,
            proposal_hash=canonical.proposal_hash,
        ),
    )


def _item_id(row: Mapping[str, Any], prefix: str, index: int) -> str:
    return str(row.get("id") or row.get("evidence_id") or f"{prefix}:{index}")


def _entity_ids(document: Mapping[str, Any]) -> set[str]:
    raw = document.get("entities")
    if isinstance(raw, Mapping):
        return {str(key) for key in raw}
    if isinstance(raw, (list, tuple)):
        result: set[str] = set()
        for index, row in enumerate(raw):
            if not isinstance(row, Mapping):
                raise ValueError(f"campaign_bible.entities[{index}]_must_be_object")
            if row.get("id"):
                result.add(str(row["id"]))
        return result
    if raw is None:
        return set()
    raise ValueError("campaign_bible.entities_must_be_object_or_array")


def _fact_identity(row: Mapping[str, Any], prefix: str, index: int) -> str:
    explicit = str(row.get("id") or row.get("evidence_id") or "").strip()
    if explicit:
        return explicit
    subject = str(row.get("subject") or "").strip()
    predicate = str(row.get("predicate") or "").strip()
    return f"{prefix}:{subject}:{predicate}" if subject and predicate else f"{prefix}:{index}"


def _meaning(row: Mapping[str, Any]) -> str:
    return str(
        row.get("object")
        or row.get("content")
        or row.get("statement")
        or row.get("summary")
        or ""
    ).strip().casefold()


def _existing_rows(document: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    raw = document.get(key)
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"campaign_bible.{key}_must_be_array")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, Mapping):
            raise ValueError(f"campaign_bible.{key}[{index}]_must_be_object")
        rows.append(dict(row))
    return rows


def audit_world_forge_proposal(
    snapshot: CampaignBibleSnapshot,
    proposal: WorldForgeProposal,
) -> WorldForgeAudit:
    proposal = validate_world_forge_proposal_for_publication(proposal).proposal
    issues: list[WorldForgeIssue] = []
    if proposal.campaign_id != snapshot.campaign_id:
        issues.append(
            WorldForgeIssue(
                "campaign_mismatch",
                "Proposal campaign does not match the Campaign Bible.",
            )
        )
    if proposal.base_bible_revision != snapshot.revision:
        issues.append(
            WorldForgeIssue(
                "stale_bible_revision",
                f"Proposal targets revision {proposal.base_bible_revision}; current is {snapshot.revision}.",
            )
        )

    known_entities = _entity_ids(snapshot.document)
    proposed_entities: set[str] = set()
    for index, row in enumerate(proposal.entities, start=1):
        entity_id = _item_id(row, "entity", index)
        if entity_id in proposed_entities or entity_id in known_entities:
            issues.append(
                WorldForgeIssue(
                    "duplicate_entity",
                    "Entity already exists or is duplicated.",
                    entity_id,
                )
            )
        proposed_entities.add(entity_id)

    existing: dict[str, str] = {}
    for key in ("facts", "relationships"):
        for index, row in enumerate(_existing_rows(snapshot.document, key), start=1):
            existing[_fact_identity(row, key[:-1], index)] = _meaning(row)

    seen: set[str] = set()
    cross_links: dict[str, tuple[str, ...]] = {}
    for group, rows in (
        ("fact", proposal.facts),
        ("relationship", proposal.relationships),
        ("card", proposal.retrieval_cards),
    ):
        for index, row in enumerate(rows, start=1):
            item_id = _fact_identity(row, group, index)
            if item_id in seen:
                issues.append(
                    WorldForgeIssue(
                        "duplicate_item",
                        "Proposal item identifier is duplicated.",
                        item_id,
                    )
                )
            seen.add(item_id)
            meaning = _meaning(row)
            if not meaning:
                issues.append(
                    WorldForgeIssue(
                        "empty_meaning",
                        "Proposal item has no narrative meaning.",
                        item_id,
                    )
                )
            prior = existing.get(item_id)
            if prior is not None and prior != meaning:
                issues.append(
                    WorldForgeIssue(
                        "contradiction",
                        "Proposal conflicts with existing Campaign Bible meaning.",
                        item_id,
                    )
                )
            refs = tuple(
                str(ref)
                for ref in (row.get("entity_refs") or row.get("entities") or ())
                if str(ref).strip()
            )
            cross_links[item_id] = refs
            for ref in refs:
                if ref not in known_entities and ref not in proposed_entities:
                    issues.append(
                        WorldForgeIssue(
                            "dangling_entity_ref",
                            f"Unknown entity reference: {ref}",
                            item_id,
                        )
                    )
            if (
                group != "card"
                and str(row.get("authority") or "generated_proposal")
                != "generated_proposal"
            ):
                issues.append(
                    WorldForgeIssue(
                        "proposal_claims_authority",
                        "Unapproved World Forge items must use generated_proposal authority.",
                        item_id,
                    )
                )
    return WorldForgeAudit(
        passed=not issues,
        issues=tuple(issues),
        cross_links=cross_links,
    )


def apply_world_forge_proposal(
    snapshot: CampaignBibleSnapshot,
    proposal: WorldForgeProposal,
) -> tuple[dict[str, Any], WorldForgeAudit]:
    validated = validate_world_forge_proposal_for_publication(proposal)
    proposal = validated.proposal
    audit = audit_world_forge_proposal(snapshot, proposal)
    if not audit.passed:
        return dict(snapshot.document), audit

    document = dict(snapshot.document)
    entities = document.get("entities")
    if entities is None:
        entity_map: dict[str, Any] = {}
    elif isinstance(entities, Mapping):
        entity_map = dict(entities)
    else:
        raise ValueError("campaign_bible.entities_must_be_object_for_publication")
    for index, row in enumerate(proposal.entities, start=1):
        entity_id = _item_id(row, "entity", index)
        entity_map[entity_id] = {**dict(row), "id": entity_id}
    document["entities"] = entity_map

    for key, rows in (
        ("facts", proposal.facts),
        ("relationships", proposal.relationships),
        ("retrieval_cards", proposal.retrieval_cards),
    ):
        existing_rows = _existing_rows(document, key)
        approved: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if key != "retrieval_cards":
                item["authority"] = str(
                    item.pop("approved_authority", "objective_canon")
                )
            item["approved_from_proposal"] = proposal.proposal_id
            item["proposal_hash"] = proposal.proposal_hash
            approved.append(item)
        document[key] = existing_rows + approved

    provenance = document.get("generation_provenance")
    if provenance is None:
        provenance_map: dict[str, Any] = {}
    elif isinstance(provenance, Mapping):
        provenance_map = dict(provenance)
    else:
        raise ValueError("campaign_bible.generation_provenance_must_be_object")
    provenance_map[proposal.proposal_id] = {
        "proposal_hash": proposal.proposal_hash,
        "base_bible_revision": proposal.base_bible_revision,
        "validation_receipt": validated.receipt.as_dict(),
        **dict(proposal.provenance),
    }
    document["generation_provenance"] = provenance_map
    return document, audit
