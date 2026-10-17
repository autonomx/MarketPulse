"""Narration guardrails for RPG Environment 2.0."""
from __future__ import annotations

from typing import Any
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.session_environment_narration.environment_narration_prompt_block', "1", "Narration rule: describe these values only; never mutate weather, time, season, temperature, visibility, hazards, or terrain.")

FORBIDDEN_ENVIRONMENT_MUTATIONS = (
    "create_new_weather",
    "clear_weather",
    "advance_time",
    "change_season",
    "invent_temperature",
    "invent_visibility",
    "invent_hazards",
    "contradict_current_environment",
)

ALLOWED_ENVIRONMENT_NARRATION = (
    "describe_current_snapshot",
    "emphasize_sensory_details",
    "interpret_from_actor_perspective",
    "mention_encoded_practical_implications",
)


EnvironmentNarrationResult = dict[str, Any]


def build_environment_narration_contract(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Return read-only environment context for narrator prompts/contracts."""

    safe_snapshot = snapshot if isinstance(snapshot, dict) else {}
    return {
        "authority": "read_only_environment_snapshot",
        "environment_snapshot": safe_snapshot,
        "allowed": list(ALLOWED_ENVIRONMENT_NARRATION),
        "forbidden": list(FORBIDDEN_ENVIRONMENT_MUTATIONS),
        "instruction": "Describe the current environment snapshot; do not create, clear, advance, or contradict environment state.",
    }


def environment_narration_prompt_block(snapshot: dict[str, Any] | None) -> str:
    """Return a compact prompt block that makes environment authority explicit."""

    contract = build_environment_narration_contract(snapshot)
    weather = _weather_label(contract["environment_snapshot"])
    display = raw_display if isinstance(raw_display := contract["environment_snapshot"].get("display"), dict) else {}
    return "\n".join(
        [
            "Environment Snapshot (read-only):",
            f"- Time: {display.get('day_time') or 'Not tracked yet'}",
            f"- Weather: {weather}",
            f"- Temperature: {display.get('temperature') or 'Not tracked yet'}",
            f"- Terrain: {display.get('terrain') or 'Not tracked yet'}",
            _PROMPT_1.text,
        ]
    )


def _weather_label(snapshot: dict[str, Any]) -> str:
    display = raw_display if isinstance(raw_display := snapshot.get("display"), dict) else {}
    if display.get("weather"):
        return str(display["weather"])
    weather = raw_weather if isinstance(raw_weather := snapshot.get("weather"), dict) else {}
    condition = str(weather.get("condition") or "Not tracked yet")
    intensity = str(weather.get("intensity") or "").strip()
    return f"{intensity.title()} {condition.title()}".strip()


def _context_label(snapshot: dict[str, Any]) -> str:
    context = raw_context if isinstance(raw_context := snapshot.get("context"), dict) else {}
    exposure = str(context.get("exposure") or "Not tracked yet")
    shelter = str(context.get("shelter") or "Not tracked yet")
    return f"{exposure} / {shelter}"
