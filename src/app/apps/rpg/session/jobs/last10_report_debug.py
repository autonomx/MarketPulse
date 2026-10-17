"""Bounded debug extraction for RPG last-ten-turn reports."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.jobs.models import JobRecord
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

_DIAGNOSTIC_KEYS = (
    "raw_intent_diagnostics",
    "interactive_cli_intent_diagnostics",
    "first_call_grounding_diagnostics",
    "intent_diagnostics",
)
_SELECTION_SCALAR_KEYS = (
    "narration_source",
    "selected_narration_source",
    "selected_response_source",
    "response_source",
    "fallback_reason",
    "fallback_source",
    "visible_interaction_reason",
    "selection_reason",
)
_SELECTION_PAYLOAD_KEYS = (
    "response_selection_trace",
    "interactive_cli_response_quality",
    "final_narration_candidate",
    "visible_response",
    "accepted_candidate",
    "rejected_candidates",
)
_DIALOGUE_PAYLOAD_KEYS = (
    "dialogue_payload",
    "dialogue_diagnostics",
    "dialogue_response",
    "npc_dialogue",
    "npc_response",
    "social_runtime",
    "social_scene",
)
_PROVIDER_METRIC_KEYS = (
    "provider_elapsed_ms",
    "provider_duration_ms",
    "provider_total_ms",
    "intent_llm_ms",
    "dialogue_llm_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "model",
    "provider_status",
    "provider_called",
    "provider_requested",
    "intent_llm_used",
    "intent_fast_path_used",
)


def build_turn_debug_payload(job: JobRecord) -> dict[str, Any]:
    """Build non-mutating bounded debug evidence for one completed RPG turn job."""

    turn_response_record = _turn_response_record(job)
    intent = _intent_diagnostics(turn_response_record, job)
    dialogue = _dialogue_payload(turn_response_record)
    selection = _response_selection_trace(turn_response_record, intent)
    performance = _performance_trace(job, turn_response_record, intent)
    return {
        "turn_response_record": turn_response_record,
        "raw_intent_diagnostics": intent,
        "dialogue_payload": dialogue,
        "response_selection_trace": selection,
        "performance_trace": performance,
        "raw_input_payload": _dict_value(job.input_payload),
        "raw_output_refs": _list_value(job.output_refs),
        "raw_logs": _list_value(job.logs),
    }


def _turn_response_record(job: JobRecord) -> dict[str, Any]:
    refs = _list_value(job.output_refs)
    priority_types = {
        "rpg_turn_response",
        "rpg_turn_result",
        "rpg_turn_response_payload",
        "rpg_turn_debug",
        "rpg_runtime_result",
    }
    for ref in refs:
        record = _dict_value(ref)
        direct = _dict_value(record.get("turn_response"))
        if direct:
            return direct
        if _safe_str(record.get("type")) in priority_types:
            parsed = _record_payload(record)
            if parsed:
                return parsed
    for record in [_dict_value(item) for item in refs + _list_value(job.logs)]:
        parsed = _record_payload(record)
        if parsed:
            return parsed
    return {}


def _record_payload(record: dict[str, Any]) -> dict[str, Any]:
    for key in ("turn_response", "result", "payload", "content", "text", "message"):
        parsed = _parse_mapping(record.get(key))
        if parsed:
            return parsed
    return {}


def _intent_diagnostics(record: dict[str, Any], job: JobRecord) -> dict[str, Any]:
    roots: list[Any] = [record, job.input_payload, job.output_refs, job.logs]
    for root in roots:
        for key in _DIAGNOSTIC_KEYS:
            found = _find_key(root, key)
            if isinstance(found, dict):
                return found
    advisory = _find_key(record, "first_call_action_advisory") or _find_key(
        record,
        "first_call_semantic_advisory",
    )
    nested = _find_key(advisory, "first_call_grounding_diagnostics")
    return nested if isinstance(nested, dict) else {}


def _dialogue_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _DIALOGUE_PAYLOAD_KEYS:
        value = _find_key(record, key)
        if isinstance(value, dict):
            payload[key] = value
    for key in ("npc", "final_narration_candidate", "visible_response"):
        value = _find_key(record, key)
        if isinstance(value, dict):
            payload[key] = value
    for key in ("npc_speaker", "npc_line", "target_npc", "target_name", "target_id"):
        value = _find_key(record, key)
        if value not in (None, ""):
            payload[key] = value
    return payload


def _response_selection_trace(record: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    trace: dict[str, Any] = {}
    for key in _SELECTION_SCALAR_KEYS:
        value = _find_key(record, key)
        if value not in (None, ""):
            trace[key] = value
    for key in _SELECTION_PAYLOAD_KEYS:
        value = _find_key(record, key)
        if value not in (None, "", [], {}):
            trace[key] = value
    visible_response = _find_key(intent, "visible_response")
    if visible_response not in (None, "", [], {}):
        trace["intent_visible_response"] = visible_response
    if "fallback_reason" in trace and "selected_response_source" not in trace:
        trace["selected_response_source"] = "fallback"
    return trace


def _performance_trace(job: JobRecord, record: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    stage_timings = [_stage_timing(_stage_dict(stage)) for stage in _list_value(job.stages)]
    provider_metrics: dict[str, Any] = {}
    for root in (
        record,
        intent,
        _find_key(record, "performance_trace"),
        _find_key(record, "provider_metrics"),
    ):
        for key in _PROVIDER_METRIC_KEYS:
            value = _find_key(root, key)
            if value not in (None, ""):
                provider_metrics[key] = value
        usage = _find_key(root, "usage")
        if isinstance(usage, dict):
            provider_metrics["usage"] = usage
    return {
        "job_timing_seconds": {
            "created_to_started": _duration(job.created_at, job.started_at),
            "started_to_completed": _duration(
                job.started_at or job.created_at,
                job.completed_at or job.updated_at,
            ),
            "created_to_completed": _duration(job.created_at, job.completed_at or job.updated_at),
        },
        "stage_timings": stage_timings,
        "provider_metrics": provider_metrics,
    }


def _stage_timing(stage: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": stage.get("id"),
        "label": stage.get("label"),
        "status": stage.get("status"),
        "resource_class": stage.get("resource_class"),
        "started_at": stage.get("started_at"),
        "completed_at": stage.get("completed_at"),
        "duration_seconds": _duration(stage.get("started_at"), stage.get("completed_at")),
        "error": stage.get("error"),
    }


def _stage_dict(stage: Any) -> dict[str, Any]:
    if isinstance(stage, dict):
        return stage
    dump = getattr(stage, "model_dump", None)
    if callable(dump):
        return _dict_value(dump(mode="json"))
    return {}


def _duration(start: Any, end: Any) -> float | None:
    start_ts = _timestamp(start)
    end_ts = _timestamp(end)
    if start_ts <= 0 or end_ts <= 0 or end_ts < start_ts:
        return None
    return round(end_ts - start_ts, 3)


def _timestamp(value: Any) -> float:
    text = _safe_str(value)
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _find_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_key(child, key)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, key)
            if found not in (None, "", [], {}):
                return found
    return None


def _parse_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    for candidate in (text, _json_slice(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _json_slice(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else ""


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


