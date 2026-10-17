"""The world conversation tick's hooks as the session runs it (R-3).

The world simulation does not import narration or the session; whoever steps
it during a session passes these, so NPC conversations get model-written lines
when the settings ask for them and the latest conversation's ambient narration
is queued.
"""
from __future__ import annotations

from typing import Any

from app.apps.rpg.narration.ai.conversation_gateway import write_conversation_line
from app.apps.rpg.world.contracts import ConversationHooks


def _queue_ambient_conversation_narration(
    session_id: str,
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    from app.apps.rpg.session.companion_turn_runtime import _maybe_enqueue_latest_ambient_conversation_narration

    return _maybe_enqueue_latest_ambient_conversation_narration(session_id, simulation_state, runtime_state)


SESSION_CONVERSATION_HOOKS = ConversationHooks(
    write_line=write_conversation_line,
    after_tick=_queue_ambient_conversation_narration,
)
