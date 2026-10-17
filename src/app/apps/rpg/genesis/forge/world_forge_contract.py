"""Campaign Genesis World Forge contracts and dependency-graph planner.

The graph is deliberately prose-free. It declares which campaign topics must be
created, their dependencies, which generator role owns them, and whether the
first playable turn is allowed before each topic is complete.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, cast


WorldGenerationDepth = Literal["quick", "standard", "epic"]


@dataclass(frozen=True)
class WorldForgeDepthProfile:
    depth: WorldGenerationDepth
    lore_page_range: tuple[int, int]
    major_npc_range: tuple[int, int]
    location_range: tuple[int, int]
    faction_range: tuple[int, int]
    max_parallel_jobs: int
    background_expansion_allowed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth,
            "lore_page_range": list(self.lore_page_range),
            "major_npc_range": list(self.major_npc_range),
            "location_range": list(self.location_range),
            "faction_range": list(self.faction_range),
            "max_parallel_jobs": self.max_parallel_jobs,
            "background_expansion_allowed": self.background_expansion_allowed,
        }


WORLD_FORGE_DEPTH_PROFILES: dict[WorldGenerationDepth, WorldForgeDepthProfile] = {
    "quick": WorldForgeDepthProfile(
        depth="quick",
        lore_page_range=(12, 20),
        major_npc_range=(4, 6),
        location_range=(5, 8),
        faction_range=(3, 4),
        max_parallel_jobs=3,
        background_expansion_allowed=True,
    ),
    "standard": WorldForgeDepthProfile(
        depth="standard",
        lore_page_range=(30, 50),
        major_npc_range=(8, 12),
        location_range=(10, 16),
        faction_range=(5, 8),
        max_parallel_jobs=4,
        background_expansion_allowed=True,
    ),
    "epic": WorldForgeDepthProfile(
        depth="epic",
        lore_page_range=(70, 120),
        major_npc_range=(15, 25),
        location_range=(20, 35),
        faction_range=(8, 14),
        max_parallel_jobs=4,
        background_expansion_allowed=False,
    ),
}


def world_forge_depth_profile(depth: str | None) -> WorldForgeDepthProfile:
    normalized = str(depth or "standard").strip().casefold()
    return WORLD_FORGE_DEPTH_PROFILES.get(cast(Any, normalized), WORLD_FORGE_DEPTH_PROFILES["standard"])


@dataclass(frozen=True)
class CampaignTopicNode:
    topic_id: str
    title: str
    category: str
    dependencies: tuple[str, ...] = ()
    generator_role: str = "world_forge"
    required_before_launch: bool = True
    visibility: str = "game_master_canon"
    target_count: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "category": self.category,
            "dependencies": list(self.dependencies),
            "generator_role": self.generator_role,
            "required_before_launch": self.required_before_launch,
            "visibility": self.visibility,
            "target_count": self.target_count,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CampaignTopicGraph:
    graph_version: str
    campaign_template: str
    depth: WorldGenerationDepth
    nodes: tuple[CampaignTopicNode, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def node_map(self) -> dict[str, CampaignTopicNode]:
        return {node.topic_id: node for node in self.nodes}

    def validate(self) -> tuple[str, ...]:
        node_map = self.node_map()
        issues: list[str] = []
        if len(node_map) != len(self.nodes):
            issues.append("duplicate_topic_id")
        for node in self.nodes:
            for dependency in node.dependencies:
                if dependency not in node_map:
                    issues.append(f"unknown_dependency:{node.topic_id}:{dependency}")
        try:
            self.topological_order()
        except ValueError:
            issues.append("dependency_cycle")
        return tuple(dict.fromkeys(issues))

    def topological_order(self) -> tuple[CampaignTopicNode, ...]:
        node_map = self.node_map()
        pending = {node.topic_id: set(node.dependencies) for node in self.nodes}
        ordered: list[CampaignTopicNode] = []
        while pending:
            ready = sorted(topic_id for topic_id, dependencies in pending.items() if not dependencies)
            if not ready:
                raise ValueError("campaign topic graph contains a dependency cycle")
            for topic_id in ready:
                ordered.append(node_map[topic_id])
                pending.pop(topic_id)
            for dependencies in pending.values():
                dependencies.difference_update(ready)
        return tuple(ordered)

    def launch_required_topic_ids(self) -> tuple[str, ...]:
        return tuple(node.topic_id for node in self.topological_order() if node.required_before_launch)

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph_version": self.graph_version,
            "campaign_template": self.campaign_template,
            "depth": self.depth,
            "nodes": [node.as_dict() for node in self.nodes],
            "launch_required_topic_ids": list(self.launch_required_topic_ids()),
            "metadata": dict(self.metadata),
        }


_GRAPH_VERSION = "rpg_campaign_topic_graph_v2"
_LAUNCH_GENERATION_TOPIC_IDS = {
    "realm",
    "regions",
    "factions",
    "current_conflicts",
    "hero_system",
    "locations",
    "npcs",
    "opening_threads",
}
_PIPELINE_TOPIC_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}


def _target(profile: WorldForgeDepthProfile, category: str) -> int:
    ranges = {
        "regions": profile.location_range,
        "locations": profile.location_range,
        "points_of_interest": profile.location_range,
        "factions": profile.faction_range,
        "races": profile.faction_range,
        "classes": profile.faction_range,
        "npcs": profile.major_npc_range,
        "monsters": profile.major_npc_range,
        "items": profile.major_npc_range,
        "spells": profile.major_npc_range,
        "feats": profile.major_npc_range,
        "quests": profile.faction_range,
        "lore": profile.lore_page_range,
    }
    lower, upper = ranges.get(category, (1, 1))
    return lower if profile.depth == "quick" else round((lower + upper) / 2)


def _scenario_target(profile: WorldForgeDepthProfile, quick: int, standard: int, epic: int) -> int:
    return {"quick": quick, "standard": standard, "epic": epic}[profile.depth]


def _node(
    topic_id: str,
    title: str,
    category: str,
    dependencies: Iterable[str] = (),
    *,
    role: str,
    required: bool = True,
    target_count: int = 1,
    visibility: str = "game_master_canon",
    **metadata: Any,
) -> CampaignTopicNode:
    return CampaignTopicNode(
        topic_id=topic_id,
        title=title,
        category=category,
        dependencies=tuple(dependencies),
        generator_role=role,
        required_before_launch=required,
        visibility=visibility,
        target_count=max(1, int(target_count)),
        metadata=metadata,
    )


def _domain_node(
    topic_id: str,
    title: str,
    dependencies: Iterable[str],
    *,
    role: str,
    target_count: int,
    required: bool,
    visibility: str = "partially_known",
    required_fields: Iterable[str],
) -> CampaignTopicNode:
    return _node(
        topic_id,
        title,
        topic_id,
        dependencies,
        role=role,
        required=required,
        target_count=target_count,
        visibility=visibility,
        entity_kind=topic_id.rstrip("s"),
        required_entity_fields=list(required_fields),
        schema_version=f"rpg_world_{topic_id}_v1",
    )


def build_campaign_topic_graph(
    *,
    campaign_template: str,
    genre: str | None,
    tone: str,
    depth: str | None = "standard",
    starting_location: str = "",
    background_expansion: bool = False,
) -> CampaignTopicGraph:
    """Create a deterministic prose-free dependency graph for campaign genesis."""

    profile = world_forge_depth_profile(depth)
    allow_deferred = bool(background_expansion and profile.background_expansion_allowed)
    optional_required = not allow_deferred
    nodes = (
        _node("realm", "Realm Overview", "lore", role="realm_architect", target_count=1, visibility="public"),
        _node("cosmology", "Cosmology and World Laws", "lore", ("realm",), role="metaphysics_architect", visibility="public"),
        _node("magic_technology", "Magic and Technology", "lore", ("cosmology",), role="systems_architect", visibility="public"),
        _node("history", "History", "lore", ("realm", "cosmology"), role="historian", visibility="public"),
        _node("calendar", "Calendar and Eras", "lore", ("history",), role="historian", visibility="public"),
        _node(
            "regions", "Regions and Geography", "regions", ("realm", "history"),
            role="geography_architect", target_count=_target(profile, "regions"), visibility="public",
        ),
        _node("cultures", "Cultures and Peoples", "lore", ("regions", "history"), role="culture_architect", required=optional_required, visibility="public"),
        _domain_node(
            "races", "Races and Ancestries", ("cultures", "regions", "history", "cosmology"),
            role="ancestry_architect", target_count=_target(profile, "races"), required=optional_required,
            visibility="public", required_fields=("name", "homelands", "cultures", "traits", "languages"),
        ),
        _node(
            "factions", "Factions and Powers", "factions", ("regions", "history", "cultures"),
            role="faction_architect", target_count=_target(profile, "factions"), visibility="partially_known",
        ),
        _node("institutions", "Institutions", "lore", ("factions", "cultures"), role="institution_architect", visibility="public"),
        _node("pantheon", "Religions and Pantheon", "lore", ("cosmology", "cultures"), role="religion_architect", required=optional_required, visibility="partially_known"),
        _node("hero_system", "Heroes, Summoning, and Exceptional Powers", "lore", ("cosmology", "magic_technology", "institutions"), role="hero_system_architect", visibility="public"),
        _domain_node(
            "classes", "Classes and Disciplines", ("hero_system", "cultures", "institutions", "magic_technology"),
            role="class_architect", target_count=_target(profile, "classes"), required=optional_required,
            visibility="public", required_fields=("name", "capabilities", "progression", "equipment", "institution_ids"),
        ),
        _domain_node(
            "spells", "Spells and Rituals", ("magic_technology", "cosmology", "institutions"),
            role="spell_architect", target_count=_target(profile, "spells"), required=optional_required,
            visibility="partially_known", required_fields=("name", "school", "tier", "costs", "effects", "range"),
        ),
        _domain_node(
            "feats", "Feats and Talents", ("classes", "hero_system", "cultures"),
            role="feat_architect", target_count=_target(profile, "feats"), required=optional_required,
            visibility="public", required_fields=("name", "prerequisites", "benefits", "limitations"),
        ),
        _node("current_conflicts", "Current Conflicts", "lore", ("factions", "institutions", "regions"), role="conflict_architect", visibility="partially_known"),
        _node(
            "locations", "Major Locations", "locations", ("regions", "factions", "current_conflicts"),
            role="location_dossier_generator", target_count=_target(profile, "locations"), visibility="partially_known",
            starting_location=starting_location,
        ),
        _domain_node(
            "points_of_interest", "Points of Interest", ("locations", "regions", "history", "current_conflicts"),
            role="point_of_interest_architect", target_count=_target(profile, "points_of_interest"), required=optional_required,
            required_fields=("name", "location_id", "region_id", "purpose", "hooks", "sensory_profile"),
        ),
        _domain_node(
            "monsters", "Monsters and Creatures", ("regions", "cosmology", "magic_technology"),
            role="creature_architect", target_count=_target(profile, "monsters"), required=optional_required,
            required_fields=("name", "region_ids", "habitats", "threat_level", "abilities", "weaknesses"),
        ),
        _domain_node(
            "items", "Items and Relics", ("magic_technology", "cultures", "factions", "locations"),
            role="item_architect", target_count=_target(profile, "items"), required=optional_required,
            required_fields=("name", "item_type", "rarity", "value", "effects", "origin_ids"),
        ),
        _node(
            "npcs", "Central NPC Cast", "npcs", ("factions", "institutions", "locations", "current_conflicts"),
            role="npc_dossier_generator", target_count=_target(profile, "npcs"), visibility="game_master_canon",
        ),
        _domain_node(
            "quests", "Quest Catalog", ("current_conflicts", "npcs", "locations", "factions", "points_of_interest"),
            role="quest_architect", target_count=_target(profile, "quests"), required=optional_required,
            visibility="game_master_canon", required_fields=("name", "giver_id", "location_ids", "faction_ids", "objectives", "rewards", "stakes"),
        ),
        _node("opening_threads", "Opening Story Threads", "story", ("npcs", "locations", "current_conflicts"), role="story_thread_architect", visibility="game_master_canon"),
        _domain_node(
            "encounter_seeds", "Encounter Seeds", ("monsters", "npcs", "locations", "current_conflicts"),
            role="encounter_architect", target_count=_scenario_target(profile, 4, 8, 12), required=optional_required,
            visibility="game_master_canon", required_fields=("name", "location_ids", "actor_ids", "threat_ids", "setup", "complications", "outcomes"),
        ),
        _domain_node(
            "one_shots", "One-Shot Adventures", ("quests", "opening_threads", "npcs", "locations", "encounter_seeds"),
            role="one_shot_architect", target_count=_scenario_target(profile, 2, 4, 8), required=optional_required,
            visibility="game_master_canon", required_fields=("name", "premise", "location_ids", "actor_ids", "quest_ids", "beats", "rewards"),
        ),
        _domain_node(
            "opening_scenarios", "Opening Scenarios", ("opening_threads", "quests", "npcs", "locations", "one_shots"),
            role="opening_scenario_architect", target_count=_scenario_target(profile, 2, 3, 5), required=optional_required,
            visibility="game_master_canon", required_fields=("name", "starting_location_id", "initial_npc_ids", "opening_seed_ids", "starting_resources", "premise"),
        ),
        _node(
            "relationships", "Cross-domain Relationships", "compiler",
            ("regions", "races", "factions", "institutions", "classes", "spells", "feats", "locations", "points_of_interest", "monsters", "items", "npcs", "quests", "encounter_seeds", "one_shots", "opening_scenarios"),
            role="relationship_compiler",
        ),
        _node("consistency_audit", "Canon Consistency Audit", "audit", ("calendar", "relationships", "opening_threads", "hero_system", "pantheon"), role="canon_critic"),
        _node("canon_compile", "Canon Compilation", "compiler", ("consistency_audit",), role="canon_compiler"),
        _node("retrieval_index", "Lore Retrieval Index", "index", ("canon_compile",), role="retrieval_index_compiler"),
        _node("opening_materialization", "Opening Scene Materialization", "bootstrap", ("canon_compile", "retrieval_index"), role="campaign_materializer"),
    )
    graph = CampaignTopicGraph(
        graph_version=_GRAPH_VERSION,
        campaign_template=str(campaign_template or "classic_fantasy"),
        depth=profile.depth,
        nodes=nodes,
        metadata={
            "genre": str(genre or campaign_template or "classic_fantasy"),
            "tone": str(tone or "heroic adventure"),
            "background_expansion": allow_deferred,
            "depth_profile": profile.as_dict(),
            "domain_schema_version": "rpg_world_structured_domains_v1",
        },
    )
    issues = graph.validate()
    if issues:
        raise ValueError("invalid campaign topic graph: " + ",".join(issues))
    return graph


_build_legacy_campaign_topic_graph = build_campaign_topic_graph


def build_campaign_topic_graph(  # type: ignore[no-redef]  # wraps the definition above
    *,
    campaign_template: str,
    genre: str | None,
    tone: str,
    depth: str | None = "standard",
    starting_location: str = "",
    background_expansion: bool = False,
) -> CampaignTopicGraph:
    """Use genre profiles for non-fantasy fallbacks while preserving fantasy parity."""

    requested_genre = str(genre or campaign_template or "classic_fantasy")
    from .world_forge_profile_generation import (
        normalize_genre_key,
        resolve_or_generate_genre_profile,
    )

    normalized = normalize_genre_key(requested_genre)
    if "fantasy" in normalized or normalized in {
        "summoned_heroes",
        "classic_fantasy",
        "high_fantasy",
        "medieval_fantasy",
    }:
        return _build_legacy_campaign_topic_graph(
            campaign_template=campaign_template,
            genre=genre,
            tone=tone,
            depth=depth,
            starting_location=starting_location,
            background_expansion=background_expansion,
        )

    from .world_forge_profile_graph import build_profile_topic_graph

    resolution = resolve_or_generate_genre_profile(
        genre=requested_genre,
        campaign_mode="persistent_living_world",
    )
    return build_profile_topic_graph(
        resolution.profile,
        campaign_template=str(campaign_template or resolution.profile.profile_id),
        depth=str(depth or "standard"),
        tone=tone,
        starting_location=starting_location,
        background_expansion=background_expansion,
    )
