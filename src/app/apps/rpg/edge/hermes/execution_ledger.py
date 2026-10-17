from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import time
import uuid
from typing import Any

_LEDGER: list[tuple[float, dict[str, Any]]] = []
_MAX_LEDGER_ITEMS = 200
_LEDGER_TTL_SECONDS = 24 * 60 * 60


def _ledger_now() -> float:
    return time.monotonic()


def _prune_ledger(now: float) -> None:
    while _LEDGER and _LEDGER[0][0] <= now:
        _LEDGER.pop(0)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def hermes_rpg_execution_ledger_reset() -> None:
    _LEDGER.clear()


def hermes_rpg_execution_ledger_record(
    *,
    payload: dict[str, Any],
    config: dict[str, Any],
    flow: dict[str, Any],
    readout: dict[str, Any],
) -> dict[str, Any]:
    now = _ledger_now()
    _prune_ledger(now)
    user_step = _mapping(payload.get("user_step"))
    replay_entry = _mapping(payload.get("replay_entry"))
    context = _mapping(payload.get("context"))
    flow_result = _mapping(flow.get("result"))
    rpg_result = _mapping(flow_result.get("rpg_result"))
    flow_error = _text(flow.get("error")) or _text(flow_result.get("error"))
    command_text = _text(readout.get("command_text")) or _text(user_step.get("command_text")) or _text(replay_entry.get("command_text"))
    session_id = _text(readout.get("session_id")) or _text(context.get("session_id"))
    context_hash = _text(readout.get("context_hash")) or _text(context.get("context_hash"))
    sequence_id = _text(context.get("sequence_id"))
    item_id = _text(context.get("item_id"))
    if context_hash and context_hash.startswith("sequence:"):
        parts = context_hash.split(":")
        sequence_id = sequence_id or _text(parts[1] if len(parts) > 1 else None)
        item_id = item_id or _text(parts[2] if len(parts) > 2 else None)
    entry = {
        "ok": flow.get("ok") is True,
        "source": "hermes_rpg_execution_ledger",
        "execution_id": f"hermes-rpg-{uuid.uuid4().hex}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "context_hash": context_hash,
        "command_text": command_text,
        "sequence_id": sequence_id,
        "item_id": item_id,
        "approval_source": _text(context.get("approval_source")) or "approved_flow",
        "checkpoint_reason": _text(context.get("checkpoint_reason")),
        "config_enabled": config.get("enabled") is True,
        "readout_status": readout.get("status"),
        "rpg_ok": readout.get("rpg_ok") is True,
        "state_changed": flow.get("state_changed") is True,
        "turn": rpg_result.get("turn"),
        "result_summary": _text(rpg_result.get("narration")) or _text(rpg_result.get("summary")) or _text(readout.get("status")),
        "error": readout.get("error") or flow_error,
    }
    _LEDGER.append((now + _LEDGER_TTL_SECONDS, entry))
    if len(_LEDGER) > _MAX_LEDGER_ITEMS:
        del _LEDGER[:-_MAX_LEDGER_ITEMS]
    return deepcopy(entry)


def hermes_rpg_execution_ledger_recent(limit: int = 20, *, session_id: str | None = None, sequence_id: str | None = None) -> dict[str, Any]:
    _prune_ledger(_ledger_now())
    safe_limit = min(max(int(limit or 20), 1), _MAX_LEDGER_ITEMS)
    filtered = [
        item
        for _expires_at, item in _LEDGER
        if (not session_id or item.get("session_id") == session_id)
        and (not sequence_id or item.get("sequence_id") == sequence_id)
    ]
    items = [deepcopy(item) for item in reversed(filtered[-safe_limit:])]
    return {
        "ok": True,
        "source": "hermes_rpg_execution_ledger",
        "items": items,
        "count": len(items),
        "filters": {"session_id": session_id, "sequence_id": sequence_id, "limit": safe_limit},
    }
