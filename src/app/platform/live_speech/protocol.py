"""Client-event dispatch for live speech realtime sessions."""
from __future__ import annotations

from typing import Any

from .events import LiveSpeechProtocolError
from .realtime import LiveSpeechRealtimeService


def dispatch_client_event(service: LiveSpeechRealtimeService, message: dict[str, Any]):
    event_type = message.get("type")
    if event_type == "session.update":
        return service.update_session(message.get("session") or {})
    if event_type == "input_audio_buffer.append":
        return service.append_audio_b64(str(message.get("audio") or message.get("data") or ""))
    if event_type == "input_audio_buffer.commit":
        return service.finalize_transcript()
    if event_type == "conversation.item.create":
        return service.inject_text(_extract_input_text(message.get("item") or {}))
    if event_type == "response.create":
        response = message.get("response") or {}
        instructions = response.get("instructions") if isinstance(response, dict) else None
        return service.create_response(instructions=instructions)
    if event_type == "response.cancel":
        return service.cancel_response(reason="client_cancelled")
    raise LiveSpeechProtocolError("unknown_or_invalid_event", f"unsupported realtime event: {event_type}")


def _extract_input_text(item: dict[str, Any]) -> str:
    if item.get("type") == "input_text":
        return str(item.get("text", "")).strip()
    parts = item.get("content") or []
    if isinstance(parts, list):
        return " ".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and part.get("type") == "input_text"
        ).strip()
    return ""
