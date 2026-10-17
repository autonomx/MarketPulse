"""Contracts, prompts, and payload normalization for provider-backed World Forge."""

from app.config.env import environment as _environment
import logging
from dataclasses import dataclass
from typing import Any, Literal, Mapping
from pydantic import BaseModel, ConfigDict, Field
from app.providers.base import BaseProvider
from app.providers.structured import StructuredCapabilities, StructuredContract
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_dossiers import dossier_prompt_contract
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.prompts import prompt_template
from collections.abc import Sequence

_PROMPT_1 = prompt_template('rpg.worlds_providers_world_forge_foundation.prompt', "1", 'You are the Omnix Campaign World Forge. Return strict JSON only for the single requested topic. Build rich, internally consistent campaign canon, not player-facing turn narration. The campaign_context.world_brief is authoritative: its title and description override generic genre, tone, or template labels. Ground every name, institution, conflict, technology, culture, creature, and location in that brief and its dependencies. Do not fall back to generic fantasy conventions (such as magic, elves, kingdoms, or medieval classes) unless the world brief explicitly supports them. Produce exactly the requested target_count of distinct, substantive entities. Never pad output with numbered topic names or generic placeholder canon. Respect dependency entities and IDs. Return topic_id plus arrays named documents, entities, facts, relationships, knowledge_rules, and story_threads, and a provenance object. Set provenance to exactly {{}}; Omnix adds trusted provider, authorship, usage, and validation provenance after accepting the response. Never copy dependency provenance or authorship ledgers into the response. Every generated entity must include short_summary plus a dossier object matching the supplied rpg_world_entity_dossier_v1 contract. Dossier sections use stable IDs, titled sections, and one to three substantial paragraphs per substantive section. Use short_summary only for cards; do not replace the long dossier with a one- or two-line description. Keep mechanics and canonical references in their structured fields rather than hiding them in prose. NPC dossiers must include appearance, personality, backstory, goals, motives, speech_style, faction_ids, location_id, secrets, and known_facts. Location dossiers must include a sensory_profile and region_id. Every factual row must use stable IDs, generated_proposal authority, approved objective_canon authority, visibility, and entity_refs. Facts use content for a concise one-sentence canon summary and expanded_description for one or two self-contained lore paragraphs that explain origins, impact, or consequences. Never invent an unresolved dependency ID. The requested domain is {v0}; follow its domain-specific section template exactly.')
_PROMPT_2 = prompt_template('rpg.worlds_providers_world_forge_foundation.system_prompt', "1", "{v0} This is entity batch {v1} of {v2}. Return only this batch's requested entities, with no overlap with earlier batches. Earlier entities are: {v3}. Use these preallocated entity IDs exactly, one per returned entity: {v4}. Expand the allocated registry slots exactly; preserve each assigned name, role, and distinction: {v5}.")
_PROMPT_3 = prompt_template('rpg.worlds_providers_world_forge_foundation.entity_registry_system_prompt', "1", 'You are the Omnix Campaign World Forge planner. Return strict JSON only. Create a compact registry for the requested topic before any dossiers are written. Use every allocated ID exactly once. Give every entry a unique, setting-grounded name, role, and distinction so later parallel writers can expand different canon rather than inventing overlapping entities. Return only topic_id, entities, and provenance. Each entity must contain id, name, role, and distinction; set provenance to exactly {{}}; do not write dossiers, facts, documents, dependency provenance, or authorship ledgers. The requested domain is {v0}.')


_LOGGER = logging.getLogger(__name__)
_COLLECTIONS = (
    "documents",
    "entities",
    "facts",
    "relationships",
    "knowledge_rules",
    "story_threads",
)
_ENTITY_ID_PREFIXES = {
    "areas": "area",
    "classes": "class",
    "encounter_seeds": "encounter",
    "factions": "faction",
    "feats": "feat",
    "items": "item",
    "locations": "location",
    "monsters": "monster",
    "npcs": "npc",
    "one_shots": "one_shot",
    "opening_scenarios": "opening",
    "points_of_interest": "poi",
    "quests": "quest",
    "races": "race",
    "regions": "region",
    "spells": "spell",
}


