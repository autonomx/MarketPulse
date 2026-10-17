"""Deterministic retrieval helpers for recent RPG memory entries."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.apps.rpg.narration.memory_writer import (
    MAX_MEMORY_TEXT,
    MEMORY_SCHEMA_VERSION,
    memory_state_from_session,
)

DEFAULT_RECENT_MEMORY_LIMIT = 6
DEFAULT_RELEVANT_MEMORY_LIMIT = 8
RETRIEVABLE_MEMORY_KINDS = {"turn", "dialogue", "recap"}


def _clean_text(value: Any, limit: int = MAX_MEMORY_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:limit]


def _clean_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:120]
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _clean_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default


def _clean_tags(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    tags: List[str] = []
    for value in values:
        tag = _clean_id(value).casefold()
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:12]


def _clean_limit(limit: Any) -> int:
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        return limit
    return 0


def _compact_entry(entry: Dict[str, Any]) -> Dict[str, Any] | None:
    entry_id = _clean_id(entry.get("id"))
    kind = _clean_id(entry.get("kind")).casefold()
    text = _clean_text(entry.get("text"))
    if not entry_id or kind not in RETRIEVABLE_MEMORY_KINDS or not text:
        return None
    return {
        "id": entry_id,
        "schema_version": _clean_id(entry.get("schema_version")) or MEMORY_SCHEMA_VERSION,
        "kind": kind,
        "text": text,
        "tick": _clean_int(entry.get("tick")),
        "turn_id": _clean_id(entry.get("turn_id")),
        "actor_id": _clean_id(entry.get("actor_id")),
        "subject_id": _clean_id(entry.get("subject_id")),
        "location_id": _clean_id(entry.get("location_id")),
        "visibility": "private" if entry.get("visibility") == "private" else "public",
        "salience": _clean_int(entry.get("salience")),
        "tags": _clean_tags(entry.get("tags")),
        "source": _clean_id(entry.get("source")),
    }


def _memory_entries(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    memory = memory_state_from_session(session if isinstance(session, dict) else {})
    entries: List[Dict[str, Any]] = []
    for entry in memory.get("entries", []):
        if not isinstance(entry, dict):
            continue
        compact = _compact_entry(entry)
        if compact is not None:
            entries.append(compact)
    return entries


def _recent_entries(entries: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    cleaned_limit = _clean_limit(limit)
    if cleaned_limit == 0:
        return []
    return [dict(entry) for entry in entries[-cleaned_limit:]]


def _matches_npc(entry: Dict[str, Any], npc_key: str) -> bool:
    if not npc_key:
        return True
    identifiers = (
        entry.get("actor_id", "").casefold(),
        entry.get("subject_id", "").casefold(),
    )
    return (
        npc_key in identifiers
        or npc_key in entry.get("tags", [])
        or npc_key in entry.get("text", "").casefold()
    )


def _normalize_query_terms(query_terms: Iterable[Any] | str | None) -> List[str]:
    if query_terms is None:
        return []
    values: Iterable[Any]
    if isinstance(query_terms, str):
        values = query_terms.split()
    else:
        values = query_terms
    terms: List[str] = []
    for value in values:
        term = _clean_text(value, limit=80).casefold() or _clean_id(value).casefold()
        if term and term not in terms:
            terms.append(term)
    return terms


def _matches_query_terms(entry: Dict[str, Any], query_terms: List[str]) -> bool:
    if not query_terms:
        return True
    haystack = " ".join(
        [
            entry.get("kind", ""),
            entry.get("text", ""),
            entry.get("turn_id", ""),
            entry.get("actor_id", ""),
            entry.get("subject_id", ""),
            entry.get("location_id", ""),
            " ".join(entry.get("tags", [])),
        ]
    ).casefold()
    return any(term in haystack for term in query_terms)


def get_relevant_recent_memory(
    session: Dict[str, Any],
    *,
    npc_id: Any = None,
    query_terms: Iterable[Any] | str | None = None,
    limit: int = DEFAULT_RELEVANT_MEMORY_LIMIT,
) -> List[Dict[str, Any]]:
    """Return recent turn/dialogue memory matching any supplied NPC or term filter.

    Without filters, this is a compact recent-memory window. Results are always
    oldest-to-newest within the selected recent set.
    """
    npc_key = _clean_id(npc_id).casefold()
    terms = _normalize_query_terms(query_terms)
    entries: List[Dict[str, Any]] = []
    for entry in _memory_entries(session):
        if not npc_key and not terms:
            entries.append(entry)
            continue
        if npc_key and _matches_npc(entry, npc_key):
            entries.append(entry)
            continue
        if terms and _matches_query_terms(entry, terms):
            entries.append(entry)
    return _recent_entries(entries, limit)
