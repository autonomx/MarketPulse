"""Add a natural spoken-conversation contract to live voice prompts only."""
from __future__ import annotations

from app.platform.chat.contracts import PromptAssembly, RenderedPrompt, RenderedPromptMessage
from app.conversation.contracts import estimate_tokens

_SECTION_NAME = "live_voice_spoken_style"

LIVE_VOICE_SPOKEN_STYLE = """This response will be spoken aloud in a live conversation.
Speak as the character in ordinary conversational language, not as an assistant writing polished copy.
Return only the literal words the character says aloud. The response is dialogue, not a screenplay, roleplay transcript, performance note, or description of delivery.
Never write actions, gestures, facial expressions, sound descriptions, tone labels, implied sounds, or production notes in parentheses, brackets, or asterisks. Do not write annotations such as "(soft pause)", "*chuckles*", "typing sounds implied", or "playful tone".
Do not spell out laughter, sighs, breaths, typing, or other sound effects. Do not use markdown emphasis or emoji.
Answer the user's actual point directly, then develop the answer for as long as the subject genuinely needs. Longer responses are welcome when they add useful detail; do not force every reply into a brief summary.
Use contractions, natural transitions, and complete spoken sentences. Keep longer answers coherent and easy to follow aloud rather than sounding like an essay, report, list of talking points, or scripted monologue.
Prefer simple, specific words over enthusiastic adjectives, poetic metaphors, therapy language, customer-service phrasing, or exaggerated validation.
Do not turn the user's wording into a story about your AI, circuits, programming, an upgrade, or an attempt to sound human unless that detail is genuinely necessary to answer them.
A single brief hesitation is fine only when the character is genuinely thinking. Never stack fillers such as "Hmm... Um...", and do not add filler to every reply.
Do not end every response with a question; ask one only when it moves the conversation forward."""


def apply_live_voice_spoken_style(rendered: RenderedPrompt) -> RenderedPrompt:
    """Keep the live-only style instruction inside the stable leading context.

    Stateful LM Studio Responses can continue with only the new user input when
    every system message precedes the rolling user/assistant history. Placing
    this stable instruction immediately before the current user turn made an
    otherwise valid live prompt structurally non-continuable once history was
    present. Insert it after the existing leading system block instead.
    """
    if any(
        message.role == "system" and message.content == LIVE_VOICE_SPOKEN_STYLE
        for message in rendered.messages
    ):
        return rendered

    insertion_index = len(rendered.messages)
    for index, message in enumerate(rendered.messages):
        if message.role in {"user", "assistant"}:
            insertion_index = index
            break
    rendered.messages.insert(
        insertion_index,
        RenderedPromptMessage(role="system", content=LIVE_VOICE_SPOKEN_STYLE),
    )
    token_cost = estimate_tokens(LIVE_VOICE_SPOKEN_STYLE)
    rendered.diagnostics.estimated_tokens += token_cost
    rendered.diagnostics.section_tokens[_SECTION_NAME] = token_cost
    return rendered


def record_spoken_style_diagnostics(
    assembly: PromptAssembly,
    rendered: RenderedPrompt,
) -> None:
    assembly.diagnostics[_SECTION_NAME] = {
        "enabled": True,
        "tokens": rendered.diagnostics.section_tokens.get(_SECTION_NAME, 0),
    }


__all__ = [
    "LIVE_VOICE_SPOKEN_STYLE",
    "apply_live_voice_spoken_style",
    "record_spoken_style_diagnostics",
]
