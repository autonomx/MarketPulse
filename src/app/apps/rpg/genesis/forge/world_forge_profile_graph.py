"""Compile full and launch CampaignTopicGraph objects from validated profiles."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from .world_forge_authorship_policy import field_policy_row, topic_authorship_policy
from .world_forge_causal_generation import causal_generation_contract
from .world_forge_causal_profile import augment_profile_with_causal_traceability
from .world_forge_contract import CampaignTopicGraph, CampaignTopicNode
from .world_forge_lore_quality import lore_quality_contract
from .world_forge_ordinary_life import ordinary_life_components
from .world_forge_planning import planning_contract_metadata
from .world_forge_profiles import DomainDefinition, FieldDefinition, GenreProfile
from .world_forge_spatial_routes import minimum_route_count

_PIPELINE_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}
_MISSIONS = {"quests", "encounter_seeds", "opening_threads", "opening_scenarios"}
_ACTORS = {"actors"}
_NETWORKS = {"networks"}
_SPATIAL = {"places"}
_ECONOMIC = {"places", "economy_law"}
_RESOURCES = {"groups", "technology_augmentations", "economy_law"}
_ORDINARY_LIFE = {"cultures"}
_NETWORK_CAPABILITY = "digital_spaces"
_PROVIDER_TARGETS = {
    "groups": ("places",),
    "technology_augmentations": ("groups",),
    "economy_law": ("groups",),
}
_CONSUMER_TARGETS = {
    "groups": ("places",),
    "technology_augmentations": ("groups",),
    "economy_law": ("places",),
}


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


_MISSION_FIELDS = (
    _field("mission_signature", "structured_object", required=True, role="mission_signature"),
    _field("campaign_arc_id", role="campaign_arc"),
    _field("arc_role", role="campaign_arc"),
    _field("arc_sequence", "integer", role="campaign_arc"),
)
_ACTOR_FIELDS = (
    _field("incentive_signature", "structured_object", required=True, role="actor_incentive_signature"),
)
_NETWORK_FIELDS = (
    _field("controller_group_ids", "entity_ref_list", required=True, targets=("groups",), role="network_controller"),
    _field("covered_place_ids", "entity_ref_list", required=True, targets=("places",), role="network_coverage"),
    _field("network_constraint_signature", "structured_object", required=True, role="network_constraint_signature"),
)
_SPATIAL_FIELDS = (
    _field("connected_place_ids", "entity_ref_list", required=True, targets=("places",), role="travel_route"),
    _field("travel_route_signature", "structured_object", required=True, role="travel_route_signature"),
)
_ECONOMIC_FIELDS = (
    _field("economic_scale_signature", "structured_object", required=True, role="economic_scale_signature"),
)
_ORDINARY_LIFE_FIELDS = (
    _field("ordinary_life_place_ids", "entity_ref_list", required=True, targets=("places",), role="ordinary_life_place"),
    _field("ordinary_life_signature", "structured_object", required=True, role="ordinary_life_signature"),
)


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _network_enabled(domain: DomainDefinition, flags: Mapping[str, bool]) -> bool:
    return bool(flags.get(_NETWORK_CAPABILITY)) and domain.domain_id in _NETWORKS


def _spatial_enabled(domain: DomainDefinition) -> bool:
    return domain.domain_id in _SPATIAL and domain.target_range.epic[1] > 1


def _resource_fields(domain: DomainDefinition) -> tuple[FieldDefinition, ...]:
    if domain.domain_id not in _RESOURCES:
        return ()
    return (
        _field("resource_provider_ids", "entity_ref_list", required=True, targets=_PROVIDER_TARGETS[domain.domain_id], role="resource_provider"),
        _field("resource_consumer_ids", "entity_ref_list", required=True, targets=_CONSUMER_TARGETS[domain.domain_id], role="resource_consumer"),
        _field("resource_dependency_signature", "structured_object", required=True, role="resource_dependency_signature"),
    )


def _domain_fields(domain: DomainDefinition, flags: Mapping[str, bool]) -> tuple[FieldDefinition, ...]:
    network_enabled = _network_enabled(domain, flags)
    economic_enabled = domain.domain_id in _ECONOMIC
    fields = [
        replace(field, required=True)
        if (network_enabled and field.field_id == "controller_group_ids")
        or (economic_enabled and domain.domain_id == "economy_law" and field.field_id == "affected_place_ids")
        else field
        for field in domain.fields
    ]
    additions: tuple[FieldDefinition, ...] = ()
    if domain.domain_id in _MISSIONS:
        additions += _MISSION_FIELDS
    if domain.domain_id in _ACTORS:
        additions += _ACTOR_FIELDS
    if network_enabled:
        additions += _NETWORK_FIELDS
    if _spatial_enabled(domain):
        additions += _SPATIAL_FIELDS
    if economic_enabled:
        additions += _ECONOMIC_FIELDS
    additions += _resource_fields(domain)
    if domain.domain_id in _ORDINARY_LIFE:
        additions += _ORDINARY_LIFE_FIELDS
    existing = {field.field_id for field in fields}
    fields.extend(field for field in additions if field.field_id not in existing)
    return tuple(fields)


def _contract_metadata(domain: DomainDefinition, depth: str, network_enabled: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if domain.domain_id in _MISSIONS:
        result["mission_signature_contract"] = {
            "schema_version": "rpg_world_mission_signature_contract_v1",
            "required": True,
            "signature_field": "mission_signature",
            "arc_fields": ["campaign_arc_id", "arc_role", "arc_sequence"],
            "signature_components": ["activity", "target", "location", "principal_actor", "antagonist", "pressure", "resolution_modes", "consequence_type"],
        }
    if domain.domain_id in _ACTORS:
        result["actor_incentive_contract"] = {
            "schema_version": "rpg_world_actor_incentive_contract_v1",
            "required": True,
            "signature_field": "incentive_signature",
            "signature_components": ["primary_motive", "scarce_need", "dependency_type", "risk_tolerance", "preferred_method", "red_line", "alliance_preference", "conflict_preference"],
        }
    if network_enabled:
        result["network_constraint_contract"] = {
            "schema_version": "rpg_world_network_constraint_contract_v1",
            "required": True,
            "capability": _NETWORK_CAPABILITY,
            "controller_field": "controller_group_ids",
            "coverage_field": "covered_place_ids",
            "signature_field": "network_constraint_signature",
            "signature_components": ["coverage_scope", "access_model", "latency_class", "monitoring_mode", "blind_spot", "traceability_limit", "failure_mode", "jurisdiction_model"],
        }
    if _spatial_enabled(domain):
        count = max(1, domain.target_range.target(depth))
        result["spatial_route_contract"] = {
            "schema_version": "rpg_world_spatial_route_contract_v1",
            "required": True,
            "connection_field": "connected_place_ids",
            "signature_field": "travel_route_signature",
            "signature_components": ["travel_time_band", "access_mode", "route_blocker", "failure_condition", "capacity_class", "information_delay"],
            "place_count": count,
            "minimum_route_count": minimum_route_count(count, depth),
            "depth": str(depth or "standard"),
        }
    if domain.domain_id in _ECONOMIC:
        result["economic_scale_contract"] = {
            "schema_version": "rpg_world_economic_scale_contract_v1",
            "required": True,
            "signature_field": "economic_scale_signature",
            "coverage_field": "affected_place_ids" if domain.domain_id == "economy_law" else "",
            "expected_scope": "service_system" if domain.domain_id == "economy_law" else "place_population",
            "signature_components": ["scale_scope", "served_population_band", "workforce_band", "service_reach_band", "throughput_band", "price_basis", "scarcity_level", "reserve_horizon", "demand_pressure"],
        }
    if domain.domain_id in _RESOURCES:
        result["resource_dependency_contract"] = {
            "schema_version": "rpg_world_resource_dependency_contract_v1",
            "required": True,
            "provider_field": "resource_provider_ids",
            "consumer_field": "resource_consumer_ids",
            "signature_field": "resource_dependency_signature",
            "signature_components": ["resource_class", "supply_mode", "dependency_strength", "substitute_class", "bottleneck_type", "depletion_horizon", "failure_consequence", "recovery_mode"],
        }
    if domain.domain_id in _ORDINARY_LIFE:
        result["ordinary_life_contract"] = {
            "schema_version": "rpg_world_ordinary_life_contract_v1",
            "required": True,
            "place_field": "ordinary_life_place_ids",
            "signature_field": "ordinary_life_signature",
            "signature_components": list(ordinary_life_components()),
        }
    return result


def _field_metadata(domain: DomainDefinition, flags: Mapping[str, bool], depth: str) -> dict[str, Any]:
    fields = _domain_fields(domain, flags)
    guidance = dict(domain.generation_guidance)
    metadata = {
        "entity_kind": domain.entity_kind,
        "required_entity_fields": [field.field_id for field in fields if field.required],
        "field_definitions": [field_policy_row(field) for field in fields],
        "authorship_policy": topic_authorship_policy(fields),
        "reference_fields": {
            field.field_id: {"value_type": field.value_type, "allowed_target_domains": list(field.allowed_target_domains)}
            for field in fields
            if field.value_type in {"entity_ref", "entity_ref_list"}
        },
        "semantic_roles": list(domain.semantic_roles),
        "generation_guidance": guidance,
        "presentation": _record(guidance.get("presentation")),
        "schema_version": f"rpg_profile_domain_{domain.domain_id}_{'v2' if len(fields) != len(domain.fields) else 'v1'}",
    }
    metadata.update(_contract_metadata(domain, depth, _network_enabled(domain, flags)))
    return metadata


def _effective_dependencies(domain: DomainDefinition, flags: Mapping[str, bool]) -> tuple[str, ...]:
    referenced = (
        target
        for field in _domain_fields(domain, flags)
        if field.value_type in {"entity_ref", "entity_ref_list"}
        for target in field.allowed_target_domains
        if target != domain.domain_id
    )
    return tuple(dict.fromkeys((*domain.dependencies, *referenced)))


def _domain_node(domain: DomainDefinition, *, depth: str, flags: Mapping[str, bool]) -> CampaignTopicNode:
    metadata = _field_metadata(domain, flags, depth)
    page_kind = str(_record(metadata.get("presentation")).get("page_kind") or "document")
    category = domain.domain_id if domain.category == "domain" and page_kind == "collection" else domain.category
    if category == "domain":
        category = "lore"
    probe = CampaignTopicNode(
        topic_id=domain.domain_id,
        title=domain.title,
        category=category,
        dependencies=_effective_dependencies(domain, flags),
        generator_role=domain.generator_role,
        required_before_launch=domain.required_before_launch,
        visibility=domain.visibility_default,
        target_count=max(1, domain.target_range.target(depth)),
        metadata=metadata,
    )
    configured = _record(_record(domain.generation_guidance).get("lore_quality"))
    metadata["lore_quality"] = configured or lore_quality_contract(probe)
    causal = causal_generation_contract(probe)
    if causal:
        metadata["causal_generation_contract"] = causal
    return replace(probe, metadata=metadata)


def _pipeline_nodes(domain_ids: tuple[str, ...]) -> tuple[CampaignTopicNode, ...]:
    specs = (
        ("relationships", "Cross-domain Relationships", "compiler", domain_ids, "relationship_compiler"),
        ("consistency_audit", "Canon Consistency Audit", "audit", ("relationships",), "canon_critic"),
        ("canon_compile", "Canon Compilation", "compiler", ("consistency_audit",), "canon_compiler"),
        ("retrieval_index", "Lore Retrieval Index", "index", ("canon_compile",), "retrieval_index_compiler"),
        ("opening_materialization", "Opening Scene Materialization", "bootstrap", ("canon_compile", "retrieval_index"), "campaign_materializer"),
    )
    return tuple(
        CampaignTopicNode(topic_id=i, title=t, category=c, dependencies=d, generator_role=r)
        for i, t, c, d, r in specs
    )


def _enabled(domain_ids: tuple[str, ...], selected: set[str]) -> list[str]:
    return sorted(selected.intersection(domain_ids))


def build_profile_topic_graph(
    profile: GenreProfile,
    *,
    campaign_template: str,
    depth: str = "standard",
    tone: str = "",
    starting_location: str = "",
    background_expansion: bool = False,
    runtime_capabilities: dict[str, bool] | None = None,
) -> CampaignTopicGraph:
    profile = augment_profile_with_causal_traceability(profile)
    flags = {**profile.runtime_capability_defaults.as_dict(), **dict(runtime_capabilities or {})}
    nodes = tuple(_domain_node(domain, depth=depth, flags=flags) for domain in profile.domains)
    domain_ids = tuple(domain.domain_id for domain in profile.domains)
    metadata: dict[str, Any] = {
        "genre_profile_id": profile.profile_id,
        "genre_profile_version": profile.version,
        "resolved_profile_hash": str(dict(profile.provenance).get("base_profile_hash") or profile.content_hash),
        "compiled_profile_hash": profile.content_hash,
        "resolved_profile": profile.as_dict(),
        "genre_tags": list(profile.genre_tags),
        "tone": str(tone or ""),
        "starting_location": str(starting_location or ""),
        "background_expansion": bool(background_expansion),
        "runtime_capabilities": flags,
        "launch_requirements": profile.launch_requirements.as_dict(),
        "planning_contract": planning_contract_metadata(),
        "mission_signature_contract": {"schema_version": "rpg_world_mission_signature_contract_v1", "domain_ids": sorted(_MISSIONS)},
        "actor_incentive_contract": {"schema_version": "rpg_world_actor_incentive_contract_v1", "domain_ids": sorted(_ACTORS)},
    }
    network = _enabled(domain_ids, _NETWORKS) if flags.get(_NETWORK_CAPABILITY) else []
    contracts = (
        ("network_constraint_contract", network, {"schema_version": "rpg_world_network_constraint_contract_v1", "capability": _NETWORK_CAPABILITY, "required_before_launch": True}),
        ("spatial_route_contract", _enabled(domain_ids, _SPATIAL), {"schema_version": "rpg_world_spatial_route_contract_v1", "required_before_launch": True, "depth": str(depth or "standard")}),
        ("economic_scale_contract", _enabled(domain_ids, _ECONOMIC), {"schema_version": "rpg_world_economic_scale_contract_v1", "required_before_launch": True, "minimum_band_diversity": 2}),
        ("resource_dependency_contract", _enabled(domain_ids, _RESOURCES), {"schema_version": "rpg_world_resource_dependency_contract_v1", "required_before_launch": True, "minimum_resource_class_count": 2, "requires_chokepoint": True, "requires_substitute": True}),
        ("ordinary_life_contract", _enabled(domain_ids, _ORDINARY_LIFE), {"schema_version": "rpg_world_ordinary_life_contract_v1", "required_before_launch": True, "minimum_signature_diversity": 2}),
    )
    for name, enabled, values in contracts:
        if enabled:
            metadata[name] = {**values, "domain_ids": enabled}
    if "spatial_route_contract" in metadata:
        place = next(node for node in nodes if node.topic_id in _SPATIAL)
        metadata["spatial_route_contract"].update({"place_count": place.target_count, "minimum_route_count": minimum_route_count(place.target_count, depth)})
    graph = CampaignTopicGraph(
        graph_version="rpg_profile_topic_graph_v2",
        campaign_template=str(campaign_template or profile.profile_id),
        depth=str(depth or "standard"),  # type: ignore[arg-type]
        nodes=(*nodes, *_pipeline_nodes(domain_ids)),
        metadata=metadata,
    )
    issues = graph.validate()
    if issues:
        raise ValueError("invalid_profile_campaign_topic_graph:" + ",".join(issues))
    return graph


def _dependency_closure(graph: CampaignTopicGraph, selected_ids: Iterable[str]) -> set[str]:
    selected = {str(value) for value in selected_ids}
    pending = list(selected)
    node_map = graph.node_map()
    while pending:
        node = node_map.get(pending.pop())
        if node is None:
            continue
        for dependency in node.dependencies:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return selected


def build_profile_launch_topic_graph(graph: CampaignTopicGraph, profile: GenreProfile) -> CampaignTopicGraph:
    profile = augment_profile_with_causal_traceability(profile)
    required_roles = set(profile.launch_requirements.required_semantic_roles)
    selected = set(profile.launch_requirements.required_domain_ids)
    selected.update(
        domain.domain_id
        for domain in profile.domains
        if domain.required_before_launch or set(domain.semantic_roles) & required_roles
    )
    for name, contract in graph.metadata.items():
        if str(name).endswith("_contract") and isinstance(contract, Mapping) and contract.get("required_before_launch"):
            selected.update(str(value) for value in contract.get("domain_ids") or () if str(value))
    selected = _dependency_closure(graph, selected)
    selected.update(node.topic_id for node in graph.nodes if node.category in _PIPELINE_CATEGORIES)
    nodes = []
    for node in graph.topological_order():
        if node.topic_id not in selected:
            continue
        dependencies = tuple(value for value in node.dependencies if value in selected)
        if node.topic_id == "relationships":
            dependencies = tuple(domain.domain_id for domain in profile.domains if domain.domain_id in selected)
        nodes.append(replace(node, dependencies=dependencies, required_before_launch=True, metadata=dict(node.metadata)))
    projected = CampaignTopicGraph(
        graph_version=graph.graph_version,
        campaign_template=graph.campaign_template,
        depth=graph.depth,
        nodes=tuple(nodes),
        metadata={
            **dict(graph.metadata),
            "generation_tier": "launch_canon",
            "deferred_topic_ids": [node.topic_id for node in graph.topological_order() if node.topic_id not in selected and node.category not in _PIPELINE_CATEGORIES],
        },
    )
    issues = projected.validate()
    if issues:
        raise ValueError("invalid_profile_launch_topic_graph:" + ",".join(issues))
    missing = set(profile.launch_requirements.required_domain_ids) - set(projected.node_map())
    if missing:
        raise ValueError("profile_launch_topics_missing:" + ",".join(sorted(missing)))
    return projected
