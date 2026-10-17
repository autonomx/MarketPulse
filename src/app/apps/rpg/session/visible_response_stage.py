"""Final player-visible response stage for the RPG turn pipeline."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.visible_response_contract import attach_visible_turn_record


def apply_visible_response_stage(ctx: Any) -> Any:
    """Attach the canonical visible-text audit to a resolved turn."""

    if isinstance(ctx.result, dict):
        ctx.result = attach_visible_turn_record(
            ctx.result,
            player_input=ctx.player_input,
        )
    return ctx
