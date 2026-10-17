"""Deterministic fast-combat presentation selection for the runtime owner."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.fast_combat_presentation import (
    deterministic_fast_combat_payload,
    repair_fast_combat_grounding_validation,
)
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str


def select_fast_combat_presentation(
    final_result: dict[str, Any],
    *,
    runtime_narration_payload: dict[str, Any],
) -> dict[str, Any]:
    """Promote a delta-backed summary before selecting provider narration."""

    repaired = repair_fast_combat_grounding_validation(final_result)
    final_result.clear()
    final_result.update(repaired)
    payload = deterministic_fast_combat_payload(final_result)
    narration = _safe_str(payload.get("narration")).strip()
    if not narration:
        return {}
    return {
        "source": "deterministic_combat_fast_summary",
        "narration": narration,
        "npc": _safe_dict(payload.get("npc")),
        "llm_called": False,
        "runtime_payload_source": _safe_str(
            _safe_dict(runtime_narration_payload).get("source")
        ),
        "combat_delta": _safe_dict(
            payload.get("combat_delta") or payload.get("combat_delta_contract")
        ),
    }


__all__ = ["select_fast_combat_presentation"]
