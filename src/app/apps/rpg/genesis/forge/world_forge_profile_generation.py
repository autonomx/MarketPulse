"""Genre profile registry, resolution, and validated world-local generation."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from .world_forge_profiles import (
    DomainDefinition,
    DomainTargetRange,
    FieldDefinition,
    GenreProfile,
    LaunchRequirements,
    RuntimeCapabilityDefaults,
)


def normalize_genre_key(value: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold())) or "unknown"


class GenreProfileGenerator(Protocol):
    def generate_profile(
        self,
        *,
        genre: str,
        description: str,
        campaign_mode: str,
    ) -> GenreProfile: ...


@dataclass(frozen=True)
class ProfileResolution:
    profile: GenreProfile
    source: str
    requested_genre: str
    normalized_genre: str
    generated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.as_dict(),
            "source": self.source,
            "requested_genre": self.requested_genre,
            "normalized_genre": self.normalized_genre,
            "generated": self.generated,
        }


class GenreProfileRegistry:
    def __init__(self, profiles: tuple[GenreProfile, ...] = ()) -> None:
        self._profiles: dict[str, GenreProfile] = {}
        self._aliases: dict[str, str] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: GenreProfile) -> None:
        profile.require_valid()
        self._profiles[profile.profile_id] = profile
        for value in (profile.profile_id, profile.display_name, *profile.aliases):
            self._aliases[normalize_genre_key(value)] = profile.profile_id

    def resolve(self, genre: str) -> GenreProfile | None:
        profile_id = self._aliases.get(normalize_genre_key(genre))
        return self._profiles.get(profile_id or "")

    def profiles(self) -> tuple[GenreProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))


def _field(
    field_id: str,
    value_type: str = "string",
    *,
    required: bool = False,
    targets: tuple[str, ...] = (),
    semantic_role: str = "",
) -> FieldDefinition:
    return FieldDefinition(
        field_id=field_id,
        value_type=value_type,  # type: ignore[arg-type]
        required=required,
        allowed_target_domains=targets,
        semantic_role=semantic_role,
    )


def _presentation(
    page_kind: str,
    card_variant: str,
    image_role: str,
    *,
    group: str = "world",
) -> dict[str, Any]:
    return {
        "presentation": {
            "page_kind": page_kind,
            "card_variant": card_variant,
            "image_role": image_role,
            "group": group,
        }
    }


def _domain(
    domain_id: str,
    title: str,
    entity_kind: str,
    *,
    dependencies: tuple[str, ...] = (),
    fields: tuple[FieldDefinition, ...] = (),
    target_range: DomainTargetRange = DomainTargetRange(),
    required_before_launch: bool = False,
    semantic_roles: tuple[str, ...] = (),
    page_kind: str = "collection",
    card_variant: str = "entity",
    image_role: str = "illustration",
    group: str = "world",
) -> DomainDefinition:
    return DomainDefinition(
        domain_id=domain_id,
        title=title,
        entity_kind=entity_kind,
        dependencies=dependencies,
        required_before_launch=required_before_launch,
        semantic_roles=semantic_roles,
        fields=fields,
        target_range=target_range,
        generation_guidance=_presentation(
            page_kind,
            card_variant,
            image_role,
            group=group,
        ),
    )


STANDARD_DOMAIN_IDS: tuple[str, ...] = (
    "setting_rules",
    "history_timeline",
    "regions",
    "places",
    "groups",
    "cultures",
    "actors",
    "networks",
    "technology_augmentations",
    "equipment_vehicles",
    "roles_archetypes",
    "threats",
    "economy_law",
    "pressures",
    "quests",
    "encounter_seeds",
    "opening_threads",
    "opening_scenarios",
)


def _core_domains() -> tuple[DomainDefinition, ...]:
    """Return the engine-owned authoring catalogue shared by every genre."""

    return (
        _domain(
            "setting_rules",
            "World Overview and Setting Rules",
            "setting_rule",
            required_before_launch=True,
            semantic_roles=("starting_context",),
            fields=(
                _field("name", required=True),
                _field("rule", required=True),
                _field("observable_consequences", "structured_object", required=True),
            ),
            page_kind="document",
            card_variant="setting_rule",
            image_role="none",
            group="lore",
        ),
        _domain(
            "history_timeline",
            "History and Timeline",
            "historical_event",
            dependencies=("setting_rules",),
            fields=(
                _field("name", required=True),
                _field("era", required=True),
                _field("cause", required=True),
                _field("consequences", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((2, 3), (5, 8), (10, 16)),
            page_kind="document",
            card_variant="history",
            image_role="illustration",
            group="lore",
        ),
        _domain(
            "regions",
            "Regions, Districts and Zones",
            "region",
            dependencies=("setting_rules", "history_timeline"),
            fields=(
                _field("name", required=True),
                _field("identity", required=True),
                _field("boundaries", "structured_object", required=True),
                _field("current_pressure", required=True),
            ),
            target_range=DomainTargetRange((2, 4), (5, 8), (9, 14)),
            card_variant="regions",
            image_role="landscape",
        ),
        _domain(
            "places",
            "Places and Points of Interest",
            "place",
            dependencies=("regions",),
            required_before_launch=True,
            semantic_roles=("starting_context",),
            fields=(
                _field("name", required=True),
                _field("region_id", "entity_ref", required=True, targets=("regions",)),
                _field("parent_place_id", "entity_ref", targets=("places",)),
                _field("access_routes", "structured_object", required=True),
                _field("current_pressure", required=True),
                _field("observable_evidence", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((3, 5), (7, 10), (14, 22)),
            card_variant="locations",
            image_role="scene",
        ),
        _domain(
            "groups",
            "Organisations and Institutions",
            "group",
            dependencies=("setting_rules", "places"),
            required_before_launch=True,
            semantic_roles=("initial_conflict",),
            fields=(
                _field("name", required=True),
                _field("controlled_place_ids", "entity_ref_list", targets=("places",)),
                _field("resources", "structured_object", required=True),
                _field("dependencies", "structured_object", required=True),
                _field("internal_divisions", "structured_object", required=True),
                _field("current_objective", required=True),
                _field("next_action", required=True),
                _field("failure_response", required=True),
                _field("observable_signs", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((3, 4), (5, 8), (9, 14)),
            card_variant="factions",
            image_role="emblem",
        ),
        _domain(
            "cultures",
            "Cultures and Subcultures",
            "culture",
            dependencies=("history_timeline", "regions", "groups"),
            fields=(
                _field("name", required=True),
                _field("region_ids", "entity_ref_list", targets=("regions",)),
                _field("group_ids", "entity_ref_list", targets=("groups",)),
                _field("values", "structured_object", required=True),
                _field("customs", "structured_object", required=True),
                _field("internal_tensions", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((2, 3), (4, 7), (8, 12)),
            card_variant="races",
            image_role="illustration",
        ),
        _domain(
            "actors",
            "Actors and NPCs",
            "actor",
            dependencies=("groups", "places", "cultures"),
            required_before_launch=True,
            semantic_roles=("initial_actors",),
            fields=(
                _field("name", required=True),
                _field("location_id", "entity_ref", required=True, targets=("places",)),
                _field("group_ids", "entity_ref_list", targets=("groups",)),
                _field("culture_id", "entity_ref", targets=("cultures",)),
                _field("goal", required=True),
                _field("dependency", required=True),
                _field("current_pressure", required=True),
                _field("next_action", required=True),
                _field("reaction_conditions", "structured_object", required=True),
                _field("knowledge_limits", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((4, 6), (8, 12), (15, 25)),
            card_variant="npcs",
            image_role="portrait",
        ),
        _domain(
            "networks",
            "Information, Networks and Artificial Intelligences",
            "network",
            dependencies=("groups", "places", "technology_augmentations"),
            fields=(
                _field("name", required=True),
                _field("controller_group_ids", "entity_ref_list", targets=("groups",)),
                _field("access_conditions", "structured_object", required=True),
                _field("security_pressure", required=True),
                _field("observable_effects", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((2, 3), (4, 7), (8, 12)),
            card_variant="networks",
            image_role="illustration",
        ),
        _domain(
            "technology_augmentations",
            "Technology, Powers and Augmentations",
            "technology",
            dependencies=("setting_rules", "groups"),
            fields=(
                _field("name", required=True),
                _field("source_group_ids", "entity_ref_list", targets=("groups",)),
                _field("capability", required=True),
                _field("cost", required=True),
                _field("dependency", required=True),
                _field("failure_mode", required=True),
            ),
            target_range=DomainTargetRange((3, 5), (6, 10), (12, 18)),
            card_variant="items",
            image_role="illustration",
        ),
        _domain(
            "equipment_vehicles",
            "Weapons, Equipment, Vehicles and Commodities",
            "equipment",
            dependencies=("technology_augmentations", "groups"),
            fields=(
                _field("name", required=True),
                _field("producer_group_ids", "entity_ref_list", targets=("groups",)),
                _field("function", required=True),
                _field("availability", required=True),
                _field("cost", required=True),
                _field("limitations", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((4, 6), (8, 14), (16, 26)),
            card_variant="items",
            image_role="icon",
        ),
        _domain(
            "roles_archetypes",
            "Roles, Archetypes, Skills and Talents",
            "role_archetype",
            dependencies=("cultures", "groups", "technology_augmentations"),
            fields=(
                _field("name", required=True),
                _field("group_ids", "entity_ref_list", targets=("groups",)),
                _field("capabilities", "structured_object", required=True),
                _field("progression", "structured_object", required=True),
                _field("equipment_ids", "entity_ref_list", targets=("equipment_vehicles",)),
            ),
            target_range=DomainTargetRange((3, 4), (5, 8), (9, 14)),
            card_variant="classes",
            image_role="illustration",
        ),
        _domain(
            "threats",
            "Threats and Hostile Entities",
            "threat",
            dependencies=("places", "groups", "technology_augmentations"),
            fields=(
                _field("name", required=True),
                _field("place_ids", "entity_ref_list", targets=("places",)),
                _field("group_ids", "entity_ref_list", targets=("groups",)),
                _field("capabilities", "structured_object", required=True),
                _field("behaviour", required=True),
                _field("weaknesses", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((3, 5), (6, 10), (12, 18)),
            card_variant="monsters",
            image_role="portrait",
        ),
        _domain(
            "economy_law",
            "Economy, Services, Laws and Control Systems",
            "world_system",
            dependencies=("groups", "places", "technology_augmentations"),
            fields=(
                _field("name", required=True),
                _field("controller_group_ids", "entity_ref_list", targets=("groups",)),
                _field("affected_place_ids", "entity_ref_list", targets=("places",)),
                _field("rules", "structured_object", required=True),
                _field("services", "structured_object", required=True),
                _field("failure_effect", required=True),
            ),
            target_range=DomainTargetRange((2, 3), (4, 7), (8, 12)),
            card_variant="economy_law",
            image_role="illustration",
            group="lore",
        ),
        _domain(
            "pressures",
            "Current Conflicts and Pressures",
            "pressure",
            dependencies=("groups", "actors", "places", "economy_law"),
            required_before_launch=True,
            semantic_roles=("initial_conflict",),
            fields=(
                _field("name", required=True),
                _field("actor_ids", "entity_ref_list", targets=("actors",)),
                _field("group_ids", "entity_ref_list", targets=("groups",)),
                _field("place_ids", "entity_ref_list", targets=("places",)),
                _field("current_state", required=True),
                _field("next_tick_change", required=True),
                _field("escalation_condition", required=True),
                _field("observable_evidence", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((3, 4), (5, 8), (9, 14)),
            card_variant="pressures",
            image_role="scene",
            group="lore",
        ),
        _domain(
            "quests",
            "Quests and Missions",
            "quest",
            dependencies=("pressures", "actors", "places", "groups"),
            fields=(
                _field("name", required=True),
                _field("giver_id", "entity_ref", targets=("actors",)),
                _field("location_ids", "entity_ref_list", targets=("places",)),
                _field("group_ids", "entity_ref_list", targets=("groups",)),
                _field("objectives", "structured_object", required=True),
                _field("stakes", required=True),
                _field("rewards", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((3, 5), (6, 10), (12, 18)),
            card_variant="quests",
            image_role="cover",
            group="game-master",
        ),
        _domain(
            "encounter_seeds",
            "Encounter Seeds",
            "encounter_seed",
            dependencies=("quests", "threats", "actors", "places"),
            fields=(
                _field("name", required=True),
                _field("place_ids", "entity_ref_list", targets=("places",)),
                _field("actor_ids", "entity_ref_list", targets=("actors",)),
                _field("threat_ids", "entity_ref_list", targets=("threats",)),
                _field("setup", required=True),
                _field("complications", "structured_object", required=True),
                _field("outcomes", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((3, 5), (6, 10), (12, 18)),
            card_variant="encounter_seeds",
            image_role="scene",
            group="game-master",
        ),
        _domain(
            "opening_threads",
            "Opening Threads",
            "opening_thread",
            dependencies=("pressures", "actors", "places", "groups"),
            required_before_launch=True,
            fields=(
                _field("name", required=True),
                _field("actor_ids", "entity_ref_list", targets=("actors",)),
                _field("place_ids", "entity_ref_list", targets=("places",)),
                _field("pressure_ids", "entity_ref_list", targets=("pressures",)),
                _field("initial_evidence", "structured_object", required=True),
                _field("player_choices", "structured_object", required=True),
                _field("aftermath", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((2, 3), (3, 5), (5, 8)),
            card_variant="opening_threads",
            image_role="scene",
            group="game-master",
        ),
        _domain(
            "opening_scenarios",
            "Opening Scenarios and One-Shots",
            "opening_scenario",
            dependencies=("opening_threads", "encounter_seeds", "roles_archetypes"),
            fields=(
                _field("name", required=True),
                _field("starting_place_id", "entity_ref", required=True, targets=("places",)),
                _field("initial_actor_ids", "entity_ref_list", targets=("actors",)),
                _field("opening_thread_ids", "entity_ref_list", targets=("opening_threads",)),
                _field("premise", required=True),
                _field("beats", "structured_object", required=True),
                _field("starting_resources", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((1, 2), (2, 4), (4, 6)),
            card_variant="opening_scenarios",
            image_role="cover",
            group="game-master",
        ),
    )


def _flavour(
    domains: tuple[DomainDefinition, ...],
    titles: Mapping[str, str],
    kinds: Mapping[str, str] | None = None,
) -> tuple[DomainDefinition, ...]:
    kind_overrides = dict(kinds or {})
    return tuple(
        replace(
            domain,
            title=titles.get(domain.domain_id, domain.title),
            entity_kind=kind_overrides.get(domain.domain_id, domain.entity_kind),
        )
        for domain in domains
    )


def _profile(
    profile_id: str,
    display_name: str,
    *,
    aliases: tuple[str, ...],
    titles: Mapping[str, str] | None = None,
    kinds: Mapping[str, str] | None = None,
    tags: tuple[str, ...] = (),
    capability_defaults: Mapping[str, bool] | None = None,
) -> GenreProfile:
    domains = _flavour(_core_domains(), dict(titles or {}), kinds)
    return GenreProfile(
        profile_id=profile_id,
        version=2,
        display_name=display_name,
        aliases=aliases,
        domains=domains,
        genre_tags=tags,
        launch_requirements=LaunchRequirements(
            required_domain_ids=(
                "setting_rules",
                "places",
                "groups",
                "actors",
                "pressures",
                "opening_threads",
            ),
        ),
        runtime_capability_defaults=RuntimeCapabilityDefaults(
            dict(capability_defaults or {})
        ),
        provenance={
            "source": "built_in_profile_v2",
            "standard_catalogue": list(STANDARD_DOMAIN_IDS),
        },
        scope="built_in",
    ).require_valid()


def _post_apocalyptic_profile() -> GenreProfile:
    return _profile(
        "post_apocalyptic",
        "Post-apocalyptic",
        aliases=("post apocalypse", "wasteland", "nuclear wasteland", "fallout style"),
        titles={
            "regions": "Wasteland Regions, Territories and Hazard Zones",
            "places": "Settlements, Vaults, Ruins and Points of Interest",
            "groups": "Factions, Settlements and Raider Clans",
            "cultures": "Wasteland Cultures and Survivor Communities",
            "networks": "Radio, Terminals and Artificial Intelligences",
            "technology_augmentations": "Salvaged Technology, Mutations and Chems",
            "equipment_vehicles": "Weapons, Scrap Equipment, Vehicles and Supplies",
            "roles_archetypes": "Wasteland Roles, Skills and Perks",
            "threats": "Mutants, Robots, Raiders and Wasteland Creatures",
            "economy_law": "Barter, Services, Settlement Laws and Control",
            "quests": "Jobs, Expeditions and Settlement Problems",
        },
        tags=("scarcity", "survival", "collapse"),
        capability_defaults={"scarcity": True, "resource_simulation": True},
    )


def _cyberpunk_profile() -> GenreProfile:
    return _profile(
        "cyberpunk",
        "Cyberpunk",
        aliases=("cyber punk", "corporate dystopia", "high tech low life"),
        titles={
            "regions": "Megacities, Districts and Corporate Zones",
            "places": "Places, Facilities and Points of Interest",
            "groups": "Corporations, Gangs, Governments and Institutions",
            "cultures": "Cultures and Subcultures",
            "actors": "Actors and NPCs",
            "networks": "Networks, Virtual Spaces and Artificial Intelligences",
            "technology_augmentations": "Technology and Augmentations",
            "equipment_vehicles": "Weapons, Equipment, Vehicles and Commodities",
            "roles_archetypes": "Roles, Archetypes, Skills and Talents",
            "threats": "Threats, Drones, Security Forces and Engineered Creatures",
            "economy_law": "Economy, Services, Laws and Surveillance",
            "pressures": "Current Conflicts and Pressures",
            "quests": "Quests, Jobs and Contracts",
            "opening_scenarios": "Opening Scenarios and One-Shots",
        },
        kinds={
            "technology_augmentations": "augmentation",
            "equipment_vehicles": "equipment",
            "threats": "cyberpunk_threat",
        },
        tags=("corporations", "augmentation", "networks"),
        capability_defaults={"digital_spaces": True, "economy": True},
    )


def _fantasy_profile() -> GenreProfile:
    return _profile(
        "classic_fantasy",
        "Classic fantasy",
        aliases=("fantasy", "high fantasy", "medieval fantasy"),
        titles={
            "regions": "Realms, Regions and Wild Frontiers",
            "places": "Settlements, Dungeons and Points of Interest",
            "groups": "Kingdoms, Guilds, Orders and Institutions",
            "cultures": "Cultures, Peoples and Traditions",
            "networks": "Communication, Divination and Otherworldly Spaces",
            "technology_augmentations": "Magic, Relics, Rituals and Transformations",
            "equipment_vehicles": "Weapons, Equipment, Mounts and Commodities",
            "roles_archetypes": "Classes, Professions, Skills and Talents",
            "threats": "Monsters, Undead, Armies and Supernatural Threats",
            "economy_law": "Markets, Services, Laws and Feudal Authority",
            "quests": "Quests, Bounties and Adventures",
        },
        kinds={
            "technology_augmentations": "magic_system",
            "roles_archetypes": "class",
            "threats": "monster",
        },
        tags=("fantasy", "supernatural"),
        capability_defaults={"supernatural_rules": True},
    )


def default_profile_registry() -> GenreProfileRegistry:
    return GenreProfileRegistry(
        (_fantasy_profile(), _post_apocalyptic_profile(), _cyberpunk_profile())
    )


class HeuristicWorldLocalProfileGenerator:
    """Safe fallback when no model-backed profile generator is configured."""

    def generate_profile(
        self,
        *,
        genre: str,
        description: str,
        campaign_mode: str,
    ) -> GenreProfile:
        normalized = normalize_genre_key(genre)
        concept_domain = DomainDefinition(
            "genre_elements",
            f"{genre.strip() or 'Unknown Genre'} Elements",
            "genre_element",
            dependencies=("setting_rules", "places", "groups"),
            fields=(
                _field("name", required=True),
                _field("function_in_setting", required=True),
                _field("dependency", required=True),
                _field("current_pressure", required=True),
                _field("observable_evidence", "structured_object", required=True),
            ),
            target_range=DomainTargetRange((3, 4), (5, 8), (9, 14)),
            generation_guidance={
                **_presentation("collection", "entity", "illustration"),
                "requested_genre": genre,
                "description": description,
                "campaign_mode": campaign_mode,
                "instruction": "Generate setting-specific concepts; do not import unrelated genre defaults.",
            },
        )
        return GenreProfile(
            profile_id=f"world_local:{normalized}",
            version=2,
            display_name=genre.strip() or "Unknown genre",
            aliases=(genre,),
            domains=(*_core_domains(), concept_domain),
            genre_tags=(normalized,),
            launch_requirements=LaunchRequirements(
                required_domain_ids=(
                    "setting_rules",
                    "places",
                    "groups",
                    "actors",
                    "pressures",
                    "opening_threads",
                ),
            ),
            provenance={
                "source": "world_local_profile_generator_v2",
                "requested_description": description,
                "campaign_mode": campaign_mode,
                "standard_catalogue": list(STANDARD_DOMAIN_IDS),
            },
            scope="world_local",
        ).require_valid()


def resolve_or_generate_genre_profile(
    *,
    genre: str,
    description: str = "",
    campaign_mode: str = "persistent_living_world",
    registry: GenreProfileRegistry | None = None,
    generator: GenreProfileGenerator | None = None,
) -> ProfileResolution:
    resolved_registry = registry or default_profile_registry()
    existing = resolved_registry.resolve(genre)
    if existing is not None:
        return ProfileResolution(
            profile=existing,
            source="registry",
            requested_genre=genre,
            normalized_genre=normalize_genre_key(genre),
            generated=False,
        )
    generated = (generator or HeuristicWorldLocalProfileGenerator()).generate_profile(
        genre=genre,
        description=description,
        campaign_mode=campaign_mode,
    ).require_valid()
    if generated.scope != "world_local":
        generated = replace(generated, scope="world_local")
    return ProfileResolution(
        profile=generated,
        source="generated_world_local",
        requested_genre=genre,
        normalized_genre=normalize_genre_key(genre),
        generated=True,
    )
