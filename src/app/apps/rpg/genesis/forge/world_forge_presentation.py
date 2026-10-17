"""Render profile-driven presentation without inventing live-provider lore."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence, cast

from .world_forge_contract import CampaignTopicNode
from .world_forge_contradictions import (
    PresentationContradictionReport,
    audit_presentation_contradictions,
)
from .world_forge_dossiers import validate_entity_dossier
from .world_forge_fact_pipeline import (
    StructuredFactIssue,
    StructuredFactValidationError,
)
from .world_forge_generation import GeneratedTopic
from .world_forge_integrity import (
    WorldForgeIntegrityError,
    WorldForgeIntegrityIssue,
)

_DOSSIER_SCHEMA = "rpg_world_entity_dossier_v1"
_PRESENTATION_SOURCE_FIELDS = {
    "description",
    "summary",
    "short_summary",
    "dossier",
    "quote",
    "subtitle",
}


def _definitions(node: CampaignTopicNode) -> tuple[dict[str, Any], ...]:
    value = node.metadata.get("field_definitions")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _entity_id(entity: Mapping[str, Any]) -> str:
    return str(entity.get("id") or entity.get("entity_id") or "").strip()


def _provider_generated(topic: GeneratedTopic) -> bool:
    return str(dict(topic.provenance).get("generator") or "").startswith(
        "structured_world_forge_provider_"
    )


def _display(value: Any) -> str:
    if value in (None, "", [], (), {}):
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Mapping):
        return "; ".join(
            f"{str(key).replace('_', ' ')}: {_display(item)}"
            for key, item in value.items()
            if _display(item)
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return ", ".join(
            rendered for item in value if (rendered := _display(item))
        )
    return " ".join(str(value).split())


def _sentence(label: str, value: Any) -> str:
    rendered = _display(value).rstrip(" .")
    if not rendered:
        return ""
    return f"{label.replace('_', ' ').title()}: {rendered}."


def _section_for_field(field_id: str, value_type: str) -> tuple[str, str]:
    if value_type in {"entity_ref", "entity_ref_list"}:
        return "connections", "Connections"
    if any(
        marker in field_id
        for marker in ("observable", "evidence", "sign", "rumor", "rumour")
    ):
        return "evidence", "Observable Evidence"
    if any(
        marker in field_id
        for marker in (
            "next_action",
            "next_tick",
            "reaction",
            "failure",
            "aftermath",
            "escalation",
        )
    ):
        return "plans", "Plans and Consequences"
    if any(
        marker in field_id
        for marker in (
            "goal",
            "objective",
            "pressure",
            "dependency",
            "resource",
            "cost",
            "scarcity",
        )
    ):
        return "pressures", "Goals, Pressures, and Dependencies"
    if any(marker in field_id for marker in ("history", "origin", "former", "cause")):
        return "context", "History and Context"
    return "details", "Canonical Details"


def _preferred_summary_field(field_ids: set[str]) -> str | None:
    preferences = (
        "current_objective",
        "goal",
        "purpose",
        "rule",
        "current_pressure",
        "next_action",
        "function_in_setting",
        "former_purpose",
    )
    return next((field_id for field_id in preferences if field_id in field_ids), None)


def _structured_facts(topic: GeneratedTopic) -> tuple[dict[str, Any], ...]:
    return tuple(
        dict(row)
        for row in topic.facts
        if str(row.get("source") or "") == "profile_structured_fact_compiler_v1"
        and str(row.get("approved_authority") or "") == "objective_canon"
        and str(row.get("subject") or "")
        and str(row.get("field_id") or "")
    )


def _presentation_fact_proposals(
    topic: GeneratedTopic,
    *,
    preserved_entity_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    preserved = preserved_entity_ids or set()
    proposals: list[dict[str, Any]] = []
    for entity in topic.entities:
        entity_id = _entity_id(entity)
        for field_id in sorted(_PRESENTATION_SOURCE_FIELDS):
            value = entity.get(field_id)
            if value in (None, "", [], (), {}):
                continue
            status = "non_canonical_presentation_proposal"
            if entity_id in preserved and field_id in {"short_summary", "dossier"}:
                status = "preserved_provider_presentation"
            proposals.append(
                {
                    "entity_id": entity_id,
                    "source_field": field_id,
                    "value": value,
                    "status": status,
                }
            )
    for fact in topic.facts:
        if str(fact.get("source") or "") == "profile_structured_fact_compiler_v1":
            continue
        proposals.append(
            {
                "entity_id": str(fact.get("subject") or ""),
                "source_field": "fact",
                "value": dict(fact),
                "status": "non_canonical_fact_proposal",
            }
        )
    return proposals


def _canonical_entity(
    node: CampaignTopicNode,
    original: Mapping[str, Any],
    facts: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    entity_id = _entity_id(original)
    entity: dict[str, Any] = {
        "id": entity_id,
        "kind": str(
            node.metadata.get("entity_kind") or original.get("kind") or "entity"
        ),
        "visibility": str(original.get("visibility") or node.visibility),
    }
    for fact in facts:
        entity[str(fact["field_id"])] = fact.get("object")
    entity.setdefault("name", str(original.get("name") or entity_id))
    return entity


def _build_dossier(
    node: CampaignTopicNode,
    entity: Mapping[str, Any],
    facts: tuple[dict[str, Any], ...],
    definitions: Mapping[str, Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Build deterministic presentation only for explicit offline/test generation."""

    entity_id = _entity_id(entity)
    name = str(entity.get("name") or entity_id).strip()
    fact_fields = {str(fact.get("field_id") or "") for fact in facts}
    summary_field = _preferred_summary_field(fact_fields)
    summary_value = entity.get(summary_field or "") if summary_field else None
    if summary_value in (None, "", [], (), {}):
        summary_value = next(
            (
                fact.get("object")
                for fact in facts
                if str(fact.get("field_id") or "") not in {"name", "title"}
            ),
            str(node.title),
        )
    rendered_summary = _display(summary_value)
    short_summary = f"{name} — {rendered_summary}" if rendered_summary else name
    if len(short_summary) > 420:
        short_summary = short_summary[:419].rstrip(" ,;:-") + "…"

    grouped: dict[str, dict[str, Any]] = {}
    represented_fields: list[str] = []
    quick_facts: list[dict[str, Any]] = []
    related_ids: list[str] = []
    for fact in facts:
        field_id = str(fact.get("field_id") or "")
        if field_id in {"name", "title"}:
            continue
        definition = definitions.get(field_id, {})
        value_type = str(
            definition.get("value_type") or fact.get("value_type") or ""
        )
        section_id, section_title = _section_for_field(field_id, value_type)
        section = grouped.setdefault(
            section_id,
            {"id": section_id, "title": section_title, "paragraphs": []},
        )
        paragraph = _sentence(field_id, fact.get("object"))
        if paragraph:
            section["paragraphs"].append(paragraph)
            represented_fields.append(field_id)
        if value_type in {
            "string",
            "integer",
            "number",
            "boolean",
            "enum",
            "entity_ref",
        }:
            quick_facts.append(
                {
                    "label": field_id.replace("_", " ").title(),
                    "value": fact.get("object"),
                }
            )
        for reference in fact.get("entity_refs") or ():
            reference_id = str(reference)
            if reference_id and reference_id != entity_id:
                related_ids.append(reference_id)

    overview = {
        "id": "overview",
        "title": "Overview",
        "paragraphs": [short_summary.rstrip(".") + "."],
    }
    ordered_ids = (
        "context",
        "details",
        "pressures",
        "plans",
        "evidence",
        "connections",
    )
    sections = [overview, *(grouped[key] for key in ordered_ids if key in grouped)]
    source_fact_ids = tuple(str(fact.get("id") or "") for fact in facts)
    dossier = {
        "schema_version": _DOSSIER_SCHEMA,
        "subtitle": node.title,
        "quote": None,
        "quick_facts": quick_facts[:12],
        "sections": sections,
        "related_entity_ids": list(dict.fromkeys(related_ids)),
        "generated_from_legacy": False,
        "generated_from_approved_facts": True,
        "source_fact_ids": list(source_fact_ids),
        "represented_field_ids": list(dict.fromkeys(represented_fields)),
    }
    required_representation = fact_fields - {"name", "title"}
    missing = required_representation - set(represented_fields)
    if missing:
        raise StructuredFactValidationError(
            tuple(
                StructuredFactIssue(
                    "presentation_field_not_rendered",
                    node.topic_id,
                    entity_id,
                    field_id,
                    "Validated structured field was not represented in the dossier.",
                    entity.get(field_id),
                )
                for field_id in sorted(missing)
            )
        )
    return short_summary, dossier


