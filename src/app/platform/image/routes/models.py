"""Gateway controls for the external image model service."""
from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import inspect
import os
import threading
import time
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, SecretStr
from starlette.concurrency import run_in_threadpool

from app.platform.image.downloads import get_image_local_model_status
from app.platform.image.providers.registry import get_image_provider_definition, list_image_providers
from app.platform.image.image_http_client import (
    download_image_model_via_service,
    get_image_service_status,
    is_image_generation_enabled,
    load_image_model_via_service,
    start_image_service_via_launcher,
    unload_image_model_via_service,
)
from app.errors import error_code

logger = logging.getLogger(__name__)

router = APIRouter()

_DOWNLOAD_TOTALS_LOCK = threading.Lock()
_MAX_PROVIDER_DOWNLOAD_ENTRIES = 32
_DOWNLOAD_TOTAL_TTL_SECONDS = 3600.0
_DOWNLOAD_TOKEN_TTL_SECONDS = 7200.0
_DOWNLOAD_TOTALS: OrderedDict[str, tuple[int, float]] = OrderedDict()
_DOWNLOAD_TOKENS: OrderedDict[str, tuple[str, float]] = OrderedDict()


def _download_metadata_now() -> float:
    return time.monotonic()


def _prune_download_metadata_locked(now: float) -> None:
    expired_totals = [
        provider
        for provider, (_total, stored_at) in _DOWNLOAD_TOTALS.items()
        if now - stored_at > _DOWNLOAD_TOTAL_TTL_SECONDS
    ]
    expired_tokens = [
        provider
        for provider, (_token, stored_at) in _DOWNLOAD_TOKENS.items()
        if now - stored_at > _DOWNLOAD_TOKEN_TTL_SECONDS
    ]
    for provider in expired_totals:
        _DOWNLOAD_TOTALS.pop(provider, None)
    for provider in expired_tokens:
        _DOWNLOAD_TOKENS.pop(provider, None)
    while len(_DOWNLOAD_TOTALS) > _MAX_PROVIDER_DOWNLOAD_ENTRIES:
        _DOWNLOAD_TOTALS.popitem(last=False)
    while len(_DOWNLOAD_TOKENS) > _MAX_PROVIDER_DOWNLOAD_ENTRIES:
        _DOWNLOAD_TOKENS.popitem(last=False)


def _remember_download_total(provider: str, total: int) -> None:
    now = _download_metadata_now()
    with _DOWNLOAD_TOTALS_LOCK:
        _prune_download_metadata_locked(now)
        _DOWNLOAD_TOTALS[provider] = (total, now)
        _DOWNLOAD_TOTALS.move_to_end(provider)
        _prune_download_metadata_locked(now)


def _remember_download_token(provider: str, token: str) -> None:
    now = _download_metadata_now()
    with _DOWNLOAD_TOTALS_LOCK:
        _prune_download_metadata_locked(now)
        _DOWNLOAD_TOKENS[provider] = (token, now)
        _DOWNLOAD_TOKENS.move_to_end(provider)
        _prune_download_metadata_locked(now)


def clear_image_download_metadata() -> None:
    """Clear cached provider sizes and transient download credentials."""
    with _DOWNLOAD_TOTALS_LOCK:
        _DOWNLOAD_TOTALS.clear()
        _DOWNLOAD_TOKENS.clear()


class ImageModelActionRequest(BaseModel):
    provider: str = "flux_klein"


class ImageModelDownloadRequest(ImageModelActionRequest):
    hf_token: SecretStr | None = None


class ImageModelDownloadProgress(BaseModel):
    status: str
    bytes_downloaded: int
    bytes_total: int
    percent: float | None
    indeterminate: bool


class ImageModelLocalStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool | None = None
    exists: bool | None = None
    complete: bool | None = None
    missing: list[str] | None = None
    local_dir: str | None = None
    repo_id: str | None = None
    gated: bool | None = None
    license: str | None = None


class ImageModelEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str | None = None
    label: str | None = None
    provider: str | None = None
    model: str | None = None
    loaded: bool | None = None
    state: str | None = None
    downloaded: bool | None = None
    supports_download: bool | None = None
    supports_image_to_image: bool | None = None
    repo_id: str | None = None
    gated: bool | None = None
    license: str | None = None
    minimum_diffusers: str | None = None
    minimum_torch: str | None = None
    local_model: ImageModelLocalStatus | None = None
    download_progress: ImageModelDownloadProgress | None = None


class ImageModelStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool | None = None
    service: str | None = None
    enabled: bool | None = None
    provider: str | None = None
    model: str | None = None
    loaded: bool | None = None
    state: str | None = None
    error: str | None = None
    explicit_load_required: bool | None = None
    key: str | None = None
    label: str | None = None
    downloaded: bool | None = None
    supports_download: bool | None = None
    supports_image_to_image: bool | None = None
    repo_id: str | None = None
    gated: bool | None = None
    license: str | None = None
    minimum_diffusers: str | None = None
    minimum_torch: str | None = None
    local_model: ImageModelLocalStatus | None = None
    download_progress: ImageModelDownloadProgress | None = None
    models: list[ImageModelEntry] | None = None


def _provider(value: str | None) -> str:
    normalized = str(value or "flux_klein").strip().lower() or "flux_klein"
    return normalized.removeprefix("image:")


def _model_definition(provider: str) -> dict[str, Any]:
    return get_image_provider_definition(provider) or {}


def _model_label(provider: str) -> str:
    definition = _model_definition(provider)
    return str(definition.get("label") or provider or "Image model")


def _read_service_status(provider: str) -> dict[str, Any]:
    """Forward provider selection while preserving zero-argument test doubles."""

    try:
        accepts_provider = bool(inspect.signature(get_image_service_status).parameters)
    except (TypeError, ValueError):
        accepts_provider = True
    return get_image_service_status(provider) if accepts_provider else get_image_service_status()


def _repository_total_bytes(provider: str) -> int:
    now = _download_metadata_now()
    with _DOWNLOAD_TOTALS_LOCK:
        _prune_download_metadata_locked(now)
        cached_entry = _DOWNLOAD_TOTALS.get(provider)
        if cached_entry is not None:
            _DOWNLOAD_TOTALS.move_to_end(provider)
        token_entry = _DOWNLOAD_TOKENS.get(provider)
        transient_token = token_entry[0] if token_entry is not None else ""
    cached = cached_entry[0] if cached_entry is not None else None
    if cached is not None:
        return cached

    definition = _model_definition(provider)
    repo_id = str(definition.get("repo_id") or "").strip()
    if not repo_id:
        return 0
    try:
        from huggingface_hub import HfApi

        info = HfApi().repo_info(
            repo_id=repo_id,
            files_metadata=True,
            token=transient_token or (_env_str("HF_TOKEN", "") or "").strip() or None,
        )
        total = sum(
            max(0, int(getattr(sibling, "size", 0) or 0))
            for sibling in (getattr(info, "siblings", None) or [])
        )
    except Exception:
        total = 0

    if total > 0:
        _remember_download_total(provider, total)
    return total


def _downloaded_bytes(local_dir: str) -> int:
    local_dir = str(local_dir or "").strip()
    if not local_dir or not os.path.isdir(local_dir):
        return 0

    total = 0
    for root, _dirs, files in os.walk(local_dir):
        normalized_root = root.replace("\\", "/")
        in_download_cache = "/.cache/huggingface/download" in normalized_root
        for name in files:
            if in_download_cache and not name.endswith(".incomplete"):
                continue
            path = os.path.join(root, name)
            try:
                total += max(0, os.path.getsize(path))
            except OSError:
                continue
    return total


def _download_progress(provider: str, model: dict[str, Any]) -> dict[str, Any]:
    local_model = model.get("local_model")
    local_model = local_model if isinstance(local_model, dict) else {}
    current = _downloaded_bytes(str(local_model.get("local_dir") or ""))
    total = _repository_total_bytes(provider)
    percent = round(min(100.0, (current / total) * 100.0), 1) if total > 0 else None
    return {
        "status": "downloading",
        "bytes_downloaded": current,
        "bytes_total": total,
        "percent": percent,
        "indeterminate": total <= 0,
    }


def _attach_download_progress(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    if payload.get("state") == "downloading" and payload.get("provider") == provider:
        payload["download_progress"] = _download_progress(provider, payload)

    models = payload.get("models")
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict):
                continue
            model_provider = _provider(str(model.get("provider") or model.get("key") or ""))
            if model.get("state") == "downloading":
                model["download_progress"] = _download_progress(model_provider, model)
    return payload


def _unavailable_model_entry(definition: dict[str, Any]) -> dict[str, Any]:
    provider = _provider(str(definition.get("key") or ""))
    try:
        local_model = get_image_local_model_status(provider)
    except Exception:
        local_model = {"complete": False, "missing": [], "local_dir": ""}
    return {
        **definition,
        "provider": provider,
        "model": definition.get("label"),
        "loaded": False,
        "state": "unavailable",
        "downloaded": bool(local_model.get("complete")),
        "local_model": local_model,
    }


async def _call_service(function, *args: Any) -> dict[str, Any]:
    try:
        result = await run_in_threadpool(function, *args)
    except RuntimeError as exc:
        logger.warning("image_service_call_failed", exc_info=True)
        raise HTTPException(status_code=503, detail=error_code(exc, "image_service_unavailable")) from exc
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="invalid_image_service_response")
    return result


