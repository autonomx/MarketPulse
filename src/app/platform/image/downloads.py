"""Download helpers for global image models."""
from __future__ import annotations

from app.config.env import env_str, environment

import json
import os
from typing import Any, Dict, Iterable, List

from app.platform.image.providers.registry import get_image_provider_definition
from app.runtime.paths import MODELS_DIR
from app.settings.access import load_settings, save_settings


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _provider_definition(provider_name: str) -> Dict[str, Any]:
    provider_name = _safe_str(provider_name).strip().lower()
    definition = get_image_provider_definition(provider_name)
    if not definition:
        raise ValueError(f"unsupported_image_provider:{provider_name}")
    return definition


def _append_missing(missing: List[str], value: str) -> None:
    value = _safe_str(value).strip()
    if value and value not in missing:
        missing.append(value)


def _relative_path(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except Exception:
        return os.path.normpath(path).replace("\\", "/")


def _read_json(path: str) -> Dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _candidate_image_model_dirs(
    provider_name: str,
    local_dir: str,
    download_dir: str,
) -> List[str]:
    definition = _provider_definition(provider_name)
    download_dir = _safe_str(download_dir).strip() or "image"
    root = download_dir if os.path.isabs(download_dir) else os.path.join(MODELS_DIR, download_dir)
    candidates: List[str] = []

    configured = _safe_str(local_dir).strip()
    if configured:
        candidates.append(os.path.normpath(configured))

    preferred_name = _safe_str(definition.get("local_dir_name")).strip()
    if preferred_name:
        candidates.append(os.path.normpath(os.path.join(root, preferred_name)))

    for legacy_name in definition.get("legacy_local_dir_names") or []:
        legacy_name = _safe_str(legacy_name).strip()
        if legacy_name:
            candidates.append(os.path.normpath(os.path.join(root, legacy_name)))

    unique: List[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def normalize_image_model_local_dir(
    provider_name: str,
    local_dir: str,
    download_dir: str,
) -> str:
    candidates = _candidate_image_model_dirs(provider_name, local_dir, download_dir)
    if not candidates:
        raise ValueError(f"image_model_local_dir_unresolved:{provider_name}")

    # Prefer a directory that already contains a real model snapshot. This lets
    # the canonical resources/models/image/<model> folder win over stale paths
    # left in settings by earlier builds.
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "model_index.json")):
            return candidate

    # Preserve partial downloads so Hugging Face can resume them.
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return candidates[0]


def resolve_image_local_dir_from_settings(settings: Dict[str, Any], provider_name: str) -> str:
    provider_name = _safe_str(provider_name).strip().lower()
    settings = _safe_dict(settings)
    image_cfg = _safe_dict(settings.get("image"))
    provider_cfg = _safe_dict(image_cfg.get(provider_name))
    primary = normalize_image_model_local_dir(
        provider_name,
        _safe_str(provider_cfg.get("local_dir")),
        _safe_str(provider_cfg.get("download_dir")),
    )

    if provider_name != "flux_klein":
        return primary

    # Older installs stored the FLUX path only under rpg_visual. Retain that as
    # a fallback, while still preferring the canonical downloaded directory.
    rpg_visual = _safe_dict(settings.get("rpg_visual"))
    rpg_flux = _safe_dict(rpg_visual.get("flux_klein"))
    legacy = normalize_image_model_local_dir(
        provider_name,
        _safe_str(rpg_flux.get("local_dir")),
        _safe_str(rpg_flux.get("download_dir")),
    )
    if os.path.isfile(os.path.join(primary, "model_index.json")):
        return primary
    if os.path.isfile(os.path.join(legacy, "model_index.json")):
        return legacy
    return primary


def required_image_model_files(provider_name: str) -> List[str]:
    definition = _provider_definition(provider_name)
    required = ["model_index.json"]
    for path in definition.get("required_paths") or []:
        normalized = _safe_str(path).strip()
        if normalized and normalized not in required:
            required.append(normalized)
    return required


