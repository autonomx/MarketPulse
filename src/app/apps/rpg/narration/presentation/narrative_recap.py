"""Product Layer A6 — Narrative recap and codex surfacing.

Read-only builder for lightweight player-facing recap and surfaced codex hints.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list


def _safe_str(v: Any, default: str = "") -> str:
    return str(v) if v is not None else default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def build_narrative_recap_payload(
    simulation_state: Dict[str, Any] | None = None,
    runtime_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build narrative recap payload with recent dialogue lines and surfaced codex entries."""
    simulation_state = _safe_dict(simulation_state)
    runtime_payload = _safe_dict(runtime_payload)

    runtime_dialogue = _safe_dict(runtime_payload.get("runtime_dialogue"))
    turns = [
        _safe_dict(v)
        for v in _safe_list(runtime_dialogue.get("turns"))
        if isinstance(v, dict)
    ]
    turns = sorted(
        turns,
        key=lambda item: (
            _safe_int(item.get("tick"), 0),
            _safe_int(item.get("sequence_index"), 0),
            _safe_str(item.get("turn_id")),
        ),
    )

    recent_lines = []
    for turn in turns[-3:]:
        speaker = _safe_str(turn.get("speaker_name"))
        text = _safe_str(turn.get("text"))
        if speaker and text:
            recent_lines.append(f"{speaker}: {text}")

    codex_state = _safe_dict(simulation_state.get("codex_state"))
    surfaced_entries = []
    for entry in _safe_list(codex_state.get("entries"))[:5]:
        entry = _safe_dict(entry)
        surfaced_entries.append({
            "entry_id": _safe_str(entry.get("entry_id")),
            "title": _safe_str(entry.get("title")),
            "summary": _safe_str(entry.get("summary")),
        })

    recap_text = " ".join(recent_lines).strip()
    if not recap_text:
        recap_text = "The situation is still developing."

    return {
        "narrative_recap": {
            "recap_text": recap_text,
            "recent_lines": recent_lines,
            "surfaced_codex_entries": surfaced_entries,
        }
    }