"""Phase 12 — Visual state management for character portraits and scene illustrations.

Provides deterministic, bounded, presentation-only visual identity management.
Images are presentation assets, not simulation truth.

Design invariants:
- No LLM calls
- No mutation of simulation truth
- No generated image affects deterministic gameplay logic
- Image metadata persisted in bounded presentation state only after explicit generation
"""
from __future__ import annotations

from typing import Any, Dict
import logging
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

logger = logging.getLogger(__name__)

_MAX_SCENE_ILLUSTRATIONS = 24
_MAX_IMAGE_REQUESTS = 24
_MAX_VISUAL_ASSETS = 64
_MAX_APPEARANCE_EVENTS = 32


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _safe_str(value).strip()
        if text:
            return text
    return ""


def _safe_int(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except Exception:
        return default


def _normalize_scalar(value: Any) -> Any:
    """Normalize a scalar value for appearance features."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return _safe_str(value)


def _normalize_visual_identity_entry(value: Any) -> Dict[str, Any]:
    """Normalize a character visual identity entry to a consistent shape."""
    data = _safe_dict(value)
    seed = _safe_int(data.get("seed"), None)
    version = _safe_int(data.get("version"), 1)
    if version is None or version < 1:
        version = 1

    return {
        "portrait_url": _safe_str(data.get("portrait_url")).strip(),
        "portrait_local_path": _safe_str(data.get("portrait_local_path")).strip(),
        "portrait_asset_id": _safe_str(data.get("portrait_asset_id")).strip(),
        "seed": seed,
        "style": _safe_str(data.get("style")).strip(),
        "base_prompt": _safe_str(data.get("base_prompt")).strip(),
        "model": _safe_str(data.get("model")).strip(),
        "version": version,
        "status": _first_non_empty(data.get("status"), "idle"),
    }


def _normalize_scene_illustration(value: Any) -> Dict[str, Any]:
    """Normalize a scene illustration entry to a consistent shape."""
    data = _safe_dict(value)
    return {
        "scene_id": _safe_str(data.get("scene_id")).strip(),
        "event_id": _safe_str(data.get("event_id")).strip(),
        "title": _safe_str(data.get("title")).strip(),
        "image_url": _safe_str(data.get("image_url")).strip(),
        "local_path": _safe_str(data.get("local_path")).strip(),
        "asset_id": _safe_str(data.get("asset_id")).strip(),
        "seed": _safe_int(data.get("seed"), None),
        "style": _safe_str(data.get("style")).strip(),
        "prompt": _safe_str(data.get("prompt")).strip(),
        "model": _safe_str(data.get("model")).strip(),
        "status": _first_non_empty(data.get("status"), "idle"),
    }


def _normalize_image_request(value: Any) -> Dict[str, Any]:
    """Normalize an image request entry to a consistent shape."""
    data = _safe_dict(value)
    kind = _first_non_empty(data.get("kind"), "character_portrait")
    if kind not in {"character_portrait", "scene_illustration"}:
        kind = "character_portrait"

    status = _first_non_empty(data.get("status"), "pending")
    if status not in {"pending", "complete", "failed", "blocked"}:
        status = "pending"

    return {
        "request_id": _safe_str(data.get("request_id")).strip(),
        "kind": kind,
        "target_id": _safe_str(data.get("target_id")).strip(),
        "prompt": _safe_str(data.get("prompt")).strip(),
        "seed": _safe_int(data.get("seed"), None),
        "style": _safe_str(data.get("style")).strip(),
        "model": _safe_str(data.get("model")).strip(),
        "status": status,
        "attempts": max(0, _safe_int(data.get("attempts"), 0) or 0),
        "max_attempts": max(1, _safe_int(data.get("max_attempts"), 3) or 3),
        "error": _safe_str(data.get("error")).strip(),
        "created_at": _safe_str(data.get("created_at")).strip(),
        "updated_at": _safe_str(data.get("updated_at")).strip(),
        "completed_at": _safe_str(data.get("completed_at")).strip(),
    }


def _normalize_visual_asset(value: Any) -> Dict[str, Any]:
    """Normalize a visual asset entry to a consistent shape."""
    data = _safe_dict(value)
    version = _safe_int(data.get("version"), 1)
    if version is None or version < 1:
        version = 1

    kind = _first_non_empty(data.get("kind"), "character_portrait")
    if kind not in {"character_portrait", "scene_illustration"}:
        kind = "character_portrait"

    status = _first_non_empty(data.get("status"), "complete")
    if status not in {"pending", "complete", "failed", "blocked"}:
        status = "complete"

    moderation = _safe_dict(data.get("moderation"))
    moderation_status = _first_non_empty(moderation.get("status"), "unchecked")
    if moderation_status not in {"unchecked", "approved", "blocked", "flagged"}:
        moderation_status = "unchecked"

    return {
        "asset_id": _safe_str(data.get("asset_id")).strip(),
        "kind": kind,
        "target_id": _safe_str(data.get("target_id")).strip(),
        "url": _safe_str(data.get("url")).strip(),
        "local_path": _safe_str(data.get("local_path")).strip(),
        "cache_key": _safe_str(data.get("cache_key")).strip(),
        "seed": _safe_int(data.get("seed"), None),
        "style": _safe_str(data.get("style")).strip(),
        "model": _safe_str(data.get("model")).strip(),
        "prompt": _safe_str(data.get("prompt")).strip(),
        "version": version,
        "status": status,
        "created_from_request_id": _safe_str(data.get("created_from_request_id")).strip(),
        "moderation": {
            "status": moderation_status,
            "reason": _safe_str(moderation.get("reason")).strip(),
        },
    }


def _normalize_appearance_profile(value: Any) -> Dict[str, Any]:
    """Normalize an appearance profile entry to a consistent shape."""
    data = _safe_dict(value)

    features = _safe_dict(data.get("features"))
    normalized_features = {}
    for key in sorted(features.keys(), key=lambda v: _safe_str(v)):
        if not _safe_str(key).strip():
            continue
        normalized_features[_safe_str(key)] = _normalize_scalar(features.get(key))

    return {
        "base_description": _safe_str(data.get("base_description")).strip(),
        "current_summary": _safe_str(data.get("current_summary")).strip(),
        "features": normalized_features,
        "last_reason": _safe_str(data.get("last_reason")).strip(),
        "version": max(1, _safe_int(data.get("version"), 1) or 1),
    }


def _normalize_appearance_event(value: Any) -> Dict[str, Any]:
    """Normalize an appearance event entry to a consistent shape."""
    data = _safe_dict(value)
    reason = _first_non_empty(data.get("reason"), "update")
    if reason not in {
        "initial",
        "injury",
        "promotion",
        "faction_change",
        "equipment_change",
        "corruption",
        "disguise",
        "aging",
        "manual_refresh",
        "update",
    }:
        reason = "update"

    return {
        "event_id": _safe_str(data.get("event_id")).strip(),
        "reason": reason,
        "summary": _safe_str(data.get("summary")).strip(),
        "tick": max(0, _safe_int(data.get("tick"), 0) or 0),
    }


def ensure_visual_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure simulation_state has normalized visual state.

    Returns the mutated simulation_state with normalized visual state.
    """
    if not isinstance(simulation_state, dict):
        simulation_state = {}

    presentation_state = simulation_state.setdefault("presentation_state", {})
    if not isinstance(presentation_state, dict):
        presentation_state = {}
        simulation_state["presentation_state"] = presentation_state

    visual_state = presentation_state.setdefault("visual_state", {})
    if not isinstance(visual_state, dict):
        visual_state = {}
        presentation_state["visual_state"] = visual_state

    identities_in = _safe_dict(visual_state.get("character_visual_identities"))
    identities_out: Dict[str, Any] = {}
    for actor_id in sorted(identities_in.keys(), key=lambda v: _safe_str(v)):
        identities_out[_safe_str(actor_id)] = _normalize_visual_identity_entry(identities_in.get(actor_id))
    visual_state["character_visual_identities"] = identities_out

    illustrations_in = _safe_list(visual_state.get("scene_illustrations"))
    illustrations_out = [
        _normalize_scene_illustration(item)
        for item in illustrations_in
        if isinstance(item, dict) and ":" in _safe_str(item.get("scene_id")).strip()
    ]
    illustrations_out = sorted(
        illustrations_out,
        key=lambda item: (
            _safe_str(item.get("scene_id")),
            _safe_str(item.get("event_id")),
            _safe_str(item.get("title")).lower(),
        ),
    )[:_MAX_SCENE_ILLUSTRATIONS]
    visual_state["scene_illustrations"] = illustrations_out

    requests_in = _safe_list(visual_state.get("image_requests"))
    requests_out = [
        _normalize_image_request(item)
        for item in requests_in
        if isinstance(item, dict) and ":" in _safe_str(item.get("target_id")).strip()
    ]
    requests_out = sorted(
        requests_out,
        key=lambda item: (
            _safe_str(item.get("kind")),
            _safe_str(item.get("target_id")),
            _safe_str(item.get("request_id")),
        ),
    )[:_MAX_IMAGE_REQUESTS]
    visual_state["image_requests"] = requests_out

    # Phase 12.3 — visual assets
    assets_in = _safe_list(visual_state.get("visual_assets"))
    assets_out = [
        _normalize_visual_asset(item)
        for item in assets_in
        if isinstance(item, dict) and ":" in _safe_str(item.get("target_id")).strip()
    ]
    assets_out = sorted(
        assets_out,
        key=lambda item: (
            _safe_str(item.get("kind")),
            _safe_str(item.get("target_id")),
            _safe_str(item.get("asset_id")),
        ),
    )[-_MAX_VISUAL_ASSETS:]
    visual_state["visual_assets"] = assets_out

    # Phase 12.4 — appearance profiles
    profiles_in = _safe_dict(visual_state.get("appearance_profiles"))
    profiles_out: Dict[str, Any] = {}
    for actor_id in sorted(profiles_in.keys(), key=lambda v: _safe_str(v)):
        profiles_out[_safe_str(actor_id)] = _normalize_appearance_profile(profiles_in.get(actor_id))
    visual_state["appearance_profiles"] = profiles_out

    # Phase 12.4 — appearance events
    events_in = _safe_dict(visual_state.get("appearance_events"))
    events_out: Dict[str, Any] = {}
    for actor_id in sorted(events_in.keys(), key=lambda v: _safe_str(v)):
        raw_events = _safe_list(events_in.get(actor_id))
        normalized_events = [
            _normalize_appearance_event(item)
            for item in raw_events
            if isinstance(item, dict)
        ]
        normalized_events = sorted(
            normalized_events,
            key=lambda item: (
                item.get("tick", 0),
                _safe_str(item.get("reason")),
                _safe_str(item.get("event_id")),
            ),
        )[-_MAX_APPEARANCE_EVENTS:]
        events_out[_safe_str(actor_id)] = normalized_events
    visual_state["appearance_events"] = events_out

    defaults = _safe_dict(visual_state.get("defaults"))
    visual_state["defaults"] = {
        "portrait_style": _first_non_empty(defaults.get("portrait_style"), "rpg-portrait"),
        "scene_style": _first_non_empty(defaults.get("scene_style"), "rpg-scene"),
        "model": _first_non_empty(defaults.get("model"), "default"),
        "fallback_portrait_url": _safe_str(defaults.get("fallback_portrait_url")).strip(),
        "fallback_scene_url": _safe_str(defaults.get("fallback_scene_url")).strip(),
    }

    return simulation_state


# ---- Phase 12.10 — Request update helpers for worker ----


# ---- Phase 12.3 — Asset registry + continuity mutators ----


# ---- Phase 12.5 — Request validation / moderation / fallback helpers ----


# NOTE:
# Scene illustrations are presentation artifacts. They may be requested in response
# to important events, but generation itself must remain explicit and external to
# authoritative simulation reducers. Reducers may suggest event IDs/titles, but must
# never depend on image output.