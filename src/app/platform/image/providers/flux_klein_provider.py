"""FLUX.2 Klein image provider."""
from __future__ import annotations
import logging

import contextlib
import gc
import inspect
import os
import threading
from typing import Any, Dict, Iterable

import torch

from app.platform.image.downloads import get_flux_local_model_status
from app.platform.image.flux_pipeline_loading import (
    build_flux_pipeline,
    validate_flux_pipeline_import,
    validate_flux_repo_runtime,
)
from app.platform.image.providers.base import BaseImageProvider, ImageGenerationResult
from app.runtime.paths import generated_images_root

logger = logging.getLogger(__name__)

_PIPELINE_LOCK = threading.Lock()
_GENERATE_LOCK = threading.Lock()
_GIB = float(1024**3)
_DEFAULT_MIN_LOAD_FREE_GIB = 14.0
_DEFAULT_MIN_GENERATION_FREE_GIB = 3.0
_DEFAULT_MAX_PIXELS = 1024 * 1024
_DEFAULT_MAX_REFERENCE_PIXELS = 2 * 1024 * 1024
_DEFAULT_MAX_REFERENCE_COUNT = 2
_DEFAULT_MAX_REFERENCE_EDGE = 512


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


def _cuda_memory_gib() -> tuple[float, float] | None:
    if not torch.cuda.is_available():
        return None
    try:
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        return free_bytes / _GIB, total_bytes / _GIB
    except Exception:
        logger.debug("suppressed error in %s", "_cuda_memory_gib", exc_info=True)
        return None


def _release_generation_memory() -> None:
    """Release transient CPU/GPU allocations before another request can start."""

    with contextlib.suppress(Exception):
        gc.collect()
    with contextlib.suppress(Exception):
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()


def _reference_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None]
    return [value]


def _reference_pixels(images: Iterable[Any]) -> int:
    total = 0
    for image in images:
        size = getattr(image, "size", None)
        if isinstance(size, tuple) and len(size) >= 2:
            total += max(1, _safe_int(size[0], 1) * _safe_int(size[1], 1))
    return total


def _bounded_reference_input(value: Any, max_edge: int) -> tuple[Any, list[Any]]:
    """Downsample oversized references to bound FLUX conditioning memory."""

    if value is None:
        return None, []
    items = value if isinstance(value, list) else [value]
    bounded: list[Any] = []
    owned: list[Any] = []
    for image in items:
        size = getattr(image, "size", None)
        if not isinstance(size, tuple) or len(size) < 2:
            bounded.append(image)
            continue
        width = max(1, _safe_int(size[0], 1))
        height = max(1, _safe_int(size[1], 1))
        longest = max(width, height)
        if longest <= max_edge:
            bounded.append(image)
            continue
        scale = max_edge / longest
        resized = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            resample=3,  # Pillow BICUBIC without coupling this provider to a Pillow enum version.
        )
        bounded.append(resized)
        owned.append(resized)
    return (bounded if isinstance(value, list) else bounded[0]), owned


