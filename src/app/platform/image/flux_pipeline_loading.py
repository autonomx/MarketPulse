"""Compatibility helpers for FLUX pipeline loading."""
from __future__ import annotations

import importlib
import json
import os
import traceback
from typing import Any, Dict, Tuple


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _load_json_if_exists(path: str) -> Dict[str, Any]:
    try:
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _read_repo_index(repo_or_path: str) -> Dict[str, Any]:
    repo_or_path = _safe_str(repo_or_path).strip()
    if not repo_or_path or not os.path.isdir(repo_or_path):
        return {}

    for name in ("model_index.json", "modular_model_index.json"):
        payload = _load_json_if_exists(os.path.join(repo_or_path, name))
        if payload:
            payload["_index_filename"] = name
            return payload
    return {}


def _extract_required_symbols(index_payload: Dict[str, Any]) -> Dict[str, set[str]]:
    required: Dict[str, set[str]] = {}
    for key, value in dict(index_payload or {}).items():
        if key.startswith("_"):
            continue
        if (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], str)
            and isinstance(value[1], str)
        ):
            module_name = value[0]
            class_name = value[1]
            required.setdefault(module_name, set()).add(class_name)
    return required


def validate_flux_repo_runtime(repo_or_path: str) -> Dict[str, Any]:
    details: Dict[str, Any] = {"repo_or_path": repo_or_path}

    index_payload = _read_repo_index(repo_or_path)
    if not index_payload:
        return {"ok": True, "error": "", "details": details}

    details["index_filename"] = index_payload.get("_index_filename", "")
    details["repo_class_name"] = index_payload.get("_class_name", "")
    details["repo_diffusers_version"] = index_payload.get("_diffusers_version", "")

    required = _extract_required_symbols(index_payload)
    details["required_symbols"] = {k: sorted(v) for k, v in required.items()}

    import diffusers
    import transformers

    details["installed_versions"] = {
        "diffusers": getattr(diffusers, "__version__", ""),
        "transformers": getattr(transformers, "__version__", ""),
    }

    missing: list[str] = []
    for module_name, class_names in required.items():
        if module_name == "diffusers":
            for class_name in class_names:
                if not hasattr(diffusers, class_name):
                    missing.append(f"diffusers.{class_name}")
        elif module_name == "transformers":
            for class_name in class_names:
                if not hasattr(transformers, class_name):
                    missing.append(f"transformers.{class_name}")

    if missing:
        return {
            "ok": False,
            "error": "repo_component_classes_missing:" + ",".join(sorted(missing)),
            "details": details,
        }

    return {"ok": True, "error": "", "details": details}


def validate_flux_python_stack() -> Dict[str, Any]:
    details: Dict[str, Any] = {}

    try:
        import torch
        details["torch"] = getattr(torch, "__version__", "")
    except Exception as exc:
        details["traceback"] = traceback.format_exc(limit=12)
        return {"ok": False, "error": f"torch_import_failed:{exc!r}", "details": details}

    try:
        import torchvision
        details["torchvision"] = getattr(torchvision, "__version__", "")
    except Exception as exc:
        details["traceback"] = traceback.format_exc(limit=12)
        return {"ok": False, "error": f"torchvision_import_failed:{exc!r}", "details": details}

    try:
        from torchvision.transforms import InterpolationMode  # noqa: F401
        details["torchvision_probe"] = "ok"
    except Exception as exc:
        details["traceback"] = traceback.format_exc(limit=20)
        return {
            "ok": False,
            "error": f"torchvision_runtime_failed:{exc!r}",
            "details": details,
        }

    try:
        import transformers
        details["transformers"] = getattr(transformers, "__version__", "")
    except Exception as exc:
        details["traceback"] = traceback.format_exc(limit=12)
        return {"ok": False, "error": f"transformers_import_failed:{exc!r}", "details": details}

    try:
        from transformers import AutoTokenizer, Qwen3ForCausalLM  # noqa: F401
        details["qwen3_probe"] = "ok"
    except Exception as exc:
        details["traceback"] = traceback.format_exc(limit=20)
        return {
            "ok": False,
            "error": f"qwen3_import_failed:{exc!r}",
            "details": details,
        }

    try:
        import diffusers
        details["diffusers"] = getattr(diffusers, "__version__", "")
    except Exception as exc:
        details["traceback"] = traceback.format_exc(limit=12)
        return {"ok": False, "error": f"diffusers_import_failed:{exc!r}", "details": details}

    return {"ok": True, "error": "", "details": details}


def resolve_flux_pipeline_class() -> Tuple[type, str]:
    import diffusers

    if hasattr(diffusers, "Flux2KleinPipeline"):
        return getattr(diffusers, "Flux2KleinPipeline"), "Flux2KleinPipeline"
    if hasattr(diffusers, "FluxPipeline"):
        return getattr(diffusers, "FluxPipeline"), "FluxPipeline"

    fallback_candidates = [
        ("diffusers.pipelines.flux.pipeline_flux", "FluxPipeline"),
    ]
    errors = []
    for module_name, class_name in fallback_candidates:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, class_name):
                return getattr(module, class_name), f"{module_name}.{class_name}"
            errors.append(f"{module_name}:{class_name}:missing_attr")
        except Exception as exc:
            errors.append(f"{module_name}:{class_name}:{exc!r}")

    raise ImportError(
        "No supported FLUX pipeline class is importable from diffusers. "
        + "; ".join(errors)
    )


def validate_flux_pipeline_import() -> Dict[str, Any]:
    details: Dict[str, Any] = {}

    python_stack = validate_flux_python_stack()
    details["python_stack"] = dict(python_stack.get("details") or {})
    if not python_stack.get("ok"):
        return {
            "ok": False,
            "error": python_stack.get("error", "flux_python_stack_failed"),
            "details": details,
        }

    try:
        import diffusers  # noqa: F401
        details["diffusers_version"] = getattr(diffusers, "__version__", "")
    except Exception as exc:
        return {
            "ok": False,
            "error": f"diffusers_import_failed:{exc!r}",
            "details": details,
        }

    try:
        cls, class_name = resolve_flux_pipeline_class()
        details["pipeline_class"] = class_name.split(".")[-1]
        details["pipeline_resolved_from"] = class_name
        details["pipeline_module"] = getattr(cls, "__module__", "")
        return {"ok": True, "error": "", "details": details}
    except Exception as exc:
        details["traceback"] = traceback.format_exc(limit=20)
        return {
            "ok": False,
            "error": f"flux_pipeline_import_failed:{exc!r}",
            "details": details,
        }


def build_flux_pipeline(
    repo_or_path: str,
    *,
    torch_dtype: Any,
    local_files_only: bool,
    device_map: str | None = None,
):
    pipeline_cls: Any
    pipeline_cls, _class_name = resolve_flux_pipeline_class()
    kwargs: Dict[str, Any] = {
        "torch_dtype": torch_dtype,
        "local_files_only": local_files_only,
    }
    if device_map:
        kwargs["device_map"] = device_map
    return pipeline_cls.from_pretrained(repo_or_path, **kwargs)
