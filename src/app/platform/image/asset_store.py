"""Global image asset store (IMG-4)."""
from __future__ import annotations

import logging

import hashlib
import json
import os
import re
from typing import Any, Dict

from app.runtime.paths import generated_images_root

logger = logging.getLogger(__name__)

ASSET_DIR = str(generated_images_root())
MANIFEST_PATH = os.path.join(ASSET_DIR, "manifest.json")


def _ensure_dirs():
    os.makedirs(ASSET_DIR, exist_ok=True)


def _load_manifest() -> Dict[str, Any]:
    if not os.path.isfile(MANIFEST_PATH):
        return {"assets": {}}
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"assets": {}}


def _save_manifest(data: Dict[str, Any]):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _safe_asset_filename_component(value: str) -> str:
    """
    Convert an arbitrary asset id into a Windows-safe filename component.

    Important:
    - keep the original asset_id unchanged in the manifest key
    - only sanitize the on-disk filename
    """
    text = str(value or "").strip()
    if not text:
        return "asset"

    # Windows-invalid filename chars: <>:"/\\|?*
    text = re.sub(r'[<>:"/\\\\|?*]+', "_", text)
    # Collapse whitespace / repeated separators a bit
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._ ")

    return text or "asset"


def save_image_asset_bytes(image_bytes: bytes, mime_type: str, asset_id: str, metadata: Dict[str, Any]):
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.image.persistence.image_assets import save_image_asset_bytes_postgres
        return save_image_asset_bytes_postgres(image_bytes, mime_type, asset_id, metadata)
    _ensure_dirs()
    manifest = _load_manifest()

    content_hash = _hash_bytes(image_bytes)
    safe_asset_id = _safe_asset_filename_component(asset_id)
    filename = f"{safe_asset_id}_{content_hash}.png"
    path = os.path.join(ASSET_DIR, filename)

    if not os.path.isfile(path):
        with open(path, "wb") as f:
            f.write(image_bytes)

    manifest["assets"][asset_id] = {
        "path": path,
        "mime_type": mime_type,
        "hash": content_hash,
        "metadata": metadata,
    }

    _save_manifest(manifest)
    return path


def register_image_asset_file(file_path: str, asset_id: str, metadata: Dict[str, Any]):
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.image.persistence.image_assets import register_image_asset_file_postgres
        return register_image_asset_file_postgres(file_path, asset_id, metadata)
    _ensure_dirs()
    manifest = _load_manifest()

    manifest["assets"][asset_id] = {
        "path": file_path,
        "mime_type": "image/png",
        "hash": "",
        "metadata": metadata,
    }

    _save_manifest(manifest)
    return file_path


def get_image_asset_manifest():
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.image.persistence.image_assets import get_image_asset_manifest_postgres
        return get_image_asset_manifest_postgres()
    return _load_manifest()


def delete_image_asset(asset_id: str, *, delete_file: bool = True) -> Dict[str, Any]:
    """Delete a legacy image manifest entry and, by default, its file."""
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.image.persistence.image_assets import delete_image_asset_postgres
        return delete_image_asset_postgres(asset_id, delete_file=delete_file)

    manifest = _load_manifest()
    assets = manifest.setdefault("assets", {})
    payload = assets.pop(str(asset_id), None)
    if payload is None:
        return {
            "ok": False,
            "asset_id": str(asset_id),
            "deleted": False,
            "file_deleted": False,
        }

    _save_manifest(manifest)
    path = str((payload or {}).get("path") or "").strip()
    file_deleted = False
    file_error = ""
    if delete_file and path and os.path.isfile(path):
        try:
            os.remove(path)
            file_deleted = True
        except OSError as exc:
            file_error = str(exc)

    result: Dict[str, Any] = {
        "ok": True,
        "asset_id": str(asset_id),
        "deleted": True,
        "file_deleted": file_deleted,
    }
    if file_error:
        result["file_error"] = file_error
    return result


def cleanup_unused_image_assets():
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.image.persistence.image_assets import cleanup_unused_image_assets_postgres
        return cleanup_unused_image_assets_postgres()
    manifest = _load_manifest()
    existing = set()

    for v in manifest["assets"].values():
        existing.add(v.get("path"))

    for fname in os.listdir(ASSET_DIR):
        full = os.path.join(ASSET_DIR, fname)
        if full not in existing and fname != "manifest.json":
            try:
                os.remove(full)
            except Exception:
                logger.debug("suppressed error in %s", "cleanup_unused_image_assets", exc_info=True)

    return {"ok": True}
