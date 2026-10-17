from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

DEFAULT_MAX_MEMORIES_PER_SUBJECT = 100

BLOCKED_SYNTHETIC_SUBJECT_IDS = {
    "",
    "environment",
    "ambient_wait",
    "observe",
    "observe_environment",
    "npc:The Room/Environment",
    "Environment/NPCs (General)",
}


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _safe_float(value: Any, default: float = 1.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def is_valid_memory_subject(subject_id: str) -> bool:
    subject_id = str(subject_id or "").strip()
    if subject_id in BLOCKED_SYNTHETIC_SUBJECT_IDS:
        return False
    lowered = subject_id.lower()
    if "environment/npcs" in lowered:
        return False
    if "room/environment" in lowered:
        return False
    return bool(subject_id)


def stable_memory_id(
    *,
    subject_id: str,
    event_id: str,
    kind: str,
    source: str,
    facts: Dict[str, Any] | None = None,
) -> str:
    payload = {
        "subject_id": subject_id,
        "event_id": event_id,
        "kind": kind,
        "source": source,
        "facts": facts or {},
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return f"mem:{digest}"


def normalize_causal_memory(value: Dict[str, Any]) -> Dict[str, Any]:
    value = _safe_dict(value)
    subject_id = _safe_str(value.get("subject_id"))
    event_id = _safe_str(value.get("event_id"))
    kind = _safe_str(value.get("kind")) or "system"
    source = _safe_str(value.get("source")) or "manual"
    facts = dict(_safe_dict(value.get("facts")))
    memory_id = (
        _safe_str(value.get("memory_id"))
        or stable_memory_id(
            subject_id=subject_id,
            event_id=event_id,
            kind=kind,
            source=source,
            facts=facts,
        )
    )

    return {
        "memory_id": memory_id,
        "subject_id": subject_id,
        "event_id": event_id,
        "kind": kind,
        "source": source,
        "summary": _safe_str(value.get("summary")),
        "facts": facts,
        "confidence": max(0.0, min(1.0, _safe_float(value.get("confidence"), 1.0))),
        "turn_index": _safe_int(value.get("turn_index"), 0),
        "timestamp": _safe_str(value.get("timestamp")),
        "tags": [str(tag) for tag in _safe_list(value.get("tags")) if str(tag)],
        "visibility": dict(_safe_dict(value.get("visibility"))),
        "audibility": dict(_safe_dict(value.get("audibility"))),
        "expires_after_turn": value.get("expires_after_turn"),
    }


def make_causal_memory(
    *,
    subject_id: str,
    event_id: str,
    kind: str,
    source: str,
    summary: str,
    facts: Dict[str, Any] | None = None,
    confidence: float = 1.0,
    turn_index: int = 0,
    tags: List[str] | None = None,
    visibility: Dict[str, Any] | None = None,
    audibility: Dict[str, Any] | None = None,
    expires_after_turn: int | None = None,
) -> Dict[str, Any]:
    facts = dict(facts or {})
    memory = {
        "subject_id": subject_id,
        "event_id": event_id,
        "kind": kind,
        "source": source,
        "summary": summary,
        "facts": facts,
        "confidence": confidence,
        "turn_index": turn_index,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tags": list(tags or []),
        "visibility": dict(visibility or {}),
        "audibility": dict(audibility or {}),
        "expires_after_turn": expires_after_turn,
    }
    memory["memory_id"] = stable_memory_id(
        subject_id=subject_id,
        event_id=event_id,
        kind=kind,
        source=source,
        facts=facts,
    )
    return normalize_causal_memory(memory)


def normalize_npc_memory_state(value: Dict[str, Any] | None) -> Dict[str, Any]:
    value = _safe_dict(value)
    max_memories = _safe_int(
        value.get("max_memories_per_subject"),
        DEFAULT_MAX_MEMORIES_PER_SUBJECT,
    )
    if max_memories <= 0:
        max_memories = DEFAULT_MAX_MEMORIES_PER_SUBJECT

    memories_by_subject: Dict[str, List[Dict[str, Any]]] = {}
    for subject_id, rows in _safe_dict(value.get("memories_by_subject")).items():
        subject_id = str(subject_id or "")
        if not is_valid_memory_subject(subject_id):
            continue
        normalized_rows = [
            normalize_causal_memory(row)
            for row in _safe_list(rows)
            if isinstance(row, dict)
        ]
        deduped: Dict[str, Dict[str, Any]] = {}
        for row in normalized_rows:
            if row.get("subject_id") != subject_id:
                row["subject_id"] = subject_id
            deduped[str(row.get("memory_id"))] = row
        kept = sorted(
            deduped.values(),
            key=lambda row: (
                _safe_int(row.get("turn_index"), 0),
                str(row.get("memory_id") or ""),
            ),
        )[-max_memories:]
        memories_by_subject[subject_id] = kept

    return {
        "version": 1,
        "memories_by_subject": memories_by_subject,
        "max_memories_per_subject": max_memories,
    }


def ensure_npc_memory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(simulation_state, dict):
        raise TypeError("simulation_state_must_be_dict")
    state = normalize_npc_memory_state(simulation_state.get("npc_memory_state"))
    simulation_state["npc_memory_state"] = state
    return state


def add_causal_memory(
    simulation_state: Dict[str, Any],
    memory: Dict[str, Any],
) -> Dict[str, Any]:
    memory = normalize_causal_memory(memory)
    subject_id = str(memory.get("subject_id") or "")
    if not is_valid_memory_subject(subject_id):
        return {
            "ok": False,
            "reason": "invalid_subject",
            "subject_id": subject_id,
            "memory": memory,
        }

    state = ensure_npc_memory_state(simulation_state)
    max_memories = int(state.get("max_memories_per_subject") or DEFAULT_MAX_MEMORIES_PER_SUBJECT)
    rows = list(state["memories_by_subject"].get(subject_id) or [])

    by_id = {str(row.get("memory_id")): row for row in rows}
    by_id[str(memory.get("memory_id"))] = memory
    kept = sorted(
        by_id.values(),
        key=lambda row: (
            _safe_int(row.get("turn_index"), 0),
            str(row.get("memory_id") or ""),
        ),
    )[-max_memories:]
    state["memories_by_subject"][subject_id] = kept

    return {
        "ok": True,
        "reason": "recorded",
        "subject_id": subject_id,
        "memory_id": memory.get("memory_id"),
        "memory": memory,
        "count": len(kept),
    }