"""Pure actor-specific RPG memory writer and retrieval helpers."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.apps.rpg.narration.memory_writer import (
    MAX_MEMORY_TEXT,
    MEMORY_SCHEMA_VERSION,
    memory_state_from_session,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

ACTOR_MEMORY_KIND = "actor"
DEFAULT_ACTOR_MEMORY_LIMIT = 6
DEFAULT_ACTOR_MEMORY_SALIENCE = 5


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


def _clean_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 4)
    return None


def _clean_visibility(value: Any) -> str:
    return "public" if _clean_id(value).casefold() == "public" else "private"


def _clean_tags(values: Iterable[Any]) -> List[str]:
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


def _normalize_relationship(value: Any, fallback_target_id: str = "") -> Dict[str, Any]:
    payload = _safe_dict(value)
    target_id = _clean_id(
        payload.get("target_id")
        or payload.get("subject_id")
        or payload.get("with_actor_id")
        or fallback_target_id
    )
    stance = _clean_id(payload.get("stance")).casefold()
    raw_axes = raw_axes if isinstance(raw_axes := payload.get("axes"), dict) else payload
    axes: Dict[str, int | float] = {}
    for raw_key, raw_value in sorted(_safe_dict(raw_axes).items()):
        key = _clean_id(raw_key).casefold()
        if key in {"target_id", "subject_id", "with_actor_id", "stance"}:
            continue
        amount = _clean_number(raw_value)
        if key and amount is not None:
            axes[key] = amount
    relationship: Dict[str, Any] = {}
    if target_id:
        relationship["target_id"] = target_id
    if stance:
        relationship["stance"] = stance
    if axes:
        relationship["axes"] = axes
    return relationship


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


def _compact_actor_entry(entry: Dict[str, Any]) -> Dict[str, Any] | None:
    if _clean_id(entry.get("kind")).casefold() != ACTOR_MEMORY_KIND:
        return None
    entry_id = _clean_id(entry.get("id"))
    actor_id = _clean_id(entry.get("actor_id"))
    text = _clean_text(entry.get("text"))
    if not entry_id or not actor_id or not text:
        return None
    raw_tag_value = entry.get("tags")
    raw_tags: List[Any] = raw_tag_value if isinstance(raw_tag_value, list) else []
    compact: Dict[str, Any] = {
        "id": entry_id,
        "schema_version": _clean_id(entry.get("schema_version")) or MEMORY_SCHEMA_VERSION,
        "kind": ACTOR_MEMORY_KIND,
        "text": text,
        "tick": _clean_int(entry.get("tick")),
        "turn_id": _clean_id(entry.get("turn_id")),
        "actor_id": actor_id,
        "subject_id": _clean_id(entry.get("subject_id")),
        "location_id": _clean_id(entry.get("location_id")),
        "visibility": _clean_visibility(entry.get("visibility")),
        "salience": _clean_int(entry.get("salience")),
        "tags": _clean_tags(raw_tags),
        "source": _clean_id(entry.get("source")),
    }
    relationship = _normalize_relationship(entry.get("relationship"), compact["subject_id"])
    if relationship:
        compact["relationship"] = relationship
    return compact


def _actor_entries(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    memory = memory_state_from_session(session if isinstance(session, dict) else {})
    entries: List[Dict[str, Any]] = []
    for entry in memory.get("entries", []):
        if not isinstance(entry, dict):
            continue
        compact = _compact_actor_entry(entry)
        if compact is not None:
            entries.append(compact)
    return entries


def _matches_terms(entry: Dict[str, Any], terms: List[str]) -> bool:
    if not terms:
        return True
    relationship = _safe_dict(entry.get("relationship"))
    axes = _safe_dict(relationship.get("axes"))
    haystack = " ".join(
        [
            entry.get("text", ""),
            entry.get("actor_id", ""),
            entry.get("subject_id", ""),
            entry.get("location_id", ""),
            relationship.get("target_id", ""),
            relationship.get("stance", ""),
            " ".join(entry.get("tags", [])),
            " ".join(str(key) for key in axes),
        ]
    ).casefold()
    return any(term in haystack for term in terms)


def get_actor_memory(
    session: Dict[str, Any],
    actor_id: Any,
    *,
    subject_id: Any = None,
    visibility: Any = None,
    limit: int = DEFAULT_ACTOR_MEMORY_LIMIT,
) -> List[Dict[str, Any]]:
    """Return actor memory for one actor, oldest-to-newest within the window."""
    actor_key = _clean_id(actor_id).casefold()
    subject_key = _clean_id(subject_id).casefold()
    visibility_key = _clean_id(visibility).casefold()
    cleaned_limit = _clean_limit(limit)
    if not actor_key or cleaned_limit == 0:
        return []
    entries = []
    for entry in _actor_entries(session):
        if entry["actor_id"].casefold() != actor_key:
            continue
        if subject_key and entry["subject_id"].casefold() != subject_key:
            continue
        if visibility_key and entry["visibility"] != visibility_key:
            continue
        entries.append(entry)
    return [dict(entry) for entry in entries[-cleaned_limit:]]


def get_relevant_actor_memory(
    session: Dict[str, Any],
    actor_id: Any,
    *,
    query_terms: Iterable[Any] | str | None = None,
    subject_id: Any = None,
    limit: int = DEFAULT_ACTOR_MEMORY_LIMIT,
) -> List[Dict[str, Any]]:
    """Return actor memory matching any supplied query terms."""
    terms = _normalize_query_terms(query_terms)
    entries = [
        entry
        for entry in get_actor_memory(session, actor_id, subject_id=subject_id, limit=limit)
        if _matches_terms(entry, terms)
    ]
    return entries[-_clean_limit(limit) :]
