"""Inline Voice Studio job execution for local backend wiring."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import re
import tempfile
import wave
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.assets import AssetRecord, AssetType, default_asset_store
from app.providers.tts_service import voice_stem
from app.persistence.blob_store import default_blob_store
from app.runtime.paths import resources_data_root

from app.jobs.models import CompleteJobRequest, CreateJobRequest, FailJobRequest, JobRecord
from app.jobs.inline_execution import mark_inline_execution, require_execution_authority

VOICE_STUDIO_JOB_TYPES = {
    "tts.synthesize",
    "tts.multi_speaker_synthesize",
    "voice-cloning.create-profile",
    "voice-cloning.transcribe-sample",
}
DEFAULT_UNTAGGED_SPEAKER = "Narrator"


def execute_voice_studio_job(job_store: Any, job: JobRecord) -> JobRecord:
    """Run a Voice Studio job synchronously and persist generated assets."""
    job_store.mark_running(job.id)
    try:
        if job.type == "voice-cloning.create-profile":
            result = _execute_clone_job(job, job_store=job_store)
        elif job.type == "voice-cloning.transcribe-sample":
            result = _execute_transcribe_sample_job(job)
        elif job.type in {"tts.synthesize", "tts.multi_speaker_synthesize"}:
            result = _execute_tts_job(job, job_store=job_store)
        else:  # pragma: no cover - guarded by caller.
            raise RuntimeError(f"Unsupported Voice Studio job type: {job.type}")
    except Exception as exc:
        failed = job_store.fail_job(
            job.id,
            FailJobRequest(
                code="voice_studio_job_failed",
                message=str(exc) or "Voice Studio job failed",
                retryable=True,
                details={"job_type": job.type, "module": job.module},
            ),
        )
        return failed or job

    completed = job_store.complete_job(
        job.id,
        CompleteJobRequest(
            output_refs=result["output_refs"],
            logs=[
                {
                    "level": "info",
                    "message": result["log_message"],
                    "content": result["content"],
                }
            ],
        ),
    )
    return completed or job


def _execute_clone_job(job: JobRecord, *, job_store: Any = None) -> dict[str, Any]:
    payload = job.input_payload or {}
    profile_name = _require_text(payload.get("profile_name"), "Voice name is required")
    audio_bytes = _sample_audio_bytes(payload)
    source_file_name = _text(payload.get("source_file_name")) or f"{profile_name}.wav"
    suffix = Path(source_file_name).suffix.lower() or ".wav"
    if suffix not in {".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg", ".flac"}:
        suffix = ".wav"

    voice_id = _safe_segment(profile_name)
    clone_dir = _voice_clone_dir()
    clone_dir.mkdir(parents=True, exist_ok=True)
    clone_path = clone_dir / f"{voice_id}{suffix}"
    require_execution_authority(job_store, job.id)
    clone_path.write_bytes(audio_bytes)
    reference_text, transcript_source, stt_provider = _reference_transcript(payload, clone_path)
    require_execution_authority(job_store, job.id)
    transcript_path = _write_reference_metadata(
        clone_dir=clone_dir,
        voice_id=voice_id,
        profile_name=profile_name,
        clone_path=clone_path,
        language=_text(payload.get("language")) or "English (US)",
        reference_text=reference_text,
        transcript_source=transcript_source,
        stt_provider=stt_provider,
    )
    enriched_payload = {
        **payload,
        "reference_text": reference_text,
        "transcript_source": transcript_source,
        "transcript_path": str(transcript_path) if transcript_path else "",
        "stt_provider_id": stt_provider,
    }
    require_execution_authority(job_store, job.id)
    _upsert_legacy_voice_manifest(voice_id, profile_name, enriched_payload, clone_path)

    require_execution_authority(job_store, job.id)
    asset = _upsert_asset(
        AssetRecord(
            id=f"voice-cloning:{voice_id}",
            module="voice-cloning",
            type=AssetType.VOICE_PROFILE,
            mime_type=_mime_for_path(clone_path),
            storage_path=str(clone_path),
            metadata={
                "profile_name": profile_name,
                "voice_id": voice_id,
                "language": _text(payload.get("language")) or "English (US)",
                "quality": _text(payload.get("quality")) or "High (Recommended)",
                "notes": _text(payload.get("notes")),
                "source_kind": _text(payload.get("source_kind")) or "upload",
                "source_file_name": source_file_name,
                "provider_id": _text(payload.get("provider_id")),
                "reference_text": reference_text,
                "transcript_source": transcript_source,
                "transcript_path": str(transcript_path) if transcript_path else "",
                "stt_provider_id": stt_provider,
                "has_audio": True,
            },
            source_job_id=job.id,
            created_at=_utcnow(),
            compat={"contract": "voice_studio_clone_v1", "storage_hint": "resources/voice_clones"},
        )
    )
    return {
        "content": f"Voice clone '{profile_name}' saved to {clone_path}",
        "log_message": "Voice clone profile stored locally",
        "output_refs": [_asset_output_ref(asset, title=profile_name)],
    }


def _execute_transcribe_sample_job(job: JobRecord) -> dict[str, Any]:
    """Transcribe a clone sample without creating a voice-library entry."""
    payload = job.input_payload or {}
    audio_bytes = _sample_audio_bytes(payload)
    source_file_name = _text(payload.get("source_file_name")) or "voice-sample.wav"
    suffix = Path(source_file_name).suffix.lower() or ".wav"
    if suffix not in {".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg", ".flac"}:
        suffix = ".wav"

    with tempfile.TemporaryDirectory(prefix="omnix-voice-transcript-") as temp_dir:
        sample_path = Path(temp_dir) / f"sample{suffix}"
        sample_path.write_bytes(audio_bytes)
        transcript, _source, provider_name = _reference_transcript(
            {**payload, "reference_text": "", "generate_transcript": True},
            sample_path,
        )

    return {
        "content": transcript,
        "log_message": "Voice clone sample transcribed",
        "output_refs": [
            {
                "type": "transcript",
                "title": "Reference transcript",
                "content": transcript,
                "provider_id": provider_name,
            }
        ],
    }


def _reference_transcript(payload: dict[str, Any], clone_path: Path) -> tuple[str, str, str]:
    manual_text = _text(payload.get("reference_text"))
    if manual_text:
        return manual_text, "manual", ""
    if not bool(payload.get("generate_transcript")):
        return "", "", ""

    from app.providers.service import get_stt_provider

    requested_provider = _text(payload.get("stt_provider_id"))
    provider = get_stt_provider(requested_provider or None)
    if provider is None:
        raise ValueError(
            "The configured STT provider is unavailable; enter a reference transcript "
            "manually or disable STT generation"
        )

    result = provider.transcribe(
        str(clone_path),
        language=_text(payload.get("language")) or None,
    )
    transcript = _text(result.get("text")) if isinstance(result, dict) else ""
    if not isinstance(result, dict) or not result.get("success") or not transcript:
        detail = _text(result.get("error")) if isinstance(result, dict) else ""
        raise ValueError(
            f"STT could not generate a reference transcript{': ' + detail if detail else ''}"
        )

    provider_name = (
        requested_provider
        or _text(getattr(provider, "provider_name", ""))
        or "configured-stt"
    )
    return transcript, "stt", provider_name


def _write_reference_metadata(
    *,
    clone_dir: Path,
    voice_id: str,
    profile_name: str,
    clone_path: Path,
    language: str,
    reference_text: str,
    transcript_source: str,
    stt_provider: str,
) -> Path | None:
    if not reference_text:
        return None
    transcript_path = clone_dir / f"{voice_id}.json"
    transcript_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "voice_id": voice_id,
                "profile_name": profile_name,
                "audio_path": str(clone_path),
                "language": language,
                "ref_text": reference_text,
                "transcript_source": transcript_source,
                "stt_provider": stt_provider,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return transcript_path


def _execute_tts_job(job: JobRecord, *, job_store: Any = None) -> dict[str, Any]:
    payload = job.input_payload or {}
    text = _require_text(payload.get("text"), "Text is required")
    raw_assignments = payload.get("character_voice_assignments")
    assignments: list[Any] = raw_assignments if isinstance(raw_assignments, list) else []
    assignments_by_speaker = _assignments_by_speaker(assignments)
    segments = _script_segments(payload, text)
    primary_voice = _text(payload.get("voice_id")) or _first_assignment_voice(assignments)
    default_speaker = voice_stem(primary_voice) or _text(payload.get("speaker")) or DEFAULT_UNTAGGED_SPEAKER

    wav_chunks: list[bytes] = []
    segment_outputs: list[dict[str, Any]] = []
    for segment in segments:
        speaker_name = _text(segment.get("speaker")) or DEFAULT_UNTAGGED_SPEAKER
        assignment = assignments_by_speaker.get(speaker_name.casefold(), {})
        voice_id = _text(assignment.get("voice_id")) or primary_voice
        generated_speaker = voice_stem(voice_id) or speaker_name or default_speaker
        wav_bytes, metadata = _generate_audio_bytes(_text(segment.get("text")), speaker=generated_speaker, payload=payload)
        wav_chunks.append(wav_bytes)
        segment_outputs.append(
            {
                "index": int(segment.get("index") or len(segment_outputs)),
                "speaker": speaker_name,
                "text": _text(segment.get("text")),
                "voice_id": voice_id,
                "style": _text(assignment.get("style")),
                "sample_rate": metadata.get("sample_rate"),
                "duration": metadata.get("duration"),
                "provider_success": metadata.get("provider_success"),
                "provider_fallback": metadata.get("provider_fallback"),
                "provider_error": metadata.get("provider_error"),
                "voice_controls": metadata.get("voice_controls"),
            }
        )

    wav_bytes, combined_metadata = _combine_segment_wavs(wav_chunks, text)
    title = _tts_title(payload, job)
    output_dir = resources_data_root() / "voice_studio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_safe_segment(title)}-{_safe_segment(job.id)}.wav"
    require_execution_authority(job_store, job.id)
    output_path.write_bytes(wav_bytes)

    metadata = {
        "title": title,
        "text": text,
        "speaker": default_speaker,
        "provider_id": _text(payload.get("provider_id")),
        "script_mode": "multi_speaker" if len({segment["speaker"] for segment in segments}) > 1 else "single_speaker",
        "script_speakers": payload.get("script_speakers") or _speakers_from_segments(segments),
        "script_segments": segments,
        "segment_outputs": segment_outputs,
        "character_voice_assignments": assignments,
        "output_settings": payload.get("output_settings") or {},
        "audio_effects": payload.get("audio_effects") or [],
        **combined_metadata,
    }
    require_execution_authority(job_store, job.id)
    asset = _upsert_asset(
        AssetRecord(
            id=f"audio:voice-studio-{_safe_segment(job.id)}",
            module="voice",
            type=AssetType.AUDIO,
            mime_type="audio/wav",
            storage_path=str(output_path),
            metadata=metadata,
            source_job_id=job.id,
            created_at=_utcnow(),
            compat={"contract": "voice_studio_tts_v2", "generation_mode": "segment_stitch"},
        )
    )
    output_ref = _asset_output_ref(asset, title=title)
    output_ref.update(
        {
            # Served from the stored asset; job rows never embed the audio.
            "audio_url": asset_audio_url(asset.id),
            "duration": combined_metadata.get("duration"),
            "segments": segment_outputs,
        }
    )
    return {
        "content": f"Speech audio saved to {output_path}",
        "log_message": "Voice Studio speech generated per script segment and saved",
        "output_refs": [output_ref],
    }


def _generate_audio_bytes(text: str, *, speaker: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    try:
        from app.providers.service import get_tts_provider

        from .output_settings import adjust_wav, audio_adjustments, sampling_overrides

        provider = get_tts_provider()
        if provider is None:
            raise RuntimeError("Configured TTS provider is unavailable")
        output_settings = payload.get("output_settings") if isinstance(payload.get("output_settings"), dict) else {}
        sampling = sampling_overrides(str(getattr(provider, "provider_name", "") or ""), output_settings)
        result = provider.generate_audio(
            text,
            speaker=speaker,
            language=_tts_language_code(payload.get("language")),
            parity_mode=True,
            non_streaming_mode=True,
            use_cuda_graphs=False,
            **sampling,
        )
        if not isinstance(result, dict):
            raise RuntimeError("TTS provider returned an invalid response")

        provider_error = _text(result.get("error"))
        provider_fallback = bool(result.get("is_fallback") or result.get("fallback"))
        if not result.get("success") or provider_fallback:
            reason = provider_error or _text(result.get("fallback_reason")) or "TTS provider did not produce speech audio"
            raise RuntimeError(reason)

        encoded = _text(result.get("audio_base64")) or _text(result.get("audio"))
        if not encoded:
            raise RuntimeError("TTS provider returned no speech audio")
        try:
            wav_bytes = base64.b64decode(encoded)
        except Exception as exc:
            raise RuntimeError("TTS provider returned invalid base64 audio") from exc
        if not wav_bytes:
            raise RuntimeError("TTS provider returned empty speech audio")
        adjustments = audio_adjustments(output_settings)
        if adjustments:
            wav_bytes = adjust_wav(wav_bytes, adjustments)
        timing = _wav_metadata(wav_bytes) if adjustments else {
            "sample_rate": result.get("sample_rate"), "duration": result.get("duration"),
        }
        return wav_bytes, {
            **timing,
            "provider_success": True,
            "provider_fallback": False,
            "provider_error": "",
            "voice_controls": {"sampling": sampling, "audio": adjustments},
        }
    except Exception as exc:
        raise RuntimeError(f"Real TTS generation failed for speaker '{speaker}': {exc}") from exc


def _tts_language_code(value: Any) -> str:
    """Normalize UI language labels to provider language codes.

    The Qwen3 provider maps short codes such as ``en`` to its supported
    language name (``English``). Podcast forms use display labels like
    ``English (US)``, which the provider runtime does not accept directly.
    """
    raw = _text(value)
    if not raw:
        return "en"

    compact = re.sub(r"[^a-z]+", "", raw.casefold())
    first_word = re.split(r"[^a-z]+", raw.casefold(), maxsplit=1)[0]
    language_map = {
        "en": "en",
        "eng": "en",
        "english": "en",
        "englishus": "en",
        "englishuk": "en",
        "enus": "en",
        "engb": "en",
        "zh": "zh",
        "chinese": "zh",
        "mandarin": "zh",
        "ja": "ja",
        "japanese": "ja",
        "fr": "fr",
        "french": "fr",
        "de": "de",
        "german": "de",
        "es": "es",
        "spanish": "es",
        "it": "it",
        "italian": "it",
        "ru": "ru",
        "russian": "ru",
        "ko": "ko",
        "korean": "ko",
        "pt": "pt",
        "portuguese": "pt",
    }
    return language_map.get(compact) or language_map.get(first_word) or raw


def _fallback_wav(text: str) -> bytes:
    sample_rate = 12000
    duration = _fallback_duration(text)
    frame_count = max(int(sample_rate * duration), sample_rate // 2)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            t = index / sample_rate
            envelope = min(1.0, index / max(sample_rate * 0.03, 1), (frame_count - index) / max(sample_rate * 0.03, 1))
            value = int(32767 * 0.14 * envelope * (math.sin(2 * math.pi * 220 * t) + 0.45 * math.sin(2 * math.pi * 330 * t)))
            frames.extend(value.to_bytes(2, byteorder="little", signed=True))
        wav_file.writeframes(bytes(frames))
    return buffer.getvalue()


def _combine_segment_wavs(wav_chunks: list[bytes], fallback_text: str) -> tuple[bytes, dict[str, Any]]:
    if not wav_chunks:
        raise ValueError("No TTS audio segments were generated")
    if len(wav_chunks) == 1:
        metadata = _wav_metadata(wav_chunks[0])
        metadata["segment_count"] = 1
        return wav_chunks[0], metadata
    try:
        combined = _concat_wavs(wav_chunks)
        metadata = _wav_metadata(combined)
        metadata["segment_count"] = len(wav_chunks)
        return combined, metadata
    except Exception as exc:
        raise ValueError("Voice Studio could not stitch generated speech segments") from exc


def _concat_wavs(wav_chunks: list[bytes]) -> bytes:
    params = None
    frames = bytearray()
    for wav_bytes in wav_chunks:
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            current_params = (wav_file.getnchannels(), wav_file.getsampwidth(), wav_file.getframerate())
            if params is None:
                params = current_params
            if current_params != params:
                raise ValueError("segment audio parameters do not match")
            frames.extend(wav_file.readframes(wav_file.getnframes()))
            frames.extend(b"\x00" * current_params[0] * current_params[1] * max(int(current_params[2] * 0.16), 1))
    channels, sample_width, frame_rate = params or (1, 2, 12000)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(frame_rate)
        output.writeframes(bytes(frames))
    return buffer.getvalue()


def _wav_metadata(wav_bytes: bytes) -> dict[str, Any]:
    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
    return {"sample_rate": sample_rate, "duration": frame_count / sample_rate if sample_rate else 0.0}


def _fallback_duration(text: str) -> float:
    return max(0.75, min(6.0, max(len(text.strip()), 1) / 16.0))


def _script_segments(payload: dict[str, Any], text: str) -> list[dict[str, Any]]:
    raw_segments = payload.get("script_segments")
    segments: list[dict[str, Any]] = []
    if isinstance(raw_segments, list):
        for index, raw in enumerate(raw_segments):
            if not isinstance(raw, dict):
                continue
            segment_text = _text(raw.get("text"))
            if segment_text:
                segments.append({"index": int(raw.get("index") or index), "speaker": _text(raw.get("speaker")) or DEFAULT_UNTAGGED_SPEAKER, "text": segment_text})
        if segments:
            return segments

    segments = []
    untagged: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        tagged = _parse_tagged_line(line)
        if tagged:
            if untagged:
                segments.append({"index": len(segments), "speaker": DEFAULT_UNTAGGED_SPEAKER, "text": " ".join(untagged)})
                untagged = []
            segments.append({"index": len(segments), **tagged})
        else:
            untagged.append(line)
    if untagged:
        segments.append({"index": len(segments), "speaker": DEFAULT_UNTAGGED_SPEAKER, "text": " ".join(untagged)})
    return segments or [{"index": 0, "speaker": DEFAULT_UNTAGGED_SPEAKER, "text": text.strip()}]


def _parse_tagged_line(line: str) -> dict[str, str] | None:
    colon = line.find(":")
    if colon <= 0:
        return None
    speaker = line[:colon].strip()
    content = line[colon + 1 :].strip()
    if not speaker or not content or len(speaker) > 50:
        return None
    return {"speaker": speaker, "text": content}


def _speakers_from_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for segment in segments:
        speaker = _text(segment.get("speaker")) or DEFAULT_UNTAGGED_SPEAKER
        counts[speaker] = counts.get(speaker, 0) + 1
    return [{"name": speaker, "count": count} for speaker, count in counts.items()]


def _assignments_by_speaker(assignments: list[Any]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in assignments:
        if isinstance(row, dict):
            speaker = _text(row.get("speaker"))
            if speaker:
                mapped[speaker.casefold()] = row
    return mapped


MAX_CLONE_SAMPLE_BYTES = 50 * 1024 * 1024


def asset_audio_url(asset_id: str) -> str:
    return f"/api/assets/{quote(asset_id, safe='')}/audio"


def store_inline_clone_sample(input_payload: dict[str, Any]) -> dict[str, Any]:
    """Move an inline base64 sample into the blob store at job admission.

    The job row then carries only the content-addressed blob key and digest,
    never the audio itself.
    """
    if not input_payload.get("sample_audio_base64"):
        return input_payload
    audio = _decode_audio_payload(input_payload["sample_audio_base64"])
    if not audio:
        raise ValueError("Audio sample is required")
    if len(audio) > MAX_CLONE_SAMPLE_BYTES:
        raise ValueError("Audio sample is too large")
    if not _is_audio_container(audio):
        raise ValueError("Audio sample is not a WAV, MP3, MP4, WebM, Ogg or FLAC file")
    digest = hashlib.sha256(audio).hexdigest()
    suffix = Path(_text(input_payload.get("source_file_name")) or "sample.wav").suffix.lower()
    if suffix not in {".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg", ".flac"}:
        suffix = ".wav"
    record = default_blob_store().put_bytes(f"voice-samples/{digest[:2]}/{digest}{suffix}", audio)
    updated = {key: value for key, value in input_payload.items() if key != "sample_audio_base64"}
    updated["sample_blob_key"] = record["storage_key"]
    updated["sample_sha256"] = digest
    return updated


def _is_audio_container(data: bytes) -> bool:
    """A sample's leading bytes name an accepted audio container (ASVS 12.2.1)."""
    head = data[:12]
    return (
        (head[:4] == b"RIFF" and head[8:12] == b"WAVE")
        or head[:4] in {b"OggS", b"fLaC", b"\x1aE\xdf\xa3"}
        or head[:3] == b"ID3"
        or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
        or head[4:8] == b"ftyp"
    )


