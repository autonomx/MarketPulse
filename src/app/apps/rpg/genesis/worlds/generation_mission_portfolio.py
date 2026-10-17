"""Structured mission-signature and intentional campaign-arc certification."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_SIGNATURE_COMPONENTS = (
    "activity",
    "target",
    "location",
    "principal_actor",
    "antagonist",
    "pressure",
    "resolution_modes",
    "consequence_type",
)
_ARC_FIELDS = ("campaign_arc_id", "arc_role", "arc_sequence")


@dataclass(frozen=True)
class MissionSignatureOccurrence:
    topic_id: str
    item_id: str
    path: str
    campaign_arc_id: str = ""
    arc_role: str = ""
    arc_sequence: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "item_id": self.item_id,
            "path": self.path,
            "campaign_arc_id": self.campaign_arc_id,
            "arc_role": self.arc_role,
            "arc_sequence": self.arc_sequence,
        }


@dataclass(frozen=True)
class MissionPortfolioIssue:
    code: str
    topic_id: str
    item_id: str
    path: str
    message: str
    fingerprint: str = ""
    occurrences: tuple[MissionSignatureOccurrence, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "item_id": self.item_id,
            "path": self.path,
            "message": self.message,
            "fingerprint": self.fingerprint,
            "occurrences": [row.as_dict() for row in self.occurrences],
            "severity": "error",
            "blocking": True,
        }


class MissionPortfolioCompilationError(ValueError):
    def __init__(self, issues: Sequence[MissionPortfolioIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.topic_id}:{issue.item_id}:{issue.fingerprint}"
            for issue in self.issues
        )
        super().__init__("mission_portfolio_integrity_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "mission_portfolio_integrity_failed",
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


def _mission_domains(topic_graph: Mapping[str, Any] | None) -> set[str]:
    graph = _mapping(topic_graph)
    domains: set[str] = set()
    for node in _rows(graph.get("nodes")):
        topic_id = str(node.get("topic_id") or "")
        metadata = _mapping(node.get("metadata"))
        contract = _mapping(metadata.get("mission_signature_contract"))
        if topic_id and bool(contract.get("required")):
            domains.add(topic_id)
    metadata = _mapping(graph.get("metadata"))
    contract = _mapping(metadata.get("mission_signature_contract"))
    domains.update(str(value) for value in contract.get("domain_ids") or () if str(value))
    return domains


def _normalise_scalar(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _signature_payload(value: Any) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("mission_signature_must_be_object",)
    row = dict(value)
    issues: list[str] = []
    payload: dict[str, Any] = {}
    for field in _SIGNATURE_COMPONENTS:
        raw = row.get(field)
        if field == "resolution_modes":
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                issues.append("mission_resolution_modes_must_be_array")
                continue
            modes = sorted({_normalise_scalar(item) for item in raw if _normalise_scalar(item)})
            if not modes:
                issues.append("mission_resolution_modes_required")
                continue
            payload[field] = modes
            continue
        rendered = _normalise_scalar(raw)
        if not rendered:
            issues.append(f"mission_signature_component_required:{field}")
            continue
        payload[field] = rendered
    return (payload if not issues else None), tuple(issues)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _arc_values(row: Mapping[str, Any]) -> tuple[str, str, int, bool, bool]:
    arc_id = str(row.get("campaign_arc_id") or "").strip()
    role = str(row.get("arc_role") or "").strip()
    raw_sequence = row.get("arc_sequence")
    sequence = raw_sequence if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool) else 0
    any_declared = bool(arc_id or role or raw_sequence not in (None, "", 0))
    complete = bool(arc_id and role and sequence > 0)
    return arc_id, role, sequence, any_declared, complete


def _intentional_arc(occurrences: Sequence[MissionSignatureOccurrence]) -> bool:
    if len(occurrences) < 2:
        return False
    arc_ids = {row.campaign_arc_id for row in occurrences}
    if len(arc_ids) != 1 or not next(iter(arc_ids), ""):
        return False
    roles = [row.arc_role for row in occurrences]
    sequences = [row.arc_sequence for row in occurrences]
    return (
        all(roles)
        and len(set(roles)) == len(roles)
        and all(value > 0 for value in sequences)
        and sorted(sequences) == list(range(1, len(sequences) + 1))
    )


def _mission_rows(
    topic_rows: Sequence[Mapping[str, Any]],
    required_domains: set[str],
) -> tuple[tuple[str, str, str, dict[str, Any], bool], ...]:
    values: list[tuple[str, str, str, dict[str, Any], bool]] = []
    for topic_index, raw_topic in enumerate(topic_rows, start=1):
        topic = _mapping(raw_topic)
        candidate = _candidate(topic)
        topic_id = str(
            topic.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{topic_index}"
        )
        required = topic_id in required_domains
        for index, entity in enumerate(_rows(candidate.get("entities"))):
            if not required and "mission_signature" not in entity and not any(
                field in entity for field in _ARC_FIELDS
            ):
                continue
            item_id = str(entity.get("id") or f"{topic_id}:entity:{index + 1}")
            values.append((topic_id, item_id, f"/entities/{index}", entity, required))
        for index, thread in enumerate(_rows(candidate.get("story_threads"))):
            if "mission_signature" not in thread and not any(
                field in thread for field in _ARC_FIELDS
            ):
                continue
            item_id = str(thread.get("id") or f"{topic_id}:story_thread:{index + 1}")
            values.append((topic_id, item_id, f"/story_threads/{index}", thread, False))
    return tuple(values)


def mission_portfolio_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[MissionPortfolioIssue, ...]:
    """Validate required signatures and distinguish deliberate arcs from reskins."""

    required_domains = _mission_domains(topic_graph)
    issues: list[MissionPortfolioIssue] = []
    by_fingerprint: dict[str, list[MissionSignatureOccurrence]] = {}
    for topic_id, item_id, path, row, required in _mission_rows(
        topic_rows,
        required_domains,
    ):
        signature = row.get("mission_signature")
        if signature is None and required:
            issues.append(
                MissionPortfolioIssue(
                    code="mission_signature_required",
                    topic_id=topic_id,
                    item_id=item_id,
                    path=f"{path}/mission_signature",
                    message="Graph-owned mission domains require a structured mission signature.",
                )
            )
        payload, signature_issues = _signature_payload(signature) if signature is not None else (None, ())
        for code in signature_issues:
            issues.append(
                MissionPortfolioIssue(
                    code=code,
                    topic_id=topic_id,
                    item_id=item_id,
                    path=f"{path}/mission_signature",
                    message="Mission signature does not satisfy the graph-owned component contract.",
                )
            )
        arc_id, role, sequence, any_declared, complete = _arc_values(row)
        if any_declared and not complete:
            issues.append(
                MissionPortfolioIssue(
                    code="campaign_arc_declaration_incomplete",
                    topic_id=topic_id,
                    item_id=item_id,
                    path=path,
                    message=(
                        "Intentional arc membership requires campaign_arc_id, arc_role, "
                        "and a positive arc_sequence."
                    ),
                )
            )
        if payload is None:
            continue
        fingerprint = _fingerprint(payload)
        by_fingerprint.setdefault(fingerprint, []).append(
            MissionSignatureOccurrence(
                topic_id=topic_id,
                item_id=item_id,
                path=f"{path}/mission_signature",
                campaign_arc_id=arc_id,
                arc_role=role,
                arc_sequence=sequence,
            )
        )

    for fingerprint, occurrences in sorted(by_fingerprint.items()):
        unique = {
            (row.topic_id, row.item_id, row.path): row for row in occurrences
        }
        ordered = tuple(unique[key] for key in sorted(unique))
        if len(ordered) < 2 or _intentional_arc(ordered):
            continue
        first = ordered[0]
        issues.append(
            MissionPortfolioIssue(
                code="repeated_mission_signature",
                topic_id=first.topic_id,
                item_id=first.item_id,
                path=first.path,
                fingerprint=fingerprint,
                occurrences=ordered,
                message=(
                    "The same structured mission shape is reused without one complete, "
                    "ordered campaign-arc declaration."
                ),
            )
        )
    unique_issues = {
        (
            issue.code,
            issue.topic_id,
            issue.item_id,
            issue.path,
            issue.fingerprint,
        ): issue
        for issue in issues
    }
    return tuple(unique_issues[key] for key in sorted(unique_issues))


def mission_portfolio_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    required_domains = _mission_domains(topic_graph)
    rows = _mission_rows(topic_rows, required_domains)
    issues = mission_portfolio_issues(topic_rows, topic_graph)
    signed = sum(1 for *_prefix, row, _required in rows if row.get("mission_signature") is not None)
    declared_arcs = {
        str(row.get("campaign_arc_id") or "")
        for *_prefix, row, _required in rows
        if str(row.get("campaign_arc_id") or "")
    }
    return {
        "schema_version": "rpg_world_mission_portfolio_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "required_domain_count": len(required_domains),
            "mission_item_count": len(rows),
            "signed_mission_count": signed,
            "declared_arc_count": len(declared_arcs),
            "repeated_signature_count": sum(
                1 for issue in issues if issue.code == "repeated_mission_signature"
            ),
        },
    }


def require_valid_mission_portfolio(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = mission_portfolio_issues(topic_rows, topic_graph)
    if issues:
        raise MissionPortfolioCompilationError(issues)
    return mission_portfolio_report(topic_rows, topic_graph)


__all__ = [
    "MissionPortfolioCompilationError",
    "MissionPortfolioIssue",
    "MissionSignatureOccurrence",
    "mission_portfolio_issues",
    "mission_portfolio_report",
    "require_valid_mission_portfolio",
]
