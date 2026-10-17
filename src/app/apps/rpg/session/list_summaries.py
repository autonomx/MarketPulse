"""Bounded RPG session summaries for list endpoints.

The typed session list route should not normalize or deep-copy complete session
payloads. Some long-running saves can contain large transcript/runtime blobs; the
list view only needs manifest data plus enough environment state to decorate the
row with a derived snapshot.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping

from app.apps.rpg.session.durable_store import (
    CorruptSessionPayloadError,
    _read_payload_json,
    ensure_session_dir,
)
from app.apps.rpg.session.migrations import migrate_session_payload
from app.apps.rpg.foundation.persistence.rpg_repository import SESSION_SUMMARY_STATE_KEYS
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

logger = logging.getLogger(__name__)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _summary_manifest(session: Mapping[str, Any], *, fallback_id: str) -> Dict[str, Any]:
    manifest = _safe_dict(session.get("manifest"))
    session_id = _safe_str(manifest.get("session_id") or manifest.get("id") or fallback_id)
    return {
        "id": _safe_str(manifest.get("id") or session_id).strip(),
        "session_id": session_id.strip(),
        "schema_version": int(manifest.get("schema_version") or 2),
        "title": _safe_str(manifest.get("title")).strip(),
        "status": _safe_str(manifest.get("status") or "active").strip(),
        "created_at": _safe_str(manifest.get("created_at")).strip(),
        "updated_at": _safe_str(manifest.get("updated_at")).strip(),
        "source_pack_id": _safe_str(manifest.get("source_pack_id")).strip(),
        "source_template_id": _safe_str(manifest.get("source_template_id")).strip(),
        "archived": bool(manifest.get("archived")),
    }


def _environment_summary(environment: Mapping[str, Any]) -> Dict[str, Any]:
    environment = _safe_dict(environment)
    if not environment:
        return {}
    return {key: environment[key] for key in sorted(environment.keys())}


def _region_summaries(regions: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for region_id, region in sorted(_safe_dict(regions).items()):
        region_data = _safe_dict(region)
        environment = _environment_summary(_safe_dict(region_data.get("environment")))
        if environment:
            out[_safe_str(region_id)] = {"environment": environment}
    return out


def _state_environment_summary(state: Mapping[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(state)
    world = _safe_dict(state.get("world"))
    scene = _safe_dict(state.get("scene"))
    environment = _environment_summary(_safe_dict(world.get("environment")))
    regions = _region_summaries(_safe_dict(world.get("regions")))
    environment_context = _environment_summary(
        _safe_dict(scene.get("environment_context"))
    )

    summary: Dict[str, Any] = {"world": {}, "scene": {}}
    if environment:
        summary["world"]["environment"] = environment
    if regions:
        summary["world"]["regions"] = regions
    if environment_context:
        summary["scene"]["environment_context"] = environment_context

    # Published campaigns need their canonical opening projection in the list
    # response: the RPG shell is rendered from this bounded row before a full
    # session is requested.  Keeping these small fields prevents the generic
    # new-game template environment from leaking into a published world.
    for key in SESSION_SUMMARY_STATE_KEYS:
        value = state.get(key)
        if isinstance(value, (str, int, float, bool, dict)):
            summary[key] = value
    return summary


def session_list_summary(session: Mapping[str, Any], *, fallback_id: str = "") -> Dict[str, Any]:
    session = _safe_dict(session)
    return {
        "manifest": _summary_manifest(session, fallback_id=fallback_id),
        "state": _state_environment_summary(_safe_dict(session.get("state"))),
        "setup_payload": {},
        "simulation_state": {},
        "runtime_state": {},
    }


def _summary_from_path(path: Path) -> Dict[str, Any]:
    raw_payload = _read_payload_json(path, path.stem)
    migrated = migrate_session_payload(raw_payload)
    session = _safe_dict(migrated.get("session"))
    return session_list_summary(session, fallback_id=path.stem)


def list_session_summaries_from_disk(*, limit: int | None = None) -> List[Dict[str, Any]]:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.apps.rpg.session.persistence.session_records import list_session_summaries_from_postgres
        return list_session_summaries_from_postgres(limit=limit)
    """Return bounded session summaries without normalizing full session payloads."""

    sessions: List[Dict[str, Any]] = []
    paths = sorted(
        ensure_session_dir().glob("*.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if limit is not None:
        paths = paths[: max(0, int(limit))]
    for path in paths:
        try:
            sessions.append(_summary_from_path(path))
        except CorruptSessionPayloadError:
            continue
        except Exception:
            logger.exception("Failed to list RPG session summary", extra={"path": str(path)})
    return sessions
