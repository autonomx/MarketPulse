"""Validated deployment configuration models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from app.caching.bounded_cache import bounded_lru_cache
from socket import gethostname
from typing import Literal
from urllib.parse import urlsplit

from .env import env_int, environment

DeviceModelClass = Literal["tts", "stt", "image", "llm-local"]
_DEVICE_MODEL_CLASSES: tuple[DeviceModelClass, ...] = ("tts", "stt", "image", "llm-local")


@dataclass(frozen=True, slots=True)
class DevicePermitSettings:
    """Deployment capacity for device classes coordinated through PostgreSQL."""

    device_id: str
    capacities: tuple[tuple[DeviceModelClass, int, int], ...]
    lease_seconds: int = 120
    tts_model_owner: str | None = None
    live_max_calls: int = 1

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> DevicePermitSettings:
        source = environment() if env is None else env
        device_id = str(source.get("OMNIX_DEVICE_ID", f"{gethostname()}:gpu0")).strip()
        if not device_id or len(device_id) > 255 or any(char.isspace() for char in device_id):
            raise ValueError("OMNIX_DEVICE_ID must be a non-empty device label without whitespace")
        capacities = tuple(
            (
                model_class,
                env_int(
                    f"OMNIX_DEVICE_{model_class.upper().replace('-', '_')}_CAPACITY",
                    1,
                    minimum=1,
                    maximum=1024,
                    env=source,
                ),
                env_int(
                    f"OMNIX_DEVICE_{model_class.upper().replace('-', '_')}_REALTIME_RESERVED",
                    0,
                    minimum=0,
                    maximum=1024,
                    env=source,
                ),
            )
            for model_class in _DEVICE_MODEL_CLASSES
        )
        if any(reserved > capacity for _, capacity, reserved in capacities):
            raise ValueError("device realtime reservation cannot exceed its configured capacity")
        lease_seconds = env_int(
            "OMNIX_DEVICE_PERMIT_LEASE_SECONDS",
            120,
            minimum=5,
            maximum=86_400,
            env=source,
        )
        owner = str(source.get("OMNIX_TTS_MODEL_OWNER", "")).strip().casefold() or None
        if owner not in {None, "gateway", "tts-server"}:
            raise ValueError("OMNIX_TTS_MODEL_OWNER must be gateway or tts-server")
        tts_capacity = next(capacity for model, capacity, _ in capacities if model == "tts")
        live_max_calls = env_int(
            "OMNIX_LIVE_MAX_CALLS",
            tts_capacity,
            minimum=1,
            maximum=1024,
            env=source,
        )
        return cls(device_id, capacities, lease_seconds, owner, live_max_calls)


@bounded_lru_cache(max_entries=1, ttl_seconds=3600.0)
def configured_job_priority_aging_seconds() -> int:
    """Read the process job-aging policy once through the typed config owner."""
    return env_int(
        "OMNIX_JOB_PRIORITY_AGING_SECONDS",
        60,
        minimum=1,
        maximum=86_400,
        env=environment(),
    )


class GatewayRole(str, Enum):
    WORKER = "worker"
    API = "api"
    SCHEDULER = "scheduler"
    JOB_WORKER = "job-worker"


def _url(value: str, *, origin: bool = False) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not any(char.isspace() for char in value)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and (not origin or parsed.path in {"", "/"})
        )
    except ValueError:
        valid = False
    if not valid or parsed.hostname is None:
        raise ValueError("Service URLs must be absolute HTTP(S) URLs without credentials, query or fragment")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    suffix = f":{port}" if port and (parsed.scheme, port) not in {("http", 80), ("https", 443)} else ""
    return f"{parsed.scheme}://{host}{suffix}{parsed.path.rstrip('/')}"


@dataclass(frozen=True, slots=True)
class ServiceEndpoint:
    url: str
    required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _url(self.url))


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    gateway_role: GatewayRole = GatewayRole.WORKER
    tts: ServiceEndpoint | None = None
    stt: ServiceEndpoint | None = None
    image: ServiceEndpoint | None = None
    use_remote_tts: bool = False
    tts_model_owner: str | None = None
    api_replica_origins: tuple[str, ...] = ()
    required_workers: tuple[str, ...] = ()
    build_revision: str = "unversioned"
    worker_environment: tuple[tuple[str, str], ...] = ()
    enabled_features: tuple[str, ...] = ("all",)
    disabled_features: tuple[str, ...] = ()
    job_priority_aging_seconds: int = 60
    scheduler_thread_workers: int = 4
    scheduler_process_workers: int = 2
    live_max_calls: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "gateway_role", GatewayRole(self.gateway_role))
        owner = None if self.tts_model_owner is None else str(self.tts_model_owner).strip().casefold()
        if owner not in {None, "gateway", "tts-server"}:
            raise ValueError("OMNIX_TTS_MODEL_OWNER must be gateway or tts-server")
        object.__setattr__(self, "tts_model_owner", owner)
        origins = tuple(_url(value, origin=True) for value in self.api_replica_origins)
        if len(set(origins)) != len(origins):
            raise ValueError("Duplicate API replica origins")
        if len(origins) > 8:
            raise ValueError("At most eight API replica origins are supported")
        if not 1 <= int(self.job_priority_aging_seconds) <= 86_400:
            raise ValueError("OMNIX_JOB_PRIORITY_AGING_SECONDS must be between 1 and 86400")
        object.__setattr__(self, "job_priority_aging_seconds", int(self.job_priority_aging_seconds))
        if not 1 <= int(self.scheduler_thread_workers) <= 64:
            raise ValueError("OMNIX_SCHEDULER_THREAD_WORKERS must be between 1 and 64")
        if not 1 <= int(self.scheduler_process_workers) <= 16:
            raise ValueError("OMNIX_SCHEDULER_PROCESS_WORKERS must be between 1 and 16")
        object.__setattr__(self, "scheduler_thread_workers", int(self.scheduler_thread_workers))
        object.__setattr__(self, "scheduler_process_workers", int(self.scheduler_process_workers))
        if not 1 <= int(self.live_max_calls) <= 1024:
            raise ValueError("OMNIX_LIVE_MAX_CALLS must be between 1 and 1024")
        object.__setattr__(self, "live_max_calls", int(self.live_max_calls))
        object.__setattr__(self, "api_replica_origins", origins)
        object.__setattr__(self, "required_workers", tuple(sorted(set(self.required_workers))))
        enabled = tuple(dict.fromkeys(value.strip() for value in self.enabled_features if value.strip()))
        disabled = tuple(dict.fromkeys(value.strip() for value in self.disabled_features if value.strip()))
        object.__setattr__(self, "enabled_features", enabled or ("all",))
        object.__setattr__(self, "disabled_features", disabled)
        workers = tuple((key, _url(value) if key.endswith('_URL') and value else value)
                        for key, value in self.worker_environment)
        object.__setattr__(self, 'worker_environment', workers)
        if self.gateway_role is GatewayRole.API and self.tts is not None:
            object.__setattr__(self, "use_remote_tts", True)
        if self.tts_model_owner == "tts-server" and self.tts is None:
            raise ValueError("OMNIX_TTS_MODEL_OWNER=tts-server requires OMNIX_TTS_URL")
        if self.use_remote_tts and self.tts is None:
            raise ValueError("Gateway HTTP TTS requires OMNIX_TTS_URL")

    @property
    def owns_background_runtime(self) -> bool:
        return self.gateway_role is GatewayRole.WORKER

    @property
    def runs_schedulers(self) -> bool:
        return self.gateway_role in {GatewayRole.WORKER, GatewayRole.SCHEDULER}

    @property
    def runs_job_workers(self) -> bool:
        return self.gateway_role is GatewayRole.JOB_WORKER

    @property
    def allow_local_tts(self) -> bool:
        if self.use_remote_tts or self.tts_model_owner == "tts-server":
            return False
        if self.tts_model_owner == "gateway":
            return self.gateway_role is GatewayRole.WORKER
        return self.gateway_role in {GatewayRole.WORKER, GatewayRole.JOB_WORKER}

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> RuntimeConfig:
        env = {} if env is None else env

        def flag(name: str, default: bool) -> bool:
            value = env.get(name)
            if value is None:
                return default
            value = value.strip().lower()
            if value in {"1", "true", "yes", "on"}:
                return True
            if value in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{name} must be a boolean")

        role = GatewayRole(env.get("OMNIX_GATEWAY_BACKGROUND_ROLE", "worker").strip())
        permit_settings = DevicePermitSettings.from_environment(env)
        job_priority_aging_seconds = env_int(
            "OMNIX_JOB_PRIORITY_AGING_SECONDS",
            60,
            minimum=1,
            maximum=86_400,
            env=env,
        )
        scheduler_thread_workers = env_int(
            "OMNIX_SCHEDULER_THREAD_WORKERS",
            4,
            minimum=1,
            maximum=64,
            env=env,
        )
        scheduler_process_workers = env_int(
            "OMNIX_SCHEDULER_PROCESS_WORKERS",
            2,
            minimum=1,
            maximum=16,
            env=env,
        )
        owns = role is GatewayRole.WORKER
        if flag("OMNIX_GATEWAY_OWNS_BACKGROUND_RUNTIME", owns) != owns:
            raise ValueError("Explicit background ownership contradicts gateway role")
        required = tuple(value.strip() for value in env.get("OMNIX_GATEWAY_REQUIRED_WORKERS", "").split(",") if value.strip())

        def endpoint(name: str) -> ServiceEndpoint | None:
            value = env.get(f"OMNIX_{name.upper()}_URL", "").strip()
            return ServiceEndpoint(value, name in required) if value else None

        tts = endpoint("tts")
        remote = (
            flag("OMNIX_GATEWAY_TTS_HTTP", False)
            or (not owns and tts is not None)
            or permit_settings.tts_model_owner == "tts-server"
        )
        local = role in {GatewayRole.WORKER, GatewayRole.JOB_WORKER} and not remote
        if flag("OMNIX_GATEWAY_ALLOW_LOCAL_TTS", local) != local:
            raise ValueError("Explicit local TTS policy contradicts gateway topology")
        return cls(
            gateway_role=role, tts=tts, stt=endpoint("stt"), image=endpoint("image"),
            use_remote_tts=remote,
            tts_model_owner=permit_settings.tts_model_owner,
            api_replica_origins=tuple(value.strip() for value in env.get("OMNIX_GATEWAY_API_ORIGINS", "").split(",") if value.strip()),
            required_workers=required,
            build_revision=env.get("OMNIX_SOFTWARE_REVISION", "unversioned"),
            worker_environment=tuple(sorted((key, value) for key, value in env.items()
                                     if key.startswith('OMNIX_WORKER_') or key in {
                                         'OMNIX_GATEWAY_WORKERS', 'OMNIX_GATEWAY_MOCK_WORKERS', 'OMNIX_GATEWAY_MOCK_WORKERS_LIST'})),
            enabled_features=tuple(
                value.strip()
                for value in env.get("OMNIX_FEATURES", "all").split(",")
                if value.strip()
            ),
            disabled_features=tuple(
                value.strip()
                for value in env.get("OMNIX_FEATURES_DISABLED", "").split(",")
                if value.strip()
            ),
            job_priority_aging_seconds=job_priority_aging_seconds,
            scheduler_thread_workers=scheduler_thread_workers,
            scheduler_process_workers=scheduler_process_workers,
            live_max_calls=permit_settings.live_max_calls,
        )

    def worker_discovery_environment(self) -> dict[str, str]:
        env = dict(self.worker_environment)
        for name in ('tts', 'stt', 'image'):
            endpoint = getattr(self, name)
            if endpoint is not None:
                env[f'OMNIX_{name.upper()}_URL'] = endpoint.url
        return env
