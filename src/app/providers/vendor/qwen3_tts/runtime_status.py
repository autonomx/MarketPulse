from __future__ import annotations

from typing import Any, Dict

from .bootstrap import ensure_vendored_qwen3_tts_available


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _ok_payload(provider: str, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "provider": provider,
        "ready": True,
        "status": "ready",
        "summary": f"{provider.upper()} READY",
        "error": "",
        "details": dict(details or {}),
    }


def _fail_payload(provider: str, error: str, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    err = _safe_str(error).strip() or "unknown_runtime_error"
    return {
        "provider": provider,
        "ready": False,
        "status": "not_ready",
        "summary": f"{provider.upper()} NOT READY",
        "error": err,
        "details": dict(details or {}),
    }


def _dependency_missing_error(package_name: str, install_hint: str, exc: Exception) -> str:
    return f"missing_dependency:{package_name}:install={install_hint}:error={exc!r}"


def validate_qwen3_tts_runtime() -> Dict[str, Any]:
    versions: Dict[str, Any] = {}
    compat: Dict[str, Any] = {}
    vendor_paths: Dict[str, Any] = {}

    try:
        vendor_paths = ensure_vendored_qwen3_tts_available()
    except Exception as exc:
        return _fail_payload(
            "qwen3_tts",
            f"vendored_package_validation_failed:{exc!r}",
            {"vendor_paths": vendor_paths},
        )

    try:
        import numpy  # noqa: F401
        versions["numpy"] = getattr(numpy, "__version__", "")
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"numpy_import_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        import torch  # noqa: F401
        versions["torch"] = getattr(torch, "__version__", "")
        try:
            compat["cuda_available"] = bool(torch.cuda.is_available())
        except Exception:
            compat["cuda_available"] = False
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"torch_import_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        import transformers  # noqa: F401
        versions["transformers"] = getattr(transformers, "__version__", "")
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"transformers_import_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        import tokenizers  # noqa: F401
        versions["tokenizers"] = getattr(tokenizers, "__version__", "")
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"tokenizers_import_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        import accelerate  # noqa: F401
        versions["accelerate"] = getattr(accelerate, "__version__", "")
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"accelerate_import_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        import safetensors  # noqa: F401
        versions["safetensors"] = getattr(safetensors, "__version__", "")
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"safetensors_import_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        import soundfile  # noqa: F401
        versions["soundfile"] = getattr(soundfile, "__version__", "")
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"soundfile_import_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        import onnxruntime  # noqa: F401
        versions["onnxruntime"] = getattr(onnxruntime, "__version__", "")
    except Exception as exc:
        return _fail_payload(
            "qwen3_tts",
            _dependency_missing_error("onnxruntime", "pip install onnxruntime>=1.18.0", exc),
            {"versions": versions, "vendor_paths": vendor_paths},
        )

    try:
        from transformers import modeling_utils  # noqa: F401
        compat["has_all_attention_functions"] = hasattr(modeling_utils, "ALL_ATTENTION_FUNCTIONS")
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"transformers_modeling_utils_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        from transformers import utils as transformers_utils  # noqa: F401
        compat["has_auto_docstring"] = hasattr(transformers_utils, "auto_docstring")
        compat["has_auto_class_docstring"] = hasattr(transformers_utils, "auto_class_docstring")
    except Exception as exc:
        return _fail_payload("qwen3_tts", f"transformers_utils_failed:{exc!r}", {"versions": versions, "vendor_paths": vendor_paths})

    try:
        from app.providers.vendor.faster_qwen3_tts.model import (
            _ensure_transformers_qwen3_compat,
        )
        _ensure_transformers_qwen3_compat()
    except Exception as exc:
        return _fail_payload(
            "qwen3_tts",
            f"compat_shim_apply_failed:{exc!r}",
            {"versions": versions, "compat": compat, "vendor_paths": vendor_paths},
        )

    try:
        from transformers import modeling_utils
        from transformers import utils as transformers_utils
        compat["shim_has_all_attention_functions"] = hasattr(modeling_utils, "ALL_ATTENTION_FUNCTIONS")
        compat["shim_has_auto_docstring"] = hasattr(transformers_utils, "auto_docstring")
        compat["shim_has_auto_class_docstring"] = hasattr(transformers_utils, "auto_class_docstring")
        try:
            import importlib
            masking_utils = importlib.import_module("transformers.masking_utils")
            compat["shim_has_create_masks_for_generate"] = hasattr(masking_utils, "create_masks_for_generate")
        except Exception:
            compat["shim_has_create_masks_for_generate"] = False
    except Exception as exc:
        return _fail_payload(
            "qwen3_tts",
            f"compat_postcheck_failed:{exc!r}",
            {"versions": versions, "compat": compat, "vendor_paths": vendor_paths},
        )

    try:
        import safetensors
        safe_open = getattr(safetensors, "safe_open", None)
        compat["shim_patched_safetensors_metadata"] = bool(
            safe_open is not None and getattr(safe_open, "_omnix_qwen3_metadata_compat", False)
        )
    except Exception as exc:
        return _fail_payload(
            "qwen3_tts",
            f"safetensors_postcheck_failed:{exc!r}",
            {"versions": versions, "compat": compat, "vendor_paths": vendor_paths},
        )

    try:
        from app.providers.vendor.qwen_tts import Qwen3TTSModel  # noqa: F401
    except Exception as exc:
        return _fail_payload(
            "qwen3_tts",
            f"vendored_qwen_import_failed:{exc!r}",
            {"versions": versions, "compat": compat, "vendor_paths": vendor_paths},
        )

    return _ok_payload(
        "qwen3_tts",
        {
            "versions": versions,
            "compat": compat,
            "vendor_paths": vendor_paths,
        },
    )


def log_qwen3_tts_runtime_status() -> Dict[str, Any]:
    status = validate_qwen3_tts_runtime()
    if status.get("ready"):
        print(f"[APP][TTS][QWEN3] QWEN3-TTS READY {status.get('details', {})}")
    else:
        print(
            f"[APP][TTS][QWEN3] QWEN3-TTS NOT READY "
            f"error={status.get('error')} details={status.get('details', {})}"
        )
    return status


if __name__ == "__main__":
    result = log_qwen3_tts_runtime_status()
    raise SystemExit(0 if result.get("ready") else 1)
