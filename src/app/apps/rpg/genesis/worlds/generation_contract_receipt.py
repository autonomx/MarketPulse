"""Trusted contract receipts for World Forge candidates."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.generation_authorship_signing import verify_record_signature

RECEIPT_SCHEMA_VERSION = "rpg_world_forge_contract_receipt_v1"
CONTRACT_DESCRIPTOR_KEYS = (
    "contract_id",
    "contract_version",
    "provider_schema_hash",
    "authored_schema_hash",
    "prompt_contract_hash",
    "canonical_contract_hash",
    "dossier_template_hash",
    "collection_policy_hash",
    "payload_limits_hash",
    "materializer_version",
    "semantic_policy_version",
    "schema_projection_version",
)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, GeneratedTopic):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="python"))
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def canonical_candidate_content_hash(value: Any) -> str:
    """Hash canonical lore content while excluding mutable processing provenance."""

    payload = _mapping(value)
    payload.pop("provenance", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def contract_receipt(value: Any) -> dict[str, Any]:
    provenance = _mapping(_mapping(value).get("provenance"))
    return _mapping(provenance.get("authoritative_contract_receipt"))


def contract_descriptor_from_candidate(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    provenance = _mapping(payload.get("provenance"))
    explicit = _mapping(provenance.get("contract_descriptor"))
    receipt = contract_receipt(payload)
    source = explicit or receipt
    return {
        key: source.get(key)
        for key in CONTRACT_DESCRIPTOR_KEYS
        if source.get(key) not in (None, "")
    }


def require_authoritative_contract_receipt(
    value: Any,
    *,
    expected_topic_id: str = "",
    verify_content_hash: bool = True,
    require_signature: bool = False,
) -> dict[str, Any]:
    payload = _mapping(value)
    receipt = contract_receipt(payload)
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ValueError("world_forge_authoritative_contract_receipt_required")
    if receipt.get("materialized") is not True:
        raise ValueError("world_forge_candidate_not_materialized")
    if expected_topic_id and str(payload.get("topic_id") or "") != expected_topic_id:
        raise ValueError("world_forge_contract_receipt_topic_mismatch")
    if str(receipt.get("topic_id") or "") != str(payload.get("topic_id") or ""):
        raise ValueError("world_forge_contract_receipt_topic_mismatch")
    required = (
        "contract_id",
        "contract_version",
        "canonical_contract_hash",
        "authored_draft_hash",
        "canonical_content_hash",
        "materializer_version",
    )
    missing = [key for key in required if not str(receipt.get(key) or "")]
    if missing:
        raise ValueError(
            "world_forge_contract_receipt_incomplete:" + ",".join(missing)
        )
    if verify_content_hash and str(receipt["canonical_content_hash"]) != (
        canonical_candidate_content_hash(payload)
    ):
        raise ValueError("world_forge_contract_receipt_content_hash_mismatch")
    if require_signature and not verify_record_signature(receipt):
        raise ValueError("world_forge_contract_receipt_signature_invalid")
    return receipt


__all__ = [
    "RECEIPT_SCHEMA_VERSION",
    "CONTRACT_DESCRIPTOR_KEYS",
    "canonical_candidate_content_hash",
    "contract_descriptor_from_candidate",
    "contract_receipt",
    "require_authoritative_contract_receipt",
]
