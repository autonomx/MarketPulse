"""Local narrative-opportunity certification for assembled worlds."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_local_narrative import local_narrative_components

_DIVERSITY = ("discovery_channel", "entry_mode", "consequence_scope")
_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_UNBOUNDED = {
    "global", "universal", "omniscient", "unlimited", "instant",
    "perfect", "none", "unknown", "placeholder",
}
_REFERENCE_FIELDS = {
    "local_place_ids": frozenset({"places"}),
    "local_pressure_ids": frozenset({"pressures"}),
    "local_actor_ids": frozenset({"actors"}),
    "local_group_ids": frozenset({"groups"}),
    "local_evidence_source_ids": frozenset({"places", "pressures", "actors", "groups"}),
}


@dataclass(frozen=True)
class LocalNarrativeIssue:
    code: str
    opportunity_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "opportunity_id": self.opportunity_id,
            "path": self.path,
            "message": self.message,
            "evidence": dict(self.evidence),
            "severity": "error",
            "blocking": True,
        }


class LocalNarrativeCompilationError(ValueError):
    def __init__(self, issues: Sequence[LocalNarrativeIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(f"{row.code}:{row.opportunity_id}:{row.path}" for row in self.issues)
        super().__init__("local_narrative_integrity_failed:" + rendered)


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(row) for row in value if isinstance(row, Mapping))


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("candidate", "content"):
        if isinstance(row.get(key), Mapping):
            return dict(row[key])
    return dict(row)


def _entities(topic_rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int, dict[str, Any]], ...]:
    values: list[tuple[str, int, dict[str, Any]]] = []
    for topic_index, raw in enumerate(topic_rows, 1):
        topic = _map(raw)
        candidate = _candidate(topic)
        topic_id = str(topic.get("topic_id") or candidate.get("topic_id") or f"topic:{topic_index}")
        values.extend((topic_id, index, row) for index, row in enumerate(_rows(candidate.get("entities"))))
    return tuple(values)


def _domains(graph: Mapping[str, Any] | None) -> set[str]:
    domains: set[str] = set()
    for node in _rows(_map(graph).get("nodes")):
        fields = {
            str(row.get("field_id") or "")
            for row in _rows(_map(node.get("metadata")).get("field_definitions"))
        }
        if "local_narrative_signature" in fields:
            domains.add(str(node.get("topic_id") or ""))
    domains.discard("")
    return domains


def _normalise(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("local_narrative_signature_must_be_object",)
    payload: dict[str, str] = {}
    issues: list[str] = []
    for component in local_narrative_components():
        rendered = _normalise(value.get(component))
        if not rendered:
            issues.append(f"local_narrative_component_required:{component}")
        elif not _CATEGORY.fullmatch(rendered):
            issues.append(f"local_narrative_component_invalid:{component}")
        elif rendered in _UNBOUNDED:
            issues.append(f"local_narrative_component_unbounded:{component}")
        else:
            payload[component] = rendered
    return (payload if not issues else None), tuple(issues)


def _ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def local_narrative_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[LocalNarrativeIssue, ...]:
    domains = _domains(topic_graph)
    entities = _entities(topic_rows)
    registry = {
        str(row.get("id") or ""): (topic_id, row)
        for topic_id, _index, row in entities
        if str(row.get("id") or "")
    }
    opportunities = [
        (topic_id, index, row)
        for topic_id, index, row in entities
        if topic_id in domains or "local_narrative_signature" in row
    ]
    issues: list[LocalNarrativeIssue] = []
    fingerprints: dict[str, list[str]] = {}
    pressure_counts: dict[str, int] = {}
    payloads: list[dict[str, str]] = []
    for topic_id, index, opportunity in opportunities:
        opportunity_id = str(opportunity.get("id") or f"{topic_id}:opportunity:{index + 1}")
        base = f"/{topic_id}/entities/{index}"
        references: dict[str, tuple[str, ...]] = {}
        for field, allowed_topics in _REFERENCE_FIELDS.items():
            values = _ids(opportunity.get(field))
            references[field] = values
            if not values:
                issues.append(LocalNarrativeIssue(
                    "local_narrative_reference_required", opportunity_id, f"{base}/{field}",
                    "Local narrative opportunities require explicit canonical grounding references.",
                    {"field": field},
                ))
                continue
            invalid = sorted(
                value for value in values
                if value not in registry or registry[value][0] not in allowed_topics
            )
            if invalid:
                issues.append(LocalNarrativeIssue(
                    "local_narrative_reference_invalid", opportunity_id, f"{base}/{field}",
                    "Local narrative references must resolve to the expected canonical domains.",
                    {"field": field, "invalid_entity_ids": invalid},
                ))
        local_ids = set().union(*(set(values) for field, values in references.items() if field != "local_evidence_source_ids"))
        remote_evidence = sorted(set(references["local_evidence_source_ids"]) - local_ids)
        if remote_evidence:
            issues.append(LocalNarrativeIssue(
                "local_narrative_evidence_not_local", opportunity_id,
                f"{base}/local_evidence_source_ids",
                "Evidence sources must also appear in the opportunity's local grounding set.",
                {"remote_evidence_source_ids": remote_evidence},
            ))
        local_places = set(references["local_place_ids"])
        for actor_id in references["local_actor_ids"]:
            actor = registry.get(actor_id)
            actor_place = str(_map(actor[1] if actor else {}).get("location_id") or "")
            if actor_place and actor_place not in local_places:
                issues.append(LocalNarrativeIssue(
                    "local_narrative_actor_outside_places", opportunity_id,
                    f"{base}/local_actor_ids",
                    "Locally grounded actors must be located at one of the opportunity places.",
                    {"actor_id": actor_id, "actor_place_id": actor_place, "local_place_ids": sorted(local_places)},
                ))
        signature = opportunity.get("local_narrative_signature")
        if signature is None:
            issues.append(LocalNarrativeIssue(
                "local_narrative_signature_required", opportunity_id,
                f"{base}/local_narrative_signature",
                "Local opportunities require a bounded discovery and consequence signature.", {},
            ))
            continue
        payload, codes = _payload(signature)
        for code in codes:
            issues.append(LocalNarrativeIssue(
                code, opportunity_id, f"{base}/local_narrative_signature",
                "Local narrative signature violates the categorical contract.",
                {"signature": dict(signature) if isinstance(signature, Mapping) else signature},
            ))
        if payload is None:
            continue
        payloads.append(payload)
        for pressure_id in set(references["local_pressure_ids"]):
            pressure_counts[pressure_id] = pressure_counts.get(pressure_id, 0) + 1
        if payload.get("information_scope") == "single_place" and len(local_places) != 1:
            issues.append(LocalNarrativeIssue(
                "single_place_opportunity_has_multiple_places", opportunity_id,
                f"{base}/local_place_ids",
                "A single-place discovery claim must identify exactly one canonical place.",
                {"local_place_ids": sorted(local_places)},
            ))
        if payload.get("information_scope") == "faction_reach" and local_places:
            reaches = [
                set(_ids(_map(registry.get(group_id, ("", {}))[1]).get("information_place_ids")))
                for group_id in references["local_group_ids"]
            ]
            if not any(local_places.issubset(reach) for reach in reaches):
                issues.append(LocalNarrativeIssue(
                    "local_narrative_exceeds_faction_information_reach", opportunity_id,
                    f"{base}/local_narrative_signature/information_scope",
                    "Faction-reach discovery cannot cover places outside every referenced group's direct information territory.",
                    {"local_place_ids": sorted(local_places), "local_group_ids": list(references["local_group_ids"])},
                ))
        identity = {
            "signature": payload,
            **{field: sorted(values) for field, values in references.items()},
        }
        fingerprints.setdefault(_fingerprint(identity), []).append(opportunity_id)
    for fingerprint, repeated in sorted(fingerprints.items()):
        if len(repeated) > 1:
            issues.append(LocalNarrativeIssue(
                "duplicate_local_narrative_opportunity", repeated[0], "/local_narrative/portfolio",
                "Multiple opportunities share the same complete local grounding and discovery profile.",
                {"fingerprint": fingerprint, "opportunity_ids": sorted(repeated)},
            ))
    if len(opportunities) >= 4:
        maximum = max(pressure_counts.values(), default=0)
        if maximum > math.ceil(len(opportunities) * 0.75):
            dominant = sorted(key for key, value in pressure_counts.items() if value == maximum)
            issues.append(LocalNarrativeIssue(
                "local_narrative_pressure_concentration_high", dominant[0] if dominant else "",
                "/local_narrative/pressure_concentration",
                "One pressure cannot drive almost every local narrative opportunity.",
                {"pressure_ids": dominant, "opportunity_count": len(opportunities), "maximum_count": maximum},
            ))
        for component in _DIVERSITY:
            component_values = sorted({payload.get(component, "") for payload in payloads if payload.get(component)})
            if len(component_values) < 2:
                issues.append(LocalNarrativeIssue(
                    "local_narrative_component_uniform", "", f"/local_narrative/{component}",
                    "Discovery channels, entry modes, and consequence scopes require portfolio diversity.",
                    {"component": component, "values": component_values},
                ))
    unique = {(row.code, row.opportunity_id, row.path): row for row in issues}
    return tuple(unique[key] for key in sorted(unique))


def local_narrative_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    domains = _domains(topic_graph)
    opportunities = [
        row for topic_id, _index, row in _entities(topic_rows)
        if topic_id in domains or "local_narrative_signature" in row
    ]
    issues = local_narrative_issues(topic_rows, topic_graph)
    valid = [payload for row in opportunities if (payload := _payload(row.get("local_narrative_signature"))[0]) is not None]
    return {
        "schema_version": "rpg_world_local_narrative_report_v1",
        "passed": not issues,
        "issues": [row.as_dict() for row in issues],
        "checks": {
            "contract_domain_ids": sorted(domains),
            "opportunity_count": len(opportunities),
            "valid_signature_count": len(valid),
            "unique_signature_count": len({_fingerprint(row) for row in valid}),
        },
    }


def require_valid_local_narrative(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> None:
    issues = local_narrative_issues(topic_rows, topic_graph)
    if issues:
        raise LocalNarrativeCompilationError(issues)


__all__ = [
    "LocalNarrativeCompilationError",
    "LocalNarrativeIssue",
    "local_narrative_issues",
    "local_narrative_report",
    "require_valid_local_narrative",
]
