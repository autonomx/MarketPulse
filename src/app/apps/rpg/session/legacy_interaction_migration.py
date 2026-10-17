"""Idempotent migration from legacy transcript rows to interaction events."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

LEGACY_INTERACTION_MIGRATION_VERSION = "rpg_legacy_interaction_migration_v1"
INTERACTION_TIMELINE_VERSION = "rpg_interaction_timeline_v1"
MAX_MIGRATED_INTERACTIONS = 50
MAX_RECENT_INTERACTIONS = 12


def migrate_legacy_interactions(session: dict[str, Any]) -> dict[str, Any]:
    """Add a modern interaction timeline without deleting legacy transcript data."""

    session = session if isinstance(session, dict) else {}
    runtime = _dict(session.get("runtime_state"))
    timeline = _dict(runtime.get("interaction_timeline"))
    existing_events = _dict_rows(timeline.get("events"))
    marker = _dict(runtime.get("legacy_interaction_migration"))

    if existing_events:
        _synchronize_runtime(runtime, timeline, existing_events)
        session["runtime_state"] = runtime
        return session
    if marker.get("format_version") == LEGACY_INTERACTION_MIGRATION_VERSION:
        session["runtime_state"] = runtime
        return session

    rows, source = _find_legacy_rows(session)
    if not rows:
        # Nothing predates the timeline. Record that, so a later pass during a
        # turn does not read the turn's own history as a legacy interaction
        # and give it a state revision that was never committed.
        runtime["legacy_interaction_migration"] = {
            "format_version": LEGACY_INTERACTION_MIGRATION_VERSION,
            "source": "",
            "source_row_count": 0,
            "event_count": 0,
            "status": "no_legacy_rows",
        }
        session["runtime_state"] = runtime
        return session

    drafts = _interaction_drafts(rows)
    events = _materialize_events(drafts, runtime=runtime, timeline=timeline, source=source)
    if not events:
        runtime["legacy_interaction_migration"] = {
            "format_version": LEGACY_INTERACTION_MIGRATION_VERSION,
            "source": source,
            "source_row_count": len(rows),
            "event_count": 0,
            "status": "no_convertible_rows",
        }
        session["runtime_state"] = runtime
        return session

    bounded = events[-MAX_MIGRATED_INTERACTIONS:]
    last = bounded[-1]
    timeline.update(
        {
            "format_version": INTERACTION_TIMELINE_VERSION,
            "last_sequence": last["sequence"],
            "state_revision": last["state_revision"],
            "events": bounded,
        }
    )
    runtime["interaction_timeline"] = timeline
    runtime["interaction_seq"] = max(
        _safe_int(runtime.get("interaction_seq")),
        _safe_int(last.get("sequence")),
    )
    runtime["state_revision"] = max(
        _safe_int(runtime.get("state_revision")),
        _safe_int(last.get("state_revision")),
    )
    runtime["recent_interactions"] = deepcopy(bounded[-MAX_RECENT_INTERACTIONS:])
    runtime["last_interaction"] = deepcopy(last)
    runtime["legacy_interaction_migration"] = {
        "format_version": LEGACY_INTERACTION_MIGRATION_VERSION,
        "source": source,
        "source_row_count": len(rows),
        "event_count": len(events),
        "retained_event_count": len(bounded),
        "status": "completed",
    }
    session["runtime_state"] = runtime
    return session


def _find_legacy_rows(session: dict[str, Any]) -> tuple[list[Any], str]:
    runtime = _dict(session.get("runtime_state"))
    state = _dict(session.get("state"))
    simulation = _dict(session.get("simulation_state"))
    presentation = _dict(simulation.get("presentation_state"))
    candidates = (
        ("runtime_state.recent_interactions", runtime.get("recent_interactions")),
        ("runtime_state.transcript", runtime.get("transcript")),
        ("runtime_state.dialogue_history", runtime.get("dialogue_history")),
        ("runtime_state.conversation_history", runtime.get("conversation_history")),
        ("runtime_state.turn_history", runtime.get("turn_history")),
        ("state.transcript", state.get("transcript")),
        ("state.dialogue_history", state.get("dialogue_history")),
        ("state.conversation_history", state.get("conversation_history")),
        ("simulation_state.transcript", simulation.get("transcript")),
        ("simulation_state.presentation_state.transcript", presentation.get("transcript")),
        ("transcript", session.get("transcript")),
    )
    for source, value in candidates:
        if isinstance(value, list) and value:
            return list(value), source
    return [], ""


def _interaction_drafts(rows: list[Any]) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for index, raw in enumerate(rows):
        if isinstance(raw, dict) and _is_interaction_row(raw):
            if pending and _draft_has_content(pending):
                drafts.append(_finalize_draft(pending))
                pending = None
            draft = _draft_from_interaction_row(raw, index)
            if _draft_has_content(draft):
                drafts.append(_finalize_draft(draft))
            continue

        if isinstance(raw, str):
            pending = pending or _new_draft(index)
            _append_narration(pending, raw)
            continue
        if not isinstance(raw, dict):
            continue

        role = _row_role(raw)
        text = _row_text(raw)
        if role == "player":
            if pending and _draft_has_content(pending):
                drafts.append(_finalize_draft(pending))
            pending = _new_draft(index)
            pending["player_input"] = text
            _copy_metadata(pending, raw)
            continue

        pending = pending or _new_draft(index)
        pending["row_indexes"].append(index)
        _copy_metadata(pending, raw)
        if role == "narrator":
            _append_narration(pending, text)
            continue
        if text:
            pending["messages"].append(
                _drop_empty(
                    {
                        "kind": "npc_dialogue",
                        "speaker_id": _text(raw.get("speaker_id") or raw.get("npc_id") or raw.get("actor_id")),
                        "speaker": _speaker_name(raw),
                        "text": text,
                    }
                )
            )

    if pending and _draft_has_content(pending):
        drafts.append(_finalize_draft(pending))
    return drafts


def _is_interaction_row(row: dict[str, Any]) -> bool:
    if isinstance(row.get("visible_response"), dict):
        return True
    keys = {
        "player_input",
        "npc_line",
        "narration",
        "final_narration",
        "interaction_id",
        "turn_id",
    }
    role = _normalize(row.get("role") or row.get("tone"))
    return bool(keys & set(row)) and role not in {"player", "user", "assistant", "npc", "narrator"}


def _draft_from_interaction_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    draft = _new_draft(index)
    draft["interaction_id"] = _text(row.get("interaction_id"))
    draft["player_input"] = _text(
        row.get("player_input")
        or row.get("command")
        or _dict(row.get("input_payload")).get("player_input")
        or _dict(row.get("input_payload")).get("command")
    )
    visible = _dict(row.get("visible_response"))
    draft["narration"] = _text(
        visible.get("narration")
        or row.get("narration")
        or row.get("final_narration")
        or row.get("summary")
    )
    raw_messages = visible.get("messages")
    if isinstance(raw_messages, list):
        for raw_message in raw_messages:
            if not isinstance(raw_message, dict):
                continue
            text = _text(raw_message.get("text") or raw_message.get("line") or raw_message.get("content"))
            if not text:
                continue
            draft["messages"].append(
                _drop_empty(
                    {
                        "kind": _text(raw_message.get("kind")) or "npc_dialogue",
                        "speaker_id": _text(raw_message.get("speaker_id") or raw_message.get("npc_id") or raw_message.get("id")),
                        "speaker": _text(raw_message.get("speaker") or raw_message.get("name")) or "NPC",
                        "text": text,
                    }
                )
            )
    npc = _dict(visible.get("npc")) or _dict(row.get("npc"))
    line = _text(
        row.get("npc_line")
        or npc.get("line")
        or npc.get("text")
        or row.get("line")
    )
    if line and not draft["messages"]:
        draft["messages"].append(
            _drop_empty(
                {
                    "kind": "npc_dialogue",
                    "speaker_id": _text(npc.get("speaker_id") or npc.get("npc_id") or npc.get("id") or row.get("speaker_id")),
                    "speaker": _text(npc.get("speaker") or npc.get("name") or row.get("speaker")) or "NPC",
                    "text": line,
                }
            )
        )
    draft["plain_text"] = _text(visible.get("plain_text") or row.get("plain_text"))
    _copy_metadata(draft, row)
    return draft


def _new_draft(index: int) -> dict[str, Any]:
    return {
        "row_indexes": [index],
        "player_input": "",
        "narration": "",
        "messages": [],
        "plain_text": "",
        "interaction_id": "",
        "turn_id": "",
        "submission_id": "",
        "simulation_tick": None,
        "created_at": "",
        "stateful": None,
        "action_type": "",
        "semantic_family": "",
    }


def _copy_metadata(draft: dict[str, Any], row: dict[str, Any]) -> None:
    draft["turn_id"] = draft.get("turn_id") or _text(row.get("turn_id"))
    draft["submission_id"] = draft.get("submission_id") or _text(row.get("submission_id"))
    draft["simulation_tick"] = draft.get("simulation_tick") or _int_or_none(
        row.get("simulation_tick") or row.get("tick")
    )
    draft["created_at"] = draft.get("created_at") or _text(
        row.get("created_at") or row.get("timestamp") or row.get("time")
    )
    if draft.get("stateful") is None and isinstance(row.get("stateful"), bool):
        draft["stateful"] = row.get("stateful")
    draft["action_type"] = draft.get("action_type") or _text(
        row.get("action_type") or row.get("semantic_action_type")
    )
    draft["semantic_family"] = draft.get("semantic_family") or _text(row.get("semantic_family"))


def _finalize_draft(draft: dict[str, Any]) -> dict[str, Any]:
    messages = [item for item in draft.get("messages", []) if isinstance(item, dict) and _text(item.get("text"))]
    narration = _text(draft.get("narration"))
    plain_text = _text(draft.get("plain_text")) or _compose_plain_text(narration, messages)
    draft["messages"] = messages
    draft["narration"] = narration
    draft["plain_text"] = plain_text
    return draft


def _draft_has_content(draft: dict[str, Any]) -> bool:
    return bool(
        _text(draft.get("player_input"))
        or _text(draft.get("narration"))
        or any(_text(item.get("text")) for item in draft.get("messages", []) if isinstance(item, dict))
    )


def _materialize_events(
    drafts: list[dict[str, Any]],
    *,
    runtime: dict[str, Any],
    timeline: dict[str, Any],
    source: str,
) -> list[dict[str, Any]]:
    existing_sequence = max(
        _safe_int(runtime.get("interaction_seq")),
        _safe_int(timeline.get("last_sequence")),
    )
    existing_revision = max(
        _safe_int(runtime.get("state_revision")),
        _safe_int(timeline.get("state_revision")),
    )
    sequence_start = max(1, existing_sequence - len(drafts) + 1) if existing_sequence else 1
    revision_start = max(1, existing_revision - len(drafts) + 1) if existing_revision else 1
    events: list[dict[str, Any]] = []
    for offset, draft in enumerate(drafts):
        sequence = sequence_start + offset
        revision = revision_start + offset
        messages = deepcopy(draft.get("messages") or [])
        narration = _text(draft.get("narration"))
        event = _drop_empty(
            {
                "format_version": INTERACTION_TIMELINE_VERSION,
                "interaction_id": _text(draft.get("interaction_id")) or f"interaction:{sequence}",
                "sequence": sequence,
                "state_revision": revision,
                "simulation_tick": draft.get("simulation_tick"),
                "turn_id": _text(draft.get("turn_id")),
                "submission_id": _text(draft.get("submission_id")),
                "kind": "npc_dialogue" if messages else "player_turn",
                "stateful": draft.get("stateful"),
                "player_input": _text(draft.get("player_input")),
                "visible_response": _drop_empty(
                    {
                        "format_version": "rpg_visible_response_v1",
                        "narration": narration,
                        "messages": messages,
                        "plain_text": _text(draft.get("plain_text")),
                    }
                ),
                "speaker_id": _first_message_field(messages, "speaker_id"),
                "speaker": _first_message_field(messages, "speaker"),
                "npc_line": _first_message_field(messages, "text"),
                "narration": narration,
                "action_type": _text(draft.get("action_type")),
                "semantic_family": _text(draft.get("semantic_family")),
                "created_at": _text(draft.get("created_at")),
                "legacy_source": source,
                "legacy_row_indexes": list(dict.fromkeys(draft.get("row_indexes") or [])),
            }
        )
        events.append(event)
    return events


def _synchronize_runtime(
    runtime: dict[str, Any],
    timeline: dict[str, Any],
    events: list[dict[str, Any]],
) -> None:
    last = events[-1]
    last_sequence = max(
        _safe_int(timeline.get("last_sequence")),
        _safe_int(last.get("sequence")),
    )
    state_revision = max(
        _safe_int(timeline.get("state_revision")),
        _safe_int(last.get("state_revision")),
    )
    timeline["format_version"] = timeline.get("format_version") or INTERACTION_TIMELINE_VERSION
    timeline["last_sequence"] = last_sequence
    timeline["state_revision"] = state_revision
    timeline["events"] = events[-MAX_MIGRATED_INTERACTIONS:]
    runtime["interaction_timeline"] = timeline
    runtime["interaction_seq"] = max(_safe_int(runtime.get("interaction_seq")), last_sequence)
    runtime["state_revision"] = max(_safe_int(runtime.get("state_revision")), state_revision)
    runtime["recent_interactions"] = deepcopy(events[-MAX_RECENT_INTERACTIONS:])
    runtime["last_interaction"] = deepcopy(last)


def _row_role(row: dict[str, Any]) -> str:
    role = _normalize(row.get("role") or row.get("tone") or row.get("kind") or row.get("type"))
    speaker = _normalize(row.get("speaker") or row.get("name") or row.get("actor"))
    if role in {"player", "user", "human", "player message"} or speaker in {"player", "you"}:
        return "player"
    if role in {"narrator", "narration", "system", "gm", "game master"} or speaker in {
        "narrator",
        "omnix",
        "system",
        "gm",
        "game master",
    }:
        return "narrator"
    return "npc"


def _row_text(row: dict[str, Any]) -> str:
    return _text(
        row.get("text")
        or row.get("content")
        or row.get("line")
        or row.get("message")
        or row.get("response")
        or row.get("narration")
    )


def _speaker_name(row: dict[str, Any]) -> str:
    speaker = _text(row.get("speaker") or row.get("name") or row.get("actor"))
    return speaker or "NPC"


def _append_narration(draft: dict[str, Any], text: Any) -> None:
    value = _text(text)
    if not value:
        return
    current = _text(draft.get("narration"))
    draft["narration"] = f"{current}\n\n{value}" if current else value


def _compose_plain_text(narration: str, messages: list[dict[str, Any]]) -> str:
    paragraphs = [narration] if narration else []
    for message in messages:
        text = _text(message.get("text"))
        if not text:
            continue
        speaker = _text(message.get("speaker")) or "NPC"
        paragraphs.append(f'{speaker}: "{text}"')
    return "\n\n".join(paragraphs)


def _first_message_field(messages: list[dict[str, Any]], key: str) -> str:
    for message in messages:
        value = _text(message.get(key))
        if value:
            return value
    return ""


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    return [deepcopy(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").replace("-", " ").split())


def _safe_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _int_or_none(value: Any) -> int | None:
    resolved = _safe_int(value)
    return resolved if resolved or value == 0 else None


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {}
    }
