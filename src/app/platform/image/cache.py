"""Deterministic image generation cache."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from typing import Any, Dict

from app.runtime.paths import generated_images_root

CACHE_DIR = str(generated_images_root() / "_cache")
INDEX_PATH = os.path.join(CACHE_DIR, "index.json")


def _read_index() -> Dict[str, Any]:
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_index(index: Dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)
    os.replace(tmp, INDEX_PATH)


def image_cache_key(payload: Dict[str, Any]) -> str:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    stable = {
        "provider": payload.get("provider"),
        "prompt": payload.get("prompt"),
        "negative_prompt": payload.get("negative_prompt"),
        "seed": payload.get("seed"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "steps": payload.get("steps") or payload.get("num_inference_steps"),
        "guidance_scale": payload.get("guidance_scale"),
        "style": payload.get("style"),
        "model": payload.get("model") or metadata.get("model"),
        "quality": payload.get("quality") or metadata.get("quality"),
    }
    raw = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def lookup_image_cache(cache_key: str) -> Dict[str, Any] | None:
    index = _read_index()
    row = index.get(cache_key)
    if not isinstance(row, dict):
        return None
    file_path = row.get("file_path")
    if not file_path or not os.path.exists(file_path):
        return None
    return dict(row)


def store_image_cache(cache_key: str, result: Any) -> Dict[str, Any]:
    file_path = getattr(result, "file_path", "") or ""
    if not file_path or not os.path.exists(file_path):
        return {}

    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_filename = f"{cache_key}.png"
    cache_path = os.path.join(CACHE_DIR, cache_filename)

    if os.path.abspath(file_path) != os.path.abspath(cache_path):
        shutil.copyfile(file_path, cache_path)

    row = {
        "cache_key": cache_key,
        "file_path": cache_path,
        "asset_url": f"/generated-images/_cache/{cache_filename}",
        "width": getattr(result, "width", 0),
        "height": getattr(result, "height", 0),
        "seed": getattr(result, "seed", None),
        "mime_type": getattr(result, "mime_type", "image/png") or "image/png",
        "provider": getattr(result, "provider", ""),
    }

    index = _read_index()
    index[cache_key] = row
    _write_index(index)
    return row
