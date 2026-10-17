"""Compact replay-safe records for direct foreground RPG turns."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.narration.narrative_reference import canonical_narrative_reference
from app.apps.rpg.narration.presentation.turn_response import build_turn_response_v2
from app.apps.rpg.narration.presentation.turn_response_budget import (
    encoded_size_bytes,
    enforce_turn_response_budget,
)

FOREGROUND_TURN_RECORD_VERSION = "rpg_foreground_turn_record_v1"
FOREGROUND_TURN_RECORD_MAX_BYTES = 20_000
_SHORT_REPLAY_TEXT = "The turn completed. Its canonical presentation is available by reference."


def build_foreground_turn_record(
    result: dict[str, Any],
    *,
    session_id: str,
    submission_id: str,
    command: str,
) -> dict[str, Any]:
    """Project a runtime result into a bounded record that can be replayed safely."""

    source = result if isinstance(result, dict) else {}
    reference: dict[str, Any] = {}
    if source.get("ok") is not True:
        payload = _failed_record(
            source,
            session_id=session_id,
            submission_id=submission_id,
        )
    else:
        payload = build_turn_response_v2(
            source,
            session_id=session_id,
            command=command,
            session=None,
        )
        reference = canonical_narrative_reference(source)
        if reference:
            payload["canonical_narrative"] = reference
        payload["submission_id"] = submission_id
        payload["record_version"] = FOREGROUND_TURN_RECORD_VERSION
    bounded = enforce_turn_response_budget(
        payload,
        max_bytes=FOREGROUND_TURN_RECORD_MAX_BYTES,
    )
    return _preserve_canonical_reference(
        bounded,
        reference,
        session_id=session_id,
        submission_id=submission_id,
    )


def _preserve_canonical_reference(
    payload: dict[str, Any],
    reference: dict[str, Any],
    *,
    session_id: str,
    submission_id: str,
) -> dict[str, Any]:
    if not reference:
        return payload
    output = dict(payload)
    output["canonical_narrative"] = dict(reference)
    if encoded_size_bytes(output) <= FOREGROUND_TURN_RECORD_MAX_BYTES:
        return output

    visible = dict(output.get("visible_response") or {})
    visible.update(
        {
            "format_version": "rpg_visible_response_v1",
            "narration": "",
            "messages": [],
            "plain_text": _SHORT_REPLAY_TEXT,
        }
    )
    output["visible_response"] = visible
    output["response"] = _SHORT_REPLAY_TEXT
    output["content"] = _SHORT_REPLAY_TEXT
    if encoded_size_bytes(output) <= FOREGROUND_TURN_RECORD_MAX_BYTES:
        return output

    return {
        "ok": True,
        "contract_version": "rpg_turn_response_v2",
        "record_version": FOREGROUND_TURN_RECORD_VERSION,
        "session_id": session_id,
        "submission_id": submission_id,
        "turn_id": reference["turn_id"],
        "canonical_narrative": dict(reference),
        "visible_response": {
            "format_version": "rpg_visible_response_v1",
            "narration": "",
            "messages": [],
            "plain_text": _SHORT_REPLAY_TEXT,
        },
        "response": _SHORT_REPLAY_TEXT,
        "content": _SHORT_REPLAY_TEXT,
        "response_budget": {
            "compacted": True,
            "fallback": True,
            "canonical_reference_preserved": True,
            "maximum_bytes": FOREGROUND_TURN_RECORD_MAX_BYTES,
        },
    }


def _failed_record(
    result: dict[str, Any],
    *,
    session_id: str,
    submission_id: str,
) -> dict[str, Any]:
    error = str(result.get("error") or "direct_rpg_turn_failed").strip()
    message = str(result.get("message") or error or "The turn could not be completed.").strip()
    return {
        "ok": False,
        "contract_version": "rpg_turn_response_v2",
        "record_version": FOREGROUND_TURN_RECORD_VERSION,
        "session_id": session_id,
        "submission_id": submission_id,
        "interaction_id": _text(result.get("interaction_id")) or None,
        "turn_id": _text(result.get("turn_id")) or None,
        "error": error,
        "visible_response": {
            "format_version": "rpg_visible_response_v1",
            "narration": "",
            "messages": [],
            "plain_text": message,
        },
        "response": message,
        "content": message,
    }


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
