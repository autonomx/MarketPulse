"""Profile-aware semantic quality gates for newly generated world canon."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic

_GENERIC_PHRASES = (
    "a consequential",
    "tied to active world pressures",
    "has a distinct atmosphere",
    "failure changes local power",
    "a rival intervenes",
    "the environment changes",
    "a signature attack",
    "a discoverable behavioral or material weakness",
    "one bounded mechanical advantage",
    "one explicit restriction or tradeoff",
    "advance the unresolved opening conflict",
    "maintain influence over the opening conflict",
    "defined structured canon",
    "defined for",
    "a named regional culture",
    "a region-specific lair",
    "success changes local state",
    "failure advances an opposing clock",
)
_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "against",
        "also",
        "and",
        "are",
        "because",
        "before",
        "being",
        "between",
        "from",
        "have",
        "into",
        "only",
        "other",
        "their",
        "there",
        "these",
        "they",
        "this",
        "through",
        "under",
        "when",
        "where",
        "which",
        "while",
        "with",
        "world",
    }
)
_OPERATIONAL_FIELD_MARKERS = (
    "next_action",
    "next_tick",
    "current_objective",
    "failure_response",
    "reaction_condition",
    "escalation_condition",
    "aftermath",
)
_CAUSAL_FIELD_MARKERS = (
    "dependency",
    "dependencies",
    "resource",
    "pressure",
    "controller",
    "cause",
    "cost",
    "failure",
)
_OBSERVABLE_FIELD_MARKERS = (
    "observable",
    "evidence",
    "sign",
    "rumour",
    "rumor",
    "access_route",
)
_REFERENCE_CONCENTRATION_EXEMPT_ROLES = frozenset(
    {
        "caused_by",
        "formed_by",
        "founded_by",
        "originated_in",
        "origin_region",
        "descended_from",
        "shaped_by",
        "cultural_affiliation",
        "cause",
        "effect",
    }
)


@dataclass(frozen=True)
class SemanticQualityIssue:
    code: str
    topic_id: str
    entity_ids: tuple[str, ...]
    fields: tuple[str, ...]
    reason: str
    severity: str = "error"
    regeneration_scope: str = "entities"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "entity_ids": list(self.entity_ids),
            "fields": list(self.fields),
            "reason": self.reason,
            "severity": self.severity,
            "regeneration_scope": self.regeneration_scope,
        }


@dataclass(frozen=True)
class SemanticQualityReport:
    passed: bool
    issues: tuple[SemanticQualityIssue, ...]
    checks: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [issue.as_dict() for issue in self.issues],
            "checks": dict(self.checks),
        }


class WorldForgeSemanticQualityError(ValueError):
    def __init__(self, report: SemanticQualityReport) -> None:
        self.report = report
        super().__init__(
            "world_forge_semantic_quality_failed:"
            + ";".join(
                f"{issue.code}:{issue.topic_id}:{','.join(issue.entity_ids)}"
                for issue in report.issues
                if issue.severity == "error"
            )
        )


def _definitions(node: CampaignTopicNode) -> tuple[dict[str, Any], ...]:
    value = node.metadata.get("field_definitions")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _entity_id(entity: Mapping[str, Any], index: int) -> str:
    return str(entity.get("id") or entity.get("entity_id") or f"{index}")


def _render(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if value is None:
        return ""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(value)


def _meaningful_tokens(value: Any) -> tuple[str, ...]:
    return tuple(
        token
        for token in re.findall(r"[a-z0-9][a-z0-9'-]+", _render(value).casefold())
        if len(token) > 2 and token not in _STOP_WORDS
    )


def _fingerprint(value: Any) -> str:
    return " ".join(_meaningful_tokens(value))


def _generic_matches(value: Any) -> tuple[str, ...]:
    text = _render(value).casefold()
    return tuple(phrase for phrase in _GENERIC_PHRASES if phrase in text)


def _reference_values(value: Any, value_type: str) -> tuple[str, ...]:
    if value_type == "entity_ref":
        return (str(value).strip(),) if str(value or "").strip() else ()
    if value_type == "entity_ref_list" and isinstance(
        value,
        Sequence,
    ) and not isinstance(value, (str, bytes)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _declared_clusters(
    campaign_context: Mapping[str, Any],
) -> set[tuple[str, str, str]]:
    declarations = campaign_context.get("intentional_reference_clusters")
    if not isinstance(declarations, Sequence) or isinstance(
        declarations,
        (str, bytes),
    ):
        return set()
    result: set[tuple[str, str, str]] = set()
    for declaration in declarations:
        if not isinstance(declaration, Mapping):
            continue
        result.add(
            (
                str(declaration.get("topic_id") or ""),
                str(declaration.get("field") or ""),
                str(declaration.get("entity_id") or ""),
            )
        )
    return result


def _reference_tuple_declared(
    topic_id: str,
    reference_tuple: tuple[tuple[str, tuple[str, ...]], ...],
    declarations: set[tuple[str, str, str]],
) -> bool:
    members = tuple(
        (topic_id, field_id, referenced_id)
        for field_id, referenced_ids in reference_tuple
        for referenced_id in referenced_ids
    )
    return bool(members) and all(member in declarations for member in members)


def _has_marker(field_id: str, markers: tuple[str, ...]) -> bool:
    return any(marker in field_id for marker in markers)


def _has_substantive_value(value: Any) -> bool:
    return value not in (None, "", [], (), {})


def audit_topic_semantic_quality(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    campaign_context: Mapping[str, Any] | None = None,
) -> SemanticQualityReport:
    """Evaluate specificity and operational utility without genre assumptions."""

    definitions = _definitions(node)
    if not definitions or not topic.entities:
        return SemanticQualityReport(
            passed=True,
            issues=(),
            checks={"entities": len(topic.entities), "profile_fields": len(definitions)},
        )
    context = dict(campaign_context or {})
    issues: list[SemanticQualityIssue] = []
    definition_map = {
        str(definition.get("field_id") or ""): definition
        for definition in definitions
    }
    descriptive_fields = tuple(
        field_id
        for field_id, definition in definition_map.items()
        if field_id not in {"name", "title"}
        and str(definition.get("value_type") or "")
        not in {"entity_ref", "entity_ref_list", "boolean", "integer", "number"}
    )
    operational_fields = tuple(
        field_id
        for field_id in definition_map
        if _has_marker(field_id, _OPERATIONAL_FIELD_MARKERS)
    )
    causal_fields = tuple(
        field_id
        for field_id in definition_map
        if _has_marker(field_id, _CAUSAL_FIELD_MARKERS)
    )
    observable_fields = tuple(
        field_id
        for field_id in definition_map
        if _has_marker(field_id, _OBSERVABLE_FIELD_MARKERS)
    )

    fingerprints_by_field: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    reference_counts: dict[str, Counter[str]] = defaultdict(Counter)
    reference_tuples: dict[
        tuple[tuple[str, tuple[str, ...]], ...],
        list[str],
    ] = defaultdict(list)
    generic_total = 0

    for index, entity in enumerate(topic.entities, start=1):
        entity_id = _entity_id(entity, index)
        all_tokens: set[str] = set()
        entity_generic: list[str] = []
        reference_tuple: list[tuple[str, tuple[str, ...]]] = []
        for field_id, definition in definition_map.items():
            value = entity.get(field_id)
            all_tokens.update(_meaningful_tokens(value))
            matches = _generic_matches(value)
            if matches:
                entity_generic.extend(matches)
                generic_total += len(matches)
            value_type = str(definition.get("value_type") or "")
            refs = _reference_values(value, value_type)
            if refs:
                reference_tuple.append((field_id, tuple(sorted(refs))))
                reference_counts[field_id].update(set(refs))
            if field_id in descriptive_fields:
                fingerprint = _fingerprint(value)
                if fingerprint:
                    fingerprints_by_field[field_id][fingerprint].append(entity_id)

        if len(all_tokens) < max(6, len(descriptive_fields) * 2):
            issues.append(
                SemanticQualityIssue(
                    "insufficient_structured_specificity",
                    node.topic_id,
                    (entity_id,),
                    descriptive_fields,
                    "Structured content lacks enough distinct setting-specific detail.",
                    regeneration_scope="entity_fields",
                )
            )
        if entity_generic:
            issues.append(
                SemanticQualityIssue(
                    "generic_fallback_language",
                    node.topic_id,
                    (entity_id,),
                    descriptive_fields,
                    "Detected fallback phrases: "
                    + ", ".join(sorted(set(entity_generic))),
                    regeneration_scope="entity_fields",
                )
            )
        for field_group, code, reason in (
            (
                operational_fields,
                "weak_operational_state",
                "Operational fields must specify a concrete action, trigger, or near-term change.",
            ),
            (
                causal_fields,
                "weak_causal_integration",
                "Causal fields must identify concrete dependencies, pressures, costs, or failure effects.",
            ),
            (
                observable_fields,
                "weak_observable_evidence",
                "Observable fields must describe evidence the player can encounter.",
            ),
        ):
            weak = tuple(
                field_id
                for field_id in field_group
                if _has_substantive_value(entity.get(field_id))
                and len(set(_meaningful_tokens(entity.get(field_id)))) < 3
            )
            if weak:
                issues.append(
                    SemanticQualityIssue(
                        code,
                        node.topic_id,
                        (entity_id,),
                        weak,
                        reason,
                        regeneration_scope="entity_fields",
                    )
                )
        if reference_tuple:
            reference_tuples[tuple(sorted(reference_tuple))].append(entity_id)

    for field_id, fingerprints in fingerprints_by_field.items():
        for fingerprint, entity_ids in fingerprints.items():
            if len(entity_ids) > 1 and len(fingerprint.split()) >= 3:
                issues.append(
                    SemanticQualityIssue(
                        "insufficient_entity_differentiation",
                        node.topic_id,
                        tuple(sorted(entity_ids)),
                        (field_id,),
                        "Multiple entities contain the same substantive structured value.",
                        regeneration_scope="entities",
                    )
                )

    declarations = _declared_clusters(context)
    entity_count = len(topic.entities)
    if entity_count >= 4:
        threshold = max(3, int(entity_count * 0.8 + 0.999))
        for field_id, counts in reference_counts.items():
            semantic_role = str(
                definition_map[field_id].get("semantic_role") or ""
            ).strip()
            if semantic_role in _REFERENCE_CONCENTRATION_EXEMPT_ROLES:
                continue
            for referenced_id, count in counts.items():
                declaration = (node.topic_id, field_id, referenced_id)
                if count >= threshold and declaration not in declarations:
                    issues.append(
                        SemanticQualityIssue(
                            "suspicious_reference_concentration",
                            node.topic_id,
                            tuple(
                                _entity_id(entity, index)
                                for index, entity in enumerate(
                                    topic.entities,
                                    start=1,
                                )
                                if referenced_id
                                in _reference_values(
                                    entity.get(field_id),
                                    str(definition_map[field_id].get("value_type") or ""),
                                )
                            ),
                            (field_id,),
                            f"{count} of {entity_count} entities reference {referenced_id}.",
                            regeneration_scope="topic",
                        )
                    )
        for shared_references, referencing_ids in reference_tuples.items():
            non_exempt_tuple = tuple(
                (field_id, referenced_ids)
                for field_id, referenced_ids in shared_references
                if str(definition_map[field_id].get("semantic_role") or "").strip()
                not in _REFERENCE_CONCENTRATION_EXEMPT_ROLES
            )
            if not non_exempt_tuple:
                continue
            if len(referencing_ids) >= threshold and not _reference_tuple_declared(
                node.topic_id,
                non_exempt_tuple,
                declarations,
            ):
                issues.append(
                    SemanticQualityIssue(
                        "repeated_reference_tuple",
                        node.topic_id,
                        tuple(sorted(referencing_ids)),
                        tuple(field for field, _ in non_exempt_tuple),
                        "Most entities share the same cross-domain reference tuple.",
                        regeneration_scope="topic",
                    )
                )

    error_count = sum(1 for issue in issues if issue.severity == "error")
    return SemanticQualityReport(
        passed=error_count == 0,
        issues=tuple(issues),
        checks={
            "entities": len(topic.entities),
            "profile_fields": len(definitions),
            "descriptive_fields": len(descriptive_fields),
            "operational_fields": len(operational_fields),
            "causal_fields": len(causal_fields),
            "observable_fields": len(observable_fields),
            "generic_phrase_matches": generic_total,
            "errors": error_count,
        },
    )


def require_topic_semantic_quality(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    campaign_context: Mapping[str, Any] | None = None,
) -> SemanticQualityReport:
    report = audit_topic_semantic_quality(node, topic, campaign_context)
    if not report.passed:
        raise WorldForgeSemanticQualityError(report)
    return report
