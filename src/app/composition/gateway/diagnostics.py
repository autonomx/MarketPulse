"""Gateway diagnostics summary for platform contracts."""
from __future__ import annotations

from dataclasses import asdict
from pydantic import BaseModel, Field
import re
from typing import Any

from app.runtime.worker_health import WorkerHealthPayload, get_worker_health_payload
from app.jobs import ModelResidencyDiagnostics, ModelResidencyRecord, get_model_residency_diagnostics
from app.providers.cache_status import ProviderModelCachePayload, get_provider_model_cache_status
from app.persistence.device_permits import default_device_permit_service


class EventReaderDiagnostics(BaseModel):
    """Live job events in this process (WP-5.4)."""

    readers: int = 0
    subscribers: int = 0
    queries: int = 0
    listeners_alive: int = 0


class RetentionRunDiagnostics(BaseModel):
    """The newest retention run; the error is reported by class only."""

    status: str
    started_at: str | None = None
    completed_at: str | None = None
    deleted: dict[str, int] = Field(default_factory=dict)
    error_class: str | None = None


class VersionDiagnostics(BaseModel):
    build_revision: str | None = None
    application_schema: str


class RuntimeDiagnostics(BaseModel):
    """One process's runtime view (WP-10.9); sections are documented in docs/operations/DIAGNOSTICS.md."""

    schema_version: int = 2
    version: VersionDiagnostics | None = None
    features: list[str] = Field(default_factory=list)
    events: EventReaderDiagnostics = Field(default_factory=EventReaderDiagnostics)
    retention: RetentionRunDiagnostics | None = None
    process: dict[str, Any] = Field(default_factory=dict)
    postgresql: dict[str, Any] = Field(default_factory=dict)
    background: dict[str, Any] = Field(default_factory=dict)
    jobs: dict[str, Any] = Field(default_factory=dict)
    chat: dict[str, Any] = Field(default_factory=dict)
    scheduler: dict[str, Any] = Field(default_factory=dict)
    tts: dict[str, Any] = Field(default_factory=dict)
    replicas: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsPayload(BaseModel):
    ok: bool
    status: str
    workers: WorkerHealthPayload
    event_stream: dict[str, str] = Field(default_factory=dict)
    model_residency: ModelResidencyDiagnostics = Field(default_factory=get_model_residency_diagnostics)
    device_permits: list[dict[str, Any]] = Field(default_factory=list)
    provider_model_cache: ProviderModelCachePayload = Field(default_factory=get_provider_model_cache_status)
    logs: list[dict[str, str]] = Field(default_factory=list)
    runtime: RuntimeDiagnostics | None = None


def get_diagnostics_payload(model_residency_records: list[ModelResidencyRecord] | None = None) -> DiagnosticsPayload:
    workers = get_worker_health_payload()
    device_permits: list[dict[str, Any]] = []
    permit_service = default_device_permit_service()
    if permit_service is not None:
        try:
            device_permits = [asdict(item) for item in permit_service.diagnostics()]
        except Exception as exc:
            device_permits = [{"status": "unavailable", "error_class": type(exc).__name__}]
    try:
        provider_model_cache = get_provider_model_cache_status()
    except Exception as exc:
        provider_model_cache = ProviderModelCachePayload(
            status="unavailable",
            diagnostics=[
                {
                    "code": "provider_model_cache_unavailable",
                    "error_class": type(exc).__name__,
                }
            ],
        )
    return DiagnosticsPayload(
        ok=workers.ok,
        status="ready" if workers.ok else "degraded",
        workers=workers,
        event_stream={
            "transport": "sse",
            "client": "web/src/events/eventClient.ts",
            "status": "available",
        },
        model_residency=get_model_residency_diagnostics(model_residency_records),
        device_permits=device_permits,
        provider_model_cache=provider_model_cache,
        logs=[],
    )


def redact_diagnostics(value, key=''):
    """Redact nested service metadata as well as credentials embedded in URLs."""
    if any(part in key.lower() for part in ('password', 'api_key', 'secret', 'credential', 'token')):
        return '[REDACTED]' if value not in (None, '') else value
    if isinstance(value, dict):
        return {str(name): redact_diagnostics(item, str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_diagnostics(item) for item in value]
    if isinstance(value, str):
        return re.sub(r'([a-z][a-z0-9+.-]*://)[^\s/@]+@', r'\1[REDACTED]@', value, flags=re.I)
    return value


def get_runtime_diagnostics_payload(
    state,
    *,
    model_residency_store_factory,
    allow_offline_store: bool = False,
) -> DiagnosticsPayload:
    """Keep local ownership diagnostics available when durable reads fail."""
    from app.jobs.residency import GpuResidencyPolicy
    from app.composition.gateway.runtime_diagnostics import runtime_diagnostics

    runtime = runtime_diagnostics(state)
    error_class = runtime.postgresql.get('error_class')
    payload = None
    if runtime.postgresql.get("connectivity") is not False or allow_offline_store:
        try:
            payload = get_diagnostics_payload(model_residency_store_factory().list_records())
        except Exception as exc:
            error_class = type(exc).__name__
    if payload is None:
        payload = DiagnosticsPayload(
            ok=False, status='degraded', workers=get_worker_health_payload(),
            model_residency=ModelResidencyDiagnostics(status='unavailable', policy=GpuResidencyPolicy()),
            provider_model_cache=ProviderModelCachePayload(
                status='unavailable', diagnostics=[{'code': 'durable_diagnostics_unavailable', 'error_class': error_class or 'Unavailable'}],
            ),
        )
    payload.runtime = runtime
    tts_worker = next((worker for worker in payload.workers.workers if worker.id == 'tts'), None)
    runtime.tts['endpoint_health'] = {'ok': tts_worker.ok, 'status': tts_worker.status} if tts_worker is not None else None
    return DiagnosticsPayload.model_validate(redact_diagnostics(payload.model_dump()))
