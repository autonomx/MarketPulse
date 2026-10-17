"""Concrete worker model-control hooks for residency transition jobs."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from app.runtime.config import ServiceEndpoint, get_runtime_config
from app.security.service_token import service_headers

from .models import JobRecord
from .residency import ModelResidencyHook, ModelResidencyRecord


PostJson = Callable[[str, dict[str, Any], float], dict[str, Any]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: object,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def create_worker_model_control_hooks(
    *,
    post_json: PostJson | None = None,
    timeout_seconds: float = 120.0,
) -> tuple[ModelResidencyHook, ModelResidencyHook]:
    """Return load/evict hooks for workers with provider control endpoints."""

    return (
        lambda record, job: load_worker_model(record, job, post_json=post_json, timeout_seconds=timeout_seconds),
        lambda record, job: evict_worker_model(record, job, post_json=post_json, timeout_seconds=timeout_seconds),
    )


def load_worker_model(
    record: ModelResidencyRecord,
    job: JobRecord,
    *,
    post_json: PostJson | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    return _post_model_control(
        record,
        job,
        action="loaded",
        path="/provider/load",
        post_json=post_json or _post_json,
        timeout_seconds=timeout_seconds,
    )


def evict_worker_model(
    record: ModelResidencyRecord,
    job: JobRecord,
    *,
    post_json: PostJson | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    return _post_model_control(
        record,
        job,
        action="evicted",
        path="/provider/unload",
        post_json=post_json or _post_json,
        timeout_seconds=timeout_seconds,
    )


def _post_model_control(
    record: ModelResidencyRecord,
    job: JobRecord,
    *,
    action: str,
    path: str,
    post_json: PostJson,
    timeout_seconds: float,
) -> dict[str, Any]:
    endpoint = _worker_endpoint(record, job)
    provider = _provider_key(record)
    payload = {
        "provider": provider,
        "provider_id": record.provider_id,
        "model_id": record.model_id,
        "model_name": record.model_name,
        "job_id": job.id,
        "resource_class": record.resource_class.value,
    }
    response = post_json(f"{endpoint}{path}", payload, timeout_seconds)
    if response.get("ok") is False:
        raise RuntimeError(_response_error(response, action))
    return {
        "logs": [
            {
                "level": "info",
                "message": f"worker model control {action}",
                "model_id": record.model_id,
                "provider": provider,
                "worker_endpoint": endpoint,
            }
        ],
        "output_refs": [
            {
                "kind": "worker_model_control",
                "action": action,
                "model_id": record.model_id,
                "provider": provider,
                "worker_endpoint": endpoint,
                "response": response,
            }
        ],
    }


def _worker_endpoint(record: ModelResidencyRecord, job: JobRecord) -> str:
    payload = job.input_payload or {}
    endpoint = _safe_str(record.worker_endpoint or payload.get("worker_endpoint")).strip().rstrip("/")
    if not endpoint:
        raise RuntimeError("worker_model_control_endpoint_missing")
    try:
        endpoint = ServiceEndpoint(endpoint).url
    except ValueError as exc:
        raise RuntimeError("worker_model_control_endpoint_invalid") from exc
    config = get_runtime_config()
    allowed = {configured.url if configured else f"http://127.0.0.1:{port}"
               for configured, port in ((config.tts, 5101), (config.stt, 5201), (config.image, 5301))}
    if endpoint not in allowed:
        # Public job input and a residency record must never choose the credential audience.
        raise RuntimeError("worker_model_control_endpoint_not_configured")
    return endpoint


def _provider_key(record: ModelResidencyRecord) -> str:
    provider_id = _safe_str(record.provider_id).strip()
    if ":" in provider_id:
        return provider_id.split(":", 1)[1] or provider_id
    model_id = _safe_str(record.model_id).strip()
    if ":" in model_id:
        return model_id.split(":", 1)[1] or model_id
    return provider_id or record.model_id


def _response_error(response: dict[str, Any], action: str) -> str:
    error = _safe_str(response.get("error")).strip()
    provider = _safe_str(response.get("provider")).strip()
    if error:
        return f"worker_model_control_{action}_failed:{error}"
    if provider:
        return f"worker_model_control_{action}_failed:{provider}"
    return f"worker_model_control_{action}_failed"


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", **service_headers()},
        method="POST",
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"worker_model_control_http_{exc.code}:{raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"worker_model_control_unreachable:{exc}") from exc
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)
