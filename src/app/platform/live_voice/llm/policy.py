"""Provider options selected for a live voice turn."""
from __future__ import annotations

from typing import Any


def is_live_voice_message(user_message: Any) -> bool:
    metadata = getattr(user_message, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    speech_segment_id = str(metadata.get("speech_segment_id") or "").strip()
    user_turn_id = str(metadata.get("user_turn_id") or "").strip()
    return bool(speech_segment_id or user_turn_id.startswith("voice-user-turn:"))


def lmstudio_live_voice_options(user_message: Any) -> dict[str, Any]:
    """Disable chain-of-thought output for spoken turns on LM Studio."""
    if not is_live_voice_message(user_message):
        return {}
    return {"chat_template_kwargs": {"enable_thinking": False}}


__all__ = ["is_live_voice_message", "lmstudio_live_voice_options"]
