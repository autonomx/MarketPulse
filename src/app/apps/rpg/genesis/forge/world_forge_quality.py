"""Strict dossier and provenance quality gates for generated World Forge canon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .canon_audit import CanonAuditIssue, CanonAuditReport
from .world_forge_generation import GeneratedTopic


@dataclass(frozen=True)
class WorldForgeQualitySummary:
    npc_dossiers: int = 0
    location_dossiers: int = 0
    faction_dossiers: int = 0
    documents: int = 0
    facts: int = 0
    story_threads: int = 0


def _text(row: Mapping[str, Any], key: str) -> str:
    return " ".join(str(row.get(key) or "").split())


def _strings(row: Mapping[str, Any], key: str) -> tuple[str, ...] | None:
    value = row.get(key)
    if not isinstance(value, list | tuple | set):
        return None
    return tuple(str(item).strip() for item in value if str(item).strip())


def _issue(code: str, message: str, item_id: str) -> CanonAuditIssue:
    return CanonAuditIssue(code=code, message=message, item_id=item_id)


def _missing_text_fields(
    row: Mapping[str, Any],
    requirements: Mapping[str, int],
) -> tuple[str, ...]:
    return tuple(
        field
        for field, minimum in requirements.items()
        if len(_text(row, field)) < minimum
    )


def _topic_rows(
    topics: Iterable[GeneratedTopic],
    field: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for topic in topics:
        rows.extend(
            dict(row)
            for row in getattr(topic, field)
            if isinstance(row, Mapping)
        )
    return rows


def _entity_quality_issues(
    entities: list[dict[str, Any]],
    fact_ids: set[str],
) -> tuple[list[CanonAuditIssue], WorldForgeQualitySummary]:
    issues: list[CanonAuditIssue] = []
    entity_ids = {
        str(row.get("id") or "").strip()
        for row in entities
        if str(row.get("id") or "").strip()
    }
    npc_count = 0
    location_count = 0
    faction_count = 0
    for row in entities:
        entity_id = str(row.get("id") or "").strip() or "entity:<missing>"
        kind = str(row.get("kind") or "").strip().casefold()
        if kind == "npc":
            npc_count += 1
            missing = list(
                _missing_text_fields(
                    row,
                    {
                        "name": 2,
                        "appearance": 20,
                        "personality": 20,
                        "backstory": 30,
                        "speech_style": 10,
                    },
                )
            )
            for field in ("goals", "motives"):
                values = _strings(row, field)
                if not values:
                    missing.append(field)
            for field in ("faction_ids", "secrets", "known_facts"):
                if _strings(row, field) is None:
                    missing.append(field)
            if str(row.get("dossier_status") or "") != "complete":
                missing.append("dossier_status")
            if missing:
                issues.append(
                    _issue(
                        "incomplete_npc_dossier",
                        "NPC dossier is missing required structured fields: "
                        + ",".join(sorted(set(missing))),
                        entity_id,
                    )
                )
            location_id = str(row.get("location_id") or "").strip()
            mobility = str(row.get("mobility_status") or "").strip().casefold()
            if not location_id and mobility not in {
                "itinerant",
                "nomadic",
                "unplaced",
            }:
                issues.append(
                    _issue(
                        "npc_location_undefined",
                        "NPC dossier must name a canonical location or explicit mobility status.",
                        entity_id,
                    )
                )
            if location_id and location_id not in entity_ids:
                issues.append(
                    _issue(
                        "dangling_npc_location",
                        f"NPC location does not resolve: {location_id}",
                        entity_id,
                    )
                )
            for faction_id in _strings(row, "faction_ids") or ():
                if faction_id not in entity_ids:
                    issues.append(
                        _issue(
                            "dangling_npc_faction",
                            f"NPC faction does not resolve: {faction_id}",
                            entity_id,
                        )
                    )
            for fact_id in _strings(row, "known_facts") or ():
                if fact_id not in fact_ids:
                    issues.append(
                        _issue(
                            "dangling_npc_known_fact",
                            f"NPC known fact does not resolve: {fact_id}",
                            entity_id,
                        )
                    )
        elif kind == "location":
            location_count += 1
            missing = list(
                _missing_text_fields(
                    row,
                    {
                        "name": 2,
                        "region_id": 3,
                        "sensory_profile": 20,
                    },
                )
            )
            if str(row.get("dossier_status") or "") != "complete":
                missing.append("dossier_status")
            if missing:
                issues.append(
                    _issue(
                        "incomplete_location_dossier",
                        "Location dossier is missing required structured fields: "
                        + ",".join(sorted(set(missing))),
                        entity_id,
                    )
                )
            region_id = str(row.get("region_id") or "").strip()
            if region_id and region_id not in entity_ids:
                issues.append(
                    _issue(
                        "dangling_location_region",
                        f"Location region does not resolve: {region_id}",
                        entity_id,
                    )
                )
        elif kind == "faction":
            faction_count += 1
            missing = list(_missing_text_fields(row, {"name": 2}))
            for field in ("values", "goals"):
                values = _strings(row, field)
                if not values:
                    missing.append(field)
            if missing:
                issues.append(
                    _issue(
                        "incomplete_faction_dossier",
                        "Faction dossier is missing required structured fields: "
                        + ",".join(sorted(set(missing))),
                        entity_id,
                    )
                )
    return issues, WorldForgeQualitySummary(
        npc_dossiers=npc_count,
        location_dossiers=location_count,
        faction_dossiers=faction_count,
    )


def _document_issues(
    documents: list[dict[str, Any]],
    entity_ids: set[str],
) -> list[CanonAuditIssue]:
    issues: list[CanonAuditIssue] = []
    for index, row in enumerate(documents, start=1):
        document_id = str(row.get("document_id") or "").strip() or f"document:{index}"
        missing = _missing_text_fields(
            row,
            {
                "document_id": 3,
                "title": 2,
                "full_text": 30,
                "summary_500": 20,
                "summary_120": 20,
                "visibility": 3,
            },
        )
        if missing:
            issues.append(
                _issue(
                    "incomplete_lore_document",
                    "Lore document is missing required fields: "
                    + ",".join(missing),
                    document_id,
                )
            )
        refs = _strings(row, "entities")
        if refs is None:
            issues.append(
                _issue(
                    "invalid_document_entity_refs",
                    "Lore document entities must be an array.",
                    document_id,
                )
            )
        for ref in refs or ():
            if ref not in entity_ids:
                issues.append(
                    _issue(
                        "dangling_document_entity",
                        f"Lore document entity does not resolve: {ref}",
                        document_id,
                    )
                )
    return issues


def _fact_issues(
    facts: list[dict[str, Any]],
    entity_ids: set[str],
) -> list[CanonAuditIssue]:
    issues: list[CanonAuditIssue] = []
    for index, row in enumerate(facts, start=1):
        fact_id = str(row.get("id") or "").strip() or f"fact:{index}"
        missing = _missing_text_fields(
            row,
            {"id": 3, "content": 10, "visibility": 3},
        )
        if missing:
            issues.append(
                _issue(
                    "incomplete_generated_fact",
                    "Generated fact is missing required fields: "
                    + ",".join(missing),
                    fact_id,
                )
            )
        if str(row.get("authority") or "") != "generated_proposal":
            issues.append(
                _issue(
                    "invalid_generated_fact_authority",
                    "Generated facts must remain generated_proposal until compilation.",
                    fact_id,
                )
            )
        if str(row.get("approved_authority") or "") != "objective_canon":
            issues.append(
                _issue(
                    "missing_fact_approval_target",
                    "Generated facts must declare objective_canon as the approval target.",
                    fact_id,
                )
            )
        refs = _strings(row, "entity_refs")
        if not refs:
            issues.append(
                _issue(
                    "missing_fact_entity_refs",
                    "Generated facts must cite at least one canonical entity.",
                    fact_id,
                )
            )
        for ref in refs or ():
            if ref not in entity_ids:
                issues.append(
                    _issue(
                        "dangling_fact_entity_ref",
                        f"Generated fact entity does not resolve: {ref}",
                        fact_id,
                    )
                )
    return issues


def _story_thread_issues(
    threads: list[dict[str, Any]],
    entity_ids: set[str],
) -> list[CanonAuditIssue]:
    issues: list[CanonAuditIssue] = []
    for index, row in enumerate(threads, start=1):
        thread_id = str(row.get("id") or "").strip() or f"thread:{index}"
        missing = _missing_text_fields(
            row,
            {"id": 3, "title": 3, "summary": 20, "status": 3},
        )
        for field in ("actor_ids", "location_ids", "faction_ids"):
            if _strings(row, field) is None:
                missing = (*missing, field)
        if missing:
            issues.append(
                _issue(
                    "incomplete_story_thread",
                    "Story thread is missing required fields: "
                    + ",".join(sorted(set(missing))),
                    thread_id,
                )
            )
        for field, code in (
            ("actor_ids", "dangling_story_actor"),
            ("location_ids", "dangling_story_location"),
            ("faction_ids", "dangling_story_faction"),
        ):
            for ref in _strings(row, field) or ():
                if ref not in entity_ids:
                    issues.append(
                        _issue(
                            code,
                            f"Story thread reference does not resolve: {ref}",
                            thread_id,
                        )
                    )
    return issues


def apply_world_forge_quality_audit(
    topics: Iterable[GeneratedTopic],
    report: CanonAuditReport,
) -> CanonAuditReport:
    """Extend structural canon checks with rich dossier and provenance requirements."""

    topic_list = tuple(topics)
    entities = _topic_rows(topic_list, "entities")
    documents = _topic_rows(topic_list, "documents")
    facts = _topic_rows(topic_list, "facts")
    threads = _topic_rows(topic_list, "story_threads")
    entity_ids = {
        str(row.get("id") or "").strip()
        for row in entities
        if str(row.get("id") or "").strip()
    }
    fact_ids = {
        str(row.get("id") or "").strip()
        for row in facts
        if str(row.get("id") or "").strip()
    }
    issues: list[CanonAuditIssue] = []
    for topic in topic_list:
        generator = str(topic.provenance.get("generator") or "").strip()
        if not generator:
            issues.append(
                _issue(
                    "missing_topic_provenance",
                    "Every generated topic must identify its generator.",
                    topic.topic_id,
                )
            )
        if generator == "structured_world_forge_provider_v1":
            provider = str(topic.provenance.get("provider") or "").strip()
            attempt_count = topic.provenance.get("attempt_count")
            if not provider or not isinstance(attempt_count, int) or attempt_count < 1:
                issues.append(
                    _issue(
                        "incomplete_provider_provenance",
                        "Live World Forge topics require provider and attempt metadata.",
                        topic.topic_id,
                    )
                )
    entity_issues, summary = _entity_quality_issues(entities, fact_ids)
    issues.extend(entity_issues)
    issues.extend(_document_issues(documents, entity_ids))
    issues.extend(_fact_issues(facts, entity_ids))
    issues.extend(_story_thread_issues(threads, entity_ids))
    quality_error_count = sum(1 for issue in issues if issue.severity == "error")
    return CanonAuditReport(
        passed=report.passed and quality_error_count == 0,
        issues=tuple([*report.issues, *issues]),
        patches=report.patches,
        checks={
            **dict(report.checks),
            "quality_errors": quality_error_count,
            "npc_dossiers": summary.npc_dossiers,
            "location_dossiers": summary.location_dossiers,
            "faction_dossiers": summary.faction_dossiers,
            "quality_documents": len(documents),
            "quality_facts": len(facts),
            "quality_story_threads": len(threads),
        },
    )
