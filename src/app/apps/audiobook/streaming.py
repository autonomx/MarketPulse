"""Story/audiobook websocket streaming routes for the browser gateway."""
from __future__ import annotations

import asyncio
import json
import time
import wave
from io import BytesIO
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.providers.tts_service import generate_audio_bytes, voice_stem

from .audio_text import split_sentences

AUDIOBOOK_SAMPLE_RATE = 24_000
AUDIOBOOK_FRAME_BYTES = 4_800  # 100 ms of mono int16 PCM at 24 kHz.
MAX_SENTENCE_CHARS = 500


def create_audiobook_streaming_router() -> APIRouter:
    """Create the Storyteller-compatible audiobook websocket router."""
    router = APIRouter()

    @router.websocket("/ws/audiobook")
    async def websocket_audiobook(websocket: WebSocket) -> None:
        await websocket.accept()
        loop = asyncio.get_running_loop()

        try:
            while True:
                raw = await websocket.receive_text()
                if not raw:
                    continue

                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "Invalid websocket JSON payload."})
                    continue

                message_type = str(message.get("type") or "")
                if message_type == "stop":
                    await websocket.send_json({"type": "stopped"})
                    return
                if message_type != "start":
                    await websocket.send_json({"type": "error", "message": "Expected a start message."})
                    continue

                sentence_segments = _sentence_segments_from_start_message(message)
                job_id = str(message.get("job_id") or f"story-{int(time.time())}")
                await websocket.send_json({"type": "start", "total_segments": len(sentence_segments)})

                for index, segment in enumerate(sentence_segments):
                    await websocket.send_json(
                        {
                            "type": "segment",
                            "index": index,
                            "speaker": segment["speaker"],
                            "text": segment["text"][:200],
                        }
                    )
                    chunks = await loop.run_in_executor(
                        None,
                        _generate_segment_pcm_chunks,
                        segment["text"],
                        segment["voice"],
                    )
                    for chunk in chunks:
                        await websocket.send_bytes(chunk)

                await websocket.send_json({"type": "done", "job_id": job_id})
        except WebSocketDisconnect:
            return
        except Exception as exc:  # pragma: no cover - defensive websocket cleanup.
            try:
                await websocket.send_json({"type": "error", "message": str(exc) or "Audiobook websocket failed."})
            except (WebSocketDisconnect, RuntimeError):
                return

    return router


def _sentence_segments_from_start_message(message: dict[str, Any]) -> list[dict[str, str]]:
    voice_mapping = message.get("voice_mapping") if isinstance(message.get("voice_mapping"), dict) else {}
    voice_map = message.get("voice_map") if isinstance(message.get("voice_map"), dict) else {}
    merged_map = {str(key).casefold().strip(): str(value).strip() for key, value in {**voice_mapping, **voice_map}.items() if value}
    default_voices = message.get("default_voices") if isinstance(message.get("default_voices"), dict) else {}

    sentence_segments: list[dict[str, str]] = []
    raw_segments = message.get("segments")
    if isinstance(raw_segments, list) and raw_segments:
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                continue
            speaker = _clean_text(raw_segment.get("speaker")) or "Narrator"
            text = _clean_text(raw_segment.get("text"))
            if not text:
                continue
            voice = _resolve_voice_for_speaker(speaker, merged_map, default_voices)
            for sentence in _split_into_sentences(text):
                sentence_segments.append({"speaker": speaker, "text": sentence, "voice": voice})
    else:
        plain_text = _clean_text(message.get("text"))
        voice = _resolve_voice_for_speaker("Narrator", merged_map, default_voices)
        for sentence in _split_into_sentences(plain_text):
            sentence_segments.append({"speaker": "Narrator", "text": sentence, "voice": voice})

    return sentence_segments


def _resolve_voice_for_speaker(speaker: str, merged_map: dict[str, str], default_voices: dict[str, Any]) -> str:
    mapped = merged_map.get(speaker.casefold().strip())
    if mapped:
        return voice_stem(mapped) or mapped
    default_voice = default_voices.get("narrator") or default_voices.get("male") or default_voices.get("female") or "Narrator"
    default_text = _clean_text(default_voice)
    return voice_stem(default_text) or default_text or "Narrator"


def _generate_segment_pcm_chunks(text: str, voice: str) -> list[bytes]:
    wav_bytes, _metadata = generate_audio_bytes(text, speaker=voice or "Narrator", payload={"language": "en"})
    return _wav_bytes_to_pcm_chunks(wav_bytes)


def _wav_bytes_to_pcm_chunks(wav_bytes: bytes) -> list[bytes]:
    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        if wav_file.getsampwidth() != 2:
            raise ValueError("Story audio websocket requires 16-bit PCM WAV audio.")
        if wav_file.getnchannels() != 1:
            raise ValueError("Story audio websocket requires mono WAV audio.")
        if wav_file.getframerate() != AUDIOBOOK_SAMPLE_RATE:
            raise ValueError(f"Story audio websocket requires {AUDIOBOOK_SAMPLE_RATE} Hz WAV audio.")
        pcm = wav_file.readframes(wav_file.getnframes())
    return [pcm[index : index + AUDIOBOOK_FRAME_BYTES] for index in range(0, len(pcm), AUDIOBOOK_FRAME_BYTES) if pcm[index : index + AUDIOBOOK_FRAME_BYTES]]


def _split_into_sentences(text: str) -> list[str]:
    cleaned = _clean_text(text)
    if not cleaned:
        return []
    sentences = split_sentences(cleaned)
    result: list[str] = []
    for sentence in sentences:
        result.extend(_split_long_sentence(_clean_text(sentence)))
    return [sentence for sentence in result if sentence]


def _split_long_sentence(sentence: str) -> list[str]:
    if len(sentence) <= MAX_SENTENCE_CHARS:
        return [sentence] if sentence else []
    chunks: list[str] = []
    remaining = sentence
    while len(remaining) > MAX_SENTENCE_CHARS:
        split_at = max(remaining.rfind(". ", 0, MAX_SENTENCE_CHARS), remaining.rfind(", ", 0, MAX_SENTENCE_CHARS))
        if split_at < MAX_SENTENCE_CHARS // 3:
            split_at = MAX_SENTENCE_CHARS
        chunks.append(remaining[: split_at + 1].strip())
        remaining = remaining[split_at + 1 :].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _clean_text(value: Any) -> str:
    return str(value).replace("\r\n", "\n").strip() if value is not None else ""
