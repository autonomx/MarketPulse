from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any, cast

from app.apps.rpg.edge.hermes.approved_config import hermes_rpg_approved_flow_config_payload
from app.apps.rpg.edge.hermes.approved_flow import hermes_rpg_approved_flow
from app.apps.rpg.edge.hermes.canonical_submitter import hermes_rpg_canonical_submitter
from app.apps.rpg.edge.hermes.execution_ledger import hermes_rpg_execution_ledger_record
from app.apps.rpg.edge.hermes.flow_readout import hermes_rpg_flow_readout
from app.apps.rpg.edge.hermes.submit_bridge import RpgSubmitter
from app.apps.rpg.edge.hermes.assist_mode import hermes_assist_mode_policy
from app.apps.rpg.edge.hermes.sequence_state import (
    apply_hermes_sequence_item_result,
    latest_hermes_sequence_state,
    write_hermes_sequence_state,
)
from app.apps.rpg.edge.hermes.sequence_loop_guard import hermes_sequence_loop_guard

StateLoader = Callable[[str], dict[str, Any]]
StateWriter = Callable[[dict[str, Any]], dict[str, Any]]


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _default_loader(session_id: str) -> dict[str, Any]:
    return latest_hermes_sequence_state(session_id=session_id)


def _default_writer(state: dict[str, Any]) -> dict[str, Any]:
    return write_hermes_sequence_state(state)


def _next_preview(items: list[dict[str, Any]], current_item_index: int) -> dict[str, Any] | None:
    if 0 <= current_item_index < len(items):
        return deepcopy(items[current_item_index])
    return None


def _approved_flow_payload(
    *,
    command_text: str,
    context_hash: str,
    session_id: str,
    submitter: RpgSubmitter | None,
    environ: Mapping[str, str] | None,
) -> dict[str, Any]:
    config = hermes_rpg_approved_flow_config_payload(environ)
    if config.get("enabled") is not True:
        return {
            "ok": False,
            "source": "hermes_rpg_approved_flow_route",
            "error": "hermes_rpg_approved_flow_disabled",
            "enabled": False,
            "config": config,
            "state_changed": False,
        }
    payload = {
        "enabled": True,
        "user_step": {"ready": True, "command_text": command_text},
        "replay_entry": {"ok": True, "command_text": command_text},
        "context": {
            "session_id": session_id,
            "context_hash": context_hash,
            "approval_source": "sequence_step",
            "sequence_id": context_hash.split(":")[1] if context_hash.startswith("sequence:") and len(context_hash.split(":")) > 1 else None,
            "item_id": context_hash.split(":")[2] if context_hash.startswith("sequence:") and len(context_hash.split(":")) > 2 else None,
        },
    }
    flow = hermes_rpg_approved_flow(
        cast(Any, payload["user_step"]),
        cast(Any, payload["replay_entry"]),
        cast(Any, payload["context"]),
        submitter or hermes_rpg_canonical_submitter,
    )
    readout = hermes_rpg_flow_readout(flow)
    ledger_entry = hermes_rpg_execution_ledger_record(payload=payload, config=config, flow=flow, readout=readout)
    return {
        "ok": flow.get("ok") is True,
        "source": "hermes_rpg_approved_flow_route",
        "enabled": True,
        "config": config,
        "flow": flow,
        "readout": readout,
        "ledger_entry": ledger_entry,
        "state_changed": flow.get("state_changed") is True,
    }


def hermes_rpg_sequence_execute_step_payload(
    payload: dict[str, Any] | None,
    *,
    submitter: RpgSubmitter | None = None,
    environ: Mapping[str, str] | None = None,
    state_loader: StateLoader = _default_loader,
    state_writer: StateWriter = _default_writer,
) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    session_id = _text(data.get("session_id"))
    if not session_id:
        return {"ok": False, "source": "hermes_sequence_approved_executor", "error": "missing_session_id", "state_changed": False}
    mode = hermes_assist_mode_policy(data.get("assist_mode"))
    if mode.get("execution_allowed") is not True:
        return {
            "ok": False,
            "source": "hermes_sequence_approved_executor",
            "error": mode.get("blocked_reason") or "assist_mode_blocks_execution",
            "assist_mode": mode,
            "state_changed": False,
        }

    loaded = state_loader(session_id)
    state = _mapping(loaded.get("state"))
    if not state:
        return {"ok": False, "source": "hermes_sequence_approved_executor", "error": "sequence_state_not_found", "state_changed": False}

    sequence = _mapping(state.get("sequence"))
    items = [_mapping(item) for item in _list(sequence.get("items"))]
    loop_guard = hermes_sequence_loop_guard(sequence, state)
    if loop_guard.get("ok") is False:
        blocked_state = dict(state)
        blocked_state["status"] = "blocked"
        blocked_state["blocked_reason"] = loop_guard.get("stop_reason")
        saved = state_writer(blocked_state)
        return {
            "ok": False,
            "source": "hermes_sequence_approved_executor",
            "status": "blocked",
            "stop_reason": loop_guard.get("stop_reason"),
            "sequence_state": saved,
            "state_changed": False,
            "next_item_preview": _next_preview(items, int(saved.get("current_item_index") or 0)),
        }
    item_index = int(state.get("current_item_index") or 0)
    if item_index >= len(items):
        return {
            "ok": True,
            "source": "hermes_sequence_approved_executor",
            "status": "completed",
            "sequence_state": state,
            "state_changed": False,
            "next_item_preview": None,
        }

    item = items[item_index]
    command_text = _text(item.get("statement"))
    if not command_text:
        return {"ok": False, "source": "hermes_sequence_approved_executor", "error": "missing_command_text", "item_index": item_index, "state_changed": False}

    flow = _approved_flow_payload(
        command_text=command_text,
        context_hash=f"sequence:{state.get('sequence_id')}:{item.get('item_id')}:{item_index}",
        session_id=session_id,
        submitter=submitter,
        environ=environ,
    )
    result_for_state = flow
    if flow.get("ok") is True and flow.get("state_changed") is not True:
        result_for_state = {**flow, "ok": False, "error": "no_progress"}
    updated = apply_hermes_sequence_item_result(state, item_index=item_index, result=result_for_state)
    saved = state_writer(updated)
    saved_sequence = _mapping(saved.get("sequence"))
    saved_items = [_mapping(saved_item) for saved_item in _list(saved_sequence.get("items"))]
    accepted = result_for_state.get("ok") is True
    return {
        "ok": accepted,
        "source": "hermes_sequence_approved_executor",
        "status": "accepted" if accepted else "blocked",
        "stop_reason": None if accepted else result_for_state.get("error") or "execution_blocked",
        "item_index": item_index,
        "command_text": command_text,
        "rpg_turn_result": _mapping(_mapping(_mapping(flow.get("flow")).get("result")).get("rpg_result")),
        "approved_flow": flow,
        "sequence_state": saved,
        "state_changed": flow.get("state_changed") is True,
        "next_item_preview": _next_preview(saved_items, int(saved.get("current_item_index") or 0)),
    }
