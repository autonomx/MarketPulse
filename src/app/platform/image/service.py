"""Global image generation service."""
from __future__ import annotations

from app.config.env import env_str, environment

import os
from typing import Any, Dict

from app.platform.image.cache import image_cache_key, lookup_image_cache, store_image_cache
from app.platform.image.config import get_active_image_provider_name, get_provider_config
from app.platform.image.lifecycle import (
    get_cached_provider,
    get_or_create_image_provider,
    is_image_provider_loaded,
    load_image_provider,
    unload_image_provider,
)
from app.platform.image.models import ImageGenerationRequest, ImageGenerationResponse
from app.platform.image.providers.registry import get_image_provider_definition, is_supported_image_provider
from app.platform.image.reference_assets import (
    ImageReferenceError,
    close_image_references,
    load_image_reference_assets,
)
from app.platform.image.reference_transport import REFERENCE_IMAGES_PAYLOAD_KEY, decode_reference_payloads
from app.platform.image.style import apply_image_style
from app.platform.image.image_http_client import generate_image_via_service, is_image_service_enabled
from app.persistence.device_permits import PermitPriority, device_permit_slot

_GIB = float(1024**3)


def _metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _generate_with_device_permit(provider: Any, payload: dict[str, Any], *, priority: PermitPriority) -> Any:
    with device_permit_slot("image", priority=priority, timeout_seconds=30.0):
        return provider.generate(payload)


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


