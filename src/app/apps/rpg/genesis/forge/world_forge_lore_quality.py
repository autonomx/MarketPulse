"""Retryable quality contract for live provider-authored World Forge lore.

Structured facts own simulation truth.  This module verifies that the provider also
returned readable, topic-appropriate prose instead of allowing terse fields, generic
filler, or template text to reach the authoring pages.  It never writes replacement
lore; callers must retry the configured provider or fail the topic.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic
from .world_forge_integrity import WorldForgeIntegrityError, WorldForgeIntegrityIssue

_WORD = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?][\"'’)]?$", re.UNICODE)
_FIELD_LABEL = re.compile(
    r"^\s*(?:goal|current pressure|dependency|identity|boundaries|next action|"
    r"reaction conditions?|knowledge limits?|observable evidence|resources?|"
    r"capabilities?|weaknesses?|objectives?|stakes?|rewards?|details?|consequence)\s*:",
    re.IGNORECASE,
)
_STOP_WORDS = frozenset(
    {
        "about", "after", "again", "against", "also", "and", "are", "because",
        "before", "being", "between", "from", "have", "into", "only", "other",
        "their", "there", "these", "they", "this", "through", "under", "when",
        "where", "which", "while", "with", "world", "would", "could", "should",
        "than", "that", "then", "them", "each", "such", "will", "does", "not",
    }
)
_GENERIC_FILLER = (
    "deterministic profile fixture canon",
    "deterministic world tick",
    "every listed value is structured",
    "within the wider",
    "for a lorekeeper",
    "the concerns gathered under",
    "the record also preserves",
    "meaningful material for play",
    "future additions should deepen",
    "this entry is established canon",
    "grounded in the established setting",
    "a specific institution, material, and consequence",
    "ignoring this",
)


@dataclass(frozen=True)
class LoreQualityContract:
    minimum_words: int
    minimum_paragraph_words: int
    minimum_summary_words: int
    required_sections: tuple[str, ...]


_CONTRACTS: Mapping[str, LoreQualityContract] = {
    "setting_rules": LoreQualityContract(
        500, 24, 18,
        ("overview", "foundations", "lived-experience", "boundaries", "consequences"),
    ),
    "history_timeline": LoreQualityContract(
        500, 24, 18,
        ("overview", "chronology", "causes", "turning-points", "consequences", "legacy"),
    ),
    "regions": LoreQualityContract(
        500, 24, 18,
        ("overview", "geography", "identity", "inhabitants", "powers", "conflicts", "landmarks"),
    ),
    "places": LoreQualityContract(
        500, 24, 18,
        ("overview", "atmosphere", "history", "function", "inhabitants", "dangers", "hooks", "connections"),
    ),
    "groups": LoreQualityContract(
        500, 24, 18,
        ("overview", "origins", "ideology", "organisation", "leadership", "resources", "relations", "current-objective", "methods"),
    ),
    "cultures": LoreQualityContract(
        500, 24, 18,
        ("overview", "origins", "values", "customs", "social-structure", "internal-tensions", "relations"),
    ),
    "actors": LoreQualityContract(
        500, 24, 18,
        ("overview", "appearance", "personality", "backstory", "goals", "relationships", "knowledge", "speech", "current-situation"),
    ),
    "networks": LoreQualityContract(
        500, 24, 18,
        ("overview", "architecture", "access", "control", "security", "culture", "consequences", "connections"),
    ),
    "technology_augmentations": LoreQualityContract(
        350, 24, 18,
        ("overview", "operation", "capabilities", "costs", "risks", "availability", "social-impact", "connections"),
    ),
    "equipment_vehicles": LoreQualityContract(
        350, 24, 18,
        ("overview", "appearance", "function", "operation", "availability", "limitations", "ownership", "connections"),
    ),
    "roles_archetypes": LoreQualityContract(
        350, 24, 18,
        ("overview", "social-role", "training", "capabilities", "progression", "equipment", "limitations"),
    ),
    "threats": LoreQualityContract(
        350, 24, 18,
        ("overview", "appearance", "behaviour", "capabilities", "weaknesses", "habitat", "encounter-use", "connections"),
    ),
    "economy_law": LoreQualityContract(
        500, 24, 18,
        ("overview", "economy", "services", "laws", "enforcement", "institutions", "failure-effects", "player-impact"),
    ),
    "pressures": LoreQualityContract(
        500, 24, 18,
        ("overview", "background", "involved-parties", "current-state", "escalation", "evidence", "choices", "consequences"),
    ),
    "quests": LoreQualityContract(
        350, 24, 18,
        ("overview", "background", "objectives", "people", "locations", "complications", "outcomes"),
    ),
    "encounter_seeds": LoreQualityContract(
        250, 24, 16,
        ("overview", "setup", "actors", "complications", "escalation", "outcomes"),
    ),
    "opening_threads": LoreQualityContract(
        250, 24, 16,
        ("overview", "initial-evidence", "involved-parties", "choices", "escalation", "aftermath"),
    ),
    "opening_scenarios": LoreQualityContract(
        350, 24, 18,
        ("overview", "opening", "cast", "locations", "beats", "choices", "resources", "stakes"),
    ),
}
_DEFAULT_CONTRACT = LoreQualityContract(
    350,
    24,
    18,
    ("overview", "context", "details", "connections"),
)


def _normalized_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def _word_count(value: Any) -> int:
    return len(_WORD.findall(str(value or "")))


def _render(value: Any) -> str:
    if value in (None, "", [], (), {}):
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in (word.casefold() for word in _WORD.findall(_render(value)))
        if len(token) > 2 and token not in _STOP_WORDS
    }


def _entity_id(entity: Mapping[str, Any], index: int) -> str:
    return str(entity.get("id") or entity.get("entity_id") or f"entity:{index}")


def _provider_generated(topic: GeneratedTopic) -> bool:
    return str(dict(topic.provenance).get("generator") or "").startswith(
        "structured_world_forge_provider_"
    )


def _contract(node: CampaignTopicNode) -> LoreQualityContract:
    default = _CONTRACTS.get(node.topic_id, _DEFAULT_CONTRACT)
    raw = node.metadata.get("lore_quality")
    if not isinstance(raw, Mapping):
        return default
    try:
        minimum_words = max(40, int(raw.get("minimum_words") or default.minimum_words))
        minimum_paragraph_words = max(
            12,
            int(raw.get("minimum_paragraph_words") or default.minimum_paragraph_words),
        )
        minimum_summary_words = max(
            8,
            int(raw.get("minimum_summary_words") or default.minimum_summary_words),
        )
    except (TypeError, ValueError):
        return default
    required = raw.get("required_sections")
    required_sections = (
        tuple(_normalized_id(item) for item in required if _normalized_id(item))
        if isinstance(required, Sequence) and not isinstance(required, (str, bytes))
        else default.required_sections
    )
    return LoreQualityContract(
        minimum_words,
        minimum_paragraph_words,
        minimum_summary_words,
        required_sections,
    )


def lore_quality_contract(node: CampaignTopicNode) -> dict[str, Any]:
    """Return the provider-facing prose contract for diagnostics and prompts."""

    value = _contract(node)
    return {
        "minimum_words": value.minimum_words,
        "minimum_paragraph_words": value.minimum_paragraph_words,
        "minimum_summary_words": value.minimum_summary_words,
        "required_sections": list(value.required_sections),
        "rules": [
            "Write all substantive information as titled sections containing complete paragraphs.",
            "Do not use field-label fragments, JSON-like prose, generic filler, or template language.",
            "Explain every required structured field in the prose; quick facts alone are insufficient.",
            "Keep each entity distinct and grounded in the approved world brief and dependencies.",
        ],
    }


def _issue(
    code: str,
    node: CampaignTopicNode,
    entity_id: str,
    field: str,
    supplied: str,
    message: str,
) -> WorldForgeIntegrityIssue:
    return WorldForgeIntegrityIssue(
        code=code,
        topic_id=node.topic_id,
        item_id=entity_id,
        field=field,
        supplied_value=supplied,
        message=message,
    )


def _required_field_coverage_issues(
    node: CampaignTopicNode,
    entity: Mapping[str, Any],
    entity_id: str,
    dossier_text: str,
    related_ids: set[str],
) -> list[WorldForgeIntegrityIssue]:
    issues: list[WorldForgeIntegrityIssue] = []
    dossier_tokens = _tokens(dossier_text)
    definitions = node.metadata.get("field_definitions")
    if not isinstance(definitions, Sequence) or isinstance(definitions, (str, bytes)):
        return issues
    for definition in definitions:
        if not isinstance(definition, Mapping) or not bool(definition.get("required", False)):
            continue
        field_id = str(definition.get("field_id") or "")
        if field_id in {"name", "title"}:
            continue
        value = entity.get(field_id)
        value_type = str(definition.get("value_type") or "")
        if value_type in {"entity_ref", "entity_ref_list"}:
            references = (
                [str(value)]
                if value_type == "entity_ref"
                else [str(item) for item in value or ()]
            )
            missing = [
                reference
                for reference in references
                if reference and reference not in related_ids and reference not in dossier_text
            ]
            if missing:
                issues.append(
                    _issue(
                        "provider_lore_reference_not_explained",
                        node,
                        entity_id,
                        "dossier",
                        ",".join(missing),
                        f"Explain required field {field_id} in a headed paragraph and include its canonical reference. Regenerate provider lore.",
                    )
                )
            continue
        value_tokens = _tokens(value)
        if not value_tokens:
            continue
        required_overlap = 1 if len(value_tokens) < 5 else 2
        if len(value_tokens & dossier_tokens) < required_overlap:
            issues.append(
                _issue(
                    "provider_lore_field_not_explained",
                    node,
                    entity_id,
                    "dossier",
                    field_id,
                    f"Required structured field {field_id} is not explained in the dossier paragraphs. Regenerate the provider-authored dossier and cover every required field in prose.",
                )
            )
    return issues


def provider_lore_quality_issues(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    campaign_context: Mapping[str, Any] | None = None,
) -> tuple[WorldForgeIntegrityIssue, ...]:
    """Return retryable issues for live lore without synthesising replacement text."""

    if not _provider_generated(topic):
        return ()
    contract = _contract(node)
    context = dict(campaign_context or {})
    world_brief = dict(context.get("world_brief") or {})
    world_title_tokens = _tokens(world_brief.get("title"))
    world_description_tokens = _tokens(world_brief.get("description"))
    issues: list[WorldForgeIntegrityIssue] = []
    prose_fingerprints: dict[str, list[str]] = {}

    for index, entity in enumerate(topic.entities, start=1):
        entity_id = _entity_id(entity, index)
        name = " ".join(str(entity.get("name") or "").split())
        summary = " ".join(str(entity.get("short_summary") or "").split())
        if _word_count(summary) < contract.minimum_summary_words:
            issues.append(
                _issue(
                    "provider_lore_summary_too_short",
                    node,
                    entity_id,
                    "short_summary",
                    str(_word_count(summary)),
                    f"Write a specific two- or three-sentence short summary of at least {contract.minimum_summary_words} words. Regenerate provider lore.",
                )
            )
        if summary and not _SENTENCE_END.search(summary):
            issues.append(
                _issue(
                    "provider_lore_summary_fragment",
                    node,
                    entity_id,
                    "short_summary",
                    summary[-40:],
                    "The short summary must use complete sentences rather than a fragment or field value.",
                )
            )

        dossier = entity.get("dossier")
        if not isinstance(dossier, Mapping):
            continue
        raw_sections = dossier.get("sections")
        sections = (
            [dict(item) for item in raw_sections if isinstance(item, Mapping)]
            if isinstance(raw_sections, Sequence) and not isinstance(raw_sections, (str, bytes))
            else []
        )
        section_ids = [_normalized_id(item.get("id") or item.get("title")) for item in sections]
        missing_sections = [
            section_id
            for section_id in contract.required_sections
            if section_id not in section_ids
        ]
        if missing_sections:
            issues.append(
                _issue(
                    "provider_lore_required_sections_missing",
                    node,
                    entity_id,
                    "dossier",
                    ",".join(missing_sections),
                    "Regenerate the provider-authored dossier with these exact headed sections: "
                    + ", ".join(contract.required_sections)
                    + ". Put substantive information in paragraphs under every header.",
                )
            )
        if len(section_ids) != len(set(section_ids)):
            issues.append(
                _issue(
                    "provider_lore_duplicate_section_heading",
                    node,
                    entity_id,
                    "dossier",
                    ",".join(section_ids),
                    "Every dossier section must have a unique, topic-appropriate heading.",
                )
            )

        paragraphs: list[str] = []
        for section in sections:
            rows = section.get("paragraphs")
            section_paragraphs = (
                [" ".join(str(value).split()) for value in rows if str(value).strip()]
                if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes))
                else []
            )
            if not section_paragraphs:
                issues.append(
                    _issue(
                        "provider_lore_empty_section",
                        node,
                        entity_id,
                        "dossier",
                        str(section.get("id") or section.get("title") or "<unknown>"),
                        "Every headed section must contain at least one substantive paragraph.",
                    )
                )
            for paragraph in section_paragraphs:
                paragraphs.append(paragraph)
                words = _word_count(paragraph)
                if words < contract.minimum_paragraph_words:
                    issues.append(
                        _issue(
                            "provider_lore_paragraph_too_short",
                            node,
                            entity_id,
                            "dossier",
                            str(words),
                            f"Each lore paragraph must contain at least {contract.minimum_paragraph_words} words and develop a complete idea.",
                        )
                    )
                if not _SENTENCE_END.search(paragraph):
                    issues.append(
                        _issue(
                            "provider_lore_paragraph_fragment",
                            node,
                            entity_id,
                            "dossier",
                            paragraph[-40:],
                            "Lore paragraphs must end as complete prose, not truncated fragments.",
                        )
                    )
                if _FIELD_LABEL.search(paragraph) or "detail:" in paragraph.casefold() or "consequence:" in paragraph.casefold():
                    issues.append(
                        _issue(
                            "provider_lore_field_label_fragment",
                            node,
                            entity_id,
                            "dossier",
                            paragraph[:120],
                            "Rewrite field labels and structured objects as natural paragraphs under meaningful headers.",
                        )
                    )
                filler = [phrase for phrase in _GENERIC_FILLER if phrase in paragraph.casefold()]
                if filler:
                    issues.append(
                        _issue(
                            "provider_lore_generic_or_deterministic_filler",
                            node,
                            entity_id,
                            "dossier",
                            ",".join(filler),
                            "Remove generic/template language and write setting-specific provider-authored lore.",
                        )
                    )

        normalized_paragraphs = [" ".join(value.casefold().split()) for value in paragraphs]
        if len(normalized_paragraphs) != len(set(normalized_paragraphs)):
            issues.append(
                _issue(
                    "provider_lore_duplicate_paragraph",
                    node,
                    entity_id,
                    "dossier",
                    "duplicate",
                    "Do not repeat the same paragraph across dossier sections.",
                )
            )
        total_words = sum(_word_count(paragraph) for paragraph in paragraphs)
        if total_words < contract.minimum_words:
            issues.append(
                _issue(
                    "provider_lore_total_too_short",
                    node,
                    entity_id,
                    "dossier",
                    str(total_words),
                    f"Expand the dossier to at least {contract.minimum_words} words of substantive, setting-specific prose across the required headers.",
                )
            )

        related_ids = {
            str(value)
            for value in dossier.get("related_entity_ids") or ()
            if str(value)
        }
        dossier_text = " ".join(paragraphs)
        issues.extend(
            _required_field_coverage_issues(
                node,
                entity,
                entity_id,
                dossier_text,
                related_ids,
            )
        )

        name_tokens = _tokens(name)
        brief_tokens = world_title_tokens | world_description_tokens
        if len(name_tokens) >= 6 and brief_tokens and len(name_tokens & brief_tokens) >= max(4, len(name_tokens) - 2):
            issues.append(
                _issue(
                    "provider_lore_world_brief_echo_name",
                    node,
                    entity_id,
                    "name",
                    name,
                    "Give the entity a concise in-world proper name; do not prepend or repeat the world brief.",
                )
            )

        fingerprint = " ".join(sorted(_tokens(summary + " " + dossier_text)))
        if fingerprint:
            prose_fingerprints.setdefault(fingerprint, []).append(entity_id)

    for entity_ids in prose_fingerprints.values():
        if len(entity_ids) > 1:
            for entity_id in entity_ids:
                issues.append(
                    _issue(
                        "provider_lore_duplicate_entity_prose",
                        node,
                        entity_id,
                        "dossier",
                        ",".join(entity_ids),
                        "Entities must have distinct summaries and dossiers rather than recycled prose.",
                    )
                )
    return tuple(issues)


def require_provider_lore_quality(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    campaign_context: Mapping[str, Any] | None = None,
) -> GeneratedTopic:
    issues = provider_lore_quality_issues(node, topic, campaign_context)
    if issues:
        raise WorldForgeIntegrityError(issues)
    return topic


__all__ = [
    "LoreQualityContract",
    "lore_quality_contract",
    "provider_lore_quality_issues",
    "require_provider_lore_quality",
]
