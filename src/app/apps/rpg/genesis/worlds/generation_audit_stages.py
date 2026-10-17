"""Two-stage audit evidence for World Forge compilation."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.canon_audit import audit_generated_canon
from app.apps.rpg.genesis.forge.canon_relationships import compile_cross_domain_relationships
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.forge.world_forge_quality import apply_world_forge_quality_audit


def _candidate(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = row.get("candidate")
    if isinstance(value, Mapping):
        return value
    value = row.get("content")
    return value if isinstance(value, Mapping) else None


def _issue_key(issue: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(issue.get("code") or "unknown"),
        str(issue.get("item_id") or ""),
        str(issue.get("severity") or "error"),
    )


def _issue_payload(key: tuple[str, str, str]) -> dict[str, str]:
    return {"code": key[0], "item_id": key[1], "severity": key[2]}


def pre_repair_audit_report(
    topic_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit raw assembled candidates before deterministic repair mutates them."""

    topics: list[GeneratedTopic] = []
    parse_issues: list[dict[str, Any]] = []
    for row in topic_rows:
        topic_id = str(row.get("topic_id") or "")
        payload = _candidate(row)
        if payload is None:
            parse_issues.append(
                {
                    "code": "topic_candidate_missing",
                    "message": "Topic has no candidate or authoring content.",
                    "item_id": topic_id,
                    "severity": "error",
                }
            )
            continue
        try:
            topics.append(GeneratedTopic.from_dict(dict(payload)))
        except (TypeError, ValueError, KeyError) as exc:
            parse_issues.append(
                {
                    "code": "topic_candidate_invalid",
                    "message": str(exc),
                    "item_id": topic_id,
                    "severity": "error",
                }
            )

    relationships = compile_cross_domain_relationships(tuple(topics))
    report = audit_generated_canon(
        tuple(topics),
        compiled_relationships=relationships,
    )
    report = apply_world_forge_quality_audit(tuple(topics), report)
    payload = report.as_dict()
    issues = [*parse_issues, *list(payload.get("issues") or ())]
    payload["issues"] = issues
    payload["passed"] = bool(payload.get("passed")) and not parse_issues
    payload["stage"] = "pre_repair"
    payload["topic_count"] = len(topics)
    return payload


def two_stage_audit_report(
    topic_rows: Sequence[Mapping[str, Any]],
    post_repair_report: Mapping[str, Any] | None,
    *,
    default_post_passed: bool = False,
) -> dict[str, Any]:
    """Compare raw findings with the final post-repair consistency audit."""

    pre = pre_repair_audit_report(topic_rows)
    post = dict(post_repair_report or {})
    post.setdefault("passed", default_post_passed)
    post.setdefault("issues", [])
    post["stage"] = "post_repair"

    pre_keys = {
        _issue_key(issue)
        for issue in pre.get("issues") or ()
        if isinstance(issue, Mapping)
    }
    post_keys = {
        _issue_key(issue)
        for issue in post.get("issues") or ()
        if isinstance(issue, Mapping)
    }
    resolved = sorted(pre_keys - post_keys)
    persistent = sorted(pre_keys & post_keys)
    introduced = sorted(post_keys - pre_keys)
    return {
        "schema_version": "rpg_world_two_stage_audit_v1",
        "passed": bool(post.get("passed")),
        "pre_repair": pre,
        "post_repair": post,
        "repair_delta": {
            "resolved": [_issue_payload(key) for key in resolved],
            "persistent": [_issue_payload(key) for key in persistent],
            "introduced": [_issue_payload(key) for key in introduced],
            "resolved_count": len(resolved),
            "persistent_count": len(persistent),
            "introduced_count": len(introduced),
        },
    }


__all__ = ["pre_repair_audit_report", "two_stage_audit_report"]