def _truthy(value: Any) -> bool:
    return _safe_str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _reference_asset_ids(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        normalized = _safe_str(item).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _reference_transport_payloads(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [normalized for item in value if (normalized := _safe_str(item).strip())]


def _normalize_request(payload: Dict[str, Any]) -> ImageGenerationRequest:
    payload = payload if isinstance(payload, dict) else {}
    provider = _safe_str(payload.get("provider")).strip() or get_active_image_provider_name()
    if not is_supported_image_provider(provider):
        provider = get_active_image_provider_name()
    return ImageGenerationRequest(
        provider=provider,
        prompt=_safe_str(payload.get("prompt")).strip(),
        negative_prompt=_safe_str(payload.get("negative_prompt")).strip(),
        width=_safe_int(payload.get("width"), 1024),
        height=_safe_int(payload.get("height"), 1024),
        seed=payload.get("seed"),
        steps=payload.get("steps"),
        guidance_scale=payload.get("guidance_scale"),
        kind=_safe_str(payload.get("kind")).strip() or "image",
        source=_safe_str(payload.get("source")).strip() or "app",
        style=_safe_str(payload.get("style")).strip(),
        reference_asset_ids=_reference_asset_ids(payload.get("reference_asset_ids")),
        session_id=_safe_str(payload.get("session_id")).strip(),
        request_id=_safe_str(payload.get("request_id")).strip(),
        metadata=_metadata(payload.get("metadata")),
    )


def _map_to_provider_payload(req: ImageGenerationRequest, provider_config: Dict[str, Any]) -> Dict[str, Any]:
    definition = get_image_provider_definition(req.provider) or {}
    steps = req.steps
    if steps is None:
        steps = _safe_int(
            provider_config.get("num_inference_steps"),
            _safe_int(definition.get("default_steps"), 4),
        )

    guidance_scale = req.guidance_scale
    if guidance_scale is None:
        guidance_scale = _safe_float(
            provider_config.get("guidance_scale"),
            _safe_float(definition.get("default_guidance_scale"), 1.0),
        )

    return {
        "prompt": apply_image_style(req.prompt, req.style),
        "negative_prompt": req.negative_prompt,
        "width": req.width,
        "height": req.height,
        "seed": req.seed,
        "num_inference_steps": steps,
        "guidance_scale": guidance_scale,
        "kind": req.kind,
        "style": req.style,
        "reference_asset_ids": list(req.reference_asset_ids),
        "metadata": req.metadata,
    }


def _reference_failure(
    req: ImageGenerationRequest,
    provider_name: str,
    error: Exception | str,
    *,
    image_to_image: bool = True,
) -> ImageGenerationResponse:
    return ImageGenerationResponse(
        ok=False,
        provider=provider_name,
        status="failed",
        error=str(error) or repr(error),
        seed=req.seed,
        width=req.width,
        height=req.height,
        mime_type="image/png",
        metadata={
            "image_to_image": image_to_image,
            "reference_asset_ids": list(req.reference_asset_ids),
        },
    )


def _model_unloaded_response(req: ImageGenerationRequest, provider_name: str) -> ImageGenerationResponse:
    return ImageGenerationResponse(
        ok=False,
        provider=provider_name,
        status="model_unloaded",
        error="image_model_not_loaded",
        seed=req.seed,
        width=req.width,
        height=req.height,
        mime_type="image/png",
        metadata={"model": provider_name, "load_endpoint": "/provider/load"},
    )


def _generation_budget_error(
    req: ImageGenerationRequest,
    provider_name: str,
    definition: Dict[str, Any],
) -> str:
    max_pixels = _safe_int(definition.get("max_pixels"), 1024 * 1024)
    requested_pixels = max(1, req.width * req.height)
    if requested_pixels > max_pixels:
        return (
            f"{provider_name}_image_too_large:"
            f"requested_pixels={requested_pixels} max_pixels={max_pixels} "
            f"requested_size={req.width}x{req.height}"
        )

    if bool(definition.get("default_cpu_offload")):
        return ""
    minimum = definition.get("min_generation_free_gib")
    if minimum is None:
        return ""
    try:
        import torch
    except Exception:
        return ""
    if not torch.cuda.is_available():
        return ""
    try:
        free_bytes, total_bytes = torch.cuda.mem_get_info()
    except Exception:
        return ""
    free_gib = free_bytes / _GIB
    total_gib = total_bytes / _GIB
    required_gib = float(minimum)
    if free_gib < required_gib:
        return (
            f"{provider_name}_insufficient_generation_vram:"
            f"free_gib={free_gib:.2f} required_gib={required_gib:.2f} "
            f"total_gib={total_gib:.2f} requested_size={req.width}x{req.height}; "
            "reduce image size or unload other GPU models"
        )
    return ""


def _generation_failure(
    req: ImageGenerationRequest,
    provider_name: str,
    error: Exception | str,
) -> ImageGenerationResponse:
    return ImageGenerationResponse(
        ok=False,
        provider=provider_name,
        status="failed",
        error=str(error) or repr(error),
        seed=req.seed,
        width=req.width,
        height=req.height,
        mime_type="image/png",
        metadata={"source": req.source, "kind": req.kind, "request_id": req.request_id},
    )


def generate_image_local(payload: Dict[str, Any]) -> ImageGenerationResponse:
    payload = payload if isinstance(payload, dict) else {}
    req = _normalize_request(payload)
    provider_name = req.provider or get_active_image_provider_name()
    provider_config = get_provider_config(provider_name)
    definition = get_image_provider_definition(provider_name) or {}
    unload_after_generation = bool(payload.get("unload_after_generation"))
    transported_references = _reference_transport_payloads(payload.get(REFERENCE_IMAGES_PAYLOAD_KEY))
    image_to_image = bool(req.reference_asset_ids or transported_references)

    if image_to_image and definition.get("supports_image_to_image") is False:
        if unload_after_generation:
            unload_image_provider(provider_name)
        return _reference_failure(
            req,
            provider_name,
            f"{provider_name}_image_to_image_not_supported",
            image_to_image=True,
        )

    budget_error = _generation_budget_error(req, provider_name, definition)
    if budget_error:
        if unload_after_generation:
            unload_image_provider(provider_name)
        return _generation_failure(req, provider_name, budget_error)

    if definition.get("supports_local_model") and not is_image_provider_loaded(provider_name):
        if _truthy(environment().get("OMNIX_IMAGE_REQUIRE_EXPLICIT_LOAD", "1")):
            return _model_unloaded_response(req, provider_name)
        try:
            load_image_provider(provider_name)
        except Exception as exc:
            return _generation_failure(req, provider_name, exc)

    try:
        provider_payload = _map_to_provider_payload(req, provider_config)
        provider_payload["provider"] = provider_name
        provider_payload["request_id"] = req.request_id
        progress_callback = payload.get("_progress_callback")
        if callable(progress_callback):
            provider_payload["_progress_callback"] = progress_callback

        # Reference-conditioned output is intentionally not cached. A deleted or
        # replaced reference must not leave a reusable derived cache result behind.
        use_cache = (
            not bool(payload.get("no_cache"))
            and not bool(payload.get("warmup"))
            and not image_to_image
        )
        cache_key = image_cache_key(provider_payload)
        if use_cache:
            cached = lookup_image_cache(cache_key)
            if cached:
                return ImageGenerationResponse(
                    ok=True,
                    provider=_safe_str(cached.get("provider")) or provider_name,
                    status="completed",
                    error="",
                    asset_url=_safe_str(cached.get("asset_url")),
                    local_path=_safe_str(cached.get("file_path")),
                    seed=cached.get("seed"),
                    width=_safe_int(cached.get("width"), req.width),
                    height=_safe_int(cached.get("height"), req.height),
                    mime_type=_safe_str(cached.get("mime_type")) or "image/png",
                    metadata={"cache_hit": True, "cache_key": cache_key},
                )

        provider = get_cached_provider(provider_name) or get_or_create_image_provider(provider_name)
        reference_images = []
        try:
            if transported_references:
                if req.reference_asset_ids and len(transported_references) != len(req.reference_asset_ids):
                    raise ImageReferenceError(
                        "image_reference_transport_count_mismatch:"
                        f"ids={len(req.reference_asset_ids)} payloads={len(transported_references)}"
                    )
                reference_images = decode_reference_payloads(transported_references)
            elif req.reference_asset_ids:
                reference_images = load_image_reference_assets(req.reference_asset_ids)

            if reference_images:
                provider_payload["image"] = reference_images[0] if len(reference_images) == 1 else reference_images

            if definition.get("supports_local_model"):
                priority: PermitPriority = (
                    "batch"
                    if payload.get("_device_permit_priority") == "batch"
                    else "interactive"
                )
                result = _generate_with_device_permit(
                    provider,
                    provider_payload,
                    priority=priority,
                )
            else:
                result = provider.generate(provider_payload)
            if use_cache and result.ok:
                cached = store_image_cache(cache_key, result)
                if cached:
                    result.file_path = cached.get("file_path") or getattr(result, "file_path", "")
                    result.asset_url = cached.get("asset_url") or getattr(result, "asset_url", "")
        except ImageReferenceError as exc:
            return _reference_failure(
                req,
                provider_name,
                exc,
                image_to_image=image_to_image,
            )
        except Exception as exc:
            return _generation_failure(req, provider_name, exc)
        finally:
            close_image_references(reference_images)

        local_path = _safe_str(getattr(result, "file_path", "")).strip()
        asset_url = _safe_str(getattr(result, "asset_url", "")).strip()
        error = _safe_str(getattr(result, "error", "")).strip()
        status = _safe_str(getattr(result, "status", "")).strip() or (
            "completed" if getattr(result, "ok", False) else "failed"
        )
        mime_type = _safe_str(getattr(result, "mime_type", "")).strip() or "image/png"
        revised_prompt = _safe_str(getattr(result, "revised_prompt", "")).strip()
        result_metadata = getattr(result, "metadata", None)
        if not isinstance(result_metadata, dict):
            result_metadata = {}

        return ImageGenerationResponse(
            ok=bool(getattr(result, "ok", False)),
            provider=provider_name,
            status=status,
            error=error,
            asset_url=asset_url,
            local_path=local_path,
            seed=req.seed,
            width=req.width,
            height=req.height,
            mime_type=mime_type,
            metadata={
                **(result_metadata or {}),
                "cache_hit": False,
                "cache_key": cache_key if use_cache else "",
                "source": req.source,
                "kind": req.kind,
                "style": req.style,
                "image_to_image": image_to_image,
                "reference_asset_ids": list(req.reference_asset_ids),
                "request_id": req.request_id,
                "session_id": req.session_id,
                "revised_prompt": revised_prompt,
                "unloaded_after_generation": unload_after_generation,
            },
        )
    finally:
        if unload_after_generation:
            unload_image_provider(provider_name)


def generate_image(payload: Dict[str, Any]) -> ImageGenerationResponse:
    if is_image_service_enabled() and environment().get("OMNIX_IMAGE_SERVICE_MODE") != "1":
        request_payload = dict(payload) if isinstance(payload, dict) else {}
        # The callback cannot cross the wire; the service streams its steps back.
        progress_callback = request_payload.pop("_progress_callback", None)
        data = generate_image_via_service(
            request_payload,
            on_progress=progress_callback if callable(progress_callback) else None,
        )
        return ImageGenerationResponse(
            ok=bool(data.get("ok")),
            provider=_safe_str(data.get("provider")),
            status=_safe_str(data.get("status")) or ("completed" if data.get("ok") else "failed"),
            error=_safe_str(data.get("error")),
            asset_url=_safe_str(data.get("asset_url")),
            local_path=_safe_str(data.get("local_path")),
            blob_key=_safe_str(data.get("blob_key")),
            checksum_sha256=_safe_str(data.get("checksum_sha256")),
            seed=data.get("seed"),
            width=_safe_int(data.get("width"), 0),
            height=_safe_int(data.get("height"), 0),
            mime_type=_safe_str(data.get("mime_type")) or "image/png",
            metadata=_metadata(data.get("metadata")),
        )

    return generate_image_local(payload)
