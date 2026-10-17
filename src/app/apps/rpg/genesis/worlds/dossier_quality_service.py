"""Quality reporting and incremental enrichment for stored world dossiers."""
from __future__ import annotations

from collections import defaultdict
import logging
from typing import Any, Mapping

from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.genesis.forge.world_forge_dossier_quality import (
    dossier_word_count,
    validate_dossier_quality,
)
from app.apps.rpg.genesis.forge.world_forge_dossiers import (
    placeholder_section_title_count,
    project_entity_dossier,
    validate_entity_dossier,
)
from app.apps.rpg.genesis.forge.world_forge_generation import WorldForgeTopicGenerator

from .dossier_authoring import regenerate_world_entity_dossier

_LOGGER = logging.getLogger(__name__)


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def world_dossier_quality(
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
        topics = work.world_library.list_topics(context, world_id)
        work.rollback()

    known_ids = {
        str(entity.get("id") or entity.get("entity_id") or "")
        for topic in topics
        for entity in _rows(dict(topic.get("content") or {}).get("entities"))
        if str(entity.get("id") or entity.get("entity_id") or "")
    }
    total = 0
    rich = 0
    projected = 0
    invalid = 0
    heading_repairs = 0
    words = 0
    unresolved: set[str] = set()
    candidates: list[dict[str, Any]] = []
    by_topic: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "entities": 0,
            "rich": 0,
            "projected": 0,
            "invalid": 0,
            "words": 0,
        }
    )

    for topic in topics:
        topic_id = str(topic.get("topic_id") or "")
        content = dict(topic.get("content") or {})
        for entity in _rows(content.get("entities")):
            entity_id = str(entity.get("id") or entity.get("entity_id") or "")
            if not entity_id:
                continue
            total += 1
            topic_metrics = by_topic[topic_id]
            topic_metrics["entities"] += 1
            _summary, dossier = project_entity_dossier(
                entity,
                card_type=topic_id,
                content=content,
                entity_id=entity_id,
            )
            schema_issues = validate_entity_dossier(dossier)
            quality_issues = validate_dossier_quality(dossier, topic_id=topic_id)
            placeholder_titles = placeholder_section_title_count(entity.get("dossier"))
            heading_repairs += placeholder_titles
            entity_words = dossier_word_count(dossier)
            words += entity_words
            topic_metrics["words"] += entity_words
            generated_from_legacy = bool(dossier.get("generated_from_legacy"))
            if generated_from_legacy:
                projected += 1
                topic_metrics["projected"] += 1
            if schema_issues or quality_issues or placeholder_titles:
                invalid += 1
                topic_metrics["invalid"] += 1
                candidates.append(
                    {
                        "topic_id": topic_id,
                        "entity_id": entity_id,
                        "title": str(entity.get("name") or entity.get("title") or entity_id),
                        "word_count": entity_words,
                        "generated_from_legacy": generated_from_legacy,
                        "issues": [
                            *schema_issues,
                            *quality_issues,
                            *(
                                [f"dossier_placeholder_section_titles:{placeholder_titles}"]
                                if placeholder_titles
                                else []
                            ),
                        ],
                    }
                )
            else:
                rich += 1
                topic_metrics["rich"] += 1
            for related_id in dossier.get("related_entity_ids") or ():
                related = str(related_id)
                if related and related not in known_ids:
                    unresolved.add(related)

    by_topic_rows = []
    for topic_id, metrics in sorted(by_topic.items()):
        entity_count = int(metrics["entities"])
        by_topic_rows.append(
            {
                "topic_id": topic_id,
                **metrics,
                "coverage_percent": 100
                if entity_count == 0
                else round(int(metrics["rich"]) / entity_count * 100),
                "average_words": 0
                if entity_count == 0
                else round(int(metrics["words"]) / entity_count),
            }
        )

    return {
        "ok": True,
        "world_id": world_id,
        "draft_revision": int(world.get("draft_revision") or 0),
        "schema_version": "rpg_world_entity_dossier_v1",
        "metrics": {
            "entities": total,
            "rich_dossiers": rich,
            "projected_legacy_dossiers": projected,
            "invalid_or_thin_dossiers": invalid,
            "heading_repairs": heading_repairs,
            "coverage_percent": 100 if total == 0 else round(rich / total * 100),
            "average_words": 0 if total == 0 else round(words / total),
            "unresolved_related_entity_ids": len(unresolved),
        },
        "by_topic": by_topic_rows,
        "unresolved_related_entity_ids": sorted(unresolved),
        "enrichment_candidates": candidates,
    }


def enrich_world_dossiers(
    world_id: str,
    *,
    limit: int = 10,
    all_candidates: bool = False,
    dry_run: bool = True,
    directives: Mapping[str, Any] | None = None,
    requested_candidates: list[Mapping[str, Any]] | None = None,
    generator: WorldForgeTopicGenerator | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    """Plan or execute bounded dossier-only enrichment for existing world entries."""

    report = world_dossier_quality(world_id, database=database)
    available_candidates = list(report["enrichment_candidates"])
    candidates = (
        available_candidates
        if all_candidates
        else available_candidates[: max(1, min(int(limit), 25))]
    )
    if requested_candidates is not None:
        requested_ids = {
            (str(candidate.get("topic_id") or ""), str(candidate.get("entity_id") or ""))
            for candidate in requested_candidates
        }
        candidates = [
            candidate for candidate in available_candidates
            if (str(candidate["topic_id"]), str(candidate["entity_id"])) in requested_ids
        ]
    if dry_run:
        return {
            "ok": True,
            "world_id": world_id,
            "dry_run": True,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "metrics": report["metrics"],
        }

    completed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    context = current_tenant()
    for candidate in candidates:
        topic_id = str(candidate["topic_id"])
        entity_id = str(candidate["entity_id"])
        try:
            with unit_of_work(database) as work:
                world = work.world_scenarios.get_world(context, world_id)
                topic = work.world_generation.get_topic(
                    context,
                    world_id=world_id,
                    topic_id=topic_id,
                )
                work.rollback()
            if world is None or topic is None:
                raise KeyError(f"world_dossier_candidate_missing:{topic_id}:{entity_id}")
            result = regenerate_world_entity_dossier(
                world_id,
                topic_id,
                entity_id,
                expected_draft_revision=int(world["draft_revision"]),
                expected_content_hash=str(topic["content_hash"]),
                directives={
                    "enrichment_workflow": True,
                    "quality_issues": list(candidate.get("issues") or ()),
                    **dict(directives or {}),
                },
                generator=generator,
                database=database,
            )
            completed.append(
                {
                    "topic_id": topic_id,
                    "entity_id": entity_id,
                    "content_hash": str(result["topic"]["content_hash"]),
                }
            )
        except Exception as exc:
            _LOGGER.warning(
                "World dossier enrichment failed for %s/%s: %s",
                topic_id,
                entity_id,
                exc,
                exc_info=True,
            )
            failed.append(
                {
                    "topic_id": topic_id,
                    "entity_id": entity_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return {
        "ok": not failed,
        "world_id": world_id,
        "dry_run": False,
        "attempted": len(candidates),
        "completed": completed,
        "failed": failed,
        "quality": world_dossier_quality(world_id, database=database),
    }
