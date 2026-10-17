"""Structured provider adapter for generating validated world-local genre profiles."""
from __future__ import annotations

import json
from dataclasses import replace
from contextlib import nullcontext
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.providers.base import BaseProvider, ChatMessage
from app.persistence.device_permits import device_permit_slot
from app.providers.registry import get_provider
from app.providers.structured import (
    StructuredContract,
    StructuredOutputGateway,
    StructuredRetryBudget,
)
from app.apps.rpg.genesis.forge.world_forge_profile_generation import (
    GenreProfileGenerator,
    HeuristicWorldLocalProfileGenerator,
    _core_domains,
    normalize_genre_key,
)
from app.apps.rpg.genesis.forge.world_forge_profiles import (
    DomainDefinition,
    DomainTargetRange,
    FieldDefinition,
    GenreProfile,
    LaunchRequirements,
    RuntimeCapabilityDefaults,
)
from app.apps.rpg.genesis.worlds.providers.world_forge import WorldForgeProviderConfig

_CORE_DOMAINS = _core_domains()
_ALLOWED_CORE_IDS = tuple(domain.domain_id for domain in _CORE_DOMAINS)
_LAUNCH_CORE_IDS = tuple(domain.domain_id for domain in _CORE_DOMAINS if domain.required_before_launch)
FieldValueType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "enum",
    "entity_ref",
    "entity_ref_list",
    "structured_object",
]
PageKind = Literal["document", "collection"]
ImageRole = Literal[
    "none",
    "portrait",
    "scene",
    "landscape",
    "emblem",
    "icon",
    "illustration",
    "cover",
    "map",
]
AuthoringGroup = Literal["world", "lore", "game-master"]


class ProfileTargetRangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quick: tuple[int, int] = (1, 2)
    standard: tuple[int, int] = (2, 4)
    epic: tuple[int, int] = (4, 8)


class ProfileFieldResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str = Field(min_length=1)
    value_type: FieldValueType = "string"
    required: bool = False
    semantic_role: str = ""
    allowed_target_domains: list[str] = Field(default_factory=list)
    enum_values: list[str] = Field(default_factory=list)
    description: str = ""


class ProfileDomainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    entity_kind: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    generator_role: str = "world_forge"
    visibility_default: str = "game_master_canon"
    required_before_launch: bool = False
    semantic_roles: list[str] = Field(default_factory=list)
    page_kind: PageKind = "collection"
    card_variant: str = "entity"
    image_role: ImageRole = "illustration"
    authoring_group: AuthoringGroup = "world"
    fields: list[ProfileFieldResponse] = Field(default_factory=list)
    target_range: ProfileTargetRangeResponse = Field(
        default_factory=ProfileTargetRangeResponse
    )
    generation_guidance: dict[str, Any] = Field(default_factory=dict)


class GenreProfileProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    genre_tags: list[str] = Field(default_factory=list)
    # The standard catalogue is always supplied by the engine.  A provider may
    # correctly conclude that the requested genre needs no additional domains,
    # so extensions are optional (and the prompt explicitly permits zero).
    domains: list[ProfileDomainResponse] = Field(default_factory=list, max_length=8)
    runtime_capability_defaults: dict[str, bool] = Field(default_factory=dict)
    rationale: str = Field(min_length=1)


def _valid_range(value: tuple[int, int]) -> bool:
    return len(value) == 2 and 1 <= value[0] <= value[1] <= 50


