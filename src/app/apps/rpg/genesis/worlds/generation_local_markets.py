"""Place-local market certification for assembled worlds."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_local_markets import local_market_components

_DIVERSITY = ("supply_reliability", "price_level", "shock_sensitivity")
_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ABSOLUTES = {"global", "universal", "unlimited", "instant", "static", "none", "unknown", "placeholder"}


@dataclass(frozen=True)
class LocalMarketIssue:
    code: str
    place_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "place_id": self.place_id, "path": self.path, "message": self.message, "evidence": dict(self.evidence), "severity": "error", "blocking": True}


class LocalMarketCompilationError(ValueError):
    def __init__(self, issues: Sequence[LocalMarketIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("local_market_integrity_failed:" + ";".join(f"{row.code}:{row.place_id}:{row.path}" for row in self.issues))


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in value if isinstance(row, Mapping)) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    return next((dict(row[key]) for key in ("candidate", "content") if isinstance(row.get(key), Mapping)), dict(row))


def _domains(graph: Mapping[str, Any] | None) -> set[str]:
    value = _map(graph); domains = set()
    for node in _rows(value.get("nodes")):
        metadata = _map(node.get("metadata")); fields = {str(row.get("field_id") or "") for row in _rows(metadata.get("field_definitions"))}
        if "local_market_signature" in fields:
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
    if not isinstance(value, Mapping): return None, ("local_market_signature_must_be_object",)
    payload = {}; issues = []
    for component in local_market_components():
        rendered = _normalise(value.get(component))
        if not rendered: issues.append(f"local_market_component_required:{component}")
        elif not _CATEGORY.fullmatch(rendered): issues.append(f"local_market_component_invalid:{component}")
        elif rendered in _ABSOLUTES: issues.append(f"local_market_component_unbounded:{component}")
        else: payload[component] = rendered
    return (payload if not issues else None), tuple(issues)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def local_market_issues(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> tuple[LocalMarketIssue, ...]:
    domains = _domains(topic_graph); places = [(topic, index, row) for topic, index, row in _entities(topic_rows) if topic in domains or "local_market_signature" in row]
    issues = []; payloads = {}; paths = {}
    for topic_id, index, place in places:
        place_id = str(place.get("id") or f"{topic_id}:place:{index + 1}"); path = f"/{topic_id}/entities/{index}/local_market_signature"; paths[place_id] = path
        signature = place.get("local_market_signature")
        if signature is None:
            issues.append(LocalMarketIssue("local_market_signature_required", place_id, path, "Every generated place requires a bounded local market state.", {})); continue
        payload, codes = _payload(signature)
        for code in codes:
            issues.append(LocalMarketIssue(code, place_id, path, "Local market signature violates the categorical contract.", {"signature": dict(signature) if isinstance(signature, Mapping) else signature}))
        if payload is None: continue
        payloads[place_id] = payload
        scale = _map(place.get("economic_scale_signature")); scarcity = _normalise(scale.get("scarcity_level"))
        if scarcity in {"scarce", "critical"} and payload.get("supply_reliability") == "robust":
            issues.append(LocalMarketIssue("local_market_supply_conflicts_with_scarcity", place_id, path, "Scarce places cannot claim robust local supply.", {"scarcity_level": scarcity, "supply_reliability": "robust"}))
        if scarcity == "critical" and payload.get("price_level") in {"discounted", "stable"}:
            issues.append(LocalMarketIssue("local_market_price_conflicts_with_scarcity", place_id, path, "Critical scarcity requires elevated, volatile, crisis, or barter pricing.", {"scarcity_level": scarcity, "price_level": payload.get("price_level")}))
    if len(places) >= 4:
        by_signature: dict[Any, Any] = {}
        for place_id, payload in payloads.items(): by_signature.setdefault(_fingerprint(payload), []).append(place_id)
        for fingerprint, repeated in sorted(by_signature.items()):
            if len(repeated) > 1: issues.append(LocalMarketIssue("duplicate_local_market_signature", repeated[0], paths[repeated[0]], "Multiple places share the same complete local market state.", {"fingerprint": fingerprint, "place_ids": sorted(repeated)}))
        for component in _DIVERSITY:
            values = sorted({payload.get(component, "") for payload in payloads.values() if payload.get(component)})
            if len(values) < 2: issues.append(LocalMarketIssue("local_market_component_uniform", "", f"/local_markets/{component}", "Place markets require distinct supply, price, and shock conditions.", {"component": component, "values": values}))
    unique = {(row.code, row.place_id, row.path): row for row in issues}; return tuple(unique[key] for key in sorted(unique))


def local_market_report(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    domains = _domains(topic_graph); places = [row for topic, _index, row in _entities(topic_rows) if topic in domains or "local_market_signature" in row]
    issues = local_market_issues(topic_rows, topic_graph); valid = [payload for row in places if (payload := _payload(row.get("local_market_signature"))[0]) is not None]
    return {"schema_version": "rpg_world_local_market_report_v1", "passed": not issues, "issues": [row.as_dict() for row in issues], "checks": {"contract_domain_ids": sorted(domains), "place_count": len(places), "valid_signature_count": len(valid), "unique_signature_count": len({_fingerprint(row) for row in valid})}}


def require_valid_local_markets(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> None:
    issues = local_market_issues(topic_rows, topic_graph)
    if issues: raise LocalMarketCompilationError(issues)


__all__ = ["LocalMarketCompilationError", "LocalMarketIssue", "local_market_issues", "local_market_report", "require_valid_local_markets"]
