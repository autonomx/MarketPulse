"""Product Layer A2 — Deterministic intro-scene generator.

Read-only builder for a strong first 60 seconds player experience.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def _safe_str(v: Any, default: str = "") -> str:
    return str(v) if v is not None else default


def _build_intro_by_genre(genre: str) -> Dict[str, Any]:
    presets = {
        "fantasy": {
            "scene_id": "intro:fantasy:gate",
            "location_name": "South Gate",
            "npc": {"speaker_id": "npc:gatewarden", "speaker_name": "Gatewarden"},
            "hook": "A caravan has failed to arrive, and the guard is uneasy.",
            "affordance": "Ask about the missing caravan.",
        },
        "cyberpunk": {
            "scene_id": "intro:cyberpunk:alley",
            "location_name": "Service Alley",
            "npc": {"speaker_id": "npc:fixer", "speaker_name": "Fixer Juno"},
            "hook": "A courier route has gone dark three blocks from the exchange.",
            "affordance": "Take the urgent courier job.",
        },
        "horror": {
            "scene_id": "intro:horror:chapel",
            "location_name": "Broken Chapel",
            "npc": {"speaker_id": "npc:caretaker", "speaker_name": "Caretaker Vale"},
            "hook": "The bells rang in the night even though the tower is empty.",
            "affordance": "Investigate the bell tower.",
        },
        "science_fiction": {
            "scene_id": "intro:scifi:dock",
            "location_name": "Dock Ring 6",
            "npc": {"speaker_id": "npc:dockmaster", "speaker_name": "Dockmaster Pell"},
            "hook": "A freight seal was broken from the inside during vacuum transit.",
            "affordance": "Inspect the compromised freight container.",
        },
        "post_apocalypse": {
            "scene_id": "intro:apoc:checkpoint",
            "location_name": "Rust Checkpoint",
            "npc": {"speaker_id": "npc:scout", "speaker_name": "Scout Marr"},
            "hook": "Water thieves were seen near the reserve line at dawn.",
            "affordance": "Track the thieves before sunset.",
        },
        "mystery": {
            "scene_id": "intro:mystery:street",
            "location_name": "Lamplight Street",
            "npc": {"speaker_id": "npc:inspector", "speaker_name": "Inspector Hale"},
            "hook": "A witness insists the victim left the room after death.",
            "affordance": "Examine the locked room.",
        },
        "western": {
            "scene_id": "intro:western:station",
            "location_name": "Dry Creek Station",
            "npc": {"speaker_id": "npc:marshal", "speaker_name": "Marshal Boone"},
            "hook": "A rail pay chest vanished before the noon train arrived.",
            "affordance": "Question the station hands.",
        },
    }
    return _safe_dict(presets.get(genre, presets["fantasy"]))


def build_intro_scene_payload(session_bootstrap: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build deterministic intro scene payload for first 60 seconds experience."""
    session_bootstrap = _safe_dict(session_bootstrap)
    genre = _safe_str((_safe_dict(session_bootstrap.get("world_seed"))).get("genre"), "fantasy")
    intro = _build_intro_by_genre(genre)

    npc = _safe_dict(intro.get("npc"))
    hook = _safe_str(intro.get("hook"))
    affordance = _safe_str(intro.get("affordance"))

    return {
        "intro_scene": {
            "scene_id": _safe_str(intro.get("scene_id")),
            "location_name": _safe_str(intro.get("location_name")),
            "opening_npc": npc,
            "tension_hook": hook,
            "actionable_affordance": affordance,
            "suggested_actions": [
                {"action_id": "ask", "label": "Ask for details"},
                {"action_id": "observe", "label": "Observe the surroundings"},
                {"action_id": "accept", "label": affordance},
            ],
            "guarantees": {
                "has_opening_npc": bool(npc),
                "has_tension_hook": bool(hook),
                "has_actionable_affordance": bool(affordance),
            },
        }
    }