def profile_from_proposal(
    proposal: GenreProfileProposalResponse,
    *,
    genre: str,
    description: str,
    campaign_mode: str,
    provenance: Mapping[str, Any] | None = None,
) -> GenreProfile:
    """Compile a provider proposal into an engine-owned, validated profile."""

    core_domains = _core_domains()
    core_ids = {domain.domain_id for domain in core_domains}
    proposed_ids = [domain.domain_id for domain in proposal.domains]
    if len(set(proposed_ids)) != len(proposed_ids):
        raise ValueError("genre_profile_proposal_duplicate_domain_id")
    collisions = sorted(core_ids.intersection(proposed_ids))
    if collisions:
        raise ValueError(
            "genre_profile_proposal_redefines_core_domain:" + ",".join(collisions)
        )

    known_ids = core_ids.union(proposed_ids)
    domains: list[DomainDefinition] = []
    for domain in proposal.domains:
        if len(domain.fields) > 12:
            raise ValueError(
                f"genre_profile_proposal_too_many_fields:{domain.domain_id}"
            )
        for target_range in (
            domain.target_range.quick,
            domain.target_range.standard,
            domain.target_range.epic,
        ):
            if not _valid_range(target_range):
                raise ValueError(
                    f"genre_profile_proposal_invalid_target_range:{domain.domain_id}"
                )
        unknown_dependencies = sorted(set(domain.dependencies).difference(known_ids))
        if unknown_dependencies:
            raise ValueError(
                f"genre_profile_proposal_unknown_dependency:{domain.domain_id}:"
                + ",".join(unknown_dependencies)
            )
        fields: list[FieldDefinition] = []
        field_ids: set[str] = set()
        for field in domain.fields:
            if field.field_id in field_ids:
                raise ValueError(
                    f"genre_profile_proposal_duplicate_field:{domain.domain_id}.{field.field_id}"
                )
            field_ids.add(field.field_id)
            unknown_targets = sorted(
                set(field.allowed_target_domains).difference(known_ids)
            )
            if unknown_targets:
                raise ValueError(
                    f"genre_profile_proposal_unknown_reference_target:"
                    f"{domain.domain_id}.{field.field_id}:"
                    + ",".join(unknown_targets)
                )
            fields.append(
                FieldDefinition(
                    field_id=field.field_id,
                    value_type=field.value_type,
                    required=field.required,
                    semantic_role=field.semantic_role,
                    allowed_target_domains=tuple(field.allowed_target_domains),
                    enum_values=tuple(field.enum_values),
                    description=field.description,
                )
            )
        domains.append(
            DomainDefinition(
                domain_id=domain.domain_id,
                title=domain.title,
                entity_kind=domain.entity_kind,
                dependencies=tuple(domain.dependencies),
                generator_role=domain.generator_role,
                visibility_default=domain.visibility_default,
                required_before_launch=domain.required_before_launch,
                semantic_roles=tuple(domain.semantic_roles),
                fields=tuple(fields),
                target_range=DomainTargetRange(
                    quick=domain.target_range.quick,
                    standard=domain.target_range.standard,
                    epic=domain.target_range.epic,
                ),
                generation_guidance={
                    **domain.generation_guidance,
                    "presentation": {
                        "page_kind": domain.page_kind,
                        "card_variant": domain.card_variant or domain.entity_kind,
                        "image_role": domain.image_role,
                        "group": domain.authoring_group,
                    },
                    "requested_genre": genre,
                    "world_description": description,
                    "campaign_mode": campaign_mode,
                },
            )
        )

    return GenreProfile(
        profile_id=f"world_local:{normalize_genre_key(genre)}",
        version=2,
        display_name=proposal.display_name,
        aliases=tuple(dict.fromkeys((genre, *proposal.aliases))),
        domains=(*core_domains, *domains),
        genre_tags=tuple(dict.fromkeys(proposal.genre_tags)),
        launch_requirements=LaunchRequirements(
            required_domain_ids=_LAUNCH_CORE_IDS,
        ),
        runtime_capability_defaults=RuntimeCapabilityDefaults(
            proposal.runtime_capability_defaults
        ),
        provenance={
            "source": "llm_world_local_profile_v2",
            "requested_genre": genre,
            "requested_description": description,
            "campaign_mode": campaign_mode,
            "rationale": proposal.rationale,
            **dict(provenance or {}),
        },
        scope="world_local",
    ).require_valid()


def _proposal_contract(
    *, genre: str, description: str, campaign_mode: str
) -> StructuredContract[GenreProfileProposalResponse]:
    def validate(value: GenreProfileProposalResponse) -> None:
        profile_from_proposal(
            value,
            genre=genre,
            description=description,
            campaign_mode=campaign_mode,
        )

    return StructuredContract(
        contract_id="rpg.world_forge.genre_profile",
        version=2,
        output_model=GenreProfileProposalResponse,
        semantic_validator=validate,
        schema_profile="canon_strict",
        schema_name="rpg_world_forge_genre_profile",
    )


