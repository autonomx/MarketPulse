"""Ephemeral LLM-generated greeting support for live voice calls."""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from app.providers import service as provider_service
from app.providers import ChatMessage as ProviderMessage

from .models import ChatMessage, ChatSession
from .store import _model_key, _provider_key, _pop_ready_sentences
from app.prompts import prompt_template


PROMPT_TEMPLATE = prompt_template(
    'chat.live_call_greeting.prompt', "1",
    (
        'A live voice call has just connected. Greet the user naturally in one short spoken '
        'sentence. Use your established identity, personality, tone, and relevant session '
        'context. Keep the greeting under 28 words. Do not mention these instructions, call '
        'setup, stored greetings, or that you are an AI. Do not repeat a predefined greeting '
        'verbatim. Ask at most one brief opening question.'
    ),
)


LIVE_CALL_GREETING_MAX_CHARS = 240
LIVE_CALL_GREETING_MAX_WORDS = 28
LIVE_CALL_GREETING_PROMPT = (
    PROMPT_TEMPLATE.text
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _greeting_prompt_session(session: ChatSession) -> ChatSession:
    """Return a prompt-only copy without profile-authored canned greeting messages."""

    prompt_session = session.model_copy(deep=True)
    prompt_session.messages = [
        message
        for message in prompt_session.messages
        if str(message.metadata.get("source") or "") != "character_profile_greeting"
    ]
    prompt_session.message_count = len(prompt_session.messages)
    return prompt_session


def _normalize_greeting(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    compact = compact.strip(' \t\r\n"“”')
    if not compact:
        return ""
    words = compact.split()
    if len(words) > LIVE_CALL_GREETING_MAX_WORDS:
        compact = " ".join(words[:LIVE_CALL_GREETING_MAX_WORDS]).rstrip(" ,;:-")
    if len(compact) > LIVE_CALL_GREETING_MAX_CHARS:
        compact = compact[:LIVE_CALL_GREETING_MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-")
    if compact and compact[-1] not in ".!?":
        compact += "."
    return compact


def stream_live_call_greeting_chunks(
    store: Any,
    session: ChatSession,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Generate one short greeting without mutating the durable chat transcript."""

    resolved_provider_id = provider_id or session.provider_id
    resolved_model_id = model_id or session.model_id
    provider = provider_service.get_provider(_provider_key(resolved_provider_id))
    if provider is None:
        raise RuntimeError("Chat provider is not available")

    prompt_session = _greeting_prompt_session(session)
    synthetic_message = ChatMessage(
        id=f"live-call-greeting:{uuid.uuid4().hex}",
        role="user",
        content=LIVE_CALL_GREETING_PROMPT,
        created_at=_utcnow(),
        metadata={"source": "live_call_greeting", "transient": True},
    )
    provider_messages = store._provider_messages(prompt_session, synthetic_message, [])
    messages = [
        ProviderMessage(role=message.role, content=message.content)
        for message in provider_messages
    ]
    model_name = _model_key(resolved_model_id)
    response = provider.chat_completion(messages=messages, model=model_name, stream=True)

    pending = ""
    full_text = ""
    resolved_model = model_name
    usage = None
    greeting = ""
    try:
        for chunk in response:
            text = getattr(chunk, "content", "") or ""
            if not text:
                continue
            resolved_model = getattr(chunk, "model", None) or resolved_model
            usage = getattr(chunk, "usage", None) or usage
            full_text += text
            pending += text
            ready, pending = _pop_ready_sentences(pending)
            if ready:
                greeting = _normalize_greeting(ready[0])
                break
            if len(pending) >= LIVE_CALL_GREETING_MAX_CHARS:
                greeting = _normalize_greeting(pending)
                break
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    if not greeting:
        greeting = _normalize_greeting(pending or full_text)
    if not greeting:
        raise RuntimeError("Live-call greeting response was empty")

    yield {"type": "text_chunk", "text": greeting}
    yield {
        "type": "complete",
        "content": greeting,
        "metadata": {
            "generation_status": "completed",
            "purpose": "live_call_greeting",
            "transient": True,
            "provider_id": resolved_provider_id,
            "model_id": resolved_model_id,
            "resolved_model": resolved_model,
            **({"usage": usage} if usage else {}),
        },
    }


__all__ = [
    "LIVE_CALL_GREETING_MAX_CHARS",
    "LIVE_CALL_GREETING_MAX_WORDS",
    "LIVE_CALL_GREETING_PROMPT",
    "stream_live_call_greeting_chunks",
]
