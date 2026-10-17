"""Pure world and event RPG memory writer and retrieval helpers."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.apps.rpg.narration.memory_writer import (
    MAX_MEMORY_TEXT,
    MEMORY_SCHEMA_VERSION,
    memory_state_from_session,
)

WORLD_MEMORY_KIND = "world"
DEFAULT_WORLD_MEMORY_LIMIT = 8
DEFAULT_WORLD_MEMORY_SALIENCE = 4
ALLOWED_WORLD_SCOPES = {"global", "location", "faction", "quest", "actor"}


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


def _clean_limit(limit: Any) -> int:
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        return limit
    return 0


def _clean_visibility(value: Any) -> str:
    return "private" if _clean_id(value).casefold() == "private" else "public"


def _clean_scope(value: Any, scope_id: str = "", location_id: str = "") -> str:
    scope = _clean_id(value).casefold()
    if scope in ALLOWED_WORLD_SCOPES:
        return scope
    if scope_id or location_id:
        return "location"
    return "global"


def _clean_tags(values: Iterable[Any]) -> List[str]:
    tags: List[str] = []
    for value in values:
        tag = _clean_id(value).casefold()
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:12]


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


def _compact_world_entry(entry: Dict[str, Any]) -> Dict[str, Any] | None:
    if _clean_id(entry.get("kind")).casefold() != WORLD_MEMORY_KIND:
        return None
    entry_id = _clean_id(entry.get("id"))
    text = _clean_text(entry.get("text"))
    event_type = _clean_id(entry.get("event_type")).casefold()
    if not entry_id or not text or not event_type:
        return None
    raw_tag_value = entry.get("tags")
    raw_tags: List[Any] = raw_tag_value if isinstance(raw_tag_value, list) else []
    scope_id = _clean_id(entry.get("scope_id"))
    location_id = _clean_id(entry.get("location_id"))
    return {
        "id": entry_id,
        "schema_version": _clean_id(entry.get("schema_version")) or MEMORY_SCHEMA_VERSION,
        "kind": WORLD_MEMORY_KIND,
        "text": text,
        "event_type": event_type,
        "scope": _clean_scope(entry.get("scope"), scope_id, location_id),
        "scope_id": scope_id,
        "tick": _clean_int(entry.get("tick")),
        "turn_id": _clean_id(entry.get("turn_id")),
        "actor_id": _clean_id(entry.get("actor_id")),
        "subject_id": _clean_id(entry.get("subject_id")),
        "location_id": location_id,
        "visibility": _clean_visibility(entry.get("visibility")),
        "salience": _clean_int(entry.get("salience")),
        "tags": _clean_tags(raw_tags),
        "source": _clean_id(entry.get("source")),
    }


def _world_entries(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    memory = memory_state_from_session(session if isinstance(session, dict) else {})
    entries: List[Dict[str, Any]] = []
    for entry in memory.get("entries", []):
        if not isinstance(entry, dict):
            continue
        compact = _compact_world_entry(entry)
        if compact is not None:
            entries.append(compact)
    return entries


def _matches_filter(entry: Dict[str, Any], key: str, value: Any) -> bool:
    cleaned = _clean_id(value).casefold()
    return not cleaned or entry.get(key, "").casefold() == cleaned


def _matches_terms(entry: Dict[str, Any], terms: List[str]) -> bool:
    if not terms:
        return True
    haystack = " ".join(
        [
            entry.get("text", ""),
            entry.get("event_type", ""),
            entry.get("scope", ""),
            entry.get("scope_id", ""),
            entry.get("actor_id", ""),
            entry.get("subject_id", ""),
            entry.get("location_id", ""),
            " ".join(entry.get("tags", [])),
        ]
    ).casefold()
    return any(term in haystack for term in terms)


def get_world_memory(
    session: Dict[str, Any],
    *,
    event_type: Any = None,
    scope: Any = None,
    scope_id: Any = None,
    location_id: Any = None,
    visibility: Any = None,
    limit: int = DEFAULT_WORLD_MEMORY_LIMIT,
) -> List[Dict[str, Any]]:
    """Return world/event memory, oldest-to-newest within the selected window."""
    cleaned_limit = _clean_limit(limit)
    if cleaned_limit == 0:
        return []
    visibility_key = _clean_id(visibility).casefold()
    entries = []
    for entry in _world_entries(session):
        if not _matches_filter(entry, "event_type", event_type):
            continue
        if not _matches_filter(entry, "scope", scope):
            continue
        if not _matches_filter(entry, "scope_id", scope_id):
            continue
        if not _matches_filter(entry, "location_id", location_id):
            continue
        if visibility_key and entry["visibility"] != visibility_key:
            continue
        entries.append(entry)
    return [dict(entry) for entry in entries[-cleaned_limit:]]


def get_relevant_world_memory(
    session: Dict[str, Any],
    *,
    query_terms: Iterable[Any] | str | None = None,
    event_type: Any = None,
    scope: Any = None,
    scope_id: Any = None,
    location_id: Any = None,
    limit: int = DEFAULT_WORLD_MEMORY_LIMIT,
) -> List[Dict[str, Any]]:
    """Return world/event memory matching supplied filters and query terms."""
    terms = _normalize_query_terms(query_terms)
    entries = [
        entry
        for entry in get_world_memory(
            session,
            event_type=event_type,
            scope=scope,
            scope_id=scope_id,
            location_id=location_id,
            limit=limit,
        )
        if _matches_terms(entry, terms)
    ]
    return entries[-_clean_limit(limit) :]