def _provider_presentation(
    original: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    short_summary = " ".join(str(original.get("short_summary") or "").split())
    raw_dossier = original.get("dossier")
    if not short_summary or validate_entity_dossier(raw_dossier):
        return None
    return short_summary, dict(cast(Any, raw_dossier))


def _provider_presentation_issues(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    contradiction_report: PresentationContradictionReport,
) -> tuple[WorldForgeIntegrityIssue, ...]:
    issues: list[WorldForgeIntegrityIssue] = []
    for contradiction in contradiction_report.contradictions:
        issues.append(
            WorldForgeIntegrityIssue(
                code="provider_presentation_contradiction",
                topic_id=node.topic_id,
                item_id=contradiction.entity_id,
                field="entities",
                supplied_value=contradiction.source,
                candidates=contradiction.canonical_reference_ids,
                message=(
                    f"{contradiction.message} Regenerate the provider-authored topic; "
                    "deterministic replacement prose is forbidden."
                ),
            )
        )
    for entity in topic.entities:
        entity_id = _entity_id(entity)
        short_summary = " ".join(str(entity.get("short_summary") or "").split())
        dossier_issues = validate_entity_dossier(entity.get("dossier"))
        if not short_summary:
            issues.append(
                WorldForgeIntegrityIssue(
                    code="provider_short_summary_required",
                    topic_id=node.topic_id,
                    item_id=entity_id,
                    field="entities",
                    supplied_value="<missing>",
                    message=(
                        "Live World Forge entities require provider-authored short_summary "
                        "text. Regenerate the topic instead of synthesizing presentation prose."
                    ),
                )
            )
        if dossier_issues:
            issues.append(
                WorldForgeIntegrityIssue(
                    code="provider_dossier_invalid",
                    topic_id=node.topic_id,
                    item_id=entity_id,
                    field="entities",
                    supplied_value=",".join(dossier_issues),
                    message=(
                        "Live World Forge entities require a schema-valid provider-authored "
                        "dossier. Regenerate the topic instead of synthesizing presentation prose."
                    ),
                )
            )
    return tuple(issues)


def render_fact_derived_presentations(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
) -> GeneratedTopic:
    """Render profile canon without replacing live-provider lore with templates."""

    definition_rows = _definitions(node)
    if not definition_rows:
        return topic
    contradiction_report = audit_presentation_contradictions(node, topic)
    provider_generated = _provider_generated(topic)
    if provider_generated:
        provider_issues = _provider_presentation_issues(
            node,
            topic,
            contradiction_report,
        )
        if provider_issues:
            raise WorldForgeIntegrityError(provider_issues)

    definitions = {
        str(definition.get("field_id") or ""): definition
        for definition in definition_rows
    }
    facts = _structured_facts(topic)
    facts_by_entity: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        facts_by_entity.setdefault(str(fact["subject"]), []).append(fact)

    entities: list[dict[str, Any]] = []
    issues: list[StructuredFactIssue] = []
    preserved_entity_ids: set[str] = set()
    for original in topic.entities:
        entity_id = _entity_id(original)
        entity_facts = tuple(facts_by_entity.get(entity_id, ()))
        if not entity_facts:
            issues.append(
                StructuredFactIssue(
                    "presentation_source_facts_missing",
                    node.topic_id,
                    entity_id,
                    "facts",
                    "Profile-driven dossier requires validated structured source facts.",
                )
            )
            continue
        canonical = _canonical_entity(node, original, entity_facts)
        if provider_generated:
            provider_presentation = _provider_presentation(original)
            assert provider_presentation is not None
            short_summary, provider_dossier = provider_presentation
            dossier = {
                **provider_dossier,
                "provider_authored_presentation": True,
            }
            preserved_entity_ids.add(entity_id)
        else:
            short_summary, dossier = _build_dossier(
                node,
                canonical,
                entity_facts,
                definitions,
            )
        canonical.update(
            {
                "short_summary": short_summary,
                "dossier": dossier,
                "dossier_status": "complete",
                "presentation_source_fact_ids": [
                    str(fact.get("id") or "") for fact in entity_facts
                ],
            }
        )
        entities.append(canonical)
    if issues:
        raise StructuredFactValidationError(issues)

    proposals = _presentation_fact_proposals(
        topic,
        preserved_entity_ids=preserved_entity_ids,
    )
    contradiction_rows = contradiction_report.as_dict()["contradictions"]
    documents = tuple(
        {
            **dict(document),
            "authority": "presentation_only",
            "canonical_source_fact_ids": [
                str(fact.get("id") or "") for fact in facts
            ],
            "presentation_contradictions": [
                row
                for row in contradiction_rows
                if row["source"]
                == f"document:{document.get('document_id') or document.get('id') or ''}"
            ],
        }
        for document in topic.documents
    )
    return replace(
        topic,
        entities=tuple(entities),
        facts=facts,
        documents=documents,
        provenance={
            **dict(topic.provenance),
            "presentation_schema": (
                "rpg_provider_authored_presentation_v1"
                if provider_generated
                else "rpg_fact_derived_presentation_v1"
            ),
            "presentation_derived_from_structured_facts": not provider_generated,
            "provider_authored_presentation": provider_generated,
            "provider_presentations_preserved": bool(preserved_entity_ids),
            "provider_presentation_entity_ids": sorted(preserved_entity_ids),
            "presentation_fact_proposals": proposals,
            "presentation_contradiction_report": contradiction_report.as_dict(),
            "discarded_noncanonical_fact_count": sum(
                1
                for proposal in proposals
                if proposal["status"] == "non_canonical_fact_proposal"
            ),
        },
    )
