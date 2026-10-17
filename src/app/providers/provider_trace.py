from __future__ import annotations
import logging

from app.config.env import env_str, environment

import os
import time
import traceback
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROVIDER_TRACE_ROWS: ContextVar[List[Dict[str, Any]] | None] = ContextVar(
    "RPG_PROVIDER_TRACE_ROWS",
    default=None,
)


def provider_trace_enabled() -> bool:
    return env_str("RPG_TRACE_PROVIDER_CALLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def clear_provider_trace() -> None:
    _PROVIDER_TRACE_ROWS.set([])


def get_provider_trace() -> List[Dict[str, Any]]:
    return list(_PROVIDER_TRACE_ROWS.get() or [])


def _rows() -> List[Dict[str, Any]]:
    rows = _PROVIDER_TRACE_ROWS.get()
    if rows is None:
        rows = []
        _PROVIDER_TRACE_ROWS.set(rows)
    return rows


def _message_stats(messages: Any) -> Dict[str, Any]:
    count = 0
    total_chars = 0
    roles: List[str] = []
    if isinstance(messages, list):
        count = len(messages)
        for msg in messages:
            role = ""
            content = ""
            if isinstance(msg, dict):
                role = str(msg.get("role") or "")
                content = str(msg.get("content") or "")
            else:
                role = str(getattr(msg, "role", "") or "")
                content = str(getattr(msg, "content", "") or "")
            if role:
                roles.append(role)
            total_chars += len(content)
    return {
        "message_count": count,
        "message_roles": roles,
        "prompt_chars": total_chars,
    }


def infer_provider_call_purpose(stack: List[str]) -> str:
    text = "\n".join(stack).lower()
    if "player_agent" in text or "_select_player_action" in text:
        return "player_agent"
    if "parallel_pipeline" in text or "deferred_narration" in text:
        return "background_deferred_narration"
    if "runtime_narration" in text or "build_runtime_narration_payload" in text:
        return "runtime_narration"
    if "manual_llm_transcript" in text or "_run_one_manual_turn" in text:
        return "manual_harness_unknown"
    return "unknown"


def provider_call_enter(
    *,
    provider: str,
    method: str,
    model: Optional[str],
    messages: Any,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not provider_trace_enabled():
        return {"enabled": False}

    stack = [line.strip() for line in traceback.format_stack(limit=18)]
    row = {
        "event": "provider_call_enter",
        "provider": provider,
        "method": method,
        "model": model or "",
        "start_perf": time.perf_counter(),
        "purpose": infer_provider_call_purpose(stack),
        "stack": stack,
        **_message_stats(messages),
    }
    if extra:
        row.update(extra)
    _rows().append(row)
    logger.info('[RPG][provider-call][enter] %s', {
            "provider": row["provider"],
            "method": row["method"],
            "model": row["model"],
            "purpose": row["purpose"],
            "message_count": row["message_count"],
            "prompt_chars": row["prompt_chars"],
        })
    return row


def provider_call_exit(row: Dict[str, Any], *, ok: bool, error: str = "") -> None:
    if not row or not row.get("enabled", True):
        return
    elapsed = time.perf_counter() - float(row.get("start_perf") or time.perf_counter())
    row["event"] = "provider_call"
    row["ok"] = bool(ok)
    row["error"] = error
    row["elapsed_seconds"] = round(elapsed, 3)
    logger.warning('[RPG][provider-call][exit] %s', {
            "provider": row.get("provider"),
            "purpose": row.get("purpose"),
            "elapsed_seconds": row.get("elapsed_seconds"),
            "ok": row.get("ok"),
            "error": row.get("error"),
        })