"""N122.2 runtime-promotion presentation bridge.

Wraps the existing runtime presentation bridge and appends live climate,
survival, and runtime-promotion panel payloads without changing dialogue
presentation semantics.
"""
from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.narration.presentation.runtime_bridge import (
    build_runtime_presentation_payload as _base_runtime_presentation_payload,
)
from app.apps.rpg.rules.runtime_promotions import (
    build_climate_survival_runtime_payload,
    build_runtime_promotion_panel_payload,
)
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def build_runtime_presentation_payload(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = _safe_dict(_base_runtime_presentation_payload(simulation_state, runtime_state))
    runtime_state = _safe_dict(runtime_state)

    climate_survival = build_climate_survival_runtime_payload(
        simulation_state,
        runtime_state,
    )
    runtime_promotion_panel = build_runtime_promotion_panel_payload(
        simulation_state,
        runtime_state,
    )

    payload["climate_survival"] = climate_survival
    payload["runtime_promotion_panel"] = runtime_promotion_panel
    return payload
