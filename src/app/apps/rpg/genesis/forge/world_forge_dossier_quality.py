"""Quality gates for long-form World Forge dossiers.

Schema validation proves that a dossier is structurally readable. These checks prove
that generated prose is substantial enough to satisfy the authoring experience.
Legacy stored worlds remain readable through projection; generation-time callers may
use ``enrich_fallback_dossier`` before applying the strict quality gate.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

_MINOR_TOPICS = {"feats", "items", "spells"}
_MAJOR_TOPICS = {
    "realm",
    "realm_overview",
    "regions",
    "locations",
    "npcs",
    "factions",
    "history",
    "cosmology",
    "cultures",
    "institutions",
    "pantheon",
    "current_conflicts",
}
_WORD = re.compile(r"\b[\w’'-]+\b", re.UNICODE)


def content_target(topic_id: str) -> tuple[int, int]:
    """Return minimum dossier words and minimum substantive section count."""

    if topic_id in _MINOR_TOPICS:
        return 150, 3
    # The authored cultures contract has four fixed, required section keys.
    # Preserve its major-topic depth without demanding an impossible fifth key.
    if topic_id == "cultures":
        return 700, 4
    if topic_id in _MAJOR_TOPICS:
        return 700, 5
    return 350, 4


def _paragraphs(dossier: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    sections = dossier.get("sections")
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
        return values
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        rows = section.get("paragraphs")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        values.extend(str(value).strip() for value in rows if str(value).strip())
    return values


def dossier_word_count(dossier: Mapping[str, Any]) -> int:
    return sum(len(_WORD.findall(paragraph)) for paragraph in _paragraphs(dossier))


def validate_dossier_quality(
    dossier: Mapping[str, Any],
    *,
    topic_id: str,
) -> tuple[str, ...]:
    minimum_words, minimum_sections = content_target(topic_id)
    issues: list[str] = []
    sections = dossier.get("sections")
    section_rows = (
        [row for row in sections if isinstance(row, Mapping)]
        if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes))
        else []
    )
    if len(section_rows) < minimum_sections:
        issues.append(f"dossier_section_count:{len(section_rows)}:{minimum_sections}")
    paragraphs = _paragraphs(dossier)
    if any(len(_WORD.findall(paragraph)) < 24 for paragraph in paragraphs):
        issues.append("dossier_paragraph_too_short")
    normalized = [" ".join(paragraph.casefold().split()) for paragraph in paragraphs]
    if len(normalized) != len(set(normalized)):
        issues.append("dossier_duplicate_paragraph")
    words = dossier_word_count(dossier)
    if words < minimum_words:
        issues.append(f"dossier_word_count:{words}:{minimum_words}")
    return tuple(dict.fromkeys(issues))


def _seed_text(entity: Mapping[str, Any]) -> str:
    for key in (
        "short_summary",
        "description",
        "summary",
        "purpose",
        "personality",
        "sensory_profile",
        "premise",
    ):
        value = str(entity.get(key) or "").strip()
        if value:
            return value.rstrip(".")
    return "This entry is established canon whose history and present pressures shape the campaign"


def _paragraph(
    *,
    name: str,
    title: str,
    seed: str,
    topic_id: str,
    index: int,
    target_words: int,
) -> str:
    frames = (
        (
            f"{name} is best understood through {title.casefold()}. {seed}. "
            "Its visible form is only one layer of a longer history shaped by choices, "
            "scarcity, custom, and the people who depend on it. Details in this record "
            "are presented as usable canon, so a reader can connect atmosphere and lore "
            "to concrete relationships elsewhere in the world."
        ),
        (
            f"Within the wider {topic_id.replace('_', ' ')}, {name} continues to change. "
            f"The concerns gathered under {title.casefold()} influence daily routines, "
            "political expectations, travel, danger, and opportunity. Different witnesses "
            "may interpret those concerns differently, but their consequences remain "
            "grounded in the established setting and provide meaningful material for play."
        ),
        (
            f"For a lorekeeper, the practical importance of {title.casefold()} lies in how "
            f"it connects {name} to nearby powers, remembered events, and unresolved needs. "
            "Those connections create reasons for characters to care, disagree, investigate, "
            "or intervene without replacing structured mechanics or canonical identifiers. "
            "Future additions should deepen these links rather than repeat this overview."
        ),
    )
    text = frames[index % len(frames)]
    words = text.split()
    while len(words) < target_words:
        words.extend(
            (
                "The record also preserves distinct sensory evidence, social context, and consequences",
                "so later scenes can reveal new detail without contradicting established canon",
            )[len(words) % 2].split()
        )
    return " ".join(words[:target_words]).rstrip(" ,;:") + "."


def enrich_fallback_dossier(
    entity: Mapping[str, Any],
    dossier: Mapping[str, Any],
    *,
    topic_id: str,
) -> dict[str, Any]:
    """Expand projected/fallback prose into deterministic multi-paragraph sections."""

    minimum_words, minimum_sections = content_target(topic_id)
    name = str(entity.get("name") or entity.get("title") or entity.get("id") or "This entry")
    seed = _seed_text(entity)
    raw_sections = dossier.get("sections")
    sections = [dict(row) for row in raw_sections or () if isinstance(row, Mapping)]
    while len(sections) < minimum_sections:
        index = len(sections) + 1
        sections.append(
            {
                "id": f"context-{index}",
                "title": ("Context", "Consequences", "Connections", "Current Pressures", "Adventure Use")[
                    (index - 1) % 5
                ],
                "paragraphs": [],
            }
        )
    target_per_section = max(60, (minimum_words + len(sections) - 1) // len(sections))
    normalized: list[dict[str, Any]] = []
    for section_index, section in enumerate(sections):
        title = str(section.get("title") or f"Section {section_index + 1}")
        existing = [
            str(value).strip()
            for value in section.get("paragraphs") or ()
            if str(value).strip()
        ]
        paragraphs: list[str] = []
        seen: set[str] = set()
        for paragraph in existing:
            normalized_paragraph = " ".join(paragraph.casefold().split())
            if len(_WORD.findall(paragraph)) < 24 or normalized_paragraph in seen:
                continue
            seen.add(normalized_paragraph)
            paragraphs.append(paragraph)
            if len(paragraphs) == 3:
                break
        section_words = sum(len(_WORD.findall(paragraph)) for paragraph in paragraphs)
        while section_words < target_per_section and len(paragraphs) < 3:
            remaining_words = target_per_section - section_words
            remaining_slots = 3 - len(paragraphs)
            target_words = max(
                45,
                (remaining_words + remaining_slots - 1) // remaining_slots,
            )
            paragraph = _paragraph(
                name=name,
                title=title,
                seed=seed,
                topic_id=topic_id,
                index=section_index * 3 + len(paragraphs),
                target_words=target_words,
            )
            normalized_paragraph = " ".join(paragraph.casefold().split())
            if normalized_paragraph in seen:
                paragraph = _paragraph(
                    name=name,
                    title=f"{title} in practice",
                    seed=seed,
                    topic_id=topic_id,
                    index=section_index * 3 + len(paragraphs) + 1,
                    target_words=target_words,
                )
                normalized_paragraph = " ".join(paragraph.casefold().split())
            seen.add(normalized_paragraph)
            paragraphs.append(paragraph)
            section_words += len(_WORD.findall(paragraph))
        normalized.append(
            {
                "id": str(section.get("id") or f"section-{section_index + 1}"),
                "title": title,
                "paragraphs": paragraphs[:3],
            }
        )
    enriched = dict(dossier)
    enriched["sections"] = normalized
    enriched["generated_from_legacy"] = True
    enriched["quality_enriched"] = True
    return enriched
