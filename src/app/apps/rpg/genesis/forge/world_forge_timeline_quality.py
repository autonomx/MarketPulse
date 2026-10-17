"""Quality checks for provider-authored history, era, and calendar lore.

Timeline pages must be more than dated labels.  Each entry should explain when it
happened, what caused it, what occurred, who and where it affected, its immediate
consequences, and the legacy that still matters in the generated world.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic
from .world_forge_integrity import WorldForgeIntegrityIssue

_TIMELINE_TOPIC_IDS = frozenset(
    {"history", "history_timeline", "calendar", "calendar_and_eras"}
)
_WORD = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
_SENTENCE = re.compile(r"[.!?](?:[\"'’)]|\s|$)", re.UNICODE)
_TEMPORAL_FIELDS = (
    "date",
    "date_label",
    "year",
    "era",
    "epoch",
    "period",
    "range",
    "start_year",
    "end_year",
    "season",
    "month",
    "chronology_index",
    "sequence",
)
_HISTORY_REQUIREMENTS: Mapping[str, tuple[str, ...]] = {
    "cause": ("cause", "causes", "origin", "origins", "trigger", "background"),
    "event": ("event", "what_happened", "occurrence", "chronology", "turning-points"),
    "participants": (
        "participants",
        "affected_parties",
        "people",
        "factions",
        "places",
        "participants-and-places",
    ),
    "consequences": (
        "consequence",
        "consequences",
        "impact",
        "aftermath",
        "immediate-consequences",
    ),
    "legacy": ("legacy", "long_term_effects", "long-term-legacy", "modern_relevance"),
}
_CALENDAR_REQUIREMENTS: Mapping[str, tuple[str, ...]] = {
    "timekeeping": ("timekeeping", "calendar_structure", "calendar-structure", "dating_conventions"),
    "cycle": ("cycle", "cycles", "season", "seasons", "month", "months", "era"),
    "observance": ("observance", "observances", "festival", "festivals", "ritual"),
    "social_impact": ("social_impact", "social-impact", "daily_life", "regional_variation"),
}


def _provider_generated(topic: GeneratedTopic) -> bool:
    return str(dict(topic.provenance).get("generator") or "").startswith(
        "structured_world_forge_provider_"
    )


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def _word_count(value: Any) -> int:
    return len(_WORD.findall(str(value or "")))


def _issue(
    code: str,
    node: CampaignTopicNode,
    item_id: str,
    supplied: Any,
    message: str,
) -> WorldForgeIntegrityIssue:
    return WorldForgeIntegrityIssue(
        code=code,
        topic_id=node.topic_id,
        item_id=item_id,
        field="timeline",
        supplied_value=supplied,
        message=message,
    )


def _sections(entity: Mapping[str, Any]) -> list[dict[str, Any]]:
    dossier = entity.get("dossier")
    if not isinstance(dossier, Mapping):
        return []
    raw = dossier.get("sections")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _paragraphs(sections: Sequence[Mapping[str, Any]]) -> list[str]:
    rows: list[str] = []
    for section in sections:
        values = section.get("paragraphs")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        rows.extend(" ".join(str(value).split()) for value in values if str(value).strip())
    return rows


def _temporal_label(entity: Mapping[str, Any]) -> str:
    values = [str(entity.get(field) or "").strip() for field in _TEMPORAL_FIELDS]
    return " · ".join(value for value in values if value)


def _has_requirement(
    entity: Mapping[str, Any],
    section_ids: set[str],
    prose: str,
    aliases: Sequence[str],
) -> bool:
    normalized_keys = {_normalized(key) for key, value in entity.items() if value not in (None, "", [], {})}
    normalized_aliases = {_normalized(alias) for alias in aliases}
    if normalized_keys & normalized_aliases or section_ids & normalized_aliases:
        return True
    words = {word.casefold() for word in _WORD.findall(prose)}
    return any(
        set(_WORD.findall(alias.replace("_", " ").replace("-", " "))) <= words
        for alias in aliases
        if _WORD.findall(alias.replace("_", " ").replace("-", " "))
    )


def _document_text(document: Mapping[str, Any]) -> str:
    return "\n\n".join(
        str(document.get(key) or "").strip()
        for key in ("full_text", "body", "text", "summary")
        if str(document.get(key) or "").strip()
    )


def timeline_lore_quality_issues(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
) -> tuple[WorldForgeIntegrityIssue, ...]:
    """Return soft quality issues for chronological lore.

    These issues contribute to the 0-100 lore score and targeted retries.  They do
    not synthesize replacement text and do not make a structurally valid candidate
    unusable when it is selected as the best result after all attempts.
    """

    if node.topic_id not in _TIMELINE_TOPIC_IDS or not _provider_generated(topic):
        return ()

    issues: list[WorldForgeIntegrityIssue] = []
    calendar = node.topic_id in {"calendar", "calendar_and_eras"}
    requirements = _CALENDAR_REQUIREMENTS if calendar else _HISTORY_REQUIREMENTS
    minimum_words = 180 if calendar else 260
    seen_temporal_labels: dict[str, str] = {}

    if not topic.entities:
        documents = [dict(row) for row in topic.documents if isinstance(row, Mapping)]
        total_text = "\n\n".join(_document_text(row) for row in documents)
        if _word_count(total_text) < (500 if calendar else 900):
            issues.append(
                _issue(
                    "provider_timeline_document_too_short",
                    node,
                    node.topic_id,
                    _word_count(total_text),
                    "Expand the chronological lore into rich headed prose and multiple detailed timeline entries. History should explain causes, events, participants, consequences, and lasting legacy; calendars should explain timekeeping, cycles, observances, and social effects.",
                )
            )
        event_lists = [
            value
            for document in documents
            for key in ("events", "timeline", "eras", "entries", "observances")
            for value in [document.get(key)]
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        ]
        if not event_lists:
            issues.append(
                _issue(
                    "provider_timeline_entries_missing",
                    node,
                    node.topic_id,
                    "none",
                    "Return structured chronological entries with explicit dates or eras in addition to the explanatory paragraphs, so the authoring page can render a real timeline.",
                )
            )
        return tuple(issues)

    for index, entity in enumerate(topic.entities, start=1):
        entity_id = str(entity.get("id") or entity.get("entity_id") or f"timeline:{index}")
        temporal_label = _temporal_label(entity)
        if not temporal_label:
            issues.append(
                _issue(
                    "provider_timeline_temporal_marker_missing",
                    node,
                    entity_id,
                    "missing",
                    "Give every timeline entry an explicit date, year, era, period, season, or ordered chronology index. Avoid vague labels such as 'long ago'.",
                )
            )
        else:
            normalized_label = _normalized(temporal_label)
            prior = seen_temporal_labels.get(normalized_label)
            if prior:
                issues.append(
                    _issue(
                        "provider_timeline_duplicate_temporal_marker",
                        node,
                        entity_id,
                        temporal_label,
                        f"Distinguish this entry chronologically from {prior}; repeated dates or eras need an explicit sequence or narrower date range.",
                    )
                )
            seen_temporal_labels[normalized_label] = entity_id

        sections = _sections(entity)
        section_ids = {
            _normalized(section.get("id") or section.get("title"))
            for section in sections
        }
        paragraphs = _paragraphs(sections)
        prose = " ".join(paragraphs)
        total_words = _word_count(prose)
        if total_words < minimum_words:
            issues.append(
                _issue(
                    "provider_timeline_entry_too_short",
                    node,
                    entity_id,
                    total_words,
                    f"Expand this timeline entry to at least {minimum_words} words of substantive history. A dated label and short summary are not enough.",
                )
            )
        if len(_SENTENCE.findall(prose)) < 5:
            issues.append(
                _issue(
                    "provider_timeline_entry_insufficient_narrative",
                    node,
                    entity_id,
                    len(_SENTENCE.findall(prose)),
                    "Develop the entry as several complete sentences across headed paragraphs, showing a sequence of cause, action, reaction, and lasting consequence.",
                )
            )

        for requirement, aliases in requirements.items():
            if _has_requirement(entity, section_ids, prose, aliases):
                continue
            issues.append(
                _issue(
                    f"provider_timeline_{requirement}_missing",
                    node,
                    entity_id,
                    requirement,
                    "History entries must explain causes, what happened, affected people and places, immediate consequences, and long-term legacy. Calendar entries must explain timekeeping, cycles, observances, and their effect on daily life.",
                )
            )

        if not calendar and not _has_requirement(
            entity,
            section_ids,
            prose,
            ("sources", "uncertainty", "interpretations", "contested-memory"),
        ):
            issues.append(
                _issue(
                    "provider_timeline_sources_or_uncertainty_missing",
                    node,
                    entity_id,
                    "missing",
                    "Include the surviving sources, public memory, propaganda, uncertainty, or competing interpretations that shape how this event is understood in the present.",
                )
            )

    return tuple(issues)


__all__ = ["timeline_lore_quality_issues"]