def _validate_model_components(local_dir: str, missing: List[str]) -> None:
    model_index_path = os.path.join(local_dir, "model_index.json")
    if not os.path.isfile(model_index_path):
        return
    model_index = _read_json(model_index_path)
    if model_index is None:
        _append_missing(missing, "model_index.json:invalid")
        return

    for component, descriptor in model_index.items():
        if _safe_str(component).startswith("_"):
            continue
        if not isinstance(descriptor, (list, tuple)) or len(descriptor) < 2:
            continue
        class_name = descriptor[1]
        if not _safe_str(class_name).strip():
            continue
        component_path = os.path.join(local_dir, component)
        if not os.path.exists(component_path):
            _append_missing(missing, f"component:{component}")


def _weight_index_files(local_dir: str) -> Iterable[str]:
    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [name for name in dirs if name != ".cache"]
        for name in files:
            if name.endswith((".safetensors.index.json", ".bin.index.json")):
                yield os.path.join(root, name)


def _validate_weight_shards(local_dir: str, missing: List[str]) -> None:
    for index_path in _weight_index_files(local_dir):
        index_data = _read_json(index_path)
        if index_data is None:
            _append_missing(missing, f"{_relative_path(local_dir, index_path)}:invalid")
            continue
        weight_map = index_data.get("weight_map")
        if not isinstance(weight_map, dict):
            _append_missing(missing, f"{_relative_path(local_dir, index_path)}:weight_map")
            continue
        index_dir = os.path.dirname(index_path)
        for shard in sorted({_safe_str(value).strip() for value in weight_map.values()}):
            if not shard:
                continue
            candidates = [os.path.join(index_dir, shard), os.path.join(local_dir, shard)]
            if not any(os.path.isfile(candidate) for candidate in candidates):
                _append_missing(missing, _relative_path(local_dir, candidates[0]))


def _validate_no_incomplete_downloads(local_dir: str, missing: List[str]) -> None:
    for root, _dirs, files in os.walk(local_dir):
        for name in files:
            if name.endswith(".incomplete"):
                _append_missing(missing, f"incomplete:{_relative_path(local_dir, os.path.join(root, name))}")


def get_image_local_model_status(provider_name: str, local_dir: str = "") -> Dict[str, Any]:
    provider_name = _safe_str(provider_name).strip().lower()
    definition = _provider_definition(provider_name)
    raw_local_dir = _safe_str(local_dir).strip()
    local_dir = os.path.normpath(raw_local_dir) if raw_local_dir else ""
    if not local_dir:
        local_dir = resolve_image_local_dir_from_settings(load_settings(), provider_name)

    if not definition.get("supports_local_model"):
        return {
            "ok": True,
            "exists": True,
            "complete": True,
            "missing": [],
            "local_dir": "",
            "provider": provider_name,
            "repo_id": "",
        }

    exists = os.path.isdir(local_dir)
    missing: List[str] = []
    if exists:
        for rel in required_image_model_files(provider_name):
            if not os.path.exists(os.path.join(local_dir, rel)):
                _append_missing(missing, rel)
        _validate_model_components(local_dir, missing)
        _validate_weight_shards(local_dir, missing)
        _validate_no_incomplete_downloads(local_dir, missing)
    else:
        for rel in required_image_model_files(provider_name):
            _append_missing(missing, rel)

    has_weights = False
    if exists:
        for _root, dirs, files in os.walk(local_dir):
            dirs[:] = [name for name in dirs if name != ".cache"]
            if any(name.endswith((".safetensors", ".bin")) for name in files):
                has_weights = True
                break
    if not has_weights:
        _append_missing(missing, "*.safetensors")

    complete = exists and not missing
    return {
        "ok": complete,
        "exists": exists,
        "complete": complete,
        "missing": missing,
        "local_dir": local_dir,
        "provider": provider_name,
        "repo_id": _safe_str(definition.get("repo_id")),
        "gated": bool(definition.get("gated")),
        "license": _safe_str(definition.get("license")),
    }


