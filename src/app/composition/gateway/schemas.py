"""Typed response contracts for kernel-owned gateway routes."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.assets import PublicAssetRecord
from app.runtime.worker_health import (
    GATEWAY_FORMAT_VERSION,
    WorkerHealthPayload,
    WorkerPayloadPolicy,
)


class GatewayHealth(BaseModel):
    ok: bool = True
    status: Literal["ready"] = "ready"
    service: Literal["omnix-gateway"] = "omnix-gateway"
    format_version: str = GATEWAY_FORMAT_VERSION


class RuntimeStatusPayload(BaseModel):
    ok: bool = True
    status: Literal["ready", "degraded"] = "ready"
    format_version: str = GATEWAY_FORMAT_VERSION
    gateway: GatewayHealth = Field(default_factory=GatewayHealth)
    workers: WorkerHealthPayload = Field(default_factory=WorkerHealthPayload)
    compatibility: dict[str, Any] = Field(default_factory=dict)


class GatewayReadinessPayload(BaseModel):
    """Stable readiness fields plus deployment-specific diagnostic fields."""

    model_config = {"extra": "allow"}

    ready: bool
    backend: str | None = None
    authority_state: str | None = None
    migrations_pending: list[str] = Field(default_factory=list)
    required_workers_unavailable: list[str] = Field(default_factory=list)
    execution_owner_ready: bool | None = None
    background_role: str | None = None
    background_ready: bool | None = None
    build_revision: str | None = None
    reason: str | None = None


class CodexAuthStatus(BaseModel):
    installed: bool = False
    authenticated: bool = False
    auth_mode: str | None = None
    cli_version: str | None = None
    detail: str = ""
    started: bool = False
    pid: int | None = None
    auth_url: str | None = None


class CompatibilityHandoffPayload(BaseModel):
    ok: bool = True
    format_version: str = GATEWAY_FORMAT_VERSION
    legacy_ui_status: Literal["retired"] = "retired"
    existing_fastapi_app: str = "app.composition.gateway.main:app"
    domain_logic_policy: str = "delegate_to_existing_service_modules"
    migration_note: str = (
        "The classic browser UI and compatibility application server are retired. "
        "Current apps use the shared gateway; saved-data migration adapters remain supported."
    )
    handoff_targets: list[dict[str, str]] = Field(default_factory=list)


class AssetContentResponse(BaseModel):
    asset: PublicAssetRecord
    content: str
    encoding: Literal["utf-8"] = "utf-8"
    size_bytes: int
    truncated: Literal[False] = False


__all__ = [
    "AssetContentResponse",
    "CodexAuthStatus",
    "CompatibilityHandoffPayload",
    "GatewayHealth",
    "GatewayReadinessPayload",
    "RuntimeStatusPayload",
    "WorkerHealthPayload",
    "WorkerPayloadPolicy",
]
