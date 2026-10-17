"""Feature-module contracts used by the composition root."""
from __future__ import annotations

import builtins
from dataclasses import dataclass
import logging
from typing import Any, Callable, Literal, Protocol, get_args

from fastapi import APIRouter
from pydantic import BaseModel

from .background import BackgroundWorker
from .capabilities import RuntimeCapabilities, RuntimeCapability
from .config import RuntimeConfig
from .ports import ContributionSpec

from app.runtime.contracts import KernelServices


class JobHandlerSpec(Protocol):
    """Structural job-handler contract without importing the jobs kernel.

    Read-only, so the jobs kernel's frozen ``JobHandlerSpec`` satisfies it.
    """

    @property
    def type(self) -> str: ...
    @property
    def handler(self) -> Callable[..., object]: ...
    @property
    def input_model(self) -> builtins.type[BaseModel]: ...  # ``type`` is the property above
    @property
    def resource_class(self) -> object: ...
    @property
    def timeout_seconds(self) -> float: ...
    @property
    def max_attempts(self) -> int: ...
    @property
    def retry_backoff(self) -> object: ...
    @property
    def submission_policy(self) -> Callable[..., object] | None: ...


class JobObserverFactory(Protocol):
    """Feature factory that returns an enabled kernel job observer, if any."""

    def __call__(self) -> object | None: ...


class ScheduledTaskSpec(Protocol):
    """Structural contract for an independently owned scheduled task."""

    task_id: str
    run: Callable[..., object]
    interval_seconds: float | None
    cron: str | None
    jitter_seconds: float
    timeout_seconds: float
    executor: object
    max_overlap: int
    requires: frozenset[RuntimeCapability]
    enabled: Callable[[], bool]
    on_startup: tuple[Callable[[], Any], ...]
    on_shutdown: tuple[Callable[[], Any], ...]


class ScheduledTaskFactory(Protocol):
    """Feature factory that builds a task against the composed services."""

    def __call__(self, context: "FeatureContext", /) -> ScheduledTaskSpec | None: ...


class RepositorySpec(Protocol):
    """Structural repository factory contract without importing persistence."""

    @property
    def type(self) -> object: ...
    @property
    def factory(self) -> Callable[..., object]: ...
    @property
    def alias(self) -> str | None: ...


class OutboxConsumerSpec(Protocol):
    """Structural contract for a feature-owned outbox consumer (``app.events.outbox_relay.OutboxConsumer``)."""

    @property
    def consumer_name(self) -> str: ...
    @property
    def aggregate_types(self) -> frozenset[str]: ...
    @property
    def handler(self) -> Callable[..., object]: ...
    @property
    def event_type_pattern(self) -> str: ...


class SettingSpec(Protocol):
    """Structural settings contract without importing the settings kernel."""

    @property
    def key(self) -> str: ...
    @property
    def value_type(self) -> type: ...
    @property
    def default(self) -> object: ...
    @property
    def feature(self) -> str: ...
    @property
    def writable(self) -> bool: ...


class PermissionSpec(Protocol):
    """Structural permission contract; authorization policy is feature owned."""

    action: str
    resource: str


@dataclass(frozen=True, slots=True)
class FeatureLifecycle:
    name: str
    startup: tuple[Callable[[], Any], ...] = ()
    shutdown: tuple[Callable[[], Any], ...] = ()
    requires: frozenset[RuntimeCapability] = frozenset({RuntimeCapability.SERVE_API})


@dataclass(frozen=True, slots=True)
class FeatureContext:
    feature_id: str
    config: BaseModel | None
    runtime: RuntimeConfig
    capabilities: RuntimeCapabilities
    services: "KernelServices"
    logger: logging.Logger
    runtime_state: Any = None


RouterFactory = Callable[[FeatureContext], APIRouter]
BackgroundWorkerFactory = Callable[[FeatureContext], BackgroundWorker | None]


# ADR-0016: a platform capability is reusable by other features through its
# contract; an app is a user-facing product that no other feature imports.
FeatureTier = Literal["platform", "app"]
FEATURE_TIERS: frozenset[str] = frozenset(get_args(FeatureTier))


@dataclass(frozen=True, slots=True)
class FeatureModule:
    id: str
    title: str
    tier: FeatureTier
    requires: frozenset[RuntimeCapability] = frozenset({RuntimeCapability.SERVE_API})
    depends_on: tuple[str, ...] = ()
    # Contracts this feature imports from features it can run without (ADR-0016):
    # the import is declared and one-way, but the other feature may be disabled.
    uses: tuple[str, ...] = ()
    config_model: type[BaseModel] | None = None
    routers: tuple[RouterFactory, ...] = ()
    internal_routers: tuple[RouterFactory, ...] = ()
    job_handlers: tuple[JobHandlerSpec, ...] = ()
    job_observers: tuple[JobObserverFactory, ...] = ()
    background_workers: tuple[BackgroundWorkerFactory, ...] = ()
    scheduled_tasks: tuple[ScheduledTaskFactory, ...] = ()
    repositories: tuple[RepositorySpec, ...] = ()
    contributions: tuple[ContributionSpec, ...] = ()
    outbox_consumers: tuple[OutboxConsumerSpec, ...] = ()
    settings: tuple[SettingSpec, ...] = ()
    permissions: tuple[PermissionSpec, ...] = ()
    public_paths: frozenset[str] = frozenset()
    lifecycle: FeatureLifecycle | None = None

    def __post_init__(self) -> None:
        feature_id = self.id.strip()
        if not feature_id or feature_id != self.id or not feature_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid feature id: {self.id!r}")
        if self.tier not in FEATURE_TIERS:
            raise ValueError(f"Feature {self.id} has invalid tier: {self.tier!r}")
        if any(not isinstance(item, ContributionSpec) for item in self.contributions):
            raise TypeError(f"Feature {self.id} contributions must be ContributionSpec values")
        if self.id in self.depends_on or self.id in self.uses:
            raise ValueError(f"Feature {self.id} cannot depend on itself")