class ProviderGenreProfileGenerator:
    """Generate one ontology proposal without generating world-specific lore."""

    def __init__(self, provider: BaseProvider, config: WorldForgeProviderConfig) -> None:
        self.provider = provider
        self.config = config

    def generate_profile(
        self,
        *,
        genre: str,
        description: str,
        campaign_mode: str,
    ) -> GenreProfile:
        system = (
            "You are the Omnix World Forge ontology architect. Return strict JSON only. "
            "Design a reusable ontology for the requested genre before any lore is generated. "
            "The engine already owns a complete standard world-authoring catalogue with these "
            f"domains: {', '.join(_ALLOWED_CORE_IDS)}. Do not redefine or omit them. Return only "
            "setting-specific extension domains that add capabilities the standard catalogue cannot "
            "represent. Do not invent named factions, characters, locations, events, or lore. Define "
            "the kinds of records the later lore generator must create, their dependencies, typed "
            "fields, reference targets, target ranges, and runtime capability defaults. For each "
            "extension domain choose page_kind document or collection, a card_variant, an image_role "
            "from none, portrait, scene, landscape, emblem, icon, illustration, cover, map, and an "
            "authoring_group from world, lore, game-master. Use only these field types: string, "
            "integer, number, boolean, enum, entity_ref, entity_ref_list, structured_object. Reference "
            "fields must name valid standard or proposed domains. Create zero to eight distinctive "
            "extension domains only when the genre genuinely needs them. Do not import unrelated "
            "fantasy, magic, races, classes, pantheons, spells, monsters, or summoning concepts."
        )
        payload = {
            "genre": genre,
            "description": description,
            "campaign_mode": campaign_mode,
            "standard_domain_ids": list(_ALLOWED_CORE_IDS),
            "instruction": (
                "Describe ontology and presentation only. The result must be useful for structured "
                "canon, causal world simulation, observable evidence, dossier generation, authoring "
                "cards, and image planning."
            ),
        }
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user",
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        ]
        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(self.provider)
        total_calls = max(1, self.config.max_retries + 2)
        provider_key = self.config.provider.strip().casefold().removeprefix("llm:")
        call_limiter = (
            device_permit_slot("llm-local", priority="batch")
            if provider_key in {"lmstudio", "ollama", "local"}
            else nullcontext()
        )
        with call_limiter:
            outcome = gateway.try_generate(
                messages,
                contract=replace(
                    _proposal_contract(
                        genre=genre,
                        description=description,
                        campaign_mode=campaign_mode,
                    ),
                    temperature=min(self.config.temperature, 0.35),
                    max_tokens=min(self.config.max_tokens, 6144),
                ),
                model=self.config.model or None,
                retry_budget=StructuredRetryBudget(
                    max_provider_calls=total_calls,
                    max_transport_retries=self.config.max_retries,
                    max_format_downgrades=(
                        1 if self.config.lmstudio_schema_fallback else 0
                    ),
                    max_validation_regenerations=self.config.max_retries,
                    deadline_seconds=float(self.config.timeout_seconds),
                ),
            )
        if outcome.error is not None:
            # A profile is an approval-gated ontology, not authored world lore.
            # When a provider cannot satisfy the schema after its bounded repair
            # attempts, retain a usable engine-owned catalogue for review instead
            # of trapping the world before any lore can be generated.  The
            # provenance makes this explicit, and the profile still requires
            # author approval before it can unlock World Forge generation.
            fallback = HeuristicWorldLocalProfileGenerator().generate_profile(
                genre=genre,
                description=description,
                campaign_mode=campaign_mode,
            )
            return replace(
                fallback,
                provenance={
                    **dict(fallback.provenance),
                    "source": "profile_schema_safe_fallback_v1",
                    "provider_failure": {
                        "type": type(outcome.error).__name__,
                        "contract": "rpg.world_forge.genre_profile.v2",
                    },
                    "structured_diagnostics": outcome.diagnostics.as_dict(),
                },
            )
        assert outcome.value is not None
        return profile_from_proposal(
            outcome.value,
            genre=genre,
            description=description,
            campaign_mode=campaign_mode,
            provenance={
                "provider": self.config.provider,
                "model": self.config.model,
                "structured_diagnostics": outcome.diagnostics.as_dict(),
            },
        )


def build_genre_profile_generator_from_settings(
    settings: Mapping[str, Any],
) -> GenreProfileGenerator:
    """Build the exact provider/model pinned into a durable profile job."""

    provider_id = str(settings.get("provider_route") or "").strip().casefold()
    if provider_id.startswith("llm:"):
        provider_id = provider_id.split(":", 1)[1]
    model_id = str(settings.get("model") or "").strip()
    if provider_id in {"", "configured", "auto", "settings"}:
        raise RuntimeError("world profile job contains an unresolved provider route")
    if provider_id in {"deterministic", "offline", "reference-safe", "test"}:
        return HeuristicWorldLocalProfileGenerator()

    config = replace(
        WorldForgeProviderConfig.from_environment(),
        mode="live",
        provider=provider_id,
        model=model_id,
    )
    provider = None
    try:
        from app.providers import service as provider_service

        provider = provider_service.get_provider(provider_id)
    except Exception:
        provider = None
    if provider is None:
        provider = get_provider(
            provider_id,
            {
                "api_key": config.api_key,
                "base_url": config.base_url,
                "model": config.model or None,
                "timeout": config.timeout_seconds,
                "max_retries": config.max_retries,
            },
        )
    if provider is None:
        raise RuntimeError(f"world profile provider {provider_id} is unavailable")
    return ProviderGenreProfileGenerator(provider, config)
