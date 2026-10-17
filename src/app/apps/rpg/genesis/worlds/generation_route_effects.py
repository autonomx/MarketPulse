"""Route-specific travel-effect certification for assembled worlds."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_route_effects import route_effect_components

_DIVERSITY = ("hazard_level", "supply_effect", "information_delay")
_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_UNBOUNDED = {"zero", "free", "instant", "global", "unlimited", "universal", "static", "none", "unknown", "placeholder"}
_MATERIAL_SUPPLY_EFFECTS = {"improves_supply", "stabilises_supply", "reduces_supply", "bottleneck_supply", "cuts_supply"}


@dataclass(frozen=True)
class RouteEffectIssue:
    code: str
    place_id: str
    endpoint_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "place_id": self.place_id, "endpoint_id": self.endpoint_id, "path": self.path, "message": self.message, "evidence": dict(self.evidence), "severity": "error", "blocking": True}


class RouteEffectCompilationError(ValueError):
    def __init__(self, issues: Sequence[RouteEffectIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("route_effect_integrity_failed:" + ";".join(f"{row.code}:{row.place_id}:{row.endpoint_id}" for row in self.issues))


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in value if isinstance(row, Mapping)) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    return next((dict(row[key]) for key in ("candidate", "content") if isinstance(row.get(key), Mapping)), dict(row))


def _domains(graph: Mapping[str, Any] | None) -> set[str]:
    domains = set()
    for node in _rows(_map(graph).get("nodes")):
        fields = {str(row.get("field_id") or "") for row in _rows(_map(node.get("metadata")).get("field_definitions"))}
        if "route_effects" in fields:
            domains.add(str(node.get("topic_id") or ""))
    domains.discard(""); return domains


def _entities(topic_rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int, dict[str, Any]], ...]:
    values: list[Any] = []
    for topic_index, raw in enumerate(topic_rows, 1):
        topic = _map(raw); candidate = _candidate(topic); topic_id = str(topic.get("topic_id") or candidate.get("topic_id") or f"topic:{topic_index}")
        values.extend((topic_id, index, entity) for index, entity in enumerate(_rows(candidate.get("entities"))))
    return tuple(values)


def _normalise(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping): return None, ("route_effect_signature_must_be_object",)
    payload = {}; issues = []
    for component in route_effect_components():
        rendered = _normalise(value.get(component))
        if not rendered: issues.append(f"route_effect_component_required:{component}")
        elif not _CATEGORY.fullmatch(rendered): issues.append(f"route_effect_component_invalid:{component}")
        elif rendered in _UNBOUNDED: issues.append(f"route_effect_component_unbounded:{component}")
        else: payload[component] = rendered
    return (payload if not issues else None), tuple(issues)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def route_effect_issues(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> tuple[RouteEffectIssue, ...]:
    domains = _domains(topic_graph); entities = _entities(topic_rows)
    places = [(topic, index, row) for topic, index, row in entities if topic in domains or "route_effects" in row]
    place_ids = {str(row.get("id") or "") for _topic, _index, row in places if str(row.get("id") or "")}
    issues = []; valid_payloads = []; route_pairs = set()
    for topic_id, index, place in places:
        place_id = str(place.get("id") or f"{topic_id}:place:{index + 1}"); base = f"/{topic_id}/entities/{index}/route_effects"
        connected_raw = place.get("connected_place_ids")
        connected = tuple(str(value).strip() for value in connected_raw if str(value).strip()) if isinstance(connected_raw, Sequence) and not isinstance(connected_raw, (str, bytes)) else ()
        effects = place.get("route_effects")
        if not isinstance(effects, Mapping):
            issues.append(RouteEffectIssue("route_effects_required", place_id, "", base, "Every canonical connection requires endpoint-specific travel effects.", {})); continue
        effect_map = {str(key): value for key, value in effects.items() if str(key)}
        missing = sorted(set(connected) - set(effect_map)); extra = sorted(set(effect_map) - set(connected))
        if missing:
            issues.append(RouteEffectIssue("route_effect_endpoint_missing", place_id, missing[0], base, "Every connected endpoint requires a route-effect entry.", {"missing_endpoint_ids": missing}))
        if extra:
            issues.append(RouteEffectIssue("route_effect_endpoint_not_connected", place_id, extra[0], base, "Route effects cannot invent endpoints outside connected_place_ids.", {"extra_endpoint_ids": extra}))
        invalid_refs = sorted(endpoint for endpoint in connected if endpoint not in place_ids or endpoint == place_id)
        if invalid_refs:
            issues.append(RouteEffectIssue("route_effect_endpoint_reference_invalid", place_id, invalid_refs[0], base, "Route-effect endpoints must resolve to other canonical places.", {"invalid_endpoint_ids": invalid_refs}))
        for endpoint in sorted(set(connected) & set(effect_map)):
            path = f"{base}/{endpoint}"; payload, codes = _payload(effect_map[endpoint])
            for code in codes:
                issues.append(RouteEffectIssue(code, place_id, endpoint, path, "Route-effect signature violates the bounded categorical contract.", {"signature": dict(effect_map[endpoint]) if isinstance(effect_map[endpoint], Mapping) else effect_map[endpoint]}))
            if payload is not None:
                valid_payloads.append(payload); route_pairs.add(tuple(sorted((place_id, endpoint))))
        market = _map(place.get("local_market_signature"))
        if _normalise(market.get("shock_sensitivity")) == "route_sensitive":
            material = any((_payload(effect_map.get(endpoint))[0] or {}).get("supply_effect") in _MATERIAL_SUPPLY_EFFECTS for endpoint in connected)
            if not material:
                issues.append(RouteEffectIssue("route_sensitive_market_without_route_effect", place_id, "", base, "Route-sensitive markets require at least one route with a material supply effect.", {"connected_place_ids": list(connected)}))
    if len(valid_payloads) >= 4:
        counts: dict[Any, Any] = {}
        for payload in valid_payloads: counts.setdefault(_fingerprint(payload), 0); counts[_fingerprint(payload)] += 1
        if max(counts.values(), default=0) > max(2, len(valid_payloads) // 2):
            issues.append(RouteEffectIssue("route_effect_portfolio_too_repetitive", "", "", "/route_effects/portfolio", "One exact route-effect template cannot dominate the route network.", {"route_effect_count": len(valid_payloads), "maximum_duplicate_count": max(counts.values())}))
        for component in _DIVERSITY:
            values = sorted({payload.get(component, "") for payload in valid_payloads if payload.get(component)})
            if len(values) < 2:
                issues.append(RouteEffectIssue("route_effect_component_uniform", "", "", f"/route_effects/{component}", "Route hazards, supply effects, and information delays require network diversity.", {"component": component, "values": values}))
    unique = {(row.code, row.place_id, row.endpoint_id, row.path): row for row in issues}
    return tuple(unique[key] for key in sorted(unique))


def route_effect_report(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    domains = _domains(topic_graph); places = [row for topic, _index, row in _entities(topic_rows) if topic in domains or "route_effects" in row]
    issues = route_effect_issues(topic_rows, topic_graph)
    route_count = sum(len(_map(row.get("route_effects"))) for row in places)
    return {"schema_version": "rpg_world_route_effect_report_v1", "passed": not issues, "issues": [row.as_dict() for row in issues], "checks": {"contract_domain_ids": sorted(domains), "place_count": len(places), "directional_route_effect_count": route_count}}


def require_valid_route_effects(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> None:
    issues = route_effect_issues(topic_rows, topic_graph)
    if issues: raise RouteEffectCompilationError(issues)


__all__ = ["RouteEffectCompilationError", "RouteEffectIssue", "route_effect_issues", "route_effect_report", "require_valid_route_effects"]
