"""Product Layer A3 — Player-facing dialogue UX helpers.

Read-only builder for intent buttons, hybrid input aids, and layered dialogue output.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list

INTENT_BUTTONS = [
    {"intent_id": "ask", "label": "Ask"},
    {"intent_id": "threaten", "label": "Threaten"},
    {"intent_id": "help", "label": "Help"},
    {"intent_id": "observe", "label": "Observe"},
    {"intent_id": "leave", "label": "Leave"},
]


def _safe_str(v: Any, default: str = "") -> str:
    return str(v) if v is not None else default


def build_dialogue_ux_payload(
    dialogue_payload: Dict[str, Any] | None = None,
    runtime_payload: Dict[str, Any] | None = None,
    orchestration_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build dialogue UX payload with intent buttons, hybrid input, and layered output."""
    dialogue_payload = _safe_dict(dialogue_payload)
    runtime_payload = _safe_dict(runtime_payload)
    orchestration_payload = _safe_dict(orchestration_payload)

    dialogue = _safe_dict(dialogue_payload.get("dialogue")) if "dialogue" in dialogue_payload else dialogue_payload
    text = _safe_str(dialogue.get("text"))
    speaker_name = _safe_str(dialogue.get("speaker_name"))

    runtime_dialogue = _safe_dict(runtime_payload.get("runtime_dialogue"))
    turns = _safe_list(runtime_dialogue.get("turns"))
    latest_turn = _safe_dict(turns[-1]) if turns else {}

    llm_orchestration = _safe_dict(orchestration_payload.get("llm_orchestration"))

    layered_output = {
        "speaker_layer": {
            "speaker_name": speaker_name,
            "text": text,
        },
        "companion_layer": {
            "has_companion_interjection": bool(_safe_str(latest_turn.get("role")) == "companion" and _safe_str(latest_turn.get("text"))),
            "text": _safe_str(latest_turn.get("text")) if _safe_str(latest_turn.get("role")) == "companion" else "",
        },
        "system_layer": {
            "show_streaming_hint": bool(_safe_str(llm_orchestration.get("provider_mode")) in {"capture", "live"}),
            "show_turn_cursor": True,
            "turn_cursor": runtime_dialogue.get("turn_cursor", 0),
        },
    }

    return {
        "dialogue_ux": {
            "intent_buttons": list(INTENT_BUTTONS),
            "hybrid_input": {
                "allow_free_text": True,
                "allow_intent_buttons": True,
                "suggested_placeholder": "Type what you say, or pick an intent.",
            },
            "layered_output": layered_output,
        }
    }