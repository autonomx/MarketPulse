from __future__ import annotations

from typing import Any


def hermes_rpg_pipeline_handoff(user_step: dict[str, Any], replay_entry: dict[str, Any]) -> dict[str, Any]:
    command = str(replay_entry.get("command_text") or user_step.get("command_text") or "").strip()
    ready = user_step.get("ready") is True and replay_entry.get("ok") is True and bool(command)
    return {
        "ok": ready,
        "source": "hermes_rpg_pipeline_handoff",
        "command_text": command,
        "canonical_path": "rpg_command_input",
        "ready_for_rpg_pipeline": ready,
        "state_changed": False,
    }
