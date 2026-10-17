"""Where an asset's bytes live, behind one interface (WP-5.8).

Blob-backed assets (``storage_key`` set) are read through the configured
BlobStore, local filesystem or S3. File-discovered and legacy assets carry
only a local ``storage_path``. Feature code reads through these helpers
instead of opening ``storage_path``, so it works the same on every host and
with remote blob storage.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import threading
from typing import Any, BinaryIO

from app.runtime.paths import resources_data_root

_MATERIALIZE_LOCK = threading.Lock()


class AssetContentUnavailable(FileNotFoundError):
    pass


def default_blob_store() -> Any:
    # Imported lazily: app.persistence imports app.assets (models), so a
    # module-level import here would create an import-time package cycle.
    from app.persistence.blob_store import default_blob_store as configured_store

    return configured_store()


def _blob_key(asset: Any) -> str | None:
    key = getattr(asset, "storage_key", None)
    return str(key) if key else None


def _local_path(asset: Any) -> Path | None:
    value = str(getattr(asset, "storage_path", "") or "").strip()
    return Path(value) if value else None


def asset_available(asset: Any) -> bool:
    """True when the asset has non-empty content that can be read right now."""
    key = _blob_key(asset)
    if key is not None:
        return bool(default_blob_store().exists(key))
    path = _local_path(asset)
    return bool(path and path.is_file() and path.stat().st_size > 0)


def local_asset_path(asset: Any) -> Path | None:
    """The asset's local file path, whether or not it currently exists.

    For file-managed assets (legacy voice clones) that need to clean up
    sibling files. Remote blobs have no local path and return None.
    """
    key = _blob_key(asset)
    if key is not None:
        local_path = getattr(default_blob_store(), "local_path", None)
        return Path(local_path(key)) if callable(local_path) else None
    return _local_path(asset)


def open_asset(asset: Any) -> BinaryIO:
    """Open the content for streaming reads; verified when a checksum is known."""
    key = _blob_key(asset)
    if key is not None:
        store = default_blob_store()
        checksum = getattr(asset, "checksum_sha256", None)
        try:
            if checksum:
                return store.open_verified(key, expected_checksum=str(checksum))
            return store.open(key)
        except FileNotFoundError as exc:
            raise AssetContentUnavailable(str(getattr(asset, "id", key))) from exc
    path = _local_path(asset)
    if path is None or not path.is_file():
        raise AssetContentUnavailable(str(getattr(asset, "id", "")))
    return path.open("rb")


def read_asset_bytes(asset: Any, *, max_bytes: int | None = None) -> bytes:
    """Read the whole content, refusing anything larger than ``max_bytes``."""
    with open_asset(asset) as handle:
        if max_bytes is None:
            return handle.read()
        content = handle.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError(f"asset {getattr(asset, 'id', '')} exceeds {max_bytes} bytes")
    return content


def asset_checksum(asset: Any) -> str:
    """SHA-256 of the content (recorded for blobs, computed for local files)."""
    recorded = getattr(asset, "checksum_sha256", None)
    if recorded:
        return str(recorded)
    digest = hashlib.sha256()
    with open_asset(asset) as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def materialize_asset(asset: Any) -> Path:
    """A local file path with the asset's content, for tools that need a path.

    Local blobs and file-discovered assets return their own file. Remote
    blobs are downloaded once into a content-addressed, checksum-verified
    cache, so the path stays valid and identical content is shared.
    """
    key = _blob_key(asset)
    if key is None:
        path = _local_path(asset)
        if path is None or not path.is_file():
            raise AssetContentUnavailable(str(getattr(asset, "id", "")))
        return path
    store = default_blob_store()
    local_path = getattr(store, "local_path", None)
    if callable(local_path):
        path = Path(local_path(key))
        if not path.is_file():
            raise AssetContentUnavailable(str(getattr(asset, "id", key)))
        return path
    checksum = getattr(asset, "checksum_sha256", None)
    if not checksum:
        raise AssetContentUnavailable(f"remote asset {getattr(asset, 'id', key)} has no checksum")
    target = resources_data_root() / "cache" / "blobs" / str(checksum)[:2] / f"{checksum}{Path(key).suffix}"
    with _MATERIALIZE_LOCK:
        if not target.is_file():
            try:
                store.copy_verified_to(key, target, expected_checksum=str(checksum))
            except FileNotFoundError as exc:
                raise AssetContentUnavailable(str(getattr(asset, "id", key))) from exc
    return target


def delete_asset_content(asset: Any) -> bool:
    """Remove the content; True when something was deleted."""
    key = _blob_key(asset)
    if key is not None:
        return bool(default_blob_store().delete(key))
    path = _local_path(asset)
    if path is None or not path.is_file():
        return False
    path.unlink()
    return True


def asset_location(asset: Any) -> str:
    """Reference for listings: the local path when there is one (unchanged
    for local deployments), otherwise ``blob:<key>`` for remote blobs."""
    path = _local_path(asset)
    if path is not None:
        return str(path)
    key = _blob_key(asset)
    return f"blob:{key}" if key is not None else ""


__all__ = [
    "AssetContentUnavailable",
    "asset_available",
    "asset_checksum",
    "asset_location",
    "delete_asset_content",
    "local_asset_path",
    "materialize_asset",
    "open_asset",
    "read_asset_bytes",
]
