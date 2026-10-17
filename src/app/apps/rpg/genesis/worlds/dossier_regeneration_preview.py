"""Generate dossier-only editorial previews without mutating stored canon."""
from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Mapping, cast

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_dossier_quality import validate_dossier_quality
from app.apps.rpg.genesis.forge.world_forge_dossiers import (
    compact_summary,
    validate_entity_dossier,
)
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeTopicGenerator,
)

from .entity_authoring import _entity, _topic_node
from .topic_authoring import _assert_writable_topic, _latest_run, _record


def preview_world_entity_dossier_regeneration(
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
    """Return candidate prose while preserving the current topic unchanged."""

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
    preview_node = replace(
        node,
        target_count=1,
        metadata={
            **dict(node.metadata),
            "entity_dossier_regeneration": {
                "entity_id": entity_id,
                "entity_name": str(before.get("name") or ""),
                "editorial_only": True,
                "preview_only": True,
                "preserve_all_other_fields": True,
                "directives": dict(directives or {}),
            },
        },
    )
    generated = selected_generator.generate(
        preview_node,
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
                "preview_only": True,
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
    dossier = candidate.get("dossier")
    schema_issues = validate_entity_dossier(dossier)
    quality_issues = (
        validate_dossier_quality(dossier, topic_id=topic_id)
        if isinstance(dossier, Mapping)
        else ()
    )
    issues = (*schema_issues, *quality_issues)
    if not candidate.get("short_summary") or issues:
        raise ValueError(
            "world_entity_dossier_requires_llm_authored_prose:"
            + ",".join(issues or ("short_summary_required",))
        )
    dossier = dict(cast(Any, dossier))
    dossier["generated_from_legacy"] = False
    return {
        "ok": True,
        "preview_only": True,
        "world_id": world_id,
        "topic_id": topic_id,
        "entity_id": entity_id,
        "expected_draft_revision": expected_draft_revision,
        "expected_content_hash": expected_content_hash,
        "short_summary": short_summary,
        "dossier": dossier,
        "generation": {
            **dict(generated.provenance),
            "provider_route_source": "durable_world_generation_run",
        },
        "canonical_fields_preserved": True,
        "stored": False,
    }
