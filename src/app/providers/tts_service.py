"""Feature-neutral TTS helpers shared by Voice and Audiobook."""
from __future__ import annotations

import base64
import re
from typing import Any


def generate_audio_bytes(
    text: str,
    *,
    speaker: str,
    payload: dict[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    from app.providers.service import get_tts_provider

    try:
        provider = get_tts_provider()
        if provider is None:
            raise RuntimeError("Configured TTS provider is unavailable")
        result = provider.generate_audio(
            text,
            speaker=speaker,
            language=tts_language_code(payload.get("language")),
            output_settings=payload.get("output_settings") or {},
            audio_effects=payload.get("audio_effects") or [],
            parity_mode=True,
            non_streaming_mode=True,
            use_cuda_graphs=False,
        )
        if not isinstance(result, dict):
            raise RuntimeError("TTS provider returned an invalid response")
        provider_error = _text(result.get("error"))
        provider_fallback = bool(result.get("is_fallback") or result.get("fallback"))
        if not result.get("success") or provider_fallback:
            reason = (
                provider_error
                or _text(result.get("fallback_reason"))
                or "TTS provider did not produce speech audio"
            )
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
        return wav_bytes, {
            "sample_rate": result.get("sample_rate"),
            "duration": result.get("duration"),
            "provider_success": True,
            "provider_fallback": False,
            "provider_error": "",
        }
    except Exception as exc:
        raise RuntimeError(
            f"Real TTS generation failed for speaker '{speaker}': {exc}"
        ) from exc


def tts_language_code(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "en"
    compact = re.sub(r"[^a-z]+", "", raw.casefold())
    first_word = re.split(r"[^a-z]+", raw.casefold(), maxsplit=1)[0]
    language_map = {
        "en": "en", "eng": "en", "english": "en", "englishus": "en",
        "englishuk": "en", "enus": "en", "engb": "en",
        "zh": "zh", "chinese": "zh", "mandarin": "zh",
        "ja": "ja", "japanese": "ja", "fr": "fr", "french": "fr",
        "de": "de", "german": "de", "es": "es", "spanish": "es",
        "it": "it", "italian": "it", "ru": "ru", "russian": "ru",
        "ko": "ko", "korean": "ko", "pt": "pt", "portuguese": "pt",
    }
    return language_map.get(compact) or language_map.get(first_word) or raw


def voice_stem(value: str) -> str:
    """The TTS speaker for a voice reference.

    Clients send the voice's asset id (``voice-cloning:<id>``), which the
    provider resolves, so it passes unchanged. A file path (saved by older
    clients) becomes its file stem."""
    if not value:
        return ""
    if value.startswith("voice-cloning:"):
        return value
    name = re.split(r"[\\/]", value)[-1]
    return name.rsplit(".", 1)[0]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
