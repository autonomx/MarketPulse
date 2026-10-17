"""RPG player-facing compatibility helpers for gateway bridge routes."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.rules.player.player_encounter import build_encounter_view
from app.apps.rpg.rules.player.player_scene_state import ensure_player_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _simulation_state_from_setup(setup_payload: dict[str, Any]) -> dict[str, Any]:
    metadata = _safe_dict(_safe_dict(setup_payload).get("metadata"))
    return _safe_dict(metadata.get("simulation_state"))


def _player_state_from_request(data: dict[str, Any]) -> dict[str, Any]:
    setup_payload = _safe_dict(_safe_dict(data).get("setup_payload"))
    simulation_state = _simulation_state_from_setup(setup_payload)
    state = ensure_player_state(simulation_state)
    return _safe_dict(state.get("player_state"))


def player_state_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "player_state": _player_state_from_request(data)}


def player_journal_payload(data: dict[str, Any]) -> dict[str, Any]:
    player_state = _player_state_from_request(data)
    entries = player_state.get("journal_entries")
    return {
        "ok": True,
        "journal_entries": list(entries if isinstance(entries, list) else [])[-50:],
    }


def player_codex_payload(data: dict[str, Any]) -> dict[str, Any]:
    player_state = _player_state_from_request(data)
    return {"ok": True, "codex": _safe_dict(player_state.get("codex"))}


def player_objectives_payload(data: dict[str, Any]) -> dict[str, Any]:
    player_state = _player_state_from_request(data)
    objectives = player_state.get("active_objectives")
    return {
        "ok": True,
        "active_objectives": list(objectives if isinstance(objectives, list) else [])[-20:],
    }


def player_encounter_payload(data: dict[str, Any]) -> dict[str, Any]:
    request = _safe_dict(data)
    setup_payload = _safe_dict(request.get("setup_payload"))
    simulation_state = ensure_player_state(_simulation_state_from_setup(setup_payload))
    return {
        "ok": True,
        "encounter": build_encounter_view(_safe_dict(request.get("scene")), simulation_state),
    }
