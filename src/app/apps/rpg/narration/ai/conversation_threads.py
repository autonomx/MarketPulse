from __future__ import annotations

from typing import Any, Dict, List, cast
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MAX_THREADS = 8
MAX_THREAD_LINES = 12
MAX_RECENT_THREAD_EVENTS = 20
DEFAULT_THREAD_TTL_TICKS = 12

def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def normalize_conversation_threads(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    threads = []
    for raw in _safe_list(runtime_state.get("conversation_threads")):
        thread = _safe_dict(raw)
        thread_id = _safe_str(thread.get("thread_id"))
        if not thread_id:
            continue
        lines = []
        for line_raw in _safe_list(thread.get("lines"))[-MAX_THREAD_LINES:]:
            line = _safe_dict(line_raw)
            text = _safe_str(line.get("text")).strip()
            speaker_id = _safe_str(line.get("speaker_id")).strip()
            if not text or not speaker_id:
                continue
            lines.append(
                {
                    "tick": _safe_int(line.get("tick"), 0),
                    "speaker_id": speaker_id,
                    "speaker_name": _safe_str(line.get("speaker_name") or speaker_id),
                    "target_id": _safe_str(line.get("target_id")),
                    "target_name": _safe_str(line.get("target_name")),
                    "text": text[:280],
                    "kind": _safe_str(line.get("kind") or "statement"),
                }
            )
        thread["thread_id"] = thread_id
        thread["participants"] = _safe_list(thread.get("participants"))[:6]
        thread["lines"] = lines
        thread["turn_count"] = _safe_int(thread.get("turn_count"), len(lines))
        thread["started_tick"] = _safe_int(thread.get("started_tick"), 0)
        thread["updated_tick"] = _safe_int(thread.get("updated_tick"), thread["started_tick"])
        thread["expires_tick"] = _safe_int(
            thread.get("expires_tick"),
            thread["updated_tick"] + DEFAULT_THREAD_TTL_TICKS,
        )
        thread["phase"] = _safe_str(thread.get("phase") or "active")
        thread["topic"] = _safe_dict(thread.get("topic"))
        thread["world_signals"] = _safe_list(thread.get("world_signals"))[:6]
        threads.append(thread)
    threads.sort(
        key=lambda t: (
            -_safe_int(_safe_dict(t).get("updated_tick"), 0),
            _safe_str(_safe_dict(t).get("thread_id")),
        )
    )
    runtime_state["conversation_threads"] = threads[:MAX_THREADS]
    runtime_state["recent_conversation_thread_events"] = _safe_list(
        runtime_state.get("recent_conversation_thread_events")
    )[-MAX_RECENT_THREAD_EVENTS:]
    return runtime_state

def build_thread_id(kind: str, participants: List[str], topic_key: str) -> str:
    clean_participants = [p for p in participants if _safe_str(p).strip()]
    clean_participants = sorted(set(clean_participants))[:4]
    base = ":".join(clean_participants) or "scene"
    topic_key = _safe_str(topic_key).strip().lower().replace(" ", "_")[:40] or "ambient"
    kind = _safe_str(kind).strip().lower() or "ambient"
    return f"thread:{kind}:{base}:{topic_key}"

def seed_or_update_thread(
    runtime_state: Dict[str, Any],
    *,
    kind: str,
    participants: List[str],
    topic: Dict[str, Any],
    current_tick: int,
    location_id: str = "",
    scene_id: str = "",
) -> Dict[str, Any]:
    runtime_state = normalize_conversation_threads(runtime_state)
    topic = _safe_dict(topic)
    topic_key = _safe_str(topic.get("key") or topic.get("type") or topic.get("summary") or "ambient")
    thread_id = build_thread_id(kind, participants, topic_key)
    threads = []
    matched = False
    for raw in _safe_list(runtime_state.get("conversation_threads")):
        thread = _safe_dict(raw)
        if _safe_str(thread.get("thread_id")) == thread_id:
            thread["updated_tick"] = _safe_int(current_tick, 0)
            thread["expires_tick"] = _safe_int(current_tick, 0) + DEFAULT_THREAD_TTL_TICKS
            thread["phase"] = "active"
            old_topic = _safe_dict(thread.get("topic"))
            old_topic.update(topic)
            thread["topic"] = old_topic
            matched = True
        threads.append(thread)
    if not matched:
        threads.append(
            {
                "thread_id": thread_id,
                "kind": _safe_str(kind or "ambient"),
                "phase": "active",
                "participants": sorted(set([p for p in participants if _safe_str(p).strip()]))[:6],
                "topic": topic,
                "location_id": _safe_str(location_id),
                "scene_id": _safe_str(scene_id),
                "started_tick": _safe_int(current_tick, 0),
                "updated_tick": _safe_int(current_tick, 0),
                "expires_tick": _safe_int(current_tick, 0) + DEFAULT_THREAD_TTL_TICKS,
                "turn_count": 0,
                "lines": [],
                "world_signals": [],
            }
        )
    runtime_state["conversation_threads"] = threads
    return normalize_conversation_threads(runtime_state)

def add_thread_line(
    runtime_state: Dict[str, Any],
    *,
    thread_id: str,
    speaker_id: str,
    speaker_name: str,
    text: str,
    current_tick: int,
    target_id: str = "",
    target_name: str = "",
    kind: str = "statement",
) -> Dict[str, Any]:
    runtime_state = normalize_conversation_threads(runtime_state)
    text = _safe_str(text).strip()
    if not thread_id or not speaker_id or not text:
        return runtime_state
    events = _safe_list(runtime_state.get("recent_conversation_thread_events"))
    for raw in _safe_list(runtime_state.get("conversation_threads")):
        thread = _safe_dict(raw)
        if _safe_str(thread.get("thread_id")) != _safe_str(thread_id):
            continue
        line = {
            "tick": _safe_int(current_tick, 0),
            "speaker_id": _safe_str(speaker_id),
            "speaker_name": _safe_str(speaker_name or speaker_id),
            "target_id": _safe_str(target_id),
            "target_name": _safe_str(target_name),
            "text": text[:280],
            "kind": _safe_str(kind or "statement"),
        }
        lines = _safe_list(thread.get("lines"))
        if lines:
            last = _safe_dict(lines[-1])
            if (
                _safe_str(last.get("speaker_id")) == line["speaker_id"]
                and _safe_str(last.get("text")).strip().lower() == cast(str, line["text"]).lower()
            ):
                return runtime_state
        lines.append(line)
        thread["lines"] = lines[-MAX_THREAD_LINES:]
        thread["turn_count"] = _safe_int(thread.get("turn_count"), 0) + 1
        thread["updated_tick"] = _safe_int(current_tick, 0)
        thread["expires_tick"] = _safe_int(current_tick, 0) + DEFAULT_THREAD_TTL_TICKS
        event = {
            "kind": "conversation_thread_line",
            "thread_id": _safe_str(thread_id),
            "tick": _safe_int(current_tick, 0),
            "speaker_id": line["speaker_id"],
            "speaker_name": line["speaker_name"],
            "target_id": line["target_id"],
            "target_name": line["target_name"],
            "text": line["text"],
        }
        events.append(event)
        break
    runtime_state["recent_conversation_thread_events"] = events[-MAX_RECENT_THREAD_EVENTS:]
    return normalize_conversation_threads(runtime_state)

def expire_conversation_threads(
    runtime_state: Dict[str, Any],
    *,
    current_tick: int,
) -> Dict[str, Any]:
    runtime_state = normalize_conversation_threads(runtime_state)
    kept = []
    for raw in _safe_list(runtime_state.get("conversation_threads")):
        thread = _safe_dict(raw)
        if _safe_str(thread.get("phase")) == "resolved":
            continue
        if _safe_int(thread.get("expires_tick"), -999999) < _safe_int(current_tick, 0):
            thread["phase"] = "resolved"
            continue
        kept.append(thread)
    runtime_state["conversation_threads"] = kept[:MAX_THREADS]
    return normalize_conversation_threads(runtime_state)

def build_conversation_thread_prompt_context(
    runtime_state: Dict[str, Any],
    *,
    current_tick: int,
    limit: int = 4,
) -> List[Dict[str, Any]]:
    runtime_state = normalize_conversation_threads(runtime_state)
    rows = []
    for raw in _safe_list(runtime_state.get("conversation_threads")):
        thread = _safe_dict(raw)
        if _safe_str(thread.get("phase")) != "active":
            continue
        if _safe_int(thread.get("expires_tick"), -999999) < _safe_int(current_tick, 0):
            continue
        rows.append(
            {
                "thread_id": _safe_str(thread.get("thread_id")),
                "kind": _safe_str(thread.get("kind")),
                "participants": _safe_list(thread.get("participants"))[:6],
                "topic": _safe_dict(thread.get("topic")),
                "turn_count": _safe_int(thread.get("turn_count"), 0),
                "recent_lines": _safe_list(thread.get("lines"))[-4:],
                "world_signals": _safe_list(thread.get("world_signals"))[:4],
            }
        )
    rows.sort(
        key=lambda r: (
            -_safe_int(_safe_dict(r).get("turn_count"), 0),
            _safe_str(_safe_dict(r).get("thread_id")),
        )
    )
    return rows[:limit]

