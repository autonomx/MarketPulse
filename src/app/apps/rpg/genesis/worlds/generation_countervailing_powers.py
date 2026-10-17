"""Countervailing-power portfolio certification for assembled worlds."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_countervailing_powers import countervailing_power_components

_DIVERSITY = ("constraint_mechanism", "leverage_type", "vulnerability")
_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ABSOLUTES = {"absolute", "unlimited", "unopposed", "universal", "invulnerable", "none", "unknown", "placeholder"}


@dataclass(frozen=True)
class CountervailingPowerIssue:
    code: str
    group_id: str
    path: str
    message: str
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "group_id": self.group_id, "path": self.path, "message": self.message, "evidence": dict(self.evidence), "severity": "error", "blocking": True}


class CountervailingPowerCompilationError(ValueError):
    def __init__(self, issues: Sequence[CountervailingPowerIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("countervailing_power_integrity_failed:" + ";".join(f"{row.code}:{row.group_id}:{row.path}" for row in self.issues))


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in value if isinstance(row, Mapping)) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    return next((dict(row[key]) for key in ("candidate", "content") if isinstance(row.get(key), Mapping)), dict(row))


def _domains(graph: Mapping[str, Any] | None) -> set[str]:
    value = _map(graph)
    domains = {str(item) for item in _map(_map(value.get("metadata")).get("countervailing_power_contract")).get("domain_ids") or () if str(item)}
    for node in _rows(value.get("nodes")):
        metadata = _map(node.get("metadata"))
        fields = {str(row.get("field_id") or "") for row in _rows(metadata.get("field_definitions"))}
        if _map(metadata.get("countervailing_power_contract")).get("required") or "countervailing_power_signature" in fields:
            domains.add(str(node.get("topic_id") or ""))
    domains.discard("")
    return domains


def _entities(topic_rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int, dict[str, Any]], ...]:
    values: list[Any] = []
    for topic_index, raw in enumerate(topic_rows, 1):
        topic = _map(raw); candidate = _candidate(topic)
        topic_id = str(topic.get("topic_id") or candidate.get("topic_id") or f"topic:{topic_index}")
        values.extend((topic_id, index, entity) for index, entity in enumerate(_rows(candidate.get("entities"))))
    return tuple(values)


def _normalise(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _payload(value: Any) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("countervailing_power_signature_must_be_object",)
    payload = {}; issues = []
    for component in countervailing_power_components():
        rendered = _normalise(value.get(component))
        if not rendered:
            issues.append(f"countervailing_power_component_required:{component}")
        elif not _CATEGORY.fullmatch(rendered):
            issues.append(f"countervailing_power_component_invalid:{component}")
        elif rendered in _ABSOLUTES:
            issues.append(f"countervailing_power_component_unbounded:{component}")
        else:
            payload[component] = rendered
    return (payload if not issues else None), tuple(issues)


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def countervailing_power_issues(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> tuple[CountervailingPowerIssue, ...]:
    domains = _domains(topic_graph); entities = _entities(topic_rows)
    groups = [(topic, index, row) for topic, index, row in entities if topic in domains or "countervailing_power_signature" in row]
    group_ids = {str(row.get("id") or "") for _topic, _index, row in groups if str(row.get("id") or "")}
    issues = []; payloads = {}; constraints = {}; paths = {}
    for topic_id, index, group in groups:
        group_id = str(group.get("id") or f"{topic_id}:group:{index + 1}")
        base = f"/{topic_id}/entities/{index}"; paths[group_id] = base
        raw_targets = group.get("constrained_by_group_ids")
        targets = tuple(str(value).strip() for value in raw_targets if str(value).strip()) if isinstance(raw_targets, Sequence) and not isinstance(raw_targets, (str, bytes)) else ()
        constraints[group_id] = targets
        if not targets:
            issues.append(CountervailingPowerIssue("countervailing_constraint_required", group_id, f"{base}/constrained_by_group_ids", "Every major power must be constrained by another canonical group.", {}))
        invalid = sorted({value for value in targets if value not in group_ids or value == group_id})
        if invalid:
            issues.append(CountervailingPowerIssue("countervailing_constraint_reference_invalid", group_id, f"{base}/constrained_by_group_ids", "Constraint references must resolve to other canonical groups.", {"invalid_group_ids": invalid}))
        signature = group.get("countervailing_power_signature")
        if signature is None:
            issues.append(CountervailingPowerIssue("countervailing_power_signature_required", group_id, f"{base}/countervailing_power_signature", "Major groups require a bounded countervailing-power signature.", {})); continue
        payload, codes = _payload(signature)
        for code in codes:
            issues.append(CountervailingPowerIssue(code, group_id, f"{base}/countervailing_power_signature", "Countervailing-power signature violates the categorical contract.", {"signature": dict(signature) if isinstance(signature, Mapping) else signature}))
        if payload is not None:
            payloads[group_id] = payload
    count = len(groups)
    if count >= 3:
        adjacency = {group_id: {value for value in targets if value in group_ids and value != group_id} for group_id, targets in constraints.items()}
        undirected = {group_id: set(values) for group_id, values in adjacency.items()}
        for source, linked in adjacency.items():
            for target in linked:
                undirected.setdefault(target, set()).add(source)
        seen = set(); pending = [next(iter(group_ids))] if group_ids else []
        while pending:
            current = pending.pop()
            if current in seen: continue
            seen.add(current); pending.extend(undirected.get(current, set()) - seen)
        if seen != group_ids:
            issues.append(CountervailingPowerIssue("countervailing_power_graph_disconnected", "", "/countervailing_powers/graph", "Major powers must form one connected constraint graph.", {"unreachable_group_ids": sorted(group_ids - seen)}))
        by_signature: dict[Any, Any] = {}
        for group_id, payload in payloads.items(): by_signature.setdefault(_fingerprint(payload), []).append(group_id)
        for fingerprint, repeated in sorted(by_signature.items()):
            if len(repeated) > 1:
                issues.append(CountervailingPowerIssue("duplicate_countervailing_power_signature", repeated[0], f"{paths[repeated[0]]}/countervailing_power_signature", "Multiple powers share the same complete constraint profile.", {"fingerprint": fingerprint, "group_ids": sorted(repeated)}))
        for component in _DIVERSITY:
            values = sorted({payload.get(component, "") for payload in payloads.values() if payload.get(component)})
            if len(values) < 2:
                issues.append(CountervailingPowerIssue("countervailing_power_component_uniform", "", f"/countervailing_powers/{component}", "Constraint mechanisms, leverage, and vulnerabilities require portfolio diversity.", {"component": component, "values": values}))
        target_counts = {group_id: sum(group_id in values for values in adjacency.values()) for group_id in group_ids}
        maximum = max(target_counts.values(), default=0)
        if maximum > math.ceil(count * 0.75):
            dominant = sorted(group_id for group_id, value in target_counts.items() if value == maximum)
            issues.append(CountervailingPowerIssue("countervailing_constraint_concentration_high", dominant[0], "/countervailing_powers/concentration", "One group cannot be the sole counterweight to almost every other power.", {"group_ids": dominant, "constraint_count": maximum, "group_count": count}))
    unique = {(row.code, row.group_id, row.path): row for row in issues}
    return tuple(unique[key] for key in sorted(unique))


def countervailing_power_report(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> dict[str, Any]:
    domains = _domains(topic_graph); groups = [row for topic, _index, row in _entities(topic_rows) if topic in domains or "countervailing_power_signature" in row]
    issues = countervailing_power_issues(topic_rows, topic_graph)
    payloads = [_payload(row.get("countervailing_power_signature"))[0] for row in groups]
    valid = [row for row in payloads if row is not None]
    return {"schema_version": "rpg_world_countervailing_power_report_v1", "passed": not issues, "issues": [row.as_dict() for row in issues], "checks": {"contract_domain_ids": sorted(domains), "group_count": len(groups), "valid_signature_count": len(valid), "unique_signature_count": len({_fingerprint(row) for row in valid})}}


def require_valid_countervailing_powers(topic_rows: Sequence[Mapping[str, Any]], topic_graph: Mapping[str, Any] | None) -> None:
    issues = countervailing_power_issues(topic_rows, topic_graph)
    if issues: raise CountervailingPowerCompilationError(issues)


__all__ = ["CountervailingPowerCompilationError", "CountervailingPowerIssue", "countervailing_power_issues", "countervailing_power_report", "require_valid_countervailing_powers"]
