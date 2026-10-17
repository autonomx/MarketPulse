"""
Vendored Qwen3-TTS model loader with caching.

This is the only module that imports directly from the vendored TTS package.
All other code should use this loader interface.
"""
from app.config.env import env_str as _env_str
# Fix PyTorch DLL loading hang on Windows (PyTorch 2.9+)
import os
import re
import platform

if platform.system() == "Windows":
    import ctypes
    from importlib.util import find_spec
    try:
        if (spec := find_spec("torch")) and spec.origin:
            torch_lib_dir = os.path.join(os.path.dirname(spec.origin), "lib")
            c10_dll = os.path.join(torch_lib_dir, "c10.dll")
            if os.path.exists(c10_dll):
                ctypes.CDLL(os.path.normpath(c10_dll))
    except Exception:
        pass

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .bootstrap import ensure_vendored_qwen3_tts_available
from .runtime_status import validate_qwen3_tts_runtime

logger = logging.getLogger(__name__)
# A Hugging Face repository id: one "owner/name" pair.
_REPO_ID = re.compile(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*")

# Global model cache
_model_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()


LEGACY_BROKEN_LOCAL_MODEL_DEFAULTS = {
    r"F:\LLM\omnix\Qwen\Qwen3-TTS-12Hz-0.6B-Base",
    r".\Qwen\Qwen3-TTS-12Hz-0.6B-Base",
    r"Qwen\Qwen3-TTS-12Hz-0.6B-Base",
}


def _looks_like_local_model_path(model_name: str) -> bool:
    value = str(model_name or "").strip()
    if not value:
        return False
    if os.path.isabs(value):
        return True
    if value.startswith(".") or value.startswith(".."):
        return True
    if "\\" in value:
        return True
    if "/" in value:
        # "org/name" is a Hugging Face repository id unless such a directory exists.
        if _REPO_ID.fullmatch(value) and not Path(value).expanduser().exists():
            return False
        return True
    return False


def _find_env_model_override(model_name: str) -> Optional[Path]:
    """
    Allow explicit local override so the app can pin a known-good downloaded model snapshot.

    Supported env vars:
      - OMNIX_TTS_MODEL_DIR
      - OMNIX_QWEN3_TTS_MODEL_DIR
    """
    candidates = [
        _env_str("OMNIX_TTS_MODEL_DIR", "").strip(),
        _env_str("OMNIX_QWEN3_TTS_MODEL_DIR", "").strip(),
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if path.exists() and path.is_dir():
            logger.info("Using env-overridden Qwen3-TTS model dir for %s: %s", model_name, path)
            return path
    return None


def _find_cached_snapshot_dir(model_name: str) -> Optional[Path]:
    """
    Resolve a HuggingFace repo id to a local cache snapshot when available.
    We prefer a local snapshot so we can validate files before calling transformers.
    """
    model_name = str(model_name or "").strip()
    if not model_name or _looks_like_local_model_path(model_name):
        return None

    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        logger.info("huggingface_hub unavailable while resolving cached snapshot for %s: %r", model_name, exc)
        return None

    try:
        snapshot_dir = snapshot_download(
            repo_id=model_name,
            local_files_only=True,
        )
    except Exception as exc:
        logger.info("No local HuggingFace snapshot available yet for %s: %r", model_name, exc)
        return None

    path = Path(snapshot_dir).expanduser().resolve()
    if path.exists() and path.is_dir():
        logger.info("Resolved cached local snapshot for %s -> %s", model_name, path)
        return path
    return None


def _resolve_model_source(model_name: str) -> str:
    """
    Prefer a known local directory when possible so we can validate artifacts before load.
    Resolution order:
      1. explicit env override
      2. explicit local path in config
      3. locally cached HF snapshot
      4. raw repo id / original model name
    """
    env_override = _find_env_model_override(model_name)
    if env_override is not None:
        return str(env_override)

    if str(model_name or "").strip() in LEGACY_BROKEN_LOCAL_MODEL_DEFAULTS:
        logger.warning(
            "Ignoring legacy broken local Qwen3-TTS default model path: %s; "
            "falling back to repo-id resolution instead",
            model_name,
        )
        return "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

    if _looks_like_local_model_path(model_name):
        return str(Path(model_name).expanduser().resolve())

    cached_snapshot = _find_cached_snapshot_dir(model_name)
    if cached_snapshot is not None:
        return str(cached_snapshot)

    return str(model_name)


def _validate_local_model_dir(model_dir: Path) -> Dict[str, Any]:
    """
    Validate a local model directory before transformers touches it.

    This catches:
      - missing required config files
      - empty or missing safetensors shards
      - safetensors shards with missing metadata
    """
    from safetensors import safe_open

    model_dir = model_dir.expanduser().resolve()
    if not model_dir.exists():
        raise RuntimeError(f"model_path_not_found:{model_dir}")
    if not model_dir.is_dir():
        raise RuntimeError(f"model_path_not_directory:{model_dir}")

    required_files = [
        "config.json",
        "preprocessor_config.json",
    ]
    missing_required = [name for name in required_files if not (model_dir / name).exists()]
    if missing_required:
        raise RuntimeError(
            f"model_dir_missing_required_files:{model_dir}:missing={','.join(missing_required)}"
        )

    shard_files = sorted(model_dir.glob("*.safetensors"))
    if not shard_files:
        raise RuntimeError(f"model_dir_missing_safetensors:{model_dir}")

    shard_details = []
    for shard in shard_files:
        if not shard.exists() or shard.stat().st_size <= 0:
            raise RuntimeError(f"safetensors_shard_empty_or_missing:{shard}")
        try:
            with safe_open(str(shard), framework="pt") as handle:
                metadata = handle.metadata()
        except Exception as exc:
            raise RuntimeError(f"safetensors_open_failed:{shard}:{exc}") from exc

        if metadata is None:
            metadata = {"format": "pt", "_omnix_inferred": True}

        shard_details.append(
            {
                "path": str(shard),
                "size_bytes": int(shard.stat().st_size),
                "metadata_keys": sorted(list(metadata.keys())),
            }
        )

    return {
        "model_dir": str(model_dir),
        "required_files_ok": True,
        "num_safetensors_shards": len(shard_files),
        "shards": shard_details,
    }


def _normalize_requested_device(requested_device: str | None) -> str:
    import torch

    raw = (requested_device or "").strip().lower()
    if not raw:
        raw = "cuda"

    if raw.startswith("cuda"):
        cuda_available = bool(getattr(torch, "cuda", None)) and torch.cuda.is_available()
        torch_cuda_compiled = getattr(torch.version, "cuda", None) is not None
        if not cuda_available or not torch_cuda_compiled:
            return "cpu"
        return raw

    if raw in {"cpu", "mps"}:
        return raw

    return "cpu"


def _classify_qwen3_model_load_error(exc: Exception) -> str:
    message = str(exc or "").strip()
    if message.startswith("safetensors_metadata_missing:"):
        return (
            "safetensors_metadata_missing_or_incompatible:"
            f"{message.split(':', 1)[1]}"
        )
    if message.startswith("safetensors_open_failed:"):
        return (
            "safetensors_open_failed:"
            f"{message.split(':', 1)[1]}"
        )
    if message.startswith("model_dir_missing_required_files:"):
        return (
            "local_model_dir_incomplete:"
            f"{message.split(':', 1)[1]}"
        )
    if message.startswith("model_dir_missing_safetensors:"):
        return (
            "local_model_dir_missing_weights:"
            f"{message.split(':', 1)[1]}"
        )
    if message.startswith("model_path_not_found:") or message.startswith("model_path_not_directory:"):
        return message
    if "'NoneType' object has no attribute 'get'" in message:
        return (
            "safetensors_metadata_missing_or_incompatible:"
            "transformers attempted metadata.get('format') but shard metadata was None"
        )
    if "Incompatible safetensors file. File metadata is not ['pt', 'tf', 'flax', 'mlx'] but None" in message:
        return (
            "safetensors_metadata_missing_or_incompatible:"
            "transformers reported shard metadata=None while validating safetensors format"
        )
    return message or exc.__class__.__name__


def load_tts_model(model_name: str, device: str, **kwargs) -> Any:
    """
    Load and return vendored Qwen3-TTS model runtime instance.

    This function always loads a fresh model instance.
    For cached access use get_or_create_tts_model().

    Args:
        model_name: HuggingFace model name or local path
        device: Target device ('cuda', 'cpu', 'mps')
        **kwargs: Additional model loading parameters

    Returns:
        Initialized FasterQwen3TTS model instance

    Raises:
        RuntimeError: If bootstrap or model loading fails
    """
    ensure_vendored_qwen3_tts_available()
    
    runtime_status = validate_qwen3_tts_runtime()
    if not runtime_status.get("ready", False):
        raise RuntimeError(
            f"qwen3_tts_runtime_not_ready:{runtime_status.get('error') or 'unknown_runtime_error'}"
        )

    # Import only after bootstrap validation is complete
    from app.providers.vendor.faster_qwen3_tts.model import (
        _ensure_transformers_qwen3_compat,
    )
    _ensure_transformers_qwen3_compat()
    
    from app.providers.vendor.faster_qwen3_tts.model import FasterQwen3TTS

    resolved_model_source = _resolve_model_source(model_name)
    validation_details: Dict[str, Any] = {}

    if _looks_like_local_model_path(resolved_model_source):
        validation_details = _validate_local_model_dir(Path(resolved_model_source))
        logger.info(
            "Validated local Qwen3-TTS model dir for %s -> %s (%s shards)",
            model_name,
            validation_details.get("model_dir"),
            validation_details.get("num_safetensors_shards"),
        )
    else:
        logger.info(
            "Loading vendored Qwen3-TTS model from repo id %s on %s (no local snapshot resolved)",
            resolved_model_source,
            device,
        )

    normalized_device = _normalize_requested_device(device)
    
    logger.info(
        "Loading vendored Qwen3-TTS model: requested=%s resolved=%s device=%s normalized_device=%s "
        "(vendored runtime package remains under resources/models/tts/faster-qwen3-tts-main)",
        model_name,
        resolved_model_source,
        device,
        normalized_device,
    )
    
    try:
        model = FasterQwen3TTS.from_pretrained(
            model_name=resolved_model_source,
            device=normalized_device,
            **kwargs
        )
        
        logger.info("Successfully loaded vendored Qwen3-TTS model: requested=%s resolved=%s", model_name, resolved_model_source)
        return model
        
    except Exception as e:
        classified = _classify_qwen3_model_load_error(e)
        logger.error(
            "Failed to load vendored Qwen3-TTS model: %s (requested=%s resolved=%s validation=%s raw=%r)",
            classified,
            model_name,
            resolved_model_source,
            validation_details,
            e,
            exc_info=True,
        )
        raise RuntimeError(f"Model loading failed: {classified}") from e


def get_or_create_tts_model(model_name: str, device: str, **kwargs) -> Any:
    """
    Return cached model instance keyed by model name + device.

    Thread-safe singleton access pattern.
    """
    cache_key = f"{model_name}:{device}"
    
    # Fast path: already cached
    if cache_key in _model_cache:
        return _model_cache[cache_key]
    
    # Thread-safe initialization
    with _cache_lock:
        # Double-check after acquiring lock
        if cache_key in _model_cache:
            return _model_cache[cache_key]
        
        model = load_tts_model(model_name, device, **kwargs)
        _model_cache[cache_key] = model
        
        return model


def reset_tts_model_cache() -> None:
    """
    Clear cached vendored TTS model state and free memory.

    This will unload all models from GPU/CPU memory.
    Subsequent calls to get_or_create_tts_model() will reload fresh instances.
    """
    global _model_cache
    
    with _cache_lock:
        for model in _model_cache.values():
            if hasattr(model, 'unload'):
                try:
                    model.unload()
                except Exception:
                    pass
        
        _model_cache.clear()
    
    # Force garbage collection
    import gc
    gc.collect()
    
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
    
    logger.info("Vendored Qwen3-TTS model cache cleared")