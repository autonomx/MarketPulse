"""Just-in-time campaign lore and executable mechanics materialization.

Runtime discoveries are campaign-local overlays. They never mutate the pinned
published world revision, and prose is generated from the same validated
definition that gameplay consumes.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.persistence.database import default_database
from app.security.tenant_context import current_tenant
from app.persistence.unit_of_work import unit_of_work
from app.apps.rpg.foundation.llm_app_gateway import build_app_llm_gateway

from .campaign_lore_store import (
    _campaign_id,
    _hydrate_session,
    _mapping,
    _rows,
    _save_portable_projection,
    _text,
    load_campaign_lore,
)
from app.prompts import prompt_template

if TYPE_CHECKING:
    from app.apps.rpg.genesis.contracts import CampaignSessions

_PROMPT_1 = prompt_template('rpg.session_genesis_runtime_materialization.common', "1", 'Materialize the campaign-local {v0} named "{v1}". Return strict JSON only. Generate structured executable truth first and lore_text from that same truth. lore_text must be 350-650 words in 4-7 natural paragraphs, player-safe, vivid, and internally consistent. Preserve compatible existing canon and definition fields. Do not emit code, formulas, headings, markdown, or unsupported mechanics. ')
_PROMPT_2 = prompt_template('rpg.session_genesis_runtime_materialization.prompt', "1", (
    "Use root keys kind, name, lore_text, creature, and location. Set kind to "
        '"location" and creature to null. The location object must contain definition_id, '
        "name, region_id, environment, tags, services, exits, hazards, and atmosphere. "
        "Each exit must contain destination_id, label, access, requirement. Each hazard must "
        "contain hazard_id, name, trigger, check, difficulty, consequence. Allowed checks are "
        "agility, endurance, intellect, perception, and survival."
))
_PROMPT_3 = prompt_template('rpg.session_genesis_runtime_materialization.prompt_2', "1", (
    "Use root keys kind, name, lore_text, creature, and location. Set kind to "
            '"creature" and location to null. The creature object must contain definition_id, '
            "name, level, hp, defense, armor, damage_min, damage_max, accuracy_bonus, "
            "initiative_bonus, morale_threshold, tags, loot_table_id, xp_value, budget_cost, "
            "condition_immunities, vulnerabilities, behavior, and habitat. Each vulnerability "
            "must contain trigger_tag, aliases, condition, duration_turns, magnitude, and "
            "description. Allowed conditions are bleeding, burning, poisoned, prone, and stunned."
))

_SUPPORTED_CONDITIONS = {
    "bleeding",
    "burning",
    "poisoned",
    "prone",
    "stunned",
}


class RuntimeMaterializationUnavailable(RuntimeError):
    """Raised when a runtime definition cannot be safely generated."""


class RuntimeMaterializationConflict(RuntimeError):
    """Raised when canon changes while a definition is being generated."""


class _StrictMaterializationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatureVulnerability(_StrictMaterializationModel):
    trigger_tag: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    condition: Literal["bleeding", "burning", "poisoned", "prone", "stunned"]
    duration_turns: int = Field(default=1, ge=1, le=5)
    magnitude: int = Field(default=1, ge=1, le=5)
    description: str = Field(min_length=1, max_length=300)


class CreatureDefinition(_StrictMaterializationModel):
    definition_id: str = Field(min_length=1, max_length=180)
    definition_revision: int = Field(default=1, ge=1)
    source_document_id: str = ""
    name: str = Field(min_length=1, max_length=120)
    level: int = Field(default=1, ge=1, le=30)
    hp: int = Field(ge=1, le=5000)
    defense: int = Field(ge=1, le=50)
    armor: int = Field(default=0, ge=0, le=30)
    damage_min: int = Field(ge=0, le=500)
    damage_max: int = Field(ge=1, le=1000)
    accuracy_bonus: int = Field(default=0, ge=-20, le=30)
    initiative_bonus: int = Field(default=0, ge=-20, le=30)
    morale_threshold: int = Field(default=35, ge=0, le=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    loot_table_id: str = Field(default="loot:creature_common", max_length=180)
    xp_value: int = Field(default=25, ge=0, le=100000)
    budget_cost: int = Field(default=25, ge=1, le=100000)
    condition_immunities: list[str] = Field(default_factory=list, max_length=12)
    vulnerabilities: list[CreatureVulnerability] = Field(
        default_factory=list,
        max_length=8,
    )
    behavior: str = Field(min_length=1, max_length=1200)
    habitat: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_damage_and_conditions(self) -> "CreatureDefinition":
        if self.damage_max < self.damage_min:
            raise ValueError("damage_max_must_be_at_least_damage_min")
        invalid = {
            value
            for value in self.condition_immunities
            if value not in _SUPPORTED_CONDITIONS
        }
        if invalid:
            raise ValueError(
                "unsupported_condition_immunities:" + ",".join(sorted(invalid))
            )
        return self


class LocationHazard(_StrictMaterializationModel):
    hazard_id: str = Field(min_length=1, max_length=180)
    name: str = Field(min_length=1, max_length=120)
    trigger: str = Field(min_length=1, max_length=300)
    check: Literal["agility", "endurance", "intellect", "perception", "survival"]
    difficulty: int = Field(default=10, ge=1, le=40)
    consequence: str = Field(min_length=1, max_length=300)


class LocationExit(_StrictMaterializationModel):
    destination_id: str = Field(min_length=1, max_length=180)
    label: str = Field(min_length=1, max_length=120)
    access: Literal["open", "hidden", "locked", "conditional"] = "open"
    requirement: str = Field(default="", max_length=300)


class LocationDefinition(_StrictMaterializationModel):
    definition_id: str = Field(min_length=1, max_length=180)
    definition_revision: int = Field(default=1, ge=1)
    source_document_id: str = ""
    name: str = Field(min_length=1, max_length=120)
    region_id: str = Field(default="", max_length=180)
    environment: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    services: list[str] = Field(default_factory=list, max_length=20)
    exits: list[LocationExit] = Field(default_factory=list, max_length=20)
    hazards: list[LocationHazard] = Field(default_factory=list, max_length=12)
    atmosphere: str = Field(min_length=1, max_length=1200)


class RuntimeMaterializationProposal(_StrictMaterializationModel):
    kind: Literal["creature", "location"]
    name: str = Field(min_length=1, max_length=120)
    lore_text: str = Field(min_length=600, max_length=12000)
    creature: CreatureDefinition | None = None
    location: LocationDefinition | None = None

    @model_validator(mode="after")
    def validate_matching_definition(self) -> "RuntimeMaterializationProposal":
        definition = self.creature if self.kind == "creature" else self.location
        other = self.location if self.kind == "creature" else self.creature
        if definition is None or other is not None:
            raise ValueError("proposal_must_contain_exactly_one_matching_definition")
        if definition.name.casefold() != self.name.casefold():
            raise ValueError("proposal_name_must_match_definition_name")
        paragraphs = [
            row.strip()
            for row in re.split(r"\n\s*\n", self.lore_text)
            if row.strip()
        ]
        if len(paragraphs) < 3:
            raise ValueError("lore_text_requires_at_least_three_paragraphs")
        return self


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "entry"


def _definition_id(kind: str, name: str) -> str:
    return f"{kind}:{_slug(name)}"


def _matching_document(
    bible: Mapping[str, Any],
    *,
    kind: str,
    name: str,
    document_id: str,
) -> dict[str, Any] | None:
    topic = "monsters" if kind == "creature" else "locations"
    for row in _rows(bible.get("documents")):
        if document_id and _text(row.get("document_id")) == document_id:
            return row
        if (
            _text(row.get("topic_id")) == topic
            and _text(row.get("title")).casefold() == name.casefold()
        ):
            return row
    return None


def _existing_definition(
    bible: Mapping[str, Any],
    *,
    kind: str,
    name: str,
) -> dict[str, Any] | None:
    catalog = _mapping(bible.get("mechanics_catalog"))
    group = _mapping(catalog.get("creatures" if kind == "creature" else "locations"))
    wanted_id = _definition_id(kind, name)
    for definition_id, value in group.items():
        row = _mapping(value)
        if (
            _text(definition_id) == wanted_id
            or _text(row.get("name")).casefold() == name.casefold()
        ):
            return row
    return None


def _generation_context(
    bible: Mapping[str, Any],
    *,
    kind: str,
    name: str,
    direction: str,
    document: Mapping[str, Any] | None,
    existing_definition: Mapping[str, Any] | None,
) -> dict[str, Any]:
    related = []
    for row in _rows(bible.get("documents")):
        if document and _text(row.get("document_id")) == _text(
            document.get("document_id")
        ):
            continue
        if _text(row.get("visibility")) not in {
            "public",
            "player_known",
            "learned",
            "partially_known",
            "disputed",
        }:
            continue
        related.append(
            {
                "title": _text(row.get("title")),
                "topic_id": _text(row.get("topic_id")),
                "summary": _text(row.get("summary_500") or row.get("summary_120")),
            }
        )
        if len(related) >= 12:
            break
    return {
        "target": {"kind": kind, "name": name},
        "user_direction": direction[:1000],
        "existing_lore": _text(_mapping(document).get("full_text")),
        "existing_definition": dict(existing_definition or {}),
        "related_player_known_canon": related,
    }


def _prompt(kind: str, name: str) -> str:
    common = (
        _PROMPT_1.format(v0=(kind), v1=(name))
    )
    if kind == "creature":
        return common + (
            _PROMPT_3.text
        )
    return common + (
        _PROMPT_2.text
    )


def _expected_proposal_validator(kind: str, name: str):
    def validate(value: RuntimeMaterializationProposal) -> None:
        if value.kind != kind:
            raise ValueError(f"materialization_kind_mismatch:{value.kind}:{kind}")
        if value.name.casefold() != name.casefold():
            raise ValueError(f"materialization_name_mismatch:{value.name}:{name}")

    return validate


def _canonicalize_proposal(
    proposal: RuntimeMaterializationProposal,
    *,
    kind: str,
    name: str,
) -> RuntimeMaterializationProposal:
    payload = proposal.model_dump(mode="python")
    payload["kind"] = kind
    payload["name"] = name
    definition = dict(payload.get(kind) or {})
    definition["definition_id"] = _definition_id(kind, name)
    definition["name"] = name
    payload[kind] = definition
    payload["location" if kind == "creature" else "creature"] = None
    try:
        return RuntimeMaterializationProposal.model_validate(payload)
    except Exception as exc:
        raise RuntimeMaterializationUnavailable(
            f"The generated {kind} definition failed canonical validation: {exc}"
        ) from exc


def _legacy_validated_proposal(
    raw: Any,
    *,
    kind: str,
    name: str,
) -> RuntimeMaterializationProposal:
    """Compatibility for injected legacy test gateways without typed support."""

    text = _text(raw).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        payload = json.loads(text)
    except Exception as exc:
        raise RuntimeMaterializationUnavailable(
            "The legacy materialization gateway returned invalid JSON."
        ) from exc
    try:
        proposal = RuntimeMaterializationProposal.model_validate(payload)
    except Exception as exc:
        raise RuntimeMaterializationUnavailable(
            f"The generated {kind} definition failed validation: {exc}"
        ) from exc
    _expected_proposal_validator(kind, name)(proposal)
    return _canonicalize_proposal(proposal, kind=kind, name=name)


def _retrieval_cards(
    cards: list[dict[str, Any]],
    document: Mapping[str, Any],
    revision: int,
) -> list[dict[str, Any]]:
    document_id = _text(document.get("document_id"))
    by_size = {
        _text(row.get("summary_size")): row
        for row in cards
        if _text(row.get("document_id")) == document_id
    }
    for size, summary_key in (("short", "summary_120"), ("medium", "summary_500")):
        content = _text(document.get(summary_key))
        row = by_size.get(size)
        if row is None:
            cards.append(
                {
                    "id": f"card:{document_id}:{size}",
                    "document_id": document_id,
                    "summary_size": size,
                    "title": _text(document.get("title")),
                    "content": content,
                    "visibility": _text(document.get("visibility")) or "public",
                    "canon_revision": revision,
                }
            )
        else:
            row.update(
                {
                    "title": _text(document.get("title")),
                    "content": content,
                    "canon_revision": revision,
                }
            )
    return cards


def apply_runtime_materialization(
    bible: Mapping[str, Any],
    proposal: RuntimeMaterializationProposal,
    *,
    canon_revision: int,
    requested_document_id: str = "",
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Apply one validated proposal to canon and its mechanics catalog."""

    candidate = deepcopy(dict(bible))
    existing_document = _matching_document(
        candidate,
        kind=proposal.kind,
        name=proposal.name,
        document_id=requested_document_id,
    )
    document_id = _text(_mapping(existing_document).get("document_id")) or (
        f"lore:runtime:{proposal.kind}:{_slug(proposal.name)}"
    )
    topic_id = "monsters" if proposal.kind == "creature" else "locations"
    definition_model = proposal.creature or proposal.location
    assert definition_model is not None
    definition = definition_model.model_dump(mode="json")
    previous_definition = _existing_definition(
        candidate,
        kind=proposal.kind,
        name=proposal.name,
    )
    definition["definition_revision"] = int(
        _mapping(previous_definition).get("definition_revision") or 0
    ) + 1
    definition["source_document_id"] = document_id
    definition["campaign_canon_revision"] = canon_revision
    full_text = proposal.lore_text.strip()
    document = {
        **dict(existing_document or {}),
        "document_id": document_id,
        "topic_id": topic_id,
        "title": proposal.name,
        "full_text": full_text,
        "summary_500": full_text[:500].rstrip(),
        "summary_120": full_text[:120].rstrip(),
        "keywords": list(
            dict.fromkeys(
                [
                    proposal.name,
                    _text(definition.get("definition_id")),
                    topic_id,
                    *[str(value) for value in definition.get("tags") or ()],
                ]
            )
        ),
        "entity_refs": [_text(definition.get("definition_id"))],
        "visibility": _text(_mapping(existing_document).get("visibility")) or "public",
        "canon_revision": canon_revision,
        "provenance": {
            **_mapping(_mapping(existing_document).get("provenance")),
            "source": "runtime_structured_materialization",
            "definition_id": definition["definition_id"],
            "definition_revision": definition["definition_revision"],
        },
    }
    documents = _rows(candidate.get("documents"))
    replaced = False
    for index, row in enumerate(documents):
        if _text(row.get("document_id")) == document_id:
            documents[index] = document
            replaced = True
            break
    if not replaced:
        documents.append(document)
    candidate["documents"] = documents
    catalog = deepcopy(_mapping(candidate.get("mechanics_catalog")))
    catalog.update(
        {
            "schema_version": "rpg_campaign_mechanics_v1",
            "revision": canon_revision,
        }
    )
    group_key = "creatures" if proposal.kind == "creature" else "locations"
    group = deepcopy(_mapping(catalog.get(group_key)))
    group[_text(definition.get("definition_id"))] = definition
    catalog[group_key] = group
    catalog.setdefault("creatures", {})
    catalog.setdefault("locations", {})
    candidate["mechanics_catalog"] = catalog
    entities = deepcopy(_mapping(candidate.get("entities")))
    definition_id = _text(definition.get("definition_id"))
    entities[definition_id] = {
        **_mapping(entities.get(definition_id)),
        "kind": "monster" if proposal.kind == "creature" else "location",
        "name": proposal.name,
        "description": document["summary_500"],
        "visibility": document["visibility"],
        "mechanics_definition_id": definition_id,
        "mechanics_definition_revision": definition["definition_revision"],
    }
    candidate["entities"] = entities
    discovery = deepcopy(_mapping(candidate.get("discovery_state")))
    pages = deepcopy(_mapping(discovery.get("pages")))
    entity_statuses = deepcopy(_mapping(discovery.get("entities")))
    pages[document_id] = pages.get(document_id) or "learned"
    entity_statuses[definition_id] = entity_statuses.get(definition_id) or "learned"
    discovery.update(
        {
            "pages": pages,
            "entities": entity_statuses,
            "discoveries": list(discovery.get("discoveries") or ()),
        }
    )
    candidate["discovery_state"] = discovery
    candidate["retrieval_cards"] = _retrieval_cards(
        _rows(candidate.get("retrieval_cards")),
        document,
        canon_revision,
    )
    candidate["canon_revision"] = canon_revision
    manifest = deepcopy(_mapping(candidate.get("manifest")))
    manifest["runtime_materialization_count"] = int(
        manifest.get("runtime_materialization_count") or 0
    ) + 1
    candidate["manifest"] = manifest
    return candidate, document_id, definition


