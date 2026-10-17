"""What the world context offers and asks of the contexts above it (R-3).

The world never imports narration or the session pipeline. Where the
simulation needs them, the caller that runs it passes the behaviour in.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# (conversation, speaker_id, simulation_state, runtime_state, recent_lines) ->
# the recorded model call, whose ``parsed`` holds the line, or None.
ConversationLineWriter = Callable[
    [dict[str, Any], str, dict[str, Any], dict[str, Any], list[dict[str, Any]]], Any
]
# (session_id, simulation_state, runtime_state) -> a result whose
# ``runtime_state`` replaces the tick's runtime state when present.
ConversationTickObserver = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ConversationHooks:
    """The conversation tick's calls into narration and the session.

    ``write_line`` writes an NPC conversation line with the language model when
    the settings ask for it (narration); without it the tick uses a template
    line. ``after_tick`` queues the ambient narration of a session's latest
    conversation (session); it is presentation only and never fails the tick.
    """

    write_line: ConversationLineWriter | None = None
    after_tick: ConversationTickObserver | None = None


NO_CONVERSATION_HOOKS = ConversationHooks()
