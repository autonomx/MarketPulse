"""Runtime byte-budget enforcement for foreground RPG turn responses."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

_DEFAULT_MAX_BYTES = 50_000
_SHORTENED_TEXT = (
    "The turn completed, but its presentation was shortened to keep the response safe."
)


def enforce_turn_response_budget(
    payload: dict[str, Any],
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Return a contract-preserving payload whose encoded size is within the limit."""

    limit = max(1_024, int(max_bytes))
    original_bytes = encoded_size_bytes(payload)
    if original_bytes <= limit:
        return payload

    compacted = _compact_payload(payload, original_bytes=original_bytes, max_bytes=limit)
    if encoded_size_bytes(compacted) <= limit:
        return compacted

    fallback = _fallback_payload(
        compacted,
        original_bytes=original_bytes,
        max_bytes=limit,
    )
    if encoded_size_bytes(fallback) <= limit:
        return fallback

    absolute = _absolute_fallback(
        fallback,
        original_bytes=original_bytes,
        max_bytes=limit,
    )
    if encoded_size_bytes(absolute) > limit:
        raise ValueError("RPG turn response could not be reduced below its byte budget")
    return absolute


def encoded_size_bytes(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    )


def _compact_payload(
    payload: dict[str, Any],
    *,
    original_bytes: int,
    max_bytes: int,
) -> dict[str, Any]:
    compacted = deepcopy(payload)
    for key in (
        "session_id",
        "submission_id",
        "interaction_id",
        "turn_id",
        "job_id",
        "trace_id",
    ):
        if key in compacted:
            compacted[key] = _truncate_utf8(compacted.get(key), 512)
    if "command" in compacted:
        compacted["command"] = _truncate_utf8(compacted.get("command"), 2_000)

    visible = _compact_visible_response(compacted.get("visible_response"))
    compacted["visible_response"] = visible
    visible_text = _truncate_utf8(visible.get("plain_text"), 12_000)
    compacted["response"] = visible_text
    compacted["content"] = visible_text

    result = _dict(compacted.get("result"))
    result.pop("visible_response", None)
    result["timing"] = _numeric_dict(result.get("timing"), max_items=20)
    compacted["result"] = _drop_empty(result)
    compacted["timing"] = _numeric_dict(compacted.get("timing"), max_items=20)
    compacted["state"] = _compact_state(compacted.get("state"))
    compacted["session_summary"] = _bounded_dict(
        compacted.get("session_summary"),
        max_items=12,
        text_bytes=512,
    )
    compacted["creation_server_trace"] = _bounded_dict(
        compacted.get("creation_server_trace"),
        max_items=12,
        text_bytes=512,
    )
    compacted["response_budget"] = {
        "compacted": True,
        "fallback": False,
        "original_bytes": original_bytes,
        "maximum_bytes": max_bytes,
    }
    return _drop_empty(compacted)


def _fallback_payload(
    payload: dict[str, Any],
    *,
    original_bytes: int,
    max_bytes: int,
) -> dict[str, Any]:
    visible = _dict(payload.get("visible_response"))
    text = _truncate_utf8(visible.get("plain_text") or payload.get("response"), 8_000)
    if not text:
        text = _SHORTENED_TEXT
    fallback = {
        "ok": payload.get("ok") is not False,
        "contract_version": _truncate_utf8(payload.get("contract_version"), 128),
        "session_id": _truncate_utf8(payload.get("session_id"), 256),
        "submission_id": _truncate_utf8(payload.get("submission_id"), 256),
        "interaction_id": _truncate_utf8(payload.get("interaction_id"), 256),
        "turn_id": _truncate_utf8(payload.get("turn_id"), 256),
        "simulation_tick": _safe_scalar(payload.get("simulation_tick")),
        "job_id": _truncate_utf8(payload.get("job_id"), 256),
        "trace_id": _truncate_utf8(payload.get("trace_id"), 256),
        "visible_response": {
            "format_version": _truncate_utf8(
                visible.get("format_version") or "rpg_visible_response_v1",
                128,
            ),
            "narration": "",
            "messages": [],
            "plain_text": text,
        },
        "response": text,
        "content": text,
        "state": _compact_state(payload.get("state")),
        "timing": _numeric_dict(payload.get("timing"), max_items=12),
        "response_budget": {
            "compacted": True,
            "fallback": True,
            "original_bytes": original_bytes,
            "maximum_bytes": max_bytes,
        },
    }
    return _drop_empty(fallback)


