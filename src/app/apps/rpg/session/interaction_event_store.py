"""Append-only durable storage for RPG interaction events."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.apps.rpg.foundation.performance_trace import current_rpg_pipeline_trace, rpg_pipeline_span

from . import durable_store
from .interaction_timeline import MAX_RECENT_INTERACTIONS

INTERACTION_EVENT_LOG_VERSION = "rpg_interaction_event_log_v1"
INTERACTION_COMPACTION_EVENT_COUNT = 25
INTERACTION_COMPACTION_BYTES = 256_000


def interaction_event_log_path(session_id: str) -> Path:
    return durable_store._session_path(session_id).with_suffix(".interactions.jsonl")


def append_interaction_event(session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.session.persistence.session_records import append_interaction_event_postgres
        return append_interaction_event_postgres(session_id, event)
    path = interaction_event_log_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical = _canonical_event_json(event)
    envelope = {
        "format_version": INTERACTION_EVENT_LOG_VERSION,
        "checksum": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "event": event,
    }
    line = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    context = rpg_pipeline_span("interaction.event_append", fields={"session_id": session_id})
    if current_rpg_pipeline_trace() is None:
        _append_line(path, line)
    else:
        with context as span:
            _append_line(path, line)
            span["event_bytes"] = len(line.encode("utf-8"))
            span["sequence"] = event.get("sequence")
    return envelope


def load_interaction_events(
    session_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 1_000,
) -> list[dict[str, Any]]:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.session.persistence.session_records import load_interaction_events_postgres
        return load_interaction_events_postgres(session_id, after_sequence=after_sequence, limit=limit)
    path = interaction_event_log_path(session_id)
    if not path.exists():
        return []
    fields = {"session_id": session_id, "after_sequence": after_sequence}
    if current_rpg_pipeline_trace() is None:
        return _read_events(path, after_sequence=after_sequence, limit=limit)
    with rpg_pipeline_span("interaction.event_read_replay", fields=fields) as span:
        events = _read_events(path, after_sequence=after_sequence, limit=limit)
        span["event_count"] = len(events)
        span["log_bytes"] = path.stat().st_size if path.exists() else 0
        return events


def replay_interaction_events(
    session: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(session, dict) or not events:
        return session
    runtime = raw_runtime_state if isinstance(raw_runtime_state := session.get("runtime_state"), dict) else {}
    timeline = raw_interaction_timeline if isinstance(raw_interaction_timeline := runtime.get("interaction_timeline"), dict) else {}
    existing = [item for item in timeline.get("events", []) if isinstance(item, dict)]
    by_id = {
        str(item.get("interaction_id") or f"sequence:{item.get('sequence')}"): dict(item)
        for item in existing
    }
    for event in events:
        key = str(event.get("interaction_id") or f"sequence:{event.get('sequence')}")
        by_id[key] = dict(event)
    merged = sorted(by_id.values(), key=lambda item: int(item.get("sequence") or 0))
    merged = merged[-MAX_RECENT_INTERACTIONS:]
    last = merged[-1] if merged else {}
    last_sequence = int(last.get("sequence") or timeline.get("last_sequence") or 0)
    state_revision = int(last.get("state_revision") or timeline.get("state_revision") or 0)
    timeline.update(
        {
            "format_version": "rpg_interaction_timeline_v1",
            "last_sequence": last_sequence,
            "state_revision": state_revision,
            "events": merged,
        }
    )
    runtime["interaction_seq"] = max(int(runtime.get("interaction_seq") or 0), last_sequence)
    runtime["state_revision"] = max(int(runtime.get("state_revision") or 0), state_revision)
    runtime["interaction_timeline"] = timeline
    runtime["recent_interactions"] = [dict(item) for item in merged[-12:]]
    if last:
        runtime["last_interaction"] = dict(last)
    session["runtime_state"] = runtime
    return session


def load_and_replay_interaction_events(session_id: str, session: dict[str, Any]) -> dict[str, Any]:
    runtime = raw_runtime_state if isinstance(raw_runtime_state := session.get("runtime_state"), dict) else {}
    timeline = raw_interaction_timeline if isinstance(raw_interaction_timeline := runtime.get("interaction_timeline"), dict) else {}
    snapshot_sequence = int(timeline.get("last_sequence") or runtime.get("interaction_seq") or 0)
    events = load_interaction_events(session_id, after_sequence=snapshot_sequence)
    return replay_interaction_events(session, events)


def interaction_log_requires_compaction(session_id: str) -> bool:
    path = interaction_event_log_path(session_id)
    if not path.exists():
        return False
    try:
        if path.stat().st_size >= INTERACTION_COMPACTION_BYTES:
            return True
        return _line_count(path) >= INTERACTION_COMPACTION_EVENT_COUNT
    except OSError:
        return False


def compact_interaction_event_log(session_id: str, *, through_sequence: int) -> int:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.session.persistence.session_records import compact_interaction_events_postgres
        return compact_interaction_events_postgres(session_id, through_sequence=through_sequence)
    path = interaction_event_log_path(session_id)
    if not path.exists():
        return 0
    remaining = load_interaction_events(session_id, after_sequence=through_sequence)
    lines = [_envelope_line(event) for event in remaining]
    _write_lines_atomic(path, lines)
    return len(remaining)


def interaction_event_log_status(session_id: str) -> dict[str, Any]:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.session.persistence.session_records import interaction_log_status_postgres
        return interaction_log_status_postgres(session_id)
    path = interaction_event_log_path(session_id)
    return {
        "format_version": INTERACTION_EVENT_LOG_VERSION,
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "event_count": _line_count(path) if path.exists() else 0,
        "compaction_required": interaction_log_requires_compaction(session_id),
    }


def _append_line(path: Path, line: str) -> None:
    encoded = line.encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_events(path: Path, *, after_sequence: int, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            event = _decode_event_line(raw)
            if event is None:
                continue
            if int(event.get("sequence") or 0) <= after_sequence:
                continue
            events.append(event)
            if len(events) >= limit:
                break
    return events


def _decode_event_line(raw: str) -> dict[str, Any] | None:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict) or envelope.get("format_version") != INTERACTION_EVENT_LOG_VERSION:
        return None
    event = envelope.get("event")
    if not isinstance(event, dict):
        return None
    checksum = str(envelope.get("checksum") or "")
    expected = hashlib.sha256(_canonical_event_json(event).encode("utf-8")).hexdigest()
    return event if checksum == expected else None


def _envelope_line(event: dict[str, Any]) -> str:
    canonical = _canonical_event_json(event)
    envelope = {
        "format_version": INTERACTION_EVENT_LOG_VERSION,
        "checksum": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "event": event,
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _canonical_event_json(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_lines_atomic(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)
