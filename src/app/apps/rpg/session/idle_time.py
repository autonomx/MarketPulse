"""Replay time handling shared by RPG idle-tick boundaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def recorded_idle_tick_time(session: dict[str, Any]) -> tuple[datetime | None, str | None]:
    runtime_state = session.get("runtime_state")
    runtime_state = runtime_state if isinstance(runtime_state, dict) else {}
    simulation_state = session.get("simulation_state")
    simulation_state = simulation_state if isinstance(simulation_state, dict) else {}
    current_tick = int(simulation_state.get("tick", runtime_state.get("tick", 0)) or 0)
    mode = str(runtime_state.get("mode") or "live").strip().lower()
    if mode != "replay":
        return None, None

    capture_key = f"idle_tick:{current_tick}"
    records = runtime_state.get("llm_records_index")
    records = records if isinstance(records, dict) else {}
    captured = records.get(capture_key)
    captured = captured if isinstance(captured, dict) else {}
    recorded_now = str(captured.get("now") or "").strip()
    if not recorded_now:
        return None, f"missing_replay_idle_tick_time_for_tick:{current_tick}"
    try:
        turn_now = datetime.fromisoformat(recorded_now.replace("Z", "+00:00"))
        if turn_now.tzinfo is None:
            turn_now = turn_now.replace(tzinfo=timezone.utc)
        return turn_now.astimezone(timezone.utc), None
    except ValueError:
        return None, f"invalid_replay_idle_tick_time_for_tick:{current_tick}"
