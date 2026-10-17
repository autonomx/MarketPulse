"""Apply deterministic NPC-dialogue quality policy at the turn boundary."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.narration.presentation.dialogue_quality import (
    enforce_dialogue_quality,
)

def apply_dialogue_quality_stage(ctx: Any) -> Any:
    """Enforce the visible dialogue contract on a resolved turn context."""

    result = ctx.result
    if not isinstance(result, dict) or result.get("ok") is not True:
        return ctx
    session = result.get("session")
    if not isinstance(session, dict) and isinstance(ctx.session_override, dict):
        session = ctx.session_override
    if not isinstance(session, dict):
        from .service import load_session

        session = load_session(ctx.session_id)
    enforced = enforce_dialogue_quality(
        result,
        session=session if isinstance(session, dict) else {},
        player_input=ctx.player_input,
    )
    enforced["dialogue_quality_hook_applied"] = True
    ctx.result = enforced
    return ctx
