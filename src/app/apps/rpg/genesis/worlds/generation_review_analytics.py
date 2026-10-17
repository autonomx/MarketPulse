"""Analytics projections for immutable World Forge review results."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence


def _value(result: Mapping[str, Any], run: Mapping[str, Any], key: str) -> str:
    provider = result.get("provider")
    provider = provider if isinstance(provider, Mapping) else {}
    settings = run.get("settings")
    settings = settings if isinstance(settings, Mapping) else {}
    aliases = {
        "model": ("model",),
        "prompt_version": ("prompt_version", "prompt_contract"),
        "provider": ("provider", "provider_route"),
    }
    for candidate in aliases.get(key, (key,)):
        text = str(provider.get(candidate) or settings.get(candidate) or "").strip()
        if text:
            return text
    return "unknown"


def world_generation_review_analytics(
    results: Sequence[Mapping[str, Any]],
    run: Mapping[str, Any],
) -> dict[str, Any]:
    by_code: Counter[str] = Counter()
    by_field: Counter[str] = Counter()
    by_topic: Counter[str] = Counter()
    by_domain: Counter[str] = Counter()
    by_model: Counter[str] = Counter()
    by_prompt_version: Counter[str] = Counter()
    by_provider: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()

    for result in results:
        topic_id = str(result.get("topic_id") or "unknown")
        status_counts[str(result.get("status") or "unknown")] += 1
        validation = result.get("validation")
        validation = validation if isinstance(validation, Mapping) else {}
        issues = [row for row in validation.get("issues") or () if isinstance(row, Mapping)]
        if not issues:
            issues = [
                {"code": code, "topic_id": topic_id}
                for code in validation.get("reason_codes") or ()
            ]
        for issue in issues:
            code = str(issue.get("code") or "unknown")
            field_id = str(issue.get("field_id") or "")
            issue_topic = str(issue.get("topic_id") or topic_id)
            by_code[code] += 1
            by_topic[issue_topic] += 1
            by_domain[issue_topic] += 1
            if field_id:
                by_field[field_id] += 1
            by_model[_value(result, run, "model")] += 1
            by_prompt_version[_value(result, run, "prompt_version")] += 1
            by_provider[_value(result, run, "provider")] += 1

    return {
        "status": dict(sorted(status_counts.items())),
        "by_code": dict(sorted(by_code.items())),
        "by_field": dict(sorted(by_field.items())),
        "by_topic": dict(sorted(by_topic.items())),
        "by_domain": dict(sorted(by_domain.items())),
        "by_model": dict(sorted(by_model.items())),
        "by_prompt_version": dict(sorted(by_prompt_version.items())),
        "by_provider": dict(sorted(by_provider.items())),
    }


__all__ = ["world_generation_review_analytics"]
