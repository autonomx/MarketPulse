"""Deterministic RPG memory schema and post-turn writer.

This module is intentionally pure: callers pass a session snapshot plus the
resolved turn payload and receive a copied session with memory entries appended.
Later slices can wire the writer into runtime persistence, retrieval, prompt
context, reports, and grounding guards without changing the schema contract.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MEMORY_SCHEMA_VERSION = "rpg_memory_v1"
MAX_MEMORY_TEXT = 500
DEFAULT_TURN_SALIENCE = 3
DEFAULT_DIALOGUE_SALIENCE = 4


def _clean_text(value: Any, limit: int = MAX_MEMORY_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:limit]


def _clean_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:120]
    if isinstance(value, int):
        return str(value)
    return ""


def _clean_tags(values: Iterable[Any]) -> List[str]:
    tags: List[str] = []
    for value in values:
        tag = _clean_id(value).casefold()
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:12]


def memory_state_from_session(session: Dict[str, Any]) -> Dict[str, Any]:
    """Return a normalized memory state from a session snapshot."""
    runtime = _safe_dict(_safe_dict(session).get("runtime_state"))
    memory = _safe_dict(runtime.get("memory"))
    entries = [entry for entry in _safe_list(memory.get("entries")) if isinstance(entry, dict)]
    next_sequence = memory.get("next_sequence")
    if not isinstance(next_sequence, int) or next_sequence < 1:
        next_sequence = len(entries) + 1
    return {
        "version": MEMORY_SCHEMA_VERSION,
        "next_sequence": next_sequence,
        "entries": [dict(entry) for entry in entries],
    }


def _next_entry_id(memory: Dict[str, Any]) -> str:
    sequence = memory.get("next_sequence")
    if not isinstance(sequence, int) or sequence < 1:
        sequence = len(_safe_list(memory.get("entries"))) + 1
    memory["next_sequence"] = sequence + 1
    return f"mem:{sequence:06d}"


def make_memory_entry(
    memory: Dict[str, Any],
    *,
    kind: str,
    text: str,
    tick: int = 0,
    turn_id: str = "",
    actor_id: str = "",
    subject_id: str = "",
    location_id: str = "",
    visibility: str = "public",
    salience: int = DEFAULT_TURN_SALIENCE,
    tags: Iterable[Any] = (),
    source: str = "post_turn_writer",
) -> Dict[str, Any]:
    """Create one canonical memory entry with a deterministic sequence id."""
    cleaned_text = _clean_text(text)
    entry = {
        "id": _next_entry_id(memory),
        "schema_version": MEMORY_SCHEMA_VERSION,
        "kind": _clean_id(kind) or "turn",
        "text": cleaned_text,
        "tick": tick if isinstance(tick, int) and tick >= 0 else 0,
        "turn_id": _clean_id(turn_id),
        "actor_id": _clean_id(actor_id),
        "subject_id": _clean_id(subject_id),
        "location_id": _clean_id(location_id),
        "visibility": "private" if visibility == "private" else "public",
        "salience": salience if isinstance(salience, int) else DEFAULT_TURN_SALIENCE,
        "tags": _clean_tags(tags),
        "source": _clean_id(source) or "post_turn_writer",
    }
    return entry