def download_image_model(provider_name: str, hf_token: str = "") -> Dict[str, Any]:
    provider_name = _safe_str(provider_name).strip().lower()
    definition = _provider_definition(provider_name)
    if not definition.get("supports_download"):
        return {"ok": False, "provider": provider_name, "error": "image_model_download_not_supported"}

    settings = load_settings()
    image_cfg = _safe_dict(settings.get("image"))
    provider_cfg = _safe_dict(image_cfg.get(provider_name))
    repo_id = _safe_str(provider_cfg.get("repo_id")).strip() or _safe_str(definition.get("repo_id"))
    local_dir = resolve_image_local_dir_from_settings(settings, provider_name)
    os.makedirs(local_dir, exist_ok=True)

    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        return {"ok": False, "provider": provider_name, "error": f"huggingface_hub_missing:{exc}"}

    # A UI-supplied token is scoped to this call only. It is never persisted in
    # settings; the launcher-level HF_TOKEN remains the fallback for headless use.
    token = _safe_str(hf_token).strip() or environment().get("HF_TOKEN", "").strip() or None
    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=local_dir,
            token=token,
        )
    except Exception as exc:
        hint = ""
        if definition.get("gated"):
            hint = ": accept the model license on Hugging Face and provide a valid token"
        return {
            "ok": False,
            "provider": provider_name,
            "error": f"{provider_name}_download_failed:{exc}{hint}",
            "repo_id": repo_id,
            "local_dir": local_dir,
            "gated": bool(definition.get("gated")),
        }

    local_status = get_image_local_model_status(provider_name, local_dir)
    if not local_status.get("complete"):
        return {
            "ok": False,
            "provider": provider_name,
            "error": f"{provider_name}_download_incomplete",
            "repo_id": repo_id,
            "local_dir": local_dir,
            "local_status": local_status,
            "loaded": False,
        }

    provider_cfg = dict(provider_cfg)
    provider_cfg.update({
        "repo_id": repo_id,
        "local_dir": local_dir,
        "download_dir": "image",
        "prefer_local_files": True,
        "allow_repo_fallback": False,
    })
    image_cfg[provider_name] = provider_cfg
    settings["image"] = image_cfg

    # Keep the legacy RPG FLUX config in sync without changing the active model.
    if provider_name == "flux_klein":
        rpg_visual = _safe_dict(settings.get("rpg_visual"))
        rpg_flux = _safe_dict(rpg_visual.get("flux_klein"))
        rpg_flux.update({
            "local_dir": local_dir,
            "download_dir": "image",
            "prefer_local_files": True,
            "allow_repo_fallback": False,
        })
        rpg_visual["flux_klein"] = rpg_flux
        if not _safe_str(rpg_visual.get("provider")).strip():
            rpg_visual["provider"] = "flux_klein"
        settings["rpg_visual"] = rpg_visual

    save_settings(settings)
    return {
        "ok": True,
        "provider": provider_name,
        "repo_id": repo_id,
        "local_dir": local_dir,
        "local_status": local_status,
        "loaded": False,
        "downloaded_via": "/api/image-generation/model/download",
    }


# Backward-compatible FLUX helpers used by existing routes and tests.
def normalize_flux_local_dir(local_dir: str, download_dir: str) -> str:
    return normalize_image_model_local_dir("flux_klein", local_dir, download_dir)


def resolve_flux_local_dir_from_settings(settings: Dict[str, Any]) -> str:
    return resolve_image_local_dir_from_settings(settings, "flux_klein")


def required_flux_files() -> List[str]:
    return required_image_model_files("flux_klein")


def get_flux_local_model_status(local_dir: str) -> Dict[str, Any]:
    return get_image_local_model_status("flux_klein", local_dir)


def download_flux_klein_model() -> Dict[str, Any]:
    return download_image_model("flux_klein")
