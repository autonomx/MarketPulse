"""Audit current World Forge drafts and queue blocked lore for regeneration."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work

from .generation_authorship_policy_signing import (
    signed_authorship_policy,
    validate_policy_bound_authorship,
)
from .generation_authorship_runtime import generation_artifact
from .generation_authorship_signing import strict_lore_string_leaves
from collections.abc import Sequence

_BLOCKED_DETERMINISTIC_CODES = {
    "deterministic_fallback",
    "deterministic_profile_fixture_v1",
    "deterministic_world_forge_v1",
    "quality_enriched",
    "presentation_derived_from_structured_facts",
    "used_llm_false",
}
_ARTIFACT_FAILURE_CODES = {
    "server_generation_artifact_missing",
    "server_authorship_signature_invalid",
    "generation_artifact_signature_invalid",
    "generation_artifact_hash_mismatch",
    "generation_artifact_payload_hash_mismatch",
    "generation_artifact_provider_or_model_missing",
    "genuine_provider_response_hash_required",
    "origin_artifact_content_mismatch",
}


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _classification(
    *,
    publishable: bool,
    lore_count: int,
    origin_count: int,
    codes: set[str],
) -> str:
    if publishable:
        return "verified_authored"
    if lore_count == 0 or "generated_from_legacy" in codes:
        return "missing_lore"
    if codes & _BLOCKED_DETERMINISTIC_CODES:
        return "deterministic_lore"
    if origin_count:
        return "mixed_origin"
    return "legacy_unknown"


def _artifact_status(
    artifact: Mapping[str, Any],
    codes: set[str],
) -> str:
    if not artifact:
        return "missing"
    if codes & _ARTIFACT_FAILURE_CODES:
        return "invalid"
    return "verified"


def _entity_rows(
    content: Mapping[str, Any],
    blocked_paths: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entities = [
        dict(row)
        for row in content.get("entities") or ()
        if isinstance(row, Mapping)
    ]
    policy = signed_authorship_policy(content)
    leaves = strict_lore_string_leaves(content, policy=policy)
    rows: list[dict[str, Any]] = []
    for index, entity in enumerate(entities):
        prefix = f"/entities/{index}/"
        entity_leaves = [row for row in leaves if str(row["path"]).startswith(prefix)]
        entity_blockers = [
            dict(row)
            for row in blocked_paths
            if str(row.get("path") or "").startswith(prefix)
        ]
        codes = {str(row.get("code") or "") for row in entity_blockers}
        dossier = _record(entity.get("dossier"))
        generation_required = (
            bool(dossier.get("generation_required"))
            or bool(dossier.get("lore_required"))
            or str(dossier.get("dossier_status") or "") == "generation_required"
            or not bool(dossier.get("sections"))
        )
        classification = _classification(
            publishable=not entity_blockers and bool(entity_leaves) and not generation_required,
            lore_count=len(entity_leaves) if not generation_required else 0,
            origin_count=max(0, len(entity_leaves) - len(entity_blockers)),
            codes=codes | ({"generated_from_legacy"} if generation_required else set()),
        )
        rows.append(
            {
                "entity_id": str(entity.get("id") or entity.get("entity_id") or ""),
                "name": str(entity.get("name") or entity.get("title") or ""),
                "classification": classification,
                "lore_string_count": len(entity_leaves),
                "blocked_paths": entity_blockers,
                "generation_required": generation_required,
            }
        )
    return rows


def _topic_audit(topic: Mapping[str, Any]) -> dict[str, Any]:
    topic_id = str(topic.get("topic_id") or "")
    content = topic.get("content")
    if not isinstance(content, Mapping):
        return {
            "topic_id": topic_id,
            "classification": "missing_lore",
            "publishable": False,
            "artifact_status": "missing",
            "lore_string_count": 0,
            "origin_count": 0,
            "blocked_paths": [{"path": "/", "code": "topic_content_missing"}],
            "entities": [],
        }
    payload = dict(content)
    artifact = generation_artifact(payload)
    report = validate_policy_bound_authorship(payload)
    blocked_paths = [dict(row) for row in report.get("blocked_paths") or ()]
    entity_rows = _entity_rows(payload, blocked_paths)
    generation_required = any(
        bool(row.get("generation_required")) for row in entity_rows
    )
    codes = {str(row.get("code") or "") for row in blocked_paths}
    if generation_required:
        codes.add("generated_from_legacy")
    classification = _classification(
        publishable=bool(report.get("publishable")) and not generation_required,
        lore_count=(
            0
            if generation_required
            else int(report.get("lore_string_count") or 0)
        ),
        origin_count=int(report.get("origin_count") or 0),
        codes=codes,
    )
    return {
        "topic_id": topic_id,
        "source": str(topic.get("source") or ""),
        "status": str(topic.get("status") or ""),
        "classification": classification,
        "publishable": bool(report.get("publishable")) and not generation_required,
        "artifact_status": _artifact_status(artifact, codes),
        "generation_artifact_id": str(
            artifact.get("generation_artifact_id") or ""
        ),
        "lore_string_count": int(report.get("lore_string_count") or 0),
        "origin_count": int(report.get("origin_count") or 0),
        "blocked_paths": blocked_paths,
        "entities": entity_rows,
    }


def audit_world_authorship(
    world_id: str,
    *,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id)
        if world is None:
            work.rollback()
            raise KeyError(f"world_not_found:{world_id}")
        topics = work.world_generation.list_topics(
            context,
            world_id=world_id,
            draft_revision=int(world["draft_revision"]),
        )
        revisions = work.world_library.list_world_revisions(context, world_id)
        releases = work.world_library.list_world_releases(context, world_id)
        work.rollback()

    topic_rows = [_topic_audit(row) for row in topics]
    classifications = Counter(str(row["classification"]) for row in topic_rows)
    blocked_topic_ids = [
        str(row["topic_id"])
        for row in topic_rows
        if not bool(row["publishable"])
    ]
    return {
        "schema_version": "rpg_world_authorship_audit_v2",
        "ok": True,
        "world_id": world_id,
        "draft_revision": int(world["draft_revision"]),
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "publishable": not blocked_topic_ids and bool(topic_rows),
        "classification_counts": dict(sorted(classifications.items())),
        "blocked_topic_ids": blocked_topic_ids,
        "topics": topic_rows,
        "immutable_history": {
            "world_revision_count": len(revisions),
            "world_release_count": len(releases),
            "mutated": False,
        },
    }


def remediate_world_authorship(
    world_id: str,
    *,
    queue_regeneration: bool = True,
    database: Any | None = None,
) -> dict[str, Any]:
    """Mark blocked current drafts stale and optionally start forced regeneration."""

    report = audit_world_authorship(world_id, database=database)
    blocked = tuple(str(value) for value in report["blocked_topic_ids"])
    if not blocked:
        return {**report, "remediation": {"status": "not_required", "topic_ids": []}}

    context = current_tenant()
    audited_at = datetime.now(timezone.utc).isoformat()
    with unit_of_work(database) as work:
        world = work.world_scenarios.get_world(context, world_id)
        if world is None:
            work.rollback()
            raise KeyError(f"world_not_found:{world_id}")
        for topic_id in blocked:
            row = work.world_generation.get_topic(
                context,
                world_id=world_id,
                topic_id=topic_id,
            )
            if row is None:
                continue
            provenance = _record(row.get("provenance"))
            provenance["authorship_audit"] = {
                "schema_version": "rpg_world_authorship_audit_marker_v2",
                "audited_at": audited_at,
                "classification": next(
                    (
                        item["classification"]
                        for item in report["topics"]
                        if item["topic_id"] == topic_id
                    ),
                    "legacy_unknown",
                ),
                "remediation_status": "queued" if queue_regeneration else "marked_stale",
            }
            work.connection.execute(
                "UPDATE omnix_rpg_world_topics SET status = 'stale', "
                "provenance_jsonb = %s::jsonb, updated_at = CURRENT_TIMESTAMP "
                "WHERE workspace_id = %s AND world_id = %s AND topic_id = %s",
                (
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                    context.workspace_id,
                    world_id,
                    topic_id,
                ),
            )
        work.commit()

    generation: dict[str, Any] | None = None
    generation_error = ""
    if queue_regeneration:
        try:
            from .library_service import start_world_library_generation

            generation = start_world_library_generation(
                world_id,
                scope={"mode": "selected", "topic_ids": list(blocked)},
                strategy="force",
                replace_locked=True,
                database=database,
            )
        except Exception as exc:
            generation_error = str(exc)

    return {
        **report,
        "publishable": False,
        "remediation": {
            "status": (
                "queued"
                if generation is not None
                else "marked_stale"
                if not queue_regeneration
                else "queue_failed"
            ),
            "topic_ids": list(blocked),
            "generation": generation,
            "generation_error": generation_error,
        },
    }


__all__ = ["audit_world_authorship", "remediate_world_authorship"]
