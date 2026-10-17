"""Additive causal-traceability schema for World Forge genre profiles."""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .world_forge_profile_extensions import extension_fields
from .world_forge_profiles import (
    DomainDefinition,
    DomainTargetRange,
    FieldDefinition,
    GenreProfile,
)

_REQUIRED_CAUSAL_DOMAINS = frozenset(
    {"history_timeline", "regions", "places", "groups", "cultures", "actors"}
)

_LEGACY_STATUSES = (
    "continuing",
    "mixed",
    "terminated",
    "reversed",
    "absorbed",
    "concealed",
    "forgotten",
)

_EFFECT_TYPES = (
    "founded",
    "destroyed",
    "displaced",
    "annexed",
    "fragmented",
    "merged",
    "fortified",
    "isolated",
    "enriched",
    "impoverished",
    "culturally_influenced",
    "legally_inherited",
    "claimed_by",
)


def _field(
    field_id: str,
    value_type: str = "string",
    *,
    required: bool = False,
    targets: tuple[str, ...] = (),
    semantic_role: str = "",
    enum_values: tuple[str, ...] = (),
    description: str = "",
) -> FieldDefinition:
    return FieldDefinition(
        field_id=field_id,
        value_type=value_type,  # type: ignore[arg-type]
        required=required,
        semantic_role=semantic_role,
        allowed_target_domains=targets,
        enum_values=enum_values,
        description=description,
    )


def _append_fields(
    domain: DomainDefinition,
    additions: Iterable[FieldDefinition],
) -> DomainDefinition:
    existing = {field.field_id for field in domain.fields}
    fields = (*domain.fields, *(field for field in additions if field.field_id not in existing))
    return replace(domain, fields=fields)


def _causal_fields(domain_id: str) -> tuple[FieldDefinition, ...]:
    if domain_id == "history_timeline":
        return (
            _field(
                "cause_event_ids",
                "entity_ref_list",
                targets=("history_timeline",),
                semantic_role="caused_by",
                description="Earlier historical events that materially caused this event.",
            ),
            _field(
                "origin_conditions",
                "structured_object",
                required=True,
                description="Pre-existing conditions that made this event possible.",
            ),
            _field(
                "legacy_status",
                "enum",
                required=True,
                enum_values=_LEGACY_STATUSES,
                description="Whether the event's major effects continue or how they ended.",
            ),
            _field(
                "present_day_legacies",
                "structured_object",
                required=True,
                description="Present traces or explicit resolutions of the event's effects.",
            ),
        )
    if domain_id == "regions":
        return (
            _field(
                "formation_event_ids",
                "entity_ref_list",
                targets=("history_timeline",),
                semantic_role="formed_by",
            ),
            _field("origin_conditions", "structured_object", required=True),
            _field("present_day_legacies", "structured_object", required=True),
        )
    if domain_id == "places":
        return (
            _field(
                "founding_event_ids",
                "entity_ref_list",
                targets=("history_timeline",),
                semantic_role="founded_by",
            ),
            _field("founding_purpose", required=True),
            _field("economic_functions", "structured_object", required=True),
            _field("strategic_functions", "structured_object", required=True),
            _field(
                "local_market_signature",
                "structured_object",
                required=True,
                semantic_role="local_market_signature",
                description=(
                    "Bounded place-local demand, supply, price, credit, informal trade, "
                    "enforcement, shock sensitivity, and recovery state."
                ),
            ),
            _field(
                "route_effects",
                "structured_object",
                required=True,
                semantic_role="route_effects",
                description=(
                    "Endpoint-keyed travel cost, variance, hazard, supply, price, "
                    "information-delay, and closure-recovery effects for connected places."
                ),
            ),
        )
    if domain_id == "groups":
        return (
            _field(
                "formation_event_ids",
                "entity_ref_list",
                targets=("history_timeline",),
                semantic_role="formed_by",
            ),
            _field("inherited_claims", "structured_object", required=True),
            _field("historical_grievances", "structured_object", required=True),
            _field(
                "constrained_by_group_ids",
                "entity_ref_list",
                required=True,
                targets=("groups",),
                semantic_role="countervailing_constraint",
                description="Canonical groups that can materially constrain this group's power.",
            ),
            _field(
                "countervailing_power_signature",
                "structured_object",
                required=True,
                semantic_role="countervailing_power_signature",
                description="Bounded authority, leverage, accountability, reach, vulnerability, and failure profile.",
            ),
        )
    if domain_id == "cultures":
        return (
            _field(
                "origin_event_ids",
                "entity_ref_list",
                targets=("history_timeline",),
                semantic_role="originated_in",
            ),
            _field(
                "origin_region_ids",
                "entity_ref_list",
                targets=("regions",),
                semantic_role="origin_region",
            ),
            _field(
                "parent_culture_ids",
                "entity_ref_list",
                targets=("cultures",),
                semantic_role="descended_from",
            ),
            _field("migration_legacy", "structured_object", required=True),
        )
    if domain_id == "actors":
        return (
            _field(
                "formative_event_ids",
                "entity_ref_list",
                targets=("history_timeline",),
                semantic_role="shaped_by",
            ),
            _field(
                "culture_ids",
                "entity_ref_list",
                targets=("cultures",),
                semantic_role="cultural_affiliation",
                description="Additional cultural affiliations; culture_id remains primary.",
            ),
        )
    return ()


