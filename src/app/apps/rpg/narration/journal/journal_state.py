from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_JOURNAL_ENTRIES = 120


def _ensure_journal_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    journal_state = simulation_state.get("journal_state")
    if not isinstance(journal_state, dict):
        journal_state = {}
        simulation_state["journal_state"] = journal_state
    entries = journal_state.get("entries")
    if not isinstance(entries, list):
        entries = []
        journal_state["entries"] = entries
    return journal_state


def add_journal_entry(
    simulation_state: Dict[str, Any],
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    entry = deepcopy(_safe_dict(entry))
    if not entry:
        return {}

    entry_id = _safe_str(entry.get("entry_id"))
    if not entry_id:
        return {}

    journal_state = _ensure_journal_state(simulation_state)
    entries = _safe_list(journal_state.get("entries"))
    for existing in entries:
        existing = _safe_dict(existing)
        if _safe_str(existing.get("entry_id")) == entry_id:
            return deepcopy(existing)

    entries.append(entry)
    if len(entries) > MAX_JOURNAL_ENTRIES:
        del entries[:-MAX_JOURNAL_ENTRIES]
    return deepcopy(entry)


def add_rumor_journal_entry(
    simulation_state: Dict[str, Any],
    rumor: Dict[str, Any],
    *,
    provider_id: str = "",
    provider_name: str = "",
    tick: int = 0,
) -> Dict[str, Any]:
    rumor = _safe_dict(rumor)
    rumor_id = _safe_str(rumor.get("rumor_id"))
    if not rumor_id:
        return {}

    return add_journal_entry(
        simulation_state,
        {
            "entry_id": f"journal:{rumor_id}",
            "kind": "rumor",
            "title": _safe_str(rumor.get("title")),
            "summary": _safe_str(rumor.get("summary")),
            "status": "active",
            "source_id": rumor_id,
            "source_provider_id": provider_id or _safe_str(rumor.get("source_provider_id")),
            "source_provider_name": provider_name or _safe_str(rumor.get("source_provider_name")),
            "created_tick": int(tick or 0),
            "source": "deterministic_journal_runtime",
        },
    )


def get_journal_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(_ensure_journal_state(simulation_state))