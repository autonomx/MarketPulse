"""On-demand Diffusers providers for large turbo text-to-image models."""
from __future__ import annotations

import contextlib
import gc
import inspect
import os
import threading
from typing import Any, Dict

import torch
from packaging.version import InvalidVersion, Version

from app.platform.image.downloads import get_image_local_model_status
from app.platform.image.output_normalization import normalize_generated_image
from app.platform.image.providers.base import BaseImageProvider, ImageGenerationResult
from app.platform.image.providers.registry import get_image_provider_definition
from app.runtime.paths import generated_images_root

_PIPELINE_LOCK = threading.Lock()
_GENERATE_LOCK = threading.Lock()


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = _safe_str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _release_memory() -> None:
    with contextlib.suppress(Exception):
        gc.collect()
    with contextlib.suppress(Exception):
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()


def _version_at_least(current: str, minimum: str) -> bool:
    try:
        return Version(current) >= Version(minimum)
    except InvalidVersion:
        return False


class DiffusersTurboImageProvider(BaseImageProvider):
    """A local-only, explicitly loaded provider backed by a Diffusers pipeline."""

    def __init__(self, provider_name: str, config: Dict[str, Any] | None = None):
        super().__init__(config)
        self.provider_name = _safe_str(provider_name).strip().lower()
        self.definition = get_image_provider_definition(self.provider_name) or {}
        if not self.definition:
            raise RuntimeError(f"unsupported_image_provider:{self.provider_name}")
        self._pipeline = None
        self._memory_mode = "unloaded"

    def load(self):
        self._ensure_pipeline()
        return None

    def is_loaded(self) -> bool:
        with _PIPELINE_LOCK:
            return self._pipeline is not None

    def runtime_status(self) -> Dict[str, Any]:
        free_gib = None
        total_gib = None
        if torch.cuda.is_available():
            with contextlib.suppress(Exception):
                free_bytes, total_bytes = torch.cuda.mem_get_info()
                free_gib = round(free_bytes / float(1024**3), 2)
                total_gib = round(total_bytes / float(1024**3), 2)
        return {
            "memory_mode": self._memory_mode,
            "cuda_available": torch.cuda.is_available(),
            "cuda_free_gib": free_gib,
            "cuda_total_gib": total_gib,
            "pipeline_class": _safe_str(self.definition.get("pipeline_class")),
        }

    def _dtype(self):
        dtype_name = _safe_str(self.config.get("torch_dtype")).strip().lower()
        if dtype_name == "float16":
            return torch.float16
        if dtype_name == "float32":
            return torch.float32
        return torch.bfloat16

    def _local_dir(self) -> str:
        local_dir = _safe_str(self.config.get("local_dir")).strip()
        return get_image_local_model_status(self.provider_name, local_dir).get("local_dir", "")

    def _validate_runtime(self):
        try:
            import diffusers
        except Exception as exc:
            raise RuntimeError(f"{self.provider_name}_missing_diffusers:{exc}") from exc

        minimum_diffusers = _safe_str(self.definition.get("minimum_diffusers")).strip()
        if minimum_diffusers and not _version_at_least(diffusers.__version__, minimum_diffusers):
            raise RuntimeError(
                f"{self.provider_name}_diffusers_too_old:"
                f"installed={diffusers.__version__} required>={minimum_diffusers}"
            )

        minimum_torch = _safe_str(self.definition.get("minimum_torch")).strip()
        if minimum_torch and not _version_at_least(torch.__version__, minimum_torch):
            raise RuntimeError(
                f"{self.provider_name}_torch_too_old:"
                f"installed={torch.__version__} required>={minimum_torch}"
            )

        class_name = _safe_str(self.definition.get("pipeline_class")).strip()
        pipeline_class = getattr(diffusers, class_name, None)
        if pipeline_class is None:
            raise RuntimeError(f"{self.provider_name}_pipeline_unavailable:{class_name}")
        return pipeline_class

    def _ensure_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline

        with _PIPELINE_LOCK:
            if self._pipeline is not None:
                return self._pipeline
            if not torch.cuda.is_available():
                raise RuntimeError(f"{self.provider_name}_cuda_unavailable")

            local_status = get_image_local_model_status(self.provider_name, self._local_dir())
            if not local_status.get("complete"):
                missing = ",".join(local_status.get("missing") or [])
                raise RuntimeError(
                    f"{self.provider_name}_local_model_missing:"
                    f"{local_status.get('local_dir')} missing={missing}; download the model first"
                )

            pipeline_class = self._validate_runtime()
            use_cpu_offload = _optional_bool(self.config.get("enable_cpu_offload"))
            if use_cpu_offload is None:
                use_cpu_offload = bool(self.definition.get("default_cpu_offload"))

            load_kwargs: Dict[str, Any] = {
                "torch_dtype": self._dtype(),
                "local_files_only": True,
            }
            if self.provider_name == "z_image_turbo":
                load_kwargs["low_cpu_mem_usage"] = bool(self.config.get("low_cpu_mem_usage", False))

            pipe = pipeline_class.from_pretrained(local_status["local_dir"], **load_kwargs)
            if use_cpu_offload:
                pipe.enable_model_cpu_offload()
                self._memory_mode = "cpu_offload"
            else:
                pipe.to(_safe_str(self.config.get("device")).strip() or "cuda")
                self._memory_mode = "cuda_direct"

            self._pipeline = pipe
            return self._pipeline

    def unload(self):
        with _GENERATE_LOCK:
            with _PIPELINE_LOCK:
                pipe = self._pipeline
                self._pipeline = None
                self._memory_mode = "unloaded"
            with contextlib.suppress(Exception):
                del pipe
            _release_memory()
        return None

    def generate(self, payload: Dict[str, Any]) -> ImageGenerationResult:
        if payload.get("image") is not None:
            return ImageGenerationResult(
                ok=False,
                status="failed",
                error=f"{self.provider_name}_image_to_image_not_supported",
            )

        prompt = _safe_str(payload.get("prompt")).strip()
        negative_prompt = _safe_str(payload.get("negative_prompt")).strip()
        width = _safe_int(payload.get("width"), _safe_int(self.config.get("width"), 1024))
        height = _safe_int(payload.get("height"), _safe_int(self.config.get("height"), 1024))
        seed = payload.get("seed")
        steps = _safe_int(
            payload.get("num_inference_steps"),
            _safe_int(self.config.get("num_inference_steps"), _safe_int(self.definition.get("default_steps"), 8)),
        )
        guidance_scale = _safe_float(
            payload.get("guidance_scale"),
            _safe_float(self.config.get("guidance_scale"), _safe_float(self.definition.get("default_guidance_scale"), 0.0)),
        )
        progress_callback = payload.get("_progress_callback")

        kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_inference_steps": steps,
            "guidance_scale": guidance_scale,
        }
        if negative_prompt:
            kwargs["negative_prompt"] = negative_prompt
        if seed is not None:
            with contextlib.suppress(Exception):
                kwargs["generator"] = torch.Generator(device="cpu").manual_seed(int(seed))

        with _GENERATE_LOCK:
            pipe = None
            output = None
            output_image = None
            browser_image = None
            normalization_metadata: Dict[str, Any] = {}
            try:
                try:
                    pipe = self._ensure_pipeline()
                    signature = inspect.signature(pipe.__call__)
                    params = signature.parameters
                    accepts_kwargs = any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in params.values()
                    )
                    if "negative_prompt" in kwargs and "negative_prompt" not in params and not accepts_kwargs:
                        kwargs.pop("negative_prompt")
                    if "callback_on_step_end" in params:
                        def on_step_end(_pipeline, step: int, _timestep, callback_kwargs: Dict[str, Any]):
                            if callable(progress_callback):
                                progress_callback(int(step) + 1, max(1, steps), "Generating image")
                            return callback_kwargs
                        kwargs["callback_on_step_end"] = on_step_end
                        if "callback_on_step_end_tensor_inputs" in params:
                            kwargs["callback_on_step_end_tensor_inputs"] = []

                    if callable(progress_callback):
                        progress_callback(0, max(1, steps), "Generating image")
                    with torch.inference_mode():
                        output = pipe(**kwargs)
                        output_image = output.images[0]
                    if callable(progress_callback):
                        progress_callback(max(1, steps), max(1, steps), "Generating image")
                except Exception as exc:
                    return ImageGenerationResult(
                        ok=False,
                        status="failed",
                        error=f"{self.provider_name}_generate_failed:{repr(exc)}",
                    )

                out_dir = str(generated_images_root())
                os.makedirs(out_dir, exist_ok=True)
                filename = (
                    f"{_safe_str(payload.get('kind')).strip() or 'image'}_"
                    f"{self.provider_name}_{os.getpid()}_{id(output_image)}.png"
                )
                file_path = os.path.normpath(os.path.join(out_dir, filename))
                try:
                    browser_image, normalization_metadata = normalize_generated_image(output_image)
                    browser_image.save(
                        file_path,
                        format="PNG",
                        optimize=False,
                        compress_level=6,
                    )
                except Exception as exc:
                    with contextlib.suppress(OSError):
                        os.remove(file_path)
                    return ImageGenerationResult(
                        ok=False,
                        status="failed",
                        error=f"{self.provider_name}_finalize_failed:{repr(exc)}",
                    )

                actual_width = int(browser_image.width)
                actual_height = int(browser_image.height)
                return ImageGenerationResult(
                    ok=True,
                    status="completed",
                    mime_type="image/png",
                    revised_prompt=prompt,
                    file_path=file_path,
                    asset_url="",
                    metadata={
                        "width": actual_width,
                        "height": actual_height,
                        "requested_width": width,
                        "requested_height": height,
                        "memory_mode": self._memory_mode,
                        "pipeline_class": _safe_str(self.definition.get("pipeline_class")),
                        "image_normalization": normalization_metadata,
                    },
                )
            finally:
                output = None
                output_image = None
                browser_image = None
                pipe = None
                _release_memory()
