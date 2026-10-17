from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any


from .contracts import (
    GraphAssertion,
    GraphEntityRef,
    GraphValue,
    MemoryDomain,
    MemorySpaceKey,
    Observation,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_TRUST_MAP = {
    "user_approved": "user_explicit",
    "system_trusted": "system_trusted",
    "unverified_import": "imported_unverified",
    "unverified_agent": "assistant_inference",
    "external_untrusted": "external_untrusted",
}

_KIND_DOMAIN_MAP: dict[str, MemoryDomain] = {
    "semantic_fact": "fact",
    "preference": "preference",
    "instruction": "instruction",
    "relationship_state": "relationship",
    "episode": "episode",
    "routine": "routine",
    "goal": "goal",
    "open_loop": "open_loop",
    "temporal_fact": "temporal",
    "pronunciation": "fact",
}


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


def _legacy_digest(space: MemorySpaceKey, record_id: str, revision: int) -> str:
    """The digest of a record imported from Memory v1 by an earlier release (legacy-seed observations)."""
    material = f"{space.principal_id}\0{space.owner_type}\0{space.owner_id}\0{record_id}\0{revision}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)



_PROMPT_TRUST = {"user_approved", "system_trusted"}


def _record_payload(observation: Observation) -> tuple[dict[str, Any], str] | None:
    """The record an imported or curated observation holds, with its id prefix."""
    if observation.event_type == "imported_legacy_memory":
        value, prefix = observation.payload.get("legacy_record"), "legacy-seed"
    elif observation.event_type == "curated_memory":
        value, prefix = observation.payload.get("memory_record"), "curated"
    else:
        return None
    return (value, prefix) if isinstance(value, dict) else None


def curated_projector(observations: tuple[Observation, ...]) -> tuple[GraphAssertion, ...]:
    """One assertion per record v1 would let reach a prompt; deterministic for replay.

    Imported v1 records and curated records alike: only active, non-secret,
    approved records are derived (the record-level rule of
    ``app.conversation.memory_contracts.record_prompt_block_reason``); expiry becomes
    ``valid_until`` instead of a clock check, so replay stays exact.
    """
    assertions: list[GraphAssertion] = []
    for observation in observations:
        found = _record_payload(observation)
        if found is None:
            continue
        record, prefix = found
        record_id = str(record.get("id", ""))
        content = str(record.get("content", "")).strip()
        kind = str(record.get("kind", "semantic_fact"))
        if not record_id or not content:
            continue
        if (
            str(record.get("status", "active")) != "active"
            or record.get("sensitivity") == "secret"
            or str(record.get("trust_level", "user_approved")) not in _PROMPT_TRUST
        ):
            continue
        revision = int(record.get("revision", 1))
        digest = (
            _legacy_digest(observation.space, record_id, revision)
            if prefix == "legacy-seed"
            else hashlib.sha256(
                f"{observation.space.principal_id}\0{observation.space.owner_type}\0"
                f"{observation.space.owner_id}\0{record_id}".encode("utf-8")
            ).hexdigest()
        )
        domain = _KIND_DOMAIN_MAP.get(kind, "fact")
        # v1 stops serving a record at expires_at; the assertion ends there too.
        expires_at = record.get("expires_at")
        valid_until = (
            max(_parse_timestamp(str(expires_at)), observation.occurred_at)
            if expires_at
            else None
        )
        assertions.append(
            GraphAssertion(
                assertion_id=f"{prefix}:{digest[:48]}",
                space=observation.space,
                visibility_scopes=(observation.visibility_scope,),
                subject=GraphEntityRef(
                    entity_id=observation.space.principal_id,
                    entity_type="profile",
                ),
                predicate=f"legacy_{kind}" if prefix == "legacy-seed" else f"memory_{kind}",
                object=GraphValue(kind="literal", literal=content),
                domain=domain,
                assertion_type="seeded",
                confidence=float(record.get("confidence", 1.0)),
                valid_from=observation.occurred_at,
                valid_until=valid_until,
                evidence_observation_ids=(observation.observation_id,),
                derivation_version=(
                    "memory-v2-legacy-seed@1" if prefix == "legacy-seed" else "memory-v2-curated@1"
                ),
                status="active",
            )
        )
    return tuple(sorted(assertions, key=lambda item: item.assertion_id))


# The importer's original name; imported records are one case of curated records.
legacy_seed_projector = curated_projector



