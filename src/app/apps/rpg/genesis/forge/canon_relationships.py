"""Compile legacy, profile-typed, and causal canon relationships."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .world_forge_generation import GeneratedTopic


def _slug(value: str) -> str:
    return "_".join(
        "".join(ch.casefold() if ch.isalnum() else " " for ch in value).split()
    )


def _relationship(
    source_id: str,
    target_id: str,
    kind: str,
    *,
    visibility: str = "game_master_canon",
    content: str = "",
    source: str = "relationship_compiler",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "id": f"relationship:{_slug(source_id)}:{_slug(kind)}:{_slug(target_id)}",
        "source_id": source_id,
        "target_id": target_id,
        "kind": kind,
        "content": content or f"{source_id} {kind.replace('_', ' ')} {target_id}.",
        "authority": "generated_proposal",
        "approved_authority": "objective_canon",
        "visibility": visibility,
        "entity_refs": [source_id, target_id],
        "compiled_by": source,
    }
    if metadata:
        extra_refs = tuple(
            str(value)
            for value in metadata.get("entity_refs") or ()
            if str(value)
        )
        row.update(
            {
                key: value
                for key, value in metadata.items()
                if key != "entity_refs"
            }
        )
        row["entity_refs"] = list(
            dict.fromkeys([source_id, target_id, *extra_refs])
        )
    return row


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _typed_reference_values(value: Any, value_type: str) -> tuple[str, ...]:
    if value_type == "entity_ref":
        rendered = str(value or "").strip()
        return (rendered,) if rendered else ()
    if value_type == "entity_ref_list" and isinstance(value, Sequence) and not isinstance(
        value, (str, bytes)
    ):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _profile_relationship_kind(fact: Mapping[str, Any]) -> str:
    semantic_role = str(fact.get("semantic_role") or "").strip()
    if semantic_role:
        return semantic_role
    field_id = str(fact.get("field_id") or fact.get("predicate") or "references").strip()
    if field_id.endswith("_ids"):
        return field_id[:-4] or "references"
    if field_id.endswith("_id"):
        return field_id[:-3] or "references"
    return field_id or "references"


def _is_profile_structured_fact(fact: Mapping[str, Any]) -> bool:
    """Recognise trusted profile facts without pinning one compiler revision."""

    source = str(fact.get("source") or "")
    return (
        source.startswith("profile_structured_fact_compiler_v")
        and bool(str(fact.get("subject") or "").strip())
        and bool(str(fact.get("field_id") or fact.get("predicate") or "").strip())
        and str(fact.get("value_type") or "") in {"entity_ref", "entity_ref_list"}
    )


def _causal_relationships(topic: GeneratedTopic) -> tuple[dict[str, Any], ...]:
    if topic.topic_id != "causal_links":
        return ()
    relationships: list[dict[str, Any]] = []
    for entity in topic.entities:
        causal_link_id = str(entity.get("id") or entity.get("entity_id") or "").strip()
        effect_id = str(entity.get("effect_id") or "").strip()
        effect_type = str(entity.get("effect_type") or "historically_affected").strip()
        causes = _strings(entity.get("cause_event_ids"))
        if not causal_link_id or not effect_id or not causes:
            continue
        mechanism = str(entity.get("mechanism") or "").strip()
        persistence = str(entity.get("persistence") or "").strip()
        start_year = entity.get("start_year")
        end_year = entity.get("end_year")
        for cause_id in causes:
            relationships.append(
                _relationship(
                    cause_id,
                    effect_id,
                    effect_type,
                    visibility=str(entity.get("visibility") or "game_master_canon"),
                    content=mechanism,
                    source="causal_relationship_compiler_v1",
                    metadata={
                        "causal_link_id": causal_link_id,
                        "mechanism": mechanism,
                        "persistence": persistence,
                        "start_year": start_year,
                        "end_year": end_year,
                        "entity_refs": [causal_link_id],
                    },
                )
            )
    return tuple(relationships)


def compile_cross_domain_relationships(
    topics: Iterable[GeneratedTopic],
) -> tuple[dict[str, Any], ...]:
    """Derive relationships from causal entities, typed facts, and legacy fields."""

    topic_rows = tuple(topics)
    entities: dict[str, Mapping[str, Any]] = {}
    for topic in topic_rows:
        for row in topic.entities:
            entity_id = str(row.get("id") or "").strip()
            if entity_id:
                entities[entity_id] = row

    derived: list[dict[str, Any]] = []
    typed_fields: set[tuple[str, str]] = set()
    for topic in topic_rows:
        derived.extend(_causal_relationships(topic))
        for fact in topic.facts:
            if not _is_profile_structured_fact(fact):
                continue
            value_type = str(fact.get("value_type") or "")
            source_id = str(fact.get("subject") or "").strip()
            field_id = str(fact.get("field_id") or fact.get("predicate") or "").strip()
            targets = _typed_reference_values(fact.get("object"), value_type)
            if not source_id or not field_id or not targets:
                continue
            typed_fields.add((source_id, field_id))
            kind = _profile_relationship_kind(fact)
            for target_id in targets:
                derived.append(
                    _relationship(
                        source_id,
                        target_id,
                        kind,
                        visibility=str(fact.get("visibility") or "game_master_canon"),
                        content=str(fact.get("content") or ""),
                        source="profile_typed_relationship_compiler_v1",
                    )
                )

    for entity_id, row in entities.items():
        pairs = (
            ("realm_id", "within_realm"),
            ("region_id", "located_in"),
            ("location_id", "present_at"),
            ("institution_id", "affiliated_with"),
            ("parent_id", "part_of"),
        )
        for field, kind in pairs:
            if (entity_id, field) in typed_fields:
                continue
            target_id = str(row.get(field) or "").strip()
            if target_id:
                derived.append(_relationship(entity_id, target_id, kind))
        for field, kind in (
            ("faction_ids", "member_of"),
            ("opposes", "opposes"),
            ("allies", "allied_with"),
            ("recruits_from", "recruits_from"),
            ("worships", "worships"),
            ("controls", "controls"),
            ("knows", "knows"),
        ):
            if (entity_id, field) in typed_fields:
                continue
            for target_id in _strings(row.get(field)):
                derived.append(_relationship(entity_id, target_id, kind))
        routes = row.get("travel_routes")
        for route in routes if isinstance(routes, (list, tuple)) else ():
            if not isinstance(route, Mapping):
                continue
            target_id = str(route.get("target_id") or route.get("to") or "").strip()
            if target_id:
                derived.append(
                    _relationship(
                        entity_id,
                        target_id,
                        "travel_route",
                        visibility=str(route.get("visibility") or "public"),
                        content=str(route.get("description") or "")
                        or f"A travel route links {entity_id} and {target_id}.",
                    )
                )

    unique: dict[str, dict[str, Any]] = {}
    for row in derived:
        relationship_id = str(row.get("id") or "").strip()
        if relationship_id:
            unique.setdefault(relationship_id, row)
    return tuple(unique[key] for key in sorted(unique))