class _WorldForgeRow(BaseModel):
    """Typed object envelope for heterogeneous topic rows."""

    model_config = ConfigDict(extra="allow")


class WorldForgeDocument(_WorldForgeRow):
    pass


class WorldForgeDossierSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    paragraphs: list[str] = Field(min_length=1)


class WorldForgeDossier(BaseModel):
    """Provider-authored reading prose required for every generated entity."""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal["rpg_world_entity_dossier_v1"]
    subtitle: str = ""
    quote: dict[str, Any] | None = None
    quick_facts: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[WorldForgeDossierSection] = Field(min_length=1)
    related_entity_ids: list[str] = Field(default_factory=list)


class WorldForgeEntity(_WorldForgeRow):
    pass


class WorldForgeAuthoredEntity(_WorldForgeRow):
    short_summary: str = Field(min_length=1)
    dossier: WorldForgeDossier


class WorldForgeFact(_WorldForgeRow):
    pass


class WorldForgeRelationship(_WorldForgeRow):
    pass


class WorldForgeKnowledgeRule(_WorldForgeRow):
    pass


class WorldForgeStoryThread(_WorldForgeRow):
    pass


class WorldForgeTopicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=1)
    documents: list[WorldForgeDocument]
    entities: list[WorldForgeEntity]
    facts: list[WorldForgeFact]
    relationships: list[WorldForgeRelationship]
    knowledge_rules: list[WorldForgeKnowledgeRule]
    story_threads: list[WorldForgeStoryThread]
    provenance: dict[str, Any]


class WorldForgeAuthoredTopicResponse(WorldForgeTopicResponse):
    """Live-generation response whose entities always carry reviewable lore."""

    entities: list[WorldForgeAuthoredEntity]  # type: ignore[assignment]  # narrows the base field


