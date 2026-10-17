"""Apply authoritative internal planning assignments before canon publication."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .world_forge_contract import CampaignTopicNode
from .world_forge_fact_pipeline_trusted import compile_structured_entity_facts
from .world_forge_generation import GeneratedTopic, WorldForgeTopicGenerator
from .world_forge_presentation_trusted import render_fact_derived_presentations


def _entity_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("entity_id") or "").strip()


def _planning_slice(context: Mapping[str, Any]) -> dict[str, Any]:
    value = context.get("planning_slice")
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any, key: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping):
        return ()
    return tuple(dict(row) for row in value.get(key) or () if isinstance(row, Mapping))


def _expected_ids(node: CampaignTopicNode) -> tuple[str, ...]:
    value = node.metadata.get("authoritative_entity_ids")
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _remap_value(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_remap_value(item, mapping) for item in value]
    if isinstance(value, tuple):
        return tuple(_remap_value(item, mapping) for item in value)
    if isinstance(value, Mapping):
        return {str(key): _remap_value(item, mapping) for key, item in value.items()}
    return value


def _display(value: Any) -> str:
    if isinstance(value, Mapping):
        return "; ".join(
            f"{str(key).replace('_', ' ')}: {_display(item)}"
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return ", ".join(_display(item) for item in value)
    return " ".join(str(value or "").split())


def _materialize_authoritative_ids(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
) -> tuple[GeneratedTopic, bool]:
    expected_ids = _expected_ids(node)
    actual_ids = tuple(_entity_id(row) for row in topic.entities if _entity_id(row))
    if not expected_ids or actual_ids == expected_ids:
        return topic, False
    if len(actual_ids) != len(expected_ids) or len(set(actual_ids)) != len(actual_ids):
        raise ValueError(
            f"authoritative_anchor_identity_mismatch:{node.topic_id}:{actual_ids}:{expected_ids}"
        )
    mapping = dict(zip(actual_ids, expected_ids))
    payload = _remap_value(topic.as_dict(), mapping)
    payload["facts"] = [
        fact
        for fact in payload.get("facts") or ()
        if not str(fact.get("source") or "").startswith("profile_structured_fact_compiler_v")
    ]
    remapped = GeneratedTopic.from_dict(payload)
    return replace(
        remapped,
        provenance={
            **dict(remapped.provenance),
            "authoritative_identity_mapping": mapping,
        },
    ), True


def _apply_place_plan(entity: dict[str, Any], planning: Mapping[str, Any]) -> set[str]:
    place_id = _entity_id(entity)
    row = next(
        (
            item
            for item in _rows(planning.get("settlement_origin_plan"), "settlements")
            if str(item.get("place_id") or "") == place_id
        ),
        None,
    )
    if row is None:
        return set()
    changed: set[str] = set()
    assignments = {
        "region_id": str(row.get("region_id") or ""),
        "founding_event_ids": [str(row.get("founding_event_id") or "")]
        if row.get("founding_event_id")
        else [],
        "founding_purpose": str(row.get("founding_purpose") or ""),
    }
    for field_id, value in assignments.items():
        if value not in ("", []) and entity.get(field_id) != value:
            entity[field_id] = value
            changed.add(field_id)
    return changed


def _apply_culture_plan(entity: dict[str, Any], planning: Mapping[str, Any]) -> set[str]:
    culture_id = _entity_id(entity)
    row = next(
        (
            item
            for item in _rows(planning.get("culture_lineage_plan"), "lineages")
            if str(item.get("culture_id") or "") == culture_id
        ),
        None,
    )
    if row is None:
        return set()
    regions = [str(value) for value in row.get("homeland_region_ids") or () if str(value)]
    parent = str(row.get("parent_culture_id") or "")
    event_id = str(row.get("origin_event_id") or "")
    assignments = {
        "region_ids": regions,
        "origin_region_ids": regions,
        "origin_event_ids": [event_id] if event_id else [],
        "parent_culture_ids": [parent] if parent else [],
    }
    changed: set[str] = set()
    for field_id, value in assignments.items():
        if entity.get(field_id) != value:
            entity[field_id] = value
            changed.add(field_id)
    return changed


def _apply_group_plan(entity: dict[str, Any], planning: Mapping[str, Any]) -> set[str]:
    group_id = _entity_id(entity)
    claims = [
        row
        for row in _rows(planning.get("political_claim_graph"), "claims")
        if str(row.get("claimant_group_id") or "") == group_id
    ]
    if not claims:
        return set()
    inherited = entity.get("inherited_claims")
    payload = dict(inherited) if isinstance(inherited, Mapping) else {}
    planned = [
        {
            "claim_id": str(row.get("claim_id") or ""),
            "target_region_id": str(row.get("target_region_id") or ""),
            "claim_type": str(row.get("claim_type") or ""),
            "status": str(row.get("status") or ""),
        }
        for row in claims
    ]
    if payload.get("planned_claims") == planned:
        return set()
    payload["planned_claims"] = planned
    entity["inherited_claims"] = payload
    return {"inherited_claims"}


def _planned_causal_links(
    topic: GeneratedTopic,
    planning: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    if topic.topic_id != "causal_links":
        return ()
    existing = {
        (
            cause_id,
            str(entity.get("effect_id") or ""),
            str(entity.get("effect_type") or ""),
        )
        for entity in topic.entities
        for cause_id in entity.get("cause_event_ids") or ()
        if str(cause_id)
    }
    rows: list[dict[str, Any]] = []
    sequence = 1
    for settlement in _rows(planning.get("settlement_origin_plan"), "settlements"):
        event_id = str(settlement.get("founding_event_id") or "")
        effect_id = str(settlement.get("place_id") or "")
        signature = (event_id, effect_id, "founded")
        if not event_id or not effect_id or signature in existing:
            continue
        purpose = str(settlement.get("founding_purpose") or "coordination centre").replace("_", " ")
        resource = str(settlement.get("resource_dependency") or "local resources").replace("_", " ")
        rows.append(
            {
                "id": f"ent:causal:planned:{sequence:03d}",
                "kind": "causal_link",
                "name": f"Founding of {effect_id}",
                "cause_event_ids": [event_id],
                "effect_id": effect_id,
                "effect_type": "founded",
                "mechanism": f"The historical event created a permanent {purpose} organised around access to {resource}.",
                "persistence": "continuing",
                "start_year": settlement.get("founded_year"),
                "visibility": "game_master_canon",
            }
        )
        existing.add(signature)
        sequence += 1
    for lineage in _rows(planning.get("culture_lineage_plan"), "lineages"):
        event_id = str(lineage.get("origin_event_id") or "")
        effect_id = str(lineage.get("culture_id") or "")
        signature = (event_id, effect_id, "culturally_influenced")
        if not event_id or not effect_id or signature in existing:
            continue
        lineage_type = str(lineage.get("lineage_type") or "local").replace("_", " ")
        adaptation = str(lineage.get("environmental_adaptation") or "shared practice").replace("_", " ")
        rows.append(
            {
                "id": f"ent:causal:planned:{sequence:03d}",
                "kind": "causal_link",
                "name": f"Origin of {effect_id}",
                "cause_event_ids": [event_id],
                "effect_id": effect_id,
                "effect_type": "culturally_influenced",
                "mechanism": f"The event consolidated a {lineage_type} community through the shared practice of {adaptation}.",
                "persistence": "continuing",
                "visibility": "game_master_canon",
            }
        )
        existing.add(signature)
        sequence += 1
    return tuple(rows)


def _compile_and_render(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependency_topics: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    compiled = compile_structured_entity_facts(node, topic, dependency_topics)
    rendered = render_fact_derived_presentations(node, compiled)
    facts = tuple(
        {
            **dict(fact),
            "content": str(fact.get("content") or "").strip()
            or f"{str(fact.get('field_id') or fact.get('predicate') or 'Fact').replace('_', ' ').title()}: {_display(fact.get('object'))}.",
            "expanded_description": str(fact.get("expanded_description") or "").strip()
            or f"This statement is a direct presentation of the approved structured field {str(fact.get('field_id') or fact.get('predicate') or 'fact').replace('_', ' ')} for {fact.get('subject')}.",
        }
        for fact in rendered.facts
    )
    return replace(
        rendered,
        facts=facts,
        provenance={
            **dict(rendered.provenance),
            "fact_summary_projection": "structured_object_display_v1",
        },
    )


def project_planning_into_topic(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    *,
    campaign_context: Mapping[str, Any],
    dependency_topics: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    planning = _planning_slice(campaign_context)
    topic, identity_changed = _materialize_authoritative_ids(node, topic)
    if not planning:
        return _compile_and_render(node, topic, dependency_topics) if identity_changed else topic

    entities: list[dict[str, Any]] = []
    changed_fields: dict[str, set[str]] = {}
    for raw in topic.entities:
        entity = dict(raw)
        changed: set[str] = set()
        if node.topic_id == "places":
            changed.update(_apply_place_plan(entity, planning))
        elif node.topic_id == "cultures":
            changed.update(_apply_culture_plan(entity, planning))
        elif node.topic_id == "groups":
            changed.update(_apply_group_plan(entity, planning))
        entity_id = _entity_id(entity)
        if entity_id and changed:
            changed_fields[entity_id] = changed
        entities.append(entity)
    supplemental = _planned_causal_links(topic, planning)
    if supplemental:
        entities.extend(supplemental)
        changed_fields.update({_entity_id(row): set() for row in supplemental})
    if not changed_fields and not identity_changed:
        return topic

    facts = tuple(
        fact
        for fact in topic.facts
        if not str(fact.get("source") or "").startswith("profile_structured_fact_compiler_v")
    )
    projected = replace(
        topic,
        entities=tuple(entities),
        facts=facts,
        provenance={
            **dict(topic.provenance),
            "planning_projection_schema": "rpg_world_forge_plan_projection_v2",
            "planning_projected_entity_ids": sorted(changed_fields),
            "planned_causal_link_count": len(supplemental),
        },
    )
    return _compile_and_render(node, projected, dependency_topics)


class PlanningConstrainedWorldForgeGenerator:
    """Decorator that makes anchor and planning assignments publication-authoritative."""

    def __init__(self, delegate: WorldForgeTopicGenerator) -> None:
        self.delegate = delegate

    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic:
        topic = self.delegate.generate(
            node,
            seed=seed,
            campaign_context=campaign_context,
            dependency_topics=dependency_topics,
        )
        return project_planning_into_topic(
            node,
            topic,
            campaign_context=campaign_context,
            dependency_topics=dependency_topics,
        )


__all__ = [
    "PlanningConstrainedWorldForgeGenerator",
    "project_planning_into_topic",
]
