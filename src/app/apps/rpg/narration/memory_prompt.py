"""Bounded prompt-context bridge for deterministic RPG memory."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.apps.rpg.narration.memory_actor import get_actor_memory, get_relevant_actor_memory
from app.apps.rpg.narration.memory_retrieval import get_relevant_recent_memory
from app.apps.rpg.narration.memory_world import get_relevant_world_memory, get_world_memory
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_PROMPT_1 = prompt_template('rpg.session_memory_prompt.build_relevant_memory_prompt_block', "1", "Usage: continuity only; current runtime state and turn contract remain authoritative.")

MEMORY_PROMPT_CONTEXT_VERSION = "rpg_relevant_memory_prompt_v1"
DEFAULT_PROMPT_MEMORY_LIMIT = 4
MAX_PROMPT_MEMORY_TEXT = 180


def _clean_text(value: Any, limit: int = MAX_PROMPT_MEMORY_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:limit]


def _clean_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:120]
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _dedupe(values: Iterable[Any], limit: int = 8) -> List[str]:
    out: List[str] = []
    for value in values:
        item = _clean_id(value)
        key = item.casefold()
        if item and key not in {existing.casefold() for existing in out}:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _query_terms(player_input: Any, extra_terms: Iterable[Any] | str | None = None) -> List[str]:
    values: List[Any] = []
    if isinstance(extra_terms, str):
        values.extend(extra_terms.split())
    elif extra_terms is not None:
        values.extend(extra_terms)
    values.extend(_clean_text(player_input, limit=300).split())
    terms: List[str] = []
    for value in values:
        term = _clean_id(value).casefold()
        if len(term) < 3:
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= 12:
            break
    return terms


def _session_from_runtime(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    return {"runtime_state": _safe_dict(runtime_state)}


def _compact_entry(entry: Dict[str, Any], *, include_event: bool = False) -> Dict[str, Any]:
    compact = {
        "id": _clean_id(entry.get("id")),
        "kind": _clean_id(entry.get("kind")),
        "text": _clean_text(entry.get("text")),
        "actor_id": _clean_id(entry.get("actor_id")),
        "subject_id": _clean_id(entry.get("subject_id")),
        "location_id": _clean_id(entry.get("location_id")),
        "visibility": _clean_id(entry.get("visibility")) or "public",
        "salience": entry.get("salience") if isinstance(entry.get("salience"), int) else 0,
        "tags": _safe_list(entry.get("tags"))[:6],
    }
    if include_event:
        compact["event_type"] = _clean_id(entry.get("event_type"))
        compact["scope"] = _clean_id(entry.get("scope"))
        compact["scope_id"] = _clean_id(entry.get("scope_id"))
    return {key: value for key, value in compact.items() if value not in ("", [], None)}


def build_relevant_memory_context(
    session: Dict[str, Any],
    *,
    player_input: Any = "",
    actor_ids: Iterable[Any] = (),
    location_id: Any = "",
    query_terms: Iterable[Any] | str | None = None,
    limit: int = DEFAULT_PROMPT_MEMORY_LIMIT,
) -> Dict[str, Any]:
    """Return a deterministic, bounded memory payload suitable for prompts."""
    session = _safe_dict(session)
    actors = _dedupe(actor_ids, limit=4)
    terms = _query_terms(player_input, query_terms)
    location = _clean_id(location_id)
    recent = [
        _compact_entry(entry)
        for entry in get_relevant_recent_memory(
            session,
            npc_id=actors[0] if actors else None,
            query_terms=terms,
            limit=limit,
        )
    ]
    actor_memory: List[Dict[str, Any]] = []
    for actor_id in actors:
        relevant = get_relevant_actor_memory(session, actor_id, query_terms=terms, limit=limit)
        if not relevant:
            relevant = get_actor_memory(session, actor_id, limit=limit)
        actor_memory.extend(_compact_entry(entry) for entry in relevant)
    world_memory = [
        _compact_entry(entry, include_event=True)
        for entry in get_relevant_world_memory(
            session,
            query_terms=terms,
            location_id=location or None,
            limit=limit,
        )
    ]
    if not world_memory and location:
        world_memory = [
            _compact_entry(entry, include_event=True)
            for entry in get_world_memory(session, location_id=location, limit=limit)
        ]
    return {
        "format_version": MEMORY_PROMPT_CONTEXT_VERSION,
        "source": "deterministic_runtime_memory",
        "usage": "continuity_only_runtime_state_remains_authoritative",
        "query": {
            "actor_ids": actors,
            "location_id": location,
            "terms": terms,
            "limit": limit,
        },
        "recent": recent[:limit],
        "actors": actor_memory[:limit],
        "world": world_memory[:limit],
    }


def build_relevant_memory_context_from_runtime(
    runtime_state: Dict[str, Any],
    *,
    player_input: Any = "",
    actor_ids: Iterable[Any] = (),
    location_id: Any = "",
    query_terms: Iterable[Any] | str | None = None,
    limit: int = DEFAULT_PROMPT_MEMORY_LIMIT,
) -> Dict[str, Any]:
    """Build prompt memory context from a runtime_state dictionary."""
    return build_relevant_memory_context(
        _session_from_runtime(runtime_state),
        player_input=player_input,
        actor_ids=actor_ids,
        location_id=location_id,
        query_terms=query_terms,
        limit=limit,
    )


def _format_line(entry: Dict[str, Any]) -> str:
    entry_id = _clean_id(entry.get("id")) or "memory"
    kind = _clean_id(entry.get("kind")) or "memory"
    actor = _clean_id(entry.get("actor_id") or entry.get("subject_id"))
    event = _clean_id(entry.get("event_type"))
    visibility = _clean_id(entry.get("visibility")) or "public"
    prefix_parts = [entry_id, kind]
    if event:
        prefix_parts.append(event)
    if actor:
        prefix_parts.append(actor)
    prefix_parts.append(visibility)
    return f"- [{' | '.join(prefix_parts)}] {_clean_text(entry.get('text'), 220)}"


def build_relevant_memory_prompt_block(memory_context: Dict[str, Any]) -> str:
    """Render a compact Relevant Memory block for LLM prompts."""
    memory_context = _safe_dict(memory_context)
    sections = [
        ("Recent turn/dialogue", _safe_list(memory_context.get("recent"))),
        ("Actor memory", _safe_list(memory_context.get("actors"))),
        ("World/event memory", _safe_list(memory_context.get("world"))),
    ]
    lines = ["Relevant Memory:", _PROMPT_1.text]
    any_memory = False
    for title, entries in sections:
        lines.append(f"{title}:")
        if not entries:
            lines.append("- none")
            continue
        any_memory = True
        for entry in entries[:DEFAULT_PROMPT_MEMORY_LIMIT]:
            lines.append(_format_line(_safe_dict(entry)))
    if not any_memory:
        return "Relevant Memory:\n- none"
    return "\n".join(lines)
