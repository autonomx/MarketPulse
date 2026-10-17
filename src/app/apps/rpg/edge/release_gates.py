"""Provider-free structural release gates for the interactive RPG pipeline."""
from __future__ import annotations

import json
from typing import Any

from app.apps.rpg.narration.presentation.turn_response import TURN_RESPONSE_MAX_BYTES

RELEASE_GATE_VERSION = "rpg_interactive_release_gates_v1"
_FORBIDDEN_FOREGROUND_KEYS = {
    "session",
    "game",
    "simulation_state",
    "runtime_state",
    "foreground_job",
    "first_call_grounding_diagnostics",
    "narration_context",
}


def evaluate_turn_response_release_gates(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    visible = raw_visible_response if isinstance(raw_visible_response := payload.get("visible_response"), dict) else {}
    messages = raw_messages if isinstance(raw_messages := visible.get("messages"), list) else []
    state = raw_state if isinstance(raw_state := payload.get("state"), dict) else {}
    failures: list[str] = []

    if payload.get("contract_version") != "rpg_turn_response_v2":
        failures.append("wrong_contract_version")
    if len(encoded) > TURN_RESPONSE_MAX_BYTES:
        failures.append("response_exceeds_50kb")
    leaked_keys = sorted(_FORBIDDEN_FOREGROUND_KEYS & set(payload))
    if leaked_keys:
        failures.append(f"foreground_graph_leak:{','.join(leaked_keys)}")
    if not payload.get("interaction_id"):
        failures.append("missing_interaction_id")
    if not str(visible.get("plain_text") or "").strip():
        failures.append("missing_visible_text")
    if "conversation" in (state.get("changed_domains") or []) and not messages and not visible.get("narration"):
        failures.append("conversation_without_visible_response")

    return {
        "format_version": RELEASE_GATE_VERSION,
        "ok": not failures,
        "failures": failures,
        "response_bytes": len(encoded),
        "max_response_bytes": TURN_RESPONSE_MAX_BYTES,
        "interaction_id": payload.get("interaction_id"),
        "changed_domains": list(state.get("changed_domains") or []),
    }