class FluxKleinImageProvider(BaseImageProvider):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__(config)
        self._pipeline = None
        self._memory_mode = "unloaded"

    def load(self):
        self._ensure_pipeline()
        return None

    def is_loaded(self) -> bool:
        with _PIPELINE_LOCK:
            return self._pipeline is not None

    def runtime_status(self) -> Dict[str, Any]:
        memory = _cuda_memory_gib()
        return {
            "memory_mode": self._memory_mode,
            "cuda_available": torch.cuda.is_available(),
            "cuda_free_gib": round(memory[0], 2) if memory else None,
            "cuda_total_gib": round(memory[1], 2) if memory else None,
        }

    def _repo_id(self) -> str:
        variant = _safe_str(self.config.get("variant")).strip().lower()
        if variant == "base":
            return _safe_str(self.config.get("base_repo_id")).strip() or "black-forest-labs/FLUX.2-klein-base-4B"
        return _safe_str(self.config.get("repo_id")).strip() or "black-forest-labs/FLUX.2-klein-4B"

    def _local_dir(self) -> str:
        local_dir = _safe_str(self.config.get("local_dir")).strip()
        if local_dir:
            return os.path.normpath(local_dir)

        download_dir = _safe_str(self.config.get("download_dir")).strip() or "image"
        if os.path.isabs(download_dir):
            root = download_dir
        else:
            from app.runtime.paths import MODELS_DIR

            root = os.path.join(MODELS_DIR, download_dir)

        preferred = os.path.normpath(os.path.join(root, "flux2-klein-4b"))
        legacy = os.path.normpath(os.path.join(root, "flux-klein"))

        if os.path.isdir(preferred):
            return preferred
        if os.path.isdir(legacy):
            return legacy
        return preferred

    def _dtype(self):
        dtype_name = _safe_str(self.config.get("torch_dtype")).strip().lower()
        if dtype_name == "float16":
            return torch.float16
        if dtype_name == "float32":
            return torch.float32
        return torch.bfloat16

    def _resolve_memory_mode(self) -> str:
        device = _safe_str(self.config.get("device")).strip().lower() or "cuda"
        if device != "cuda":
            return "cpu"
        if not torch.cuda.is_available():
            raise RuntimeError("flux_klein_cuda_unavailable")

        explicit_offload = _optional_bool(self.config.get("enable_cpu_offload"))
        memory = _cuda_memory_gib()
        free_gib = memory[0] if memory else None
        total_gib = memory[1] if memory else None

        if explicit_offload is True:
            return "cpu_offload"
        if explicit_offload is None and total_gib is not None and total_gib < 16.0:
            return "cpu_offload"

        min_free_gib = _safe_float(
            self.config.get("min_cuda_free_gib"),
            _DEFAULT_MIN_LOAD_FREE_GIB,
        )
        if free_gib is not None and free_gib < min_free_gib:
            raise RuntimeError(
                "flux_klein_insufficient_vram:"
                f"free_gib={free_gib:.2f} required_gib={min_free_gib:.2f} "
                f"total_gib={total_gib:.2f}; unload other GPU models before loading FLUX"
            )
        return "cuda_direct"

    def _generation_budget_error(
        self,
        width: int,
        height: int,
        reference_images: Iterable[Any] | None = None,
    ) -> str:
        max_pixels = _safe_int(self.config.get("max_pixels"), _DEFAULT_MAX_PIXELS)
        pixels = max(1, width * height)
        if pixels > max_pixels:
            return (
                "flux_klein_image_too_large:"
                f"requested_pixels={pixels} max_pixels={max_pixels} "
                f"requested_size={width}x{height}"
            )

        references = list(reference_images or [])
        max_reference_count = _safe_int(
            self.config.get("max_reference_count"),
            _DEFAULT_MAX_REFERENCE_COUNT,
        )
        if len(references) > max_reference_count:
            return (
                "flux_klein_reference_limit_exceeded:"
                f"count={len(references)} max={max_reference_count}"
            )
        reference_pixels = _reference_pixels(references)
        max_reference_pixels = _safe_int(
            self.config.get("max_reference_pixels"),
            _DEFAULT_MAX_REFERENCE_PIXELS,
        )
        if reference_pixels > max_reference_pixels:
            return (
                "flux_klein_reference_images_too_large:"
                f"reference_pixels={reference_pixels} max_pixels={max_reference_pixels}"
            )

        if self._memory_mode != "cuda_direct":
            return ""
        memory = _cuda_memory_gib()
        if memory is None:
            return ""
        free_gib, total_gib = memory
        configured_min = _safe_float(
            self.config.get("min_generation_free_gib"),
            _DEFAULT_MIN_GENERATION_FREE_GIB,
        )
        output_headroom = 4.0 * (pixels / _DEFAULT_MAX_PIXELS)
        reference_headroom = 1.25 * (reference_pixels / _DEFAULT_MAX_PIXELS)
        required_gib = max(configured_min, output_headroom + reference_headroom)
        if free_gib < required_gib:
            return (
                "flux_klein_insufficient_generation_vram:"
                f"free_gib={free_gib:.2f} required_gib={required_gib:.2f} "
                f"total_gib={total_gib:.2f} requested_size={width}x{height} "
                f"reference_count={len(references)}; "
                "unload other GPU models, reduce image size, or remove a reference image"
            )
        return ""

    def _ensure_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline

        with _PIPELINE_LOCK:
            if self._pipeline is not None:
                return self._pipeline

            compat = validate_flux_pipeline_import()
            if not compat.get("ok"):
                raise RuntimeError(f"flux_klein_missing_runtime:{compat.get('error')}")

            pipeline_name = (compat.get("details") or {}).get("pipeline_class", "unknown")
            logger.info(f"[FLUX] Using pipeline: {pipeline_name}")

            local_dir = self._local_dir()
            prefer_local = bool(self.config.get("prefer_local_files", True))
            allow_repo_fallback = bool(self.config.get("allow_repo_fallback", False))

            local_status = get_flux_local_model_status(local_dir)
            if local_status.get("complete"):
                repo_or_path = local_dir
                repo_compat = validate_flux_repo_runtime(repo_or_path)
                if not repo_compat.get("ok"):
                    raise RuntimeError(
                        "flux_klein_missing_runtime:"
                        + _safe_str(repo_compat.get("error")).strip()
                    )
                local_files_only = bool(prefer_local)
            else:
                if not allow_repo_fallback:
                    missing = ",".join(local_status.get("missing") or [])
                    raise RuntimeError(
                        "flux_klein_local_model_missing:"
                        f"{local_dir}"
                        f" missing={missing} "
                        "download first via /api/image/models/flux-klein/download"
                    )
                repo_or_path = self._repo_id()
                local_files_only = False

            memory_mode = self._resolve_memory_mode()
            memory = _cuda_memory_gib()
            memory_text = (
                f" free={memory[0]:.2f}GiB total={memory[1]:.2f}GiB"
                if memory
                else ""
            )
            logger.info(f"[FLUX] Memory mode: {memory_mode}{memory_text}")

            pipe = build_flux_pipeline(
                repo_or_path,
                torch_dtype=self._dtype(),
                local_files_only=local_files_only,
                device_map="cuda" if memory_mode == "cuda_direct" else None,
            )

            if memory_mode == "cpu_offload":
                pipe.enable_model_cpu_offload()

            self._memory_mode = memory_mode
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

            _release_generation_memory()

        return None

    def generate(self, payload: Dict[str, Any]) -> ImageGenerationResult:
        prompt = _safe_str(payload.get("prompt")).strip()
        negative_prompt = _safe_str(payload.get("negative_prompt")).strip()
        width = _safe_int(payload.get("width"), _safe_int(self.config.get("width"), 768))
        height = _safe_int(payload.get("height"), _safe_int(self.config.get("height"), 768))
        seed = payload.get("seed")
        steps = _safe_int(
            payload.get("num_inference_steps"),
            _safe_int(self.config.get("num_inference_steps"), 4),
        )
        guidance_scale = _safe_float(
            payload.get("guidance_scale"),
            _safe_float(self.config.get("guidance_scale"), 1.0),
        )
        reference_input = payload.get("image")
        reference_input, owned_references = _bounded_reference_input(
            reference_input,
            max(64, _safe_int(self.config.get("max_reference_edge"), _DEFAULT_MAX_REFERENCE_EDGE)),
        )
        references = _reference_list(reference_input)

        kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_inference_steps": steps,
            "guidance_scale": guidance_scale,
        }
        if negative_prompt:
            kwargs["negative_prompt"] = negative_prompt
        if reference_input is not None:
            kwargs["image"] = reference_input

        if seed is not None:
            with contextlib.suppress(Exception):
                kwargs["generator"] = torch.Generator(device="cpu").manual_seed(int(seed))

        progress_callback = payload.get("_progress_callback")

        def report_progress(current: int) -> None:
            if callable(progress_callback):
                with contextlib.suppress(Exception):
                    progress_callback(
                        max(0, min(steps, current)),
                        max(1, steps),
                        "Generating image",
                    )

        with _GENERATE_LOCK:
            pipe = None
            output = None
            output_image = None
            try:
                try:
                    pipe = self._ensure_pipeline()
                except Exception as exc:
                    return ImageGenerationResult(
                        ok=False,
                        status="failed",
                        error=_safe_str(exc).strip() or f"flux_klein_load_failed:{repr(exc)}",
                        moderation_status="approved",
                        moderation_reason="",
                    )

                budget_error = self._generation_budget_error(width, height, references)
                if budget_error:
                    return ImageGenerationResult(
                        ok=False,
                        status="failed",
                        error=budget_error,
                        moderation_status="approved",
                        moderation_reason="",
                    )

                try:
                    report_progress(0)
                    with contextlib.suppress(Exception):
                        signature = inspect.signature(pipe.__call__)
                        params = signature.parameters
                        accepts_extra_kwargs = any(
                            parameter.kind is inspect.Parameter.VAR_KEYWORD
                            for parameter in params.values()
                        )
                        if (
                            "negative_prompt" in kwargs
                            and "negative_prompt" not in params
                            and not accepts_extra_kwargs
                        ):
                            kwargs.pop("negative_prompt")
                        if reference_input is not None and "image" not in params:
                            raise RuntimeError("flux_klein_image_to_image_unsupported_by_runtime")
                        if "callback_on_step_end" in params:

                            def on_step_end(
                                _pipeline,
                                step: int,
                                _timestep,
                                callback_kwargs: Dict[str, Any],
                            ):
                                report_progress(int(step) + 1)
                                return callback_kwargs

                            kwargs["callback_on_step_end"] = on_step_end
                            if "callback_on_step_end_tensor_inputs" in params:
                                kwargs["callback_on_step_end_tensor_inputs"] = []
                        elif "callback" in params:

                            def on_step(step: int, _timestep, _latents):
                                report_progress(int(step) + 1)

                            kwargs["callback"] = on_step
                            if "callback_steps" in params:
                                kwargs["callback_steps"] = 1

                    with torch.inference_mode():
                        output = pipe(**kwargs)
                        output_image = output.images[0]
                    report_progress(steps)
                except Exception as exc:
                    return ImageGenerationResult(
                        ok=False,
                        status="failed",
                        error=f"flux_klein_generate_failed:{repr(exc)}",
                        moderation_status="approved",
                        moderation_reason="",
                    )

                out_dir = str(generated_images_root())
                os.makedirs(out_dir, exist_ok=True)
                filename = (
                    f"{_safe_str(payload.get('kind')).strip() or 'image'}_"
                    f"{os.getpid()}_{id(output_image)}.png"
                )
                file_path = os.path.normpath(os.path.join(out_dir, filename))

                try:
                    output_image.save(file_path, format="PNG")
                except Exception as exc:
                    with contextlib.suppress(OSError):
                        os.remove(file_path)
                    return ImageGenerationResult(
                        ok=False,
                        status="failed",
                        error=f"flux_klein_finalize_failed:{repr(exc)}",
                        moderation_status="approved",
                        moderation_reason="",
                    )

                return ImageGenerationResult(
                    ok=True,
                    status="completed",
                    error="",
                    moderation_status="approved",
                    moderation_reason="",
                    mime_type="image/png",
                    revised_prompt=prompt,
                    file_path=file_path,
                    asset_url="",
                    metadata={
                        "width": width,
                        "height": height,
                        "memory_mode": self._memory_mode,
                        "image_to_image": bool(references),
                        "reference_count": len(references),
                    },
                )
            finally:
                output = None
                output_image = None
                pipe = None
                for image in owned_references:
                    with contextlib.suppress(Exception):
                        image.close()
                _release_generation_memory()
