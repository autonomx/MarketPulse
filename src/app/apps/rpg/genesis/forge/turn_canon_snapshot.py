"""Select one complete Campaign Bible snapshot for current-turn grounding."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping, Sequence

from app.apps.rpg.narration.narrative_engine import (
    CampaignBibleSnapshot,
    EvidenceRecord,
    campaign_bible_evidence,
)

_LIST_ID_FIELDS = {
    "documents": ("document_id", "id"),
    "facts": ("id", "evidence_id"),
    "retrieval_cards": ("id", "evidence_id"),
    "relationships": ("id",),
    "knowledge_rules": ("id",),
    "story_threads": ("id",),
}


def _mapping(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _row_id(row: Mapping[str, Any], fields: Sequence[str], index: int) -> str:
    return next(
        (str(row.get(field) or "") for field in fields if row.get(field)),
        f"row:{index}",
    )


def _merge_rows(
    first: Any,
    second: Any,
    fields: Sequence[str],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for source in (first or (), second or ()):
        for index, value in enumerate(source, start=1):
            if not isinstance(value, Mapping):
                continue
            row = deepcopy(dict(value))
            row_id = _row_id(row, fields, index)
            if row_id not in rows:
                rows[row_id] = row
    return list(rows.values())


def _merge_entities(first: Any, second: Any) -> dict[str, Any]:
    entities = _mapping(second)
    for entity_id, value in _mapping(first).items():
        if not isinstance(value, Mapping):
            entities.setdefault(str(entity_id), deepcopy(value))
            continue
        existing = _mapping(entities.get(entity_id))
        # Established durable canon wins; portable materialization only fills gaps.
        merged = deepcopy(existing)
        for key, item in value.items():
            if item not in (None, "", [], {}):
                merged[key] = deepcopy(item)
            else:
                merged.setdefault(key, deepcopy(item))
        entities[str(entity_id)] = merged
    return entities


def _merge_discovery(first: Any, second: Any) -> dict[str, Any]:
    left = _mapping(first)
    right = _mapping(second)
    return {
        "pages": {**_mapping(left.get("pages")), **_mapping(right.get("pages"))},
        "entities": {
            **_mapping(left.get("entities")),
            **_mapping(right.get("entities")),
        },
        "discoveries": list(
            {
                json.dumps(value, sort_keys=True, default=str): deepcopy(value)
                for value in (
                    *list(left.get("discoveries") or ()),
                    *list(right.get("discoveries") or ()),
                )
            }.values()
        ),
    }


def _merged_document(
    durable: Mapping[str, Any],
    portable: Mapping[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(dict(durable))
    for key, value in portable.items():
        merged.setdefault(key, deepcopy(value))
    for key, fields in _LIST_ID_FIELDS.items():
        merged[key] = _merge_rows(durable.get(key), portable.get(key), fields)
    merged["entities"] = _merge_entities(
        durable.get("entities"),
        portable.get("entities"),
    )
    merged["discovery_state"] = _merge_discovery(
        durable.get("discovery_state"),
        portable.get("discovery_state"),
    )
    merged["indexes"] = {
        **_mapping(durable.get("indexes")),
        **_mapping(portable.get("indexes")),
    }
    merged["mechanics_catalog"] = {
        **_mapping(durable.get("mechanics_catalog")),
        **_mapping(portable.get("mechanics_catalog")),
    }
    merged["canon_revision"] = max(
        int(durable.get("canon_revision") or 0),
        int(portable.get("canon_revision") or 0),
    )
    return merged


def _content_hash(document: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(document))
    payload.pop("content_hash", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _missing(
    evidence: Sequence[EvidenceRecord],
    target_entity_ids: Sequence[str],
) -> tuple[str, ...]:
    referenced = {
        entity_id.casefold()
        for record in evidence
        for entity_id in record.entity_refs
        if str(entity_id).strip()
    }
    return tuple(
        entity_id
        for entity_id in target_entity_ids
        if entity_id and entity_id.casefold() not in referenced
    )


def select_complete_turn_snapshot(
    loaded: CampaignBibleSnapshot | None,
    portable: CampaignBibleSnapshot | None,
    target_entity_ids: Sequence[str],
) -> tuple[
    CampaignBibleSnapshot | None,
    tuple[EvidenceRecord, ...],
    tuple[str, ...],
]:
    """Prefer a merged snapshot so newly generated canon never hides established lore."""

    candidates: list[CampaignBibleSnapshot] = []
    if loaded is not None and portable is not None:
        document = _merged_document(loaded.document, portable.document)
        candidates.append(
            CampaignBibleSnapshot(
                campaign_id=loaded.campaign_id or portable.campaign_id,
                revision=max(loaded.revision, portable.revision),
                content_hash=_content_hash(document),
                document=document,
                provenance={
                    "source": "merged_turn_campaign_bible",
                    "durable_hash": loaded.content_hash,
                    "portable_hash": portable.content_hash,
                },
                consistency_report=loaded.consistency_report,
                completeness={
                    **dict(loaded.completeness),
                    **dict(portable.completeness),
                },
            )
        )
    candidates.extend(
        snapshot for snapshot in (loaded, portable) if snapshot is not None
    )

    best_snapshot: CampaignBibleSnapshot | None = None
    best_evidence: tuple[EvidenceRecord, ...] = ()
    best_missing = tuple(str(value) for value in target_entity_ids if str(value))
    for snapshot in candidates:
        evidence = tuple(campaign_bible_evidence(snapshot))
        missing = _missing(evidence, target_entity_ids)
        if best_snapshot is None or len(missing) < len(best_missing):
            best_snapshot = snapshot
            best_evidence = evidence
            best_missing = missing
        if not missing:
            return snapshot, evidence, ()
    return best_snapshot, best_evidence, best_missing