def _require_ok(result: dict[str, Any], fallback: str) -> dict[str, Any]:
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error") or fallback)
    return result


@router.get(
    "/api/image-generation/model/status",
    response_model=ImageModelStatusResponse,
    response_model_exclude_unset=True,
)
async def image_model_status(
    provider: str = Query(default="flux_klein"),
) -> dict[str, Any]:  # validated by response_model
    provider_name = _provider(provider)
    try:
        result = await _call_service(_read_service_status, provider_name)
        return await run_in_threadpool(_attach_download_progress, result, provider_name)
    except HTTPException as exc:
        models = [
            _unavailable_model_entry(definition)
            for definition in list_image_providers()
            if definition.get("supports_local_model")
        ]
        selected = next(
            (model for model in models if model.get("provider") == provider_name),
            _unavailable_model_entry(_model_definition(provider_name)),
        )
        return {
            **selected,
            "ok": False,
            "service": "image",
            "enabled": is_image_generation_enabled(),
            "provider": provider_name,
            "model": _model_label(provider_name),
            "loaded": False,
            "state": "unavailable",
            "error": str(exc.detail or "image_service_unavailable"),
            "explicit_load_required": True,
            "models": models,
        }


@router.post(
    "/api/image-generation/service/start",
    response_model=ImageModelStatusResponse,
    response_model_exclude_unset=True,
)
async def start_image_service(request: ImageModelActionRequest) -> dict[str, Any]:  # validated by response_model
    result = await _call_service(start_image_service_via_launcher, _provider(request.provider))
    return _require_ok(result, "image_service_start_failed")


@router.post(
    "/api/image-generation/model/ensure-loaded",
    response_model=ImageModelStatusResponse,
    response_model_exclude_unset=True,
)
async def ensure_image_model_loaded(request: ImageModelActionRequest) -> dict[str, Any]:  # validated by response_model
    """Start the managed service and make the requested model resident."""

    provider_name = _provider(request.provider)
    start_result = await _call_service(start_image_service_via_launcher, provider_name)
    _require_ok(start_result, "image_service_start_failed")

    status = await _call_service(_read_service_status, provider_name)
    if status.get("ok") and status.get("loaded") and _provider(str(status.get("provider") or "")) == provider_name:
        return status

    load_result = await _call_service(load_image_model_via_service, provider_name)
    return _require_ok(load_result, "image_model_load_failed")


@router.post(
    "/api/image-generation/model/download",
    response_model=ImageModelStatusResponse,
    response_model_exclude_unset=True,
)
async def download_image_model(request: ImageModelDownloadRequest) -> dict[str, Any]:  # validated by response_model
    provider_name = _provider(request.provider)
    token = request.hf_token.get_secret_value().strip() if request.hf_token else ""
    with _DOWNLOAD_TOTALS_LOCK:
        _prune_download_metadata_locked(_download_metadata_now())
        _DOWNLOAD_TOTALS.pop(provider_name, None)
        _DOWNLOAD_TOKENS.pop(provider_name, None)
    if token:
        _remember_download_token(provider_name, token)
    try:
        result = await _call_service(
            download_image_model_via_service,
            provider_name,
            token,
        )
    finally:
        with _DOWNLOAD_TOTALS_LOCK:
            _DOWNLOAD_TOKENS.pop(provider_name, None)
    return _require_ok(result, "image_model_download_failed")


@router.post(
    "/api/image-generation/model/load",
    response_model=ImageModelStatusResponse,
    response_model_exclude_unset=True,
)
async def load_image_model(request: ImageModelActionRequest) -> dict[str, Any]:  # validated by response_model
    result = await _call_service(load_image_model_via_service, _provider(request.provider))
    return _require_ok(result, "image_model_load_failed")


@router.post(
    "/api/image-generation/model/unload",
    response_model=ImageModelStatusResponse,
    response_model_exclude_unset=True,
)
async def unload_image_model(request: ImageModelActionRequest) -> dict[str, Any]:  # validated by response_model
    result = await _call_service(unload_image_model_via_service, _provider(request.provider))
    return _require_ok(result, "image_model_unload_failed")


def create_image_model_router() -> APIRouter:
    """Return an isolated router with typed image model control routes."""
    feature_router = APIRouter()
    feature_router.include_router(router)
    return feature_router


__all__ = [
    "ImageModelActionRequest",
    "ImageModelDownloadProgress",
    "ImageModelDownloadRequest",
    "ImageModelEntry",
    "ImageModelLocalStatus",
    "ImageModelStatusResponse",
    "create_image_model_router",
]
