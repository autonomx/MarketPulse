"""Blocking reconciliation between internal World Forge plans and accepted canon."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from .canon_audit import CanonAuditIssue, CanonAuditReport
from .world_forge_generation import GeneratedTopic


def _entity_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("entity_id") or "").strip()


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _normal(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").split())


def _topic_entities(topics: Iterable[GeneratedTopic]) -> dict[str, dict[str, Mapping[str, Any]]]:
    return {
        topic.topic_id: {
            _entity_id(row): row
            for row in topic.entities
            if _entity_id(row)
        }
        for topic in topics
    }


def _anchor_rows(planning_topics: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    registry = planning_topics.get("anchor_registry")
    if not isinstance(registry, Mapping):
        return ()
    return tuple(row for row in registry.get("anchors") or () if isinstance(row, Mapping))


def audit_plan_to_canon(
    topics: Iterable[GeneratedTopic],
    planning_topics: Mapping[str, Any],
) -> tuple[CanonAuditIssue, ...]:
    by_topic = _topic_entities(tuple(topics))
    issues: list[CanonAuditIssue] = []

    expected_by_domain: dict[str, set[str]] = {}
    for anchor in _anchor_rows(planning_topics):
        expected_by_domain.setdefault(str(anchor.get("domain_id") or ""), set()).add(
            str(anchor.get("id") or "")
        )
    for domain_id, expected_ids in sorted(expected_by_domain.items()):
        if not domain_id or not expected_ids or domain_id not in by_topic:
            continue
        actual_ids = set(by_topic[domain_id])
        if actual_ids != expected_ids:
            missing = sorted(expected_ids - actual_ids)
            unexpected = sorted(actual_ids - expected_ids)
            issues.append(
                CanonAuditIssue(
                    "plan_anchor_identity_mismatch",
                    f"{domain_id} must materialise its authoritative anchor IDs; missing={missing}, unexpected={unexpected}.",
                    domain_id,
                )
            )

    place_entities = by_topic.get("places", {})
    settlement_plan = planning_topics.get("settlement_origin_plan")
    if isinstance(settlement_plan, Mapping):
        for row in settlement_plan.get("settlements") or ():
            if not isinstance(row, Mapping):
                continue
            place_id = str(row.get("place_id") or "")
            entity = place_entities.get(place_id)
            if entity is None:
                continue
            planned_region = str(row.get("region_id") or "")
            if planned_region and str(entity.get("region_id") or "") != planned_region:
                issues.append(
                    CanonAuditIssue(
                        "planned_settlement_region_mismatch",
                        f"Place must remain in planned region {planned_region}.",
                        place_id,
                    )
                )
            planned_event = str(row.get("founding_event_id") or "")
            if planned_event and planned_event not in _strings(entity.get("founding_event_ids")):
                issues.append(
                    CanonAuditIssue(
                        "planned_settlement_founding_event_mismatch",
                        f"Place must retain planned founding event {planned_event}.",
                        place_id,
                    )
                )
            planned_purpose = _normal(row.get("founding_purpose"))
            if planned_purpose and _normal(entity.get("founding_purpose")) != planned_purpose:
                issues.append(
                    CanonAuditIssue(
                        "planned_settlement_purpose_mismatch",
                        f"Place must retain planned founding purpose {row.get('founding_purpose')}.",
                        place_id,
                    )
                )

    culture_entities = by_topic.get("cultures", {})
    lineage_plan = planning_topics.get("culture_lineage_plan")
    if isinstance(lineage_plan, Mapping):
        for row in lineage_plan.get("lineages") or ():
            if not isinstance(row, Mapping):
                continue
            culture_id = str(row.get("culture_id") or "")
            entity = culture_entities.get(culture_id)
            if entity is None:
                continue
            planned_regions = set(_strings(row.get("homeland_region_ids")))
            actual_regions = set(_strings(entity.get("origin_region_ids")))
            if planned_regions and not planned_regions.issubset(actual_regions):
                issues.append(
                    CanonAuditIssue(
                        "planned_culture_homeland_mismatch",
                        f"Culture must retain planned homeland regions {sorted(planned_regions)}.",
                        culture_id,
                    )
                )
            planned_event = str(row.get("origin_event_id") or "")
            if planned_event and planned_event not in _strings(entity.get("origin_event_ids")):
                issues.append(
                    CanonAuditIssue(
                        "planned_culture_origin_event_mismatch",
                        f"Culture must retain planned origin event {planned_event}.",
                        culture_id,
                    )
                )
            planned_parent = str(row.get("parent_culture_id") or "")
            actual_parents = set(_strings(entity.get("parent_culture_ids")))
            if planned_parent and planned_parent not in actual_parents:
                issues.append(
                    CanonAuditIssue(
                        "planned_culture_parent_mismatch",
                        f"Culture must retain planned parent {planned_parent}.",
                        culture_id,
                    )
                )
            if not planned_parent and actual_parents:
                issues.append(
                    CanonAuditIssue(
                        "planned_culture_parent_mismatch",
                        "Culture introduced an unplanned parent lineage.",
                        culture_id,
                    )
                )

    group_entities = by_topic.get("groups", {})
    claim_plan = planning_topics.get("political_claim_graph")
    if isinstance(claim_plan, Mapping):
        for row in claim_plan.get("claims") or ():
            if not isinstance(row, Mapping):
                continue
            group_id = str(row.get("claimant_group_id") or "")
            entity = group_entities.get(group_id)
            if entity is None:
                continue
            rendered = json.dumps(entity.get("inherited_claims"), ensure_ascii=False, sort_keys=True)
            required_tokens = (
                str(row.get("claim_id") or ""),
                str(row.get("target_region_id") or ""),
            )
            if any(token and token not in rendered for token in required_tokens):
                issues.append(
                    CanonAuditIssue(
                        "planned_political_claim_missing",
                        f"Group must preserve planned claim {row.get('claim_id')} on {row.get('target_region_id')}.",
                        group_id,
                    )
                )

    return tuple(issues)


def attach_plan_reconciliation(
    topics: Iterable[GeneratedTopic],
    planning_topics: Mapping[str, Any],
    report: CanonAuditReport,
) -> CanonAuditReport:
    topic_rows = tuple(topics)
    findings = audit_plan_to_canon(topic_rows, planning_topics)
    issues = (*report.issues, *findings)
    errors = sum(1 for issue in issues if issue.severity == "error")
    return replace(
        report,
        passed=errors == 0,
        issues=issues,
        checks={
            **dict(report.checks),
            "plan_reconciliation_findings": len(findings),
            "plan_reconciliation_errors": sum(
                1 for issue in findings if issue.severity == "error"
            ),
            "errors": errors,
        },
    )


__all__ = ["attach_plan_reconciliation", "audit_plan_to_canon"]
