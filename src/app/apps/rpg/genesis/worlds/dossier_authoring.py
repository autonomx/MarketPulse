"""Editorial-only authoring for rich reusable-world entity dossiers.

These operations deliberately preserve entity identity, mechanics, references, facts,
and relationships. They update only short-form catalogue prose and the versioned
long-form dossier, so unrelated simulation canon does not become stale.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from typing import Any, Mapping

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_dossier_quality import validate_dossier_quality
from app.apps.rpg.genesis.forge.world_forge_dossiers import (
    DOSSIER_SCHEMA_VERSION,
    compact_summary,
    project_entity_dossier,
    validate_entity_dossier,
)
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeTopicGenerator,
)

from .entity_authoring import (
    _entity,
    _known_ids,
    _record_history,
    _topic_node,
    replace_entity_content,
    validate_entity_references,
)
from .generation_jobs import canonical_hash
from .targeted_generation_authorship import (
    attach_targeted_regeneration_authorship,
    trusted_targeted_generator,
)
from .topic_authoring import (
    _assert_writable_topic,
    _authoring,
    _latest_run,
    _record,
)


def _preserve_canonical_related_entity_ids(
    before: Mapping[str, Any],
    replacement: Mapping[str, Any],
    *,
    topic_id: str,
    entity_id: str,
    content: Mapping[str, Any],
    known_ids: set[str],
) -> dict[str, Any]:
    """Keep dossier regeneration editorial-only at the reference boundary."""

    _summary, prior_dossier = project_entity_dossier(
        before,
        card_type=topic_id,
        content=content,
        entity_id=entity_id,
    )
    preserved = [
        str(reference)
        for reference in prior_dossier.get("related_entity_ids") or ()
        if str(reference) in known_ids
    ]
    return {
        **dict(replacement),
        "related_entity_ids": list(dict.fromkeys(preserved)),
    }


def _store_editorial_replacement(
    world_id: str,
    topic_id: str,
    entity_id: str,
    *,
    expected_draft_revision: int,
    expected_content_hash: str,
    short_summary: str,
    dossier: Mapping[str, Any],
    operation: str,
    generated: GeneratedTopic | None = None,
    metadata: Mapping[str, Any] | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    issues = validate_entity_dossier(dossier)
    if issues:
        raise ValueError("world_entity_dossier_invalid:" + ",".join(issues))
    summary = compact_summary(short_summary)
    context = current_tenant()
    with unit_of_work(database) as work:
        world, current = _assert_writable_topic(
            work,
            context,
            world_id=world_id,
            topic_id=topic_id,
            expected_draft_revision=expected_draft_revision,
            expected_content_hash=expected_content_hash,
        )
        prior_payload = _record(current.get("content"))
        before = _entity(prior_payload, entity_id)
        topic_rows = work.world_generation.list_topics(
            context,
            world_id=world_id,
            draft_revision=int(world["draft_revision"]),
        )
        known_ids = _known_ids(topic_rows, world_id).union({entity_id})
        replacement_dossier = _preserve_canonical_related_entity_ids(
            before,
            dossier,
            topic_id=topic_id,
            entity_id=entity_id,
            content=prior_payload,
            known_ids=known_ids,
        )
        after = {
            **before,
            "id": entity_id,
            "short_summary": summary,
            "dossier": replacement_dossier,
        }
        validate_entity_references(
            after,
            known_ids,
        )
        payload = replace_entity_content(
            prior_payload,
            entity_id,
            after,
        )
        changed_lore_paths: tuple[str, ...] = ()
        if generated is not None:
            payload, changed_lore_paths = attach_targeted_regeneration_authorship(
                prior_payload,
                payload,
                generated,
                topic_id=topic_id,
                operation=operation,
            )
            source = "ai"
            editorial_state = "llm_regenerated"
            provenance = _record(payload.get("provenance"))
        else:
            source = "manual"
            editorial_state = "edited"
            provenance = _record(current.get("provenance"))
        existing_authoring = _authoring(current)
        repair_usage = [
            _record(item)
            for item in existing_authoring.get("dossier_regeneration_usage", [])
            if isinstance(item, Mapping)
        ]
        if generated is not None:
            repair_usage.append(
                {
                    key: value
                    for key, value in dict(generated.provenance).items()
                    if key in {"usage", "token_estimate", "latency_ms"}
                }
            )
        provenance["authoring"] = {
            **existing_authoring,
            "dossier_regeneration_usage": repair_usage,
            "editorial_state": editorial_state,
            "entity_dossier_schema": DOSSIER_SCHEMA_VERSION,
            "last_entity_edit": {
                "entity_id": entity_id,
                "operation": operation,
                "editorial_only": True,
                "edited_at": datetime.now(timezone.utc).isoformat(),
                "llm_changed_paths": list(changed_lore_paths),
            },
        }
        stored = work.world_scenarios.put_topic(
            context,
            world_id=world_id,
            topic_id=topic_id,
            draft_revision=int(world["draft_revision"]),
            source=source,
            status="ready",
            content=payload,
            directives=_record(current.get("directives")),
            dependency_hashes=_record(current.get("dependency_hashes")),
            input_hash=canonical_hash(
                {
                    "source": operation,
                    "entity_id": entity_id,
                    "short_summary": summary,
                    "dossier": dict(dossier),
                }
            ),
            content_hash=canonical_hash(payload),
            provenance=provenance,
        )
        stored_hash = str(stored.get("content_hash") or canonical_hash(payload))
        _record_history(
            work,
            context,
            world_id=world_id,
            topic_id=topic_id,
            entity_id=entity_id,
            operation=operation,
            before=before,
            after=after,
            topic_content_hash=stored_hash,
            metadata={
                "editorial_only": True,
                "llm_changed_paths": list(changed_lore_paths),
                **dict(metadata or {}),
            },
        )
        work.commit()
    return {
        "ok": True,
        "topic": stored,
        "entity": after,
        "stale_topic_ids": [],
        "stale_entity_ids": [],
        "canonical_fields_preserved": True,
        "editorial_only": True,
    }


def update_world_entity_dossier(
    world_id: str,
    topic_id: str,
    entity_id: str,
    *,
    expected_draft_revision: int,
    expected_content_hash: str,
    short_summary: str,
    dossier: Mapping[str, Any],
    database: Any | None = None,
) -> dict[str, Any]:
    return _store_editorial_replacement(
        world_id,
        topic_id,
        entity_id,
        expected_draft_revision=expected_draft_revision,
        expected_content_hash=expected_content_hash,
        short_summary=short_summary,
        dossier=dossier,
        operation="manual_dossier_edit",
        database=database,
    )


def regenerate_world_entity_dossier(
    world_id: str,
    topic_id: str,
    entity_id: str,
    *,
    expected_draft_revision: int,
    expected_content_hash: str,
    directives: Mapping[str, Any] | None = None,
    generator: WorldForgeTopicGenerator | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    context = current_tenant()
    with unit_of_work(database) as work:
        world, current = _assert_writable_topic(
            work,
            context,
            world_id=world_id,
            topic_id=topic_id,
            expected_draft_revision=expected_draft_revision,
            expected_content_hash=expected_content_hash,
        )
        before = _entity(_record(current.get("content")), entity_id)
        run = _latest_run(work, context, world_id)
        node = _topic_node(world, run, topic_id)
        rows = work.world_generation.list_topics(
            context,
            world_id=world_id,
            draft_revision=int(world["draft_revision"]),
        )
        row_map = {str(row.get("topic_id") or ""): row for row in rows}
        dependencies = {
            dependency_id: GeneratedTopic.from_dict(
                _record(row_map[dependency_id].get("content"))
            )
            for dependency_id in node.dependencies
            if dependency_id in row_map
        }
        work.rollback()

    selected_generator = generator
    if selected_generator is None:
        from .generation_routing import build_world_forge_generator_for_run

        selected_generator = build_world_forge_generator_for_run(run)
    selected_generator = trusted_targeted_generator(
        selected_generator,
        world_id=world_id,
        topic_id=topic_id,
        entity_id=entity_id,
        operation="dossier_regeneration",
    )
    node = replace(
        node,
        target_count=1,
        metadata={
            **dict(node.metadata),
            "entity_dossier_regeneration": {
                "entity_id": entity_id,
                "entity_name": str(before.get("name") or ""),
                "editorial_only": True,
                "preserve_all_other_fields": True,
                "directives": dict(directives or {}),
            },
        },
    )
    generated = selected_generator.generate(
        node,
        seed=int(world.get("seed") or 0),
        campaign_context={
            "campaign_template": _record(world.get("metadata")).get(
                "campaign_template"
            )
            or "classic_fantasy",
            "genre": str(world.get("genre") or "classic_fantasy"),
            "tone": str(world.get("tone") or "heroic adventure"),
            "custom_directives": [
                json.dumps(dict(directives or {}), sort_keys=True)
            ],
            "entity_dossier_regeneration": {
                "entity_id": entity_id,
                "current_canonical_entity": before,
                "allowed_output_fields": ["short_summary", "dossier"],
            },
        },
        dependency_topics=dependencies,
    )
    if not generated.entities:
        raise ValueError(
            f"world_entity_dossier_regeneration_empty:{topic_id}:{entity_id}"
        )
    candidate = next(
        (
            dict(row)
            for row in generated.entities
            if str(row.get("id") or row.get("entity_id") or "") == entity_id
        ),
        dict(generated.entities[0]),
    )
    short_summary = compact_summary(candidate.get("short_summary"))
    raw_dossier = candidate.get("dossier")
    raw_schema_issues = validate_entity_dossier(raw_dossier)
    if not candidate.get("short_summary") or raw_schema_issues:
        raise ValueError(
            "world_entity_dossier_requires_llm_authored_prose:"
            + ",".join((*raw_schema_issues,) or ("short_summary_required",))
        )
    _summary, dossier = project_entity_dossier(
        {**before, "short_summary": short_summary, "dossier": raw_dossier},
        card_type=topic_id,
        entity_id=entity_id,
    )
    schema_issues = validate_entity_dossier(dossier)
    quality_issues = (
        validate_dossier_quality(dossier, topic_id=topic_id)
        if isinstance(dossier, Mapping)
        else ()
    )
    if schema_issues or quality_issues:
        raise ValueError(
            "world_entity_dossier_requires_llm_authored_prose:"
            + ",".join((*schema_issues, *quality_issues))
        )
    dossier = dict(dossier)
    dossier["generated_from_legacy"] = False
    return _store_editorial_replacement(
        world_id,
        topic_id,
        entity_id,
        expected_draft_revision=expected_draft_revision,
        expected_content_hash=expected_content_hash,
        short_summary=short_summary,
        dossier=dossier,
        operation="regenerate_dossier",
        generated=generated,
        metadata={
            "directives": dict(directives or {}),
            "generation": dict(generated.provenance),
            "provider_route_source": "durable_world_generation_run",
        },
        database=database,
    )
