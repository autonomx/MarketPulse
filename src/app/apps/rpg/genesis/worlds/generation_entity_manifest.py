"""Planner-owned canonical entity slots for World Forge generation runs."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicGraph

_NON_GENERATION_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}
_SAFE_ID = re.compile(r"[^a-z0-9_.:-]+")


class EntityManifestContractError(ValueError):
    """Raised before job creation when manifest ownership is ambiguous."""


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _safe(value: str) -> str:
    rendered = _SAFE_ID.sub("-", value.strip().casefold()).strip("-")
    return rendered or "entity"


def _slot(
    *,
    topic_id: str,
    ordinal: int,
    entity_id: str = "",
    slot_id: str = "",
    domain_id: str = "",
    entity_kind: str = "",
    name_hint: str = "",
) -> dict[str, Any]:
    safe_topic = _safe(topic_id)
    return {
        "slot_id": slot_id or f"slot:{safe_topic}:{ordinal:03d}",
        "topic_id": topic_id,
        "ordinal": int(ordinal),
        "entity_id": entity_id or f"ent:{safe_topic}:{ordinal:03d}",
        "domain_id": domain_id or topic_id,
        "entity_kind": entity_kind,
        "name_hint": name_hint,
    }


def _supplied_slots(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = value.get("slots")
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    slots: list[dict[str, Any]] = []
    for topic_id, topic_value in value.items():
        if topic_id in {"schema_version", "metadata", "content_hash", "topics"}:
            continue
        if not isinstance(topic_value, Sequence) or isinstance(topic_value, (str, bytes)):
            continue
        for ordinal, item in enumerate(topic_value, start=1):
            row = dict(item) if isinstance(item, Mapping) else {"entity_id": str(item)}
            slots.append(
                _slot(
                    topic_id=str(topic_id),
                    ordinal=int(row.get("ordinal") or ordinal),
                    entity_id=str(row.get("entity_id") or row.get("id") or ""),
                    slot_id=str(row.get("slot_id") or ""),
                    domain_id=str(row.get("domain_id") or ""),
                    entity_kind=str(row.get("entity_kind") or row.get("kind") or ""),
                    name_hint=str(row.get("name_hint") or row.get("name") or ""),
                )
            )
    return slots


def _generated_nodes(graph: CampaignTopicGraph) -> dict[str, Any]:
    return {
        node.topic_id: node
        for node in graph.topological_order()
        if node.category not in _NON_GENERATION_CATEGORIES
    }


def _normalize_supplied_slot(row: Mapping[str, Any]) -> dict[str, Any]:
    return _slot(
        topic_id=str(row.get("topic_id") or ""),
        ordinal=int(row.get("ordinal") or 0),
        entity_id=str(row.get("entity_id") or row.get("id") or ""),
        slot_id=str(row.get("slot_id") or ""),
        domain_id=str(row.get("domain_id") or ""),
        entity_kind=str(row.get("entity_kind") or row.get("kind") or ""),
        name_hint=str(row.get("name_hint") or row.get("name") or ""),
    )


def _validate_slots(
    slots: Sequence[Mapping[str, Any]],
    graph: CampaignTopicGraph,
) -> None:
    nodes = _generated_nodes(graph)
    issues: list[str] = []
    slot_ids = [str(row.get("slot_id") or "") for row in slots]
    entity_ids = [str(row.get("entity_id") or "") for row in slots]
    for duplicate in sorted(
        key for key, count in Counter(slot_ids).items() if key and count > 1
    ):
        issues.append(f"duplicate_slot_id:{duplicate}")
    for duplicate in sorted(
        key for key, count in Counter(entity_ids).items() if key and count > 1
    ):
        issues.append(f"duplicate_entity_id:{duplicate}")

    by_topic: dict[str, list[Mapping[str, Any]]] = {}
    for row in slots:
        topic_id = str(row.get("topic_id") or "")
        ordinal = int(row.get("ordinal") or 0)
        if topic_id not in nodes:
            issues.append(f"unknown_manifest_topic:{topic_id or '<missing>'}")
            continue
        if ordinal < 1:
            issues.append(f"invalid_manifest_ordinal:{topic_id}:{ordinal}")
        if not str(row.get("slot_id") or ""):
            issues.append(f"manifest_slot_id_required:{topic_id}:{ordinal}")
        if not str(row.get("entity_id") or ""):
            issues.append(f"manifest_entity_id_required:{topic_id}:{ordinal}")
        by_topic.setdefault(topic_id, []).append(row)

    for topic_id, node in nodes.items():
        topic_slots = by_topic.get(topic_id, [])
        expected = int(node.target_count)
        if len(topic_slots) != expected:
            issues.append(
                f"manifest_topic_cardinality:{topic_id}:expected={expected}:actual={len(topic_slots)}"
            )
        ordinals = sorted(int(row.get("ordinal") or 0) for row in topic_slots)
        if ordinals != list(range(1, expected + 1)):
            issues.append(f"manifest_topic_ordinals:{topic_id}")

    if issues:
        raise EntityManifestContractError(
            "invalid_world_generation_entity_manifest:" + ",".join(sorted(set(issues)))
        )


def build_entity_manifest(
    graph: CampaignTopicGraph,
    supplied: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze exact canonical entity slots before provider work is scheduled."""

    nodes = _generated_nodes(graph)
    supplied_payload = dict(supplied or {})
    raw_slots = _supplied_slots(supplied_payload)
    if raw_slots:
        slots = [_normalize_supplied_slot(row) for row in raw_slots]
    elif supplied_payload:
        raise EntityManifestContractError(
            "invalid_world_generation_entity_manifest:manifest_slots_required"
        )
    else:
        slots = [
            _slot(
                topic_id=topic_id,
                ordinal=ordinal,
                domain_id=str(node.metadata.get("domain_id") or topic_id),
                entity_kind=str(node.metadata.get("entity_kind") or ""),
            )
            for topic_id, node in nodes.items()
            for ordinal in range(1, int(node.target_count) + 1)
        ]
    slots = sorted(slots, key=lambda row: (str(row["topic_id"]), int(row["ordinal"])))
    _validate_slots(slots, graph)
    topics = {
        topic_id: [str(row["slot_id"]) for row in slots if row["topic_id"] == topic_id]
        for topic_id in nodes
    }
    payload = {
        "schema_version": "rpg_world_entity_manifest_slots_v1",
        "slots": slots,
        "topics": topics,
        "slot_count": len(slots),
        "metadata": dict(supplied_payload.get("metadata") or {}),
    }
    return {**payload, "content_hash": _hash(payload)}


def topic_manifest_slots(
    manifest: Mapping[str, Any] | None,
    topic_id: str,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(manifest, Mapping):
        return ()
    return tuple(
        dict(row)
        for row in manifest.get("slots") or ()
        if isinstance(row, Mapping) and str(row.get("topic_id") or "") == topic_id
    )


__all__ = [
    "EntityManifestContractError",
    "build_entity_manifest",
    "topic_manifest_slots",
]
