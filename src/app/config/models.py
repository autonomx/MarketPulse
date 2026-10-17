"""Validated process configuration models."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .runtime import RuntimeConfig


class NetworkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    bind_host: str = "127.0.0.1"
    gateway_port: int = Field(default=8001, ge=1, le=65535)


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    url: str | None = None
    migration_url: str | None = None
    require_role_separation: bool = False


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    environment: str = "development"
    software_revision: str = "unversioned"


class OmnixConfig(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    runtime: RuntimeConfig
    network: NetworkConfig
    database: DatabaseConfig
    observability: ObservabilityConfig
