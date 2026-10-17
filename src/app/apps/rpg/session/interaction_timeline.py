"""Durable, bounded player/NPC interaction timeline."""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

INTERACTION_TIMELINE_VERSION = "rpg_interaction_timeline_v1"
MAX_RECENT_INTERACTIONS = 50


def commit_turn_interaction(
    session: dict[str, Any],
    result: dict[str, Any],
    *,
    player_input: str,
    submission_id: str | None = None,
    trace_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Append one interaction without changing authoritative mechanics or tick."""

    session = session if isinstance(session, dict) else {}
    result = result if isinstance(result, dict) else {}
    runtime = _dict(session.get("runtime_state"))
    timeline = _dict(runtime.get("interaction_timeline"))
    events = [deepcopy(item) for item in _list(timeline.get("events")) if isinstance(item, dict)]
    resolved_submission_id = _text(submission_id or result.get("submission_id"))

    replay = _find_existing_event(events, resolved_submission_id)
    if replay is not None:
        _attach_interaction_result(result, replay, persisted=True, replay=True)
        return session, result, replay

    sequence = max(
        _safe_int(runtime.get("interaction_seq")),
        _safe_int(timeline.get("last_sequence")),
        _max_event_sequence(events),
    ) + 1
    revision = max(
        _safe_int(runtime.get("state_revision")),
        _safe_int(timeline.get("state_revision")),
    ) + 1
    interaction_id = f"interaction:{sequence}"
    # Import lazily so gateway startup can install the interaction hook while
    # app.apps.rpg.narration.presentation is still completing its package initialization.
    from app.apps.rpg.narration.presentation.visible_response import build_visible_response

    visible = build_visible_response(result, player_input)
    sources = _result_sources(result)
    turn_id = _first_text(sources, "turn_id")
    tick = _first_int(sources, "tick")
    stateful = _first_bool(sources, "stateful")
    event = _drop_empty(
        {
            "format_version": INTERACTION_TIMELINE_VERSION,
            "interaction_id": interaction_id,
            "sequence": sequence,
            "state_revision": revision,
            "simulation_tick": tick,
            "turn_id": turn_id,
            "submission_id": resolved_submission_id,
            "trace_id": _text(trace_id or result.get("trace_id")),
            "kind": _interaction_kind(sources, visible),
            "stateful": stateful,
            "player_input": _text(player_input),
            "visible_response": _bounded_visible_response(visible),
            "speaker_id": _speaker_field(visible, "speaker_id"),
            "speaker": _speaker_field(visible, "speaker"),
            "npc_line": _speaker_field(visible, "text"),
            "narration": _text(visible.get("narration")),
            "action_type": _first_text(sources, "action_type", "semantic_action_type"),
            "semantic_family": _first_text(sources, "semantic_family"),
            "created_at": utc_now().isoformat(),
        }
    )
    events.append(event)
    events = events[-MAX_RECENT_INTERACTIONS:]
    timeline.update(
        {
            "format_version": INTERACTION_TIMELINE_VERSION,
            "last_sequence": sequence,
            "state_revision": revision,
            "events": events,
        }
    )
    runtime["interaction_seq"] = sequence
    runtime["state_revision"] = revision
    runtime["interaction_timeline"] = timeline
    runtime["recent_interactions"] = deepcopy(events[-12:])
    runtime["last_interaction"] = deepcopy(event)
    session["runtime_state"] = runtime
    _attach_interaction_result(result, event, persisted=False, replay=False)
    result["session"] = session
    return session, result, event


def _attach_interaction_result(
    result: dict[str, Any],
    event: dict[str, Any],
    *,
    persisted: bool,
    replay: bool,
) -> None:
    result["interaction_id"] = event.get("interaction_id")
    result["interaction_seq"] = event.get("sequence")
    result["state_revision"] = event.get("state_revision")
    result["interaction_event"] = deepcopy(event)
    result["interaction_persisted"] = persisted
    if replay:
        result["interaction_replay"] = True
    nested = result.get("result")
    if isinstance(nested, dict):
        nested["interaction_id"] = event.get("interaction_id")
        nested["interaction_seq"] = event.get("sequence")
        nested["state_revision"] = event.get("state_revision")


def mark_interaction_persisted(result: dict[str, Any]) -> None:
    result["interaction_persisted"] = True
    event = result.get("interaction_event")
    if isinstance(event, dict):
        event["persisted"] = True


def _find_existing_event(events: list[dict[str, Any]], submission_id: str) -> dict[str, Any] | None:
    if not submission_id:
        return None
    for event in reversed(events):
        if _text(event.get("submission_id")) == submission_id:
            return deepcopy(event)
    return None


def _bounded_visible_response(visible: dict[str, Any]) -> dict[str, Any]:
    messages = []
    for item in _list(visible.get("messages"))[:4]:
        if not isinstance(item, dict):
            continue
        messages.append(
            _drop_empty(
                {
                    "kind": _text(item.get("kind")),
                    "speaker_id": _text(item.get("speaker_id")),
                    "speaker": _text(item.get("speaker")),
                    "text": _clip(item.get("text"), 2_000),
                }
            )
        )
    return _drop_empty(
        {
            "format_version": _text(visible.get("format_version")),
            "narration": _clip(visible.get("narration"), 2_000),
            "messages": messages,
            "plain_text": _clip(visible.get("plain_text"), 4_000),
        }
    )


def _speaker_field(visible: dict[str, Any], key: str) -> str:
    for item in _list(visible.get("messages")):
        if isinstance(item, dict) and _text(item.get("kind")) == "npc_dialogue":
            return _text(item.get(key))
    return ""


def _interaction_kind(sources: tuple[dict[str, Any], ...], visible: dict[str, Any]) -> str:
    action_type = _first_text(sources, "action_type", "semantic_action_type")
    family = _first_text(sources, "semantic_family")
    if action_type == "npc_interpretive_dialogue" or family == "social":
        return "npc_dialogue"
    if _list(visible.get("messages")):
        return "npc_dialogue"
    if family:
        return family
    return "player_turn"


def _result_sources(result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return result, _dict(result.get("result")), _dict(result.get("authoritative"))


def _first_text(sources: tuple[dict[str, Any], ...], *keys: str) -> str:
    for source in sources:
        for key in keys:
            value = _text(source.get(key))
            if value:
                return value
    return ""


def _first_int(sources: tuple[dict[str, Any], ...], *keys: str) -> int | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and not isinstance(value, bool):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
    return None


def _first_bool(sources: tuple[dict[str, Any], ...], *keys: str) -> bool | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def _max_event_sequence(events: list[dict[str, Any]]) -> int:
    return max((_safe_int(event.get("sequence")) for event in events), default=0)


def _safe_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clip(value: Any, limit: int) -> str:
    return _text(value)[:limit]


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {}
    }


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