def _absolute_fallback(
    payload: dict[str, Any],
    *,
    original_bytes: int,
    max_bytes: int,
) -> dict[str, Any]:
    text = _truncate_utf8(
        _dict(payload.get("visible_response")).get("plain_text") or _SHORTENED_TEXT,
        512,
    )
    return _drop_empty(
        {
            "ok": payload.get("ok") is not False,
            "contract_version": "rpg_turn_response_v2",
            "session_id": _truncate_utf8(payload.get("session_id"), 96),
            "submission_id": _truncate_utf8(payload.get("submission_id"), 96),
            "interaction_id": _truncate_utf8(payload.get("interaction_id"), 96),
            "turn_id": _truncate_utf8(payload.get("turn_id"), 96),
            "visible_response": {
                "format_version": "rpg_visible_response_v1",
                "narration": "",
                "messages": [],
                "plain_text": text,
            },
            "response": text,
            "content": text,
            "response_budget": {
                "compacted": True,
                "fallback": True,
                "absolute": True,
                "original_bytes": original_bytes,
                "maximum_bytes": max_bytes,
            },
        }
    )


def _compact_visible_response(value: Any) -> dict[str, Any]:
    visible = _dict(value)
    narration = _truncate_utf8(visible.get("narration"), 8_000)
    messages: list[dict[str, Any]] = []
    raw_messages = visible.get("messages")
    if isinstance(raw_messages, list):
        for raw in raw_messages[:8]:
            message = _dict(raw)
            bounded = _drop_empty(
                {
                    "kind": _truncate_utf8(message.get("kind"), 64),
                    "speaker_id": _truncate_utf8(message.get("speaker_id"), 256),
                    "speaker": _truncate_utf8(message.get("speaker"), 128),
                    "text": _truncate_utf8(message.get("text"), 4_000),
                }
            )
            if bounded:
                messages.append(bounded)
    plain_text = _truncate_utf8(visible.get("plain_text"), 12_000)
    if not plain_text:
        paragraphs = [narration] if narration else []
        for message in messages:
            text = _truncate_utf8(message.get("text"), 4_000)
            speaker = _truncate_utf8(message.get("speaker"), 128)
            if text:
                paragraphs.append(f'{speaker}: "{text}"' if speaker else text)
        plain_text = _truncate_utf8("\n\n".join(paragraphs), 12_000)
    if not plain_text:
        plain_text = _SHORTENED_TEXT
    return _drop_empty(
        {
            "format_version": _truncate_utf8(
                visible.get("format_version") or "rpg_visible_response_v1",
                128,
            ),
            "narration": narration,
            "messages": messages,
            "plain_text": plain_text,
        }
    )


def _compact_state(value: Any) -> dict[str, Any]:
    state = _dict(value)
    domains: list[str] = []
    raw_domains = state.get("changed_domains")
    if isinstance(raw_domains, list):
        domains = [
            text
            for item in raw_domains[:12]
            if (text := _truncate_utf8(item, 128))
        ]
    return _drop_empty(
        {
            "revision": _safe_scalar(state.get("revision")),
            "changed": bool(state.get("changed")),
            "changed_domains": domains,
        }
    )


def _bounded_dict(value: Any, *, max_items: int, text_bytes: int) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, item in list(_dict(value).items())[:max_items]:
        bounded_key = _truncate_utf8(key, 128)
        if not bounded_key:
            continue
        output[bounded_key] = (
            _safe_scalar(item)
            if isinstance(item, (bool, int, float)) or item is None
            else _truncate_utf8(item, text_bytes)
        )
    return _drop_empty(output)


def _numeric_dict(value: Any, *, max_items: int) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, item in list(_dict(value).items())[:max_items]:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        output[_truncate_utf8(key, 128)] = item
    return output


def _truncate_utf8(value: Any, maximum_bytes: int) -> str:
    text = str(value).strip() if value is not None else ""
    encoded = text.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return text
    suffix = "..."
    budget = max(0, maximum_bytes - len(suffix))
    shortened = encoded[:budget]
    while shortened:
        try:
            return shortened.decode("utf-8") + suffix
        except UnicodeDecodeError:
            shortened = shortened[:-1]
    return suffix[:maximum_bytes]


def _safe_scalar(value: Any) -> Any:
    return value if value is None or isinstance(value, (bool, int, float)) else None


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != {} and item != [] and item != ""
    }


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
