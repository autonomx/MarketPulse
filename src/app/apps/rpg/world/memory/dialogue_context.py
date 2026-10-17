"""Phase 16.0 — Canonical dialogue memory shaping."""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_MAX_CONTEXT_ITEMS = 5
_MAX_LINE_LEN = 240
_MAX_PROMPT_LEN = 2000


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _score_memory(item: Dict[str, Any]) -> tuple:
    item = _safe_dict(item)
    strength = float(item.get("strength") or 0.0)
    updated_at = _safe_str(item.get("updated_at")).strip()
    text = _safe_str(item.get("text")).strip()
    return (-strength, updated_at, text)


def build_world_rumor_context(simulation_state: Dict[str, Any], *, limit: int = _MAX_CONTEXT_ITEMS) -> List[Dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    memory_state = _safe_dict(simulation_state.get("memory_state"))
    world_memory = _safe_dict(memory_state.get("world_memory"))
    rumors = [_safe_dict(item) for item in _safe_list(world_memory.get("rumors"))]
    rumors.sort(key=_score_memory)
    return rumors[: max(0, int(limit))]


def build_dialogue_memory_context(simulation_state: Dict[str, Any], actor_id: str = "", actor_ids: Any = None) -> Dict[str, Any]:
    """Build dialogue memory context for one or more actors.

    Supports both actor_id (single string) and actor_ids (list) for backward
    compatibility with existing callers.
    """
    if actor_ids is not None:
        if isinstance(actor_ids, str):
            actor_ids = [actor_ids]
    elif actor_id:
        actor_ids = [actor_id]
    else:
        actor_ids = []

    # Determine primary actor_id for backward compat
    resolved_actor_id = _safe_str(actor_id).strip() if actor_id else ""
    if not resolved_actor_id and actor_ids:
        resolved_actor_id = _safe_str(actor_ids[0]).strip()

    simulation_state = _safe_dict(simulation_state)
    memory_state = _safe_dict(simulation_state.get("memory_state"))
    actor_memory_all = _safe_dict(memory_state.get("actor_memory"))

    collected_actor_memory: List[Dict[str, Any]] = []
    for aid in actor_ids:
        aid = _safe_str(aid).strip()
        if not aid:
            continue
        entries = _safe_dict(actor_memory_all.get(aid)).get("entries", [])
        if not isinstance(entries, list):
            entries = []
        for entry in entries:
            if isinstance(entry, dict):
                collected_actor_memory.append(entry)

    collected_actor_memory.sort(key=_score_memory)
    collected_actor_memory = collected_actor_memory[:50]

    rumor_context = build_world_rumor_context(simulation_state)

    return {
        "actor_id": resolved_actor_id,
        "actor_ids": [_safe_str(a).strip() for a in actor_ids if _safe_str(a).strip()],
        "actor_memory": collected_actor_memory,
        "world_rumors": rumor_context,
    }


def build_llm_memory_prompt_block(dialogue_memory_context: Dict[str, Any]) -> str:
    dialogue_memory_context = _safe_dict(dialogue_memory_context)
    actor_memory = [_safe_dict(item) for item in _safe_list(dialogue_memory_context.get("actor_memory"))]
    world_rumors = [_safe_dict(item) for item in _safe_list(dialogue_memory_context.get("world_rumors"))]

    lines: List[str] = ["[MEMORY CONTEXT]"]

    if actor_memory:
        lines.append("Actor memories:")
        for item in actor_memory:
            text = _safe_str(item.get("text")).strip()[:_MAX_LINE_LEN]
            if text:
                lines.append(f"- {text}")

    if world_rumors:
        lines.append("World rumors:")
        for item in world_rumors:
            text = _safe_str(item.get("text")).strip()[:_MAX_LINE_LEN]
            if text:
                lines.append(f"- {text}")

    recent_consequences = _safe_list(_safe_dict(dialogue_memory_context.get("consequences")).get("recent_consequences"))
    if recent_consequences:
        lines.append("Recent world consequences:")
        for c in recent_consequences[:3]:
            c = _safe_dict(c)
            lines.append(f"- {_safe_str(c.get('summary'))}")

    local_pressure = _safe_list(_safe_dict(dialogue_memory_context.get("consequences")).get("local_pressure"))
    if local_pressure:
        lines.append("Local pressure:")
        for p in local_pressure[:3]:
            p = _safe_dict(p)
            summary = _safe_str(p.get("summary"))
            value = _safe_int(p.get("value"), 0)
            if summary:
                lines.append(f"- {summary} (level {value})")

    local_conditions = _safe_list(_safe_dict(dialogue_memory_context.get("consequences")).get("local_conditions"))
    if local_conditions:
        lines.append("Local conditions:")
        for c in local_conditions[:3]:
            c = _safe_dict(c)
            summary = _safe_str(c.get("summary"))
            severity = _safe_int(c.get("severity"), 0)
            if summary:
                lines.append(f"- {summary} (severity {severity})")

    if len(lines) == 1:
        lines.append("- none")

    prompt = "\n".join(lines[:16])
    return prompt[:_MAX_PROMPT_LEN]