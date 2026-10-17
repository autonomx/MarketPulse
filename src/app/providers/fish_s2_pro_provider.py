"""Fish Audio S2 Pro TTS provider.

Fish Speech needs its own Python environment (torch 2.8, transformers 5), so
the model runs in the Fish API server as a child process of whichever Omnix
process owns TTS. ``start`` launches that server and waits until the model is
loaded; ``stop`` ends the process, which releases all of its GPU memory.

Voices come from the shared Voice Library. Each request carries the clone's
reference audio and its transcript; the server caches the encoded reference
by audio hash, so a voice is encoded once per server lifetime.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any, Iterator, Optional

import numpy as np

from app.config.env import environment_copy
from app.runtime.paths import repo_root, resources_root

from .audio_base import AudioProviderCapability, BaseTTSProvider
from .tts_priority import generation_slot
from .voice_references import clone_reference, clone_speakers

logger = logging.getLogger(__name__)

PROVIDER_ID = "fish-s2-pro"
MODEL_ID = "Fish-Audio-S2-Pro"
# The S2 Pro codec decodes 44.1 kHz mono audio, one frame per 2048 samples.
FISH_SAMPLE_RATE = 44_100
FISH_CODEC_STEPS_PER_SECOND = 21.5
_WAV_HEADER_BYTES = 44
# Starts fish-speech's API server with lower peak host memory (see that module).
_SERVER_WRAPPER = Path(__file__).resolve().parents[2] / "services" / "tts" / "fish_api_server.py"

DEFAULT_FISH_SETTINGS: dict[str, Any] = {
    "port": 5102,
    "checkpoint_dir": "",
    "code_dir": "",
    "python": "",
    "half": False,
    "compile": False,
    # Context positions; the KV cache and mask scale with it (32768: ~6 GB).
    "max_seq_len": 12288,
    "startup_timeout_s": 600,
    "request_timeout_s": 600,
    "temperature": 0.7,
    "top_p": 0.7,
    "repetition_penalty": 1.2,
    "max_new_tokens": 0,
    "chunk_length": 200,
    "normalize": True,
}

# Request fields that change the generated audio: render cache identity.
_GENERATION_KEYS = (
    "temperature", "top_p", "repetition_penalty", "max_new_tokens", "chunk_length", "normalize", "seed",
)


def with_direction(text: str, instruct: Any) -> str:
    """Lead the text with a delivery direction as an S2 inline tag: ``[angry] Get out.``

    Audiobook spans carry a free-text delivery note ("angry, through gritted
    teeth"); S2 Pro reads any bracketed natural-language instruction.
    """
    direction = " ".join(str(instruct or "").replace("[", " ").replace("]", " ").split())
    direction = direction[:120].strip(" ,.;:")
    return f"[{direction}] {text}" if direction else text


class FishReferenceTranscriptMissing(RuntimeError):
    """S2 Pro clones from the reference audio *and* its exact words."""


def _models_dir() -> Path:
    return resources_root() / "models" / "tts"


def _fish_python(configured: str) -> str:
    if configured:
        return configured
    from app.composition.launcher.config import load_launcher_config

    return load_launcher_config(repo_root()).python("fish")


def _wav_bytes(path: str) -> bytes:
    """The reference as a PCM WAV: the server reads WAV without ffmpeg."""
    import soundfile

    audio, sample_rate = soundfile.read(path, dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    buffer = io.BytesIO()
    soundfile.write(buffer, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def _wav_metadata(wav_bytes: bytes) -> tuple[int, float]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        frames = wav_file.getnframes()
    return sample_rate, frames / sample_rate if sample_rate else 0.0


def _stop_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30, check=False)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class FishS2ProTTSProvider(BaseTTSProvider):
    """Voice-cloning TTS on Fish Audio S2 Pro, served by a managed Fish API server."""

    provider_name = PROVIDER_ID
    provider_display_name = "Fish Audio S2 Pro"
    provider_description = "Expressive voice cloning with inline [emotion] tags (Fish Audio S2 Pro, local)"
    # v2: a delivery instruction leads the text as an inline tag.
    generation_strategy_revision = "fish-s2-pro-generation-v2"
    sample_rate = FISH_SAMPLE_RATE

    default_capabilities = [
        AudioProviderCapability.STREAMING,
        AudioProviderCapability.VOICE_CLONING,
        AudioProviderCapability.MULTILINGUAL,
    ]

    def __init__(self, config: Optional[dict[str, Any]] = None):
        super().__init__({**DEFAULT_FISH_SETTINGS, **dict(config or {})})
        self._lock = threading.Lock()
        self._last_error = ""
        self._http_client = None

    @staticmethod
    def checkpoint_dir(config: Optional[dict[str, Any]] = None) -> Path:
        configured = str((config or {}).get("checkpoint_dir") or "").strip()
        return Path(configured) if configured else _models_dir() / "fish-s2-pro"

    @staticmethod
    def local_model_artifacts():
        """The installed checkpoint that audiobook renders are pinned to."""
        from app.settings.access import load_settings

        from .tts_artifacts import LocalArtifactsUnavailable, LocalModelArtifacts

        directory = FishS2ProTTSProvider.checkpoint_dir(load_settings().get(PROVIDER_ID) or {}).resolve()
        if not directory.is_dir():
            raise LocalArtifactsUnavailable("the Fish Audio S2 Pro checkpoint is not installed")
        if not list(directory.glob("*.safetensors")) or not (directory / "codec.pth").is_file():
            raise LocalArtifactsUnavailable("the Fish Audio S2 Pro checkpoint is incomplete")
        return LocalModelArtifacts(model_id=MODEL_ID, directory=directory)

    # -- server lifecycle -------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{int(self.config['port'])}"

    def _http(self):
        if self._http_client is None:
            import httpx

            self._http_client = httpx.Client(timeout=float(self.config["request_timeout_s"]))
        return self._http_client

    def _log_path(self) -> Path:
        return resources_root() / "logs" / "tts" / "fish-s2-pro.log"

    def _pid_path(self) -> Path:
        return resources_root() / "logs" / "tts" / "fish-s2-pro.pid"

    def _server_healthy(self) -> bool:
        try:
            response = self._http().get(self.base_url + "/v1/health", timeout=3)
        except Exception:
            return False
        return response.status_code == 200

    def _server_command(self) -> list[str]:
        checkpoint = self.checkpoint_dir(self.config).resolve()
        command = [
            _fish_python(str(self.config.get("python") or "").strip()),
            "-W", "ignore",
            str(_SERVER_WRAPPER),
            "--listen", f"127.0.0.1:{int(self.config['port'])}",
            "--llama-checkpoint-path", str(checkpoint),
            "--decoder-checkpoint-path", str(checkpoint / "codec.pth"),
            "--decoder-config-name", "modded_dac_vq",
        ]
        if self.config.get("max_seq_len"):
            command += ["--max-seq-len", str(int(self.config["max_seq_len"]))]
        if self.config.get("half"):
            command.append("--half")
        if self.config.get("compile"):
            command.append("--compile")
        return command

    def _code_dir(self) -> Path:
        configured = str(self.config.get("code_dir") or "").strip()
        return Path(configured) if configured else _models_dir() / "fish-speech"

    def _log_tail(self, lines: int = 12) -> str:
        try:
            text = self._log_path().read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(line for line in text.splitlines()[-lines:] if line.strip())

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._server_healthy():
                return {"running": True, "message": f"Fish Audio S2 Pro is serving at {self.base_url}"}
            try:
                self._launch_and_wait()
            except Exception as exc:
                self._last_error = str(exc)
                logger.error("Fish Audio S2 Pro failed to start: %s", exc)
                self._terminate()
                return {"running": False, "message": f"Failed to start: {exc}", "error": str(exc),
                        "provider_state": self.get_runtime_status()}
            self._last_error = ""
            return {"running": True, "message": f"Fish Audio S2 Pro loaded from {self.checkpoint_dir(self.config)}"}

    def _launch_and_wait(self) -> None:
        checkpoint = self.checkpoint_dir(self.config)
        if not (checkpoint / "codec.pth").is_file():
            raise RuntimeError(f"Fish Audio S2 Pro weights are missing from {checkpoint}")
        code_dir = self._code_dir()
        if not (code_dir / "tools" / "api_server.py").is_file():
            raise RuntimeError(f"fish-speech is not installed at {code_dir}")
        log_path = self._log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = environment_copy()
        # The child must not import Omnix's packages ahead of its own.
        env.pop("PYTHONPATH", None)
        env["PYTHONUNBUFFERED"] = "1"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with log_path.open("w", encoding="utf-8") as log_file:
            self.process = subprocess.Popen(
                self._server_command(), cwd=str(code_dir), env=env,
                stdout=log_file, stderr=subprocess.STDOUT, creationflags=creationflags,
            )
        self._pid_path().write_text(str(self.process.pid), encoding="utf-8")
        deadline = time.monotonic() + float(self.config["startup_timeout_s"])
        while time.monotonic() < deadline:
            code = self.process.poll()
            if code is not None:
                # 0xc0000005 while loading weights: host memory (commit) ran out.
                raise RuntimeError(
                    f"the Fish API server exited during startup (exit code {code & 0xFFFFFFFF:#x}):\n{self._log_tail()}"
                )
            if self._server_healthy():
                logger.info("Fish Audio S2 Pro ready at %s (pid %s)", self.base_url, self.process.pid)
                return
            time.sleep(1.0)
        raise RuntimeError(f"the Fish API server did not load within {self.config['startup_timeout_s']}s")

    def _terminate(self) -> None:
        process, self.process = self.process, None
        pid = process.pid if process is not None and process.poll() is None else None
        if pid is None:
            # A server left behind by an earlier owner of this provider.
            try:
                pid = int(self._pid_path().read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid = None
        if pid is not None:
            _stop_process_tree(pid)
            if process is not None:
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    logger.warning("Fish API server pid %s did not exit after kill", pid)
        try:
            self._pid_path().unlink()
        except OSError:
            pass

    def stop(self) -> bool:
        """End the Fish API server: the process exit releases its GPU memory."""
        with self._lock:
            try:
                self._terminate()
            except Exception as exc:
                logger.error("Error stopping Fish Audio S2 Pro: %s", exc)
                return False
            if self._http_client is not None:
                self._http_client.close()
                self._http_client = None
            return True

    def health_check(self) -> bool:
        return self._server_healthy()

    def test_connection(self) -> bool:
        return self.health_check()

    def get_runtime_status(self) -> dict[str, Any]:
        process = self.process
        return {
            "model_loaded": self._server_healthy(),
            "server_url": self.base_url,
            "server_pid": process.pid if process is not None and process.poll() is None else None,
            "configured_model_source": str(self.checkpoint_dir(self.config)),
            "last_error": self._last_error,
        }

    def get_capabilities(self) -> list[AudioProviderCapability]:
        return self.default_capabilities.copy()

    def supports_streaming(self) -> bool:
        return True

    def supports_voice_cloning(self) -> bool:
        return True

    # -- synthesis --------------------------------------------------------

    def get_speakers(self) -> list[dict[str, Any]]:
        return clone_speakers()

    def resolve_generation_parameters(self, parameters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        requested = dict(parameters or {})
        resolved = {key: requested.get(key, self.config.get(key)) for key in _GENERATION_KEYS}
        # Audiobook speech budgets count codec frames at this rate.
        resolved["codec_steps_per_second"] = FISH_CODEC_STEPS_PER_SECOND
        resolved["_generation_strategy_revision"] = self.generation_strategy_revision
        return resolved

    def _reference(self, speaker: Optional[str]) -> dict[str, Any]:
        path, transcript = clone_reference(speaker)
        if not path:
            raise RuntimeError("No reference audio available for voice cloning. Please upload a voice sample first.")
        if not transcript:
            from app.assets.voice_clone_identity import reference_transcript

            transcript = reference_transcript(path)
        if not transcript:
            raise FishReferenceTranscriptMissing(
                f"Voice '{Path(path).stem}' has no reference transcript; Fish Audio S2 Pro needs the exact "
                "words spoken in the sample. Prepare the voice library in Voice Studio or re-clone with a transcript."
            )
        return {"audio": base64.b64encode(_wav_bytes(path)).decode("ascii"), "text": transcript}

    def _request(self, text: str, speaker: Optional[str], parameters: dict[str, Any], *, streaming: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": text,
            "references": [self._reference(speaker)],
            "format": "wav",
            "streaming": streaming,
            "use_memory_cache": "on",
            "normalize": bool(parameters["normalize"]),
            "chunk_length": int(parameters["chunk_length"]),
            "max_new_tokens": int(parameters["max_new_tokens"] or 0),
            "temperature": float(parameters["temperature"]),
            "top_p": float(parameters["top_p"]),
            "repetition_penalty": float(parameters["repetition_penalty"]),
        }
        if parameters.get("seed") is not None:
            payload["seed"] = int(parameters["seed"])
        return payload

    def generate_audio(
        self, text: str, speaker: Optional[str] = None,
        language: Optional[str] = None, **kwargs,
    ) -> dict[str, Any]:
        kwargs.pop("_progress_callback", None)
        text = with_direction(text, kwargs.pop("instruct", None))
        parameters = self.resolve_generation_parameters(kwargs)
        try:
            payload = self._request(text, speaker, parameters, streaming=False)
            with generation_slot():
                response = self._http().post(self.base_url + "/v1/tts", json=payload)
            if response.status_code != 200:
                raise RuntimeError(f"Fish API server returned {response.status_code}: {response.text[:300]}")
            wav_bytes = response.content
            sample_rate, duration = _wav_metadata(wav_bytes)
        except Exception as exc:
            logger.error("Fish Audio S2 Pro generation failed: %s", exc)
            return {"success": False, "error": str(exc), "provider": PROVIDER_ID}
        encoded = base64.b64encode(wav_bytes).decode("ascii")
        return {
            "success": True,
            "audio": encoded,
            "audio_base64": encoded,
            "sample_rate": sample_rate,
            "duration": duration,
            "format": "audio/wav",
            "provider": PROVIDER_ID,
            "generation_parameters_used": parameters,
            "generation_strategy_revision": self.generation_strategy_revision,
        }

    def generate_audio_with_progress(self, text: str, speaker: Optional[str] = None,
                                     language: Optional[str] = None, *, progress_callback=None, **kwargs):
        return self.generate_audio(text, speaker=speaker, language=language, **kwargs)

    def generate_audio_stream(
        self, text: str, speaker: Optional[str] = None,
        language: Optional[str] = None, **kwargs,
    ) -> Iterator[tuple[np.ndarray, int, dict]]:
        permit_already_held = bool(kwargs.pop("_device_permit_held", False))
        text = with_direction(text, kwargs.pop("instruct", None))
        if permit_already_held:
            yield from self._stream(text, speaker, kwargs)
            return
        with generation_slot():
            yield from self._stream(text, speaker, kwargs)

    def _stream(self, text: str, speaker: Optional[str], kwargs: dict[str, Any]) -> Iterator[tuple[np.ndarray, int, dict]]:
        # The live lane's sampling knobs are tuned for Qwen; Fish keeps its own.
        parameters = self.resolve_generation_parameters()
        payload = self._request(text, speaker, parameters, streaming=True)
        started = time.perf_counter()
        with self._http().stream("POST", self.base_url + "/v1/tts", json=payload) as response:
            if response.status_code != 200:
                response.read()
                raise RuntimeError(f"Fish API server returned {response.status_code}: {response.text[:300]}")
            header = b""
            remainder = b""
            sample_rate = FISH_SAMPLE_RATE
            first = True
            for chunk in response.iter_bytes():
                if len(header) < _WAV_HEADER_BYTES:
                    needed = _WAV_HEADER_BYTES - len(header)
                    header, chunk = header + chunk[:needed], chunk[needed:]
                    if len(header) < _WAV_HEADER_BYTES:
                        continue
                    sample_rate = int.from_bytes(header[24:28], "little") or FISH_SAMPLE_RATE
                pcm = remainder + chunk
                aligned = len(pcm) - len(pcm) % 2
                remainder = pcm[aligned:]
                if not aligned:
                    continue
                timing: dict[str, Any] = {"transport": "fish_http_stream"}
                if first:
                    timing["provider_entry_to_first_audio_ms"] = (time.perf_counter() - started) * 1000.0
                    first = False
                yield np.frombuffer(pcm[:aligned], dtype="<i2").astype(np.float32) / 32768.0, sample_rate, timing

    def voice_clone(self, voice_id: str, audio_data: bytes, ref_text: Optional[str] = None) -> dict[str, Any]:
        """Save a reference sample (and its transcript) to the Voice Library folder."""
        import json
        import re

        from app.runtime.paths import VOICE_CLONES_DIR

        if not re.match(r"^[a-zA-Z0-9_\-]+$", voice_id):
            return {"success": False, "error": "Invalid voice ID. Use only letters, numbers, hyphens, and underscores."}
        try:
            with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
                if wav_file.getnchannels() not in (1, 2):
                    return {"success": False, "error": "Invalid WAV: must be mono or stereo"}
        except (wave.Error, EOFError) as exc:
            return {"success": False, "error": f"Invalid WAV: {exc}"}
        clone_dir = Path(VOICE_CLONES_DIR)
        clone_dir.mkdir(parents=True, exist_ok=True)
        (clone_dir / f"{voice_id}.wav").write_bytes(audio_data)
        if ref_text and ref_text.strip():
            (clone_dir / f"{voice_id}.json").write_text(json.dumps({
                "schema_version": 1, "voice_id": voice_id, "profile_name": voice_id,
                "audio_path": str(clone_dir / f"{voice_id}.wav"), "ref_text": ref_text.strip(),
                "transcript_source": "manual", "stt_provider": "",
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"success": True, "message": f"Voice clone '{voice_id}' created successfully", "voice_id": voice_id}
