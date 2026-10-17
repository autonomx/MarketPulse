from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.paths import resources_data_root

SOURCE = "hermes_sequence_state_store"
STATE_VERSION = "hermes_sequence_state_v1"
MAX_STATES = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _default_store_path() -> Path:
    return resources_data_root() / "hermes_rpg_sequences.json"


def _read_store(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw.get("items") if isinstance(raw, dict) else raw
    return [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _write_store(path: Path, states: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": STATE_VERSION, "items": states[-MAX_STATES:]}, indent=2, sort_keys=True), encoding="utf-8")


def write_hermes_sequence_state(state: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    store_path = path or _default_store_path()
    states = _read_store(store_path)
    session_id = _text(state.get("session_id")) or "default"
    sequence_id = _text(state.get("sequence_id")) or "hermes-sequence-draft"
    state = deepcopy(state)
    state["updated_at"] = _now()
    states = [item for item in states if not (item.get("session_id") == session_id and item.get("sequence_id") == sequence_id)]
    states.append(state)
    _write_store(store_path, states)
    return deepcopy(state)


def _current_item_index(items: list[dict[str, Any]]) -> int:
    for index, item in enumerate(items):
        if item.get("status") not in {"done", "completed", "blocked"}:
            return index
    return len(items)


def _item_statuses(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        statuses.append(
            {
                "item_index": index,
                "item_id": _text(item.get("item_id")) or f"item-{index + 1}",
                "status": _text(item.get("status")) or "pending",
                "command_text": _text(item.get("statement")),
            }
        )
    return statuses


def _blocked_reason(review_payload: dict[str, Any], sequence: dict[str, Any]) -> str:
    validation = _mapping(review_payload.get("validation"))
    errors = _list(validation.get("errors"))
    for error in errors:
        text = _text(error)
        if text:
            return text
    gate = _mapping(review_payload.get("gate"))
    checkpoint = _mapping(review_payload.get("checkpoint"))
    reason = _text(checkpoint.get("reason"))
    if reason:
        return reason
    loop_guard = _mapping(review_payload.get("loop_guard"))
    reason = _text(loop_guard.get("stop_reason"))
    if reason:
        return reason
    for decision in _list(gate.get("decisions")):
        mapping = _mapping(decision)
        if mapping.get("allowed") is False:
            return _text(mapping.get("reason")) or "blocked"
    if not _list(sequence.get("items")):
        return "missing_items"
    return ""


def build_hermes_sequence_state(
    *,
    session_id: str,
    review_payload: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sequence = _mapping(review_payload.get("sequence"))
    items = [_mapping(item) for item in _list(sequence.get("items"))]
    created_at = _text(_mapping(existing).get("created_at")) or _now()
    blocked_reason = _blocked_reason(review_payload, sequence)
    status = "blocked" if blocked_reason else "ready"
    if review_payload.get("ok") is True:
        status = "ready"
    elif not items:
        status = "invalid"
    return {
        "ok": not blocked_reason and bool(items),
        "source": SOURCE,
        "version": STATE_VERSION,
        "session_id": session_id,
        "sequence_id": _text(sequence.get("sequence_id")) or "hermes-sequence-draft",
        "status": status,
        "current_item_index": _current_item_index(items),
        "item_statuses": _item_statuses(items),
        "last_result": deepcopy(_mapping(_mapping(existing).get("last_result"))),
        "blocked_reason": blocked_reason,
        "sequence": deepcopy(sequence),
        "created_at": created_at,
        "updated_at": _now(),
    }


def save_hermes_sequence_state(
    *,
    session_id: str,
    review_payload: dict[str, Any],
    path: Path | None = None,
) -> dict[str, Any]:
    safe_session_id = _text(session_id) or "default"
    store_path = path or _default_store_path()
    states = _read_store(store_path)
    sequence_id = _text(_mapping(review_payload.get("sequence")).get("sequence_id")) or "hermes-sequence-draft"
    existing = next((state for state in reversed(states) if state.get("session_id") == safe_session_id and state.get("sequence_id") == sequence_id), None)
    state = build_hermes_sequence_state(session_id=safe_session_id, review_payload=review_payload, existing=existing)
    states = [item for item in states if not (item.get("session_id") == safe_session_id and item.get("sequence_id") == sequence_id)]
    states.append(state)
    _write_store(store_path, states)
    return deepcopy(state)


def latest_hermes_sequence_state(*, session_id: str, path: Path | None = None) -> dict[str, Any]:
    safe_session_id = _text(session_id)
    states = _read_store(path or _default_store_path())
    candidates = [state for state in states if not safe_session_id or state.get("session_id") == safe_session_id]
    if not candidates:
        return {"ok": False, "source": SOURCE, "state": None, "error": "sequence_state_not_found"}
    return {"ok": True, "source": SOURCE, "state": deepcopy(candidates[-1])}


def apply_hermes_sequence_item_result(
    state: dict[str, Any],
    *,
    item_index: int,
    result: dict[str, Any],
) -> dict[str, Any]:
    updated = deepcopy(state)
    sequence = _mapping(updated.get("sequence"))
    items = [_mapping(item) for item in _list(sequence.get("items"))]
    accepted = result.get("ok") is True
    if 0 <= item_index < len(items):
        items[item_index]["status"] = "done" if accepted else "blocked"
        sequence["items"] = items
    updated["sequence"] = sequence
    updated["item_statuses"] = _item_statuses(items)
    updated["current_item_index"] = _current_item_index(items)
    updated["last_result"] = deepcopy(result)
    updated["blocked_reason"] = "" if accepted else _text(result.get("error")) or "execution_blocked"
    updated["status"] = "completed" if accepted and updated["current_item_index"] >= len(items) else "running" if accepted else "blocked"
    updated["ok"] = accepted
    updated["updated_at"] = _now()
    return updated