class WorldForgeEntityRegistryItem(BaseModel):
    """A compact, canonical slot that a later dossier call must expand."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    distinction: str = Field(min_length=1)


class WorldForgeEntityRegistryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=1)
    entities: list[WorldForgeEntityRegistryItem]
    provenance: dict[str, Any]


def _topic_contract(
    expected_topic_id: str,
    *,
    expected_entity_count: int | None = None,
    expected_entity_ids: tuple[str, ...] = (),
    expected_entity_names: tuple[str, ...] = (),
) -> StructuredContract[WorldForgeAuthoredTopicResponse]:
    def validate_topic(value: WorldForgeAuthoredTopicResponse) -> None:
        if value.topic_id != expected_topic_id:
            raise ValueError(
                f"World Forge provider returned {value.topic_id or '<missing>'} "
                f"for {expected_topic_id}"
            )
        if (
            expected_entity_count is not None
            and len(value.entities) != expected_entity_count
        ):
            raise ValueError(
                f"World Forge provider returned {len(value.entities)} entities for "
                f"{expected_topic_id}; expected {expected_entity_count}"
            )
        if expected_entity_ids:
            actual_ids = tuple(
                str(row.get("id") or row.get("entity_id") or "")
                for row in _model_rows(value.entities)
            )
            if set(actual_ids) != set(expected_entity_ids) or len(actual_ids) != len(
                set(actual_ids)
            ):
                raise ValueError(
                    "World Forge provider returned entity IDs "
                    f"{list(actual_ids)} for {expected_topic_id}; expected "
                    f"{list(expected_entity_ids)}"
                )
        if expected_entity_names:
            actual_names = tuple(
                str(row.get("name") or row.get("title") or "").strip()
                for row in _model_rows(value.entities)
            )
            if actual_names != expected_entity_names:
                raise ValueError(
                    "World Forge provider returned entity names "
                    f"{list(actual_names)} for {expected_topic_id}; expected "
                    f"{list(expected_entity_names)}"
                )

    return StructuredContract(
        contract_id="rpg.world_forge.topic",
        version=3,
        output_model=WorldForgeAuthoredTopicResponse,
        semantic_validator=validate_topic,
        schema_profile="canon_strict",
        schema_name="rpg_world_forge_topic",
    )


@dataclass(frozen=True)
class WorldForgeProviderConfig:
    mode: str = "auto"
    provider: str = ""
    model: str = ""
    prompt_version: str = "world-prompt-v1"
    api_key: str | None = None
    base_url: str | None = None
    timeout_seconds: int = 180
    max_retries: int = 2
    temperature: float = 0.6
    max_tokens: int = 8192
    entity_batch_size: int = 1
    # Local models can repeat allocated entity IDs when sibling batches are
    # generated at once. Serial generation is the reliable default; operators
    # who have validated their provider can opt back into two workers.
    entity_batch_workers: int = 1
    retry_backoff_seconds: float = 1.0
    lmstudio_schema_fallback: bool = True

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "WorldForgeProviderConfig":
        env = environ if environ is not None else _environment()
        return cls(
            mode=str(env.get("OMNIX_RPG_WORLD_FORGE_MODE") or "auto")
            .strip()
            .casefold(),
            provider=str(env.get("OMNIX_RPG_WORLD_FORGE_PROVIDER") or "").strip(),
            model=str(env.get("OMNIX_RPG_WORLD_FORGE_MODEL") or "").strip(),
            prompt_version=str(
                env.get("OMNIX_RPG_WORLD_FORGE_PROMPT_VERSION")
                or "world-prompt-v1"
            ).strip(),
            api_key=(
                str(env.get("OMNIX_RPG_WORLD_FORGE_API_KEY") or "").strip() or None
            ),
            base_url=(
                str(env.get("OMNIX_RPG_WORLD_FORGE_BASE_URL") or "").strip() or None
            ),
            timeout_seconds=max(
                10,
                min(
                    int(env.get("OMNIX_RPG_WORLD_FORGE_TIMEOUT_SECONDS") or 180),
                    900,
                ),
            ),
            max_retries=max(
                0,
                min(int(env.get("OMNIX_RPG_WORLD_FORGE_MAX_RETRIES") or 2), 5),
            ),
            temperature=max(
                0.0,
                min(
                    float(env.get("OMNIX_RPG_WORLD_FORGE_TEMPERATURE") or 0.6),
                    2.0,
                ),
            ),
            max_tokens=max(
                1024,
                min(int(env.get("OMNIX_RPG_WORLD_FORGE_MAX_TOKENS") or 8192), 32768),
            ),
            entity_batch_size=max(
                1,
                min(
                    int(
                        env.get("OMNIX_RPG_WORLD_FORGE_ENTITY_BATCH_SIZE") or 1
                    ),
                    8,
                ),
            ),
            entity_batch_workers=max(
                1,
                min(
                    int(
                        env.get("OMNIX_RPG_WORLD_FORGE_ENTITY_BATCH_WORKERS")
                        or 1
                    ),
                    2,
                ),
            ),
            retry_backoff_seconds=max(
                0.0,
                min(
                    float(
                        env.get("OMNIX_RPG_WORLD_FORGE_RETRY_BACKOFF_SECONDS") or 1.0
                    ),
                    30.0,
                ),
            ),
            lmstudio_schema_fallback=str(
                env.get("OMNIX_RPG_WORLD_FORGE_LMSTUDIO_SCHEMA_FALLBACK") or "true"
            ).strip().casefold()
            not in {"0", "false", "no", "off"},
        )

    @property
    def live_enabled(self) -> bool:
        return self.mode not in {
            "offline",
            "deterministic",
            "test",
            "disabled",
        } and bool(self.provider)


class _ConfiguredProviderView:
    """Expose immutable route identity without mutating a shared provider instance."""

    def __init__(self, provider: BaseProvider, provider_name: str) -> None:
        self._provider = provider
        self.provider_name = provider_name or str(
            getattr(provider, "provider_name", provider.__class__.__name__)
        )
        self.config = getattr(provider, "config", None)

    def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        return self._provider.chat_completion(*args, **kwargs)

    def get_structured_capabilities(self, *args: Any, **kwargs: Any):
        method = getattr(self._provider, "get_structured_capabilities", None)
        if callable(method):
            return method(*args, **kwargs)
        return StructuredCapabilities.default_for_provider(self.provider_name)


def _system_prompt(
    node: CampaignTopicNode,
    *,
    batch_index: int | None = None,
    batch_count: int | None = None,
    existing_entities: tuple[Mapping[str, str], ...] = (),
    assigned_entity_ids: tuple[str, ...] = (),
    assigned_entities: tuple[Mapping[str, str], ...] = (),
) -> str:
    prompt = (
        _PROMPT_1.format(v0=(node.topic_id))
    )
    if batch_index is None or batch_count is None:
        return prompt
    exclusions = ", ".join(
        f"{row.get('id') or '<unknown>'} ({row.get('name') or 'unnamed'})"
        for row in existing_entities
    )
    assigned_slot_text = "; ".join(
        f"{row['id']} = {row['name']} ({row['role']}; {row['distinction']})"
        for row in assigned_entities
    )
    return (
        _PROMPT_2.format(v0=(prompt), v1=(batch_index + 1), v2=(batch_count), v3=(exclusions or 'none'), v4=(', '.join(assigned_entity_ids) or 'no allocation supplied'), v5=(assigned_slot_text or 'no registry slot supplied'))
    )


def _entity_registry_contract(
    expected_topic_id: str,
    *,
    expected_entity_ids: tuple[str, ...],
) -> StructuredContract[WorldForgeEntityRegistryResponse]:
    def validate_registry(value: WorldForgeEntityRegistryResponse) -> None:
        if value.topic_id != expected_topic_id:
            raise ValueError(
                f"World Forge registry returned {value.topic_id or '<missing>'} "
                f"for {expected_topic_id}"
            )
        actual_ids = tuple(row.id for row in value.entities)
        if set(actual_ids) != set(expected_entity_ids) or len(actual_ids) != len(
            set(actual_ids)
        ):
            raise ValueError(
                "World Forge registry returned IDs "
                f"{list(actual_ids)} for {expected_topic_id}; expected "
                f"{list(expected_entity_ids)}"
            )
        normalized_names = [row.name.strip().casefold() for row in value.entities]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError(
                f"World Forge registry returned duplicate names for {expected_topic_id}"
            )

    return StructuredContract(
        contract_id="rpg.world_forge.entity_registry",
        version=1,
        output_model=WorldForgeEntityRegistryResponse,
        semantic_validator=validate_registry,
        schema_profile="canon_strict",
        schema_name="rpg_world_forge_entity_registry",
    )


def _payload(
    node: CampaignTopicNode,
    *,
    seed: int,
    campaign_context: Mapping[str, Any],
    dependency_topics: Mapping[str, GeneratedTopic],
    batch_index: int | None = None,
    batch_count: int | None = None,
    existing_entities: tuple[Mapping[str, str], ...] = (),
    assigned_entity_ids: tuple[str, ...] = (),
    assigned_entities: tuple[Mapping[str, str], ...] = (),
) -> dict[str, Any]:
    payload = {
        "contract_version": "rpg_world_forge_topic_request_v3",
        "seed": seed,
        "topic": {
            "topic_id": node.topic_id,
            "title": node.title,
            "category": node.category,
            "visibility": node.visibility,
            "target_count": node.target_count,
            "generator_role": node.generator_role,
            "metadata": dict(node.metadata),
        },
        "campaign_context": dict(campaign_context),
        "dependencies": _compact_dependency_topics(dependency_topics),
        "required_output": {
            "topic_id": node.topic_id,
            "collections": list(_COLLECTIONS),
            "entity_dossier": dossier_prompt_contract(node.topic_id),
            "provenance": {},
        },
    }
    if batch_index is not None and batch_count is not None:
        payload["generation_batch"] = {
            "index": batch_index,
            "count": batch_count,
            "previous_entities": [dict(row) for row in existing_entities],
            "assigned_entity_ids": list(assigned_entity_ids),
            "assigned_entities": [dict(row) for row in assigned_entities],
        }
    return payload


def _entity_registry_system_prompt(node: CampaignTopicNode) -> str:
    return (
        _PROMPT_3.format(v0=(node.topic_id))
    )


def _entity_registry_payload(
    node: CampaignTopicNode,
    *,
    seed: int,
    campaign_context: Mapping[str, Any],
    dependency_topics: Mapping[str, GeneratedTopic],
    assigned_entity_ids: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "contract_version": "rpg_world_forge_entity_registry_request_v1",
        "seed": seed,
        "topic": {
            "topic_id": node.topic_id,
            "title": node.title,
            "category": node.category,
            "target_count": node.target_count,
            "metadata": dict(node.metadata),
        },
        "campaign_context": dict(campaign_context),
        "dependencies": _compact_dependency_topics(dependency_topics),
        "allocated_entity_ids": list(assigned_entity_ids),
        "required_output": {
            "topic_id": node.topic_id,
            "entity_fields": ["id", "name", "role", "distinction"],
            "provenance": {},
        },
    }


def _compact_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _compact_entity_reference(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or row.get("entity_id") or ""),
        "name": _compact_text(row.get("name") or row.get("title"), limit=160),
        "kind": str(row.get("kind") or row.get("type") or ""),
        "summary": _compact_text(
            row.get("short_summary")
            or row.get("summary")
            or row.get("description"),
            limit=320,
        ),
    }


def _compact_dependency_topics(
    dependency_topics: Mapping[str, GeneratedTopic],
) -> dict[str, Any]:
    """Keep reference identity while excluding long dossiers from later prompts."""

    compact: dict[str, Any] = {}
    for topic_id, topic in sorted(dependency_topics.items()):
        compact[topic_id] = {
            "topic_id": topic.topic_id,
            "entities": [
                _compact_entity_reference(row)
                for row in topic.entities
            ],
            "facts": [
                {
                    "id": str(row.get("id") or row.get("fact_id") or ""),
                    "content": _compact_text(
                        row.get("content") or row.get("summary"),
                        limit=360,
                    ),
                    "entity_refs": [
                        str(value) for value in row.get("entity_refs") or ()
                    ][:20],
                }
                for row in topic.facts[:40]
            ],
            "relationships": [
                {
                    "source": str(row.get("source") or row.get("source_id") or ""),
                    "target": str(row.get("target") or row.get("target_id") or ""),
                    "kind": str(row.get("kind") or row.get("relationship") or ""),
                }
                for row in topic.relationships[:40]
            ],
            "documents": [
                {
                    "title": _compact_text(
                        row.get("title") or row.get("name"), limit=160
                    ),
                    "summary": _compact_text(
                        row.get("summary") or row.get("body") or row.get("text"),
                        limit=420,
                    ),
                }
                for row in topic.documents[:8]
            ],
        }
    return compact


def _token_estimate(text: str) -> int:
    """Return a deliberately labelled, provider-independent token estimate.

    Providers do not consistently return usage for structured completions.  This
    keeps durable world-generation records useful without presenting the value
    as metered provider usage.
    """

    return max(1, (len(text) + 3) // 4)


def _model_rows(rows: Sequence[_WorldForgeRow]) -> tuple[Mapping[str, Any], ...]:
    return tuple(row.model_dump(mode="python") for row in rows)
