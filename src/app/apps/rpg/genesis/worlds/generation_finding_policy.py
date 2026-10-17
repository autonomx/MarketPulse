"""Central severity and waiver policy for World Forge findings."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

_FATAL_CODES = {
    "duplicate_id",
    "duplicate_canonical_identifier",
    "topic_candidate_invalid",
    "topic_candidate_missing",
    "unresolved_profile_reference",
    "profile_reference_wrong_domain",
    "dangling_relationship_endpoint",
    "geography_cycle",
}
_WARNING_CODES = {
    "section_title_required",
    "naming_similarity",
    "near_duplicate_name",
    "lexical_repetition",
    "style_repetition",
    "presentation_summary_short",
}
_ALLOWED_SEVERITIES = {"info", "warning", "error", "fatal"}
_WAIVABLE_SEVERITIES = {"info", "warning"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _findings(validation: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = validation.get("outstanding_findings")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        value = validation.get("issues")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def finding_severity(finding: Mapping[str, Any]) -> str:
    """Resolve severity fail-closed, with structural codes overriding provider labels."""

    code = str(finding.get("code") or "unknown")
    if code in _FATAL_CODES:
        return "fatal"
    explicit = str(finding.get("severity") or "").strip().casefold()
    if explicit in _ALLOWED_SEVERITIES:
        return explicit
    if code in _WARNING_CODES:
        return "warning"
    return "error"


def _finding_key(topic_id: str, finding: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        topic_id,
        str(finding.get("code") or "unknown"),
        str(finding.get("item_id") or finding.get("entity_id") or ""),
        str(finding.get("field_id") or finding.get("field") or ""),
        str(finding.get("message") or ""),
    )


def finding_waiver_policy_report(
    result_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify retained findings and decide whether waivers permit certification."""

    findings_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    active_waivers: list[dict[str, Any]] = []
    invalid_waivers: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    severity_counts: Counter[str] = Counter()

    for row in result_rows:
        topic_id = str(row.get("topic_id") or "")
        validation = _mapping(row.get("validation"))
        waiver = _mapping(validation.get("waiver"))
        waiver_status = str(
            validation.get("waiver_status") or waiver.get("status") or "none"
        ).casefold()
        reason = str(waiver.get("reason") or "").strip()
        accepted_by = str(waiver.get("accepted_by") or validation.get("accepted_by") or "")
        accepted_at = str(waiver.get("accepted_at") or validation.get("accepted_at") or "")

        for finding in _findings(validation):
            key = _finding_key(topic_id, finding)
            if key in findings_by_key:
                continue
            severity = finding_severity(finding)
            record = {
                **finding,
                "topic_id": topic_id,
                "severity": severity,
                "waiver_status": waiver_status,
            }
            findings_by_key[key] = record
            severity_counts[severity] += 1

            if waiver_status != "active":
                blocking.append({**record, "policy_reason": "finding_not_waived"})
                continue
            waiver_record = {
                "topic_id": topic_id,
                "code": str(finding.get("code") or "unknown"),
                "item_id": str(finding.get("item_id") or finding.get("entity_id") or ""),
                "field_id": str(finding.get("field_id") or finding.get("field") or ""),
                "severity": severity,
                "reason": reason,
                "accepted_by": accepted_by,
                "accepted_at": accepted_at,
            }
            if not reason:
                invalid = {**waiver_record, "policy_reason": "waiver_reason_required"}
                invalid_waivers.append(invalid)
                blocking.append({**record, "policy_reason": "waiver_reason_required"})
            elif severity not in _WAIVABLE_SEVERITIES:
                invalid = {**waiver_record, "policy_reason": "severity_not_waivable"}
                invalid_waivers.append(invalid)
                blocking.append({**record, "policy_reason": "severity_not_waivable"})
            else:
                active_waivers.append(waiver_record)

    return {
        "schema_version": "rpg_world_finding_waiver_policy_v1",
        "passed": not blocking and not invalid_waivers,
        "severity_counts": dict(sorted(severity_counts.items())),
        "finding_count": len(findings_by_key),
        "active_waiver_count": len(active_waivers),
        "blocking_finding_count": len(blocking),
        "invalid_waiver_count": len(invalid_waivers),
        "active_waivers": active_waivers,
        "blocking_findings": blocking,
        "invalid_waivers": invalid_waivers,
        "waivable_severities": sorted(_WAIVABLE_SEVERITIES),
    }


__all__ = ["finding_severity", "finding_waiver_policy_report"]
