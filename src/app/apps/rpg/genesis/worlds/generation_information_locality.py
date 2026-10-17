"""Faction-local information reach and delay certification."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_information_locality import information_locality_components

_DIVERSITY = ("channel_type", "latency_band", "distortion_risk")
_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_UNBOUNDED = {"instant", "global", "universal", "omniscient", "perfect", "unlimited", "none", "unknown", "placeholder"}
_LATENCY_RANK = {"same_day": 0, "one_day": 1, "several_days": 2, "one_week": 3, "irregular": 4, "event_triggered": 4}
_ROUTE_DELAY_RANK = {"same_day": 0, "one_day": 1, "several_days": 2, "one_week": 3, "irregular": 4, "courier_only": 4}


@dataclass(frozen=True)
class InformationLocalityIssue:
    code: str
    group_id: str
    place_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "group_id": self.group_id, "place_id": self.place_id, "path": self.path, "message": self.message, "evidence": dict(self.evidence), "severity": "error", "blocking": True}


class InformationLocalityCompilationError(ValueError):
    def __init__(self, issues: Sequence[InformationLocalityIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("information_locality_integrity_failed:" + ";".join(f"{row.code}:{row.group_id}:{row.place_id}" for row in self.issues))


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in value if isinstance(row, Mapping)) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    return next((dict(row[key]) for key in ("candidate", "content") if isinstance(row.get(key), Mapping)), dict(row))


def _entities(topic_rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int, dict[str, Any]], ...]:
    values: list[Any] = []
    for topic_index, raw in enumerate(topic_rows, 1):
        topic = _map(raw); candidate = _candidate(topic); topic_id = str(topic.get("topic_id") or candidate.get("topic_id") or f"topic:{topic_index}")
        values.extend((topic_id, index, entity) for index, entity in enumerate(_rows(candidate.get("entities"))))
    return tuple(values)


def _domains(graph: Mapping[str, Any] | None) -> set[str]:
    domains = set()
    for node in _rows(_map(graph).get("nodes")):
        fields = {str(row.get("field_id") or "") for row in _rows(_map(node.get("metadata")).get("field_definitions"))}
        if "information_locality_signature" in fields:
            domains.add(str(node.get("topic_id") or ""))
    domains.discard(""); return domains


def _normalise(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping): return None, ("information_locality_signature_must_be_object",)
    payload = {}; issues = []
    for component in information_locality_components():
        rendered = _normalise(value.get(component))
        if not rendered: issues.append(f"information_locality_component_required:{component}")
        elif not _CATEGORY.fullmatch(rendered): issues.append(f"information_locality_component_invalid:{component}")
        elif rendered in _UNBOUNDED: issues.append(f"information_locality_component_unbounded:{component}")
        else: payload[component] = rendered
    return (payload if not issues else None), tuple(issues)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _direct_delays(places: Mapping[str, Mapping[str, Any]]) -> dict[tuple[str, str], int]:
    delays = {}
    for place_id, place in places.items():
        for endpoint, effect in _map(place.get("route_effects")).items():
            payload = _map(effect); rank = _ROUTE_DELAY_RANK.get(_normalise(payload.get("information_delay")))
            if rank is not None:
                delays[(place_id, str(endpoint))] = rank
    return delays


def information_locality_issues(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> tuple[InformationLocalityIssue, ...]:
    domains = _domains(topic_graph); entities = _entities(topic_rows)
    places = {str(row.get("id") or ""): row for topic, _index, row in entities if topic == "places" and str(row.get("id") or "")}
    groups = [(topic, index, row) for topic, index, row in entities if topic in domains or "information_locality_signature" in row]
    delays = _direct_delays(places); issues = []; payloads = {}; paths = {}
    for topic_id, index, group in groups:
        group_id = str(group.get("id") or f"{topic_id}:group:{index + 1}"); base = f"/{topic_id}/entities/{index}"; paths[group_id] = base
        anchor = str(group.get("information_anchor_place_id") or "").strip()
        raw_reach = group.get("information_place_ids")
        reach = tuple(str(value).strip() for value in raw_reach if str(value).strip()) if isinstance(raw_reach, Sequence) and not isinstance(raw_reach, (str, bytes)) else ()
        invalid = sorted({value for value in (*reach, anchor) if value and value not in places})
        if not anchor:
            issues.append(InformationLocalityIssue("information_anchor_required", group_id, "", f"{base}/information_anchor_place_id", "Faction information requires a canonical anchor place.", {}))
        if not reach:
            issues.append(InformationLocalityIssue("information_reach_required", group_id, "", f"{base}/information_place_ids", "Faction information requires at least one canonical place in direct reach.", {}))
        if invalid:
            issues.append(InformationLocalityIssue("information_place_reference_invalid", group_id, invalid[0], f"{base}/information_place_ids", "Information territory must resolve to canonical places.", {"invalid_place_ids": invalid}))
        if anchor and reach and anchor not in reach:
            issues.append(InformationLocalityIssue("information_anchor_outside_reach", group_id, anchor, f"{base}/information_place_ids", "The anchor place must be included in direct information reach.", {"anchor_place_id": anchor}))
        if len(places) >= 4 and set(reach) == set(places):
            issues.append(InformationLocalityIssue("information_reach_universal", group_id, "", f"{base}/information_place_ids", "A faction cannot receive direct updates from every generated place.", {"place_count": len(places)}))
        signature = group.get("information_locality_signature")
        if signature is None:
            issues.append(InformationLocalityIssue("information_locality_signature_required", group_id, "", f"{base}/information_locality_signature", "Faction information requires a bounded channel and delay signature.", {})); continue
        payload, codes = _payload(signature)
        for code in codes:
            issues.append(InformationLocalityIssue(code, group_id, "", f"{base}/information_locality_signature", "Information-locality signature violates the categorical contract.", {"signature": dict(signature) if isinstance(signature, Mapping) else signature}))
        if payload is None: continue
        payloads[group_id] = payload
        claimed = _LATENCY_RANK.get(payload.get("latency_band", ""))
        for endpoint in reach:
            if endpoint == anchor or claimed is None: continue
            route_rank = delays.get((anchor, endpoint))
            if route_rank is not None and claimed < route_rank:
                issues.append(InformationLocalityIssue("information_latency_faster_than_route", group_id, endpoint, f"{base}/information_locality_signature/latency_band", "Faction latency cannot beat the direct route's information delay.", {"claimed_latency": payload.get("latency_band"), "route_delay_rank": route_rank}))
            if route_rank is None and claimed == 0:
                issues.append(InformationLocalityIssue("same_day_information_without_direct_route", group_id, endpoint, f"{base}/information_locality_signature/latency_band", "Same-day knowledge requires a direct canonical route from the information anchor.", {"anchor_place_id": anchor, "endpoint_place_id": endpoint}))
    if len(groups) >= 4:
        by_signature: dict[Any, Any] = {}
        for group_id, payload in payloads.items(): by_signature.setdefault(_fingerprint(payload), []).append(group_id)
        for fingerprint, repeated in sorted(by_signature.items()):
            if len(repeated) > 1: issues.append(InformationLocalityIssue("duplicate_information_locality_signature", repeated[0], "", f"{paths[repeated[0]]}/information_locality_signature", "Multiple factions share the same complete information network.", {"fingerprint": fingerprint, "group_ids": sorted(repeated)}))
        for component in _DIVERSITY:
            values = sorted({payload.get(component, "") for payload in payloads.values() if payload.get(component)})
            if len(values) < 2: issues.append(InformationLocalityIssue("information_locality_component_uniform", "", "", f"/information_locality/{component}", "Faction channels, latency, and distortion require portfolio diversity.", {"component": component, "values": values}))
    unique = {(row.code, row.group_id, row.place_id, row.path): row for row in issues}
    return tuple(unique[key] for key in sorted(unique))


def information_locality_report(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    domains = _domains(topic_graph); groups = [row for topic, _index, row in _entities(topic_rows) if topic in domains or "information_locality_signature" in row]
    issues = information_locality_issues(topic_rows, topic_graph); valid = [payload for row in groups if (payload := _payload(row.get("information_locality_signature"))[0]) is not None]
    return {"schema_version": "rpg_world_information_locality_report_v1", "passed": not issues, "issues": [row.as_dict() for row in issues], "checks": {"contract_domain_ids": sorted(domains), "group_count": len(groups), "valid_signature_count": len(valid), "unique_signature_count": len({_fingerprint(row) for row in valid})}}


def require_valid_information_locality(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> None:
    issues = information_locality_issues(topic_rows, topic_graph)
    if issues: raise InformationLocalityCompilationError(issues)


__all__ = ["InformationLocalityCompilationError", "InformationLocalityIssue", "information_locality_issues", "information_locality_report", "require_valid_information_locality"]
