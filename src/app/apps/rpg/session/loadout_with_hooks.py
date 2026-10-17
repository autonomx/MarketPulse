"""Compatibility wrapper for loadout actions with item hook options."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.item_loadout_hooks import loadout_item_trace_order
from app.apps.rpg.session.loadout import RpgLoadoutActionRequest, apply_loadout_action


def apply_loadout_action_with_item_hooks(
    session_id: str,
    request: RpgLoadoutActionRequest,
    *,
    diagnostics_interval: int = 10,
    maintenance_interval: int = 25,
    report_interval: int = 20,
    objective_limit: int = 5,
    record_trace: bool = True,
    record_hook_trace: bool = True,
) -> dict[str, Any]:
    """Apply a loadout action with explicit item hook controls."""

    with loadout_item_trace_order(preserve_action_traces=False):
        return apply_loadout_action(
            session_id,
            request,
            run_item_hooks=True,
            diagnostics_interval=diagnostics_interval,
            maintenance_interval=maintenance_interval,
            report_interval=report_interval,
            objective_limit=objective_limit,
            record_trace=record_trace,
            record_hook_trace=record_hook_trace,
        )
