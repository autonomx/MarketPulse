"""Ordinary-life cultural coverage certification for assembled worlds."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_ordinary_life import ordinary_life_components

_DIVERSITY_COMPONENTS = ("food_staple", "work_pattern", "leisure_practice", "care_practice")
_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ABSTRACT_ONLY = {
    "class_only", "corporate_only", "faction_only", "ideology_only", "military_only",
    "professional_only", "religion_only", "status_only", "universal", "unknown",
    "none", "not_applicable", "placeholder", "generic",
}


@dataclass(frozen=True)
class OrdinaryLifeIssue:
    code: str
    culture_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "culture_id": self.culture_id,
            "path": self.path,
            "message": self.message,
            "evidence": dict(self.evidence),
            "severity": "error",
            "blocking": True,
        }


class OrdinaryLifeCompilationError(ValueError):
    def __init__(self, issues: Sequence[OrdinaryLifeIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(f"{row.code}:{row.culture_id}:{row.path}" for row in self.issues)
        super().__init__("ordinary_life_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "ordinary_life_integrity_failed",
            "issues": [row.as_dict() for row in self.issues],
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


def _domains(topic_graph: Mapping[str, Any] | None) -> set[str]:
    graph = _mapping(topic_graph)
    values = {
        str(value)
        for value in _mapping(_mapping(graph.get("metadata")).get("ordinary_life_contract")).get("domain_ids") or ()
        if str(value)
    }
    for node in _rows(graph.get("nodes")):
        if _mapping(_mapping(node.get("metadata")).get("ordinary_life_contract")).get("required"):
            values.add(str(node.get("topic_id") or ""))
    values.discard("")
    return values


def _entities(topic_rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int, dict[str, Any]], ...]:
    values: list[tuple[str, int, dict[str, Any]]] = []
    for topic_index, raw in enumerate(topic_rows, start=1):
        topic = _mapping(raw)
        candidate = _candidate(topic)
        topic_id = str(topic.get("topic_id") or candidate.get("topic_id") or f"topic:{topic_index}")
        values.extend((topic_id, index, entity) for index, entity in enumerate(_rows(candidate.get("entities"))))
    return tuple(values)


def _normalise(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("ordinary_life_signature_must_be_object",)
    payload: dict[str, str] = {}
    issues: list[str] = []
    for component in ordinary_life_components():
        normalised = _normalise(value.get(component))
        if not normalised:
            issues.append(f"ordinary_life_component_required:{component}")
        elif not _CATEGORY.fullmatch(normalised):
            issues.append(f"ordinary_life_component_invalid:{component}")
        elif normalised in _ABSTRACT_ONLY:
            issues.append(f"ordinary_life_component_abstract_only:{component}")
        else:
            payload[component] = normalised
    return (payload if not issues else None), tuple(issues)


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def ordinary_life_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[OrdinaryLifeIssue, ...]:
    domains = _domains(topic_graph)
    entities = _entities(topic_rows)
    registry = {str(entity.get("id") or ""): topic_id for topic_id, _index, entity in entities if str(entity.get("id") or "")}
    place_ids = {entity_id for entity_id, topic_id in registry.items() if topic_id == "places"}
    cultures = [(topic_id, index, entity) for topic_id, index, entity in entities if topic_id in domains or "ordinary_life_signature" in entity]
    issues: list[OrdinaryLifeIssue] = []
    payloads: dict[str, dict[str, str]] = {}
    paths: dict[str, str] = {}
    for topic_id, index, culture in cultures:
        culture_id = str(culture.get("id") or f"{topic_id}:culture:{index + 1}")
        signature_path = f"/{topic_id}/entities/{index}/ordinary_life_signature"
        paths[culture_id] = signature_path
        raw_places = culture.get("ordinary_life_place_ids")
        places = tuple(str(value).strip() for value in raw_places if str(value).strip()) if isinstance(raw_places, Sequence) and not isinstance(raw_places, (str, bytes)) else ()
        if not places:
            issues.append(OrdinaryLifeIssue("ordinary_life_place_grounding_required", culture_id, f"/{topic_id}/entities/{index}/ordinary_life_place_ids", "Cultures require at least one canonical place where ordinary life is practised.", {"topic_id": topic_id}))
        unknown = sorted(set(places) - place_ids)
        if unknown:
            issues.append(OrdinaryLifeIssue("ordinary_life_place_reference_invalid", culture_id, f"/{topic_id}/entities/{index}/ordinary_life_place_ids", "Ordinary-life place references must resolve to canonical places.", {"unknown_place_ids": unknown}))
        signature = culture.get("ordinary_life_signature")
        if signature is None:
            issues.append(OrdinaryLifeIssue("ordinary_life_signature_required", culture_id, signature_path, "Cultures require a structured ordinary-life signature.", {"topic_id": topic_id}))
            continue
        payload, signature_issues = _payload(signature)
        for code in signature_issues:
            issues.append(OrdinaryLifeIssue(code, culture_id, signature_path, "Ordinary-life signature does not satisfy the categorical contract.", {"signature": dict(signature) if isinstance(signature, Mapping) else signature}))
        if payload is not None:
            payloads[culture_id] = payload
    if len(cultures) >= 3:
        by_signature: dict[str, list[str]] = {}
        for culture_id, payload in payloads.items():
            by_signature.setdefault(_fingerprint(payload), []).append(culture_id)
        for fingerprint, repeated in sorted(by_signature.items()):
            if len(repeated) > 1:
                issues.append(OrdinaryLifeIssue("duplicate_ordinary_life_signature", repeated[0], paths.get(repeated[0], ""), "Multiple cultures share the same complete ordinary-life template.", {"fingerprint": fingerprint, "culture_ids": sorted(repeated)}))
        for component in _DIVERSITY_COMPONENTS:
            values = sorted({payload.get(component, "") for payload in payloads.values() if payload.get(component)})
            if len(values) < 2:
                issues.append(OrdinaryLifeIssue("ordinary_life_component_uniform", "", f"/ordinary_life/{component}", "Food, work, leisure, and care practices require portfolio diversity.", {"component": component, "values": values, "culture_count": len(cultures)}))
    unique = {(row.code, row.culture_id, row.path): row for row in issues}
    return tuple(unique[key] for key in sorted(unique))


def ordinary_life_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    domains = _domains(topic_graph)
    cultures = [entity for topic_id, _index, entity in _entities(topic_rows) if topic_id in domains or "ordinary_life_signature" in entity]
    issues = ordinary_life_issues(topic_rows, topic_graph)
    signatures = [entity.get("ordinary_life_signature") for entity in cultures if isinstance(entity.get("ordinary_life_signature"), Mapping)]
    return {
        "schema_version": "rpg_world_ordinary_life_report_v1",
        "passed": not issues,
        "issues": [row.as_dict() for row in issues],
        "checks": {
            "contract_domain_ids": sorted(domains),
            "culture_count": len(cultures),
            "valid_signature_count": sum(1 for value in signatures if _payload(value)[0] is not None),
            "unique_signature_count": len({_fingerprint(_payload(value)[0] or {}) for value in signatures if _payload(value)[0] is not None}),
        },
    }


def require_valid_ordinary_life(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> None:
    issues = ordinary_life_issues(topic_rows, topic_graph)
    if issues:
        raise OrdinaryLifeCompilationError(issues)


__all__ = [
    "OrdinaryLifeCompilationError",
    "OrdinaryLifeIssue",
    "ordinary_life_issues",
    "ordinary_life_report",
    "require_valid_ordinary_life",
]
