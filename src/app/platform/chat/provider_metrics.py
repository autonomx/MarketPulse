"""Normalize provider response statistics for persisted chat metadata."""
from __future__ import annotations

from typing import Any
from app.providers.catalog import RUNTIME_STATS, provider_supports


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if parsed == parsed and parsed not in {float("inf"), float("-inf")} else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _finite_number(value)
        if parsed is not None:
            return parsed
    return None


def _first_integer(*values: Any) -> int | None:
    for value in values:
        parsed = _integer(value)
        if parsed is not None:
            return parsed
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def merge_provider_response_metrics(
    current: dict[str, Any] | None,
    response: Any,
    *,
    provider_id: str | None,
) -> dict[str, Any]:
    """Merge usage and LM Studio stats from one provider response or stream chunk."""
    metrics = dict(current or {})
    raw = _mapping(getattr(response, "raw_response", None))
    stats = _mapping(raw.get("stats"))
    usage = _mapping(getattr(response, "usage", None)) or _mapping(raw.get("usage"))
    prompt_token_details = _mapping(usage.get("prompt_tokens_details"))
    input_token_details = _mapping(usage.get("input_tokens_details"))
    provider_key = str(provider_id or "").strip().lower().removeprefix("llm:")

    # The normalized row is currently intended for LM Studio. Still accept an
    # explicit stats payload so compatible adapters can opt in without branching.
    if not provider_supports(provider_key, RUNTIME_STATS) and not stats:
        return metrics

    metrics["provider"] = provider_key or "lmstudio"

    tokens_per_second = _first_number(
        stats.get("tokens_per_second"),
        stats.get("tokensPerSecond"),
    )
    if tokens_per_second is not None:
        metrics["tokens_per_second"] = tokens_per_second

    output_tokens = _first_integer(
        usage.get("completion_tokens"),
        usage.get("output_tokens"),
        stats.get("total_output_tokens"),
        stats.get("predicted_tokens_count"),
    )
    if output_tokens is not None:
        metrics["output_tokens"] = output_tokens

    input_tokens = _first_integer(
        usage.get("prompt_tokens"),
        usage.get("input_tokens"),
        stats.get("input_tokens"),
    )
    if input_tokens is not None:
        metrics["input_tokens"] = input_tokens

    cached_input_tokens = _first_integer(
        prompt_token_details.get("cached_tokens"),
        input_token_details.get("cached_tokens"),
        usage.get("cached_input_tokens"),
        usage.get("cached_prompt_tokens"),
        stats.get("cached_input_tokens"),
        stats.get("cached_prompt_tokens"),
    )
    if cached_input_tokens is not None:
        metrics["cached_input_tokens"] = cached_input_tokens
        if input_tokens is not None:
            metrics["uncached_input_tokens"] = max(0, input_tokens - cached_input_tokens)
            if input_tokens > 0:
                metrics["prompt_cache_hit_ratio"] = min(
                    1.0,
                    cached_input_tokens / input_tokens,
                )

    total_tokens = _first_integer(
        usage.get("total_tokens"),
        stats.get("total_tokens"),
    )
    if total_tokens is not None:
        metrics["total_tokens"] = total_tokens

    generation_time = _first_number(
        stats.get("generation_time"),
        stats.get("generation_time_seconds"),
    )
    if generation_time is not None:
        metrics["generation_time_seconds"] = generation_time

    time_to_first_token = _first_number(
        stats.get("time_to_first_token"),
        stats.get("time_to_first_token_seconds"),
    )
    if time_to_first_token is not None:
        metrics["time_to_first_token_seconds"] = time_to_first_token

    draft_model = _first_text(stats.get("draft_model"))
    if draft_model is not None:
        metrics["draft_model"] = draft_model

    total_draft_tokens = _first_integer(stats.get("total_draft_tokens_count"))
    accepted_draft_tokens = _first_integer(stats.get("accepted_draft_tokens_count"))
    rejected_draft_tokens = _first_integer(stats.get("rejected_draft_tokens_count"))
    ignored_draft_tokens = _first_integer(stats.get("ignored_draft_tokens_count"))
    if total_draft_tokens is not None:
        metrics["total_draft_tokens"] = total_draft_tokens
    if accepted_draft_tokens is not None:
        metrics["accepted_draft_tokens"] = accepted_draft_tokens
    if rejected_draft_tokens is not None:
        metrics["rejected_draft_tokens"] = rejected_draft_tokens
    if ignored_draft_tokens is not None:
        metrics["ignored_draft_tokens"] = ignored_draft_tokens
    if total_draft_tokens and accepted_draft_tokens is not None:
        metrics["draft_acceptance_ratio"] = min(
            1.0,
            accepted_draft_tokens / total_draft_tokens,
        )

    stop_reason = _first_text(
        stats.get("stop_reason"),
        getattr(response, "finish_reason", None),
    )
    if stop_reason is not None:
        metrics["stop_reason"] = stop_reason

    finish_reason = _first_text(getattr(response, "finish_reason", None))
    if finish_reason is not None:
        metrics["finish_reason"] = finish_reason

    return metrics
