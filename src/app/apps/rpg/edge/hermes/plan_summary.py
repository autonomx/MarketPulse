from __future__ import annotations

from typing import Any


PLAN_PARTS = (
    "context",
    "request",
    "normalize",
    "validate",
    "ticket",
    "trace",
    "ticket_match",
    "command_card",
    "command_bundle",
    "command_summary",
)


def hermes_rpg_plan_summary_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "source": "hermes_rpg_plan_summary",
        "path": "/api/hermes/plan",
        "parts": list(PLAN_PARTS),
        "writes_state": False,
        "default_enabled": False,
        "user_step": True,
        "command_surface_clear": True,
    }
