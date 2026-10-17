from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.apps.rpg.world.memory.causal_memory import normalize_npc_memory_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_tags(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {str(item) for item in value if str(item)}


def _memory_score(
    memory: Dict[str, Any],
    *,
    actor_id: str | None = None,
    target_id: str | None = None,
    location_id: str | None = None,
    tags: Iterable[str] | None = None,
    query_text: str | None = None,
) -> tuple:
    facts = _safe_dict(memory.get("facts"))
    memory_tags = _safe_tags(memory.get("tags"))
    requested_tags = {str(tag) for tag in (tags or []) if str(tag)}

    direct_actor = 1 if actor_id and facts.get("actor_id") == actor_id else 0
    direct_target = 1 if target_id and facts.get("target_id") == target_id else 0
    same_location = 1 if location_id and facts.get("location_id") == location_id else 0
    tag_overlap = len(memory_tags & requested_tags)

    query_hit = 0
    if query_text:
        q = query_text.lower()
        haystack = " ".join(
            [
                str(memory.get("summary") or ""),
                " ".join(sorted(memory_tags)),
                " ".join(str(v) for v in facts.values()),
            ]
        ).lower()
        query_hit = 1 if q and q in haystack else 0

    confidence = _safe_float(memory.get("confidence"), 0.0)
    turn_index = _safe_int(memory.get("turn_index"), 0)
    memory_id = str(memory.get("memory_id") or "")

    return (
        direct_actor + direct_target,
        same_location,
        tag_overlap,
        query_hit,
        confidence,
        turn_index,
        memory_id,
    )


def _compact_memory(memory: Dict[str, Any]) -> Dict[str, Any]:
    facts = _safe_dict(memory.get("facts"))
    return {
        "memory_id": memory.get("memory_id"),
        "subject_id": memory.get("subject_id"),
        "event_id": memory.get("event_id"),
        "kind": memory.get("kind"),
        "source": memory.get("source"),
        "summary": memory.get("summary"),
        "facts": facts,
        "confidence": memory.get("confidence"),
        "turn_index": memory.get("turn_index"),
        "tags": list(memory.get("tags") or [])[:12],
    }


def retrieve_causal_memories(
    simulation_state: Dict[str, Any],
    subject_id: str,
    *,
    actor_id: str | None = None,
    target_id: str | None = None,
    location_id: str | None = None,
    tags: Iterable[str] | None = None,
    query_text: str | None = None,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    state = normalize_npc_memory_state(
        _safe_dict(simulation_state).get("npc_memory_state")
    )
    rows = list(state.get("memories_by_subject", {}).get(subject_id) or [])
    if not rows:
        return []

    scored = [
        (
            _memory_score(
                row,
                actor_id=actor_id,
                target_id=target_id,
                location_id=location_id,
                tags=tags,
                query_text=query_text,
            ),
            row,
        )
        for row in rows
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    limit = max(0, int(max_items or 0))
    return [_compact_memory(row) for _, row in scored[:limit]]