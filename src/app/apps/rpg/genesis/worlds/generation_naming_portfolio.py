"""World-level entity naming concentration and acronym budget certification."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_DEFAULT_POLICY = {
    "dominant_term_ratio": 0.5,
    "dominant_term_min_entities": 4,
    "dominant_term_min_topics": 2,
    "minimum_entities_for_acronym_budget": 8,
    "maximum_acronym_entity_ratio": 0.4,
    "maximum_unique_acronym_ratio": 0.25,
    "dominant_acronym_ratio": 0.5,
    "dominant_acronym_min_entities": 3,
}
_STOP_TERMS = {
    "the",
    "of",
    "and",
    "for",
    "from",
    "with",
    "into",
    "upon",
    "under",
    "over",
    "district",
    "city",
    "region",
    "council",
    "guild",
    "corporation",
    "corp",
    "company",
    "syndicate",
    "network",
    "system",
    "station",
    "academy",
    "institute",
    "order",
    "alliance",
    "union",
    "market",
    "road",
    "gate",
    "tower",
    "facility",
    "zone",
    "sector",
    "clan",
    "house",
    "family",
    "temple",
    "church",
    "army",
    "guard",
    "watch",
    "command",
    "project",
    "protocol",
    "initiative",
    "actor",
    "place",
    "group",
    "threat",
    "technology",
    "equipment",
    "culture",
    "event",
    "quest",
    "scenario",
    "seed",
}
_GENERIC_ACRONYMS = {
    "AI",
    "AR",
    "VR",
    "NPC",
    "PC",
    "HQ",
    "CEO",
    "CTO",
    "CFO",
    "COO",
}
_ROMAN_NUMERAL = re.compile(r"^[IVXLCDM]+$")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")


@dataclass(frozen=True)
class NamingOccurrence:
    topic_id: str
    entity_id: str
    name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "topic_id": self.topic_id,
            "entity_id": self.entity_id,
            "name": self.name,
        }


@dataclass(frozen=True)
class NamingPortfolioIssue:
    code: str
    token: str
    count: int
    entity_ratio: float
    topic_count: int
    occurrences: tuple[NamingOccurrence, ...]
    message: str
    budget: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "token": self.token,
            "count": self.count,
            "entity_ratio": self.entity_ratio,
            "topic_count": self.topic_count,
            "occurrences": [row.as_dict() for row in self.occurrences],
            "message": self.message,
            "budget": dict(self.budget),
            "severity": "error",
            "blocking": True,
        }


class NamingPortfolioCompilationError(ValueError):
    def __init__(self, issues: Sequence[NamingPortfolioIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.token}:{issue.count}:{issue.entity_ratio:.4f}"
            for issue in self.issues
        )
        super().__init__("naming_portfolio_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "naming_portfolio_integrity_failed",
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


def _policy(topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    graph = _mapping(topic_graph)
    configured = _mapping(_mapping(graph.get("metadata")).get("naming_portfolio_policy"))
    policy = {**_DEFAULT_POLICY, **configured}
    policy["ignored_terms"] = sorted(
        _STOP_TERMS
        | {
            str(value).strip().casefold()
            for value in configured.get("ignored_terms") or ()
            if str(value).strip()
        }
    )
    policy["ignored_acronyms"] = sorted(
        _GENERIC_ACRONYMS
        | {
            str(value).strip().upper()
            for value in configured.get("ignored_acronyms") or ()
            if str(value).strip()
        }
    )
    return policy


def _entities(topic_rows: Sequence[Mapping[str, Any]]) -> tuple[NamingOccurrence, ...]:
    values: list[NamingOccurrence] = []
    for topic_index, raw_topic in enumerate(topic_rows, start=1):
        topic = _mapping(raw_topic)
        candidate = _candidate(topic)
        topic_id = str(
            topic.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{topic_index}"
        )
        for entity_index, entity in enumerate(_rows(candidate.get("entities")), start=1):
            entity_id = str(entity.get("id") or f"{topic_id}:entity:{entity_index}")
            name = str(entity.get("name") or entity.get("title") or "").strip()
            if name:
                values.append(
                    NamingOccurrence(
                        topic_id=topic_id,
                        entity_id=entity_id,
                        name=name,
                    )
                )
    unique = {
        (row.topic_id, row.entity_id): row for row in values
    }
    return tuple(unique[key] for key in sorted(unique))


def _name_terms(name: str, ignored: set[str]) -> set[str]:
    values: set[str] = set()
    for token in _WORD.findall(name):
        normalised = token.casefold().strip("-'’")
        if (
            len(normalised) < 4
            or normalised in ignored
            or normalised.isdigit()
        ):
            continue
        values.add(normalised)
    return values


def _name_acronyms(name: str, ignored: set[str]) -> set[str]:
    return {
        token
        for token in _ACRONYM.findall(name)
        if token not in ignored
        and not _ROMAN_NUMERAL.fullmatch(token)
        and not token.isdigit()
    }


def _family_declarations(
    topic_graph: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], ...]:
    graph = _mapping(topic_graph)
    metadata = _mapping(graph.get("metadata"))
    families = []
    for index, value in enumerate(metadata.get("naming_families") or (), start=1):
        if not isinstance(value, Mapping):
            continue
        row = dict(value)
        family_id = str(row.get("family_id") or f"family:{index}")
        entity_ids = sorted(
            {str(item) for item in row.get("entity_ids") or () if str(item)}
        )
        terms = sorted(
            {str(item).casefold() for item in row.get("terms") or () if str(item)}
        )
        acronyms = sorted(
            {str(item).upper() for item in row.get("acronyms") or () if str(item)}
        )
        if entity_ids and (terms or acronyms):
            families.append(
                {
                    "family_id": family_id,
                    "entity_ids": entity_ids,
                    "terms": terms,
                    "acronyms": acronyms,
                }
            )
    return tuple(sorted(families, key=lambda row: str(row["family_id"])))


def _declared_family(
    *,
    token: str,
    occurrences: Sequence[NamingOccurrence],
    families: Sequence[Mapping[str, Any]],
    acronym: bool,
) -> str:
    entity_ids = {row.entity_id for row in occurrences}
    field = "acronyms" if acronym else "terms"
    for family in families:
        declared_tokens = {str(value) for value in family.get(field) or ()}
        declared_entities = {str(value) for value in family.get("entity_ids") or ()}
        if token in declared_tokens and entity_ids.issubset(declared_entities):
            return str(family.get("family_id") or "")
    return ""


def _occurrence_map(
    entities: Sequence[NamingOccurrence],
    *,
    extractor: Any,
) -> dict[str, tuple[NamingOccurrence, ...]]:
    values: dict[str, list[NamingOccurrence]] = {}
    for entity in entities:
        for token in extractor(entity.name):
            values.setdefault(str(token), []).append(entity)
    return {
        token: tuple(
            sorted(rows, key=lambda row: (row.topic_id, row.entity_id, row.name))
        )
        for token, rows in sorted(values.items())
    }


def naming_portfolio_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[NamingPortfolioIssue, ...]:
    """Detect undeclared dominant terms and excessive acronym density."""

    policy = _policy(topic_graph)
    entities = _entities(topic_rows)
    total = len(entities)
    if total == 0:
        return ()
    families = _family_declarations(topic_graph)
    ignored_terms = set(policy["ignored_terms"])
    ignored_acronyms = set(policy["ignored_acronyms"])
    term_map = _occurrence_map(
        entities,
        extractor=lambda name: _name_terms(name, ignored_terms),
    )
    acronym_map = _occurrence_map(
        entities,
        extractor=lambda name: _name_acronyms(name, ignored_acronyms),
    )
    issues: list[NamingPortfolioIssue] = []

    dominant_ratio = float(policy["dominant_term_ratio"])
    dominant_min = int(policy["dominant_term_min_entities"])
    dominant_topics = int(policy["dominant_term_min_topics"])
    for token, occurrences in term_map.items():
        ratio = len(occurrences) / total
        topic_count = len({row.topic_id for row in occurrences})
        if (
            len(occurrences) < dominant_min
            or ratio <= dominant_ratio
            or topic_count < dominant_topics
            or _declared_family(
                token=token,
                occurrences=occurrences,
                families=families,
                acronym=False,
            )
        ):
            continue
        issues.append(
            NamingPortfolioIssue(
                code="dominant_undeclared_naming_term",
                token=token,
                count=len(occurrences),
                entity_ratio=round(ratio, 6),
                topic_count=topic_count,
                occurrences=occurrences,
                message=(
                    "One undeclared naming term dominates unrelated canonical entities."
                ),
                budget={
                    "maximum_ratio": dominant_ratio,
                    "minimum_entities": dominant_min,
                    "minimum_topics": dominant_topics,
                },
            )
        )

    acronym_ratio = float(policy["dominant_acronym_ratio"])
    acronym_min = int(policy["dominant_acronym_min_entities"])
    for token, occurrences in acronym_map.items():
        ratio = len(occurrences) / total
        topic_count = len({row.topic_id for row in occurrences})
        if (
            len(occurrences) < acronym_min
            or ratio <= acronym_ratio
            or _declared_family(
                token=token,
                occurrences=occurrences,
                families=families,
                acronym=True,
            )
        ):
            continue
        issues.append(
            NamingPortfolioIssue(
                code="dominant_undeclared_acronym",
                token=token,
                count=len(occurrences),
                entity_ratio=round(ratio, 6),
                topic_count=topic_count,
                occurrences=occurrences,
                message=(
                    "One undeclared acronym dominates canonical entity names."
                ),
                budget={
                    "maximum_ratio": acronym_ratio,
                    "minimum_entities": acronym_min,
                },
            )
        )

    minimum_budget_size = int(policy["minimum_entities_for_acronym_budget"])
    if total >= minimum_budget_size:
        acronym_entities = {
            row.entity_id
            for occurrences in acronym_map.values()
            for row in occurrences
        }
        acronym_entity_ratio = len(acronym_entities) / total
        maximum_entity_ratio = float(policy["maximum_acronym_entity_ratio"])
        if acronym_entity_ratio > maximum_entity_ratio:
            affected = tuple(
                row for row in entities if row.entity_id in acronym_entities
            )
            issues.append(
                NamingPortfolioIssue(
                    code="acronym_entity_budget_exceeded",
                    token="*",
                    count=len(acronym_entities),
                    entity_ratio=round(acronym_entity_ratio, 6),
                    topic_count=len({row.topic_id for row in affected}),
                    occurrences=affected,
                    message=(
                        "Too many canonical entities use acronyms in their names."
                    ),
                    budget={
                        "maximum_ratio": maximum_entity_ratio,
                        "minimum_world_entities": minimum_budget_size,
                    },
                )
            )
        maximum_unique_ratio = float(policy["maximum_unique_acronym_ratio"])
        maximum_unique = max(1, math.ceil(total * maximum_unique_ratio))
        if len(acronym_map) > maximum_unique:
            affected_ids = {
                row.entity_id
                for occurrences in acronym_map.values()
                for row in occurrences
            }
            affected = tuple(
                row for row in entities if row.entity_id in affected_ids
            )
            issues.append(
                NamingPortfolioIssue(
                    code="unique_acronym_budget_exceeded",
                    token="*",
                    count=len(acronym_map),
                    entity_ratio=round(len(acronym_map) / total, 6),
                    topic_count=len({row.topic_id for row in affected}),
                    occurrences=affected,
                    message=(
                        "The world introduces more unique naming acronyms than its budget allows."
                    ),
                    budget={
                        "maximum_unique_acronyms": maximum_unique,
                        "maximum_unique_ratio": maximum_unique_ratio,
                        "minimum_world_entities": minimum_budget_size,
                    },
                )
            )

    unique = {
        (issue.code, issue.token): issue for issue in issues
    }
    return tuple(unique[key] for key in sorted(unique))


def naming_portfolio_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    policy = _policy(topic_graph)
    entities = _entities(topic_rows)
    issues = naming_portfolio_issues(topic_rows, topic_graph)
    ignored_terms = set(policy["ignored_terms"])
    ignored_acronyms = set(policy["ignored_acronyms"])
    term_map = _occurrence_map(
        entities,
        extractor=lambda name: _name_terms(name, ignored_terms),
    )
    acronym_map = _occurrence_map(
        entities,
        extractor=lambda name: _name_acronyms(name, ignored_acronyms),
    )
    return {
        "schema_version": "rpg_world_naming_portfolio_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "policy": policy,
        "declared_families": list(_family_declarations(topic_graph)),
        "checks": {
            "entity_count": len(entities),
            "tracked_term_count": len(term_map),
            "tracked_acronym_count": len(acronym_map),
            "acronym_entity_count": len(
                {
                    row.entity_id
                    for occurrences in acronym_map.values()
                    for row in occurrences
                }
            ),
        },
    }


def require_valid_naming_portfolio(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = naming_portfolio_issues(topic_rows, topic_graph)
    if issues:
        raise NamingPortfolioCompilationError(issues)
    return naming_portfolio_report(topic_rows, topic_graph)


__all__ = [
    "NamingOccurrence",
    "NamingPortfolioCompilationError",
    "NamingPortfolioIssue",
    "naming_portfolio_issues",
    "naming_portfolio_report",
    "require_valid_naming_portfolio",
]
