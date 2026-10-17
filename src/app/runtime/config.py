"""Process binding for validated deployment configuration."""

from __future__ import annotations

from app.config.runtime import DevicePermitSettings, GatewayRole, RuntimeConfig, ServiceEndpoint

_process_config: RuntimeConfig | None = None


def install_runtime_config(config: RuntimeConfig) -> None:
    """Bind policy before production imports; a serving process cannot change role."""
    global _process_config
    if _process_config is not None and _process_config != config:
        raise RuntimeError("Runtime configuration is already bound to this process")
    if _process_config is None:
        _process_config = config


def get_runtime_config() -> RuntimeConfig:
    if _process_config is not None:
        return _process_config
    from app.config.env import environment

    return RuntimeConfig.from_environment(environment())


__all__ = [
    "GatewayRole",
    "DevicePermitSettings",
    "RuntimeConfig",
    "ServiceEndpoint",
    "get_runtime_config",
    "install_runtime_config",
]