def materialize_runtime_lore(
    session_id: str,
    session: Mapping[str, Any],
    *,
    sessions: CampaignSessions,
    kind: Literal["creature", "location"],
    name: str,
    direction: str = "",
    document_id: str = "",
    database: Any | None = None,
    llm_gateway: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate, validate, compile, and atomically persist a runtime definition."""

    normalized_name = _text(name)[:120]
    if not normalized_name:
        raise RuntimeMaterializationUnavailable("A materialization name is required.")
    hydrated, _storage = load_campaign_lore(
        session_id,
        session,
        ensure_current_location=False,
        database=database,
        sessions=sessions,
    )
    campaign_id = _campaign_id(session_id, hydrated)
    db = database or default_database()
    context = current_tenant()
    with unit_of_work(db) as work:
        source_record = work.campaign_bibles.get(context, campaign_id)
        if source_record is None:
            raise RuntimeMaterializationUnavailable(
                "The authoritative Campaign Bible is unavailable."
            )
        source_revision = int(source_record["revision"])
        source_bible = deepcopy(dict(source_record["document"]))
        current_document = _matching_document(
            source_bible,
            kind=kind,
            name=normalized_name,
            document_id=document_id,
        )
        current_definition = _existing_definition(
            source_bible,
            kind=kind,
            name=normalized_name,
        )
        work.rollback()

    gateway = llm_gateway or build_app_llm_gateway()
    if gateway is None:
        raise RuntimeMaterializationUnavailable(
            "No runtime materialization provider is configured."
        )
    context_payload = _generation_context(
        source_bible,
        kind=kind,
        name=normalized_name,
        direction=_text(direction),
        document=current_document,
        existing_definition=current_definition,
    )
    try:
        if hasattr(gateway, "generate_typed"):
            proposal = gateway.generate_typed(
                _prompt(kind, normalized_name),
                output_model=RuntimeMaterializationProposal,
                contract_id="rpg.runtime_materialization.proposal",
                contract_version=2,
                context=context_payload,
                timeout_s=90.0,
                max_provider_calls=3,
                max_format_downgrades=1,
                max_validation_regenerations=2,
                temperature=0.2,
                max_tokens=8192,
                schema_profile="canon_strict",
                schema_name="rpg_runtime_materialization",
                semantic_validator=_expected_proposal_validator(
                    kind,
                    normalized_name,
                ),
            )
            proposal = _canonicalize_proposal(
                proposal,
                kind=kind,
                name=normalized_name,
            )
        else:
            raw = gateway.generate(
                _prompt(kind, normalized_name),
                context=context_payload,
                timeout_s=90.0,
            )
            proposal = _legacy_validated_proposal(
                raw,
                kind=kind,
                name=normalized_name,
            )
    except RuntimeMaterializationUnavailable:
        raise
    except Exception as exc:
        raise RuntimeMaterializationUnavailable(
            f"The runtime materialization provider failed: {exc}"
        ) from exc

    with unit_of_work(db) as work:
        current = work.campaign_bibles.get(context, campaign_id, for_update=True)
        if current is None:
            raise RuntimeMaterializationUnavailable(
                "The authoritative Campaign Bible is unavailable."
            )
        if int(current["revision"]) != source_revision:
            raise RuntimeMaterializationConflict(
                "Campaign canon changed during materialization; please try again."
            )
        next_revision = source_revision + 1
        candidate, persisted_document_id, definition = apply_runtime_materialization(
            current["document"],
            proposal,
            canon_revision=next_revision,
            requested_document_id=document_id,
        )
        stored = work.campaign_bibles.put(
            context,
            campaign_id=campaign_id,
            document=candidate,
            expected_revision=source_revision,
            provenance={
                **_mapping(current.get("provenance")),
                "last_source": "runtime_structured_materialization",
                "structured_contract": "rpg.runtime_materialization.proposal.v2",
                "materialized_kind": kind,
                "materialized_definition_id": definition["definition_id"],
            },
            consistency_report=_mapping(current.get("consistency_report")),
            completeness=_mapping(current.get("completeness")),
        )
        work.commit()

    updated = _hydrate_session(
        hydrated,
        stored["document"],
        revision=int(stored["revision"]),
        content_hash=str(stored["content_hash"]),
    )
    updated = _save_portable_projection(updated, sessions=sessions)
    diagnostics = getattr(gateway, "last_structured_diagnostics", None)
    return updated, {
        "mode": "postgresql_authority",
        "persisted": True,
        "campaign_id": campaign_id,
        "revision": int(stored["revision"]),
        "content_hash": str(stored["content_hash"]),
        "document_id": persisted_document_id,
        "kind": kind,
        "definition": definition,
        "structured_contract": "rpg.runtime_materialization.proposal.v2",
        "structured_diagnostics": (
            diagnostics.as_dict() if diagnostics is not None else {}
        ),
        "mechanics_catalog_revision": int(stored["revision"]),
        "world_revision_mutated": False,
    }
