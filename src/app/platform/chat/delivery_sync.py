"""Synchronize durable live delivery checkpoints into stored chat metadata."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .assistant_turns import AssistantTurnRecord


def sync_delivery_metadata(record: AssistantTurnRecord) -> bool:
    """Copy one coordinator checkpoint into its persisted user and assistant messages."""
    from .character_store import default_chat_store

    store = default_chat_store()
    metadata = _metadata(record)
    targeted_update = getattr(store, "update_delivery_metadata", None)
    if not callable(targeted_update):
        targeted_update = getattr(getattr(store, "_repository", None), "update_delivery_metadata", None)
    if callable(targeted_update):
        return bool(
            targeted_update(
                session_id=record.session_id,
                assistant_turn_id=record.assistant_turn_id,
                metadata=metadata,
            )
        )

    # Compatibility fallback for non-PostgreSQL stores. The active runtime uses
    # the targeted metadata update above so delivery checkpoints never serialize
    # the entire chat workspace against an immediately following voice turn.
    session = store.get_session(record.session_id)
    if session is None:
        return False
    changed = False
    for message in session.messages:
        if message.metadata.get("assistant_turn_id") != record.assistant_turn_id:
            continue
        message.metadata.update(metadata)
        changed = True
    if changed:
        store._save_session(session)
    return changed


def persist_live_voice_delivery(details: Mapping[str, Any]) -> None:
    """Persist one voice checkpoint through the chat-owned turn coordinator."""
    turn_id = str(details.get("assistant_turn_id") or "").strip()
    if not turn_id:
        return

    from .assistant_turns import default_assistant_turn_coordinator

    active_index = details.get("audio_interrupted_phrase_index")
    record = default_assistant_turn_coordinator().record_delivery(
        turn_id,
        generated_phrase_count=_count(details.get("generated_phrase_count")),
        audio_delivered_phrase_count=_count(details.get("audio_delivered_phrase_count")),
        audio_interrupted_phrase_index=(
            _count(active_index) if active_index is not None else None
        ),
        audio_played_samples=_count(details.get("audio_played_samples")),
        visual_delivered_text_end=_count(details.get("visual_delivered_text_end")),
        context_delivered_text_end=_count(details.get("context_delivered_text_end")),
        delivery_policy="reveal_as_spoken",
    )
    if record is not None:
        sync_delivery_metadata(record)


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _metadata(record: AssistantTurnRecord) -> dict[str, object]:
    return {
        "generated_phrase_count": record.generated_phrase_count,
        "audio_delivered_phrase_count": record.audio_delivered_phrase_count,
        "audio_interrupted_phrase_index": record.audio_interrupted_phrase_index,
        "audio_played_samples": record.audio_played_samples,
        "visual_delivered_text_end": record.visual_delivered_text_end,
        "context_delivered_text_end": record.context_delivered_text_end,
        "delivery_policy": record.delivery_policy,
    }