def _causal_links_domain() -> DomainDefinition:
    return DomainDefinition(
        domain_id="causal_links",
        title="Historical Causes and Lasting Effects",
        entity_kind="causal_link",
        dependencies=(
            "history_timeline",
            "regions",
            "places",
            "groups",
            "cultures",
            "actors",
        ),
        fields=(
            _field("name", required=True),
            _field(
                "cause_event_ids",
                "entity_ref_list",
                required=True,
                targets=("history_timeline",),
                semantic_role="cause",
            ),
            _field(
                "effect_id",
                "entity_ref",
                required=True,
                targets=("regions", "places", "groups", "cultures", "actors"),
                semantic_role="effect",
            ),
            _field(
                "effect_type",
                "enum",
                required=True,
                enum_values=_EFFECT_TYPES,
            ),
            _field(
                "mechanism",
                required=True,
                description="Concrete process connecting the historical cause to the effect.",
            ),
            _field(
                "persistence",
                "enum",
                required=True,
                enum_values=tuple(value for value in _LEGACY_STATUSES if value != "mixed"),
            ),
            _field("start_year", "integer"),
            _field("end_year", "integer"),
        ),
        target_range=DomainTargetRange((3, 5), (6, 10), (12, 18)),
        visibility_default="game_master_canon",
        category="lore",
        generation_guidance={
            "internal": False,
            "presentation": {
                "page_kind": "collection",
                "card_variant": "causal_links",
                "image_role": "none",
                "group": "lore",
            },
        },
    )


def augment_profile_with_causal_traceability(profile: GenreProfile) -> GenreProfile:
    """Return a validated additive causal schema without mutating stored profiles."""

    domain_map = profile.domain_map()
    if domain_map.get("causal_links") is not None:
        return profile.require_valid()
    if not _REQUIRED_CAUSAL_DOMAINS.issubset(domain_map):
        return profile.require_valid()

    domains: list[DomainDefinition] = []
    inserted = False
    for domain in profile.domains:
        additions = (*_causal_fields(domain.domain_id), *extension_fields(domain.domain_id))
        domains.append(_append_fields(domain, additions))
        if domain.domain_id == "actors":
            domains.append(_causal_links_domain())
            inserted = True
    if not inserted:
        domains.append(_causal_links_domain())

    provenance = {
        **dict(profile.provenance),
        "causal_traceability_schema": "rpg_world_forge_causal_traceability_v1",
        "base_profile_version": profile.version,
        "base_profile_hash": profile.content_hash,
    }
    return replace(
        profile,
        version=max(profile.version, 3),
        domains=tuple(domains),
        provenance=provenance,
    ).require_valid()


__all__ = ["augment_profile_with_causal_traceability"]
