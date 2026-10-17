"""Detect high-confidence entity identity leakage in self-owned generated content."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

_IDENTITY_FIELDS = {
    "id",
    "entity_id",
    "topic_id",
    "manifest_slot_id",
    "slot_id",
    "kind",
    "type",
    "visibility",
    "status",
    "schema_version",
    "name",
    "title",
    "short_name",
    "slug",
    "aliases",
    "alternate_names",
    "aka",
    "also_known_as",
    "pronouns",
    "pronoun",
    "gender",
    "sex",
}
_PRESENTATION_FIELDS = {
    "dossier",
    "short_summary",
    "summary",
    "summary_120",
    "summary_500",
    "full_text",
    "keywords",
}
_RELATIONAL_FIELD_MARKERS = {
    "relationship",
    "relationships",
    "relations",
    "connection",
    "connections",
    "allies",
    "rivals",
    "owners",
    "ownership",
    "leadership",
    "members",
    "membership",
    "inhabitants",
    "people",
    "cast",
    "actors",
    "contacts",
    "known_by",
}
_RELATIONAL_SECTION_MARKERS = {
    "relationships",
    "relations",
    "connections",
    "allies",
    "rivals",
    "leadership",
    "inhabitants",
    "people",
    "cast",
    "actors",
    "knowledge",
    "secrets",
    "rumors",
    "rumours",
    "quotes",
}
_SELF_PRONOUN_FIELDS = {
    "description",
    "overview",
    "appearance",
    "personality",
    "backstory",
    "goal",
    "goals",
    "dependency",
    "current_pressure",
    "next_action",
    "function",
    "former_purpose",
    "current_hazard",
    "scarcity",
    "failure_effect",
    "capability",
    "cost",
    "failure_mode",
    "behaviour",
    "behavior",
    "weaknesses",
    "ideology",
    "methods",
    "situation",
    "current_situation",
    "origin",
    "origins",
    "geography",
    "territory",
    "distinction",
}
_SELF_SUBJECT_VERBS = (
    "is",
    "was",
    "has",
    "had",
    "seeks",
    "wants",
    "intends",
    "aims",
    "depends",
    "faces",
    "controls",
    "operates",
    "serves",
    "defines",
    "follows",
    "uses",
    "needs",
    "plans",
    "acts",
    "works",
    "believes",
    "knows",
    "can",
    "will",
    "must",
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_OPTIONAL_LABEL = r"(?:[A-Za-z][A-Za-z0-9 _-]{0,30}:\s*)?"
_PRONOUN_PATTERN = re.compile(
    rf"^\s*{_OPTIONAL_LABEL}(?P<pronoun>he|she|they|it|his|her|their|its)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EntityIdentityContaminationIssue:
    code: str
    topic_id: str
    entity_id: str
    path: str
    field_id: str
    evidence: str
    message: str
    foreign_entity_id: str = ""
    foreign_name: str = ""
    expected_pronouns: tuple[str, ...] = ()
    observed_pronoun: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "entity_id": self.entity_id,
            "path": self.path,
            "field_id": self.field_id,
            "evidence": self.evidence,
            "message": self.message,
            "foreign_entity_id": self.foreign_entity_id,
            "foreign_name": self.foreign_name,
            "expected_pronouns": list(self.expected_pronouns),
            "observed_pronoun": self.observed_pronoun,
            "severity": "error",
            "blocking": True,
        }


class EntityIdentityContaminationCompilationError(ValueError):
    def __init__(self, issues: Sequence[EntityIdentityContaminationIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.topic_id}:{issue.entity_id}:{issue.path}:"
            f"{issue.foreign_entity_id or issue.observed_pronoun}"
            for issue in self.issues
        )
        super().__init__("entity_identity_contamination_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "entity_identity_contamination_failed",
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("candidate", "content"):
        value = row.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return dict(row)


def _normalise_name(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _entity_aliases(entity: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for field in ("name", "title", "short_name", "slug"):
        rendered = str(entity.get(field) or "").strip()
        if rendered:
            values.append(rendered)
    for field in ("aliases", "alternate_names", "aka", "also_known_as"):
        value = entity.get(field)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            values.extend(str(item).strip() for item in value if str(item).strip())
    return tuple(dict.fromkeys(values))


def _registry(
    topic_rows: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, tuple[str, str]],
    dict[str, tuple[str, ...]],
]:
    candidates: dict[str, set[tuple[str, str]]] = {}
    own_aliases: dict[str, tuple[str, ...]] = {}
    for raw_topic in topic_rows:
        candidate = _candidate(_mapping(raw_topic))
        for entity in _rows(candidate.get("entities")):
            entity_id = str(entity.get("id") or "").strip()
            if not entity_id:
                continue
            aliases = _entity_aliases(entity)
            own_aliases[entity_id] = aliases
            for alias in aliases:
                normalised = _normalise_name(alias)
                if not normalised or len(normalised) < 4:
                    continue
                candidates.setdefault(normalised, set()).add((entity_id, alias))
    unique = {
        alias: next(iter(values))
        for alias, values in candidates.items()
        if len({entity_id for entity_id, _name in values}) == 1
    }
    return unique, own_aliases


def _profile_fields(
    topic_graph: Mapping[str, Any] | None,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    graph = _mapping(topic_graph)
    self_fields: dict[str, set[str]] = {}
    reference_fields: dict[str, set[str]] = {}
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        metadata = _mapping(node.get("metadata"))
        for definition in _rows(metadata.get("field_definitions")):
            field_id = str(definition.get("field_id") or "")
            value_type = str(definition.get("value_type") or "")
            if not field_id:
                continue
            if value_type in {"entity_ref", "entity_ref_list"}:
                reference_fields.setdefault(topic_id, set()).add(field_id)
            elif value_type in {"string", "structured_object"}:
                self_fields.setdefault(topic_id, set()).add(field_id)
    return self_fields, reference_fields


def _is_relational_field(field_id: str) -> bool:
    lowered = field_id.casefold()
    return (
        lowered in _RELATIONAL_FIELD_MARKERS
        or any(marker in lowered for marker in ("relationship", "connection"))
        or lowered.endswith("_ids")
        or lowered.endswith("_id")
    )


def _fallback_self_fields(entity: Mapping[str, Any]) -> set[str]:
    return {
        str(field_id)
        for field_id, value in entity.items()
        if str(field_id) not in _IDENTITY_FIELDS
        and str(field_id) not in _PRESENTATION_FIELDS
        and not _is_relational_field(str(field_id))
        and isinstance(value, (str, Mapping, list, tuple))
    }


def _walk_strings(value: Any, *, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        if value.strip():
            yield path, value.strip()
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk_strings(item, path=f"{path}/{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            yield from _walk_strings(item, path=f"{path}/{index}")


def _contains_alias(text: str, aliases: Sequence[str]) -> bool:
    return any(
        re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text, re.IGNORECASE)
        for alias in aliases
        if alias
    )


def _foreign_subject_pattern(alias: str) -> re.Pattern[str]:
    verbs = "|".join(re.escape(value) for value in _SELF_SUBJECT_VERBS)
    return re.compile(
        rf"^\s*{_OPTIONAL_LABEL}(?:the\s+)?{re.escape(alias)}"
        rf"(?:(?:'s|’s)\s+\w+|\s+(?:{verbs})\b)",
        re.IGNORECASE,
    )


def _foreign_identity_issue(
    *,
    topic_id: str,
    entity_id: str,
    field_id: str,
    path: str,
    text: str,
    registry: Mapping[str, tuple[str, str]],
    own_aliases: Sequence[str],
) -> EntityIdentityContaminationIssue | None:
    if _contains_alias(text, own_aliases):
        return None
    for _normalised, (foreign_id, foreign_name) in registry.items():
        if foreign_id == entity_id:
            continue
        if _foreign_subject_pattern(foreign_name).search(text):
            return EntityIdentityContaminationIssue(
                code="foreign_entity_identity_leak",
                topic_id=topic_id,
                entity_id=entity_id,
                path=path,
                field_id=field_id,
                evidence=text[:300],
                foreign_entity_id=foreign_id,
                foreign_name=foreign_name,
                message=(
                    "Self-owned content is grammatically owned by another canonical "
                    "entity while the current entity is absent."
                ),
            )
    return None


def _pronoun_group(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        rendered = " ".join(str(item) for item in value.values())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rendered = " ".join(str(item) for item in value)
    else:
        rendered = str(value or "")
    tokens = set(re.findall(r"[a-z]+", rendered.casefold()))
    groups = []
    for group in (
        ("he", "him", "his"),
        ("she", "her", "hers"),
        ("they", "them", "their", "theirs"),
        ("it", "its"),
    ):
        if tokens.intersection(group):
            groups.append(group)
    return groups[0] if len(groups) == 1 else ()


def _entity_pronouns(entity: Mapping[str, Any]) -> tuple[str, ...]:
    for field in ("pronouns", "pronoun"):
        group = _pronoun_group(entity.get(field))
        if group:
            return group
    gender = str(entity.get("gender") or entity.get("sex") or "").casefold()
    if gender in {"male", "man", "masculine"}:
        return ("he", "him", "his")
    if gender in {"female", "woman", "feminine"}:
        return ("she", "her", "hers")
    if gender in {"nonbinary", "non-binary", "neutral"}:
        return ("they", "them", "their", "theirs")
    return ()


def _pronoun_issue(
    *,
    topic_id: str,
    entity_id: str,
    field_id: str,
    path: str,
    text: str,
    expected: tuple[str, ...],
    all_aliases: Sequence[str],
) -> EntityIdentityContaminationIssue | None:
    if not expected or field_id.casefold() not in _SELF_PRONOUN_FIELDS:
        return None
    if _contains_alias(text, all_aliases):
        return None
    match = _PRONOUN_PATTERN.search(text)
    if match is None:
        return None
    observed = str(match.group("pronoun") or "").casefold()
    canonical_observed = {
        "his": "he",
        "her": "she",
        "their": "they",
        "its": "it",
    }.get(observed, observed)
    canonical_expected = {
        "him": "he",
        "his": "he",
        "her": "she",
        "hers": "she",
        "them": "they",
        "their": "they",
        "theirs": "they",
        "its": "it",
    }.get(expected[0], expected[0])
    if canonical_observed == canonical_expected:
        return None
    return EntityIdentityContaminationIssue(
        code="pronoun_identity_mismatch",
        topic_id=topic_id,
        entity_id=entity_id,
        path=path,
        field_id=field_id,
        evidence=text[:300],
        expected_pronouns=expected,
        observed_pronoun=observed,
        message=(
            "Self-owned content begins with a pronoun that conflicts with the "
            "entity's explicit pronoun identity."
        ),
    )


def _dossier_sources(
    entity: Mapping[str, Any],
    *,
    entity_index: int,
) -> Iterable[tuple[str, str, str]]:
    dossier = _mapping(entity.get("dossier"))
    for section_index, section in enumerate(_rows(dossier.get("sections"))):
        field_id = str(section.get("id") or section.get("title") or "section")
        tokens = set(re.findall(r"[a-z0-9]+", field_id.casefold()))
        if tokens.intersection(_RELATIONAL_SECTION_MARKERS):
            continue
        paragraphs = section.get("paragraphs")
        if isinstance(paragraphs, str):
            paragraphs = [paragraphs]
        if not isinstance(paragraphs, Sequence) or isinstance(paragraphs, (str, bytes)):
            continue
        for paragraph_index, paragraph in enumerate(paragraphs):
            text = str(paragraph or "").strip()
            if text:
                yield (
                    field_id,
                    f"/entities/{entity_index}/dossier/sections/{section_index}/paragraphs/{paragraph_index}",
                    text,
                )


def entity_identity_contamination_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[EntityIdentityContaminationIssue, ...]:
    """Find foreign self-subjects and explicit-pronoun contradictions."""

    registry, own_alias_map = _registry(topic_rows)
    all_aliases = tuple(name for _entity_id, name in registry.values())
    profile_self_fields, reference_fields = _profile_fields(topic_graph)
    issues: list[EntityIdentityContaminationIssue] = []
    for topic_index, raw_topic in enumerate(topic_rows, start=1):
        topic = _mapping(raw_topic)
        candidate = _candidate(topic)
        topic_id = str(
            topic.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{topic_index}"
        )
        for entity_index, entity in enumerate(_rows(candidate.get("entities"))):
            entity_id = str(entity.get("id") or f"{topic_id}:entity:{entity_index + 1}")
            own_aliases = own_alias_map.get(entity_id, ())
            expected_pronouns = _entity_pronouns(entity)
            configured = profile_self_fields.get(topic_id)
            fields = set(configured or _fallback_self_fields(entity))
            fields.difference_update(reference_fields.get(topic_id, set()))
            fields = {
                field_id
                for field_id in fields
                if field_id not in _IDENTITY_FIELDS
                and field_id not in _PRESENTATION_FIELDS
                and not _is_relational_field(field_id)
            }
            for field_id in sorted(fields):
                if field_id not in entity:
                    continue
                for path, text in _walk_strings(
                    entity.get(field_id),
                    path=f"/entities/{entity_index}/{field_id}",
                ):
                    sentences = tuple(
                        sentence.strip()
                        for sentence in _SENTENCE.split(text)
                        if sentence.strip()
                    ) or (text,)
                    for sentence_index, sentence in enumerate(sentences):
                        sentence_path = (
                            path
                            if len(sentences) == 1
                            else f"{path}#sentence:{sentence_index + 1}"
                        )
                        foreign = _foreign_identity_issue(
                            topic_id=topic_id,
                            entity_id=entity_id,
                            field_id=field_id,
                            path=sentence_path,
                            text=sentence,
                            registry=registry,
                            own_aliases=own_aliases,
                        )
                        if foreign is not None:
                            issues.append(foreign)
                        pronoun = _pronoun_issue(
                            topic_id=topic_id,
                            entity_id=entity_id,
                            field_id=field_id,
                            path=sentence_path,
                            text=sentence,
                            expected=expected_pronouns,
                            all_aliases=all_aliases,
                        )
                        if pronoun is not None:
                            issues.append(pronoun)
            for field_id, path, text in _dossier_sources(
                entity,
                entity_index=entity_index,
            ):
                sentences = tuple(
                    sentence.strip()
                    for sentence in _SENTENCE.split(text)
                    if sentence.strip()
                ) or (text,)
                for sentence_index, sentence in enumerate(sentences):
                    sentence_path = (
                        path
                        if len(sentences) == 1
                        else f"{path}#sentence:{sentence_index + 1}"
                    )
                    foreign = _foreign_identity_issue(
                        topic_id=topic_id,
                        entity_id=entity_id,
                        field_id=field_id,
                        path=sentence_path,
                        text=sentence,
                        registry=registry,
                        own_aliases=own_aliases,
                    )
                    if foreign is not None:
                        issues.append(foreign)
                    pronoun = _pronoun_issue(
                        topic_id=topic_id,
                        entity_id=entity_id,
                        field_id=field_id,
                        path=sentence_path,
                        text=sentence,
                        expected=expected_pronouns,
                        all_aliases=all_aliases,
                    )
                    if pronoun is not None:
                        issues.append(pronoun)
    unique = {
        (
            issue.code,
            issue.topic_id,
            issue.entity_id,
            issue.path,
            issue.foreign_entity_id,
            issue.observed_pronoun,
        ): issue
        for issue in issues
    }
    return tuple(unique[key] for key in sorted(unique))


def entity_identity_contamination_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = entity_identity_contamination_issues(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_entity_identity_contamination_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "foreign_identity_leak_count": sum(
                1 for issue in issues if issue.code == "foreign_entity_identity_leak"
            ),
            "pronoun_identity_mismatch_count": sum(
                1 for issue in issues if issue.code == "pronoun_identity_mismatch"
            ),
        },
    }


def require_no_entity_identity_contamination(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = entity_identity_contamination_issues(topic_rows, topic_graph)
    if issues:
        raise EntityIdentityContaminationCompilationError(issues)
    return entity_identity_contamination_report(topic_rows, topic_graph)


__all__ = [
    "EntityIdentityContaminationCompilationError",
    "EntityIdentityContaminationIssue",
    "entity_identity_contamination_issues",
    "entity_identity_contamination_report",
    "require_no_entity_identity_contamination",
]