def _sample_audio_bytes(payload: dict[str, Any]) -> bytes:
    key = _text(payload.get("sample_blob_key"))
    if key:
        return default_blob_store().read_bytes(key, expected_checksum=_text(payload.get("sample_sha256")) or None)
    # Jobs queued before samples moved to the blob store.
    return _decode_audio_payload(payload.get("sample_audio_base64"))


def _decode_audio_payload(value: Any) -> bytes:
    encoded = _require_text(value, "Audio sample is required")
    if "," in encoded and encoded.strip().lower().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        return base64.b64decode(encoded, validate=False)
    except Exception as exc:
        raise ValueError("Audio sample could not be decoded") from exc


def _upsert_legacy_voice_manifest(voice_id: str, profile_name: str, payload: dict[str, Any], clone_path: Path) -> None:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        return
    from app.runtime.paths import VOICE_CLONES_FILE

    manifest_path = Path(VOICE_CLONES_FILE)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    except Exception:
        raw = {}
    raw[profile_name] = {
        "voice_clone_id": voice_id,
        "speaker": profile_name,
        "language": _text(payload.get("language")) or "English (US)",
        "gender": "neutral",
        "has_audio": True,
        "is_preloaded": False,
        "source_file_name": _text(payload.get("source_file_name")) or clone_path.name,
        "source_path": str(clone_path),
        "notes": _text(payload.get("notes")),
        "ref_text": _text(payload.get("reference_text")),
        "transcript_source": _text(payload.get("transcript_source")),
        "transcript_path": _text(payload.get("transcript_path")),
        "stt_provider": _text(payload.get("stt_provider_id")),
    }
    manifest_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _voice_clone_dir() -> Path:
    from app.runtime.paths import VOICE_CLONES_DIR

    return Path(VOICE_CLONES_DIR)


def _upsert_asset(asset: AssetRecord) -> AssetRecord:
    return default_asset_store().upsert_asset(asset)


def _asset_output_ref(asset: AssetRecord, *, title: str) -> dict[str, Any]:
    return {
        "type": asset.type.value if hasattr(asset.type, "value") else str(asset.type),
        "asset_id": asset.id,
        "title": title,
        "mime_type": asset.mime_type,
    }


def _first_assignment_voice(assignments: list[Any]) -> str:
    for row in assignments:
        if isinstance(row, dict):
            value = _text(row.get("voice_id"))
            if value:
                return value
    return ""


def _tts_title(payload: dict[str, Any], job: JobRecord) -> str:
    speakers = payload.get("script_speakers")
    if isinstance(speakers, list) and len(speakers) > 1:
        return "multi_voice_script"
    speaker = _text(payload.get("speaker")) or DEFAULT_UNTAGGED_SPEAKER
    return f"{speaker}_speech"


def _mime_for_path(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "voice"


def _require_text(value: Any, message: str) -> str:
    result = _text(value)
    if not result:
        raise ValueError(message)
    return result


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
