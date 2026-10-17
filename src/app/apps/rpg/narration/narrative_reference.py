"""Bounded identity projection for durable canonical narrative replay."""
from __future__ import annotations

from typing import Any, Mapping


def canonical_narrative_reference(result: Mapping[str, Any] | None) -> dict[str, Any]:
    root = dict(result) if isinstance(result, Mapping) else {}
    canonical = root.get("canonical_narrative_response")
    if not isinstance(canonical, Mapping):
        return {}
    response_id = str(canonical.get("response_id") or "").strip()
    content_hash = str(canonical.get("content_hash") or "").strip()
    campaign_id = str(canonical.get("campaign_id") or "").strip()
    turn_id = str(canonical.get("turn_id") or "").strip()
    if not response_id or not content_hash or not campaign_id or not turn_id:
        return {}
    blocks = canonical.get("blocks")
    return {
        "format_version": "rpg_canonical_narrative_reference_v1",
        "response_id": response_id,
        "content_hash": content_hash,
        "campaign_id": campaign_id,
        "turn_id": turn_id,
        "revision": max(1, int(canonical.get("revision") or 1)),
        "schema_version": str(
            canonical.get("schema_version") or "rpg_narrative_response_v1"
        ),
        "block_count": len(blocks) if isinstance(blocks, list | tuple) else 0,
    }


def compact_canonical_narrative_reference(value: Any) -> dict[str, Any]:
    reference = dict(value) if isinstance(value, Mapping) else {}
    output: dict[str, Any] = {}
    for key in (
        "format_version",
        "response_id",
        "content_hash",
        "campaign_id",
        "turn_id",
        "schema_version",
    ):
        text = str(reference.get(key) or "").strip()
        if text:
            output[key] = text[:512]
    for key in ("revision", "block_count"):
        value = reference.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            output[key] = max(0, value)
    required = ("response_id", "content_hash", "campaign_id", "turn_id")
    return output if all(output.get(key) for key in required) else {}
