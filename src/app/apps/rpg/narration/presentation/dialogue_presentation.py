"""Phase 10 — Dialogue presentation builder.

Builds a presentation-ready payload for dialogue, including
speaker cards, dialogue context, and presence summary.
"""
from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.rules.party import (
    build_companion_dialogue_context,
    build_companion_presence_summary,
)

from .speaker_cards import build_speaker_cards
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def build_dialogue_presentation_payload(simulation_state: Dict[str, Any], dialogue_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a presentation-ready payload for a dialogue.

    This is a pure builder: it does not mutate simulation_state.
    """
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    dialogue_state = _safe_dict(dialogue_state)

    dialogue_context = build_companion_dialogue_context(simulation_state, dialogue_state)
    presence_summary = build_companion_presence_summary(player_state)
    speaker_cards = build_speaker_cards(simulation_state, dialogue_state)

    return {
        "dialogue_id": dialogue_state.get("dialogue_id"),
        "speaker_id": dialogue_state.get("speaker_id"),
        "speaker_cards": speaker_cards,
        "dialogue_context": dialogue_context,
        "presence_summary": presence_summary,
    }