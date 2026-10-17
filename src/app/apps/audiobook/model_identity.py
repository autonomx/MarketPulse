"""Bind audiobook render identities to the installed, local TTS artifacts."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from app.caching.bounded_cache import bounded_lru_cache

if TYPE_CHECKING:
    from app.providers.tts_artifacts import LocalModelArtifacts


# ``.pth``: Fish S2 Pro's codec weights.
_MODEL_SUFFIXES = {".safetensors", ".json", ".txt", ".model", ".pth"}


class ModelIdentityError(ValueError):
    retryable = False


def _local_artifacts(provider_id: str) -> LocalModelArtifacts:
    # Imported on demand: the providers package loads every LLM provider, and
    # audiobook route registration should stay lightweight.
    from app.providers.tts_artifacts import LocalArtifactsUnavailable, local_model_artifacts

    try:
        return local_model_artifacts(provider_id)
    except LocalArtifactsUnavailable as exc:
        raise ModelIdentityError(str(exc)) from exc


@bounded_lru_cache(max_entries=4, ttl_seconds=3600.0)
def _fingerprint(filenames: tuple[tuple[str, int, int], ...], directory: str) -> str:
    digest = hashlib.sha256()
    root = Path(directory)
    for relative, size, modified_ns in filenames:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        stat = path.stat()
        if stat.st_size != size or stat.st_mtime_ns != modified_ns:
            raise ModelIdentityError("TTS model changed while its revision was being measured")
    return f"sha256:{digest.hexdigest()}"


def current_model_identity(provider_id: str | None = None) -> dict[str, object]:
    """The installed model of ``provider_id``, by default Omnix's selected TTS engine."""
    if provider_id is None:
        from app.providers.service import selected_tts_provider_id

        provider_id = selected_tts_provider_id()
    artifacts = _local_artifacts(provider_id)
    directory = artifacts.directory
    files = sorted(
        (path for path in directory.rglob("*")
         if path.is_file() and path.suffix.lower() in _MODEL_SUFFIXES),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    fingerprints = tuple(
        (path.relative_to(directory).as_posix(), path.stat().st_size,
         path.stat().st_mtime_ns) for path in files
    )
    if not fingerprints:
        raise ModelIdentityError(f"the configured {provider_id} model has no artifacts")
    return {"provider_id": provider_id, "model_id": artifacts.model_id,
            "model_revision": _fingerprint(fingerprints, str(directory)),
            "artifact_count": len(fingerprints)}


def assert_model_revision(provider_id: str, model_id: str, expected: str) -> None:
    identity = current_model_identity(provider_id)
    if identity["model_id"] != model_id or identity["model_revision"] != expected:
        raise ModelIdentityError("audiobook model revision does not match installed artifacts")
