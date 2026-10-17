"""Late-roadmap additive fields for validated World Forge profiles."""
from __future__ import annotations

from .world_forge_profiles import FieldDefinition

_LOCAL_NARRATIVE_DOMAINS = frozenset({"quests", "encounter_seeds", "opening_threads"})


def _field(
    field_id: str,
    value_type: str = "string",
    *,
    required: bool = False,
    targets: tuple[str, ...] = (),
    role: str = "",
    description: str = "",
) -> FieldDefinition:
    return FieldDefinition(
        field_id=field_id,
        value_type=value_type,  # type: ignore[arg-type]
        required=required,
        allowed_target_domains=targets,
        semantic_role=role,
        description=description,
    )


def _local_narrative_fields() -> tuple[FieldDefinition, ...]:
    return (
        _field(
            "local_place_ids",
            "entity_ref_list",
            required=True,
            targets=("places",),
            role="local_narrative_place",
            description="Canonical places where the opportunity can be discovered or acted upon.",
        ),
        _field(
            "local_pressure_ids",
            "entity_ref_list",
            required=True,
            targets=("pressures",),
            role="local_narrative_pressure",
            description="Canonical pressures that create the opportunity and receive its consequences.",
        ),
        _field(
            "local_actor_ids",
            "entity_ref_list",
            required=True,
            targets=("actors",),
            role="local_narrative_actor",
            description="Canonical local actors who expose, contest, or are affected by the opportunity.",
        ),
        _field(
            "local_group_ids",
            "entity_ref_list",
            required=True,
            targets=("groups",),
            role="local_narrative_group",
            description="Canonical groups whose information reach and response constrain discovery.",
        ),
        _field(
            "local_evidence_source_ids",
            "entity_ref_list",
            required=True,
            targets=("places", "actors", "pressures", "groups"),
            role="local_narrative_evidence",
            description="Canonical local entities that physically or socially expose the opportunity.",
        ),
        _field(
            "local_narrative_signature",
            "structured_object",
            required=True,
            role="local_narrative_signature",
            description=(
                "Bounded discovery channel, evidence, urgency, expiry, consequence scope, "
                "entry mode, information scope, and failure visibility."
            ),
        ),
    )


def extension_fields(domain_id: str) -> tuple[FieldDefinition, ...]:
    if domain_id == "groups":
        return (
            _field(
                "information_anchor_place_id",
                "entity_ref",
                required=True,
                targets=("places",),
                role="information_anchor",
                description="Canonical place from which this group evaluates information latency.",
            ),
            _field(
                "information_place_ids",
                "entity_ref_list",
                required=True,
                targets=("places",),
                role="information_reach",
                description="Canonical places within the group's direct, regularly updated information reach.",
            ),
            _field(
                "information_locality_signature",
                "structured_object",
                required=True,
                role="information_locality_signature",
                description="Bounded channel, latency, verification, distortion, interception, blackout, cadence, and confidence-decay profile.",
            ),
        )
    if domain_id == "actors":
        return (
            _field(
                "vendor_inventory_item_ids",
                "entity_ref_list",
                targets=("equipment_vehicles",),
                role="starting_vendor_inventory",
                description=(
                    "Canonical equipment or commodity definitions this actor can offer when "
                    "materialized as a local vendor."
                ),
            ),
        )
    if domain_id in _LOCAL_NARRATIVE_DOMAINS:
        return _local_narrative_fields()
    return ()


__all__ = ["extension_fields"]
