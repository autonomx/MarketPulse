"""Worker discovery and health contracts for the thin gateway."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

GATEWAY_FORMAT_VERSION = "omnix_gateway_foundation_v1"
WORKER_CONTRACT_VERSION = "omnix_worker_health_contract_v1"
DEFAULT_HEALTH_TIMEOUT_SECONDS = 2.0
DEFAULT_MOCK_WORKERS = ("tts", "stt", "image")
_health_probes = ThreadPoolExecutor(max_workers=4, thread_name_prefix="omnix-health")


class WorkerHealthSummary(BaseModel):
    configured: int = 0
    reachable: int = 0
    unreachable: int = 0
    mocked: int = 0


class WorkerHealth(BaseModel):
    id: str
    ok: bool
    status: str
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    url: str = ""
    capabilities: list[str] = Field(default_factory=list)
    source_env: str = ""
    mocked: bool = False


class WorkerHealthPayload(BaseModel):
    ok: bool = True
    status: str = "not_configured"
    format_version: str = GATEWAY_FORMAT_VERSION
    contract_version: str = WORKER_CONTRACT_VERSION
    workers: list[WorkerHealth] = Field(default_factory=list)
    summary: WorkerHealthSummary = Field(default_factory=WorkerHealthSummary)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class WorkerPayloadPolicy(BaseModel):
    ok: bool = True
    format_version: str = GATEWAY_FORMAT_VERSION
    contract_version: str = WORKER_CONTRACT_VERSION
    small_json_payloads: str = "allowed"
    generated_artifacts: str = "return_asset_reference"
    base64_media_payloads: str = "transitional_only"
    browser_worker_access: str = "forbidden"
    gateway_worker_access: str = "required"
    notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class WorkerSpec:
    id: str
    url: str
    capabilities: tuple[str, ...]
    source_env: str
    mocked: bool = False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _clean_worker_id(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip()).strip("_")


def _normalize_url(value: str | None) -> str:
    raw = (value or "").strip().strip('"').strip("'")
    raw = raw.replace(" ", "")
    return raw.rstrip("/")


def _capabilities_for_worker(worker_id: str, env: Mapping[str, str]) -> tuple[str, ...]:
    key = f"OMNIX_WORKER_{worker_id.upper()}_CAPABILITIES"
    configured = _split_csv(env.get(key))
    return tuple(configured or [worker_id])


def _configured_worker_specs(env: Mapping[str, str]) -> list[WorkerSpec]:
    specs: list[WorkerSpec] = []
    seen: set[str] = set()

    for raw_id in _split_csv(env.get("OMNIX_GATEWAY_WORKERS")):
        worker_id = _clean_worker_id(raw_id)
        if not worker_id or worker_id in seen:
            continue
        env_key = f"OMNIX_WORKER_{worker_id.upper()}_URL"
        url = _normalize_url(env.get(env_key))
        if not url:
            continue
        specs.append(
            WorkerSpec(
                id=worker_id,
                url=url,
                capabilities=_capabilities_for_worker(worker_id, env),
                source_env=env_key,
            )
        )
        seen.add(worker_id)

    compatibility_envs = {
        "tts": "OMNIX_TTS_URL",
        "stt": "OMNIX_STT_URL",
        "image": "OMNIX_IMAGE_URL",
    }
    for worker_id, env_key in compatibility_envs.items():
        if worker_id in seen:
            continue
        url = _normalize_url(env.get(env_key))
        if not url:
            continue
        specs.append(
            WorkerSpec(
                id=worker_id,
                url=url,
                capabilities=_capabilities_for_worker(worker_id, env),
                source_env=env_key,
            )
        )
        seen.add(worker_id)

    return specs


def _mock_worker_specs(env: Mapping[str, str]) -> list[WorkerSpec]:
    worker_ids = _split_csv(env.get("OMNIX_GATEWAY_MOCK_WORKERS_LIST")) or list(DEFAULT_MOCK_WORKERS)
    specs: list[WorkerSpec] = []
    for raw_id in worker_ids:
        worker_id = _clean_worker_id(raw_id)
        if worker_id:
            specs.append(
                WorkerSpec(
                    id=worker_id,
                    url=f"mock://{worker_id}",
                    capabilities=_capabilities_for_worker(worker_id, env),
                    source_env="OMNIX_GATEWAY_MOCK_WORKERS",
                    mocked=True,
                )
            )
    return specs


def discover_worker_specs(env: Mapping[str, str] | None = None) -> list[WorkerSpec]:
    """Discover workers from gateway-owned env vars and compatibility env vars."""
    from app.runtime.config import get_runtime_config
    resolved_env = get_runtime_config().worker_discovery_environment() if env is None else env
    if _truthy(resolved_env.get("OMNIX_GATEWAY_MOCK_WORKERS")):
        configured = _configured_worker_specs(resolved_env)
        return [
            WorkerSpec(
                id=spec.id,
                url=spec.url or f"mock://{spec.id}",
                capabilities=spec.capabilities,
                source_env=spec.source_env,
                mocked=True,
            )
            for spec in configured
        ] or _mock_worker_specs(resolved_env)
    return _configured_worker_specs(resolved_env)


def _health_url(base_url: str) -> str:
    if base_url.startswith("mock://"):
        return base_url
    return f"{base_url.rstrip('/')}/health"


def _safe_json_response(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _worker_details(spec: WorkerSpec, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    details = {
        "health_url": _health_url(spec.url),
        "source_env": spec.source_env,
    }
    details.update(dict(extra or {}))
    return details


def probe_worker_health(
    spec: WorkerSpec,
    *,
    timeout_seconds: float = DEFAULT_HEALTH_TIMEOUT_SECONDS,
) -> WorkerHealth:
    """Return a standard health envelope for one worker without raising."""
    if spec.mocked or spec.url.startswith("mock://"):
        return WorkerHealth(
            id=spec.id,
            ok=True,
            status="ready",
            details=_worker_details(spec, {"mode": "mock"}),
            error=None,
            url=spec.url,
            capabilities=list(spec.capabilities),
            source_env=spec.source_env,
            mocked=True,
        )

    parsed = urlparse(spec.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return WorkerHealth(
            id=spec.id,
            ok=False,
            status="unreachable",
            details=_worker_details(spec, {"reason": "invalid_worker_url"}),
            error="invalid_worker_url",
            url=spec.url,
            capabilities=list(spec.capabilities),
            source_env=spec.source_env,
        )

    try:
        request = Request(_health_url(spec.url), headers={"accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:
            data = _safe_json_response(response.read())
            ok = bool(data.get("ok", True))
            status = str(data.get("status") or ("ready" if ok else "degraded"))
            details = data.get("details") if isinstance(data.get("details"), dict) else data
            error = data.get("error") if isinstance(data.get("error"), str) else None
            return WorkerHealth(
                id=spec.id,
                ok=ok,
                status=status,
                details=_worker_details(spec, details),
                error=error,
                url=spec.url,
                capabilities=list(spec.capabilities),
                source_env=spec.source_env,
            )
    except HTTPError as exc:
        return WorkerHealth(
            id=spec.id,
            ok=False,
            status="unreachable",
            details=_worker_details(spec, {"http_status": exc.code}),
            error=str(exc),
            url=spec.url,
            capabilities=list(spec.capabilities),
            source_env=spec.source_env,
        )
    except (OSError, URLError, TimeoutError) as exc:
        return WorkerHealth(
            id=spec.id,
            ok=False,
            status="unreachable",
            details=_worker_details(spec),
            error=str(exc),
            url=spec.url,
            capabilities=list(spec.capabilities),
            source_env=spec.source_env,
        )


def get_worker_health_payload(env: Mapping[str, str] | None = None) -> WorkerHealthPayload:
    specs = discover_worker_specs(env)
    workers = list(_health_probes.map(probe_worker_health, specs))
    reachable = sum(1 for worker in workers if worker.ok)
    unreachable = sum(1 for worker in workers if not worker.ok)
    mocked = sum(1 for worker in workers if worker.mocked)
    if not workers:
        status = "not_configured"
    elif unreachable:
        status = "degraded"
    else:
        status = "ready"
    diagnostics = [
        {
            "kind": "worker_unreachable",
            "worker_id": worker.id,
            "status": worker.status,
            "error": worker.error,
            "details": worker.details,
        }
        for worker in workers
        if not worker.ok
    ]
    return WorkerHealthPayload(
        ok=unreachable == 0,
        status=status,
        workers=workers,
        summary=WorkerHealthSummary(
            configured=len(workers),
            reachable=reachable,
            unreachable=unreachable,
            mocked=mocked,
        ),
        diagnostics=diagnostics,
    )


def get_worker_payload_policy() -> WorkerPayloadPolicy:
    return WorkerPayloadPolicy(
        notes=[
            "Worker URLs are configured through environment variables and are not browser-facing.",
            "Small request and response bodies may remain JSON at the gateway boundary.",
            "Large generated audio, image, transcript, checkpoint, and report outputs should return asset references.",
            "Base64 media in worker responses is allowed only as a compatibility bridge until shared assets land.",
        ]
    )
