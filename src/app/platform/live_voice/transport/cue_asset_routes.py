"""Voice-matched live cue pack discovery and browser-safe delivery."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.runtime.paths import resources_data_root

_ROUTE_SENTINEL = "_omnix_live_voice_cue_assets_registered"
LIVE_VOICE_CUE_MANIFEST_PATH = "/api/voice/cues/{voice_id}/manifest"
LIVE_VOICE_CUE_FILE_PATH = "/api/voice/cues/{voice_id}/{cue_id}/{variant_id}.wav"
CUE_SCHEMA_VERSION = 1
SUPPORTED_CUES = {"mhm", "hmm", "inhale", "amused_exhale"}
SAFE_VOICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,119}$")
SAFE_VARIANT_ID = re.compile(r"^(mhm|hmm|inhale|amused_exhale)-v([1-9][0-9]?)$")
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


class LiveVoiceCueAsset(BaseModel):
    cue_id: Literal["mhm", "hmm", "inhale", "amused_exhale"]
    variant_id: str
    url: str
    sample_rate: int | None = Field(default=None, ge=8_000, le=192_000)
    size_bytes: int = Field(ge=1)
    sha256: str


class LiveVoiceCueManifest(BaseModel):
    schema_version: Literal[1] = CUE_SCHEMA_VERSION
    voice_id: str
    available: bool
    assets: list[LiveVoiceCueAsset] = Field(default_factory=list)


def live_voice_cue_root() -> Path:
    override = str(_env_str("OMNIX_LIVE_VOICE_CUE_ROOT") or "").strip()
    root = Path(override) if override else resources_data_root() / "voice_cues"
    root.mkdir(parents=True, exist_ok=True)
    return root


def register_live_voice_cue_asset_routes(router: APIRouter, state: Any) -> None:
    """Register cue-pack routes without exposing local filesystem paths."""
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get(
        LIVE_VOICE_CUE_MANIFEST_PATH,
        response_model=LiveVoiceCueManifest,
    )
    def live_voice_cue_manifest(voice_id: str) -> LiveVoiceCueManifest:
        voice_dir = _voice_directory(voice_id)
        assets: list[LiveVoiceCueAsset] = []
        if voice_dir.is_dir():
            for path in sorted(voice_dir.glob("*.wav")):
                parsed = _parse_variant(path.stem)
                if parsed is None:
                    continue
                cue_id, variant_id = parsed
                try:
                    stat_result = path.stat()
                    digest = _sha256_file(path)
                except OSError:
                    continue
                assets.append(
                    LiveVoiceCueAsset(
                        cue_id=cue_id,
                        variant_id=variant_id,
                        url=(
                            f"/api/voice/cues/{quote(voice_id, safe='')}/"
                            f"{cue_id}/{variant_id}.wav"
                        ),
                        sample_rate=_wav_sample_rate(path),
                        size_bytes=stat_result.st_size,
                        sha256=digest,
                    )
                )
        return LiveVoiceCueManifest(
            voice_id=voice_id.strip(),
            available=bool(assets),
            assets=assets,
        )

    @router.get(
        LIVE_VOICE_CUE_FILE_PATH,
        response_model=None,
        response_class=FileResponse,
        responses={
            200: {
                "description": "WAV audio asset bytes.",
                "content": {
                    "audio/wav": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            },
            304: {"description": "The cached cue asset is current."},
        },
    )
    def live_voice_cue_file(
        voice_id: str,
        cue_id: str,
        variant_id: str,
        request: Request,
    ) -> Response:
        parsed = _parse_variant(variant_id)
        if cue_id not in SUPPORTED_CUES or parsed is None or parsed[0] != cue_id:
            raise HTTPException(status_code=404, detail="voice_cue_not_found")
        path = _voice_directory(voice_id) / f"{variant_id}.wav"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="voice_cue_not_found")
        try:
            stat_result = path.stat()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="voice_cue_not_found") from exc
        etag = f'"{_sha256_file(path)}"'
        headers = {
            "Cache-Control": IMMUTABLE_CACHE_CONTROL,
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=path.name,
            content_disposition_type="inline",
            headers=headers,
            stat_result=stat_result,
        )


def _voice_directory(voice_id: str) -> Path:
    normalized = voice_id.strip()
    if not SAFE_VOICE_ID.fullmatch(normalized) or normalized in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid_voice_id")
    root = live_voice_cue_root().resolve()
    candidate = (root / normalized).resolve()
    if candidate.parent != root:
        raise HTTPException(status_code=400, detail="invalid_voice_id")
    return candidate


def _parse_variant(value: str) -> tuple[str, str] | None:
    match = SAFE_VARIANT_ID.fullmatch(value)
    if match is None:
        return None
    cue_id = match.group(1)
    return cue_id, value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wav_sample_rate(path: Path) -> int | None:
    try:
        import wave

        with wave.open(str(path), "rb") as handle:
            return int(handle.getframerate())
    except (OSError, EOFError, wave.Error):
        return None
