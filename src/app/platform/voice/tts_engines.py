"""Choose Omnix's TTS engine: one local engine is loaded at a time.

Selecting an engine saves it as ``audio_provider_tts`` (the default for every
TTS caller), unloads the current engine, and loads the new one in the
background. Engines that clone from the exact words of a sample (Fish S2 Pro)
first get a transcript for every Voice Library sample that lacks one.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.providers.catalog import LOCAL_DEVICE, provider_supports, specs

from .output_settings import engine_controls

logger = logging.getLogger(__name__)

# Engines that need a reference transcript for every voice they clone.
TRANSCRIPT_ENGINES = frozenset({"fish-s2-pro"})

_LOCK = threading.Lock()
_SWITCH: dict[str, Any] = {
    "provider": None,
    "phase": "idle",
    "error": None,
    "voices": {"prepared": [], "failed": [], "already_ready": 0},
}


def _snapshot() -> dict[str, Any]:
    with _LOCK:
        return json.loads(json.dumps(_SWITCH))


def _update(**values: Any) -> None:
    with _LOCK:
        _SWITCH.update(values)


def local_engine_ids() -> tuple[str, ...]:
    return tuple(spec.id for spec in specs("tts") if LOCAL_DEVICE in spec.capabilities)


def _installed(provider_id: str) -> tuple[bool, str]:
    from app.providers.tts_artifacts import LocalArtifactsUnavailable, local_model_artifacts

    try:
        local_model_artifacts(provider_id)
    except (LocalArtifactsUnavailable, ImportError) as exc:
        return False, str(exc)
    except Exception as exc:  # A broken install reports, never breaks the listing.
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def tts_engines() -> dict[str, Any]:
    """The local engines, which one is selected, and the progress of the last switch."""
    from app.providers.service import selected_tts_provider_id

    selected = selected_tts_provider_id()
    engines = []
    for spec in specs("tts"):
        if LOCAL_DEVICE not in spec.capabilities:
            continue
        try:
            provider_class = spec.load()
        except Exception as exc:
            engines.append({"id": spec.id, "label": spec.id, "description": "", "selected": spec.id == selected,
                            "installed": False, "unavailable_reason": f"{type(exc).__name__}: {exc}",
                            "needs_transcripts": spec.id in TRANSCRIPT_ENGINES,
                            "voice_controls": engine_controls(spec.id)})
            continue
        installed, reason = _installed(spec.id)
        engines.append({
            "id": spec.id,
            "label": getattr(provider_class, "provider_display_name", spec.id),
            "description": getattr(provider_class, "provider_description", ""),
            "selected": spec.id == selected,
            "installed": installed,
            "unavailable_reason": reason,
            "needs_transcripts": spec.id in TRANSCRIPT_ENGINES,
            # The Voice Studio sliders that change this engine's output.
            "voice_controls": engine_controls(spec.id),
        })
    return {"selected": selected, "engines": engines, "switch": _snapshot()}


def _voice_samples() -> list[tuple[str, Path, str]]:
    """(voice id, sample path, language) for each Voice Library sample on disk."""
    from app.assets.canonical_voice_clones import discover_canonical_voice_clone_assets
    from app.assets.content import asset_available, materialize_asset

    samples = []
    for asset in discover_canonical_voice_clone_assets():
        if not asset_available(asset):
            continue
        samples.append((asset.id, Path(materialize_asset(asset)), str(asset.metadata.get("language") or "")))
    return samples


def _write_transcript(sample: Path, transcript: str, stt_provider: str) -> Path:
    """Add the transcript to the sample's sidecar, keeping anything already in it."""
    from app.assets.voice_clone_identity import reference_transcript

    sidecar = next((item for item in sample.parent.glob("*.json")
                    if item.stem.casefold() == sample.stem.casefold()), sample.with_suffix(".json"))
    try:
        existing = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    except (OSError, ValueError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    if reference_transcript(sample):
        return sidecar  # Written meanwhile: a transcript is never replaced.
    existing.update({
        "schema_version": existing.get("schema_version", 1),
        "voice_id": existing.get("voice_id") or sample.stem,
        "profile_name": existing.get("profile_name") or sample.stem,
        "audio_path": str(sample),
        "ref_text": transcript,
        "transcript_source": "stt",
        "stt_provider": stt_provider,
    })
    sidecar.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sidecar


def prepare_voice_transcripts() -> dict[str, Any]:
    """Transcribe, with the configured STT provider, each voice sample that has no transcript."""
    from app.assets.voice_clone_identity import reference_transcript
    from app.providers.service import get_stt_provider

    prepared: list[str] = []
    failed: list[dict[str, str]] = []
    already_ready = 0
    stt = None
    for voice_id, sample, language in _voice_samples():
        if reference_transcript(sample):
            already_ready += 1
            continue
        try:
            if stt is None:
                stt = get_stt_provider()
                if stt is None:
                    raise RuntimeError("no STT provider is configured")
            result = stt.transcribe(str(sample), language=language or None)
            transcript = str(result.get("text") or "").strip() if isinstance(result, dict) else ""
            if not isinstance(result, dict) or not result.get("success") or not transcript:
                detail = str(result.get("error") or "") if isinstance(result, dict) else ""
                raise RuntimeError(f"STT returned no transcript{': ' + detail if detail else ''}")
            _write_transcript(sample, transcript, str(getattr(stt, "provider_name", "") or "stt"))
            prepared.append(voice_id)
        except Exception as exc:
            logger.warning("Voice %s could not be transcribed: %s", voice_id, exc)
            failed.append({"voice_id": voice_id, "error": str(exc)})
    return {"prepared": prepared, "failed": failed, "already_ready": already_ready}


def _run_switch(provider_id: str) -> None:
    from app.providers.service import invalidate_audio_provider_cache

    from .tts_runtime_actions import warm_tts_runtime

    try:
        _update(phase="unloading")
        # Stops the cached engine; a local one releases its GPU memory here.
        invalidate_audio_provider_cache("tts")
        if provider_id in TRANSCRIPT_ENGINES:
            _update(phase="preparing_voices")
            _update(voices=prepare_voice_transcripts())
        _update(phase="loading")
        state = warm_tts_runtime("engine-switch")
        if state.get("status") == "failed":
            _update(phase="failed", error=str(state.get("error") or "the TTS engine did not load"))
        else:
            _update(phase="ready", error=None)
    except Exception as exc:
        logger.exception("TTS engine switch to %s failed", provider_id)
        _update(phase="failed", error=str(exc))


def select_tts_engine(provider_id: str) -> dict[str, Any]:
    """Make ``provider_id`` Omnix's TTS engine; loading continues in the background."""
    from app.settings.access import load_settings, save_settings

    if not provider_supports(provider_id, LOCAL_DEVICE, "tts"):
        raise ValueError(f"{provider_id!r} is not a local TTS engine")
    with _LOCK:
        if _SWITCH["phase"] in {"unloading", "preparing_voices", "loading"}:
            raise RuntimeError(f"a switch to {_SWITCH['provider']} is still in progress")
        _SWITCH.update(provider=provider_id, phase="unloading", error=None,
                       voices={"prepared": [], "failed": [], "already_ready": 0})
    try:
        if load_settings().get("audio_provider_tts") != provider_id:
            save_settings({"audio_provider_tts": provider_id})
    except Exception:
        _update(phase="failed", error="the engine choice could not be saved")
        raise
    threading.Thread(target=_run_switch, args=(provider_id,), name="tts-engine-switch", daemon=True).start()
    return tts_engines()


__all__ = [
    "TRANSCRIPT_ENGINES",
    "local_engine_ids",
    "prepare_voice_transcripts",
    "select_tts_engine",
    "tts_engines",
]
