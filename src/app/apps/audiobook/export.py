"""Frozen export planning and lossless book assembly."""
from __future__ import annotations

from app.config.env import env_str, environment

import io
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Sequence

from .hashing import object_hash


FORMAT_MIME = {
    "m4b": "audio/mp4", "flac": "audio/flac",
    "wav": "audio/wav", "mp3": "audio/mpeg",
}


def ffmpeg_binary() -> str:
    configured = environment().get("OMNIX_FFMPEG", "").strip()
    executable = configured or shutil.which("ffmpeg")
    if not executable:
        # The repository's Windows development environment may carry the
        # imageio-ffmpeg binary without adding it to the launcher PATH.
        # Prefer that local, pinned binary before reporting the prerequisite
        # as unavailable. An explicit OMNIX_FFMPEG value and PATH still win.
        root = Path(__file__).resolve().parents[4]
        bundled = sorted(
            (root / "venv" / "Lib" / "site-packages" / "imageio_ffmpeg" / "binaries").glob(
                "ffmpeg*.exe"
            )
        )
        if bundled:
            executable = str(bundled[-1])
    if not executable or not Path(executable).is_file():
        raise RuntimeError("FFmpeg is required for audiobook export")
    return str(executable)


def ffmpeg_version(executable: str) -> str:
    result = subprocess.run(
        [executable, "-version"], capture_output=True, text=True,
        check=True, timeout=15,
    )
    return result.stdout.splitlines()[0].strip()


def freeze_manifest(*, project: dict[str, Any], source: dict[str, Any],
                    chapters: Sequence[dict[str, Any]], format: str,
                    encoder_version: str, settings: dict[str, Any]) -> dict[str, Any]:
    if format not in FORMAT_MIME:
        raise ValueError("unsupported audiobook export format")
    if not chapters:
        raise ValueError("export requires assembled chapters")
    return {
        "manifest_version": "audiobook-export-v1",
        "project_id": project["id"], "source_revision_id": source["id"],
        "render_run_id": project.get("render_run_id"),
        "source_canonical_hash": source["canonical_hash"],
        "title": project["title"], "author": project["author"],
        "language": project["language"], "cover": project.get("cover"),
        "cast": project.get("cast", []),
        "chapters": list(chapters), "format": format,
        "encoder": {"name": "ffmpeg", "version": encoder_version,
                    "settings": settings},
    }


def manifest_hash(manifest: dict[str, Any]) -> str:
    return object_hash(manifest)


def concatenate_chapters(chapter_audio: Sequence[bytes]) -> bytes:
    if not chapter_audio:
        raise ValueError("book has no chapter audio")
    sample_rate: int | None = None
    frames: list[bytes] = []
    for content in chapter_audio:
        with wave.open(io.BytesIO(content), "rb") as reader:
            if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getcomptype() != "NONE":
                raise ValueError("chapter audio must be mono PCM16 WAV")
            if sample_rate is None:
                sample_rate = reader.getframerate()
            elif sample_rate != reader.getframerate():
                raise ValueError("chapter sample rates differ")
            data = reader.readframes(reader.getnframes())
            if not data:
                raise ValueError("chapter audio is empty")
            frames.append(data)
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        for data in frames:
            writer.writeframes(data)
    return output.getvalue()


def _metadata_value(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\\n").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")


def ffmetadata(manifest: dict[str, Any]) -> str:
    lines = [";FFMETADATA1"]
    for key in ("title", "author", "language"):
        value = manifest.get(key)
        if value:
            lines.append(f"{key if key != 'author' else 'artist'}={_metadata_value(value)}")
    cast = manifest.get("cast") or []
    if cast:
        lines.append(f"narrator={_metadata_value(', '.join(str(item['name']) for item in cast))}")
    elapsed_ms = 0
    for chapter in manifest["chapters"]:
        duration_ms = round(float(chapter["duration_seconds"]) * 1000)
        lines.extend(("[CHAPTER]", "TIMEBASE=1/1000", f"START={elapsed_ms}",
                      f"END={elapsed_ms + duration_ms}",
                      f"title={_metadata_value(chapter['title'])}"))
        elapsed_ms += duration_ms
    return "\n".join(lines) + "\n"


def ffmpeg_command(executable: str, *, input_wav: str, metadata_path: str,
                   output_path: str, format: str, cover_path: str | None = None,
                   concat_input: bool = False, progress_path: str | None = None) -> list[str]:
    if format not in FORMAT_MIME:
        raise ValueError("unsupported audiobook export format")
    command = [executable, "-hide_banner", "-nostdin", "-y"]
    if progress_path:
        # Reporting only: FFmpeg appends key=value progress blocks; the encoding is unchanged.
        command.extend(("-progress", progress_path, "-nostats"))
    if concat_input:
        command.extend(("-f", "concat", "-safe", "1"))
    command.extend(("-i", input_wav, "-f", "ffmetadata", "-i", metadata_path))
    if cover_path and format == "m4b":
        command.extend(("-i", cover_path))
    command.extend(("-map", "0:a:0", "-map_metadata", "1", "-map_chapters", "1"))
    if cover_path and format == "m4b":
        command.extend(("-map", "2:v:0", "-c:v", "mjpeg", "-disposition:v", "attached_pic"))
    if format == "m4b":
        command.extend(("-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "mp4"))
    elif format == "flac":
        command.extend(("-c:a", "flac", "-compression_level", "5", "-f", "flac"))
    elif format == "mp3":
        command.extend(("-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3"))
    else:
        command.extend(("-c:a", "pcm_s16le", "-rf64", "auto", "-f", "wav"))
    return [*command, output_path]


def encoded_seconds(progress_report: str) -> float | None:
    """How much audio FFmpeg has encoded, from its ``-progress`` output (latest block wins)."""
    for line in reversed(progress_report.splitlines()):
        key, _, value = line.strip().partition("=")
        # ``out_time_ms`` is microseconds too (a long-standing FFmpeg misnomer).
        if key in {"out_time_us", "out_time_ms"}:
            try:
                return max(0.0, int(value) / 1_000_000)
            except ValueError:
                continue  # "N/A" before the first frame
    return None
